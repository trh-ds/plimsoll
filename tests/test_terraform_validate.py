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
    assert (found.line_start, found.line_end) == (3, 3)
    assert found.message.startswith("Unsupported argument")
    assert 'named "encryption_enabled" is not expected' in found.message
    assert found.id
    # The whole diagnostic survives for the context builder to slice later.
    assert found.raw["snippet"]["code"] == "  encryption_enabled = true"


def test_mixed_diagnostics():
    unconfigurable, undeclared_ref, bad_type = tv.parse_validate_output(load("mixed_diagnostics"))

    # A diagnostic inside a data block, addressed through snippet.context.
    assert unconfigurable.severity is Severity.HIGH
    assert unconfigurable.resource_address == "data.aws_s3_bucket.existing"
    assert unconfigurable.resource_type == "aws_s3_bucket"

    assert undeclared_ref.rule_class == "terraform.validate.reference_to_undeclared_resource"
    assert undeclared_ref.resource_address == "aws_iam_role_policy.reports_access"

    # The hallucinated-resource-type case this adapter exists to catch.
    assert bad_type.rule_class == "terraform.validate.invalid_resource_type"
    assert bad_type.resource_address == "aws_s3_buckets.typo"


def test_warnings_are_medium_and_use_the_address_field():
    argument, resource = tv.parse_validate_output(load("deprecation_warnings"))

    # MEDIUM by the explicit table, never inferred from the wording.
    assert argument.severity is Severity.MEDIUM
    assert resource.severity is Severity.MEDIUM
    # Provider diagnostics carry `address`, which wins over snippet.context.
    assert argument.raw["address"] == "aws_s3_bucket_object.legacy"
    assert argument.resource_address == "aws_s3_bucket_object.legacy"
    assert argument.resource_type == "aws_s3_bucket_object"


def test_an_uninitialised_workspace_is_a_failure_not_a_finding():
    """terraform saw no provider schema, so it validated nothing.

    Reporting the one diagnostic it did emit would make a check that verified nothing
    look like a check that ran and found a single problem.
    """
    payload = load("uninitialized")
    assert payload["diagnostics"][0]["summary"] == "Missing required provider"

    with pytest.raises(CheckExecutionError, match="not initialised"):
        tv.parse_validate_output(payload)


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
    monkeypatch.setattr(tv.terraform_cli, "executable", lambda: None)
    availability = tv.TerraformValidateCheck().is_available()

    assert availability.available is False
    assert availability.tool == "terraform"
    assert "PATH" in (availability.reason or "")


def test_unavailable_when_terraform_cannot_execute(monkeypatch):
    monkeypatch.setattr(tv.terraform_cli, "executable", lambda: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("exec format error"))
    )
    assert tv.TerraformValidateCheck().is_available().available is False


def test_available_reports_version(monkeypatch):
    monkeypatch.setattr(tv.terraform_cli, "executable", lambda: "/usr/bin/terraform")
    monkeypatch.setattr(
        tv.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout='{"terraform_version": "1.9.5"}', stderr=""
        ),
    )
    availability = tv.TerraformValidateCheck().is_available()
    assert (availability.available, availability.version) == (True, "1.9.5")


def fake_terraform(returncode: int, stdout: str, stderr: str = ""):
    """Stand in for `terraform_cli.run_terraform`, which also skips `terraform init`."""

    def run(ctx, args, **kwargs):
        return subprocess.CompletedProcess(["terraform", *args], returncode, stdout, stderr)

    return run


def test_run_parses_stdout(monkeypatch, tmp_path):
    payload = (FIXTURES / "unsupported_argument.json").read_text(encoding="utf-8")
    # Exit code 1 means "invalid", which is the case we care about.
    monkeypatch.setattr(tv.terraform_cli, "run_terraform", fake_terraform(1, payload))
    findings = tv.TerraformValidateCheck().run(CheckContext(tmp_path, CheckConfig()))
    assert [f.rule_class for f in findings] == ["terraform.validate.unsupported_argument"]


def test_run_raises_when_output_is_not_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tv.terraform_cli,
        "run_terraform",
        fake_terraform(1, "", "Error: Missing required argument"),
    )
    with pytest.raises(CheckExecutionError, match="did not return JSON"):
        tv.TerraformValidateCheck().run(CheckContext(tmp_path, CheckConfig()))


def test_gate_skips_the_adapter_on_a_machine_without_terraform(monkeypatch, tmp_path):
    """The unit suite must pass with no terraform installed."""
    monkeypatch.setattr(tv.terraform_cli, "executable", lambda: None)
    result = Gate([tv.TerraformValidateCheck()]).run(tmp_path)

    assert result.any_check_ran is False
    assert [s.name for s in result.checks_skipped] == ["terraform_validate"]
    assert result.checks_failed == []


CONFIG_WITH_A_BAD_ARGUMENT = """
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_bucket" "reports" {
  bucket             = "plimsoll-slow-test"
  encryption_enabled = true
}
"""


def _require_terraform() -> tv.TerraformValidateCheck:
    import shutil as real_shutil

    if real_shutil.which("terraform") is None:
        pytest.skip("terraform not installed")
    check = tv.TerraformValidateCheck()
    if not check.is_available().available:
        pytest.skip("terraform on PATH but not executable")
    return check


@pytest.mark.slow
def test_uninitialised_workspace_is_initialised_without_being_written_to(tmp_path):
    """An uninitialised workspace is handled, and stays uninitialised afterwards.

    This used to raise: without a provider schema terraform reports only that it cannot
    proceed, and the adapter refused rather than passing that off as the file's findings.
    It now initialises a throwaway copy instead, so the real diagnostic is reached. The
    second half of the assertion is the part that matters -- `terraform init` writes
    `.terraform/` and `.terraform.lock.hcl` into its working directory, and the user's
    workspace must not be that directory.
    """
    check = _require_terraform()
    (tmp_path / "main.tf").write_text(CONFIG_WITH_A_BAD_ARGUMENT, encoding="utf-8")

    findings = check.run(CheckContext(tmp_path, CheckConfig(timeout_seconds=600)))

    assert [f.rule_class for f in findings] == ["terraform.validate.unsupported_argument"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["main.tf"]


@pytest.mark.slow
def test_against_real_terraform(tmp_path):
    """Shells out to a real terraform, after a real init. Downloads the AWS provider."""
    check = _require_terraform()
    (tmp_path / "main.tf").write_text(CONFIG_WITH_A_BAD_ARGUMENT, encoding="utf-8")

    init = subprocess.run(
        ["terraform", "init", "-no-color", "-input=false"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip(f"terraform init failed (offline?): {init.stderr.strip()[:200]}")

    findings = check.run(CheckContext(tmp_path, CheckConfig(timeout_seconds=600)))

    # Not just "some findings" -- the specific one, so this test cannot pass on a
    # diagnostic that says terraform could not do its job.
    assert [f.rule_class for f in findings] == ["terraform.validate.unsupported_argument"]
    assert findings[0].resource_address == "aws_s3_bucket.reports"
