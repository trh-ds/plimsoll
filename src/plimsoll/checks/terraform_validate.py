"""`terraform validate` adapter.

Catches the failure mode that dominates AI-generated HCL: attributes and resource types
that do not exist. Needs no credentials and touches no cloud.

`terraform validate -json` emits:

    {"format_version": "1.0", "valid": false, "error_count": 1, "warning_count": 0,
     "diagnostics": [{"severity": "error", "summary": ..., "detail": ...,
                      "range": {"filename": ..., "start": {"line": ...}, "end": {...}},
                      "snippet": {"context": 'resource "aws_s3_bucket" "reports"', ...}}]}

Parsing is split from execution so the tests can run the parser against committed JSON
without terraform installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plimsoll.checks.severity_map import terraform_diagnostic_severity
from plimsoll.core.gate import CheckContext, CheckExecutionError, register
from plimsoll.core.models import (
    CheckSource,
    Finding,
    ToolAvailability,
)

BINARY = "terraform"
_VERSION_TIMEOUT_SECONDS = 30

# `snippet.context` looks like: resource "aws_s3_bucket" "reports"
_CONTEXT_RE = re.compile(r'^(?P<kind>resource|data)\s+"(?P<type>[^"]+)"\s+"(?P<name>[^"]+)"')
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Stable identifier fragment from a diagnostic summary."""
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")[:60].rstrip("_")
    return slug or "unknown"


def _address_from_context(context: str | None) -> tuple[str, str]:
    """Derive (resource_address, resource_type) from a snippet context line.

    Returns empty strings for diagnostics that are not attached to a resource or data
    block -- a bad `variable` or `provider` block, or a whole-file parse error.
    """
    if not context:
        return "", ""
    match = _CONTEXT_RE.match(context.strip())
    if match is None:
        return "", ""
    resource_type = match.group("type")
    name = match.group("name")
    if match.group("kind") == "data":
        return f"data.{resource_type}.{name}", resource_type
    return f"{resource_type}.{name}", resource_type


def _address_from_field(address: str) -> tuple[str, str]:
    parts = address.split(".")
    if address.startswith("data.") and len(parts) >= 3:
        return address, parts[1]
    if len(parts) >= 2:
        return address, parts[0]
    return address, ""


def _finding_from_diagnostic(diagnostic: Mapping[str, Any]) -> Finding:
    severity_raw = str(diagnostic.get("severity") or "error")
    summary = str(diagnostic.get("summary") or "").strip() or "terraform validate diagnostic"
    detail = str(diagnostic.get("detail") or "").strip()

    location = diagnostic.get("range") or {}
    start = location.get("start") or {}
    end = location.get("end") or {}
    line_start = int(start.get("line") or 0)
    line_end = int(end.get("line") or line_start)

    address_field = str(diagnostic.get("address") or "")
    if address_field:
        resource_address, resource_type = _address_from_field(address_field)
    else:
        snippet = diagnostic.get("snippet") or {}
        resource_address, resource_type = _address_from_context(snippet.get("context"))

    slug = _slug(summary)
    return Finding(
        source=CheckSource.TERRAFORM_VALIDATE,
        rule_id=f"terraform_validate.{slug}",
        # terraform has no rule IDs, so the class is derived from the diagnostic summary.
        # `rule_classes.yaml` maps tool IDs to classes for the rule-based adapters; this
        # adapter is the one that has nothing to map from.
        rule_class=f"terraform.validate.{slug}",
        severity=terraform_diagnostic_severity(severity_raw),
        resource_address=resource_address,
        resource_type=resource_type,
        file=Path(str(location.get("filename") or "")),
        line_start=line_start,
        line_end=line_end,
        # terraform prints summary and detail together; both are its own words.
        message=f"{summary}\n{detail}" if detail else summary,
        benchmark_refs=[],
        raw=dict(diagnostic),
    )


def parse_validate_output(payload: Mapping[str, Any]) -> list[Finding]:
    """Map `terraform validate -json` output into findings."""
    diagnostics = payload.get("diagnostics") or []
    if not isinstance(diagnostics, list):
        raise CheckExecutionError("terraform validate output has a non-list 'diagnostics'")
    return [_finding_from_diagnostic(d) for d in diagnostics if isinstance(d, Mapping)]


def parse_version(stdout: str) -> str | None:
    """Read the version from `terraform version -json`. None when it cannot be read."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("terraform_version")
    return str(version) if version else None


@register("terraform_validate")
class TerraformValidateCheck:
    name = "terraform_validate"

    def is_available(self) -> ToolAvailability:
        executable = shutil.which(BINARY)
        if executable is None:
            return ToolAvailability.missing(BINARY, f"{BINARY} not found on PATH")
        try:
            completed = subprocess.run(
                [executable, "version", "-json"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ToolAvailability.missing(BINARY, f"could not execute {BINARY}: {exc}")
        # An old terraform without `version -json` still validates; the version is just
        # unknown, which provenance records honestly rather than treating as absence.
        return ToolAvailability.ok(BINARY, parse_version(completed.stdout))

    def run(self, ctx: CheckContext) -> list[Finding]:
        # No plan needed: validate reads the HCL directly, which is why it is the one
        # check that works with no credentials and no provider download.
        executable = shutil.which(BINARY)
        if executable is None:
            raise CheckExecutionError(f"{BINARY} disappeared from PATH between probe and run")
        completed = subprocess.run(
            [executable, "validate", "-json"],
            cwd=ctx.workspace,
            capture_output=True,
            text=True,
            timeout=ctx.config.timeout_seconds,
            check=False,
        )
        # Exit code 1 just means "invalid", which is the interesting case. Only
        # unparseable output is a failure.
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = (completed.stderr or completed.stdout).strip()[:300]
            raise CheckExecutionError(f"{BINARY} validate did not return JSON: {detail}") from exc
        if not isinstance(payload, dict):
            raise CheckExecutionError(f"{BINARY} validate returned a non-object JSON payload")
        return parse_validate_output(payload)
