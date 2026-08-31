"""A pull request comment.

This is the surface most people will actually judge the tool by, so it is written for
someone who is not reading carefully: the summary line first, the things that block the
merge above the things that do not, verified diffs folded away in `<details>` so a
five-finding run is still one screen, and an explicit block for every finding the tool
could not fix.

Two rules that are not formatting preferences:

**A patch that failed re-verification is not shown.** Not folded, not labelled
"unverified suggestion". It does not exist by the time this module runs -- `core.loop`
discarded it -- and nothing here reconstructs it. What a reviewer sees under "could not
fix" is the original finding and the gate's reason.

**The footer always names what ran.** A comment listing three findings from a run where
two of four adapters were missing is a different claim from the same comment after a full
run, and the reader cannot tell them apart unless it says so.
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

TITLE = "## Plimsoll"

_ICONS: dict[Severity, str] = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "⚪",
}

_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


def _location(finding: Finding) -> str:
    where = finding.file.as_posix() or "unknown file"
    return f"`{where}:{finding.line_start}`" if finding.line_start else f"`{where}`"


def _subject(finding: Finding) -> str:
    return f"`{finding.resource_address}`" if finding.resource_address else _location(finding)


def _details(summary: str, body: str, language: str = "diff") -> list[str]:
    return [
        "<details>",
        f"<summary>{summary}</summary>",
        "",
        f"```{language}",
        body.rstrip("\n"),
        "```",
        "",
        "</details>",
    ]


def _finding_lines(finding: Finding) -> list[str]:
    lines = [
        f"**{finding.rule_class}** on {_subject(finding)} — {_location(finding)}",
        "",
        finding.message,
    ]
    if finding.also_reported_by:
        others = ", ".join(sorted(s.value for s in finding.also_reported_by))
        lines.append(f"<sub>Also reported by {others}.</sub>")
    lines.append(f"<sub>{finding.source.value} · `{finding.rule_id}`</sub>")
    return lines


def _decision_lines(decision: Decision) -> list[str]:
    finding = decision.finding
    lines = [
        f"#### {_ICONS[finding.severity]} {finding.severity.value} — {finding.rule_class}",
        "",
        f"On {_subject(finding)} at {_location(finding)}.",
        "",
        finding.message,
    ]
    if finding.also_reported_by:
        others = ", ".join(sorted(s.value for s in finding.also_reported_by))
        lines.append(f"<sub>Also reported by {others}.</sub>")
    lines.append(f"<sub>{finding.source.value} · `{finding.rule_id}`</sub>")

    ref = decision.exception_ref
    if ref is not None and ref.expired:
        when = f" on {ref.expires.isoformat()}" if ref.expires else ""
        lines += [
            "",
            f"> ⚠️ **Exception expired{when}.** {ref.approved_by} accepted this "
            f"previously; the sign-off has lapsed and the finding is live again.",
        ]
    elif ref is not None:
        lines += ["", f"> ✅ **Exempted.** {ref.approved_by}: {ref.justification}"]

    if decision.explanation:
        lines += ["", decision.explanation]

    if decision.status is DecisionStatus.FIXED and decision.patch is not None:
        lines += [""]
        lines += _details(
            f"Verified fix (attempt {decision.attempts}) — re-checked by the same gate",
            decision.patch.diff,
        )
    elif decision.status is DecisionStatus.ESCALATED:
        reason = (
            decision.verification.failure_reason
            if decision.verification and decision.verification.failure_reason
            else "no usable patch was produced"
        )
        lines += [
            "",
            f"> ❌ **Could not fix** after {decision.attempts} attempt(s). {reason}",
            ">",
            "> No unverified patch is offered. Every draft was re-run through the same "
            "checks that produced this finding and none of them passed, so showing one "
            "would be guessing. This needs a human.",
        ]
    return lines


def _footer(result: GateResult) -> list[str]:
    ran = ", ".join(f"{t.name} {t.version}" if t.version else t.name for t in result.tooling)
    lines = ["", "---", "", f"<sub>Checks run: {ran or 'none'}."]

    for skipped in result.checks_skipped:
        lines.append(f"<br>Skipped {skipped.name}: {skipped.reason}")
    for failed in result.checks_failed:
        lines.append(f"<br>**Failed** {failed.name}: {failed.error}")
    lines.append("</sub>")
    return lines


def _banner(result: GateResult, timed_out: bool) -> list[str]:
    lines: list[str] = []
    if timed_out or result.timed_out:
        lines.append(
            "> ⏱ **The run timed out.** What follows is partial and is not a verification."
        )
    if not result.any_check_ran:
        lines.append(
            "> ⛔ **Nothing was verified.** No check could run, so an empty report below "
            "does not mean this configuration is clean."
        )
    return [*lines, ""] if lines else []


def _group(items: list[Decision]) -> list[tuple[Severity, list[Decision]]]:
    return [
        (severity, [d for d in items if d.finding.severity is severity])
        for severity in _ORDER
        if any(d.finding.severity is severity for d in items)
    ]


def render_github(result: GateResult, *, fail_on: Severity) -> str:
    """A scan comment: findings only, no decisions."""
    blocking = result.findings_at_or_above(fail_on)
    lines = [TITLE, ""]
    lines += _banner(result, timed_out=False)

    if not result.findings:
        lines.append("No findings. ✅")
    else:
        lines.append(
            f"**{len(result.findings)} finding(s)**, {len(blocking)} at or above `{fail_on.value}`."
        )
        for severity in _ORDER:
            group = [f for f in result.findings if f.severity is severity]
            if not group:
                continue
            lines += ["", f"### {_ICONS[severity]} {severity.value}"]
            for finding in group:
                lines += ["", *_finding_lines(finding)]

    return "\n".join(lines + _footer(result))


def render_verify_github(result: VerifyResult, *, fail_on: Severity) -> str:
    """A verify comment: what was fixed, what was exempted, what needs a human."""
    fixed = result.fixed
    escalated = result.with_status(DecisionStatus.ESCALATED)
    blocking = result.unresolved_at_or_above

    lines = [TITLE, ""]
    lines += _banner(result.gate, result.timed_out)

    if not result.decisions:
        lines.append("No findings. ✅")
        return "\n".join(lines + _footer(result.gate))

    lines.append(
        f"**{len(result.decisions)} finding(s)** — {len(fixed)} fixed and verified, "
        f"{len(escalated)} escalated, {len(result.exempted)} exempted, "
        f"{len(blocking)} unresolved at or above `{fail_on.value}`."
    )
    if result.llm_unavailable_reason:
        lines += [
            "",
            f"> No model was used ({result.llm_unavailable_reason}). Findings below "
            "are deterministic; nothing was fixed.",
        ]
    if result.expired_exceptions:
        lines += [
            "",
            f"> ⚠️ {len(result.expired_exceptions)} exception(s) have expired and no "
            "longer exempt anything.",
        ]

    # Blocking first: a reviewer who reads one section should read the one that fails
    # the build, not the one that succeeded.
    for heading, group in (
        # `unresolved_at_or_above` is exactly ESCALATED plus BLOCKED, which is the set
        # that fails the build.
        ("Needs a human", blocking),
        ("Fixed and verified", fixed),
        ("Exempted", result.exempted),
        (
            "Reported",
            [d for d in result.decisions if d.status is DecisionStatus.REPORTED],
        ),
    ):
        if not group:
            continue
        lines += ["", f"### {heading}"]
        for _severity, items in _group(group):
            for decision in items:
                lines += ["", *_decision_lines(decision)]

    return "\n".join(lines + _footer(result.gate))
