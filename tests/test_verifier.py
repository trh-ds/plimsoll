from __future__ import annotations

from pathlib import Path

import pytest

from conftest import INSECURE_TF, SECURE_TF, MarkerCheck, write_workspace
from plimsoll.core.context import ContextError
from plimsoll.core.gate import Gate
from plimsoll.core.models import CheckSource, Finding, GateConfig, PatchOutcome, Severity
from plimsoll.core.verifier import Verifier, build_patch, finding_identity, render_diff


def acl_finding(line_start: int = 3) -> Finding:
    return Finding(
        source=CheckSource.CHECKOV,
        rule_id="MARK_s3.acl.public",
        rule_class="s3.acl.public",
        severity=Severity.CRITICAL,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=line_start,
        line_end=line_start,
        message='acl    = "public-read" found',
    )


def test_identity_ignores_line_numbers():
    """A fix that shifts lines must not read as "every finding replaced"."""
    assert finding_identity(acl_finding(3)) == finding_identity(acl_finding(99))


def test_build_patch_computes_the_diff(tmp_path):
    write_workspace(tmp_path)
    patch = build_patch(tmp_path, acl_finding(), SECURE_TF, attempt=1)

    assert patch.files_touched == [Path("main.tf")]
    assert patch.new_content == SECURE_TF
    assert '+  acl    = "private"' in patch.diff
    assert patch.attempt == 1


def test_build_patch_rejects_empty_output(tmp_path):
    write_workspace(tmp_path)
    with pytest.raises(ContextError, match="empty"):
        build_patch(tmp_path, acl_finding(), "   \n", attempt=1)


def test_build_patch_rejects_an_unchanged_file(tmp_path):
    write_workspace(tmp_path)
    with pytest.raises(ContextError, match="unchanged"):
        build_patch(tmp_path, acl_finding(), INSECURE_TF, attempt=1)


def test_build_patch_strips_a_wrapping_code_fence(tmp_path):
    """The prompt forbids fences. Models add them anyway."""
    write_workspace(tmp_path)
    patch = build_patch(tmp_path, acl_finding(), f"```hcl\n{SECURE_TF}```", attempt=1)
    assert patch.new_content.startswith('resource "aws_s3_bucket"')
    assert "```" not in patch.new_content


def test_verification_runs_the_same_gate(tmp_path):
    """Invariant 2: the object that produced the finding is the object that judges it."""
    write_workspace(tmp_path)
    check = MarkerCheck()
    gate = Gate([check])
    config = GateConfig()

    baseline = gate.run(tmp_path, config)
    verifier = Verifier(gate, config)
    verifier.set_baseline(baseline.findings)

    patch = build_patch(tmp_path, acl_finding(), SECURE_TF, attempt=1)
    verification = verifier.verify(tmp_path, acl_finding(), patch)

    assert verification.outcome is PatchOutcome.VERIFIED
    assert verification.target_resolved is True
    # Two runs of the one adapter: the baseline and the re-verification.
    assert check.runs == 2


def test_verification_does_not_touch_the_original_workspace(tmp_path):
    write_workspace(tmp_path)
    gate = Gate([MarkerCheck()])
    verifier = Verifier(gate, GateConfig())

    patch = build_patch(tmp_path, acl_finding(), SECURE_TF, attempt=1)
    verifier.verify(tmp_path, acl_finding(), patch)

    assert (tmp_path / "main.tf").read_text(encoding="utf-8") == INSECURE_TF


def test_a_pre_existing_violation_does_not_block_a_working_fix(tmp_path):
    """Without a baseline, no fix ever verifies in a workspace with two problems."""
    extra = '\nresource "aws_security_group" "web" {\n  cidr_blocks = ["0.0.0.0/0"]\n}\n'
    content = INSECURE_TF + extra
    write_workspace(tmp_path, content)

    gate = Gate([MarkerCheck()])
    config = GateConfig()
    baseline = gate.run(tmp_path, config)
    verifier = Verifier(gate, config)
    verifier.set_baseline(baseline.findings)

    fixed = content.replace('acl    = "public-read"', 'acl    = "private"')
    patch = build_patch(tmp_path, acl_finding(), fixed, attempt=1)

    assert verifier.verify(tmp_path, acl_finding(), patch).outcome is PatchOutcome.VERIFIED


def test_a_patch_no_check_could_judge_is_invalid_not_verified(tmp_path):
    """Nothing verified it, so nothing may call it verified."""
    write_workspace(tmp_path)
    gate = Gate([MarkerCheck(available=False)])
    verifier = Verifier(gate, GateConfig())

    patch = build_patch(tmp_path, acl_finding(), SECURE_TF, attempt=1)
    verification = verifier.verify(tmp_path, acl_finding(), patch)

    assert verification.outcome is PatchOutcome.INVALID
    assert "unverified" in (verification.failure_reason or "")


def test_render_diff_is_empty_for_identical_content():
    assert render_diff("a\n", "a\n", Path("main.tf")) == ""
