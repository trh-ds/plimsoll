"""Machine-readable report.

Named `json_report` rather than `json` so the module does not shadow the standard library
for everything else in the package.
"""

from __future__ import annotations

import json
from typing import Any

from plimsoll.core.models import DecisionStatus, GateResult, Severity, VerifyResult


def build_payload(result: GateResult, *, fail_on: Severity) -> dict[str, Any]:
    """The report as plain data. Separated from serialisation so it can be asserted on."""
    blocking = result.findings_at_or_above(fail_on)
    return {
        "fail_on": fail_on.value,
        "timed_out": result.timed_out,
        "summary": {
            "timed_out": result.timed_out,
            "findings": len(result.findings),
            "blocking": len(blocking),
            "checks_ran": [t.name for t in result.tooling],
            "checks_skipped": len(result.checks_skipped),
            "checks_failed": len(result.checks_failed),
        },
        "tooling": [t.model_dump(mode="json") for t in result.tooling],
        "checks_skipped": [s.model_dump(mode="json") for s in result.checks_skipped],
        "checks_failed": [f.model_dump(mode="json") for f in result.checks_failed],
        "findings": [f.model_dump(mode="json") for f in result.findings],
    }


def render_json(result: GateResult, *, fail_on: Severity) -> str:
    return json.dumps(build_payload(result, fail_on=fail_on), indent=2, sort_keys=True)


def build_verify_payload(result: VerifyResult, *, fail_on: Severity) -> dict[str, Any]:
    payload = build_payload(result.gate, fail_on=fail_on)
    # The loop can exhaust the budget after every check finished, so the run's own flag
    # wins over the gate's.
    payload["timed_out"] = result.timed_out
    payload["summary"].update(
        {
            "timed_out": result.timed_out,
            "fixed": len(result.fixed),
            "escalated": len(result.with_status(DecisionStatus.ESCALATED)),
            "exempted": len(result.exempted),
            "expired_exceptions": len(result.expired_exceptions),
            "unresolved_blocking": len(result.unresolved_at_or_above),
        }
    )
    payload["model"] = {
        "used": result.llm_used,
        "description": result.llm_description,
        "unavailable_reason": result.llm_unavailable_reason,
    }
    payload["decisions"] = [d.model_dump(mode="json") for d in result.decisions]
    return payload


def render_verify_json(result: VerifyResult, *, fail_on: Severity) -> str:
    return json.dumps(build_verify_payload(result, fail_on=fail_on), indent=2, sort_keys=True)
