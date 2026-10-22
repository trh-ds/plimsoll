"""Human-readable report.

Plain text, no colour library, no table dependency. This output is read as often from a
CI log as from a terminal, and CI logs strip colour.
"""

from __future__ import annotations

from plimsoll.core.models import (
    Decision,
    DecisionStatus,
    Finding,
    GateResult,
    Severity,
    VerifyResult,
)


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


def _header(result: GateResult) -> list[str]:
    """What ran, always first.

    A reader who skips everything else must still see whether anything was verified.
    """
    lines: list[str] = []
    if result.timed_out:
        # First line, not a footnote. Everything below it is partial, and a reader who
        # scrolls to the summary and sees a small number has to know why it is small.
        lines.append("TIMED OUT: the run budget expired. What follows is incomplete.")
    if result.tooling:
        ran = ", ".join(f"{t.name} {t.version}" if t.version else t.name for t in result.tooling)
        lines.append(f"Checks run: {ran}")
    else:
        lines.append("Checks run: none")

    for skipped in result.checks_skipped:
        lines.append(f"  skipped {skipped.name}: {skipped.reason}")
    for failed in result.checks_failed:
        lines.append(f"  FAILED  {failed.name}: {failed.error}")
    summary = result.suppression_summary()
    if summary:
        # In the header, not the footer: a short findings list means something different
        # when forty more are being held back, and the reader should know before reading it.
        lines.append(f"  {summary}; not counted toward the exit code.")
    return lines


def render_terminal(result: GateResult, *, fail_on: Severity) -> str:
    lines = _header(result)
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

    if result.timed_out:
        lines.append("The run timed out: this is a partial scan and not a verification.")
    if not result.any_check_ran:
        lines.append("Nothing was verified: no check was able to run.")

    return "\n".join(lines)


def _indent(text: str, prefix: str = "           ") -> str:
    return "\n".join(f"{prefix}{line}" for line in text.strip().splitlines())


def _decision_block(decision: Decision) -> list[str]:
    finding = decision.finding
    lines = [
        f"  {decision.status.value:<10} {finding.severity.value:<8} {finding.rule_class}",
        f"           {finding.resource_address or _location(finding)}  {_location(finding)}",
        f"           {finding.message}",
    ]

    ref = decision.exception_ref
    if ref is not None:
        lines.append("")
        if ref.expired:
            # Said loudly. A reviewer who signed this off deserves to be told the
            # sign-off lapsed, not to meet the finding again as though it were new.
            when = f" on {ref.expires.isoformat()}" if ref.expires else ""
            lines.append(
                f"           EXPIRED EXCEPTION: {ref.approved_by}'s sign-off lapsed{when}."
            )
        else:
            lines.append(f"           exempted by {ref.approved_by}: {ref.justification}")
        if ref.approved_in:
            lines.append(f"           approved in {ref.approved_in}")

    if decision.explanation:
        lines.append("")
        lines.append(_indent(decision.explanation))

    if decision.status is DecisionStatus.FIXED and decision.patch is not None:
        lines.append("")
        lines.append(f"           verified fix (attempt {decision.attempts}):")
        lines.append(_indent(decision.patch.diff))
    elif decision.status is DecisionStatus.ESCALATED:
        lines.append("")
        reason = (
            decision.verification.failure_reason
            if decision.verification and decision.verification.failure_reason
            else "no usable patch was produced"
        )
        lines.append(f"           could not fix after {decision.attempts} attempt(s): {reason}")
        # Said explicitly, because the absence of a diff here is the product working.
        lines.append("           no unverified patch is shown; this needs a human.")

    lines.append("")
    return lines


def render_verify_terminal(result: VerifyResult, *, fail_on: Severity) -> str:
    lines = _header(result.gate)

    # The gate can finish inside the budget and the loop exhaust it afterwards, so this
    # is checked on the run as well as on the gate result.
    if result.timed_out and not result.gate.timed_out:
        lines.insert(0, "TIMED OUT: the run budget expired. What follows is incomplete.")

    if result.llm_description:
        lines.append(f"Model: {result.llm_description}")
    if result.llm_unavailable_reason:
        lines.append(f"Model: not used -- {result.llm_unavailable_reason}")
        lines.append("  Deterministic findings are reported below; nothing was fixed.")

    lines.append("")

    if not result.decisions:
        lines.append("No findings.")
    else:
        for decision in result.decisions:
            lines.extend(_decision_block(decision))

    fixed = len(result.fixed)
    escalated = len(result.with_status(DecisionStatus.ESCALATED))
    blocking = len(result.unresolved_at_or_above)
    lines.append(
        f"{len(result.decisions)} finding(s): {fixed} fixed and verified, "
        f"{escalated} escalated, {len(result.exempted)} exempted, "
        f"{blocking} unresolved at or above {fail_on.value}."
    )

    expired = len(result.expired_exceptions)
    if expired:
        lines.append(f"{expired} exception(s) have expired and no longer exempt anything.")

    if result.timed_out:
        lines.append(
            "The run timed out. Findings past the budget were never attempted, and this "
            "is not a verification."
        )
    if not result.gate.any_check_ran:
        lines.append("Nothing was verified: no check was able to run.")

    return "\n".join(lines)
