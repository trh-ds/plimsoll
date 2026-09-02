"""The run budget, and the promise that a truncated run never reports clean."""

from __future__ import annotations

import time

import pytest
from typer.testing import CliRunner

from conftest import INSECURE_TF, MarkerCheck, ScriptedProvider, write_workspace
from plimsoll import cli
from plimsoll.core.deadline import Deadline
from plimsoll.core.gate import CheckContext, Gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import Finding, GateConfig, Severity, ToolAvailability

runner = CliRunner()


def test_an_unbounded_deadline_never_expires_and_never_caps():
    budget = Deadline.none()
    assert budget.bounded is False
    assert budget.remaining is None
    assert budget.expired is False
    assert budget.cap(120) == 120


def test_a_bounded_deadline_caps_a_subprocess_timeout(monkeypatch):
    # Real elapsed time between construction and `.cap()` would make `int(remaining)`
    # truncate below the exact second boundary on any clock with enough resolution to
    # see it -- flaky on Linux CI, silently masked on Windows' coarser monotonic(). Freeze
    # the clock so the arithmetic is exercised without depending on wall time at all.
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    budget = Deadline(5)
    assert budget.bounded is True
    assert budget.cap(120) == 5
    # A generous budget does not stretch a short per-check timeout.
    assert Deadline(600).cap(120) == 120


def test_cap_never_returns_zero():
    # subprocess.run(timeout=0) raises immediately with a confusing error; the caller
    # checks `expired` at the seam instead.
    assert Deadline(0).cap(120) == 1


def test_an_exhausted_deadline_is_expired():
    budget = Deadline(0)
    assert budget.expired is True
    assert budget.remaining == 0.0


class SlowCheck:
    """Burns the budget, then reports nothing. The check after it must not run."""

    def __init__(self, name: str = "slow", seconds: float = 0.05) -> None:
        self.name = name
        self._seconds = seconds
        self.runs = 0

    def is_available(self) -> ToolAvailability:
        return ToolAvailability.ok(self.name, "1.0")

    def run(self, ctx: CheckContext) -> list[Finding]:
        self.runs += 1
        time.sleep(self._seconds)
        return []


def test_the_gate_stops_at_the_seam_and_marks_the_result(tmp_path):
    # The deadline must survive per-run setup (a real TemporaryDirectory, a real
    # CheckContext) without expiring before the first check gets a turn, so it needs
    # more headroom than a bare 10ms under a loaded machine -- but still far short of
    # a check's own sleep, so the second check is skipped once the first burns it.
    first, second = SlowCheck("first", 0.3), SlowCheck("second", 0.3)
    result = Gate([first, second]).run(tmp_path, GateConfig(), Deadline(0.05))

    assert first.runs == 1
    assert second.runs == 0
    assert result.timed_out is True


def test_the_loop_stops_attempting_and_still_decides_every_finding(tmp_path):
    write_workspace(tmp_path, INSECURE_TF)
    # The gate finishes inside the budget and exhausts it on the way out, which is the
    # case `GateResult.timed_out` alone cannot see.
    loop = VerifierLoop(
        Gate([MarkerCheck(), SlowCheck("slow", 0.3)]),
        GateConfig(fail_on=Severity.HIGH),
        ScriptedProvider(),  # exhausted: reaching it at all would raise
    )

    result = loop.run(tmp_path, deadline=Deadline(0.2))

    assert result.timed_out is True
    # Nothing was dropped because the clock ran out.
    assert len(result.decisions) == len(result.gate.findings)
    assert result.unresolved_at_or_above


class StubCheck:
    def __init__(self, name: str = "checkov") -> None:
        self.name = name

    def is_available(self) -> ToolAvailability:
        return ToolAvailability.ok(self.name, "3.3.15")

    def run(self, ctx: CheckContext) -> list[Finding]:
        return []


@pytest.mark.parametrize("command", ["scan", "verify"])
def test_a_timed_out_run_exits_four_and_says_so(monkeypatch, tmp_path, command):
    monkeypatch.delenv("PLIMSOLL_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(cli, "default_gate", lambda: Gate([SlowCheck("slow", 1.2), StubCheck()]))

    result = runner.invoke(cli.app, [command, str(tmp_path), "--timeout", "1"])

    assert result.exit_code == 4
    assert "TIMED OUT" in result.output


@pytest.mark.parametrize("flag", ["--timeout", "--check-timeout"])
def test_a_non_positive_budget_is_a_config_error(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(cli, "default_gate", lambda: Gate([StubCheck()]))
    result = runner.invoke(cli.app, ["scan", str(tmp_path), flag, "0"])
    assert result.exit_code == 2
