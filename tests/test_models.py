from pathlib import Path

import pytest

from plimsoll.core.models import (
    CheckSource,
    Finding,
    GateResult,
    Severity,
    ToolAvailability,
    ToolVersion,
    compute_finding_id,
)


def make_finding(**overrides: object) -> Finding:
    kwargs = {
        "source": CheckSource.TERRAFORM_VALIDATE,
        "rule_id": "terraform_validate.unsupported_argument",
        "rule_class": "terraform.validate.unsupported_argument",
        "severity": Severity.HIGH,
        "resource_address": "aws_s3_bucket.reports",
        "resource_type": "aws_s3_bucket",
        "file": Path("main.tf"),
        "line_start": 12,
        "line_end": 12,
        "message": "Unsupported argument",
    }
    kwargs.update(overrides)
    return Finding(**kwargs)


def test_id_is_derived_and_stable():
    first = make_finding()
    second = make_finding()
    assert first.id == second.id
    assert first.id == compute_finding_id(
        source=CheckSource.TERRAFORM_VALIDATE,
        rule_id="terraform_validate.unsupported_argument",
        resource_address="aws_s3_bucket.reports",
        file=Path("main.tf"),
        line_start=12,
    )


def test_id_distinguishes_same_rule_on_different_lines():
    # The reason line_start is in the id: two bad arguments in one resource are two
    # findings, and they must not collapse into one.
    assert make_finding(line_start=12).id != make_finding(line_start=19).id


def test_explicit_id_is_preserved():
    assert make_finding(id="fixed").id == "fixed"


@pytest.mark.parametrize(
    ("severity", "threshold", "expected"),
    [
        (Severity.CRITICAL, Severity.HIGH, True),
        (Severity.HIGH, Severity.HIGH, True),
        (Severity.MEDIUM, Severity.HIGH, False),
        # Alphabetically "MEDIUM" > "HIGH"; ranked, it is not. This is why Severity
        # exposes meets() instead of comparison operators.
        (Severity.INFO, Severity.CRITICAL, False),
        (Severity.LOW, Severity.INFO, True),
    ],
)
def test_severity_threshold(severity, threshold, expected):
    assert severity.meets(threshold) is expected


def test_gate_result_reports_nothing_ran():
    empty = GateResult()
    assert empty.any_check_ran is False
    assert GateResult(tooling=[ToolVersion(name="terraform", version="1.9.5")]).any_check_ran


def test_findings_at_or_above_threshold():
    result = GateResult(
        findings=[
            make_finding(severity=Severity.HIGH, line_start=1),
            make_finding(severity=Severity.LOW, line_start=2),
        ]
    )
    assert [f.severity for f in result.findings_at_or_above(Severity.HIGH)] == [Severity.HIGH]


def test_tool_availability_constructors():
    assert ToolAvailability.ok("terraform", "1.9.5").available is True
    missing = ToolAvailability.missing("terraform", "not on PATH")
    assert missing.available is False
    assert missing.reason == "not on PATH"
