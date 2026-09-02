"""The verify -> reason -> re-verify -> escalate loop.

This is the module that makes Plimsoll an agent rather than a linter, and it is written
to be boring. The interesting decisions all live elsewhere: the gate decides what is a
violation, the verifier decides whether a patch worked. This file only sequences them
and bounds the retries.

Three things it must never do, each of which is an invariant:

- set `Decision.status` from anything a model said (1)
- verify a patch with anything other than the gate that produced the finding (2)
- surface a patch that did not verify (3)

The third is the one that takes discipline: an unverified patch is *deleted*, not
downgraded to a suggestion, not shown with a warning label. `Decision.patch` is
populated only on `FIXED`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from plimsoll.core.context import (
    ContextError,
    build_explain_prompt,
    build_patch_prompt,
)
from plimsoll.core.deadline import Deadline
from plimsoll.core.exceptions import ExceptionStore
from plimsoll.core.gate import Gate
from plimsoll.core.models import (
    Decision,
    DecisionStatus,
    ExceptionRef,
    Finding,
    GateConfig,
    PatchOutcome,
    Severity,
    VerifyResult,
)
from plimsoll.core.redaction import Redactor
from plimsoll.core.verifier import Verifier, build_patch
from plimsoll.llm import LLMError, LLMProvider, LLMUnavailable

log = logging.getLogger("plimsoll.loop")


def untried_status(finding: Finding, fail_on: Severity) -> DecisionStatus:
    """The status for a finding no model was asked about.

    Below the threshold, past the cap, or running deterministic-only. Never `PASS`: it is
    a real violation, and saying otherwise would be the exact dishonesty this project
    exists to prevent.

    A module function rather than a method because `plimsoll scan` needs the same rule to
    write a provenance record, and two copies of it would eventually disagree about what
    a finding below the threshold means.
    """
    if finding.severity.meets(fail_on):
        return DecisionStatus.BLOCKED
    return DecisionStatus.REPORTED


def _attach(decision: Decision, ref: ExceptionRef | None) -> Decision:
    """Record the exception a finding matched, on a decision that is not `EXEMPTED`.

    Only expired references reach here -- a live one short-circuits before any of this.
    They are carried anyway so the report can say the sign-off lapsed rather than
    presenting the finding as though nobody had ever looked at it.
    """
    return decision if ref is None else decision.model_copy(update={"exception_ref": ref})


class VerifierLoop:
    """Runs the gate, then tries to fix what it found."""

    def __init__(
        self,
        gate: Gate,
        config: GateConfig,
        provider: LLMProvider | None = None,
        *,
        max_retries: int = 2,
        max_findings: int = 25,
        exceptions: ExceptionStore | None = None,
    ) -> None:
        self._gate = gate
        self._config = config
        self._provider = provider
        self._max_retries = max_retries
        self._max_findings = max_findings
        self._exceptions = exceptions or ExceptionStore()

    def run(
        self,
        workspace: Path,
        *,
        explain_only: bool = False,
        deadline: Deadline | None = None,
    ) -> VerifyResult:
        budget = deadline or Deadline.none()
        gate_result = self._gate.run(workspace, self._config, budget)
        result = VerifyResult(gate=gate_result, timed_out=gate_result.timed_out)

        # Matched before anything else happens. An accepted violation must not cost a
        # token and must not leave the machine, and the only way to guarantee that is to
        # decide it before the finding is ever a candidate.
        refs = {f.id: self._exceptions.match(f) for f in gate_result.findings}
        exempt = {i for i, ref in refs.items() if ref is not None and not ref.expired}

        if self._provider is None:
            result.llm_unavailable_reason = "no model configured (--no-llm or unset provider)"
            result.decisions = [self._untried(f, refs[f.id]) for f in gate_result.findings]
            return result

        result.llm_description = self._provider.describe()

        # Highest severity first, capped. When the cap bites, the findings that get
        # model attention are the ones that would block the merge -- not the first
        # twenty-five in file order.
        candidates = [
            f
            for f in gate_result.findings
            if f.id not in exempt and f.severity.meets(self._config.fail_on)
        ]
        targeted = {f.id for f in candidates[: self._max_findings]}

        verifier = Verifier(self._gate, self._config, budget)
        verifier.set_baseline(gate_result.findings)

        decisions: list[Decision] = []
        for finding in gate_result.findings:
            ref = refs[finding.id]
            # Checked between findings. Everything past the budget still gets a decision
            # -- untried, and therefore still blocking -- because dropping a finding
            # because the clock ran out is the one thing a verification tool may not do.
            if finding.id not in targeted or budget.expired:
                result.timed_out = result.timed_out or budget.expired
                decisions.append(self._untried(finding, ref))
                continue
            result.llm_used = True
            decisions.append(
                _attach(self._handle(workspace, finding, verifier, explain_only, budget), ref)
            )

        result.decisions = decisions
        return result

    def _untried_status(self, finding: Finding) -> DecisionStatus:
        return untried_status(finding, self._config.fail_on)

    def _untried(self, finding: Finding, ref: ExceptionRef | None = None) -> Decision:
        if ref is not None and not ref.expired:
            return Decision(finding=finding, status=DecisionStatus.EXEMPTED, exception_ref=ref)
        return _attach(Decision(finding=finding, status=self._untried_status(finding)), ref)

    def _handle(
        self,
        workspace: Path,
        finding: Finding,
        verifier: Verifier,
        explain_only: bool,
        budget: Deadline,
    ) -> Decision:
        redactor = Redactor()
        explanation = self._explain(workspace, finding, redactor)

        if explain_only:
            return Decision(
                finding=finding,
                status=self._untried_status(finding),
                explanation=explanation,
            )

        return self._attempt_fixes(workspace, finding, verifier, redactor, explanation, budget)

    def _explain(self, workspace: Path, finding: Finding, redactor: Redactor) -> str | None:
        assert self._provider is not None
        try:
            prompt = build_explain_prompt(finding, workspace, redactor, self._exceptions)
            return redactor.restore(self._provider.complete(prompt)).strip()
        except (LLMError, ContextError) as exc:
            # An explanation is a convenience. Losing it must not lose the finding.
            log.debug("explanation failed for %s: %s", finding.id, exc)
            return None

    def _attempt_fixes(
        self,
        workspace: Path,
        finding: Finding,
        verifier: Verifier,
        redactor: Redactor,
        explanation: str | None,
        budget: Deadline,
    ) -> Decision:
        assert self._provider is not None
        failure_reason: str | None = None
        attempts = 0
        last_verification = None

        # One initial attempt plus `max_retries` retries.
        for attempt in range(1, self._max_retries + 2):
            # Between attempts, not mid-attempt: a half-killed gate run says nothing, and
            # an attempt started on the last second of the budget cannot be judged.
            if budget.expired:
                break
            attempts = attempt
            try:
                prompt = build_patch_prompt(
                    finding, workspace, redactor, failure_reason, self._exceptions
                )
                raw = self._provider.complete(prompt)
                patch = build_patch(
                    workspace,
                    finding,
                    redactor.restore(raw),
                    attempt,
                    rationale=explanation or "",
                )
            except LLMUnavailable as exc:
                # The provider is gone. Retrying will not bring it back, and the run
                # must still finish and report deterministically (invariant 4).
                log.warning("provider unavailable while fixing %s: %s", finding.id, exc)
                break
            except (LLMError, ContextError) as exc:
                # Malformed or unusable output. Costs a retry, not a gate run.
                failure_reason = f"the previous answer was unusable: {exc}"
                log.debug("attempt %d for %s rejected: %s", attempt, finding.id, exc)
                continue

            verification = verifier.verify(workspace, finding, patch)
            last_verification = verification

            if verification.outcome is PatchOutcome.VERIFIED:
                return Decision(
                    finding=finding,
                    status=DecisionStatus.FIXED,
                    explanation=explanation,
                    patch=patch,
                    verification=verification,
                    attempts=attempt,
                )

            failure_reason = verification.failure_reason
            log.debug("attempt %d for %s: %s", attempt, finding.id, verification.outcome.value)

        # Retries exhausted. The patch objects are dropped here, deliberately and
        # permanently: escalation carries the *original* finding and nothing that could
        # be mistaken for a fix.
        return Decision(
            finding=finding,
            status=DecisionStatus.ESCALATED,
            explanation=explanation,
            patch=None,
            verification=last_verification,
            attempts=attempts,
        )
