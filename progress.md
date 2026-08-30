# Progress

Running log. Newest entries at the top of each phase. Both humans and coding agents append here.

**Current phase:** Phase 1 — The gate (Phase 0 is being run outside this repo)
**Started:** 2026-08-30
**Last updated:** 2026-08-30

---

## How to log

Append an entry whenever you complete a unit of work, hit something that changes the plan, or make a decision worth remembering. Keep entries short. The value is in the "surprises" and "decisions" lines — anyone can reconstruct what was built from git history, but not why it was built that way.

```markdown
### YYYY-MM-DD — short title

**Done:** what actually shipped
**Surprises:** what you hit that wasn't in the plan
**Decisions:** anything that changes architecture.md, specifications.md, or the plan
**Next:** the immediate next thing
```

If a decision contradicts a doc, update the doc in the same commit. Docs that drift from the code are worse than no docs.

---

## Phase status

| Phase | Status | Target | Actual |
|---|---|---|---|
| 0 — Baseline | Owned outside this repo | Week 1 | — |
| 1 — Gate | In progress | Weeks 2–3 | — |
| 2 — Loop | Not started | Weeks 4–5 | — |
| 3 — Memory & provenance | Not started | Weeks 6–7 | — |
| 4 — Ship | Not started | Week 8 | — |
| 5 — Numbers | Not started | Weeks 9–10 | — |

---

## Phase 0 — Establish the baseline

_No entries yet._

**Exit criterion:** `benchmarks/BASELINE.md` published with an independently measured compliance pass rate, reproducible method, and model versions.

---

## Phase 1 — The gate

### 2026-08-30 — Scaffold, models, gate, and the first adapter

**Done:**
- Docs corrections: Bulwark → Plimsoll throughout (including `PLIMSOLL_*` env vars and
  `.plimsoll/`); tfsec and Terrascan replaced with Trivy and KICS wherever the docs listed
  them as future check adapters. tfsec is deprecated (merged into Trivy, 2023) and
  Terrascan was archived in November 2025.
- Scaffold: `uv`, `pyproject.toml`, ruff lint + format, `mypy --strict` on `src/`, pytest
  with a `slow` marker, GitHub Actions running lint/format/types plus unit tests on
  Python 3.11/3.12/3.13. The unit job installs no external binaries — that is the point.
- `core/models.py` — Severity, CheckSource, ToolAvailability, Finding, ToolVersion,
  SkippedCheck, FailedCheck, CheckConfig, GateConfig, GateResult. Pydantic v2, fully
  annotated.
- `core/gate.py` — the `Check` protocol, the adapter registry, availability probing, and
  per-adapter exception isolation around both the probe and the run.
- `checks/severity_map.py` and `checks/terraform_validate.py` — the first adapter, with
  parsing split from execution so tests never shell out.
- 45 unit tests. ruff, ruff format, mypy strict, and pytest all green with no terraform,
  checkov, or opa on the machine.

**Surprises:**
- `specifications.md` specifies Finding, Patch, PatchVerification, Decision, and
  ProvenanceRecord, but **not** CheckSource, ToolAvailability, GateResult, or the config
  models — those appear only as type names in `architecture.md`. They were designed here
  and should be reviewed as new spec, not treated as transcription.
- `Finding.id` as specified — hash of (source, rule_id, resource_address, file) — collides
  when one tool reports the same rule twice in one file, which `terraform validate` does
  routinely (one diagnostic per bad argument). `line_start` was added to the hash input.
  This is a spec deviation and is marked `SPEC:` in the code.
- `terraform validate` has no rule IDs and no severity vocabulary beyond
  error/warning, so `rule_id` and `rule_class` are derived from a slug of the diagnostic
  summary (`terraform.validate.unsupported_argument`). `rule_classes.yaml` has nothing to
  map for this adapter.
- Diagnostics carry no `resource_address` field in practice; it is recovered from
  `snippet.context` (`resource "aws_s3_bucket" "reports"` → `aws_s3_bucket.reports`).
  File-level diagnostics — "Missing required provider", for instance — have no range and
  no snippet at all, so address and file are empty and the line numbers are 0.
- `Severity` inherits `str`, so `<`/`>` would have compared alphabetically and silently
  ranked MEDIUM above HIGH. Ordering is exposed as `.rank`/`.meets()` instead, never as
  comparison operators.
- **The committed fixtures are reconstructed against terraform's documented `-json`
  schema, not captured from a live run** — no terraform binary was available. They need
  re-capturing before this phase is called done. See
  `tests/fixtures/terraform_validate/README.md`.

**Decisions:**
- Dedup on `(resource_address, rule_class)` is not implemented yet. With one adapter there
  is nothing to deduplicate against and no way to test it honestly; it lands with the
  second adapter.
- A check that ran and then crashed is recorded in `checks_failed` and **not** in
  `tooling`, so it cannot count toward "something was verified" for exit code 3.
- Adding an adapter is one module plus one fixture plus one `CheckSource` member — the
  enum is a closed set per the spec, so `architecture.md`'s "one file plus one fixture" is
  one line short.

**Next:** review the `Check` interface before checkov, opa, and terraform_plan are written
against it.

**Exit criterion:** `plimsoll scan` returns correct, deduplicated, severity-ranked findings on the fixture corpus, with the suite green and no external binaries installed.

### 2026-08-30 — Open-source documentation set

**Done:** the repo now has the files a stranger needs before they can contribute — `LICENSE` (Apache 2.0, copyright held by "Plimsoll contributors" so no CLA is needed), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CHANGELOG.md` (Keep a Changelog), two GitHub issue forms, and a PR template. `README.md` was corrected against what is actually built.

**Surprises:**

- The README was written in the present tense about a tool that does not exist yet. Read cold at commit seven it claims a working CLI, a GitHub Action, and published benchmarks. Fixed with a "Where this actually is" table and a status banner rather than by deleting the design description — the design is the point of the README, it just needed to stop pretending to be a changelog.
- "Signed provenance record" was in the README opening paragraph. Nothing in `specifications.md` signs anything; the design is a content hash. Corrected to "content-hashed" — the difference matters if anyone evaluates this for supply-chain use.
- `ruff format` reformats Python blocks inside Markdown, so `CONTRIBUTING.md` failed `ruff format --check` on the code snippets in the adapter walkthrough. Applied the formatter rather than excluding `.md`; it keeps the snippets consistent with the code they describe.

**Decisions:**

- `CONTRIBUTING.md` leads with the adapter walkthrough because the `Check` interface is the only thing built well enough for someone to finish a contribution against today. The policy-rule walkthrough is marked as not yet possible instead of being written speculatively.
- `SECURITY.md` defines a vulnerability in terms of the invariants: a silently dropped finding, an unverified patch reaching a human, a model setting `Decision.status`, a secret surviving redaction. None of those look like security bugs, and all of them are.
- Security reports go through GitHub private advisories only. No email address is published anywhere in the repo.
- The PR template's invariant checklist says that breaking an invariant opens a design conversation rather than an automatic rejection. Invariants that cannot be argued with get worked around silently instead.

**Unblocked:** the repo can be made public. The adapter walkthrough gives a `good first issue` shape (Trivy, KICS) that does not require the CLI, the loop, or a model to exist.

**Still open:** the `Check` interface review. No further adapters until that happens.

---

## Phase 2 — The loop

_Not started._

**Exit criterion:** a verified patch produced against a real fixture with a local 7B model, and a clean escalation on a fixture the model cannot fix.

---

## Phase 3 — Memory and provenance

_Not started._

**Exit criterion:** stable hash-verified provenance record, exception correctly attributed, no secret surviving redaction.

---

## Phase 4 — Ship

_Not started._

**Exit criterion:** ten lines of workflow YAML in an unrelated repo produce a useful PR comment.

---

## Phase 5 — The numbers

_Not started._

**Exit criterion:** an independent reader reproduces the headline delta from a clean checkout.

---

## Open questions

Move these into decisions as they resolve, with the reasoning.

- [ ] Does `terraform plan` work reliably enough with dummy credentials for greenfield AWS configs, or is LocalStack required in the default dev path?
- [ ] Is Checkov better as a subprocess or a direct import? Subprocess is the current call for version independence — revisit if parsing proves fragile.
- [ ] What is the realistic verified-patch rate for a 7B local model? This determines whether the "runs free" claim survives contact with reality, and it should be measured in Phase 2, not assumed.
- [ ] How reliably can `generated_by` be detected without a flag? Commit trailers and PR labels are both weak signals.
- [ ] Does the dedup key `(resource_address, rule_class)` hold up when Checkov and OPA disagree on severity for the same violation?

---

## Decisions log

Architectural decisions and their reasoning. Append-only — supersede rather than edit.

### 2026-08-30 — Docs scaffold established

**Decision:** Python + uv + Pydantic + Typer; subprocess adapters; no LLM framework; file-backed exceptions; no backend.
**Reasoning:** see `tech-stack.md`. Optimising for solo velocity and zero operating cost.
**Supersedes:** nothing.

### 2026-08-30 — Benchmark before agent

**Decision:** Phase 0 ships a standalone IaC-Eval baseline before any agent code exists.
**Reasoning:** cheapest artifact in the plan, useful independently of the tool, and it provides the number every later claim is measured against. It also surfaces early if the published failure rates do not reproduce.
**Supersedes:** nothing.

---

## Blocked

_Nothing blocked._
