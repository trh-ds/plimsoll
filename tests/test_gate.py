from pathlib import Path

import pytest

from plimsoll.core.gate import (
    CheckContext,
    Gate,
    build_registered_checks,
    default_gate,
    registered_names,
)
from plimsoll.core.models import (
    CheckConfig,
    CheckSource,
    Finding,
    GateConfig,
    Severity,
    ToolAvailability,
)

WORKSPACE = Path("workspace")


def finding(severity: Severity, file: str = "main.tf", line: int = 1, rule: str = "r") -> Finding:
    return Finding(
        source=CheckSource.TERRAFORM_VALIDATE,
        rule_id=rule,
        rule_class=rule,
        severity=severity,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path(file),
        line_start=line,
        line_end=line,
        message=rule,
    )


class FakeCheck:
    """A check whose behaviour each test dictates."""

    def __init__(self, name, availability, findings=None, run_error=None, probe_error=None) -> None:
        self.name = name
        self._availability = availability
        self._findings = findings or []
        self._run_error = run_error
        self._probe_error = probe_error
        self.ran = False

    def is_available(self) -> ToolAvailability:
        if self._probe_error is not None:
            raise self._probe_error
        return self._availability

    def run(self, ctx: CheckContext) -> list[Finding]:
        self.ran = True
        if self._run_error is not None:
            raise self._run_error
        return list(self._findings)


def available(tool: str = "faketool", version: str = "1.0") -> ToolAvailability:
    return ToolAvailability.ok(tool, version)


def test_available_check_contributes_findings_and_tool_version():
    check = FakeCheck("fake", available(), [finding(Severity.HIGH)])
    result = Gate([check]).run(WORKSPACE)

    assert check.ran is True
    assert len(result.findings) == 1
    assert [(t.name, t.version) for t in result.tooling] == [("faketool", "1.0")]
    assert result.any_check_ran is True


def test_unavailable_check_is_skipped_not_fatal():
    missing = FakeCheck("missing", ToolAvailability.missing("nope", "not on PATH"))
    working = FakeCheck("working", available(), [finding(Severity.LOW)])

    result = Gate([missing, working]).run(WORKSPACE)

    assert missing.ran is False
    assert [(s.name, s.reason) for s in result.checks_skipped] == [("missing", "not on PATH")]
    assert len(result.findings) == 1


def test_crashing_check_does_not_kill_the_run():
    crasher = FakeCheck("crasher", available(), run_error=RuntimeError("boom"))
    survivor = FakeCheck("survivor", available(tool="other"), [finding(Severity.CRITICAL)])

    result = Gate([crasher, survivor]).run(WORKSPACE)

    assert [(f.name, f.error) for f in result.checks_failed] == [("crasher", "RuntimeError: boom")]
    assert len(result.findings) == 1
    assert survivor.ran is True
    # A crashed check contributed no output, so it is not recorded as tooling that ran.
    assert [t.name for t in result.tooling] == ["other"]


def test_crashing_availability_probe_is_isolated():
    crasher = FakeCheck("crasher", available(), probe_error=OSError("no exec"))
    survivor = FakeCheck("survivor", available(), [finding(Severity.MEDIUM)])

    result = Gate([crasher, survivor]).run(WORKSPACE)

    assert [f.name for f in result.checks_failed] == ["crasher"]
    assert len(result.findings) == 1


def test_no_checks_available_reports_nothing_ran():
    result = Gate(
        [
            FakeCheck("a", ToolAvailability.missing("a", "gone")),
            FakeCheck("b", ToolAvailability.missing("b", "gone")),
        ]
    ).run(WORKSPACE)

    assert result.any_check_ran is False
    assert result.findings == []
    assert len(result.checks_skipped) == 2


def test_findings_are_severity_ranked_then_located():
    check = FakeCheck(
        "fake",
        available(),
        [
            finding(Severity.LOW, "a.tf", 1, "low"),
            finding(Severity.CRITICAL, "z.tf", 9, "crit"),
            finding(Severity.HIGH, "b.tf", 5, "high-b"),
            finding(Severity.HIGH, "a.tf", 3, "high-a"),
        ],
    )
    result = Gate([check]).run(WORKSPACE)

    assert [f.rule_id for f in result.findings] == ["crit", "high-a", "high-b", "low"]


def test_shared_binary_is_recorded_once():
    checks = [
        FakeCheck("validate", available(tool="terraform", version="1.9.5")),
        FakeCheck("plan", available(tool="terraform", version="1.9.5")),
    ]
    result = Gate(checks).run(WORKSPACE)

    assert [t.name for t in result.tooling] == ["terraform"]


def test_gate_config_defaults_are_applied():
    seen: list[CheckConfig] = []

    class Recorder(FakeCheck):
        def run(self, ctx) -> list[Finding]:
            seen.append(ctx.config)
            return []

    Gate([Recorder("rec", available())]).run(WORKSPACE, GateConfig())
    assert seen[0].timeout_seconds == 120


class PlanConsumer(FakeCheck):
    """An adapter that needs the plan artifact, the way `opa` will."""

    def run(self, ctx: CheckContext) -> list[Finding]:
        self.ran = True
        self.plan = ctx.plan_json()
        return list(self._findings)


def test_plan_is_produced_once_however_many_adapters_ask():
    calls: list[Path] = []

    def provider(workspace: Path, config: CheckConfig) -> Path:
        calls.append(workspace)
        return Path("plan.json")

    consumers = [PlanConsumer("a", available()), PlanConsumer("b", available())]
    Gate(consumers, plan_provider=provider).run(WORKSPACE)

    assert calls == [WORKSPACE]
    assert [c.plan for c in consumers] == [Path("plan.json"), Path("plan.json")]


def test_no_plan_provider_gives_none_rather_than_an_error():
    consumer = PlanConsumer("needs-plan", available())
    result = Gate([consumer]).run(WORKSPACE)

    assert consumer.plan is None
    assert result.checks_failed == []


def test_plan_provider_failure_is_isolated_and_identical_for_every_consumer():
    attempts: list[int] = []

    def provider(workspace: Path, config: CheckConfig) -> Path:
        attempts.append(1)
        raise OSError("terraform init failed")

    consumers = [PlanConsumer("a", available()), PlanConsumer("b", available())]
    survivor = FakeCheck("survivor", available(tool="other"), [finding(Severity.HIGH)])
    result = Gate([*consumers, survivor], plan_provider=provider).run(WORKSPACE)

    # Both consumers fail the same way, and the failure is not retried per adapter.
    assert [f.name for f in result.checks_failed] == ["a", "b"]
    assert attempts == [1]
    assert len(result.findings) == 1


def test_context_plan_is_memoized_across_direct_calls():
    calls: list[int] = []

    def provider(workspace: Path, config: CheckConfig) -> Path:
        calls.append(1)
        return Path("plan.json")

    ctx = CheckContext(WORKSPACE, CheckConfig(), provider)
    assert ctx.plan_json() == ctx.plan_json() == Path("plan.json")
    assert calls == [1]


def test_context_reraises_the_same_plan_failure():
    def provider(workspace: Path, config: CheckConfig) -> Path:
        raise RuntimeError("no credentials")

    ctx = CheckContext(WORKSPACE, CheckConfig(), provider)
    with pytest.raises(RuntimeError):
        ctx.plan_json()
    with pytest.raises(RuntimeError):
        ctx.plan_json()


def test_registry_exposes_every_adapter():
    assert "terraform_validate" in registered_names()
    assert [c.name for c in build_registered_checks()] == ["checkov", "terraform_validate"]
    assert [c.name for c in default_gate().checks] == ["checkov", "terraform_validate"]
