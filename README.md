# Plimsoll

**A verification agent for AI-generated infrastructure code.**

Plimsoll runs in CI between your AI coding agent and your cloud account. It finds policy violations in Terraform, drafts a fix, re-runs that fix through the same deterministic checks, and only surfaces changes that provably pass. Every decision leaves a signed provenance record.

> Status: pre-alpha, under active development. Not yet ready for production pipelines.

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

## Quick start

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
- uses: <org>/plimsoll-action@v0
  with:
    path: ./infra
    policy-dir: ./policies
    fail-on: high
    llm-provider: github-models
```

Plimsoll runs against Copilot, Claude, Cursor, or human-written Terraform alike, and works with the OPA and Checkov policies you already have.

## Design commitments

- **Model-agnostic.** Ollama, any OpenAI-compatible endpoint, GitHub Models, Anthropic. Swap providers with one env var.
- **Runs free.** Local models via Ollama work end to end. No paid API key is required to develop, test, or use this.
- **Degrades safely.** With `--no-llm`, or when the provider is unreachable, Plimsoll is a working policy scanner. It never fails open.
- **Never applies.** `terraform apply` is not called anywhere in this codebase. This is a pre-deployment tool.
- **Remembers exceptions.** A finding accepted in a previous PR is not re-raised. Alert fatigue is the main reason policy-as-code gets switched off.

## Benchmarks

`benchmarks/` reproduces IaC-Eval against current models and measures the delta Plimsoll's loop produces. Numbers and methodology are published there. Reproducing them requires no paid inference — the harness runs against local models.

## Contributing

The most useful contribution is a new `Check` adapter or a policy rule. Both are self-contained: implement one interface, add a fixture, ship. See `CONTRIBUTING.md` and the `good first issue` label.

## Prior art

- [K8sGPT](https://github.com/k8sgpt-ai/k8sgpt), [HolmesGPT](https://github.com/robusta-dev/holmesgpt), [Aurora](https://github.com/aurora-sre/aurora) — excellent, and all *reactive*: they answer "production is broken, why?"
- [TerraFormer (ICSE 2026)](https://arxiv.org/html/2601.08734v1) — proves verifier feedback works for IaC, as a fine-tuning research result rather than a usable tool.
- [awesome-ai-sre](https://github.com/agamm/awesome-ai-sre) — 100+ tools, almost entirely post-incident.

Plimsoll answers a different question: *this change is about to ship — should it?*

## License

Apache 2.0
