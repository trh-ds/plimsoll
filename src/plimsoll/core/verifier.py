"""Re-verification: the component the whole project is an argument for.

A patch is applied to a throwaway copy of the workspace and the **same gate object**,
with the **same config**, is run again over it. Not a lighter check, not a second model,
not a heuristic on the diff. Invariant 2 is one function call here, and it is deliberate
that there is no second code path it could drift away from.

The original workspace is never written to. `plimsoll verify` reports; it does not edit
your repository.
"""

from __future__ import annotations

import difflib
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from plimsoll.core.context import ContextError, resolve_file
from plimsoll.core.deadline import Deadline
from plimsoll.core.gate import Gate
from plimsoll.core.models import (
    Finding,
    GateConfig,
    GateResult,
    Patch,
    PatchOutcome,
    PatchVerification,
    Severity,
)

# Directories that must not be copied into the scratch workspace. `.terraform` holds
# downloaded providers -- hundreds of megabytes, copied once per patch attempt.
_SKIP_DIRS = shutil.ignore_patterns(".terraform", ".git", ".pytest_cache", "__pycache__")


def finding_identity(finding: Finding) -> tuple[str, str, str]:
    """Line-independent identity, for comparing findings across a patch.

    `Finding.id` includes `line_start`, which is right for deduplication and wrong here:
    a fix that inserts two lines shifts every finding below it, and comparing by id
    would report all of them as simultaneously removed and newly introduced. The
    comparison that matters is "is this the same rule against the same resource".
    """
    return (
        finding.source.value,
        finding.rule_class,
        finding.resource_address or finding.file.as_posix(),
    )


def _identities(findings: Iterable[Finding]) -> set[tuple[str, str, str]]:
    return {finding_identity(f) for f in findings}


def render_diff(original: str, patched: str, path: Path) -> str:
    """Unified diff, computed by us rather than parsed from a model."""
    name = path.as_posix()
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
        )
    )


def build_patch(
    workspace: Path,
    finding: Finding,
    new_content: str,
    attempt: int,
    rationale: str = "",
) -> Patch:
    """Assemble a `Patch` from raw model output, or raise `ContextError`.

    This is where obviously-useless output is rejected before it costs a gate run:
    empty content, or content identical to what is already on disk.
    """
    path = resolve_file(workspace, finding)
    original = path.read_text(encoding="utf-8")

    cleaned = _strip_fences(new_content)
    if not cleaned.strip():
        raise ContextError("model returned an empty file")
    if cleaned == original:
        raise ContextError("model returned the file unchanged")

    relative = path.relative_to(workspace.resolve())
    return Patch(
        target_finding_id=finding.id,
        attempt=attempt,
        files_touched=[relative],
        new_content=cleaned,
        diff=render_diff(original, cleaned, relative),
        rationale=rationale,
    )


def _strip_fences(text: str) -> str:
    """Remove a markdown code fence the model added despite being told not to.

    The prompt forbids fences. Models add them anyway, often enough that failing the
    patch over it would report "the model cannot fix this" when the model in fact could.
    Only a fence wrapping the *entire* answer is stripped; one in the middle is real
    file content as far as we can tell, and is left alone.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]) + "\n"


class Verifier:
    """Applies a patch to a scratch copy and re-runs the gate over it."""

    def __init__(self, gate: Gate, config: GateConfig, deadline: Deadline | None = None) -> None:
        # The same gate instance that produced the finding, and the same config. Passing
        # a fresh gate here would let the two runs diverge in adapter set or timeout,
        # and the comparison would stop meaning anything.
        self._gate = gate
        self._config = config
        # The run's budget, not a fresh one. A re-verification that could outrun the
        # whole-run timeout would make `--timeout` a promise about the first scan only.
        self._deadline = deadline or Deadline.none()
        self._baseline: set[tuple[str, str, str]] = set()

    def set_baseline(self, findings: Iterable[Finding]) -> None:
        """Record what the workspace already violated before any patch.

        Without this, a patch is blamed for every unrelated pre-existing violation in
        the file, and no fix ever verifies in a workspace with more than one problem.
        """
        self._baseline = _identities(findings)

    def verify(self, workspace: Path, finding: Finding, patch: Patch) -> PatchVerification:
        before_target = finding_identity(finding)

        try:
            after = self._gate_patched_workspace(workspace, patch)
        except (OSError, ContextError) as exc:
            return PatchVerification(
                outcome=PatchOutcome.INVALID,
                failure_reason=f"the patch could not be applied: {exc}",
            )

        if not after.any_check_ran:
            # Nothing verified the patch, so nothing may claim it is verified.
            return PatchVerification(
                outcome=PatchOutcome.INVALID,
                failure_reason=(
                    "no check could run against the patched workspace, so the patch is unverified"
                ),
            )

        if after.timed_out:
            # A partial re-run is the most dangerous result there is: the adapter that
            # would have caught the regression may simply be the one that never ran, and
            # a patch judged against half a gate would reach a human labelled "verified".
            return PatchVerification(
                outcome=PatchOutcome.INVALID,
                failure_reason=(
                    "the run budget expired part-way through re-verification, so only some "
                    "checks judged this patch"
                ),
            )

        return self._classify(finding, before_target, after)

    def _gate_patched_workspace(self, workspace: Path, patch: Patch) -> GateResult:
        with tempfile.TemporaryDirectory(prefix="plimsoll-verify-") as tmp:
            scratch = Path(tmp) / "workspace"
            shutil.copytree(workspace, scratch, ignore=_SKIP_DIRS, symlinks=False)

            for relative in patch.files_touched:
                target = (scratch / relative).resolve()
                if not target.is_relative_to(scratch.resolve()):
                    raise ContextError(f"patch targets {relative}, outside the workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(patch.new_content, encoding="utf-8")

            return self._gate.run(scratch, self._config, self._deadline)

    def _classify(
        self,
        finding: Finding,
        before_target: tuple[str, str, str],
        after: GateResult,
    ) -> PatchVerification:
        after_ids = _identities(after.findings)
        target_resolved = before_target not in after_ids

        # Anything at or above the run's threshold that the patched workspace has and
        # that is not the finding we were fixing.
        threshold: Severity = self._config.fail_on
        introduced = [
            f
            for f in after.findings
            if finding_identity(f) != before_target and f.severity.meets(threshold)
        ]
        # Only genuinely new ones count; a pre-existing violation elsewhere in the file
        # is not this patch's fault and must not block a fix that works.
        introduced = [f for f in introduced if finding_identity(f) not in self._baseline]

        if introduced:
            names = ", ".join(
                sorted({f"{f.rule_class} on {f.resource_address}" for f in introduced})
            )
            return PatchVerification(
                outcome=PatchOutcome.REGRESSED,
                target_resolved=target_resolved,
                new_findings=introduced,
                failure_reason=(
                    f"the change introduced {len(introduced)} new finding(s) at or above "
                    f"{threshold.value}: {names}"
                ),
            )

        if not target_resolved:
            return PatchVerification(
                outcome=PatchOutcome.INEFFECTIVE,
                target_resolved=False,
                failure_reason=(
                    f"{finding.rule_class} is still reported against "
                    f"{finding.resource_address or finding.file.as_posix()} after the change"
                ),
            )

        return PatchVerification(
            outcome=PatchOutcome.VERIFIED,
            target_resolved=True,
            removed_findings=[finding],
        )
