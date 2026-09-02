from pathlib import Path

import pytest

from plimsoll.core.context import (
    ContextError,
    build_explain_prompt,
    build_patch_prompt,
    extract_block,
    resolve_file,
)
from plimsoll.core.exceptions import ExceptionEntry, ExceptionStore
from plimsoll.core.models import CheckSource, Finding, Severity
from plimsoll.core.redaction import Redactor

MAIN_TF = """terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

resource "aws_s3_bucket" "reports" {
  bucket = "acme-reports"
  acl    = "public-read"
}

resource "aws_sns_topic" "alerts" {
  name = "alerts"
}
"""


def finding(**overrides: object) -> Finding:
    base = dict(
        source=CheckSource.CHECKOV,
        rule_id="CKV_AWS_20",
        rule_class="s3.acl.public",
        severity=Severity.CRITICAL,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=10,
        line_end=10,
        message="S3 bucket is public",
    )
    base.update(overrides)
    return Finding(**base)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.tf").write_text(MAIN_TF, encoding="utf-8")
    return tmp_path


def test_extract_block_finds_the_enclosing_resource():
    block = extract_block(MAIN_TF, 10)
    assert block.startswith('resource "aws_s3_bucket" "reports"')
    assert block.rstrip().endswith("}")
    # The neighbouring resource is not swept in.
    assert "aws_sns_topic" not in block


def test_extract_block_handles_nested_braces():
    block = extract_block(MAIN_TF, 3)
    assert block.startswith("terraform {")
    assert block.count("{") == block.count("}")


def test_extract_block_falls_back_to_surrounding_lines():
    block = extract_block("just\nsome\nlines\nhere\n", 2)
    assert "some" in block


def test_extract_block_on_empty_content():
    assert extract_block("", 1) == ""


def test_resolve_file_rejects_paths_outside_the_workspace(workspace):
    with pytest.raises(ContextError, match="outside the workspace"):
        resolve_file(workspace, finding(file=Path("../../etc/passwd")))


def test_resolve_file_refuses_state_files(workspace):
    (workspace / "terraform.tfstate").write_text("{}", encoding="utf-8")
    with pytest.raises(ContextError, match="never sent"):
        resolve_file(workspace, finding(file=Path("terraform.tfstate")))


def test_resolve_file_accepts_an_absolute_path_inside_the_workspace(workspace):
    resolved = resolve_file(workspace, finding(file=workspace / "main.tf"))
    assert resolved.name == "main.tf"


def test_patch_prompt_contains_the_whole_file(workspace):
    prompt = build_patch_prompt(finding(), workspace, Redactor())
    assert "aws_sns_topic" in prompt
    assert "public-read" in prompt
    assert "Return the complete, corrected content of main.tf" in prompt
    assert "s3.acl.public" in prompt


def test_patch_prompt_includes_the_previous_failure_on_retry(workspace):
    prompt = build_patch_prompt(finding(), workspace, Redactor(), "still reports s3.acl.public")
    assert "still reports s3.acl.public" in prompt
    assert "Do not repeat it" in prompt


def test_patch_prompt_redacts_credentials(workspace):
    (workspace / "main.tf").write_text(
        MAIN_TF + '\nprovider "aws" {\n  secret_key = "AKIAIOSFODNN7EXAMPLE"\n}\n',
        encoding="utf-8",
    )
    prompt = build_patch_prompt(finding(), workspace, Redactor())
    assert "AKIAIOSFODNN7EXAMPLE" not in prompt
    assert "[REDACTED:1]" in prompt


def test_patch_prompt_refuses_an_oversized_file(workspace, monkeypatch):
    import plimsoll.core.context as ctx

    monkeypatch.setattr(ctx, "MAX_FILE_CHARS", 10)
    with pytest.raises(ContextError, match="over the"):
        build_patch_prompt(finding(), workspace, Redactor())


def test_patch_prompt_refuses_a_missing_file(workspace):
    with pytest.raises(ContextError, match="does not exist"):
        build_patch_prompt(finding(file=Path("gone.tf")), workspace, Redactor())


def test_explain_prompt_sends_only_the_block(workspace):
    prompt = build_explain_prompt(finding(), workspace, Redactor())
    assert "public-read" in prompt
    assert "aws_sns_topic" not in prompt


def test_explain_degrades_when_the_file_is_missing(workspace):
    """An explanation is still worth having from the checker's own message."""
    prompt = build_explain_prompt(finding(file=Path("gone.tf")), workspace, Redactor())
    assert "(source unavailable)" in prompt
    assert "S3 bucket is public" in prompt


def _store_with(**entry_overrides: object) -> ExceptionStore:
    base = dict(
        rule_class="s3.acl.public",
        resource_address="aws_s3_bucket.other",
        justification="legacy bucket, migration tracked in JIRA-1",
        approved_by="alice",
    )
    base.update(entry_overrides)
    return ExceptionStore([ExceptionEntry(**base)])


def test_patch_prompt_cites_prior_accepted_exceptions_for_the_same_rule_class(workspace):
    prompt = build_patch_prompt(finding(), workspace, Redactor(), exceptions=_store_with())
    assert "aws_s3_bucket.other" in prompt
    assert "legacy bucket, migration tracked in JIRA-1" in prompt
    assert "alice" in prompt


def test_patch_prompt_omits_precedent_for_a_different_rule_class(workspace):
    store = _store_with(rule_class="iam.wildcard.action")
    prompt = build_patch_prompt(finding(), workspace, Redactor(), exceptions=store)
    assert "aws_s3_bucket.other" not in prompt


def test_patch_prompt_says_nothing_when_no_store_is_given(workspace):
    prompt = build_patch_prompt(finding(), workspace, Redactor())
    assert "accepted as an exception elsewhere" not in prompt


def test_explain_prompt_cites_prior_accepted_exceptions_for_the_same_rule_class(workspace):
    prompt = build_explain_prompt(finding(), workspace, Redactor(), exceptions=_store_with())
    assert "aws_s3_bucket.other" in prompt
    assert "accepted as an exception elsewhere" in prompt
    # The instruction against suggesting an exception still stands alongside the precedent.
    assert "Do not suggest suppressing" in prompt
