"""JUnit XML, for the CI systems that read test reports and not SARIF.

GitLab's merge request widget, Jenkins, Azure Pipelines, CircleCI, and Buildkite all show
a JUnit report next to the pipeline without a plugin. SARIF reaches GitHub; this reaches
everything else.

The mapping, and the reasons for it:

- **A blocking finding is a `<failure>`.** Exactly the set that sets exit code 1, so the
  test widget and the pipeline status never disagree about the same run.
- **A finding below the threshold is a passing test case** with the message in
  `<system-out>`. Reported, not hidden, and not red: a widget that shows forty failures
  on a pipeline that passed teaches people to ignore the widget.
- **A suppressed finding is `<skipped>`**, with the reason, so the debt stays countable.
- **Every check is a test case of its own.** A check that ran passes, a check that did not
  run is skipped, a check that failed is an `<error>`. Without this a run where every
  adapter was missing produces an empty suite, and an empty suite reads as green in most
  of the tools above. Silence must never read as success.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from plimsoll.core.models import (
    Decision,
    DecisionStatus,
    Finding,
    GateResult,
    Severity,
    SuppressedFinding,
    SuppressionKind,
    VerifyResult,
)

SUITE_NAME = "plimsoll"


def _case_name(finding: Finding) -> str:
    where = finding.file.as_posix() or "unknown file"
    if finding.line_start:
        where = f"{where}:{finding.line_start}"
    subject = finding.resource_address or where
    return f"{subject} ({where})" if finding.resource_address else subject


def _case(suite: ET.Element, finding: Finding) -> ET.Element:
    return ET.SubElement(
        suite,
        "testcase",
        {
            "classname": finding.rule_class,
            "name": _case_name(finding),
            "file": finding.file.as_posix(),
            **({"line": str(finding.line_start)} if finding.line_start else {}),
        },
    )


def _body(finding: Finding) -> str:
    lines = [
        f"{finding.severity.value} {finding.rule_class} on "
        f"{finding.resource_address or finding.file.as_posix()}",
        finding.message,
        f"reported by {finding.source.value} as {finding.rule_id}",
    ]
    if finding.also_reported_by:
        lines.append(
            "also reported by " + ", ".join(sorted(s.value for s in finding.also_reported_by))
        )
    return "\n".join(lines)


def _fail(case: ET.Element, finding: Finding, extra: str = "") -> None:
    failure = ET.SubElement(
        case,
        "failure",
        {"message": f"{finding.severity.value}: {finding.message}", "type": finding.rule_class},
    )
    failure.text = _body(finding) + (f"\n\n{extra}" if extra else "")


def _out(case: ET.Element, text: str) -> None:
    ET.SubElement(case, "system-out").text = text


def _suppressed(suite: ET.Element, suppressed: SuppressedFinding) -> None:
    case = _case(suite, suppressed.finding)
    ref = suppressed.exception_ref
    if suppressed.kind is SuppressionKind.EXCEPTION and ref is not None:
        reason = f"exempted by {ref.approved_by}: {ref.justification}"
    else:
        reason = "present in the baseline"
    ET.SubElement(case, "skipped", {"message": reason})


def _checks(suite: ET.Element, result: GateResult) -> None:
    for tool in result.tooling:
        name = f"{tool.name} {tool.version}" if tool.version else tool.name
        ET.SubElement(suite, "testcase", {"classname": "plimsoll.check", "name": name})
    for skipped in result.checks_skipped:
        case = ET.SubElement(
            suite, "testcase", {"classname": "plimsoll.check", "name": skipped.name}
        )
        ET.SubElement(case, "skipped", {"message": skipped.reason})
    for failed in result.checks_failed:
        case = ET.SubElement(
            suite, "testcase", {"classname": "plimsoll.check", "name": failed.name}
        )
        ET.SubElement(case, "error", {"message": failed.error}).text = failed.error
    if not result.any_check_ran:
        case = ET.SubElement(
            suite, "testcase", {"classname": "plimsoll.check", "name": "nothing verified"}
        )
        ET.SubElement(
            case,
            "error",
            {"message": "No check was able to run, so nothing was verified."},
        )
    if result.timed_out:
        case = ET.SubElement(suite, "testcase", {"classname": "plimsoll.check", "name": "budget"})
        ET.SubElement(
            case,
            "error",
            {"message": "The run exceeded --timeout; these results are partial."},
        )


def _serialise(suite: ET.Element) -> str:
    cases = suite.findall("testcase")
    suite.set("tests", str(len(cases)))
    suite.set("failures", str(sum(1 for c in cases if c.find("failure") is not None)))
    suite.set("errors", str(sum(1 for c in cases if c.find("error") is not None)))
    suite.set("skipped", str(sum(1 for c in cases if c.find("skipped") is not None)))
    root = ET.Element("testsuites", {"name": SUITE_NAME})
    root.append(suite)
    for key in ("tests", "failures", "errors", "skipped"):
        root.set(key, suite.get(key, "0"))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def render_junit(result: GateResult, *, fail_on: Severity) -> str:
    suite = ET.Element("testsuite", {"name": SUITE_NAME})
    _checks(suite, result)
    for finding in result.findings:
        case = _case(suite, finding)
        if finding.severity.meets(fail_on):
            _fail(case, finding)
        else:
            _out(case, _body(finding))
    for suppressed in result.suppressed:
        _suppressed(suite, suppressed)
    return _serialise(suite)


def _decision(suite: ET.Element, decision: Decision) -> None:
    finding = decision.finding
    case = _case(suite, finding)
    status = decision.status
    if status is DecisionStatus.EXEMPTED:
        ref = decision.exception_ref
        reason = f"exempted by {ref.approved_by}: {ref.justification}" if ref else "exempted"
        ET.SubElement(case, "skipped", {"message": reason})
    elif status is DecisionStatus.ESCALATED:
        reason = (
            decision.verification.failure_reason
            if decision.verification and decision.verification.failure_reason
            else "no usable patch was produced"
        )
        _fail(
            case,
            finding,
            f"Could not fix after {decision.attempts} attempt(s): {reason}\n"
            "No unverified patch is offered; this needs a human.",
        )
    elif status is DecisionStatus.BLOCKED:
        _fail(case, finding)
    elif status is DecisionStatus.FIXED and decision.patch is not None:
        _out(
            case,
            f"{_body(finding)}\n\nFixed, and re-verified by the same checks "
            f"(attempt {decision.attempts}):\n{decision.patch.diff}",
        )
    else:
        # REPORTED and PASS. Only ESCALATED and BLOCKED fail the build, so only they fail
        # here; `fail_on` already decided which findings became which.
        _out(case, _body(finding))


def render_verify_junit(result: VerifyResult, *, fail_on: Severity) -> str:
    suite = ET.Element("testsuite", {"name": SUITE_NAME})
    _checks(suite, result.gate)
    if result.timed_out and not result.gate.timed_out:
        case = ET.SubElement(suite, "testcase", {"classname": "plimsoll.check", "name": "budget"})
        ET.SubElement(
            case, "error", {"message": "The run exceeded --timeout; these results are partial."}
        )
    for decision in result.decisions:
        _decision(suite, decision)
    for suppressed in result.gate.suppressed:
        _suppressed(suite, suppressed)
    return _serialise(suite)
