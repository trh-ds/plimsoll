import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from plimsoll import cli
from plimsoll.core.gate import CheckContext, Gate
from plimsoll.core.models import CheckSource, Finding, Severity, ToolAvailability

runner = CliRunner()


class StubCheck:
    def __init__(self, name: str, availability: ToolAvailability, findings=None) -> None:
        self.name = name
        self._availability = availability
        self._findings = findings or []

    def is_available(self) -> ToolAvailability:
        return self._availability

    def run(self, ctx: CheckContext) -> list[Finding]:
        return list(self._findings)


def finding(severity: Severity, rule_class: str = "s3.acl.public") -> Finding:
    return Finding(
        source=CheckSource.CHECKOV,
        rule_id="CKV_AWS_20",
        rule_class=rule_class,
        severity=severity,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=12,
        line_end=12,
        message="S3 bucket allows public read",
    )


def use_gate(monkeypatch, checks) -> None:
    monkeypatch.setattr(cli, "default_gate", lambda: Gate(checks))


def available(tool: str = "checkov", version: str = "3.3.15") -> ToolAvailability:
    return ToolAvailability.ok(tool, version)


def test_clean_scan_exits_zero(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available())])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 0
    assert "No findings." in result.output


def test_findings_at_the_threshold_exit_one(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available(), [finding(Severity.CRITICAL)])])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 1
    assert "1 finding(s), 1 at or above HIGH." in result.output


def test_findings_below_the_threshold_exit_zero(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available(), [finding(Severity.MEDIUM)])])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 0
    assert "1 finding(s), 0 at or above HIGH." in result.output


def test_fail_on_is_honoured(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available(), [finding(Severity.MEDIUM)])])
    result = runner.invoke(cli.app, ["scan", str(tmp_path), "--fail-on", "MEDIUM"])

    assert result.exit_code == 1


def test_no_check_available_exits_three_even_with_no_findings(monkeypatch, tmp_path):
    """A broken toolchain must not be able to look like a clean pass."""
    use_gate(monkeypatch, [StubCheck("checkov", ToolAvailability.missing("checkov", "gone"))])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 3
    assert "Nothing was verified" in result.output
    assert "skipped checkov: gone" in result.output


def test_a_crashed_check_does_not_count_as_verification(monkeypatch, tmp_path):
    class Crasher(StubCheck):
        def run(self, ctx: CheckContext) -> list[Finding]:
            raise RuntimeError("boom")

    use_gate(monkeypatch, [Crasher("checkov", available())])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 3
    assert "FAILED  checkov: RuntimeError: boom" in result.output


def test_missing_directory_is_a_config_error(tmp_path):
    result = runner.invoke(cli.app, ["scan", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_bad_policy_dir_is_a_config_error(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available())])
    result = runner.invoke(cli.app, ["scan", str(tmp_path), "--policy-dir", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_non_positive_timeout_is_a_config_error(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available())])
    result = runner.invoke(cli.app, ["scan", str(tmp_path), "--timeout", "0"])
    assert result.exit_code == 2


def test_json_format_reports_what_ran(monkeypatch, tmp_path):
    checks = [
        StubCheck("checkov", available(), [finding(Severity.HIGH)]),
        StubCheck("terraform_validate", ToolAvailability.missing("terraform", "not on PATH")),
    ]
    use_gate(monkeypatch, checks)
    result = runner.invoke(cli.app, ["scan", str(tmp_path), "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["summary"] == {
        "findings": 1,
        "blocking": 1,
        "checks_ran": ["checkov"],
        "checks_skipped": 1,
        "checks_failed": 0,
    }
    assert payload["checks_skipped"][0]["reason"] == "not on PATH"
    assert payload["findings"][0]["rule_class"] == "s3.acl.public"


def test_terminal_report_names_the_tool_and_rule(monkeypatch, tmp_path):
    use_gate(monkeypatch, [StubCheck("checkov", available(), [finding(Severity.CRITICAL)])])
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])

    assert "Checks run: checkov 3.3.15" in result.output
    assert "[checkov CKV_AWS_20]" in result.output
    assert "main.tf:12" in result.output


def test_doctor_lists_available_and_missing(monkeypatch):
    checks = [
        StubCheck("checkov", available()),
        StubCheck("terraform_validate", ToolAvailability.missing("terraform", "not on PATH")),
    ]
    use_gate(monkeypatch, checks)
    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "ok       checkov" in result.output
    assert "missing  terraform_validate   not on PATH" in result.output
    assert "1 of 2 checks can run." in result.output


def test_doctor_exits_three_when_nothing_can_run(monkeypatch):
    use_gate(monkeypatch, [StubCheck("checkov", ToolAvailability.missing("checkov", "gone"))])
    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 3
    assert "No checks can run." in result.output


@pytest.mark.parametrize("command", ["verify", "explain", "exceptions"])
def test_unbuilt_commands_are_absent_rather_than_stubbed(command):
    result = runner.invoke(cli.app, [command, "."])
    assert result.exit_code != 0
