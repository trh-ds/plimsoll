"""Output formats for a gate run.

Every reporter states which checks ran, which were skipped, and which failed. That is not
decoration: a report that lists findings without saying what produced them cannot be
distinguished from a report from a broken toolchain, and silence must never read as
success.
"""

from plimsoll.reporters.json_report import render_json
from plimsoll.reporters.terminal import render_terminal

__all__ = ["render_json", "render_terminal"]
