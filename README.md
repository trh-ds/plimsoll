# Plimsoll

**A verification agent for AI-generated infrastructure code.**

[![CI](https://github.com/trh-ds/plimsoll/actions/workflows/ci.yml/badge.svg)](https://github.com/trh-ds/plimsoll/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Plimsoll runs in CI between your AI coding agent and your cloud account. It finds policy violations in Terraform, drafts a fix, re-runs that fix through the same deterministic checks, and only surfaces changes that provably pass. Every decision leaves a content-hashed provenance record.

> ### Status: pre-alpha. Not installable yet.
>
> Every command below runs from a checkout today, and the deterministic gate has been run
> end to end against real `terraform`, `opa`, and `checkov` together. There is no release
> and no PyPI package yet, and the reasoning loop has not been benchmarked against a live
> model. What is and is not done is tracked in
> **[Where this actually is](#where-this-actually-is)** and [`CHANGELOG.md`](CHANGELOG.md).

---

## The problem

AI now writes or assists roughly **42% of committed code**, projected to reach 65% by 2027. It is measurably worse at infrastructure code than at application code:

- GPT-4 scored **19.36% pass@1** on Terraform tasks (IaC-Eval, NeurIPS 2024) versus **86.6%** on equivalent Python.
- Six frontier models scored **20.8–30.2% deployment success**, collapsing to **8.4%** once standard compliance checks were applied (DPIaC-Eval, FSE 2026).
- AI-generated deployment infrastructure measured **57.5% vulnerable on average** (IOActive).

The results show up downstream: change failure rates up ~30%, incidents per pull request up 23.5%. And 86% of engineers use these tools while only 6% fully trust the output.

The tooling to fix this half-exists. Policy engines — OPA, Checkov, Trivy — are mature and battle-tested, but they emit a rule ID and stop. Coding agents are mature but unverified. **Nothing open-source sits between them as a closed verify → fix → re-verify → attest loop.** And no tool answers the governance question: *which infrastructure changes were AI-generated, and who approved them?*

## How it works

```
AI agent writes Terraform
        │
        ▼
 [1] Deterministic gate ──── terraform validate · terraform plan · OPA · Checkov
        │  findings, not verdicts
        ▼
 [2] Context retrieval ───── rule source · CIS benchmarks · provider docs · accepted exceptions
        │
        ▼
 [3] Reasoning layer ─────── explains the finding, drafts a minimal patch
        │
        ▼
 [4] Re-verification ─────── the patch goes back through gate [1]
        │                    fails? discard, retry (bounded), then escalate
        ▼
 [5] Provenance record ───── rule · original · corrected · model · approver · content hash
        │
        ▼
 PR comment · merge gate · JSON attestation
```

Stages 1 and 4 are the same code path. The model never judges compliance — `terraform plan` and `opa eval` do, both before and after. The model's only job is translating findings into prose a human can act on, and into a candidate diff.

If the model hallucinates a resource type, scopes an ARN wrong, or fixes one finding while introducing another, stage 4 catches it and the reviewer never sees it.

## Where this actually is

| Piece | State |
|---|---|
| The gate — adapter protocol, registry, availability probing, crash isolation | **Built** |
| All four adapters — `terraform_validate`, `terraform_plan`, `checkov`, `opa` | **Built** |
| Bundled rule pack — 20 Rego rules, with `opa test` cases and a fixture corpus | **Built** |
| Core models — `Finding`, `Patch`, `Decision`, `GateResult`, config | **Built** |
| Cross-tool deduplication | **Built** |
| The reasoning loop and patch re-verification | **Built** |
| Model providers — `openai_compat`, `null` | **Built** |
| Secret redaction, on every path a finding takes | **Built** |
| Exception store, `plimsoll exceptions list \| add \| expire \| prune` | **Built** |
| Provenance records, `--provenance`, published JSON Schema | **Built** |
| Whole-run timeout and exit code 4 | **Built** |
| Terminal, JSON, SARIF, JUnit XML, and PR-comment output | **Built** |
| Baselines for existing repositories, `plimsoll baseline` | **Built** |
| Per-repository config, `.plimsoll/config.yaml` | **Built** |
| `--strict`: fail unless every check ran | **Built** |
| pre-commit hook | **Built**, unreleased |
| GitHub Action | **Built**, unreleased; image built and driven in CI |
| Benchmark harness and scenario set | **Built** |
| Published benchmark numbers | **Not measured** |

The last row is the one that matters. The harness is written and tested; no run has
happened, so there is no pass-rate figure here and there will not be one until
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) has real rows in it. A tool that quotes a
number it has not measured is the thing this project exists to argue against.

Phase by phase, that is [`implementation_plan.md`](implementation_plan.md); what changed
when is [`CHANGELOG.md`](CHANGELOG.md); the running log with the surprises in it is
[`progress.md`](progress.md).

## Quick start

There is no PyPI release yet, so `uv tool install` does not work — run it from a
checkout, as [below](#what-you-can-run-today). The interface itself is real:

```bash
# scan only, no model required
plimsoll scan ./infra

# full loop, against any OpenAI-compatible endpoint
export PLIMSOLL_LLM_BASE_URL=https://your-router.example/v1
export PLIMSOLL_LLM_MODEL=your-model-id
export PLIMSOLL_LLM_API_KEY=...
plimsoll verify ./infra --provenance run.json

# several reports from one run, rather than one run per format
plimsoll scan ./infra --report sarif=plimsoll.sarif --report github=comment.md
```

As a GitHub Action. The model is configured with the same three environment variables, so
a `scan` needs none of them:

```yaml
permissions:
  contents: read
  pull-requests: write   # only for pr-comment
  security-events: write # only for the code-scanning upload

steps:
  - uses: actions/checkout@v4
  - uses: trh-ds/plimsoll@v0
    id: plimsoll
    with:
      path: ./infra
      fail-on: high
      pr-comment: "true"
      github-token: ${{ secrets.GITHUB_TOKEN }}
  - uses: github/codeql-action/upload-sarif@v3
    if: always()
    with:
      sarif_file: ${{ steps.plimsoll.outputs.sarif-report }}
```

Set `command: verify` and the three `PLIMSOLL_LLM_*` variables in the step's `env:` to
run the full loop. Every input mirrors a CLI flag and is named after it; the full list is
in [`action.yml`](action.yml). The image is built on the runner, so the first run of a job
is a few minutes slower than the rest.

Plimsoll is meant to run against Copilot, Claude, Cursor, or human-written Terraform alike, and to work with the OPA and Checkov policies you already have.

### What you can run today

```bash
git clone https://github.com/trh-ds/plimsoll
cd plimsoll
uv sync

uv run plimsoll doctor                            # which tools and which model
uv run plimsoll scan tests/fixtures/insecure_s3   # exit 1: findings at or above HIGH
uv run plimsoll scan tests/fixtures/compliant_s3  # exit 0
uv run pytest -m "not slow"                       # needs no terraform, checkov, or opa
```

`doctor` will tell you which of `terraform`, `opa`, and `checkov` it can find. None of
them is required — a missing one is reported and skipped, and the report says which checks
ran — but with none of them installed there is nothing to verify, and `scan` exits 3
rather than pretending the configuration passed.

The rule pack needs `terraform` and `opa`. A cold `terraform init` downloads the AWS
provider, which on a slow connection can take longer than the 120-second default; raise
`--check-timeout` if you see an init timeout.

The scan is deterministic and needs no model. `verify` adds the loop:

```bash
export PLIMSOLL_LLM_BASE_URL=https://your-router.example/v1
export PLIMSOLL_LLM_MODEL=your-model-id
export PLIMSOLL_LLM_API_KEY=...

uv run plimsoll verify tests/fixtures/insecure_s3
uv run plimsoll explain tests/fixtures/insecure_s3   # prose only, no patches
```

`verify` never writes to your workspace. Patches are applied to a temporary copy and
re-run through the same checks; only fixes that pass are printed.

Shared flags: `--fail-on`, `--format terminal|json|sarif|github|junit`,
`--report FORMAT=PATH`, `--policy-dir`, `--provenance`, `--generated-by`, `--baseline`,
`--strict`, `--config`, `--timeout` (whole run, 600s) and `--check-timeout` (one
subprocess, 120s). `verify` adds `--no-llm`, `--max-retries`, and `--max-llm-findings`.

Exit codes: **0** clean, **1** findings at or above `--fail-on` remain, **2** config
error, **3** no check could run (or, under `--strict`, not every check did), **4** the run
exceeded `--timeout`. With no checker installed it exits 3 and says nothing was verified,
rather than reporting a clean pass; a run that timed out exits 4 and never exits 0,
because partial verification is not verification.

### Adopting it on a repository that already has findings

Three mechanisms, for three different situations. Every report still counts what each one
held back, so suppressed debt stays visible instead of disappearing.

**A baseline** is for the first day. Record what is there, commit the file, and from then
on only what a change *introduces* fails the build:

```bash
plimsoll baseline ./infra                # writes ./infra/.plimsoll/baseline.json
plimsoll scan ./infra --baseline ./infra/.plimsoll/baseline.json
```

Entries key on rule, resource, and file, not line number, so editing around a baselined
resource does not bring it back. A baseline is a snapshot nobody reviewed, so it is never
recorded as an approval in provenance.

**An exception** is for a violation someone has looked at and accepted. It goes in
`.plimsoll/exceptions.yaml` with a justification, an approver, and optionally an expiry,
managed with `plimsoll exceptions list | add | expire | prune`. `scan` and `verify` both
honour it; a matched, live exception is never sent to a model, and an expired one is
reported as lapsed rather than silently re-raised.

**A config file** at `.plimsoll/config.yaml` sets the defaults a repository wants, so CI,
a pre-commit hook, and a terminal judge it the same way. A flag still wins. Unknown keys
are an error, because a misspelt `fail_on` that was silently ignored would leave the gate
at a threshold nobody chose.

```yaml
version: "0.1"
fail_on: medium
baseline: .plimsoll/baseline.json   # paths are relative to the workspace
strict: true
check_timeout: 300
```

### CI and pre-commit

`--format junit` (or `--report junit=plimsoll.xml`) produces a JUnit report for GitLab,
Jenkins, Azure Pipelines, and CircleCI; blocking findings are failures, everything else
passes or is skipped with its reason, and a check that could not run is an error rather
than an empty, green suite. `--format sarif` is for GitHub code scanning; suppressed
findings carry SARIF's own `suppressions`, and results carry a line-independent
fingerprint so alerts do not reopen when unrelated lines move.

`--strict` makes a run exit 3 unless every check ran. Locally a missing `checkov` is
reported and skipped; in CI, where the toolchain is supposed to be complete, it is
usually a broken image. The Action turns it on by default.

As a [pre-commit](https://pre-commit.com) hook, scan only, never the model:

```yaml
- repo: https://github.com/trh-ds/plimsoll
  rev: v0.1.0
  hooks:
    - id: plimsoll-scan
      args: [infra/]
```

## Design commitments

These are the properties the project exists to have. They are enforced as
[invariants](CLAUDE.md#the-invariants) in the code, not as intentions.

- **Model-agnostic.** Any OpenAI-compatible endpoint: an agent router, GitHub Models, Groq, vLLM, LM Studio, or a local Ollama if you want one. Three environment variables, no code change.
- **No GPU required.** Inference is hosted by default. Requiring a local model would make the project unusable on the average laptop, and a contributor who cannot run the tool cannot contribute to it. Free-tier hosted endpoints keep the zero-cost path open without the hardware.
- **Nothing leaves without redaction.** Prompts carry your Terraform to a third party, so every prompt passes through one audited choke point that strips credential-shaped values first. Redaction is reversible, so a patch never overwrites a real secret with a placeholder — and findings themselves are redacted at construction, so a credential a scanner echoed back cannot reach a report either.
- **Degrades safely.** With `--no-llm`, or when the provider is unreachable or unconfigured, Plimsoll is a working policy scanner. It never fails open.
- **Never applies.** `terraform apply` is not called anywhere in this codebase. This is a pre-deployment tool.
- **Never edits your repo.** `verify` copies the workspace, patches the copy, and reports. Your files are not touched — including when terraform needs a `.terraform/` directory, which it gets in a throwaway copy.
- **Remembers exceptions.** A finding accepted in a previous PR is not re-raised. Alert fatigue is the main reason policy-as-code gets switched off. Accepted findings are resolved before any model is consulted, so they cost no tokens and never leave the machine.

## Benchmarks

**The harness is built. Nothing has been measured.**

`benchmarks/runner.py` prompts a model per scenario, scores the raw Terraform with the
gate, then runs the full loop over the same output and scores it again. Both halves in one
pass, both judged by the same gate. It reports baseline pass rate, post-loop pass rate,
the delta, mean patch attempts per finding, and escalation rate, alongside model versions,
tool versions, the scenario set's digest and commit, and usage.

It needs a free-tier hosted endpoint and about ten minutes. No GPU, no paid inference —
that claim is the reason the harness is published and not only the numbers.

[`benchmarks/BASELINE.md`](benchmarks/BASELINE.md) and
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md) are stubs with their method fixed and
their tables empty, and [`RESULTS.md`](benchmarks/RESULTS.md) lists the reasons the
eventual number might be weak — written down before it exists, so it cannot be chosen to
avoid them.

Until those tables have rows, **treat every effectiveness claim in this README as a design
intention rather than a measurement.** There is no pass rate here on purpose.

## Contributing

The most useful contribution is a new `Check` adapter or a policy rule. Both are
self-contained: implement one interface, add a fixture, ship.

The [adapter walkthrough](CONTRIBUTING.md#walkthrough-add-a-check-adapter) is concrete
and the interface it targets is built and tested — [`terraform_validate.py`](src/plimsoll/checks/terraform_validate.py)
is about 150 lines and is the reference implementation. Trivy and KICS are both open.

**No cloud background?** [`ONBOARDING.md`](ONBOARDING.md) starts from what Terraform is
and ends at a list of tasks sorted by how much domain knowledge each needs. Several need
none — the reporters, the CLI, and the deduplication logic are plain Python over data
structures that are already defined and tested.

See [`CONTRIBUTING.md`](CONTRIBUTING.md), the [`good first issue`](https://github.com/trh-ds/plimsoll/labels/good%20first%20issue)
label, and the [Code of Conduct](CODE_OF_CONDUCT.md). Security issues go through
[private reporting](SECURITY.md) rather than the issue tracker.

## Prior art

- [K8sGPT](https://github.com/k8sgpt-ai/k8sgpt), [HolmesGPT](https://github.com/robusta-dev/holmesgpt), [Aurora](https://github.com/aurora-sre/aurora) — excellent, and all *reactive*: they answer "production is broken, why?"
- [TerraFormer (ICSE 2026)](https://arxiv.org/html/2601.08734v1) — proves verifier feedback works for IaC, as a fine-tuning research result rather than a usable tool.
- [awesome-ai-sre](https://github.com/agamm/awesome-ai-sre) — 100+ tools, almost entirely post-incident.

Plimsoll answers a different question: *this change is about to ship — should it?*

## License

[Apache 2.0](LICENSE). Contributions are accepted under the same terms.
