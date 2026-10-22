"""`terraform plan` adapter, and the plan provider the `opa` adapter consumes.

Two jobs, one plan run, because a plan is the most expensive thing this tool does.

**As a check**, it reports the diagnostics `validate` cannot reach. `terraform validate`
stops at type checking; `plan` runs the provider's own validation, which is where
"that instance type does not exist in this region" and "this reference cannot be
resolved" surface. On AI-generated configurations that is a large share of the failures.

**As a provider**, it produces the JSON `opa` evaluates Rego against. The plan is
computed once per gate run and shared through `CheckContext`, so `terraform_plan` and
`opa` can never disagree about which plan they judged.

`terraform apply` is not invoked. `plan` is run with `-refresh=false` and
`-lock=false` against a working directory that is not the user's repository, and the
plan file is written to the run's scratch directory. Nothing is created, changed, or
destroyed, and no state file is read or written.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plimsoll.checks import terraform_cli
from plimsoll.checks.addresses import split_address
from plimsoll.checks.severity_map import terraform_diagnostic_severity
from plimsoll.checks.terraform_validate import probe
from plimsoll.core.gate import CheckContext, CheckExecutionError, register
from plimsoll.core.models import CheckSource, Finding, ToolAvailability

PLAN_FILE = "plan.tfplan"
PLAN_JSON = "plan.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")[:60].rstrip("_")
    return slug or "unknown"


def parse_plan_diagnostics(stream: str) -> list[Mapping[str, Any]]:
    """Pull diagnostics out of `terraform plan -json`, which is line-delimited JSON.

    Every line is one message object; only `type == "diagnostic"` carries a finding.
    Lines that are not JSON at all are ignored rather than fatal -- terraform
    occasionally writes a plain warning to the same stream, and losing the whole plan
    over a stray line would be a poor trade.
    """
    out: list[Mapping[str, Any]] = []
    for line in stream.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("type") != "diagnostic":
            continue
        diagnostic = message.get("diagnostic")
        if isinstance(diagnostic, Mapping):
            out.append(diagnostic)
    return out


def _finding_from_diagnostic(diagnostic: Mapping[str, Any]) -> Finding:
    summary = str(diagnostic.get("summary") or "").strip() or "terraform plan diagnostic"
    detail = str(diagnostic.get("detail") or "").strip()
    location = diagnostic.get("range") or {}
    start = location.get("start") or {}
    end = location.get("end") or {}
    line_start = int(start.get("line") or 0)

    address = str(diagnostic.get("address") or "")
    resource_address, resource_type = split_address(address) if address else ("", "")

    slug = _slug(summary)
    return Finding(
        source=CheckSource.TERRAFORM_PLAN,
        rule_id=f"terraform_plan.{slug}",
        rule_class=f"terraform.plan.{slug}",
        severity=terraform_diagnostic_severity(str(diagnostic.get("severity") or "error")),
        resource_address=resource_address,
        resource_type=resource_type,
        file=Path(str(location.get("filename") or "")),
        line_start=line_start,
        line_end=int(end.get("line") or line_start),
        message=f"{summary}\n{detail}" if detail else summary,
        raw=dict(diagnostic),
    )


def parse_plan_output(stream: str) -> list[Finding]:
    """Map the `terraform plan -json` message stream into findings."""
    return [_finding_from_diagnostic(d) for d in parse_plan_diagnostics(stream)]


# The AWS SDK's ways of saying "I do not accept who you are". Matched only on
# diagnostics addressed to a provider block, so a resource that merely mentions a token
# is never mistaken for one.
_CREDENTIAL_FAILURE = re.compile(
    r"InvalidClientTokenId|ExpiredToken|SignatureDoesNotMatch|UnrecognizedClientException"
    r"|No valid credential sources|validating provider credentials",
    re.IGNORECASE,
)


def credential_failure(stream: str) -> str | None:
    """The summary of a provider-level credential rejection in this stream, or None.

    With real-looking credentials in the environment, the override in `terraform_cli`
    is rightly not written, and if AWS rejects them -- expired, revoked, wrong partition
    -- the plan fails before any resource is considered. That is a fact about the runner.
    Reported as a finding, it became a HIGH violation pinned to the `provider` line of a
    configuration that had nothing wrong with it, and the Rego pack quietly skipped.

    Other provider-level diagnostics stay findings: an invalid region or a malformed
    `assume_role` block is the configuration's fault.
    """
    for diagnostic in parse_plan_diagnostics(stream):
        if not str(diagnostic.get("address") or "").startswith("provider["):
            continue
        summary = str(diagnostic.get("summary") or "").strip()
        if _CREDENTIAL_FAILURE.search(f"{summary} {diagnostic.get('detail') or ''}"):
            return summary
    return None


def _credential_failure_message(summary: str) -> str:
    return (
        "AWS rejected the credentials in this environment, so terraform could not plan "
        "and nothing plan-based was verified. This is a problem with the runner, not your "
        "configuration. Fix them, or unset AWS_ACCESS_KEY_ID and AWS_PROFILE so plimsoll "
        f"plans with placeholders and needs no AWS account: {summary[:300]}"
    )


class PlanArtifacts:
    """What one plan run produced. Held in `CheckContext` so it is produced once."""

    __slots__ = ("diagnostics", "plan_json")

    def __init__(self, plan_json: Path | None, diagnostics: str) -> None:
        self.plan_json = plan_json
        self.diagnostics = diagnostics


def _plan(ctx: CheckContext) -> PlanArtifacts:
    """Run plan and show, returning the JSON path and the diagnostic stream.

    A plan that fails is not a crash: an AI-generated configuration that cannot plan is
    exactly the case this tool is for. The diagnostics explaining why become findings,
    and `plan_json` is None so `opa` reports itself skipped rather than evaluating rules
    against nothing.
    """
    plan_file = ctx.scratch / PLAN_FILE
    completed = terraform_cli.run_terraform(
        ctx,
        [
            "plan",
            "-json",
            "-no-color",
            "-input=false",
            "-lock=false",
            "-refresh=false",
            f"-out={plan_file}",
        ],
        dummy_credentials=True,
    )
    diagnostics = completed.stdout or ""
    if completed.returncode != 0 or not plan_file.exists():
        rejected = credential_failure(diagnostics)
        if rejected is not None:
            raise CheckExecutionError(_credential_failure_message(rejected))
        return PlanArtifacts(None, diagnostics)

    shown = terraform_cli.run_terraform(ctx, ["show", "-json", str(plan_file)])
    if shown.returncode != 0:
        detail = (shown.stderr or shown.stdout).strip()[:300]
        raise CheckExecutionError(f"`terraform show -json` failed: {detail}")

    plan_json = ctx.scratch / PLAN_JSON
    plan_json.write_text(shown.stdout, encoding="utf-8")
    return PlanArtifacts(plan_json, diagnostics)


def artifacts(ctx: CheckContext) -> PlanArtifacts:
    """This run's plan artifacts, computed at most once."""
    return ctx.once("terraform_plan", lambda: _plan(ctx))


def plan_provider(ctx: CheckContext) -> Path | None:
    """The `PlanProvider` wired into `default_gate`. None when there is no usable plan."""
    if terraform_cli.executable() is None:
        return None
    return artifacts(ctx).plan_json


@register("terraform_plan")
class TerraformPlanCheck:
    name = "terraform_plan"

    def is_available(self) -> ToolAvailability:
        return probe()

    def run(self, ctx: CheckContext) -> list[Finding]:
        try:
            return parse_plan_output(artifacts(ctx).diagnostics)
        except subprocess.SubprocessError as exc:  # pragma: no cover - defensive
            raise CheckExecutionError(f"terraform plan failed: {exc}") from exc
