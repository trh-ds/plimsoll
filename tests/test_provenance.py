"""Provenance: stable hashing, no secrets, and an honest attribution."""

from __future__ import annotations

import json
from datetime import date
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
from plimsoll.core.provenance import (
    SCHEMA_VERSION,
    UNKNOWN_AUTHOR,
    ProvenanceRecord,
    build_record,
    canonical_json,
    content_hash,
    detect_generated_by,
    pr_number_from_env,
    summarise,
    workspace_hash,
    write_record,
)

runner = CliRunner()

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "provenance-0.1.json"

SECRET = "AKIAIOSFODNN7EXAMPLE"


def finding(severity: Severity = Severity.HIGH, message: str = "public bucket") -> Finding:
    return Finding(
        source=CheckSource.OPA,
        rule_id="s3.acl.public",
        rule_class="s3.acl.public",
        severity=severity,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=1,
        line_end=4,
        message=message,
    )


def fixed_decision(diff: str = "-acl = public-read\n+acl = private\n") -> Decision:
    return Decision(
        finding=finding(),
        status=DecisionStatus.FIXED,
        patch=Patch(
            target_finding_id=finding().id,
            attempt=1,
            files_touched=[Path("main.tf")],
            new_content=f'provider "aws" {{ secret_key = "{SECRET}" }}\n',
            diff=diff,
            rationale="Set the ACL to private.",
        ),
        verification=PatchVerification(outcome=PatchOutcome.VERIFIED, target_resolved=True),
        attempts=1,
    )


def result_with(*decisions: Decision) -> VerifyResult:
    return VerifyResult(
        gate=GateResult(
            findings=[d.finding for d in decisions],
            tooling=[ToolVersion(name="opa", version="0.68.0")],
            checks_skipped=[SkippedCheck(name="checkov", reason="not on PATH")],
        ),
        decisions=list(decisions),
    )


def test_canonical_json_is_sorted_and_compact():
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_the_hash_survives_a_round_trip(tmp_path):
    record = build_record(result_with(fixed_decision()), tmp_path)

    assert record.hash_matches()
    path = write_record(record, tmp_path / "out" / "provenance.json")
    reloaded = ProvenanceRecord.model_validate_json(path.read_text(encoding="utf-8"))

    assert reloaded.content_hash == record.content_hash
    assert reloaded.hash_matches()


def test_the_hash_changes_when_a_decision_does(tmp_path):
    one = build_record(result_with(fixed_decision()), tmp_path)
    other = build_record(result_with(fixed_decision(diff="-acl\n+different\n")), tmp_path)
    assert one.content_hash != other.content_hash


def test_content_hash_excludes_itself_and_the_signature(tmp_path):
    record = build_record(result_with(fixed_decision()), tmp_path)
    assert "content_hash" not in record.hashable()
    assert "signature" not in record.hashable()
    assert record.content_hash == content_hash(record.hashable())


def test_the_corrected_file_never_enters_the_record(tmp_path):
    record = build_record(result_with(fixed_decision()), tmp_path)
    serialised = json.dumps(record.model_dump(mode="json"))

    assert SECRET not in serialised
    assert "new_content" not in serialised


def test_a_credential_in_a_diff_is_redacted(tmp_path):
    leaky = fixed_decision(diff=f'+  secret_key = "{SECRET}"\n')
    record = build_record(result_with(leaky), tmp_path)

    assert SECRET not in json.dumps(record.model_dump(mode="json"))
    assert record.decisions[0].patch is not None
    assert "[REDACTED]" in record.decisions[0].patch.diff


def test_a_credential_in_an_explanation_is_redacted(tmp_path):
    decision = Decision(
        finding=finding(),
        status=DecisionStatus.ESCALATED,
        explanation=f"the key {SECRET} is inline",
    )
    record = build_record(result_with(decision), tmp_path)
    assert SECRET not in json.dumps(record.model_dump(mode="json"))


def test_an_exempted_decision_is_attributed_to_its_approver(tmp_path):
    decision = Decision(
        finding=finding(),
        status=DecisionStatus.EXEMPTED,
        exception_ref=ExceptionRef(
            rule_class="s3.acl.public",
            resource_address="aws_s3_bucket.reports",
            justification="public docs site",
            approved_by="trh-ds",
            approved_in="PR #442",
            created=date(2026, 3, 15),
        ),
    )
    record = build_record(result_with(decision), tmp_path)

    recorded = record.decisions[0].exception_ref
    assert recorded is not None
    assert (recorded.approved_by, recorded.approved_in) == ("trh-ds", "PR #442")
    assert record.summary.exempted == 1


def test_the_summary_reconciles_to_the_total():
    decisions = [
        fixed_decision(),
        Decision(finding=finding(), status=DecisionStatus.ESCALATED),
        Decision(finding=finding(Severity.LOW), status=DecisionStatus.REPORTED),
        Decision(finding=finding(), status=DecisionStatus.BLOCKED),
    ]
    summary = summarise(decisions)
    parts = summary.fixed + summary.escalated + summary.exempted
    assert parts + summary.blocked + summary.reported == summary.total == 4


def test_workspace_hash_is_stable_and_ignores_provider_downloads(tmp_path):
    (tmp_path / "main.tf").write_text("resource {}\n", encoding="utf-8")
    before = workspace_hash(tmp_path)

    (tmp_path / ".terraform").mkdir()
    (tmp_path / ".terraform" / "provider").write_bytes(b"\x00" * 64)
    (tmp_path / "terraform.tfstate").write_text('{"secret": "x"}', encoding="utf-8")
    assert workspace_hash(tmp_path) == before

    (tmp_path / "main.tf").write_text("resource { changed = true }\n", encoding="utf-8")
    assert workspace_hash(tmp_path) != before


def test_workspace_hash_covers_the_exception_store(tmp_path):
    # The store is an input to every decision, so a change to it must change the hash.
    (tmp_path / "main.tf").write_text("resource {}\n", encoding="utf-8")
    before = workspace_hash(tmp_path)
    (tmp_path / ".plimsoll").mkdir()
    (tmp_path / ".plimsoll" / "exceptions.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    assert workspace_hash(tmp_path) != before


def test_a_rename_between_files_changes_the_hash(tmp_path):
    (tmp_path / "a.tf").write_text("one\n", encoding="utf-8")
    (tmp_path / "b.tf").write_text("two\n", encoding="utf-8")
    split = workspace_hash(tmp_path)

    (tmp_path / "b.tf").unlink()
    (tmp_path / "a.tf").write_text("one\ntwo\n", encoding="utf-8")
    assert workspace_hash(tmp_path) != split


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("refs/pull/512/merge", 512),
        ("refs/pull/7/head", 7),
        ("refs/heads/main", None),
        ("", None),
    ],
)
def test_pr_number_is_read_from_github_ref(ref, expected):
    assert pr_number_from_env({"GITHUB_REF": ref}) == expected


def test_generated_by_prefers_the_flag_then_the_environment(tmp_path):
    env = {"PLIMSOLL_GENERATED_BY": "from-env"}
    assert detect_generated_by(tmp_path, "from-flag", env) == "from-flag"
    assert detect_generated_by(tmp_path, None, env) == "from-env"


def test_generated_by_falls_back_to_unknown(tmp_path):
    assert detect_generated_by(tmp_path, None, {}) == UNKNOWN_AUTHOR


def test_a_non_repository_is_not_an_error(tmp_path):
    record = build_record(result_with(), tmp_path, env={})
    assert record.repository.commit is None
    assert record.schema_version == SCHEMA_VERSION


def test_the_published_schema_matches_the_model():
    """Fails when the model changes and `schemas/provenance-0.1.json` does not.

    The schema is what anyone parsing these records reads. A model that drifts from it
    turns a published contract into a description of an older build.
    """
    published = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert published == ProvenanceRecord.model_json_schema(), (
        "regenerate with: uv run python -m plimsoll.core.schema"
    )


class StubCheck:
    name = "opa"

    def is_available(self) -> ToolAvailability:
        return ToolAvailability.ok("opa", "0.68.0")

    def run(self, ctx: CheckContext) -> list[Finding]:
        return [finding()]


def test_scan_writes_a_record_with_a_decision_per_finding(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "default_gate", lambda: Gate([StubCheck()]))
    out = tmp_path / "provenance.json"

    result = runner.invoke(
        cli.app,
        ["scan", str(tmp_path), "--provenance", str(out), "--generated-by", "claude-code"],
    )

    assert result.exit_code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["generated_by"] == "claude-code"
    assert payload["model"] is None
    assert [d["status"] for d in payload["decisions"]] == ["BLOCKED"]
    assert ProvenanceRecord.model_validate(payload).hash_matches()


def test_verify_writes_a_record(monkeypatch, tmp_path):
    monkeypatch.delenv("PLIMSOLL_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "default_gate", lambda: Gate([StubCheck()]))
    out = tmp_path / "nested" / "provenance.json"

    result = runner.invoke(cli.app, ["verify", str(tmp_path), "--no-llm", "--provenance", str(out)])

    assert result.exit_code == 1
    assert ProvenanceRecord.model_validate_json(out.read_text(encoding="utf-8")).hash_matches()
