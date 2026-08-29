# Implementation plan

Six phases, roughly 10 weeks solo at a sustainable pace. Each phase ends with something demonstrable.

The ordering has one non-obvious property: **Phase 0 ships before any of the agent exists.** It is the cheapest artifact in the plan, useful to people who will never adopt the tool, and it produces the baseline number every later claim is measured against. Do not skip it or fold it into a later phase.

---

## Phase 0 — Establish the baseline (week 1)

**Goal:** independently reproduce the published IaC failure rates and publish the numbers.

- [ ] Clone IaC-Eval, read its evaluation methodology, get the dataset loading
- [ ] Write a minimal runner: prompt a model per scenario, capture Terraform output
- [ ] Score with `terraform validate` and `terraform plan` for deployability
- [ ] Add a compliance pass: Checkov and OPA over the generated configs
- [ ] Run three models — one local 7B, one free-tier hosted, one frontier if affordable
- [ ] Publish `benchmarks/BASELINE.md` with method, versions, token counts, and results

**Done when:** you can state your own measured compliance pass rate, with a reproducible method, without citing anyone else's paper.

**Why first:** it is the README's opening paragraph, the benchmark baseline, and proof the problem is real — all before writing a line of the agent. It also tells you early if the published numbers do not reproduce, which would change the project.

---

## Phase 1 — The gate (weeks 2–3)

**Goal:** a working deterministic scanner. No model anywhere.

- [ ] Project scaffold: uv, ruff, mypy strict, pytest, CI workflow
- [ ] `core/models.py` — Finding, Severity, GateResult, config models
- [ ] `Check` protocol and adapter registry with availability probing
- [ ] `checks/terraform_validate.py` — parse JSON diagnostics
- [ ] `checks/checkov.py` — parse JSON results
- [ ] `checks/opa.py` — `opa eval` against plan JSON
- [ ] `checks/terraform_plan.py` — plan to JSON, dummy-credential path, LocalStack notes
- [ ] `rule_classes.yaml` — tool rule IDs to internal classes; dedup on `(resource_address, rule_class)`
- [ ] `severity_map.py` — explicit, no inference
- [ ] Fixture corpus: one violating and one compliant Terraform config per rule class
- [ ] Recorded-output tests for every adapter (no binaries needed in unit CI)
- [ ] `plimsoll scan` and `plimsoll doctor`
- [ ] Terminal and JSON reporters
- [ ] Per-adapter exception isolation — one crashing check never kills the run
- [ ] Exit code 3 when no checks are available

**Done when:** `plimsoll scan tests/fixtures/insecure_s3` returns correct, deduplicated, severity-ranked findings, and the suite runs green with no external binaries installed.

**Watch for:** the adapter interface is the load-bearing decision of the whole project. Getting it right makes adding Trivy fifty lines. Getting it wrong means a rewrite at month three. Spend the extra day here.

---

## Phase 2 — The loop (weeks 4–5)

**Goal:** the thing that makes this an agent rather than a linter.

- [ ] `LLMProvider` protocol; `null` provider first, so tests exist before real providers
- [ ] `llm/ollama.py`, `llm/openai_compat.py`, `llm/anthropic.py`
- [ ] `core/context.py` — HCL block extraction, rule text, docs cache, exception precedent
- [ ] `llm/prompts/explain.txt` — validate against a 7B model before a frontier one
- [ ] `llm/prompts/patch.txt` — unified diff output, strict constraints
- [ ] Diff parsing and applicability validation before verification
- [ ] `core/verifier.py` — scratch copy, apply, re-gate, classify outcome
- [ ] `core/loop.py` — the retry loop, bounded, failure reason fed back into context
- [ ] `Decision` construction — assert `FIXED` is unreachable without `verified`
- [ ] Fake providers returning canned patches: verified, regressed, ineffective, invalid
- [ ] Tests for every path: success, regression, ineffective, malformed, retry exhaustion
- [ ] Graceful degradation when the provider is unreachable
- [ ] `--max-llm-findings` cap, severity-prioritised
- [ ] `plimsoll verify` and `plimsoll explain`

**Done when:** given a fixture with a wildcard IAM policy and a local 7B model, `plimsoll verify` produces a verified patch — and given a fixture the model cannot fix, produces a clean escalation with no unverified suggestion.

**Watch for:** the temptation to present a plausible-looking unverified patch because the verified rate feels low. Do not. A low honest number is the product; a high dishonest one destroys the reason anyone would use this.

---

## Phase 3 — Memory and provenance (weeks 6–7)

**Goal:** the two features that separate this from a wrapper.

- [ ] `core/exceptions.py` — YAML store, exact rule match, glob address match
- [ ] Expiry handling with a distinct `EXPIRED_EXCEPTION` report state
- [ ] `plimsoll exceptions` subcommands: list, add, expire, prune
- [ ] Exception precedent flows into `ContextBuilder`
- [ ] `core/redaction.py` — secret patterns, applied before prompts and before serialisation
- [ ] `core/provenance.py` — record assembly, canonical JSON, SHA-256 content hash
- [ ] `ToolVersion` capture for every check that ran; `SkippedCheck` for those that did not
- [ ] `generated_by` detection from commit trailer, PR label, or flag
- [ ] Published JSON schema for the provenance record
- [ ] Round-trip tests: serialise, hash, reload, verify hash stability
- [ ] Redaction tests with realistic credential shapes

**Done when:** a run against a fixture with one accepted exception and three live findings produces a stable, hash-verified provenance record with the exception correctly attributed, and no secret survives redaction.

---

## Phase 4 — Ship it (week 8)

**Goal:** someone other than you can install and run it.

- [ ] SARIF reporter for GitHub code scanning
- [ ] GitHub PR comment reporter — grouped by severity, collapsible diffs, verification status per finding
- [ ] `action/Dockerfile` with pinned terraform, checkov, opa
- [ ] `action/action.yml` with the input surface
- [ ] Marketplace-ready action repo
- [ ] PyPI publish workflow
- [ ] README quickstart verified from a clean machine
- [ ] `CONTRIBUTING.md` with the "add a check adapter" walkthrough
- [ ] Three `good first issue` tickets: Trivy adapter, KICS adapter, three additional rules
- [ ] Dogfood — run Plimsoll against its own fixture corpus in CI as a regression gate

**Done when:** ten lines of workflow YAML in an unrelated repo produce a useful PR comment.

---

## Phase 5 — The numbers (weeks 9–10)

**Goal:** the reason people take it seriously.

- [ ] Extend the Phase 0 harness to measure post-Plimsoll pass rates
- [ ] Run the full matrix: three or more models, baseline versus post-loop
- [ ] Capture patch attempts per finding, escalation rate, wall-clock, token counts
- [ ] False-positive measurement against a corpus of known-good Terraform
- [ ] `benchmarks/RESULTS.md` — full method, versions, dataset commit, reproduction instructions
- [ ] Verify the harness completes with local models only, no paid inference
- [ ] Rewrite the README's opening around the measured delta
- [ ] Announcement: writeup, Show HN, r/devops, CNCF Slack, relevant newsletters

**Done when:** an independent reader can reproduce the headline number from a clean checkout.

**Headline shape:** "Raised frontier-model IaC compliance pass rate from X% to Y%." Not "another Terraform linter."

---

## After v0.1

Ordered by evidence, not appeal.

1. **Sigstore keyless signing** — the `signature` block already exists in the schema.
2. **More adapters** — Trivy, KICS. Contribution-shaped; let others do it.
3. **Kubernetes manifests** — largest adjacent surface, and the adapter interface already fits.
4. **Cross-repo blast radius** — the Project 2 idea, as a feature rather than a separate project. Genuinely unbuilt, but needs org-wide access, so it lands better once the tool is already installed somewhere.
5. **Azure and GCP** — only after AWS is complete, benchmarked, and adopted.

## Sequencing rules

- No phase starts before the previous one's "done when" is actually true.
- Multi-cloud is not a v0.1 decision under any circumstances.
- If a phase runs a week long, cut scope inside it rather than compressing the next one. Phase 5 is the phase that cannot be cut — a shipped tool with no numbers is invisible.
- Every phase ends with something you could show someone. If a phase has no demo, it is scoped wrong.
