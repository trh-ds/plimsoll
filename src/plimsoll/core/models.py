"""Core data models.

The contracts here are specified in `specifications.md`; read that before changing a
shape. Deviations from it that were made deliberately are marked `SPEC:` below.
"""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from plimsoll.core.redaction import redact


class Severity(StrEnum):
    """Finding severity. Mapped from source tools by `checks.severity_map`, never inferred."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    @property
    def rank(self) -> int:
        """Ordinal for threshold comparison. Higher is more severe.

        Severity ordering is not exposed through `<`/`>`: `Severity` inherits `str`,
        whose comparison operators would silently compare alphabetically.
        """
        return _SEVERITY_RANK[self]

    def meets(self, threshold: Severity) -> bool:
        """True when this severity is at or above `threshold` (the `--fail-on` test)."""
        return self.rank >= threshold.rank


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class CheckSource(StrEnum):
    """The adapter a finding came from.

    SPEC: `specifications.md` names these four for v0.1. A new adapter adds a member
    here as well as a module -- `architecture.md` describes adding one as "one file plus
    one fixture", which is one line short.
    """

    TERRAFORM_VALIDATE = "terraform_validate"
    TERRAFORM_PLAN = "terraform_plan"
    CHECKOV = "checkov"
    OPA = "opa"


class ToolAvailability(BaseModel):
    """Result of probing for an adapter's external binary.

    A missing binary is not an error: the gate records the skip and continues.
    """

    model_config = ConfigDict(frozen=True)

    available: bool
    tool: str
    version: str | None = None
    reason: str | None = None

    @classmethod
    def ok(cls, tool: str, version: str | None = None) -> ToolAvailability:
        return cls(available=True, tool=tool, version=version)

    @classmethod
    def missing(cls, tool: str, reason: str) -> ToolAvailability:
        return cls(available=False, tool=tool, reason=reason)


def compute_finding_id(
    *,
    source: CheckSource,
    rule_id: str,
    resource_address: str,
    file: Path,
    line_start: int,
) -> str:
    """Stable identity for a finding.

    SPEC: `specifications.md` keys this on (source, rule_id, resource_address, file).
    `line_start` is included as well, because tools legitimately emit the same rule
    against the same file more than once -- `terraform validate` reports one diagnostic
    per bad argument -- and without the line those collapse to one id, which would
    silently drop findings during dedup and collide when decisions are keyed by id.
    """
    payload = "|".join([source.value, rule_id, resource_address, file.as_posix(), str(line_start)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_fingerprint(
    *,
    rule_class: str,
    resource_address: str,
    file: Path,
    rule_id: str,
    line_start: int,
) -> str:
    """Identity of a violation that survives unrelated edits to the file it is in.

    `Finding.id` includes the line, which it needs to (see `compute_finding_id`), and so
    changes every time someone adds a line above the resource. That is the wrong key for
    anything that must outlive a commit: a baseline of accepted findings, or a SARIF
    fingerprint code scanning uses to decide an alert is the same alert. Keyed instead on
    what the violation *is* -- which rule, on which resource, in which file -- and on the
    tool's own rule id, so checkov and the Rego pack reporting one class stay distinct.

    A finding with no resource address has nothing else to anchor it, so its line stays
    in. Such a finding drifting back to "new" fails closed, which is the right direction.
    """
    parts = [rule_class, resource_address, file.as_posix()]
    if not resource_address:
        parts += [rule_id, str(line_start)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _redact_deep(value: object) -> object:
    """Redact every string inside a nested tool payload."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_deep(v) for v in value]
    return value


class Finding(BaseModel):
    """One normalised violation, as produced by a `Check` adapter.

    SPEC: `specifications.md` describes `raw` as "unmodified tool output" and says
    redaction runs before serialisation. It runs *at construction* instead. `raw` holds
    whatever the tool chose to include, which for a hardcoded-credential finding is the
    credential; that value then flows into the reporters, into provenance, and into
    prompts. Redacting at each of those boundaries means a new boundary added later
    leaks by default. Redacting once, here, means it cannot: there is no moment at which
    an unredacted `Finding` exists.
    """

    id: str = ""
    source: CheckSource
    rule_id: str
    rule_class: str
    severity: Severity
    resource_address: str
    resource_type: str
    file: Path
    line_start: int
    line_end: int
    message: str
    benchmark_refs: list[str] = Field(default_factory=list)
    # Other tools that reported this same violation and were deduplicated into this
    # finding. Provenance has to be able to say "Checkov and OPA both flagged this";
    # dropping the duplicate silently would lose that.
    also_reported_by: list[CheckSource] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_id(self) -> Finding:
        if not self.id:
            self.id = compute_finding_id(
                source=self.source,
                rule_id=self.rule_id,
                resource_address=self.resource_address,
                file=self.file,
                line_start=self.line_start,
            )
        return self

    @property
    def fingerprint(self) -> str:
        return compute_fingerprint(
            rule_class=self.rule_class,
            resource_address=self.resource_address,
            file=self.file,
            rule_id=self.rule_id,
            line_start=self.line_start,
        )

    @model_validator(mode="after")
    def _redact(self) -> Finding:
        # Invariant 6, applied where it cannot be forgotten. Identity is computed from
        # rule and location, never from `message` or `raw`, so redaction cannot change
        # `id` or the dedup key.
        self.message = redact(self.message)
        self.raw = {key: _redact_deep(value) for key, value in self.raw.items()}
        return self


class Patch(BaseModel):
    """A candidate fix. A *proposal*, never a verdict.

    SPEC: `specifications.md` originally carried only `diff`, with the model asked to
    emit a unified diff directly. It now carries `new_content` -- the complete corrected
    file -- and `diff` is computed from it by `difflib`.

    The reason is empirical rather than aesthetic: models produce unified diffs with
    wrong hunk headers and drifting context lines often enough that a diff applier
    becomes the component that fails, and every malformed diff burns a retry without
    ever testing whether the model understood the rule. Whole-file output moves the
    formatting burden onto code that cannot get it wrong. The diff is still what a human
    reads and what provenance records.
    """

    target_finding_id: str
    attempt: int = Field(ge=1)
    files_touched: list[Path] = Field(default_factory=list)
    new_content: str = ""
    diff: str = ""
    rationale: str = ""


class PatchOutcome(StrEnum):
    """How a patch fared against the re-run gate."""

    VERIFIED = "verified"
    REGRESSED = "regressed"
    INEFFECTIVE = "ineffective"
    INVALID = "invalid"


class PatchVerification(BaseModel):
    """The gate's judgement on a patch. Produced only by `core.verifier`."""

    outcome: PatchOutcome
    target_resolved: bool = False
    new_findings: list[Finding] = Field(default_factory=list)
    removed_findings: list[Finding] = Field(default_factory=list)
    failure_reason: str | None = None


class DecisionStatus(StrEnum):
    """The outcome for one finding.

    SPEC: `REPORTED` is not in `specifications.md`. It was added because the original
    five statuses had no honest home for "a real violation, below `--fail-on`, never
    sent to the model". `PASS` means no violation, which would have been a lie, and
    `BLOCKED` means the run fails, which it does not. A tool whose entire thesis is that
    silence must not read as success cannot mislabel its own output.
    """

    PASS = "PASS"
    FIXED = "FIXED"
    ESCALATED = "ESCALATED"
    EXEMPTED = "EXEMPTED"
    BLOCKED = "BLOCKED"
    REPORTED = "REPORTED"


class ExceptionRef(BaseModel):
    """The recorded exception a finding matched, attached to its `Decision`.

    Carries the justification and approver rather than just an identifier, so a
    provenance record answers "who accepted this, and why" without needing the
    repository it came from.
    """

    model_config = ConfigDict(frozen=True)

    rule_class: str
    resource_address: str
    justification: str
    approved_by: str
    approved_in: str | None = None
    created: date | None = None
    expires: date | None = None
    # An expired entry does not exempt anything. It is attached anyway, so the report can
    # say "this lapsed on 2026-09-15" instead of silently re-raising a finding a reviewer
    # believed was handled.
    expired: bool = False


class Decision(BaseModel):
    """What happened to one finding.

    Constructed only by `core.loop`. Never by a provider, never by a check.

    `FIXED` is reachable only through `PatchVerification.outcome == VERIFIED`, and the
    validator below enforces it. Invariants 1 and 3 are one assertion, not a convention:
    without it, any future edit that sets `status` from a model's self-assessment would
    pass review by looking reasonable.
    """

    finding: Finding
    status: DecisionStatus
    explanation: str | None = None
    patch: Patch | None = None
    verification: PatchVerification | None = None
    attempts: int = 0
    exception_ref: ExceptionRef | None = None

    @property
    def exception_expired(self) -> bool:
        """True when this finding matched an exception that has lapsed.

        Reported as `EXPIRED_EXCEPTION` rather than folded into the ordinary finding
        line: a reviewer who signed off on this deserves to be told the sign-off ran out,
        not to rediscover the finding as if it were new.
        """
        return self.exception_ref is not None and self.exception_ref.expired

    @model_validator(mode="after")
    def _fixed_requires_verification(self) -> Decision:
        if self.status is DecisionStatus.EXEMPTED:
            # The mirror of the FIXED rule. `EXEMPTED` means a human accepted this in
            # writing; without the reference there is no record of who, and the status
            # would be indistinguishable from "we decided to ignore it".
            if self.exception_ref is None:
                raise ValueError("Decision.status=EXEMPTED requires the exception it matched")
            if self.exception_ref.expired:
                raise ValueError("an expired exception does not exempt a finding")
            return self
        if self.status is not DecisionStatus.FIXED:
            return self
        if self.verification is None or self.verification.outcome is not PatchOutcome.VERIFIED:
            raise ValueError(
                "Decision.status=FIXED requires a PatchVerification with outcome=verified. "
                "Only the re-run gate can produce one."
            )
        if self.patch is None:
            raise ValueError("Decision.status=FIXED requires the patch that was verified")
        return self


class ToolVersion(BaseModel):
    """An external tool that ran, and at what version. Feeds `ProvenanceRecord.tooling`."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str | None = None


class SkippedCheck(BaseModel):
    """A check that did not run because its binary was unavailable."""

    model_config = ConfigDict(frozen=True)

    name: str
    reason: str


class FailedCheck(BaseModel):
    """A check that raised. The run continues; the report says which check failed."""

    model_config = ConfigDict(frozen=True)

    name: str
    error: str


class CheckConfig(BaseModel):
    """Per-adapter execution settings."""

    timeout_seconds: int = Field(default=120, gt=0)
    policy_dir: Path | None = None


class GateConfig(BaseModel):
    """Settings for one gate run."""

    fail_on: Severity = Severity.HIGH
    check: CheckConfig = Field(default_factory=CheckConfig)
    # Fingerprints of findings that already existed when the baseline was taken. Part of
    # the gate's config rather than a step after it, so a patch is re-verified against
    # the same baseline the finding was produced under (invariant 2).
    baseline: frozenset[str] = frozenset()


class SuppressionKind(StrEnum):
    EXCEPTION = "exception"
    BASELINE = "baseline"


class SuppressedFinding(BaseModel):
    """A finding that does not count toward the exit code, and why.

    Moved out of `GateResult.findings` rather than deleted. A suppressed finding is still
    a violation in the code, and every report says how many there are and why each is
    there: hiding them is how a baseline turns into a place debt goes to be forgotten.
    """

    finding: Finding
    kind: SuppressionKind
    exception_ref: ExceptionRef | None = None


class GateResult(BaseModel):
    """Everything one gate run produced, including what did not run.

    `checks_skipped` and `checks_failed` are load-bearing: exit code 3 exists so that a
    broken toolchain cannot masquerade as a clean pass.
    """

    findings: list[Finding] = Field(default_factory=list)
    tooling: list[ToolVersion] = Field(default_factory=list)
    checks_skipped: list[SkippedCheck] = Field(default_factory=list)
    checks_failed: list[FailedCheck] = Field(default_factory=list)
    suppressed: list[SuppressedFinding] = Field(default_factory=list)
    # The run hit `--timeout` and stopped at a seam. Whatever is below is partial, so the
    # CLI exits 4 and never 0 -- partial verification is not verification.
    timed_out: bool = False

    @property
    def any_check_ran(self) -> bool:
        """False when nothing was verified -- the exit-code-3 condition."""
        return bool(self.tooling)

    def findings_at_or_above(self, threshold: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity.meets(threshold)]

    def suppression_summary(self) -> str | None:
        """ "3 suppressed (2 by exception, 1 by baseline)", or None when nothing was."""
        if not self.suppressed:
            return None
        parts = [
            f"{count} by {kind.value}"
            for kind in SuppressionKind
            if (count := sum(1 for s in self.suppressed if s.kind is kind))
        ]
        return f"{len(self.suppressed)} suppressed ({', '.join(parts)})"


class VerifyResult(BaseModel):
    """One full `plimsoll verify` run: the gate result plus what was done about it.

    `gate` is the original scan and is never mutated by the loop. Exit codes are derived
    from `gate` and the decisions, never from anything a model said.
    """

    gate: GateResult
    decisions: list[Decision] = Field(default_factory=list)
    llm_used: bool = False
    llm_description: str | None = None
    # Why the model did not run, when it did not: `--no-llm`, unreachable provider,
    # missing configuration. Printed, because degrading silently is the failure mode
    # this project exists to prevent.
    llm_unavailable_reason: str | None = None
    # Set when the run hit `--timeout`, here as well as on `gate`: the loop can exhaust
    # the budget long after every check finished.
    timed_out: bool = False

    def with_status(self, status: DecisionStatus) -> list[Decision]:
        return [d for d in self.decisions if d.status is status]

    @property
    def fixed(self) -> list[Decision]:
        return self.with_status(DecisionStatus.FIXED)

    @property
    def exempted(self) -> list[Decision]:
        return self.with_status(DecisionStatus.EXEMPTED)

    @property
    def expired_exceptions(self) -> list[Decision]:
        return [d for d in self.decisions if d.exception_expired]

    @property
    def unresolved_at_or_above(self) -> list[Decision]:
        """Decisions that still block the run: found, at or above threshold, not fixed."""
        return [
            d
            for d in self.decisions
            if d.status in (DecisionStatus.ESCALATED, DecisionStatus.BLOCKED)
        ]
