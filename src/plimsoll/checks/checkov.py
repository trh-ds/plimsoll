"""Checkov adapter.

The broadest rule coverage of the four v0.1 checks, and the one that needs no
credentials, no provider download, and no plan.

`checkov -o json` emits one of three shapes, all of which appear in the committed
fixtures:

1. A single object with `check_type` and `results` -- one framework produced findings.
2. A **list** of those objects -- more than one framework did. Any repository with a
   hardcoded credential hits this, because the `secrets` framework runs alongside
   `terraform` by default. A parser written against shape 1 alone crashes on the first
   real repository it sees.
3. A bare summary object with no `results` key at all -- nothing was found.

Two flags are load-bearing rather than cosmetic:

`--compact` drops the `code_block` field. That field contains the offending source lines,
which for a hardcoded-credential finding is the credential itself. `Finding.raw` flows
into provenance records and into model prompts, so keeping it out at the source is
cheaper and more reliable than redacting it later.

`--quiet` drops `passed_checks`. A clean scan of a medium repository otherwise emits
megabytes of passes nobody reads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from plimsoll.checks.addresses import split_address
from plimsoll.checks.rule_map import checkov_mapping
from plimsoll.core.gate import CheckContext, CheckExecutionError, register
from plimsoll.core.models import (
    CheckSource,
    Finding,
    ToolAvailability,
)

BINARY = "checkov"
_VERSION_TIMEOUT_SECONDS = 60
_TERRAFORM_FRAMEWORKS = frozenset({"terraform", "terraform_plan"})


def _relative(file_path: str) -> Path:
    """Normalise checkov's file path, which is anchored to the scanned directory.

    It arrives as `/main.tf` on posix and `\\main.tf` on Windows -- absolute-looking but
    meant as relative. Left alone, `Path("/main.tf")` reads as filesystem root.
    """
    return Path(file_path.lstrip("/\\").replace("\\", "/"))


def _line_range(entry: Mapping[str, Any]) -> tuple[int, int]:
    raw = entry.get("file_line_range") or []
    if not isinstance(raw, Sequence) or len(raw) < 2:
        return 0, 0
    try:
        return int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return 0, 0


def _address(entry: Mapping[str, Any], check_type: str) -> tuple[str, str]:
    """Resource address and type, or empty strings when the finding has neither.

    The `secrets` framework reuses the `resource` field for a SHA-1 of the matched
    string. That is an identifier, not an address, and putting it where an address
    belongs would feed nonsense to the reporter and the context builder.
    """
    resource = str(entry.get("resource") or "")
    if check_type not in _TERRAFORM_FRAMEWORKS or "." not in resource:
        return "", ""
    return split_address(resource)


def _finding_from_check(entry: Mapping[str, Any], check_type: str) -> Finding:
    check_id = str(entry.get("check_id") or "").strip() or "CKV_UNKNOWN"
    mapping = checkov_mapping(check_id)
    resource_address, resource_type = _address(entry, check_type)
    line_start, line_end = _line_range(entry)
    guideline = str(entry.get("guideline") or "").strip()

    return Finding(
        source=CheckSource.CHECKOV,
        rule_id=check_id,
        rule_class=mapping.rule_class,
        # Never from entry["severity"] -- the community edition always reports null.
        severity=mapping.severity,
        resource_address=resource_address,
        resource_type=resource_type,
        file=_relative(str(entry.get("file_path") or "")),
        line_start=line_start,
        line_end=line_end,
        message=str(entry.get("check_name") or check_id),
        benchmark_refs=[guideline] if guideline else [],
        raw=dict(entry),
    )


def _result_blocks(payload: object) -> list[Mapping[str, Any]]:
    """Flatten checkov's output into the per-framework blocks that carry results."""
    blocks = payload if isinstance(payload, list) else [payload]
    out: list[Mapping[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            raise CheckExecutionError("checkov output contained a non-object result block")
        # A run that found nothing emits only the summary, with no `results` key.
        if isinstance(block.get("results"), Mapping):
            out.append(block)
    return out


def parse_checkov_output(payload: object) -> list[Finding]:
    """Map `checkov -o json` output into findings."""
    findings: list[Finding] = []
    for block in _result_blocks(payload):
        check_type = str(block.get("check_type") or "")
        results = block["results"]
        failed = results.get("failed_checks") or []
        if not isinstance(failed, list):
            raise CheckExecutionError("checkov results has a non-list 'failed_checks'")
        findings.extend(
            _finding_from_check(entry, check_type) for entry in failed if isinstance(entry, Mapping)
        )
    return findings


def parse_version(stdout: str) -> str | None:
    """`checkov --version` prints a bare version string."""
    version = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    return version or None


@register("checkov")
class CheckovCheck:
    name = "checkov"

    def is_available(self) -> ToolAvailability:
        executable = shutil.which(BINARY)
        if executable is None:
            return ToolAvailability.missing(BINARY, f"{BINARY} not found on PATH")
        try:
            completed = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ToolAvailability.missing(BINARY, f"could not execute {BINARY}: {exc}")
        if completed.returncode != 0:
            # A broken console script -- an entry point pointing at an interpreter that
            # no longer has the package -- is on PATH but cannot run. Unavailable, not
            # a crash midway through the scan.
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit code {completed.returncode}"
            return ToolAvailability.missing(BINARY, f"{BINARY} --version failed: {reason}")
        return ToolAvailability.ok(BINARY, parse_version(completed.stdout))

    def run(self, ctx: CheckContext) -> list[Finding]:
        executable = shutil.which(BINARY)
        if executable is None:
            raise CheckExecutionError(f"{BINARY} disappeared from PATH between probe and run")
        completed = subprocess.run(
            [executable, "--directory", ".", "--output", "json", "--compact", "--quiet"],
            cwd=ctx.workspace,
            capture_output=True,
            text=True,
            timeout=ctx.config.timeout_seconds,
            check=False,
        )
        # Exit code 1 means "found something", which is the interesting case.
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = (completed.stderr or completed.stdout).strip()[:300]
            raise CheckExecutionError(f"{BINARY} did not return JSON: {detail}") from exc
        return parse_checkov_output(payload)
