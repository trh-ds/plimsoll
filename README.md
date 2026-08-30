# Plimsoll

**A verification agent for AI-generated infrastructure code.**

[![CI](https://github.com/trh-ds/plimsoll/actions/workflows/ci.yml/badge.svg)](https://github.com/trh-ds/plimsoll/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Plimsoll runs in CI between your AI coding agent and your cloud account. It finds policy violations in Terraform, drafts a fix, re-runs that fix through the same deterministic checks, and only surfaces changes that provably pass. Every decision leaves a content-hashed provenance record.

> ### Status: pre-alpha. Not installable yet.
>
> This README describes the design. Some of it is built and some of it is not, and the
> difference is tracked honestly in **[Where this actually is](#where-this-actually-is)**
> below and in [`CHANGELOG.md`](CHANGELOG.md). There is no release, no PyPI package, and
> no working CLI yet.
>
> The [interface for adding a check adapter](CONTRIBUTING.md#walkthrough-add-a-check-adapter)
> *is* built and tested, so that contribution is available today.

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
| `terraform_validate` and `checkov` adapters | **Built** |
| Core models — `Finding`, `Severity`, `GateResult`, config | **Built** |
| Cross-tool deduplication | **Built** |
| `plimsoll scan` and `plimsoll doctor`, terminal and JSON output | **Built** |
| `opa` and `terraform_plan` adapters | Next |
| Bundled rule pack (20 Rego rules) | Not started |
| CLI — `verify`, `explain`, `exceptions` | Not started |
| The reasoning loop and patch re-verification | Not started |
| Exception store and provenance records | Not started |
| GitHub Action and PR comments | Not started |
| Published benchmark numbers | Not started |

Phase by phase, that is [`implementation_plan.md`](implementation_plan.md); what changed
when is [`CHANGELOG.md`](CHANGELOG.md); the running log with the surprises in it is
[`progress.md`](progress.md).

## Quick start

**None of this works yet** — it is the v0.1 interface, specified in
[`specifications.md`](specifications.md) and written down here so it can be argued with
before it is built.

```bash
uv tool install plimsoll

# scan only, no model required
plimsoll scan ./infra

# full loop
export PLIMSOLL_LLM_PROVIDER=ollama
export PLIMSOLL_LLM_MODEL=qwen2.5-coder:7b
plimsoll verify ./infra --provenance out/attestation.json
```

As a GitHub Action:

```yaml
- uses: trh-ds/plimsoll-action@v0
  with:
    path: ./infra
    policy-dir: ./policies
    fail-on: high
    llm-provider: github-models
```

Plimsoll is meant to run against Copilot, Claude, Cursor, or human-written Terraform alike, and to work with the OPA and Checkov policies you already have.

### What you can run today

The deterministic scan works, against real Checkov, with real exit codes.

```bash
git clone https://github.com/trh-ds/plimsoll
cd plimsoll
uv sync

uv run plimsoll doctor                            # which tools are installed
uv run plimsoll scan tests/fixtures/insecure_s3   # exit 1: findings at or above HIGH
uv run plimsoll scan tests/fixtures/compliant_s3  # exit 0
uv run pytest                                     # needs no terraform, checkov, or opa
```

`scan` supports `--fail-on`, `--format terminal|json`, `--policy-dir`, and `--timeout`.
With no checker installed it exits **3** and says nothing was verified, rather than
reporting a clean pass.

## Design commitments

These are the properties the project exists to have. They are enforced as
[invariants](CLAUDE.md#the-invariants) in the code, not as intentions.

- **Model-agnostic.** Ollama, any OpenAI-compatible endpoint, GitHub Models, Anthropic. Swap providers with one env var.
- **Runs free.** Local models via Ollama work end to end. No paid API key is required to develop, test, or use this.
- **Degrades safely.** With `--no-llm`, or when the provider is unreachable, Plimsoll is a working policy scanner. It never fails open.
- **Never applies.** `terraform apply` is not called anywhere in this codebase. This is a pre-deployment tool.
- **Remembers exceptions.** A finding accepted in a previous PR is not re-raised. Alert fatigue is the main reason policy-as-code gets switched off.

## Benchmarks

*Planned, not yet published.* `benchmarks/` will reproduce IaC-Eval against current
models and measure the delta Plimsoll's loop produces, with method, model versions,
dataset commit, and token counts alongside. Reproducing it is meant to require no paid
inference — the harness runs against local models, and that claim is the whole point of
publishing the harness rather than only the numbers.

Until those numbers exist, treat every effectiveness claim in this README as a design
intention rather than a measurement.

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
