"""Core data models.

The contracts here are specified in `specifications.md`; read that before changing a
shape. Deviations from it that were made deliberately are marked `SPEC:` below.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class Finding(BaseModel):
    """One normalised violation, as produced by a `Check` adapter."""

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


class GateResult(BaseModel):
    """Everything one gate run produced, including what did not run.

    `checks_skipped` and `checks_failed` are load-bearing: exit code 3 exists so that a
    broken toolchain cannot masquerade as a clean pass.
    """

    findings: list[Finding] = Field(default_factory=list)
    tooling: list[ToolVersion] = Field(default_factory=list)
    checks_skipped: list[SkippedCheck] = Field(default_factory=list)
    checks_failed: list[FailedCheck] = Field(default_factory=list)

    @property
    def any_check_ran(self) -> bool:
        """False when nothing was verified -- the exit-code-3 condition."""
        return bool(self.tooling)

    def findings_at_or_above(self, threshold: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity.meets(threshold)]
