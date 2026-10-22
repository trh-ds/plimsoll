"""SARIF 2.1.0, for GitHub code scanning and anything else that reads the format.

The reason this reporter exists is that SARIF is where findings become annotations on
the diff a reviewer is already looking at, instead of lines in a log they have to go
find.

The reason it needs care is the same thing said the other way round: the code-scanning
UI shows *results*, and a run that produced no results looks identical whether the
workspace was clean or the toolchain was broken. So `invocations[]` carries
`executionSuccessful`, `exitSignalName`, and one `toolExecutionNotification` per skipped
and failed check. A reader who only sees the alerts list still gets nothing; a reader who
opens the run gets the truth, and the API carries it either way.

Rule metadata comes from `checks/rule_map.py`, so a rule's severity and benchmark
references in SARIF are the same ones the terminal reported. There is no second table.
"""

from __future__ import annotations

import json
from typing import Any

from plimsoll.checks.rule_map import PLIMSOLL
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

SARIF_VERSION = "2.1.0"
# The OASIS errata URL, not the one in the sarif-spec repository. The GitHub raw path is
# tied to a branch layout that has since been rearranged and now 404s, which makes
# `$schema` a claim no validator can act on. This one is the published standard's own
# address and is what the integration workflow validates against.
SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
INFORMATION_URI = "https://github.com/trh-ds/plimsoll"

# SARIF has three levels and Plimsoll has five. CRITICAL and HIGH both block a merge, so
# both are errors; LOW and INFO are notes. Collapsing MEDIUM into `warning` rather than
# `error` matches the default `--fail-on high`: a level that fails a build the CLI would
# have passed makes the two disagree about the same run.
_LEVELS: dict[Severity, str] = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}

# SARIF's own scale, so consumers that rank by number rather than by level order the
# results the way the terminal does.
_RANK: dict[Severity, float] = {
    Severity.CRITICAL: 9.5,
    Severity.HIGH: 8.0,
    Severity.MEDIUM: 5.0,
    Severity.LOW: 3.0,
    Severity.INFO: 1.0,
}


def level_for(severity: Severity) -> str:
    return _LEVELS[severity]


def _rule_id(finding: Finding) -> str:
    """The rule class, not the tool's own ID.

    A reviewer suppressing `s3.acl.public` in GitHub should not have to suppress it once
    for checkov and again for the bundled pack. `rule_id` is still on the result as a
    property, because it is what you search the tool's own documentation for.
    """
    return finding.rule_class


def _rules(findings: list[Finding]) -> list[dict[str, Any]]:
    """One `reportingDescriptor` per rule class present in this run."""
    seen: dict[str, dict[str, Any]] = {}
    for finding in findings:
        rule_id = _rule_id(finding)
        if rule_id in seen:
            continue
        mapped = PLIMSOLL.get(finding.rule_class)
        descriptor: dict[str, Any] = {
            "id": rule_id,
            "name": rule_id,
            "shortDescription": {"text": rule_id},
            "fullDescription": {"text": finding.message},
            "defaultConfiguration": {"level": level_for(finding.severity)},
            "properties": {
                "plimsoll-severity": finding.severity.value,
                "tags": sorted({finding.source.value, *(mapped.benchmark_refs if mapped else ())}),
            },
        }
        if mapped and mapped.benchmark_refs:
            descriptor["properties"]["benchmark-refs"] = list(mapped.benchmark_refs)
        seen[rule_id] = descriptor
    return [seen[key] for key in sorted(seen)]


def _location(finding: Finding) -> dict[str, Any]:
    # SARIF regions are 1-based and a zero line is invalid, which is exactly what the
    # locator produces when it cannot find the block a plan-only finding came from.
    region: dict[str, Any] = {"startLine": max(1, finding.line_start)}
    if finding.line_end and finding.line_end >= finding.line_start:
        region["endLine"] = max(1, finding.line_end)
    return {
        "physicalLocation": {
            "artifactLocation": {
                "uri": finding.file.as_posix() or "unknown",
                "uriBaseId": "%SRCROOT%",
            },
            "region": region,
        }
    }


def _result(finding: Finding, decision: Decision | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "plimsoll-severity": finding.severity.value,
        "source-tool": finding.source.value,
        "tool-rule-id": finding.rule_id,
    }
    if finding.also_reported_by:
        properties["also-reported-by"] = sorted(s.value for s in finding.also_reported_by)
    if decision is not None:
        properties["plimsoll-status"] = decision.status.value
        properties["attempts"] = decision.attempts
        if decision.exception_ref is not None:
            properties["exception-approved-by"] = decision.exception_ref.approved_by
            properties["exception-expired"] = decision.exception_ref.expired

    result: dict[str, Any] = {
        "ruleId": _rule_id(finding),
        "level": level_for(finding.severity),
        "rank": _RANK[finding.severity],
        "message": {"text": _message(finding, decision)},
        "locations": [_location(finding)],
        # `plimsollFingerprint/v1` survives lines moving; code scanning uses it to tell
        # that an alert on line 40 is the one that was on line 38 before an edit.
        "partialFingerprints": {
            "plimsollFindingId": finding.id,
            "plimsollFingerprint/v1": finding.fingerprint,
        },
        "properties": properties,
    }
    if decision is not None and decision.status is DecisionStatus.EXEMPTED:
        # SARIF's own suppression mechanism, so the code-scanning UI closes the alert
        # rather than showing an exempted finding as an open one.
        result["suppressions"] = [
            {
                "kind": "external",
                "justification": decision.exception_ref.justification
                if decision.exception_ref
                else "",
            }
        ]
    return result


def _suppressed_result(suppressed: SuppressedFinding) -> dict[str, Any]:
    """A suppressed finding, still in the log, closed by SARIF's own mechanism."""
    result = _result(suppressed.finding)
    ref = suppressed.exception_ref
    if suppressed.kind is SuppressionKind.EXCEPTION and ref is not None:
        justification = f"{ref.approved_by}: {ref.justification}"
    else:
        justification = "present when the baseline was recorded"
    result["suppressions"] = [{"kind": "external", "justification": justification}]
    if suppressed.kind is SuppressionKind.BASELINE:
        result["baselineState"] = "unchanged"
    result["properties"]["plimsoll-suppressed-by"] = suppressed.kind.value
    return result


def _message(finding: Finding, decision: Decision | None) -> str:
    if decision is None:
        return finding.message
    if decision.status is DecisionStatus.FIXED and decision.patch is not None:
        return (
            f"{finding.message}\n\nA fix was drafted and passed re-verification through the "
            f"same checks that produced this finding (attempt {decision.attempts})."
        )
    if decision.status is DecisionStatus.ESCALATED:
        reason = (
            decision.verification.failure_reason
            if decision.verification and decision.verification.failure_reason
            else "no usable patch was produced"
        )
        return (
            f"{finding.message}\n\nCould not fix after {decision.attempts} attempt(s): {reason}\n"
            "No unverified patch is offered; this needs a human."
        )
    return finding.message


def _notifications(result: GateResult) -> list[dict[str, Any]]:
    """Skipped and failed checks, as SARIF notifications.

    This is the load-bearing part of the file. Without it a run in which every adapter
    was missing produces an empty results list, and an empty results list in the
    code-scanning UI is indistinguishable from a clean workspace.
    """
    notifications: list[dict[str, Any]] = []
    for skipped in result.checks_skipped:
        notifications.append(
            {
                "level": "warning",
                "message": {"text": f"{skipped.name} did not run: {skipped.reason}"},
                "descriptor": {"id": "plimsoll.check.skipped"},
            }
        )
    for failed in result.checks_failed:
        notifications.append(
            {
                "level": "error",
                "message": {"text": f"{failed.name} failed: {failed.error}"},
                "descriptor": {"id": "plimsoll.check.failed"},
            }
        )
    if not result.any_check_ran:
        notifications.append(
            {
                "level": "error",
                "message": {
                    "text": (
                        "No check was able to run, so nothing in this repository was verified. "
                        "An empty result list here does not mean the workspace is clean."
                    )
                },
                "descriptor": {"id": "plimsoll.nothing.verified"},
            }
        )
    if result.timed_out:
        notifications.append(
            {
                "level": "error",
                "message": {
                    "text": (
                        "The run exceeded its budget and stopped early. These results are "
                        "partial and are not a verification."
                    )
                },
                "descriptor": {"id": "plimsoll.timed.out"},
            }
        )
    return notifications


def _invocation(result: GateResult) -> dict[str, Any]:
    successful = result.any_check_ran and not result.checks_failed and not result.timed_out
    invocation: dict[str, Any] = {
        "executionSuccessful": successful,
        "toolExecutionNotifications": _notifications(result),
    }
    if result.timed_out:
        invocation["exitSignalName"] = "TIMEOUT"
    return invocation


def _tool(findings: list[Finding], result: GateResult) -> dict[str, Any]:
    from plimsoll import __version__

    return {
        "driver": {
            "name": "plimsoll",
            "version": __version__,
            "informationUri": INFORMATION_URI,
            "rules": _rules(findings),
            "properties": {
                "checksRan": [t.name for t in result.tooling],
                "toolVersions": {t.name: t.version for t in result.tooling},
            },
        }
    }


def build_sarif(result: GateResult, decisions: list[Decision] | None = None) -> dict[str, Any]:
    """The SARIF log for one run. `decisions` adds verification status to each result."""
    by_id = {d.finding.id: d for d in decisions or []}
    every = result.findings + [s.finding for s in result.suppressed]
    return {
        "$schema": SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": _tool(every, result),
                "invocations": [_invocation(result)],
                "results": [_result(f, by_id.get(f.id)) for f in result.findings]
                + [_suppressed_result(s) for s in result.suppressed],
            }
        ],
    }


def render_sarif(result: GateResult, *, fail_on: Severity) -> str:
    """`fail_on` is unused: SARIF levels come from severity, not from the threshold.

    Kept in the signature so every reporter is called the same way by the CLI.
    """
    return json.dumps(build_sarif(result), indent=2, sort_keys=True)


def render_verify_sarif(result: VerifyResult, *, fail_on: Severity) -> str:
    return json.dumps(build_sarif(result.gate, result.decisions), indent=2, sort_keys=True)
