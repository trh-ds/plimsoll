"""The benchmark harness: prompt a model, score what it wrote, then run the loop over it.

This produces the only number that justifies the project, so what it measures matters
more than that it runs:

**Baseline** is the raw model's output, scored by the gate. One scenario passes when no
finding reaches the threshold. That is the "AI-generated Terraform is not compliant"
claim, measured here rather than cited from someone else's paper.

**Post-loop** is the same output after `verify`. One scenario passes when nothing at or
above the threshold is left unresolved. The difference between the two is the headline,
and it is the only thing in this repository entitled to be one.

Three properties hold here because they hold everywhere:

The gate scores both halves. Nothing a model said sets a pass. A scenario passes because
`Gate` found nothing at the threshold, and for no other reason.

Deployability is measured separately from compliance. A model that emits Terraform which
does not parse would otherwise score a perfect compliance rate on configurations that
cannot exist, which is the most flattering possible bug.

Nothing here needs a GPU. It uses the same `LLMConfig` as the tool, so a free-tier hosted
endpoint is enough -- that is the claim in `specifications.md`, and this file is where it
is either true or not.

Usage: see benchmarks/README.md. Run with `--dry-run` to check the wiring without
spending a token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from plimsoll.core.deadline import Deadline
from plimsoll.core.exceptions import ExceptionStore
from plimsoll.core.gate import default_gate
from plimsoll.core.models import (
    CheckConfig,
    DecisionStatus,
    GateConfig,
    GateResult,
    Severity,
)
from plimsoll.llm import LLMConfig, LLMError, LLMProvider, build_provider

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = HERE / "scenarios.yaml"

# Asked for as a system message so the scenario prompts stay in the voice of the person
# making the request. Nothing here mentions a security control -- adding one would
# measure instruction-following rather than default behaviour, and the whole point is
# what a model writes when nobody thinks to ask.
SYSTEM = (
    "You are helping write Terraform for AWS. Reply with the contents of a single .tf "
    "file and nothing else: no explanation, no markdown code fences, no commentary "
    "before or after. Include a provider block and any resources the request needs."
)

_FENCE = re.compile(r"^\s*```(?:[a-zA-Z]*)\n(.*?)\n?```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """Remove a markdown fence wrapping the whole answer.

    The system message forbids fences and models add them anyway. Failing over one would
    score a model as unable to write Terraform when what it could not do is follow a
    formatting instruction, and those are different findings.
    """
    match = _FENCE.match(text.strip())
    return (match.group(1) if match else text).strip() + "\n"


@dataclass
class Scenario:
    id: str
    prompt: str
    expect: list[str] = field(default_factory=list)


def load_scenarios(path: Path) -> tuple[list[Scenario], dict[str, Any]]:
    """Read the scenario file, returning the scenarios and what identifies the set."""
    raw = path.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict) or not isinstance(document.get("scenarios"), list):
        raise SystemExit(f"{path} does not contain a `scenarios:` list")

    scenarios = [
        Scenario(
            id=str(entry["id"]),
            prompt=str(entry["prompt"]).strip(),
            expect=[str(e) for e in entry.get("expect", [])],
        )
        for entry in document["scenarios"]
    ]
    duplicates = {s.id for s in scenarios if [x.id for x in scenarios].count(s.id) > 1}
    if duplicates:
        raise SystemExit(f"duplicate scenario ids in {path}: {sorted(duplicates)}")

    identity = {
        "path": str(path),
        "name": document.get("name", path.stem),
        "count": len(scenarios),
        # Both, because they answer different questions. The digest says whether the file
        # changed; the commit says which version of the repository it came from, and is
        # what someone reproducing the run needs. An external dataset has a commit here
        # and it will not be this repository's.
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "commit": git_commit(path.parent),
    }
    return scenarios, identity


def git_commit(directory: Path) -> str | None:
    """The commit the scenario set came from, or None outside a repository."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


class CountingProvider:
    """Wraps a provider to record how much was sent and returned.

    Character counts, not token counts. The chat-completions `usage` block is not carried
    through `LLMProvider.complete`, which returns text and nothing else -- widening that
    protocol to satisfy a benchmark would put reporting machinery in the one interface
    that must stay small. `estimated_tokens` is characters/4 and is labelled an estimate
    everywhere it is published, because that is what it is.
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.calls = 0
        self.prompt_chars = 0
        self.completion_chars = 0

    def is_available(self):  # noqa: ANN201 - mirrors the protocol it stands in for
        return self._inner.is_available()

    def describe(self) -> str:
        return self._inner.describe()

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        self.prompt_chars += len(prompt) + len(system or "")
        completion = self._inner.complete(prompt, system=system)
        self.completion_chars += len(completion)
        return completion

    @property
    def usage(self) -> dict[str, int]:
        total = self.prompt_chars + self.completion_chars
        return {
            "calls": self.calls,
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "estimated_tokens": total // 4,
        }


def blocking(result: GateResult, fail_on: Severity) -> list[str]:
    return sorted({f.rule_class for f in result.findings_at_or_above(fail_on)})


def deployable(result: GateResult) -> bool:
    """Whether terraform could read the configuration at all.

    Kept apart from compliance because a configuration that does not parse produces no
    findings, and would otherwise score as a compliance pass -- the most flattering
    possible bug in a benchmark like this one.
    """
    if any(f.name.startswith("terraform") for f in result.checks_failed):
        return False
    return not [f for f in result.findings if f.source.value == "terraform_validate"]


def run_scenario(
    scenario: Scenario,
    provider: CountingProvider,
    workspace: Path,
    config: GateConfig,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Generate, score, then run the loop over one scenario."""
    from plimsoll.core.loop import VerifierLoop

    started = time.monotonic()
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        generated = strip_fence(provider.complete(scenario.prompt, system=SYSTEM))
    except LLMError as exc:
        # Recorded and skipped. A router that rate-limits halfway through must not
        # silently reduce the denominator, so this scenario counts as neither a pass nor
        # a failure and the summary says how many there were.
        return {"id": scenario.id, "error": str(exc), "seconds": time.monotonic() - started}

    (workspace / "main.tf").write_text(generated, encoding="utf-8")

    gate = default_gate()
    baseline = gate.run(workspace, config, Deadline(args.timeout))
    baseline_blocking = blocking(baseline, config.fail_on)

    row: dict[str, Any] = {
        "id": scenario.id,
        "expect": scenario.expect,
        "generated_bytes": len(generated),
        "deployable": deployable(baseline),
        "checks_ran": [t.name for t in baseline.tooling],
        "baseline_findings": len(baseline.findings),
        "baseline_blocking": baseline_blocking,
        "baseline_pass": not baseline_blocking,
    }

    if not baseline.any_check_ran:
        # No adapter could start, so this scenario was not scored. Reporting it as a pass
        # would be the exact failure mode the CLI's exit code 3 exists to prevent.
        row["error"] = "no check could run"
        row["seconds"] = time.monotonic() - started
        return row

    loop = VerifierLoop(
        default_gate(),
        config,
        provider,
        max_retries=args.max_retries,
        max_findings=args.max_llm_findings,
        exceptions=ExceptionStore(entries=[]),
    )
    verified = loop.run(workspace, deadline=Deadline(args.timeout))

    attempted = [d for d in verified.decisions if d.attempts > 0]
    row.update(
        {
            "post_blocking": sorted(
                {d.finding.rule_class for d in verified.unresolved_at_or_above}
            ),
            "post_pass": not verified.unresolved_at_or_above,
            "fixed": len(verified.fixed),
            "escalated": len(verified.with_status(DecisionStatus.ESCALATED)),
            "attempts": [d.attempts for d in attempted],
            "timed_out": verified.timed_out,
            "seconds": time.monotonic() - started,
        }
    )
    return row


def summarise(rows: list[dict[str, Any]], usage: dict[str, int]) -> dict[str, Any]:
    """The five columns `specifications.md` asks for, plus what is needed to read them."""
    scored = [r for r in rows if "error" not in r]
    attempts = [a for r in scored for a in r.get("attempts", [])]
    baseline_pass = sum(1 for r in scored if r["baseline_pass"])
    post_pass = sum(1 for r in scored if r.get("post_pass"))
    escalated = sum(r.get("escalated", 0) for r in scored)
    fixed = sum(r.get("fixed", 0) for r in scored)

    def rate(count: int) -> float | None:
        return round(100 * count / len(scored), 1) if scored else None

    return {
        "scenarios": len(rows),
        "scored": len(scored),
        "errors": len(rows) - len(scored),
        "deployable": sum(1 for r in scored if r["deployable"]),
        "baseline_pass": baseline_pass,
        "baseline_pass_rate": rate(baseline_pass),
        "post_pass": post_pass,
        "post_pass_rate": rate(post_pass),
        "delta": (
            None if not scored else round(100 * (post_pass - baseline_pass) / len(scored), 1)
        ),
        "findings_fixed": fixed,
        "findings_escalated": escalated,
        "escalation_rate": (
            None if fixed + escalated == 0 else round(100 * escalated / (fixed + escalated), 1)
        ),
        "mean_attempts_per_finding": (
            None if not attempts else round(statistics.fmean(attempts), 2)
        ),
        "usage": usage,
    }


def markdown_table(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    """One row per model, per the harness contract."""

    def cell(value: object) -> str:
        return "n/a" if value is None else str(value)

    return "\n".join(
        [
            "| Model | Baseline | Post-Plimsoll | Delta | Mean attempts/finding "
            "| Escalation rate |",
            "|---|---|---|---|---|---|",
            f"| `{meta['model']}` "
            f"| {cell(summary['baseline_pass_rate'])}% "
            f"| {cell(summary['post_pass_rate'])}% "
            f"| {cell(summary['delta'])} pts "
            f"| {cell(summary['mean_attempts_per_finding'])} "
            f"| {cell(summary['escalation_rate'])}% |",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--out", type=Path, default=HERE / "runs")
    parser.add_argument("--fail-on", type=Severity, default=Severity.HIGH)
    parser.add_argument("--timeout", type=int, default=900, help="Per-scenario budget, seconds.")
    parser.add_argument("--check-timeout", type=int, default=300)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-llm-findings", type=int, default=25)
    parser.add_argument("--only", action="append", default=[], help="Scenario id; repeatable.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and validate the scenarios, then stop. Spends nothing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenarios, identity = load_scenarios(args.scenarios)
    if args.only:
        scenarios = [s for s in scenarios if s.id in set(args.only)]
        if not scenarios:
            raise SystemExit(f"no scenario matched {args.only}")

    print(f"{identity['name']}: {len(scenarios)} scenario(s) from {args.scenarios}")
    if args.dry_run:
        for scenario in scenarios:
            print(f"  {scenario.id:<28} {len(scenario.prompt)} chars, expects {scenario.expect}")
        return 0

    llm_config = LLMConfig.from_env()
    provider = build_provider(llm_config)
    availability = provider.is_available()
    if not availability.available:
        # Deliberately fatal here, unlike everywhere else in the project. `scan` degrades
        # to deterministic reporting because a scan is still useful without a model; a
        # benchmark of a model without a model is not a benchmark.
        raise SystemExit(f"no model configured: {availability.reason}")

    counting = CountingProvider(provider)
    config = GateConfig(
        fail_on=args.fail_on,
        check=CheckConfig(timeout_seconds=args.check_timeout),
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out / f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]', '_', llm_config.model or 'model')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, scenario in enumerate(scenarios, start=1):
        print(f"[{index}/{len(scenarios)}] {scenario.id} ... ", end="", flush=True)
        row = run_scenario(scenario, counting, run_dir / scenario.id, config, args)
        rows.append(row)
        if "error" in row:
            print(f"skipped: {row['error']}")
        else:
            print(
                f"baseline {'pass' if row['baseline_pass'] else 'fail'}"
                f" -> post {'pass' if row.get('post_pass') else 'fail'}"
                f" ({row['seconds']:.0f}s)"
            )

    summary = summarise(rows, counting.usage)
    meta = {
        "generated": datetime.now(UTC).isoformat(),
        "wall_clock_seconds": round(time.monotonic() - started, 1),
        "model": llm_config.model,
        "provider": llm_config.provider,
        "base_url": llm_config.base_url,
        "temperature": llm_config.temperature,
        "fail_on": args.fail_on.value,
        "max_retries": args.max_retries,
        "dataset": identity,
        "plimsoll_commit": git_commit(HERE),
        "tooling": sorted({t for r in rows for t in r.get("checks_ran", [])}),
    }

    payload = {"meta": meta, "summary": summary, "scenarios": rows}
    (run_dir / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    print()
    print(markdown_table(summary, meta))
    print()
    print(f"{summary['scored']} scored, {summary['errors']} skipped. Full run in {run_dir}")
    if summary["errors"]:
        print("Skipped scenarios are excluded from every rate above, not counted as failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
