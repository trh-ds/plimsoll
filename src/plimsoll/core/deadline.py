"""The whole-run budget behind `--timeout` and exit code 4.

`CheckConfig.timeout_seconds` bounds one subprocess. That is not the same promise: a
workspace with six adapters and three retries per finding can sit well inside every
per-check timeout and still take twenty minutes, which in CI reads as a hang.

A `Deadline` is checked at the seams -- between adapters, between findings, between
patch attempts -- and it also *caps* each subprocess timeout, so an adapter cannot
overrun the budget it was admitted under. Nothing is killed mid-write: the run stops
at the next seam and reports what it has, marked `timed_out`, which the CLI turns into
exit code 4.

A run that times out never reports clean. Partial verification is not verification.
"""

from __future__ import annotations

import time


class Deadline:
    """A monotonic budget for one run. `None` seconds means no budget."""

    __slots__ = ("_end",)

    def __init__(self, seconds: float | None = None) -> None:
        self._end = None if seconds is None else time.monotonic() + seconds

    @classmethod
    def none(cls) -> Deadline:
        """An unbounded deadline. Used where a budget is optional."""
        return cls(None)

    @property
    def bounded(self) -> bool:
        return self._end is not None

    @property
    def remaining(self) -> float | None:
        """Seconds left, or None when unbounded. Never negative."""
        if self._end is None:
            return None
        return max(0.0, self._end - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.remaining == 0.0 if self._end is not None else False

    def cap(self, seconds: int) -> int:
        """Shrink a per-subprocess timeout to what is left of the budget.

        Returns at least 1: handing `subprocess.run` a timeout of 0 raises immediately
        with a confusing error, and the caller checks `expired` at the seam anyway.
        """
        remaining = self.remaining
        if remaining is None:
            return seconds
        return max(1, min(seconds, int(remaining)))
