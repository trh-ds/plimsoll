"""The gate: the deterministic, authoritative half of the system.

The gate runs the available `Check` adapters, normalises their output, and returns a
`GateResult`. It is pure with respect to the workspace -- it never writes to it.

This same code path runs before the model to produce findings and again after it to
judge a patch. There is no lighter second gate.
"""

from __future__ import annotations

import shutil
import tempfile
import weakref
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TypeVar, cast

from plimsoll.core.deadline import Deadline
from plimsoll.core.models import (
    CheckConfig,
    FailedCheck,
    Finding,
    GateConfig,
    GateResult,
    SkippedCheck,
    SuppressedFinding,
    SuppressionKind,
    ToolAvailability,
    ToolVersion,
)

T = TypeVar("T")


class CheckExecutionError(RuntimeError):
    """An adapter could not complete: binary vanished, timed out, unparseable output.

    Raised by adapters, caught by the gate, reported as a `FailedCheck`. It is never
    fatal to the run.
    """


class CheckSkipped(Exception):
    """An adapter is installed but had nothing to evaluate.

    Distinct from `CheckExecutionError` because the two mean different things to a
    reader: `opa` with no plan to evaluate did not fail, it never started. Recorded as a
    `SkippedCheck` and excluded from `tooling`, so a run where only skipped adapters
    were present still exits 3 rather than reporting clean.

    Discovered at run time rather than at `is_available()`: whether a plan exists is not
    knowable before the adapters that produce one have run.
    """


PlanProvider = Callable[["CheckContext"], Path | None]
"""Produces a `terraform plan` JSON file for a workspace, or None when it cannot.

Injected into `Gate` rather than imported by it. The gate core does not know what
terraform is, which is what lets a second IaC format reuse it later. It is handed the
whole context because producing a plan needs the scratch directory and the run's
remaining time budget, not just the workspace path.
"""


class CheckContext:
    """What an adapter is handed for one run.

    Carries the workspace, the config, a scratch directory, the run deadline, and the
    plan artifact some adapters consume and others ignore. `opa` evaluates Rego against
    `terraform plan` JSON that `terraform_plan` produces; without a shared context the
    plan would be computed twice, or the two adapters would disagree about the flags it
    was built with.

    One instance per gate run, so every shared artifact is produced at most once no
    matter how many adapters ask for it.
    """

    def __init__(
        self,
        workspace: Path,
        config: CheckConfig,
        plan_provider: PlanProvider | None = None,
        *,
        scratch: Path | None = None,
        deadline: Deadline | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.deadline = deadline or Deadline.none()
        self._plan_provider = plan_provider
        self._scratch = scratch
        self._memo: dict[str, tuple[object, Exception | None]] = {}

    @property
    def scratch(self) -> Path:
        """A writable directory for this run's artifacts.

        Adapters never write to the workspace -- `plimsoll` reports, it does not edit
        your repository -- so anything terraform insists on producing on disk (a
        provider cache, a plan file) is produced here. Normally supplied and cleaned up
        by `Gate.run`; created on demand for a context built directly.
        """
        if self._scratch is None:
            self._scratch = Path(tempfile.mkdtemp(prefix="plimsoll-ctx-"))
            weakref.finalize(self, shutil.rmtree, self._scratch, True)
        return self._scratch

    def once(self, key: str, factory: Callable[[], T]) -> T:
        """Memoize one shared artifact for this run, including a failure to produce it.

        Two adapters asking for the same expensive thing -- a `terraform init`, a plan --
        must get the same answer, and the same exception when there is one. Otherwise
        they can reach different conclusions about one workspace and the report contains
        both.
        """
        cached = self._memo.get(key)
        if cached is not None:
            value, error = cached
            if error is not None:
                raise error
            return cast(T, value)
        try:
            produced = factory()
        except Exception as exc:
            self._memo[key] = (None, exc)
            raise
        self._memo[key] = (produced, None)
        return produced

    def plan_json(self) -> Path | None:
        """Path to the plan JSON, or None when no plan is available.

        None is not an error -- an adapter that needs a plan reports itself skipped, the
        same as one whose binary is missing. A provider that raises does so identically
        for every adapter that asks, so two consumers cannot reach different conclusions
        about the same workspace.
        """
        if self._plan_provider is None:
            return None
        provider = self._plan_provider
        return self.once("plan_json", lambda: provider(self))


class Check(Protocol):
    """One external tool, normalised.

    Implementations shell out to a single binary, parse its structured output, and map
    it into `Finding`. They never scrape human-readable text and never mutate the
    workspace.
    """

    name: str

    def is_available(self) -> ToolAvailability: ...

    def run(self, ctx: CheckContext) -> list[Finding]: ...


CheckFactory = Callable[[], Check]

_REGISTRY: dict[str, CheckFactory] = {}


def register(name: str) -> Callable[[CheckFactory], CheckFactory]:
    """Register an adapter under `name`. Applied to the adapter class."""

    def decorate(factory: CheckFactory) -> CheckFactory:
        if name in _REGISTRY:
            raise ValueError(f"check already registered: {name}")
        _REGISTRY[name] = factory
        return factory

    return decorate


def _load_adapters() -> None:
    """Import the adapter package for its registration side effects.

    Deferred to call time: the adapters import this module, so a top-level import here
    would be circular.
    """
    import plimsoll.checks  # noqa: F401


def registered_names() -> list[str]:
    _load_adapters()
    return sorted(_REGISTRY)


def build_registered_checks() -> list[Check]:
    """Instantiate every registered adapter."""
    return [_REGISTRY[name]() for name in registered_names()]


def default_gate() -> Gate:
    """Every registered adapter, with terraform wired in as the plan provider.

    Imported late so the gate core keeps no static dependency on terraform: a second
    IaC format supplies a different provider and reuses everything here.
    """
    from plimsoll.checks.terraform_plan import plan_provider

    return Gate(build_registered_checks(), plan_provider)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _sort_key(finding: Finding) -> tuple[int, str, int, str]:
    return (-finding.severity.rank, finding.file.as_posix(), finding.line_start, finding.rule_id)


def _dedupe_key(finding: Finding) -> tuple[str, str] | None:
    """The identity two tools must agree on to be reporting the same violation.

    None means "do not deduplicate this one". A finding with no resource address is
    file-level -- a hardcoded credential, a whole-file parse error -- and two of those
    in different places share a rule class without being the same problem. Merging them
    would delete a real finding, which is a far worse failure than showing a duplicate.
    """
    if not finding.resource_address:
        return None
    return (finding.resource_address, finding.rule_class)


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    """Collapse one violation reported by several tools into one finding.

    Checkov and a bundled Rego rule both flag a public bucket. Two findings for one
    problem doubles the model calls and makes the reviewer read the same thing twice.

    Expects `findings` already sorted, so the first of a group is the most severe and
    therefore the one kept.
    """
    kept: dict[tuple[str, str], Finding] = {}
    out: list[Finding] = []
    for finding in findings:
        key = _dedupe_key(finding)
        if key is None:
            out.append(finding)
            continue
        survivor = kept.get(key)
        if survivor is None:
            kept[key] = finding
            out.append(finding)
        elif finding.source != survivor.source and finding.source not in survivor.also_reported_by:
            # One tool can also duplicate itself: checkov's CKV_AWS_62 and CKV2_AWS_40
            # both mean "this policy grants admin". Those collapse without adding the
            # tool to its own credit list.
            survivor.also_reported_by.append(finding.source)
    return out


def _apply_baseline(result: GateResult, baseline: frozenset[str]) -> None:
    """Move findings that predate the baseline out of the counted set.

    After deduplication, so one violation two tools reported is matched once.
    """
    kept: list[Finding] = []
    for finding in result.findings:
        if finding.fingerprint in baseline:
            result.suppressed.append(
                SuppressedFinding(finding=finding, kind=SuppressionKind.BASELINE)
            )
        else:
            kept.append(finding)
    result.findings = kept


class Gate:
    """Runs check adapters and aggregates their findings."""

    def __init__(self, checks: Sequence[Check], plan_provider: PlanProvider | None = None) -> None:
        self._checks = list(checks)
        self._plan_provider = plan_provider

    @property
    def checks(self) -> list[Check]:
        return list(self._checks)

    def run(
        self,
        workspace: Path,
        config: GateConfig | None = None,
        deadline: Deadline | None = None,
    ) -> GateResult:
        cfg = config or GateConfig()
        budget = deadline or Deadline.none()
        result = GateResult()
        # One context per run, so a plan is produced at most once however many adapters
        # ask for it, and one scratch directory that is gone when the run is.
        with tempfile.TemporaryDirectory(prefix="plimsoll-run-") as tmp:
            ctx = CheckContext(
                workspace,
                cfg.check,
                self._plan_provider,
                scratch=Path(tmp),
                deadline=budget,
            )
            for check in self._checks:
                # Checked at the seam rather than mid-adapter: a half-killed subprocess
                # tells us nothing, and the remaining checks would report against a
                # workspace we stopped understanding.
                if budget.expired:
                    result.timed_out = True
                    break
                self._run_one(check, ctx, result)
        result.findings.sort(key=_sort_key)
        result.findings = _deduplicate(result.findings)
        if cfg.baseline:
            _apply_baseline(result, cfg.baseline)
        return result

    def _run_one(self, check: Check, ctx: CheckContext, result: GateResult) -> None:
        # Per-adapter isolation: a crashing check is recorded and the run continues.
        # `Exception` and not `BaseException` -- Ctrl-C still stops the run.
        try:
            availability = check.is_available()
        except Exception as exc:
            result.checks_failed.append(FailedCheck(name=check.name, error=_describe(exc)))
            return

        if not availability.available:
            result.checks_skipped.append(
                SkippedCheck(name=check.name, reason=availability.reason or "unavailable")
            )
            return

        try:
            findings = check.run(ctx)
        except CheckSkipped as exc:
            result.checks_skipped.append(SkippedCheck(name=check.name, reason=str(exc)))
            return
        except Exception as exc:
            result.checks_failed.append(FailedCheck(name=check.name, error=_describe(exc)))
            return

        result.findings.extend(findings)
        self._record_tool(result, availability)

    @staticmethod
    def _record_tool(result: GateResult, availability: ToolAvailability) -> None:
        # Two adapters can share a binary (validate and plan both use terraform);
        # provenance wants one entry per tool.
        entry = ToolVersion(name=availability.tool, version=availability.version)
        if entry not in result.tooling:
            result.tooling.append(entry)
