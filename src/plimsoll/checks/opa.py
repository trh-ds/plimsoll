"""Open Policy Agent adapter -- the bundled rule pack.

This is the adapter that carries the project's own opinions. Checkov brings a large
third-party catalogue; the twenty rules in `src/plimsoll/policies/` are the ones
`specifications.md` commits to, written against the documented AI-generated IaC failure
modes and each with a fixture proving it fires and a sibling proving it does not
false-positive.

Two design points worth knowing before editing:

**Rego decides what is a violation; Python decides how severe it is.** The `.rego` files
emit a rule class, a message, and a remediation hint. Severity and benchmark references
come from `checks/rule_map.py`. A severity a rule author can raise inside their own rule
file is a severity nobody reviews, and `--policy-dir` means rule authors are not always
this project.

**Plan JSON has no line numbers.** It describes what will exist, not where it was
written. `checks/locator.py` maps the resource address back to the block that declared
it, which is what makes an OPA finding reportable to a human and patchable by the loop.

`opa` needs a plan, and a plan needs terraform. When there is none, this adapter reports
itself skipped rather than evaluating rules against nothing -- an empty result from an
empty input is indistinguishable from a clean workspace, and that confusion is the exact
failure mode this project exists to prevent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plimsoll.checks.addresses import split_address
from plimsoll.checks.locator import locate_resource
from plimsoll.checks.rule_map import plimsoll_rule
from plimsoll.core.gate import CheckContext, CheckExecutionError, CheckSkipped, register
from plimsoll.core.models import CheckSource, Finding, ToolAvailability

BINARY = "opa"
QUERY = "data.plimsoll.findings"
_VERSION_TIMEOUT_SECONDS = 30

BUNDLED_POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"


def parse_version(stdout: str) -> str | None:
    """`opa version` prints `Version: 0.68.0` followed by build metadata."""
    for line in stdout.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _values(payload: object) -> list[Mapping[str, Any]]:
    """Pull the rule results out of `opa eval --format json`.

    The shape is `{"result": [{"expressions": [{"value": [...]}]}]}`. An undefined query
    -- which is what a clean workspace produces -- emits `{}` with no `result` key at
    all, and that is not an error.
    """
    if not isinstance(payload, Mapping):
        raise CheckExecutionError("opa eval did not return a JSON object")
    results = payload.get("result")
    if results is None:
        return []
    if not isinstance(results, list):
        raise CheckExecutionError("opa eval returned a non-list 'result'")

    out: list[Mapping[str, Any]] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        for expression in result.get("expressions") or []:
            if not isinstance(expression, Mapping):
                continue
            value = expression.get("value")
            if value is None:
                continue
            if not isinstance(value, list):
                raise CheckExecutionError(
                    f"{QUERY} produced {type(value).__name__}, not a list of findings"
                )
            out.extend(entry for entry in value if isinstance(entry, Mapping))
    return out


def parse_opa_output(payload: object, workspace: Path) -> list[Finding]:
    """Map `opa eval` output into findings, resolving each address back to source."""
    findings: list[Finding] = []
    for entry in _values(payload):
        rule_class = str(entry.get("rule_class") or "").strip()
        if not rule_class:
            # A rule that emits no class cannot be deduplicated, exempted, or mapped to a
            # severity. Silently filing it under a placeholder would be worse than
            # failing: the finding would look classified and would not be.
            raise CheckExecutionError(f"{QUERY} produced an entry with no rule_class")

        address = str(entry.get("resource_address") or "")
        rule = plimsoll_rule(rule_class)
        location = locate_resource(workspace, address)
        resource_type = str(entry.get("resource_type") or "")
        if address and not resource_type:
            _, resource_type = split_address(address)

        findings.append(
            Finding(
                source=CheckSource.OPA,
                rule_id=str(entry.get("rule_id") or rule_class),
                rule_class=rule_class,
                severity=rule.severity,
                resource_address=address,
                resource_type=resource_type,
                file=location.file,
                line_start=location.line_start,
                line_end=location.line_end,
                message=str(entry.get("message") or rule_class),
                benchmark_refs=list(rule.benchmark_refs),
                raw=dict(entry),
            )
        )
    return findings


def policy_arguments(policy_dir: Path | None) -> list[str]:
    """`--data` arguments: the bundled pack, plus the user's own directory.

    Both are loaded, never one instead of the other. `--policy-dir` is documented as
    "additional Rego to load alongside the bundled pack"; a flag that silently replaced
    the pack would let a repository disable every rule by pointing at an empty folder.
    """
    args = ["--data", str(BUNDLED_POLICY_DIR)]
    if policy_dir is not None:
        args += ["--data", str(policy_dir)]
    return args


@register("opa")
class OpaCheck:
    name = "opa"

    def is_available(self) -> ToolAvailability:
        executable = shutil.which(BINARY)
        if executable is None:
            return ToolAvailability.missing(BINARY, f"{BINARY} not found on PATH")
        if not BUNDLED_POLICY_DIR.is_dir():  # pragma: no cover - packaging failure
            return ToolAvailability.missing(BINARY, "the bundled rule pack is missing")
        try:
            completed = subprocess.run(
                [executable, "version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ToolAvailability.missing(BINARY, f"could not execute {BINARY}: {exc}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit code {completed.returncode}"
            return ToolAvailability.missing(BINARY, f"{BINARY} version failed: {reason}")
        return ToolAvailability.ok(BINARY, parse_version(completed.stdout))

    def run(self, ctx: CheckContext) -> list[Finding]:
        try:
            plan = ctx.plan_json()
        except CheckExecutionError as exc:
            raise CheckSkipped(f"the plan this adapter needs could not be produced: {exc}") from exc
        if plan is None:
            raise CheckSkipped("no terraform plan is available, so the rule pack was not evaluated")

        executable = shutil.which(BINARY)
        if executable is None:
            raise CheckExecutionError(f"{BINARY} disappeared from PATH between probe and run")

        completed = subprocess.run(
            [
                executable,
                "eval",
                "--format",
                "json",
                *policy_arguments(ctx.config.policy_dir),
                "--input",
                str(plan),
                QUERY,
            ],
            capture_output=True,
            text=True,
            timeout=ctx.deadline.cap(ctx.config.timeout_seconds),
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[:400]
            raise CheckExecutionError(f"{BINARY} eval failed: {detail}")
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            detail = (completed.stderr or completed.stdout).strip()[:300]
            raise CheckExecutionError(f"{BINARY} did not return JSON: {detail}") from exc
        return parse_opa_output(payload, ctx.workspace)
