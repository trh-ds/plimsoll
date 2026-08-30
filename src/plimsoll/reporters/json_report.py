"""Machine-readable report.

Named `json_report` rather than `json` so the module does not shadow the standard library
for everything else in the package.
"""

from __future__ import annotations

import json
from typing import Any

from plimsoll.core.models import GateResult, Severity


def build_payload(result: GateResult, *, fail_on: Severity) -> dict[str, Any]:
    """The report as plain data. Separated from serialisation so it can be asserted on."""
    blocking = result.findings_at_or_above(fail_on)
    return {
        "fail_on": fail_on.value,
        "summary": {
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
