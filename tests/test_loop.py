"""The loop's contract, which is mostly a contract about what it refuses to do."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import (
    INSECURE_TF,
    SECURE_TF,
    DeadProvider,
    MarkerCheck,
    ScriptedProvider,
    write_workspace,
)
from plimsoll.core.gate import Gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import (
    CheckSource,
    Decision,
    DecisionStatus,
    Finding,
    GateConfig,
    Patch,
    PatchOutcome,
    PatchVerification,
    Severity,
)
from plimsoll.llm import LLMError

# The model fixes the ACL but opens the world to the security group: a regression the
# gate catches and the reviewer never sees.
REGRESSED_TF = """resource "aws_s3_bucket" "reports" {
  bucket = "acme-reports"
  acl    = "private"
}

resource "aws_security_group" "web" {
  cidr_blocks = ["0.0.0.0/0"]
}

resource "aws_sns_topic" "alerts" {
  name = "alerts"
}
"""

# Changes something, resolves nothing.
COSMETIC_TF = INSECURE_TF.replace("acme-reports", "acme-reports-renamed")


def build(
    workspace,
    *responses: str | Exception,
    max_retries: int = 2,
    fail_on: Severity = Severity.HIGH,
    max_findings: int = 25,
    explanation: str | Exception = "The bucket ACL is public-read, which ...",
):
    """`responses` are the *patch* answers.

    Every targeted finding costs one explanation call before its first patch call, so
    the explanation is scripted here rather than in each test.
    """
    check = MarkerCheck()
    gate = Gate([check])
    provider = ScriptedProvider(explanation, *responses)
    loop = VerifierLoop(
        gate,
        GateConfig(fail_on=fail_on),
        provider,
        max_retries=max_retries,
        max_findings=max_findings,
    )
    return loop, provider, check


def test_a_verified_fix_is_reported_as_fixed(tmp_path):
    write_workspace(tmp_path)
    loop, _, _ = build(tmp_path, SECURE_TF)

    result = loop.run(tmp_path)
    fixed = result.fixed

    assert len(fixed) == 1
    decision = fixed[0]
    assert decision.finding.rule_class == "s3.acl.public"
    assert decision.verification is not None
    assert decision.verification.outcome is PatchOutcome.VERIFIED
    assert decision.patch is not None
    assert 'acl    = "private"' in decision.patch.new_content
    # The diff is computed by us, not parsed from the model.
    assert '-  acl    = "public-read"' in decision.patch.diff
    assert decision.attempts == 1


def test_the_workspace_is_never_modified(tmp_path):
    """`verify` reports. It does not edit your repository."""
    write_workspace(tmp_path)
    loop, _, _ = build(tmp_path, SECURE_TF)

    loop.run(tmp_path)

    assert (tmp_path / "main.tf").read_text(encoding="utf-8") == INSECURE_TF


def test_a_regressing_patch_is_discarded(tmp_path):
    write_workspace(tmp_path)
    # Every attempt regresses, so retries are exhausted.
    loop, _, _ = build(tmp_path, REGRESSED_TF, REGRESSED_TF, REGRESSED_TF)

    decisions = loop.run(tmp_path).decisions
    (decision,) = [d for d in decisions if d.finding.rule_class == "s3.acl.public"]

    assert decision.status is DecisionStatus.ESCALATED
    # Invariant 3: the patch does not survive into the report at all.
    assert decision.patch is None
    assert decision.verification is not None
    assert decision.verification.outcome is PatchOutcome.REGRESSED
    assert "sg.ingress.world_open" in (decision.verification.failure_reason or "")


def test_an_ineffective_patch_escalates_with_the_original_finding(tmp_path):
    write_workspace(tmp_path)
    loop, _, _ = build(tmp_path, COSMETIC_TF, COSMETIC_TF, COSMETIC_TF)

    (decision,) = loop.run(tmp_path).decisions

    assert decision.status is DecisionStatus.ESCALATED
    assert decision.patch is None
    assert decision.finding.rule_class == "s3.acl.public"
    assert decision.verification is not None
    assert decision.verification.outcome is PatchOutcome.INEFFECTIVE


def test_malformed_output_costs_a_retry_but_not_a_gate_run(tmp_path):
    write_workspace(tmp_path)
    # Empty, then unchanged, then a real fix on the last allowed attempt.
    loop, _, check = build(tmp_path, "", INSECURE_TF, SECURE_TF, max_retries=2)

    result = loop.run(tmp_path)

    assert len(result.fixed) == 1
    assert result.fixed[0].attempts == 3
    # One baseline run plus exactly one verification -- the two unusable answers were
    # rejected before they reached the gate.
    assert check.runs == 2


def test_the_failure_reason_is_fed_back_into_the_retry(tmp_path):
    write_workspace(tmp_path)
    loop, provider, _ = build(tmp_path, COSMETIC_TF, SECURE_TF)

    loop.run(tmp_path)

    # prompts: explain, patch #1, patch #2
    assert len(provider.prompts) == 3
    assert "s3.acl.public is still reported" in provider.prompts[2]
    assert "Do not repeat it" in provider.prompts[2]


def test_retries_are_bounded(tmp_path):
    write_workspace(tmp_path)
    loop, provider, _ = build(tmp_path, *[COSMETIC_TF] * 10, max_retries=1)

    (decision,) = loop.run(tmp_path).decisions

    assert decision.status is DecisionStatus.ESCALATED
    # One explain prompt plus two patch prompts: the initial attempt and one retry.
    assert len(provider.prompts) == 3
    assert decision.attempts == 2


def test_an_unreachable_provider_degrades_instead_of_failing(tmp_path):
    """Invariant 4: the run finishes and reports deterministically."""
    write_workspace(tmp_path)
    loop = VerifierLoop(Gate([MarkerCheck()]), GateConfig(), DeadProvider(), max_retries=2)

    result = loop.run(tmp_path)

    (decision,) = result.decisions
    assert decision.status is DecisionStatus.ESCALATED
    assert decision.patch is None
    assert decision.finding.rule_class == "s3.acl.public"


def test_no_llm_reports_every_finding_without_calling_a_model(tmp_path):
    write_workspace(tmp_path)
    loop = VerifierLoop(Gate([MarkerCheck()]), GateConfig(), None)

    result = loop.run(tmp_path)

    assert result.llm_used is False
    assert result.llm_unavailable_reason
    (decision,) = result.decisions
    assert decision.status is DecisionStatus.BLOCKED
    assert decision.patch is None


def test_findings_below_the_threshold_are_reported_not_passed(tmp_path):
    """A LOW finding is still a violation. `PASS` would be a lie."""
    write_workspace(tmp_path, INSECURE_TF + "\nversioning = false\n")
    loop, provider, _ = build(tmp_path, SECURE_TF + "\nversioning = false\n")

    result = loop.run(tmp_path)

    low = [d for d in result.decisions if d.finding.severity is Severity.LOW]
    assert len(low) == 1
    assert low[0].status is DecisionStatus.REPORTED
    # It was never sent to the model: one explain prompt and one patch prompt only.
    assert len(provider.prompts) == 2


def test_the_findings_cap_prefers_the_most_severe(tmp_path):
    write_workspace(tmp_path, REGRESSED_TF.replace('acl    = "private"', 'acl    = "public-read"'))
    loop, _, _ = build(tmp_path, SECURE_TF, max_findings=1)

    result = loop.run(tmp_path)

    attempted = [d for d in result.decisions if d.attempts]
    assert len(attempted) == 1
    # CRITICAL beat HIGH for the single slot.
    assert attempted[0].finding.severity is Severity.CRITICAL

    uncontested = [d for d in result.decisions if not d.attempts]
    assert all(d.status is DecisionStatus.BLOCKED for d in uncontested)


def test_explain_only_produces_no_patches(tmp_path):
    write_workspace(tmp_path)
    loop, provider, check = build(tmp_path)

    result = loop.run(tmp_path, explain_only=True)

    (decision,) = result.decisions
    assert decision.explanation
    assert decision.patch is None
    assert decision.status is DecisionStatus.BLOCKED
    assert len(provider.prompts) == 1
    # Only the baseline scan; nothing was verified because nothing was proposed.
    assert check.runs == 1


def test_a_failed_explanation_does_not_lose_the_finding(tmp_path):
    write_workspace(tmp_path)
    loop, _, _ = build(tmp_path, SECURE_TF, explanation=LLMError("rate limited"))

    result = loop.run(tmp_path)

    assert len(result.fixed) == 1
    assert result.fixed[0].explanation is None


def test_fixed_is_unreachable_without_a_verified_verification():
    """Invariants 1 and 3, asserted rather than intended."""
    f = Finding(
        source=CheckSource.CHECKOV,
        rule_id="X",
        rule_class="s3.acl.public",
        severity=Severity.CRITICAL,
        resource_address="aws_s3_bucket.reports",
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=1,
        line_end=1,
        message="m",
    )
    patch = Patch(target_finding_id=f.id, attempt=1, new_content="x")

    with pytest.raises(ValidationError, match="requires a PatchVerification"):
        Decision(finding=f, status=DecisionStatus.FIXED, patch=patch)

    with pytest.raises(ValidationError, match="requires a PatchVerification"):
        Decision(
            finding=f,
            status=DecisionStatus.FIXED,
            patch=patch,
            verification=PatchVerification(outcome=PatchOutcome.INEFFECTIVE),
        )

    # And the one legal construction.
    ok = Decision(
        finding=f,
        status=DecisionStatus.FIXED,
        patch=patch,
        verification=PatchVerification(outcome=PatchOutcome.VERIFIED, target_resolved=True),
    )
    assert ok.status is DecisionStatus.FIXED
