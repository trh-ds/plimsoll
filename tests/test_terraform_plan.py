"""The `terraform plan` adapter, against a recorded `-json` message stream.

See `tests/fixtures/terraform_plan/README.md` for the commands and versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plimsoll.checks.terraform_plan import (
    parse_plan_diagnostics,
    parse_plan_output,
    plan_provider,
)
from plimsoll.core.gate import CheckContext
from plimsoll.core.models import CheckConfig, CheckSource, Severity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "terraform_plan"


def stream(name: str) -> str:
    return (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8")


def test_a_successful_plan_produces_no_findings():
    # The normal case. Every line is version, planned_change, or change_summary.
    assert parse_plan_output(stream("insecure_s3")) == []
    assert parse_plan_output(stream("clean")) == []


def test_a_provider_diagnostic_becomes_a_finding():
    (finding,) = parse_plan_output(stream("provider_diagnostics"))

    assert finding.source is CheckSource.TERRAFORM_PLAN
    assert finding.severity is Severity.HIGH
    assert finding.resource_address == "aws_iam_role.bad_policy"
    assert finding.resource_type == "aws_iam_role"
    assert finding.file == Path("main.tf")
    assert finding.line_start == 17
    assert "invalid JSON policy" in finding.message


def test_the_rule_class_is_slugged_from_the_summary():
    (finding,) = parse_plan_output(stream("provider_diagnostics"))
    assert finding.rule_class.startswith("terraform.plan.")
    assert " " not in finding.rule_class


def test_the_whole_diagnostic_is_kept_as_raw_and_redacted():
    # The captured diagnostic carries a `snippet` holding the offending source line,
    # which for a credential finding is the credential.
    (finding,) = parse_plan_output(stream("provider_diagnostics"))
    assert "snippet" in finding.raw

    leaky = (
        '{"type":"diagnostic","diagnostic":{"severity":"error","summary":"bad",'
        '"snippet":{"code":"  secret_key = \\"AKIAIOSFODNN7EXAMPLE\\""}}}'
    )
    (finding,) = parse_plan_output(leaky)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(finding.raw)


def test_non_json_lines_are_skipped_rather_than_fatal():
    mixed = (
        "Acquiring state lock. This may take a few moments...\n"
        + stream("provider_diagnostics")
        + "\n{ not json at all\n\n"
    )
    assert len(parse_plan_output(mixed)) == 1


def test_only_diagnostic_messages_are_read():
    diagnostics = parse_plan_diagnostics(stream("insecure_s3"))
    assert diagnostics == []
    # ...but the stream itself is not empty; the adapter is filtering, not failing.
    assert stream("insecure_s3").count('"type":"planned_change"') > 0


def test_a_diagnostic_with_no_range_still_produces_a_finding():
    bare = '{"type":"diagnostic","diagnostic":{"severity":"warning","summary":"deprecated"}}'
    (finding,) = parse_plan_output(bare)
    assert finding.line_start == 0
    assert finding.file == Path("")
    assert finding.severity is not Severity.HIGH


@pytest.mark.parametrize("text", ["", "\n\n", "not json\nnor this\n"])
def test_an_empty_or_unparseable_stream_is_empty(text):
    assert parse_plan_output(text) == []


def test_the_plan_provider_returns_none_without_terraform(monkeypatch, tmp_path):
    monkeypatch.setattr("plimsoll.checks.terraform_cli.executable", lambda: None)
    assert plan_provider(CheckContext(tmp_path, CheckConfig())) is None
