"""Human-readable report.

Plain text, no colour library, no table dependency. This output is read as often from a
CI log as from a terminal, and CI logs strip colour.
"""

from __future__ import annotations

from plimsoll.core.models import Finding, GateResult, Severity


def _location(finding: Finding) -> str:
    where = finding.file.as_posix() or "<unknown file>"
    if finding.line_start:
        where = f"{where}:{finding.line_start}"
    return where


def _finding_line(finding: Finding) -> str:
    subject = finding.resource_address or _location(finding)
    line = f"  {finding.severity.value:<8} {finding.rule_class:<34} {subject}"
    if finding.also_reported_by:
        others = ", ".join(sorted(s.value for s in finding.also_reported_by))
        return f"{line}\n           {finding.message}\n           also reported by: {others}"
    return f"{line}\n           {finding.message}"


def render_terminal(result: GateResult, *, fail_on: Severity) -> str:
    lines: list[str] = []

    # What ran comes first. A reader who skips everything else must still see whether
    # anything was actually verified.
    if result.tooling:
        ran = ", ".join(f"{t.name} {t.version}" if t.version else t.name for t in result.tooling)
        lines.append(f"Checks run: {ran}")
    else:
        lines.append("Checks run: none")

    for skipped in result.checks_skipped:
        lines.append(f"  skipped {skipped.name}: {skipped.reason}")
    for failed in result.checks_failed:
        lines.append(f"  FAILED  {failed.name}: {failed.error}")

    lines.append("")

    if not result.findings:
        lines.append("No findings.")
    else:
        for finding in result.findings:
            provenance = f"[{finding.source.value} {finding.rule_id}]"
            lines.append(_finding_line(finding))
            lines.append(f"           {_location(finding)}  {provenance}")
            lines.append("")

    blocking = result.findings_at_or_above(fail_on)
    lines.append(f"{len(result.findings)} finding(s), {len(blocking)} at or above {fail_on.value}.")

    if not result.any_check_ran:
        lines.append("Nothing was verified: no check was able to run.")

    return "\n".join(lines)
