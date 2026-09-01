# Implementation plan

Six phases, roughly 10 weeks solo at a sustainable pace. Each phase ends with something demonstrable.

The ordering has one non-obvious property: **Phase 0 ships before any of the agent exists.** It is the cheapest artifact in the plan, useful to people who will never adopt the tool, and it produces the baseline number every later claim is measured against. Do not skip it or fold it into a later phase.

---

## Phase 0 — Establish the baseline (week 1)

**Goal:** independently reproduce the published IaC failure rates and publish the numbers.

- [ ] Clone IaC-Eval, read its evaluation methodology, get the dataset loading — not done. A twelve-scenario set was written instead, covering all twenty rule classes; `--scenarios PATH` points the runner at an external dataset and records its digest and commit
- [x] Write a minimal runner: prompt a model per scenario, capture Terraform output
- [x] Score with `terraform validate` and `terraform plan` for deployability
- [x] Add a compliance pass: Checkov and OPA over the generated configs
- [ ] Run three models — two free-tier hosted, one frontier if affordable. No local model: the harness must reproduce on a laptop without a GPU
- [ ] Publish `benchmarks/BASELINE.md` with method, versions, token counts, and results

**Done when:** you can state your own measured compliance pass rate, with a reproducible method, without citing anyone else's paper.

**Status: harness built, nothing measured.** This phase was meant to ship before any of
the agent existed and did not; that ordering is unrecoverable. What is still recoverable
is that the number gets measured before it is claimed, and `BASELINE.md` says so in place
of a figure. **No pass rate goes anywhere until it has been run.**

**Why first:** it is the README's opening paragraph, the benchmark baseline, and proof the problem is real — all before writing a line of the agent. It also tells you early if the published numbers do not reproduce, which would change the project.

---

## Phase 1 — The gate (weeks 2–3)

**Goal:** a working deterministic scanner. No model anywhere.

- [x] Project scaffold: uv, ruff, mypy strict, pytest, CI workflow
- [x] `core/models.py` — Finding, Severity, GateResult, config models
- [x] `Check` protocol and adapter registry with availability probing
- [x] `checks/terraform_validate.py` — parse JSON diagnostics
- [x] `checks/checkov.py` — parse JSON results
- [x] `checks/opa.py` — `opa eval` against plan JSON
- [x] `checks/terraform_plan.py` — plan to JSON, dummy-credential path, LocalStack notes *(the shared plumbing came out into `checks/terraform_cli.py`; two adapters needed one answer about where terraform runs)*
- [x] ~~`rule_classes.yaml`~~ `checks/rule_map.py` — tool rule IDs to internal classes; dedup on `(resource_address, rule_class)` *(a constant dictionary does not need a YAML loader, and a typo in one surfaces at scan time rather than at import)*
- [x] `severity_map.py` — explicit, no inference
- [x] Fixture corpus: one violating and one compliant Terraform config per rule class *(two directories rather than forty — terraform merges every `.tf` in a directory, so both halves plan as a unit and one `opa eval` sees everything)*
- [x] Recorded-output tests for every adapter (no binaries needed in unit CI)
- [x] `plimsoll scan` and `plimsoll doctor`
- [x] Terminal and JSON reporters
- [x] Per-adapter exception isolation — one crashing check never kills the run
- [x] Exit code 3 when no checks are available

**Done when:** `plimsoll scan tests/fixtures/insecure_s3` returns correct, deduplicated, severity-ranked findings, and the suite runs green with no external binaries installed.

**Status: done.** `scan tests/fixtures/insecure_s3` reports eight findings, five at or
above HIGH, each mapped to a file and line, and exits 1. The unit suite is green on a
runner with no terraform, checkov, or opa.

**Not in the plan, needed anyway:** `checks/locator.py`, because plan JSON carries no file
or line and a finding without one is not actionable; and the provider override file,
because the AWS provider will not plan at all until `sts:GetCallerIdentity` succeeds.

**Watch for:** the adapter interface is the load-bearing decision of the whole project. Getting it right makes adding Trivy fifty lines. Getting it wrong means a rewrite at month three. Spend the extra day here.

---

## Phase 2 — The loop (weeks 4–5)

**Goal:** the thing that makes this an agent rather than a linter.

- [x] `LLMProvider` protocol; `null` provider first, so tests exist before real providers
- [x] `llm/openai_compat.py` — one adapter reaches every endpoint that matters, so the planned `ollama` and `anthropic` modules were not needed
- [x] `core/context.py` — HCL block extraction *(rule text, docs cache, and exception precedent wait on the rule pack and the exception store)*
- [x] `llm/prompts/explain.txt`
- [x] `llm/prompts/patch.txt` — whole-file output, strict constraints *(revised from unified diff)*
- [x] Output validation before verification — empty and unchanged rejected, wrapping fence stripped
- [x] `core/verifier.py` — scratch copy, apply, re-gate, classify outcome
- [x] `core/loop.py` — the retry loop, bounded, failure reason fed back into context
- [x] `Decision` construction — assert `FIXED` is unreachable without `verified`
- [x] `core/redaction.py` — **not in the original plan.** Hosted inference makes every prompt an egress point, so this moved forward from Phase 3
- [x] Fake providers returning canned patches: verified, regressed, ineffective, invalid
- [x] Tests for every path: success, regression, ineffective, malformed, retry exhaustion
- [x] Graceful degradation when the provider is unreachable
- [x] `--max-llm-findings` cap, severity-prioritised
- [x] `plimsoll verify` and `plimsoll explain`

**Done when:** given a fixture with a wildcard IAM policy and a hosted model, `plimsoll verify` produces a verified patch — and given a fixture the model cannot fix, produces a clean escalation with no unverified suggestion.

**Status: built, not yet demonstrated.** Every path is tested against scripted providers, and no provider has been run against a live endpoint. The wildcard-IAM half of the exit criterion also needs the Rego pack, since nothing currently detects one.

**Watch for:** the temptation to present a plausible-looking unverified patch because the verified rate feels low. Do not. A low honest number is the product; a high dishonest one destroys the reason anyone would use this.

---

## Phase 3 — Memory and provenance (weeks 6–7)

**Goal:** the two features that separate this from a wrapper.

- [x] `core/exceptions.py` — YAML store, exact rule match, glob address match
- [x] Expiry handling with a distinct `EXPIRED_EXCEPTION` report state
- [x] `plimsoll exceptions` subcommands: list, add, expire, prune
- [ ] Exception precedent flows into `ContextBuilder` — the store exists; the prompt does not yet cite prior accepted exceptions
- [x] `core/redaction.py` — secret patterns, applied before prompts *(pulled into Phase 2: hosted inference made it a prerequisite rather than a Phase 3 nicety)*
- [x] ~~Apply redaction before serialisation too~~ — redaction moved *earlier* instead. `Finding.message` and `Finding.raw` are redacted by the model's validator at construction, so there is no path on which an unredacted finding exists. Doing it at the serialisation boundary would have left the secret in memory and in the terminal report.
- [x] `core/provenance.py` — record assembly, canonical JSON, SHA-256 content hash
- [x] `ToolVersion` capture for every check that ran; `SkippedCheck` for those that did not
- [x] `generated_by` detection from commit trailer, PR label, or flag
- [x] Published JSON schema for the provenance record
- [x] Round-trip tests: serialise, hash, reload, verify hash stability
- [x] Redaction tests with realistic credential shapes
- [x] **Not in the plan:** `core/deadline.py`, the whole-run budget, and exit code 4. `--timeout` meant the per-subprocess value, which is a different promise from the one the CLI contract makes: four adapters and three retries per finding can sit inside every per-check timeout and still take twenty minutes.

**Done when:** a run against a fixture with one accepted exception and three live findings produces a stable, hash-verified provenance record with the exception correctly attributed, and no secret survives redaction.

**Status: done**, except for exception precedent reaching the prompt.

---

## Phase 4 — Ship it (week 8)

**Goal:** someone other than you can install and run it.

- [x] SARIF reporter for GitHub code scanning
- [x] GitHub PR comment reporter — grouped by severity, collapsible diffs, verification status per finding
- [x] `action/Dockerfile` with pinned terraform, checkov, opa
- [x] ~~`action/action.yml`~~ `action.yml`, at the repository root — a container action's build context is the directory holding the metadata, so from `action/` the image could not see `src/`
- [x] ~~Marketplace-ready action *repo*~~ — one repository, not two. `uses: <owner>/plimsoll@v0` resolves to the root metadata, and a second `plimsoll-action` repository would be a synchronisation problem nobody asked for
- [x] PyPI publish workflow — trusted publishing on tag, no stored token
- [x] README quickstart verified — the documented commands were run and their exit codes checked, on the machine this was built on rather than a clean one
- [x] `CONTRIBUTING.md` with the "add a check adapter" walkthrough
- [ ] Three `good first issue` tickets: Trivy adapter, KICS adapter, three additional rules — needs the repository owner
- [x] Dogfood — run Plimsoll against its own fixture corpus in CI as a regression gate

**Done when:** ten lines of workflow YAML in an unrelated repo produce a useful PR comment.

**Status: built, not yet demonstrated.** The workflow snippet is in the README and the
Action's wiring is asserted by `tests/test_action.py`, but the image has never been built
— Docker was unavailable on the machine this was written on. The `action-image` job in
`integration.yml` builds it and drives it against the fixture corpus, and will be the
first thing to have run it. Until that job is green, this phase's exit criterion is
untested rather than met.

---

## Phase 5 — The numbers (weeks 9–10)

**Goal:** the reason people take it seriously.

- [x] Extend the Phase 0 harness to measure post-Plimsoll pass rates — done in the same pass rather than as an extension: both halves are scored by one gate on one workspace, so the two rates cannot drift apart on tool versions
- [ ] Run the full matrix: three or more models, baseline versus post-loop
- [x] Capture patch attempts per finding, escalation rate, wall-clock, token counts *(token counts are estimates; `LLMProvider.complete` does not carry the `usage` block)*
- [x] False-positive measurement against a corpus of known-good Terraform — asserted continuously instead of sampled: `tests/test_rule_corpus.py` fails if any rule fires against the compliant corpus. A third-party known-good corpus would be better and is not built
- [ ] `benchmarks/RESULTS.md` — full method, versions, dataset commit, reproduction instructions — method written, results empty
- [ ] Verify the harness completes on free-tier hosted endpoints — no paid inference, no GPU
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
