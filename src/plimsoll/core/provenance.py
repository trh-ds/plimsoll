"""The provenance record: what ran, what it decided, and what it decided it about.

A verification tool that cannot be audited afterwards is a tool you have to take on
trust, and taking a machine's word for a compliance decision is the problem this project
was written about. The record is the artifact that makes a run checkable by someone who
was not there: which binaries at which versions, which model, which commit, which
workspace bytes, and one entry per finding with the gate's own judgement attached.

Three properties matter, and each is enforced here rather than intended:

**The hash covers the record.** `content_hash` is sha256 over canonical JSON of every
field except itself and `signature`. Serialise, hash, reload, hash again, and the value
is the same -- otherwise the hash proves nothing and the record is decoration.

**Secrets do not enter it.** `Finding` is redacted at construction, so findings arrive
clean. `Patch.new_content` is the whole file *with secrets restored*, and it is the one
place invariant 6 is genuinely reachable. It is not redacted on the way in; it has no
field to live in. `ProvenancePatch` carries the diff and nothing else, and the diff goes
through `redact()`.

**Nothing here decides anything.** Every status in the record was set by `core.loop`
from a `PatchVerification` the gate produced. This module reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from plimsoll.core.models import (
    Decision,
    DecisionStatus,
    ExceptionRef,
    Finding,
    GateResult,
    Patch,
    PatchVerification,
    Severity,
    SkippedCheck,
    SuppressionKind,
    ToolVersion,
    VerifyResult,
)
from plimsoll.core.redaction import redact

SCHEMA_VERSION: Literal["0.1"] = "0.1"
UNKNOWN_AUTHOR = "unknown"

_GIT_TIMEOUT_SECONDS = 15

# Directories whose contents say nothing about the configuration that was judged.
# `.terraform` is downloaded providers; `.git` is history; the caches are noise. Note
# that `.plimsoll` is deliberately *not* skipped -- the exception store is an input to
# every decision in the record, so a change to it must change the hash.
_SKIP_DIRS = frozenset({".git", ".terraform", "__pycache__", ".pytest_cache", ".mypy_cache"})

# Terraform state holds every attribute of every deployed resource in plaintext,
# including generated passwords. It is not hashed, because it is not configuration and
# because reading it at all is a habit worth not having.
_SKIP_SUFFIXES = (".tfstate", ".tfstate.backup")

# `Generated-By: claude-code` in a commit message. Best-effort: detecting AI authorship
# reliably is unsolved, and recording the claim when it is offered is still more than
# any current tool does.
_TRAILER = re.compile(r"^\s*Generated-By\s*:\s*(?P<who>\S.*?)\s*$", re.IGNORECASE | re.MULTILINE)

# `refs/pull/512/merge`, which is what GitHub sets GITHUB_REF to on a pull request.
_PR_REF = re.compile(r"^refs/pull/(?P<number>\d+)/")


class RepoContext(BaseModel):
    """Where the judged configuration came from. Every field is best-effort."""

    model_config = ConfigDict(frozen=True)

    remote: str | None = None
    commit: str | None = None
    branch: str | None = None
    pr_number: int | None = None


class ModelIdentity(BaseModel):
    """Which model was consulted. Null in deterministic mode.

    There is no field for the API key and there never will be one. `LLMConfig` reads it
    and nothing else ever sees it.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    temperature: float | None = None
    max_retries: int | None = None


class RunSummary(BaseModel):
    """Counts, so a reader gets the shape of the run before reading the decisions.

    SPEC: `specifications.md` shows total, fixed, escalated, exempted, and blocked.
    `reported`, `expired_exceptions`, and `timed_out` are added. The first is where a
    below-threshold finding is counted -- without it the numbers do not add up to
    `total`, and a summary that does not reconcile invites the reader to assume the
    difference was nothing. The other two are conditions under which the rest of the
    summary is not what it appears.
    """

    model_config = ConfigDict(frozen=True)

    total: int = 0
    fixed: int = 0
    escalated: int = 0
    exempted: int = 0
    blocked: int = 0
    reported: int = 0
    expired_exceptions: int = 0
    timed_out: bool = False


class Signature(BaseModel):
    """Reserved for v0.2 (Sigstore keyless). Always null in v0.1.

    Present in the schema now so adding it later is not a breaking change for anything
    that already parses these records.
    """

    model_config = ConfigDict(frozen=True)

    format: str
    value: str
    certificate: str | None = None


class ProvenancePatch(BaseModel):
    """A verified patch, as recorded. Deliberately narrower than `Patch`.

    `Patch.new_content` is the corrected file with every redacted secret *restored* --
    it has to be, or the patch would write placeholders back into the user's repository.
    Serialising it would put live credentials in a file people commit and attach to
    tickets. It is not redacted here; there is no field for it to be redacted into.
    """

    model_config = ConfigDict(frozen=True)

    target_finding_id: str
    attempt: int
    files_touched: list[Path] = Field(default_factory=list)
    diff: str = ""
    rationale: str = ""

    @classmethod
    def of(cls, patch: Patch) -> ProvenancePatch:
        return cls(
            target_finding_id=patch.target_finding_id,
            attempt=patch.attempt,
            files_touched=list(patch.files_touched),
            diff=redact(patch.diff),
            rationale=redact(patch.rationale),
        )


class ProvenanceDecision(BaseModel):
    """One decision, as recorded."""

    model_config = ConfigDict(frozen=True)

    finding: Finding
    status: DecisionStatus
    explanation: str | None = None
    patch: ProvenancePatch | None = None
    verification: PatchVerification | None = None
    attempts: int = 0
    exception_ref: ExceptionRef | None = None

    @classmethod
    def of(cls, decision: Decision) -> ProvenanceDecision:
        verification = decision.verification
        if verification is not None and verification.failure_reason:
            # Composed from rule classes and resource addresses, but the `INVALID` paths
            # interpolate an exception string, and exception strings quote whatever they
            # were handed.
            verification = verification.model_copy(
                update={"failure_reason": redact(verification.failure_reason)}
            )
        return cls(
            finding=decision.finding,
            status=decision.status,
            explanation=redact(decision.explanation) if decision.explanation else None,
            patch=ProvenancePatch.of(decision.patch) if decision.patch else None,
            verification=verification,
            attempts=decision.attempts,
            exception_ref=decision.exception_ref,
        )


class ProvenanceRecord(BaseModel):
    """One run, in full. Per `specifications.md`."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str
    timestamp: datetime
    workspace_hash: str
    repository: RepoContext = Field(default_factory=RepoContext)
    generated_by: str = UNKNOWN_AUTHOR
    tooling: list[ToolVersion] = Field(default_factory=list)
    checks_skipped: list[SkippedCheck] = Field(default_factory=list)
    model: ModelIdentity | None = None
    decisions: list[ProvenanceDecision] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    content_hash: str = ""
    signature: Signature | None = None

    def hashable(self) -> dict[str, Any]:
        """Every field the hash covers -- that is, all of them but two.

        `content_hash` cannot be an input to itself, and `signature` is applied to a
        finished record, so including it would make every signed record's hash disagree
        with its unsigned one.
        """
        return self.model_dump(mode="json", exclude={"content_hash", "signature"})

    def sealed(self) -> ProvenanceRecord:
        """This record with `content_hash` filled in."""
        return self.model_copy(update={"content_hash": content_hash(self.hashable())})

    def hash_matches(self) -> bool:
        """Whether `content_hash` still describes the rest of the record."""
        return bool(self.content_hash) and self.content_hash == content_hash(self.hashable())


def canonical_json(payload: object) -> str:
    """Sorted keys, UTF-8, no insignificant whitespace.

    The point is that two processes serialising the same record produce the same bytes,
    so the hash is reproducible by someone who did not write it.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _hashable_files(workspace: Path) -> list[Path]:
    out: list[Path] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if _SKIP_DIRS.intersection(relative.parts):
            continue
        if path.name.endswith(_SKIP_SUFFIXES):
            continue
        out.append(path)
    return sorted(out)


def workspace_hash(workspace: Path) -> str:
    """sha256 over sorted per-file digests.

    Per-file rather than one stream over concatenated bytes: concatenation cannot tell
    `a.tf` + `b.tf` from a single file holding both, and a rename that moved a resource
    between files would not change the hash.
    """
    digest = hashlib.sha256()
    for path in _hashable_files(workspace):
        try:
            content = path.read_bytes()
        except OSError:
            # Unreadable is a fact about the workspace, and one the hash should reflect
            # rather than silently skip.
            content = b"<unreadable>"
        relative = path.relative_to(workspace).as_posix()
        digest.update(f"{relative}:{hashlib.sha256(content).hexdigest()}\n".encode())
    return "sha256:" + digest.hexdigest()


def _git(workspace: Path, *args: str) -> str | None:
    """One read-only git command, or None. Never raises; git is optional."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def pr_number_from_env(env: dict[str, str] | None = None) -> int | None:
    """The pull request number from `GITHUB_REF`, e.g. `refs/pull/512/merge`."""
    ref = (env if env is not None else dict(os.environ)).get("GITHUB_REF") or ""
    match = _PR_REF.match(ref)
    return int(match.group("number")) if match else None


def detect_repository(workspace: Path, env: dict[str, str] | None = None) -> RepoContext:
    """Best-effort repository context. A directory that is not a repository is fine."""
    branch = _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    return RepoContext(
        remote=_git(workspace, "remote", "get-url", "origin"),
        commit=_git(workspace, "rev-parse", "HEAD"),
        # Detached HEAD prints the literal string "HEAD", which is not a branch name.
        branch=None if branch == "HEAD" else branch,
        pr_number=pr_number_from_env(env),
    )


def detect_generated_by(
    workspace: Path,
    override: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """`--generated-by` > `PLIMSOLL_GENERATED_BY` > a commit trailer > "unknown".

    Deliberately ordered so an explicit claim always wins. The trailer is the weakest
    signal and the last consulted; `unknown` is an honest answer and is the default.
    """
    if override:
        return override
    environment = env if env is not None else dict(os.environ)
    from_env = environment.get("PLIMSOLL_GENERATED_BY")
    if from_env:
        return from_env
    message = _git(workspace, "log", "-1", "--format=%B")
    if message:
        match = _TRAILER.search(message)
        if match:
            return match.group("who")
    return UNKNOWN_AUTHOR


def summarise(decisions: list[Decision], *, timed_out: bool = False) -> RunSummary:
    counts = {status: 0 for status in DecisionStatus}
    for decision in decisions:
        counts[decision.status] += 1
    return RunSummary(
        total=len(decisions),
        fixed=counts[DecisionStatus.FIXED],
        escalated=counts[DecisionStatus.ESCALATED],
        exempted=counts[DecisionStatus.EXEMPTED],
        blocked=counts[DecisionStatus.BLOCKED],
        reported=counts[DecisionStatus.REPORTED],
        expired_exceptions=sum(1 for d in decisions if d.exception_expired),
        timed_out=timed_out,
    )


def build_record(
    result: VerifyResult,
    workspace: Path,
    *,
    model: ModelIdentity | None = None,
    generated_by: str | None = None,
    env: dict[str, str] | None = None,
) -> ProvenanceRecord:
    """Assemble a sealed record for one run."""
    record = ProvenanceRecord(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC),
        workspace_hash=workspace_hash(workspace),
        repository=detect_repository(workspace, env),
        generated_by=detect_generated_by(workspace, generated_by, env),
        tooling=list(result.gate.tooling),
        checks_skipped=list(result.gate.checks_skipped),
        model=model,
        decisions=[ProvenanceDecision.of(d) for d in result.decisions],
        summary=summarise(result.decisions, timed_out=result.timed_out),
    )
    return record.sealed()


def gate_as_verify_result(result: GateResult, fail_on: Severity) -> VerifyResult:
    """Wrap a `plimsoll scan` result so it can be recorded.

    `scan` produces findings and no decisions, but a record whose `decisions` list is
    empty would say a clean run and a scan of a broken workspace were the same thing.
    The untried statuses are computed with `core.loop.untried_status`, the same function
    the loop itself uses, so the two cannot drift.
    """
    from plimsoll.core.loop import untried_status

    # Exempted findings stay in the record, as `verify` keeps them: who accepted a
    # violation is exactly what an auditor reads this file for. Baselined ones do not --
    # a snapshot nobody reviewed is not a decision, and recording it as one would be.
    exempted = [
        Decision(finding=s.finding, status=DecisionStatus.EXEMPTED, exception_ref=s.exception_ref)
        for s in result.suppressed
        if s.kind is SuppressionKind.EXCEPTION
    ]
    return VerifyResult(
        gate=result,
        decisions=[Decision(finding=f, status=untried_status(f, fail_on)) for f in result.findings]
        + exempted,
        llm_unavailable_reason="scan does not use a model",
        timed_out=result.timed_out,
    )


def write_record(record: ProvenanceRecord, path: Path) -> Path:
    """Write the record as indented JSON.

    Indented, not canonical: this file is read by people and diffed in pull requests.
    The hash was computed over the canonical form and does not depend on this layout.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
