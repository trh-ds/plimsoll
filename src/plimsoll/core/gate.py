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


class Check(Protocol):
    """One external tool, normalised.

    Implementations shell out to a single binary, parse its structured output, and map
    it into `Finding`. They never scrape human-readable text and never mutate the
    workspace.
    """

    name: str

    def is_available(self) -> ToolAvailability: ...

    def run(self, workspace: Path, config: CheckConfig) -> list[Finding]: ...


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
    return Gate(build_registered_checks())


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _sort_key(finding: Finding) -> tuple[int, str, int, str]:
    return (-finding.severity.rank, finding.file.as_posix(), finding.line_start, finding.rule_id)


class Gate:
    """Runs check adapters and aggregates their findings."""

    def __init__(self, checks: Sequence[Check]) -> None:
        self._checks = list(checks)

    @property
    def checks(self) -> list[Check]:
        return list(self._checks)

    def run(self, workspace: Path, config: GateConfig | None = None) -> GateResult:
        cfg = config or GateConfig()
        result = GateResult()
        for check in self._checks:
            self._run_one(check, workspace, cfg.check, result)
        result.findings.sort(key=_sort_key)
        return result

    def _run_one(
        self,
        check: Check,
        workspace: Path,
        config: CheckConfig,
        result: GateResult,
    ) -> None:
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
            findings = check.run(workspace, config)
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
