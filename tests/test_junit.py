"""JUnit XML: the pipeline widget and the exit code must agree about the same run."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from plimsoll.core.models import (
    CheckSource,
    Decision,
    DecisionStatus,
    ExceptionRef,
    FailedCheck,
    Finding,
    GateResult,
    Severity,
    SkippedCheck,
    SuppressedFinding,
    SuppressionKind,
    ToolVersion,
    VerifyResult,
)
from plimsoll.reporters.junit import render_junit, render_verify_junit


def make(severity: Severity, rule: str = "s3.acl.public", message: str = "public") -> Finding:
    return Finding(
        source=CheckSource.OPA,
        rule_id="PLIM",
        rule_class=rule,
        severity=severity,
        resource_address=f"aws_s3_bucket.{rule.replace('.', '_')}",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=4,
        line_end=4,
        message=message,
    )


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("\n", 1)[1])


def cases(root: ET.Element, child: str) -> list[ET.Element]:
    return [c for c in root.iter("testcase") if c.find(child) is not None]


def test_only_findings_at_the_threshold_fail():
    result = GateResult(
        findings=[make(Severity.CRITICAL), make(Severity.MEDIUM, "s3.versioning.disabled")],
        tooling=[ToolVersion(name="opa", version="1.20.1")],
    )
    root = parse(render_junit(result, fail_on=Severity.HIGH))

    assert [c.get("classname") for c in cases(root, "failure")] == ["s3.acl.public"]
    assert root.get("failures") == "1"
    assert root.get("tests") == "3"  # the check itself, and both findings


def test_a_run_where_nothing_ran_is_an_error_not_an_empty_suite():
    result = GateResult(checks_skipped=[SkippedCheck(name="opa", reason="not on PATH")])
    root = parse(render_junit(result, fail_on=Severity.HIGH))

    assert root.get("errors") == "1"
    assert root.get("skipped") == "1"


def test_failed_checks_are_errors():
    result = GateResult(
        tooling=[ToolVersion(name="checkov", version="3")],
        checks_failed=[FailedCheck(name="terraform_plan", error="credentials rejected")],
    )
    (errored,) = cases(parse(render_junit(result, fail_on=Severity.HIGH)), "error")
    assert errored.get("name") == "terraform_plan"


def test_suppressed_findings_are_skipped_with_the_reason():
    ref = ExceptionRef(
        rule_class="s3.acl.public",
        resource_address="*",
        justification="static site",
        approved_by="alice",
    )
    result = GateResult(
        tooling=[ToolVersion(name="opa")],
        suppressed=[
            SuppressedFinding(
                finding=make(Severity.CRITICAL), kind=SuppressionKind.EXCEPTION, exception_ref=ref
            ),
            SuppressedFinding(finding=make(Severity.HIGH, "rds"), kind=SuppressionKind.BASELINE),
        ],
    )
    root = parse(render_junit(result, fail_on=Severity.HIGH))
    reasons = [c.find("skipped").get("message") for c in cases(root, "skipped")]

    assert reasons == ["exempted by alice: static site", "present in the baseline"]
    assert root.get("failures") == "0"


def test_markup_in_a_message_is_escaped_not_interpreted():
    result = GateResult(
        findings=[make(Severity.HIGH, message='<script>alert("x")</script> & more')],
        tooling=[ToolVersion(name="opa")],
    )
    (failed,) = cases(parse(render_junit(result, fail_on=Severity.HIGH)), "failure")
    assert '<script>alert("x")</script> & more' in failed.find("failure").get("message")


def test_verify_fails_exactly_the_unresolved_blocking_decisions():
    critical, high, fixed = (
        make(Severity.CRITICAL),
        make(Severity.HIGH, "iam.action.wildcard"),
        make(Severity.HIGH, "rds"),
    )
    result = VerifyResult(
        gate=GateResult(findings=[critical, high, fixed], tooling=[ToolVersion(name="opa")]),
        decisions=[
            Decision(finding=critical, status=DecisionStatus.BLOCKED),
            Decision(finding=high, status=DecisionStatus.ESCALATED, attempts=2),
            Decision(finding=fixed, status=DecisionStatus.REPORTED),
        ],
    )
    root = parse(render_verify_junit(result, fail_on=Severity.HIGH))
    failed = cases(root, "failure")

    assert {c.get("classname") for c in failed} == {"s3.acl.public", "iam.action.wildcard"}
    assert "needs a human" in failed[1].find("failure").text
