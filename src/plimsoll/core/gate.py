"""The gate: the deterministic, authoritative half of the system.

The gate runs the available `Check` adapters, normalises their output, and returns a
`GateResult`. It is pure with respect to the workspace -- it never writes to it.

This same code path runs before the model to produce findings and again after it to
judge a patch. There is no lighter second gate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from plimsoll.core.models import (
    CheckConfig,
    FailedCheck,
    Finding,
    GateConfig,
    GateResult,
    SkippedCheck,
    ToolAvailability,
    ToolVersion,
)


class CheckExecutionError(RuntimeError):
    """An adapter could not complete: binary vanished, timed out, unparseable output.

    Raised by adapters, caught by the gate, reported as a `FailedCheck`. It is never
    fatal to the run.
    """


PlanProvider = Callable[[Path, CheckConfig], Path | None]
"""Produces a `terraform plan` JSON file for a workspace, or None when it cannot.

Injected into `Gate` rather than imported by it. The gate core does not know what
terraform is, which is what lets a second IaC format reuse it later.
"""


class CheckContext:
    """What an adapter is handed for one run.

    Carries the workspace and config, plus the plan artifact some adapters consume and
    others ignore. `opa` evaluates Rego against `terraform plan` JSON that the
    `terraform_plan` adapter produces; without a shared context the plan would be
    computed twice, or the two adapters would disagree about the flags it was built with.

    One instance per gate run, so the plan is produced at most once no matter how many
    adapters ask for it.
    """

    def __init__(
        self,
        workspace: Path,
        config: CheckConfig,
        plan_provider: PlanProvider | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self._plan_provider = plan_provider
        self._plan: Path | None = None
        self._planned = False
        self._plan_error: Exception | None = None

    def plan_json(self) -> Path | None:
        """Path to the plan JSON, or None when no plan is available.

        None is not an error -- an adapter that needs a plan reports itself skipped, the
        same as one whose binary is missing. A provider that raises does so identically
        for every adapter that asks, so two consumers cannot reach different conclusions
        about the same workspace.
        """
        if self._plan_error is not None:
            raise self._plan_error
        if not self._planned:
            self._planned = True
            if self._plan_provider is None:
                return None
            try:
                self._plan = self._plan_provider(self.workspace, self.config)
            except Exception as exc:
                self._plan_error = exc
                raise
        return self._plan


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
    # No plan provider yet: it arrives with `checks/terraform_plan.py`. Until then
    # `ctx.plan_json()` is None and a plan-consuming adapter reports itself skipped.
    return Gate(build_registered_checks())


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _sort_key(finding: Finding) -> tuple[int, str, int, str]:
    return (-finding.severity.rank, finding.file.as_posix(), finding.line_start, finding.rule_id)


class Gate:
    """Runs check adapters and aggregates their findings."""

    def __init__(self, checks: Sequence[Check], plan_provider: PlanProvider | None = None) -> None:
        self._checks = list(checks)
        self._plan_provider = plan_provider

    @property
    def checks(self) -> list[Check]:
        return list(self._checks)

    def run(self, workspace: Path, config: GateConfig | None = None) -> GateResult:
        cfg = config or GateConfig()
        # One context per run, so a plan is produced at most once however many
        # adapters ask for it.
        ctx = CheckContext(workspace, cfg.check, self._plan_provider)
        result = GateResult()
        for check in self._checks:
            self._run_one(check, ctx, result)
        result.findings.sort(key=_sort_key)
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
