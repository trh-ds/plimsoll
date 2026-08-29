"""Explicit severity mapping from source tools to `Severity`.

Every entry is a deliberate table lookup. Severity is never inferred, and never comes
from a model.
"""

from __future__ import annotations

from plimsoll.core.models import Severity

# `terraform validate` diagnostics carry only "error" or "warning".
# An error means the configuration does not parse or does not type-check -- it cannot
# deploy at all, so it ranks with the security rule pack's HIGH tier rather than above it;
# CRITICAL is reserved for the security classes in `specifications.md`.
TERRAFORM_DIAGNOSTIC: dict[str, Severity] = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
}

# Unknown values are not silently downgraded to INFO: a severity a tool grew after this
# table was written should still be visible.
UNKNOWN_SEVERITY = Severity.MEDIUM


def terraform_diagnostic_severity(raw: str) -> Severity:
    return TERRAFORM_DIAGNOSTIC.get(raw.strip().lower(), UNKNOWN_SEVERITY)
