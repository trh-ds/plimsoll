"""Output formats for a gate run.

Every reporter states which checks ran, which were skipped, and which failed. That is not
decoration: a report that lists findings without saying what produced them cannot be
distinguished from a report from a broken toolchain, and silence must never read as
success.
"""

from plimsoll.reporters.github import render_github, render_verify_github
from plimsoll.reporters.json_report import render_json, render_verify_json
from plimsoll.reporters.sarif import render_sarif, render_verify_sarif
from plimsoll.reporters.terminal import render_terminal, render_verify_terminal

__all__ = [
    "render_github",
    "render_json",
    "render_sarif",
    "render_terminal",
    "render_verify_github",
    "render_verify_json",
    "render_verify_sarif",
    "render_verify_terminal",
]
