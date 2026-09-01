"""SARIF and the PR comment.

The property both formats have to hold is the one the terminal already holds: a run in
which nothing could be verified must not read as a clean run. In SARIF that is
`invocations[]`, because the code-scanning UI shows results and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from plimsoll import cli
from plimsoll.core.gate import CheckContext, Gate
from plimsoll.core.models import (
    CheckSource,
    Decision,
    DecisionStatus,
    ExceptionRef,
    FailedCheck,
    Finding,
    GateResult,
    Patch,
    PatchOutcome,
    PatchVerification,
    Severity,
    SkippedCheck,
    ToolAvailability,
    ToolVersion,
    VerifyResult,
)
from plimsoll.reporters.github import render_github, render_verify_github
from plimsoll.reporters.sarif import (
    SCHEMA_URI,
    build_sarif,
    level_for,
    render_sarif,
    render_verify_sarif,
)

runner = CliRunner()


def finding(
    severity: Severity = Severity.CRITICAL,
    rule_class: str = "s3.acl.public",
    line: int = 3,
) -> Finding:
    return Finding(
        # Distinct per rule class: `Finding.id` keys on rule_id, not rule_class, so a
        # shared id would make three findings look like one to every reporter.
        rule_id="CKV_AWS_20" if rule_class == "s3.acl.public" else rule_class.upper(),
        source=CheckSource.OPA,
        rule_class=rule_class,
        severity=severity,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=line,
        line_end=line + 2,
        message="the bucket is publicly readable",
    )


def gate(**overrides) -> GateResult:
    fields = {
        "findings": [finding()],
        "tooling": [ToolVersion(name="opa", version="0.68.0")],
    }
    fields.update(overrides)
    return GateResult(**fields)


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        (Severity.CRITICAL, "error"),
        (Severity.HIGH, "error"),
        (Severity.MEDIUM, "warning"),
        (Severity.LOW, "note"),
        (Severity.INFO, "note"),
    ],
)
def test_severity_maps_to_a_sarif_level(severity, level):
    assert level_for(severity) == level


def test_sarif_is_well_formed_and_carries_the_rule_metadata():
    log = build_sarif(gate())
    run = log["runs"][0]

    assert log["version"] == "2.1.0"
    assert run["tool"]["driver"]["name"] == "plimsoll"
    (rule,) = run["tool"]["driver"]["rules"]
    assert rule["id"] == "s3.acl.public"
    assert rule["properties"]["benchmark-refs"] == ["CIS-AWS-2.1.4"]

    (result,) = run["results"]
    assert result["ruleId"] == "s3.acl.public"
    assert result["level"] == "error"
    assert result["properties"]["tool-rule-id"] == "CKV_AWS_20"
    region = result["locations"][0]["physicalLocation"]["region"]
    assert (region["startLine"], region["endLine"]) == (3, 5)


def test_a_zero_line_becomes_line_one():
    # The locator returns 0 for a plan-only finding it cannot place; SARIF regions are
    # 1-based and reject 0.
    log = build_sarif(gate(findings=[finding(line=0)]))
    region = log["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 1


def test_a_run_where_nothing_verified_says_so_in_the_invocation():
    log = build_sarif(
        gate(findings=[], tooling=[], checks_skipped=[SkippedCheck(name="opa", reason="no plan")])
    )
    invocation = log["runs"][0]["invocations"][0]

    assert invocation["executionSuccessful"] is False
    texts = [n["message"]["text"] for n in invocation["toolExecutionNotifications"]]
    assert any("nothing in this repository was verified" in t for t in texts)
    assert any("opa did not run" in t for t in texts)


def test_a_failed_check_is_an_error_notification():
    log = build_sarif(gate(checks_failed=[FailedCheck(name="checkov", error="boom")]))
    invocation = log["runs"][0]["invocations"][0]

    assert invocation["executionSuccessful"] is False
    assert any(n["level"] == "error" for n in invocation["toolExecutionNotifications"])


def test_a_timed_out_run_is_flagged_in_the_invocation():
    log = build_sarif(gate(timed_out=True))
    invocation = log["runs"][0]["invocations"][0]

    assert invocation["exitSignalName"] == "TIMEOUT"
    assert any("partial" in n["message"]["text"] for n in invocation["toolExecutionNotifications"])


def test_a_clean_run_is_successful():
    log = build_sarif(gate(findings=[]))
    assert log["runs"][0]["invocations"][0]["executionSuccessful"] is True


def fixed() -> Decision:
    return Decision(
        finding=finding(),
        status=DecisionStatus.FIXED,
        patch=Patch(
            target_finding_id=finding().id,
            attempt=2,
            files_touched=[Path("main.tf")],
            new_content="fixed\n",
            diff='-  acl = "public-read"\n+  acl = "private"\n',
        ),
        verification=PatchVerification(outcome=PatchOutcome.VERIFIED, target_resolved=True),
        attempts=2,
    )


def escalated() -> Decision:
    return Decision(
        finding=finding(Severity.HIGH, "iam.action.wildcard"),
        status=DecisionStatus.ESCALATED,
        verification=PatchVerification(
            outcome=PatchOutcome.INEFFECTIVE,
            failure_reason="iam.action.wildcard is still reported",
        ),
        attempts=3,
    )


def exempted() -> Decision:
    return Decision(
        finding=finding(Severity.MEDIUM, "s3.logging.missing"),
        status=DecisionStatus.EXEMPTED,
        exception_ref=ExceptionRef(
            rule_class="s3.logging.missing",
            resource_address="aws_s3_bucket.reports",
            justification="logged upstream at the CDN",
            approved_by="trh-ds",
        ),
    )


def verify_result() -> VerifyResult:
    decisions = [fixed(), escalated(), exempted()]
    return VerifyResult(
        gate=gate(findings=[d.finding for d in decisions]),
        decisions=decisions,
        llm_used=True,
        llm_description="scripted",
    )


def test_sarif_carries_the_verification_status():
    log = json.loads(render_verify_sarif(verify_result(), fail_on=Severity.HIGH))
    by_rule = {r["ruleId"]: r for r in log["runs"][0]["results"]}

    assert by_rule["s3.acl.public"]["properties"]["plimsoll-status"] == "FIXED"
    assert "passed re-verification" in by_rule["s3.acl.public"]["message"]["text"]
    assert "Could not fix" in by_rule["iam.action.wildcard"]["message"]["text"]


def test_an_exempted_finding_is_suppressed_in_sarif():
    log = json.loads(render_verify_sarif(verify_result(), fail_on=Severity.HIGH))
    by_rule = {r["ruleId"]: r for r in log["runs"][0]["results"]}

    suppressions = by_rule["s3.logging.missing"]["suppressions"]
    assert suppressions[0]["justification"] == "logged upstream at the CDN"


def test_no_unverified_patch_reaches_the_sarif_or_the_comment():
    # ESCALATED decisions carry no patch by construction; assert the reporters do not
    # invent one from the verification.
    result = verify_result()
    for text in (
        render_verify_sarif(result, fail_on=Severity.HIGH),
        render_verify_github(result, fail_on=Severity.HIGH),
    ):
        assert "iam.action.wildcard" in text
        assert text.count("```diff") <= 1


def test_the_pr_comment_leads_with_the_summary_and_names_what_ran():
    comment = render_verify_github(verify_result(), fail_on=Severity.HIGH)

    assert comment.startswith("## Plimsoll")
    assert "1 fixed and verified" in comment
    assert "Checks run: opa 0.68.0" in comment


def test_the_pr_comment_folds_the_verified_diff_and_explains_the_escalation():
    comment = render_verify_github(verify_result(), fail_on=Severity.HIGH)

    assert "<details>" in comment
    assert "Verified fix (attempt 2)" in comment
    assert "Could not fix" in comment
    assert "No unverified patch is offered" in comment


def test_the_pr_comment_orders_blocking_findings_first():
    comment = render_verify_github(verify_result(), fail_on=Severity.HIGH)
    assert comment.index("### Needs a human") < comment.index("### Fixed and verified")


def test_the_pr_comment_says_when_nothing_was_verified():
    comment = render_github(gate(findings=[], tooling=[]), fail_on=Severity.HIGH)
    assert "Nothing was verified" in comment


def test_a_clean_scan_comment_is_short_and_positive():
    comment = render_github(gate(findings=[]), fail_on=Severity.HIGH)
    assert "No findings. ✅" in comment


def test_an_expired_exception_is_called_out_in_the_comment():
    lapsed = exempted().model_copy(
        update={
            "status": DecisionStatus.BLOCKED,
            "exception_ref": exempted().exception_ref.model_copy(update={"expired": True}),
        }
    )
    comment = render_verify_github(
        VerifyResult(gate=gate(findings=[lapsed.finding]), decisions=[lapsed]),
        fail_on=Severity.HIGH,
    )
    assert "Exception expired" in comment


def test_render_sarif_is_valid_json():
    json.loads(render_sarif(gate(), fail_on=Severity.HIGH))


class StubCheck:
    name = "opa"

    def is_available(self) -> ToolAvailability:
        return ToolAvailability.ok("opa", "0.68.0")

    def run(self, ctx: CheckContext) -> list[Finding]:
        return [finding()]


@pytest.mark.parametrize("output_format", ["sarif", "github"])
@pytest.mark.parametrize("command", ["scan", "verify"])
def test_the_cli_accepts_the_new_formats(monkeypatch, tmp_path, command, output_format):
    monkeypatch.delenv("PLIMSOLL_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "default_gate", lambda: Gate([StubCheck()]))

    result = runner.invoke(cli.app, [command, str(tmp_path), "--format", output_format])

    assert result.exit_code == 1
    if output_format == "sarif":
        assert json.loads(result.output)["version"] == "2.1.0"
    else:
        assert result.output.startswith("## Plimsoll")


def test_the_workflow_validates_against_the_schema_we_advertise():
    """`$schema` is a promise that some validator can act on.

    The URL was wrong once already: it pointed into a branch layout the sarif-spec
    repository has since rearranged, so every record we emitted named a 404. Nothing in
    the suite noticed, because nothing fetched it. This does not fetch it either -- it
    asserts that the integration workflow, which does, is fetching the same one.
    """
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "integration.yml"
    assert SCHEMA_URI in workflow.read_text(encoding="utf-8")
