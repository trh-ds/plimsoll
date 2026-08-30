import json
import subprocess
from pathlib import Path

import pytest

from plimsoll.checks import terraform_validate as tv
from plimsoll.core.gate import CheckContext, CheckExecutionError, Gate
from plimsoll.core.models import CheckConfig, CheckSource, Severity

FIXTURES = Path(__file__).parent / "fixtures" / "terraform_validate"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_valid_config_produces_no_findings():
    assert tv.parse_validate_output(load("valid")) == []


def test_unsupported_argument_maps_to_a_finding():
    (found,) = tv.parse_validate_output(load("unsupported_argument"))

    assert found.source is CheckSource.TERRAFORM_VALIDATE
    assert found.severity is Severity.HIGH
    assert found.rule_id == "terraform_validate.unsupported_argument"
    assert found.rule_class == "terraform.validate.unsupported_argument"
    assert found.resource_address == "aws_s3_bucket.reports"
    assert found.resource_type == "aws_s3_bucket"
    assert found.file == Path("main.tf")
    assert (found.line_start, found.line_end) == (12, 12)
    assert found.message.startswith("Unsupported argument")
    assert 'named "encryption_enabled" is not expected' in found.message
    assert found.id
    # The whole diagnostic survives for the context builder to slice later.
    assert found.raw["snippet"]["code"] == "  encryption_enabled = true"


def test_mixed_diagnostics():
    invalid_ref, deprecated, no_provider = tv.parse_validate_output(load("mixed_diagnostics"))

    assert invalid_ref.severity is Severity.HIGH
    assert invalid_ref.resource_address == "aws_iam_role_policy.reports_access"

    # A warning is MEDIUM by the explicit table, never inferred from the wording.
    assert deprecated.severity is Severity.MEDIUM
    assert deprecated.resource_address == "data.aws_s3_bucket.existing"
    assert deprecated.resource_type == "aws_s3_bucket"

    # A diagnostic with no range or snippet still parses; it just has no location.
    assert no_provider.resource_address == ""
    assert no_provider.file == Path("")
    assert (no_provider.line_start, no_provider.line_end) == (0, 0)


def test_findings_from_one_file_have_distinct_ids():
    findings = tv.parse_validate_output(load("mixed_diagnostics"))
    assert len({f.id for f in findings}) == len(findings)


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Unsupported argument", "unsupported_argument"),
        ('Invalid resource type: "aws_s3_buckets"', "invalid_resource_type_aws_s3_buckets"),
        ("!!!", "unknown"),
        ("", "unknown"),
    ],
)
def test_slug(summary, expected):
    assert tv._slug(summary) == expected


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ('resource "aws_s3_bucket" "reports"', ("aws_s3_bucket.reports", "aws_s3_bucket")),
        ('data "aws_ami" "latest"', ("data.aws_ami.latest", "aws_ami")),
        ('variable "region"', ("", "")),
        ('provider "aws"', ("", "")),
        (None, ("", "")),
    ],
)
def test_address_from_context(context, expected):
    assert tv._address_from_context(context) == expected


def test_address_field_wins_over_snippet():
    (found,) = tv.parse_validate_output(
        {
            "diagnostics": [
                {
                    "severity": "error",
                    "summary": "Invalid value",
                    "address": "module.reports.aws_s3_bucket.data",
                    "snippet": {"context": 'resource "aws_s3_bucket" "other"'},
                }
            ]
        }
    )
    assert found.resource_address == "module.reports.aws_s3_bucket.data"


def test_non_list_diagnostics_is_an_adapter_failure():
    with pytest.raises(CheckExecutionError):
        tv.parse_validate_output({"diagnostics": {"severity": "error"}})


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ('{"terraform_version": "1.9.5", "platform": "linux_amd64"}', "1.9.5"),
        ("Terraform v1.5.0", None),
        ("", None),
        ("[]", None),
    ],
)
def test_parse_version(stdout, expected):
    assert tv.parse_version(stdout) == expected


def test_unavailable_when_terraform_is_not_installed(monkeypatch):
    monkeypatch.setattr(tv.shutil, "which", lambda _: None)
    availability = tv.TerraformValidateCheck().is_available()

    assert availability.available is False
    assert availability.tool == "terraform"
    assert "PATH" in (availability.reason or "")


def test_unavailable_when_terraform_cannot_execute(monkeypatch):
    monkeypatch.setattr(tv.shutil, "which", lambda _: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("exec format error"))
    )
    assert tv.TerraformValidateCheck().is_available().available is False


def test_available_reports_version(monkeypatch):
    monkeypatch.setattr(tv.shutil, "which", lambda _: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout='{"terraform_version": "1.9.5"}', stderr=""
        ),
    )
    availability = tv.TerraformValidateCheck().is_available()
    assert (availability.available, availability.version) == (True, "1.9.5")


def test_run_parses_stdout(monkeypatch, tmp_path):
    payload = (FIXTURES / "unsupported_argument.json").read_text(encoding="utf-8")
    monkeypatch.setattr(tv.shutil, "which", lambda _: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess,
        "run",
        # Exit code 1 means "invalid", which is the case we care about.
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout=payload, stderr=""),
    )
    findings = tv.TerraformValidateCheck().run(CheckContext(tmp_path, CheckConfig()))
    assert [f.rule_class for f in findings] == ["terraform.validate.unsupported_argument"]


def test_run_raises_when_output_is_not_json(monkeypatch, tmp_path):
    monkeypatch.setattr(tv.shutil, "which", lambda _: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="Error: Missing required argument"
        ),
    )
    with pytest.raises(CheckExecutionError, match="did not return JSON"):
        tv.TerraformValidateCheck().run(CheckContext(tmp_path, CheckConfig()))


def test_gate_skips_the_adapter_on_a_machine_without_terraform(monkeypatch, tmp_path):
    """The unit suite must pass with no terraform installed."""
    monkeypatch.setattr(tv.shutil, "which", lambda _: None)
    result = Gate([tv.TerraformValidateCheck()]).run(tmp_path)

    assert result.any_check_ran is False
    assert [s.name for s in result.checks_skipped] == ["terraform_validate"]
    assert result.checks_failed == []


@pytest.mark.slow
def test_against_real_terraform(tmp_path):
    """Shells out to a real terraform. Excluded from the unit run."""
    import shutil as real_shutil

    if real_shutil.which("terraform") is None:
        pytest.skip("terraform not installed")

    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "reports" {\n  encryption_enabled = true\n}\n',
        encoding="utf-8",
    )
    check = tv.TerraformValidateCheck()
    assert check.is_available().available is True
    findings = check.run(CheckContext(tmp_path, CheckConfig()))
    assert findings, "terraform should reject this configuration"
