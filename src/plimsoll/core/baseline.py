"""Baselines: the findings a repository already had when it adopted this tool.

Turning a policy scanner on in an existing repository produces a wall of findings, and
the usual outcome is that it gets turned off again the same afternoon. A baseline records
what was there on day one, so the gate fails only on what a change *introduces* while the
old debt is paid down at its own pace.

It is not the exception store, and the difference is deliberate. An exception is a
reviewed decision about one violation: a justification, an approver, an expiry. A
baseline is a snapshot nobody reviewed line by line. So a baseline never touches a
`verify` decision, never appears as approval in provenance, and every report still counts
what it suppressed.

Three properties are load-bearing:

**Keyed on `Finding.fingerprint`, not `Finding.id`.** The id includes the line number, and
a baseline that broke every time someone added a line above a resource would be thrown
away within a week.

**A requested baseline that cannot be read is fatal.** Silently falling back to "no
baseline" fails every build; silently falling back to an empty *suppression* is fine, but
only because nothing is hidden. The CLI exits 2 instead of guessing.

**The file is sorted and human-readable.** It is committed and diffed. A reviewer should be
able to see that a pull request grew the baseline, and by what.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from plimsoll.core.models import Finding, Severity

DEFAULT_RELATIVE_PATH = Path(".plimsoll") / "baseline.json"
SCHEMA_VERSION = "0.1"


class BaselineError(RuntimeError):
    """The baseline was asked for and could not be read as one."""


class BaselineEntry(BaseModel):
    """One accepted-on-adoption finding. Everything but the fingerprint is for people."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint: str = Field(min_length=1)
    rule_class: str
    resource_address: str = ""
    file: str = ""
    severity: Severity

    @classmethod
    def from_finding(cls, finding: Finding) -> BaselineEntry:
        return cls(
            fingerprint=finding.fingerprint,
            rule_class=finding.rule_class,
            resource_address=finding.resource_address,
            file=finding.file.as_posix(),
            severity=finding.severity,
        )


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = SCHEMA_VERSION
    created: date | None = None
    findings: list[BaselineEntry] = Field(default_factory=list)

    @property
    def fingerprints(self) -> frozenset[str]:
        return frozenset(entry.fingerprint for entry in self.findings)

    @classmethod
    def from_findings(cls, findings: list[Finding], created: date | None = None) -> Baseline:
        # One entry per fingerprint: two tools reporting one violation is one debt.
        unique = {f.fingerprint: BaselineEntry.from_finding(f) for f in findings}
        ordered = sorted(
            unique.values(), key=lambda e: (e.file, e.resource_address, e.rule_class, e.fingerprint)
        )
        return cls(created=created or date.today(), findings=ordered)


def load_baseline(path: Path) -> Baseline:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineError(
            f"baseline {path} does not exist; create it with `plimsoll baseline`"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"baseline {path} could not be read: {exc}") from exc

    if isinstance(payload, dict) and str(payload.get("version")) != SCHEMA_VERSION:
        raise BaselineError(
            f"baseline {path}: version {payload.get('version')!r} is not supported "
            f"(this build reads {SCHEMA_VERSION!r})"
        )
    try:
        return Baseline.model_validate(payload)
    except ValidationError as exc:
        raise BaselineError(f"baseline {path} is malformed: {exc.errors()[0]['msg']}") from exc


def write_baseline(baseline: Baseline, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(baseline.model_dump(mode="json"), indent=2, sort_keys=False)
    path.write_text(body + "\n", encoding="utf-8")
    return path
