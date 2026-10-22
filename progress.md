# Progress

Running log. Newest entries at the top of each phase. Both humans and coding agents append here.

**Current phase:** Phase 1 — The gate (Phase 0 is being run outside this repo)
**Started:** 2026-08-30
**Last updated:** 2026-10-22

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
| 0 — Baseline | Harness built, nothing measured | Week 1 | 2026-09-01 (partial) |
| 1 — Gate | Done | Weeks 2–3 | 2026-09-01 |
| 2 — Loop | Built, not demonstrated against a live model | Weeks 4–5 | 2026-08-31 |
| 3 — Memory & provenance | Done | Weeks 6–7 | 2026-09-01 |
| 4 — Ship | Built, image never built | Week 8 | 2026-09-01 |
| 5 — Numbers | Harness built, nothing measured | Weeks 9–10 | — |

Four of the six say "built" and not "demonstrated". That is the honest state: the code is
written and tested, and three things have never been exercised for real — a live model
endpoint, the Action's container image, and the benchmark. Each is named in its phase
below.

---

## Phase 0 — Establish the baseline

### 2026-09-01 — The harness, without the numbers

**Done:** `benchmarks/runner.py`, a twelve-scenario set covering all twenty rule classes,
and stub `BASELINE.md` / `RESULTS.md`. It prompts a model per scenario, scores the raw
output with the gate, then runs the full loop over the same output and scores it again —
both halves in one pass, so the two rates cannot drift apart on tool versions. Nineteen
tests cover the scenario set, the fence stripping, the usage accounting, and the
arithmetic.

**Surprises:** Phase 0 was supposed to come first and did not, so the harness was written
against a finished gate rather than the other way round. That turned out to help in one
way — `summarise` could be tested against real `Decision` shapes rather than imagined ones
— and to cost in another: the scenario set was written by the person who wrote the rule
pack, so it probes this pack's opinions rather than an independent standard. That is
written down in `RESULTS.md` under "what would make this claim weak", before any number
exists to be defended.

Deployability had to be scored separately from compliance. Terraform that does not parse
produces no findings, so a model that emits garbage would otherwise score a perfect
compliance rate.

**Decisions:** token counts are estimates. `LLMProvider.complete` returns text and does
not carry the chat-completions `usage` block, and widening that protocol to serve a
benchmark would put reporting machinery in the one interface that has to stay small. The
harness counts characters exactly and publishes characters/4, labelled as an estimate.

**Next:** run it. It needs a free-tier endpoint and about ten minutes, and until it has
been run, no pass-rate figure belongs anywhere in this repository.

**Exit criterion:** `benchmarks/BASELINE.md` published with an independently measured compliance pass rate, reproducible method, and model versions. **Not met.**

---

## Phase 1 — The gate

### 2026-09-01 — The rest of the Rego unit tests, and the retry that did not fire

**What changed:** `iam_test.rego`, `s3_test.rego`, and `hygiene_test.rego` — 61 `opa test`
cases where there were 19, covering every rule class the pack ships. `terraform_cli` now
retries `terraform init` on a transport failure and tells a network problem apart from a
configuration one.

**Now unblocked:** a rule can be developed in a second instead of thirty. The S3 file is
the one worth reading first: four of the five rules are *absence* rules decided from the
`configuration` section rather than planned values, so every case has to carry both halves
of the plan document. A test written against planned values alone would assert nothing
about the rule that ships.

**Surprise — the exact-set assertions collided with an account-level rule.**
`logging.cloudtrail.absent` fires on any configuration holding an IAM or S3 resource,
which is every input in two of the three files. Subtracting it there and asserting it
directly in `hygiene_test.rego` keeps the exact-set comparisons meaning what they say.

**Surprise — the init retry did not fire on the failure it was written for.** The known
issue was a timeout to `releases.hashicorp.com`; running the documented smoke test
reproduced it, and the classifier missed it twice over. Terraform hard-wraps its error
output, so the real message had a newline between "read" and "tcp" and a pattern written
with literal spaces matched nothing. And the failure surfaced as `wsarecv: A connection
attempt failed`, which is the Windows socket layer's wording passed through by Go's net
package — none of the Linux phrasings matched, so *every* dropped connection on Windows
was being classified as a configuration error and told the user to go read their
Terraform. The real output is now a test fixture, wrapping included.

**Decision — retry only what is worth retrying.** A malformed `required_providers` block
fails identically every time; retrying it spends the run budget three times over to print
the same message. It raises on the first attempt. The backoff also never sleeps past the
run deadline: waiting to retry something the run will not have time to use trades one
useless failure for a slower one.

### 2026-09-01 — The rule-pack corpus, and the false positive it found

**What changed:** `tests/fixtures/rules/violating/` and `tests/fixtures/rules/compliant/` — one Terraform workspace each, five files by rule group; `tests/test_rule_corpus.py`, marked slow, asserting that every `rule_class` in `PLIMSOLL` fires against the first and none fires against the second; `policies/datastore_test.rego` and `policies/network_test.rego`, run by `opa test`; and a CloudTrail added to `tests/fixtures/compliant_s3`, which was not compliant.

**Now unblocked:** adding a rule. `test_every_rule_class_in_the_pack_fires` fails when someone adds a row to `PLIMSOLL` and no fixture for it, which is the failure worth catching.

**Decision — two directories, not forty.** Terraform merges every `.tf` in a directory into one configuration, so each side plans as a unit and one `opa eval` sees all twenty classes. Forty directories would be forty provider blocks, forty `terraform init` runs, and forty places for a rule rename to be half-applied. `set(PLIMSOLL) - fired == set()` is also a stronger claim than forty independent tests.

**Surprise — the pack had a false positive, and the compliant half found it on its first run.** The AWS provider types `encrypted` as a **string** on a launch template's block device, so plan JSON carries `"true"`, not `true`. `ebs.encryption.missing` compared to `true` and reported every correctly-encrypted launch template as unencrypted. Fixed with `device_encrypted`, which accepts both, and pinned by a regression test in `datastore_test.rego`. This is the entire argument for keeping a compliant corpus: a rule that fires is easy to write, and a rule that fires only when it should is the one a team leaves switched on.

**Surprise — `tests/fixtures/compliant_s3` was not compliant.** It tripped `logging.cloudtrail.absent`. At MEDIUM that sat below the default threshold, so the documented smoke test still exited 0 and nothing looked wrong. A fixture named "compliant" that is not is worse than no fixture, because it is what everyone reads to learn what compliant means.

**Decision — the Rego unit tests are hand-built inputs, and that is only safe here.** They assert what a rule does with a shape. The adapter fixtures assert what shape a tool actually emits, and only the second can be got wrong by writing it from documentation. `_test.rego` files are excluded from the wheel: `opa eval --data <pack>` would load them on every user's scan, and a shipped test package is something somebody will eventually mistake for a rule.

### 2026-09-01 — Recorded fixtures and tests for the plan and OPA path

**What changed:** `tests/fixtures/opa/` and `tests/fixtures/terraform_plan/`, both captured from a real terraform 1.15.8 / opa 1.20.1 / hashicorp-aws 5.100.0 pipeline with the commands and versions recorded in a README beside them; `tests/test_opa.py`, `tests/test_terraform_plan.py`, `tests/test_locator.py`, `tests/test_terraform_cli.py`; and two more redaction tests. 325 unit tests, up from 263.

**Now unblocked:** the largest gap in the tree is closed. Everything that landed on 2026-09-01 was exercised end to end by hand and by nothing else.

**Surprise — a clean OPA run does not produce an undefined query.** The adapter's comment said `{}` with no `result` key was what a clean workspace looked like. It is not: `findings` is a partial set rule, always defined, so a clean run gives `"value": []`. The bare `{}` happens when the rule package was never loaded — a broken install, and the one case that must *not* read as "no findings". Both shapes are now recorded fixtures and the comment says what is true.

**Confirmation — the plan diagnostic `snippet` carries the offending source line.** For a hardcoded-credential finding that line *is* the credential, and the adapter puts the whole diagnostic into `Finding.raw`. This is the concrete case that redaction-at-construction exists for, and there is now a test that puts a credential through it.

**Surprise — the `compliant_s3` fixture is not compliant.** It trips `logging.cloudtrail.absent` at MEDIUM, so `scan` still exits 0 under the default `--fail-on high` and the smoke test in `CLAUDE.md` still passes. It is a genuine gap in the fixture, not the rule, and it is what the rule corpus in the next unit of work has to fix.

### 2026-09-01 — The plan path, the OPA adapter, and the twenty-rule pack

**What changed:** `core/deadline.py`, a `CheckContext` with a scratch directory and `once()` memoisation, `checks/terraform_cli.py`, `checks/terraform_plan.py`, `checks/opa.py`, `checks/locator.py`, the `PLIMSOLL` table in `checks/rule_map.py`, and the six-file Rego pack in `src/plimsoll/policies/`. Verified end to end against `tests/fixtures/insecure_s3`: init → plan → show → `opa eval` → rule pack → locator → terminal report → exit 1, eight findings, five at or above HIGH, each mapped to the right file and line.

**Now unblocked:** the loop finally has something to reason about. Until this landed, findings came from `checkov` and `terraform validate` only, and the verified-fix rate could not be measured at all.

**Decision — the rule pack lives in `src/plimsoll/policies/`, not a top-level `policies/`.** `CLAUDE.md` and `implementation_plan.md` both said top-level. A pack that does not ship inside the wheel is not bundled; a user who `uv tool install plimsoll` would get an adapter with no rules and a clean report on a broken workspace, which is the exact failure this project exists to prevent. `--policy-dir` still loads a user's own directory *alongside* the pack, never instead of it.

**Decision — Rego says what is a violation, Python says how severe it is.** The `.rego` files emit a rule class, a message, and a remediation hint; `rule_map.PLIMSOLL` holds severity and benchmark references. A severity a rule author can raise inside their own rule file is a severity nobody reviews, and `--policy-dir` means rule authors are not always this project.

**Surprise — plan JSON has no filenames and no line numbers.** It describes what will exist, not where it was written, which is right for evaluating policy and useless for reporting to a human or building a patch prompt. `checks/locator.py` maps addresses back to block headers by text search. A miss degrades to a file-level finding on a stably-sorted first file, which is also what makes `logging.cloudtrail.absent` — a rule about something the configuration does *not* contain — reportable at all.

**Surprise — the AWS provider calls `sts:GetCallerIdentity` before it will plan anything.** Placeholder credentials are not enough: that call returns `InvalidClientTokenId` and the whole plan fails, which would have been reported as a finding about the user's configuration when it is really a fact about this machine having no AWS account. `terraform_cli.CREDENTIAL_OVERRIDE` writes a three-argument `provider "aws"` override into the throwaway copy, only when there are no real credentials *and* an un-aliased default provider block exists to merge into.

**Surprise — `Finding.raw` is a secret-leak path that redaction-before-serialisation does not close.** `raw` holds whatever the tool chose to include, which for a hardcoded-credential finding is the credential, and it flows into reporters, provenance, and prompts. Redacting at each boundary means a boundary added later leaks by default. `Finding` now redacts `message` and `raw` in a model validator, so there is no moment at which an unredacted `Finding` exists. Identity is computed from rule and location, so this cannot move an `id`.

**Next:** the exception store, then the whole-run timeout, then provenance.

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

### 2026-08-30 — CheckContext, the checkov adapter, dedup, and the CLI

**Done:** the interface fix, `checks/checkov.py`, cross-tool deduplication, `plimsoll scan` and `plimsoll doctor` with terminal and JSON reporters, and the `insecure_s3` / `compliant_s3` fixture corpus. 109 unit tests, green on a machine with no terraform, checkov, or opa. The deterministic scanner now works end to end.

**The interface change.** `Check.run` takes a `CheckContext` rather than `(workspace, config)`. The protocol could not express the `opa` adapter at all: `opa eval` runs against `terraform plan` JSON that a *different* adapter produces, and there was no way to share it. Both alternatives were worse — `opa` running its own `terraform plan` pays init and plan twice and lets two adapters disagree about the flags, and having `Gate` produce the plan puts terraform inside a core that is meant to be format-agnostic. The provider is injected into `Gate`, memoized per run, and its failure is cached so every consumer fails identically rather than two adapters reaching different conclusions about one workspace. Done now because there was exactly one adapter to migrate.

**Surprises, all from capturing real checkov output instead of writing fixtures against the schema:**

- **Checkov's JSON top level is sometimes a list.** One object per framework, and it returns an array as soon as a second framework finds something. The `secrets` framework runs by default, so this is not an edge case — it is what any repository with a leaked credential looks like. A parser written against the documented single-object shape crashes there.
- **`severity` is always `null`.** It is a Bridgecrew platform field. The community edition never populates it, so severity cannot come from the tool; it comes from an explicit table keyed on check ID. Nothing in the docs anticipated a scanner that reports no severity.
- **A clean run has no `results` key at all** — just a bare summary object. `payload["results"]` raises `KeyError` on success.
- **Checkov's Windows console script is broken.** It resolves `python.exe` off `PATH` rather than its own environment, so `checkov` is on `PATH` and cannot run. `is_available()` now treats a non-zero `--version` as unavailable, which turned a crash mid-scan into a skip.
- **The compliant fixture cannot be checkov-clean.** Checkov also wants event notifications, a lifecycle configuration, and cross-region replication on every bucket. Those are operational opinions, not the failure modes this project targets, so "compliant" means compliant with the v0.1 rule pack. That is now stated in the fixture rather than left to be rediscovered.
- **A single tool duplicates itself.** `CKV_AWS_62` and `CKV2_AWS_40` both mean "this policy grants admin" and both fire on the same resource. Dedup had been framed as a cross-tool problem; it is not only that.

**Decisions:**

- Unmapped checkov checks get a `checkov.<id>` rule class at MEDIUM, deliberately below the default `--fail-on`. Checkov ships far more opinions than the rule pack covers, and failing a merge on a rule nobody has classified is how policy-as-code gets switched off. The finding still appears.
- `rule_map.py` carries only IDs read off real output. A misfiled finding is invisible; a missing mapping is visible in the report. Under-classification is recoverable, misclassification is not.
- Dedup records the dropped tool in `Finding.also_reported_by` rather than discarding it — provenance has to be able to say both tools flagged it.
- Findings with no resource address are never deduplicated. Two hardcoded secrets share a rule class without being one problem, and merging them deletes a real finding.
- `--compact` is passed to checkov for hygiene, not brevity: without it every finding carries the offending source lines, which for a credential finding is the credential itself, and `Finding.raw` flows into provenance and prompts.
- The CLI was pulled ahead of the `opa` and `terraform_plan` adapters, against the order in `implementation_plan.md`. That order leaves a public repository unrunnable for weeks, including for a contributor who has just written an adapter and wants to see its output.
- `verify`, `explain`, and `exceptions` are absent rather than stubbed, with a test asserting it.

**Unblocked:** `opa` and `terraform_plan` now have an interface that can carry a plan and a CLI to report through. Contributors can see an adapter's output without the loop existing.

**Still open:**

- `--timeout` is per-check, not the whole-run budget in `specifications.md`. Exit code 4 is unimplemented; half a timeout is worse than none.
- The `terraform validate` fixtures are still reconstructed rather than captured — no terraform on this machine. The checkov fixtures are real.
- `Finding.raw` carries `file_abs_path` from the scanning machine. Not a secret, but it reaches provenance and should be considered when redaction is written.

### 2026-08-30 — Real terraform, real fixtures, and the bug they found

**Done:** installed terraform 1.15.8 and opa 1.20.1, re-captured every `terraform_validate` fixture from real runs, fixed the bug that exposed, and settled the `terraform plan` credentials question by testing it.

**The bug.** `terraform validate` in an uninitialized workspace returns exactly one diagnostic — `Missing required provider`, no range, no snippet — and never reaches anything real in the file. The adapter mapped it to an ordinary HIGH finding, so a scan of a fresh CI checkout looked like a check that ran and found one problem while every actual violation stayed invisible. `SECURITY.md` calls that class of thing a vulnerability. It now raises `CheckExecutionError`, so the tool is excluded from `tooling` and the run reports that nothing was verified. The old slow test asserted only that findings was non-empty, so it passed on that useless diagnostic and would have gone on passing indefinitely.

**Two more things the reconstructed fixtures had wrong:** the warning they used — "interpolation-only expressions are deprecated" — is not emitted by terraform 1.15 at all, and real warnings come from the provider and carry an `address` field, so the branch that prefers `address` over `snippet.context` had never been exercised by a real payload.

**`terraform plan` needs no AWS and no emulator.** Tested end to end: with `access_key = "test"`, `secret_key = "test"`, and `skip_credentials_validation`, `skip_requesting_account_id`, `skip_metadata_api_check`, `skip_region_validation`, the chain `terraform init` → `plan -out` → `show -json` completes offline and produces plan JSON v1.2 with `resource_changes[].change.after.acl == "public-read"` — exactly what `opa eval` consumes. That closes the open question in this log and means the `terraform_plan` adapter needs no LocalStack or Floci.

**On Floci** (github.com/floci-io/floci, MIT, 22.7k stars, created 2026-02-18): a LocalStack-Community alternative exposing an AWS-compatible endpoint on `localhost:4566`. It does not help here. It replaces the AWS *API endpoint*, not the *provider registry*, so it has no effect on `terraform init`, and `plan` already works without it. The place it would earn its keep is the Phase 0/5 benchmark harness, where DPIaC-Eval's deployment-success metric needs something to deploy against and LocalStack's useful tier is paid. Keeping it out of the gate also keeps distance from invariant 5 — a working local AWS one command away makes "never apply" much easier to erode.

**Decisions:**

- An adapter that cannot verify must fail, not report. Returning the one diagnostic terraform managed to emit would have been technically truthful and practically a lie.
- The `terraform_plan` adapter will use dummy credentials with the `skip_*` flags. An emulator gets reconsidered only if a fixture needs a data source or existing state, which is when that path genuinely breaks.
- Slow tests assert the specific rule class now, not merely that something was found. A slow test that passes on a "could not run" diagnostic is worse than no slow test, because it reads as coverage.

**Unblocked:** `terraform_plan` and `opa` have a verified, offline, credential-free path to plan JSON. `opa eval` is confirmed working on Rego v1.

**Still open:** nothing initializes the workspace for the user — `terraform_validate` reports that it could not run, which is honest but not yet helpful. Whether the adapter should offer to run `terraform init -backend=false` behind a flag is a decision for when the GitHub Action is built, since that is where the workflow can do it instead.

---

## Phase 2 — The loop

**Exit criterion:** a verified patch produced against a real fixture with a hosted model, and a clean escalation on a fixture the model cannot fix. *(Revised: was "with a local 7B model" — see the decision below.)*

### 2026-08-31 — The whole loop, and hosted inference by default

**What changed:** `llm/` (provider protocol, `openai_compat`, `null`, the two prompts), `core/context.py`, `core/redaction.py`, `core/verifier.py`, `core/loop.py`, the `Patch`/`PatchVerification`/`Decision` models, `plimsoll verify` and `plimsoll explain`, and decision-aware reporters. 2,119 lines of source, 183 unit tests, all still passing with no external binary, no model, and no network.

**Now unblocked:** the tool is an agent rather than a linter. Also unblocked: prompt iteration, because prompts are text files with a fake-provider test harness behind them (`tests/conftest.py`) that can script a model's answer and assert what the loop does with it.

**Decision — hosted inference by default; no local model assumed anywhere.** The old default was Ollama with `qwen2.5-coder:7b`, on the reasoning in NFR1 that a project only its author can afford to run gets no contributors. That reasoning was right and the conclusion was half-wrong: it solved cost and ignored hardware. A laptop without a discrete GPU either cannot run a 7B model or runs it too slowly to wait for, and that is most contributors' laptops. A tool you cannot run is a tool you cannot contribute to, which is the same failure NFR1 was written to prevent, arriving by the other door. Zero-cost is now met by free-tier hosted endpoints, which need neither money nor hardware. Local inference still works — Ollama speaks the same wire format — it is just no longer an assumption. NFR1, `tech-stack.md`, `architecture.md`, and the README were updated rather than left to contradict the code.

**Decision — the model returns whole files, not unified diffs.** `specifications.md` specified a diff. Models get hunk headers wrong and drift context lines, which makes the diff *applier* the component that fails; worse, every malformed diff burns a retry without ever testing whether the model understood the rule. Whole-file output moves the formatting burden onto `difflib`, which cannot get it wrong. The diff is still what a human reads and what provenance will record. Two planned dependencies fell out as a side effect — `python-hcl2` and `unidiff` are both unnecessary.

**Surprise — `Finding.id` is the wrong key for comparing across a patch.** It includes `line_start`, which is correct for deduplication and quietly wrong here: a fix that inserts two lines shifts every finding below it, so an id comparison reports all of them as simultaneously removed and newly introduced. Every patch would have looked like a catastrophic regression. `finding_identity()` is line-independent for that reason.

**Surprise — a verifier without a baseline can never verify anything in a real repository.** The first version treated any finding in the patched workspace as introduced by the patch. In a workspace with two problems, fixing one leaves the other, which reads as a regression, which discards a working fix. Recording what was already violated before any patch is not an optimisation; without it the component does not function on any input more complex than the test fixture.

**Surprise — redaction has to be reversible.** Redacting prompts was straightforward. The consequence was not: the model returns a whole file containing the placeholder, and writing that back would hand the user a patch that silently destroys a live credential. `Redactor` numbers placeholders and restores them. An invented placeholder is deliberately *not* resolved — the patch then fails re-verification and never reaches a human, which beats guessing which secret was meant.

**Still open:** no provider in this repository's history has talked to a live endpoint. Every path is tested against recorded payloads with no socket opened, per the no-network-in-unit-tests rule, but that is not the same as having worked. The first real run is where the retry-loop economics — how many attempts a real model needs, and what that costs — become measurable rather than assumed.

**Still open:** the loop has little to reason about until the Rego pack exists. Findings come from `checkov` and `terraform validate` only, so the verified-fix rate cannot be measured yet and should not be guessed at.

---

## Phase 3 — Memory and provenance

**Exit criterion:** stable hash-verified provenance record, exception correctly attributed, no secret surviving redaction.

### 2026-09-01 — SARIF and the PR comment

**What changed:** `reporters/sarif.py` (SARIF 2.1.0), `reporters/github.py` (a Markdown PR comment), and `--format sarif|github` on `scan`, `verify`, and `explain`. The CLI now dispatches formats through a table rather than a chain of `if`s in three commands. 25 new tests.

**Now unblocked:** Phase 4. The GitHub Action has something to emit.

**Decision — SARIF `ruleId` is the rule class, not the tool's own ID.** A reviewer suppressing `s3.acl.public` in GitHub should not have to suppress it once for checkov and again for the bundled pack. The tool's ID stays on the result as `properties["tool-rule-id"]`, because that is what you search the tool's own documentation for.

**Decision — MEDIUM maps to `warning`, not `error`.** The default is `--fail-on high`, so a SARIF level that failed a build the CLI would have passed makes the two disagree about the same run.

**Surprise — `invocations[]` is the load-bearing part of the SARIF file, not `results[]`.** GitHub's code-scanning UI shows results. A run in which every adapter was missing produces an empty results list, and an empty results list there is indistinguishable from a clean workspace — which is precisely the failure this project exists to prevent, arriving through a UI rather than through the code. Skipped checks, failed checks, "nothing was verified", and "the run timed out" are all `toolExecutionNotifications`, and `executionSuccessful` is false for any of them.

**Decision — an exempted finding uses SARIF's own `suppressions`.** The alert closes with the justification attached rather than sitting open forever next to a `.plimsoll/exceptions.yaml` entry saying it was accepted.

**Surprise (in a test, not the code) — `Finding.id` keys on `rule_id`, not `rule_class`.** Three fixtures sharing a `rule_id`, resource address, file, and line collapsed to one id, and every reporter correctly treated them as the same finding. The id is right; the fixture was wrong.

### 2026-09-01 — Provenance

**What changed:** `core/provenance.py` (`RepoContext`, `ModelIdentity`, `RunSummary`, `Signature`, `ProvenancePatch`, `ProvenanceDecision`, `ProvenanceRecord`), `core/schema.py` to regenerate `schemas/provenance-0.1.json`, and `--provenance` / `--generated-by` on `scan`, `verify`, and `explain`. 22 new tests.

**Now unblocked:** Phase 3's exit criterion. A run is now checkable by someone who was not there: which binaries at which versions, which model, which commit, which workspace bytes, one entry per finding, and a hash over all of it.

**Decision — `Patch.new_content` has no field in the record, rather than being redacted into one.** It is the corrected file with every redacted secret *restored* — it has to be, or a patch would write placeholders back into the user's repository. `ProvenancePatch` carries the diff and the rationale, both through `redact()`, and nothing else. A field that must be remembered to be stripped is a field that will eventually not be; a field that does not exist cannot leak. This was the one place invariant 6 was still reachable.

**Decision — `plimsoll scan` writes a record too, with a decision per finding.** A record whose `decisions` list is empty would make a clean run and a scan of a broken workspace look the same. The untried statuses come from `core.loop.untried_status`, which the loop itself now calls, so the two cannot drift.

**Decision — the workspace hash covers `.plimsoll/` and skips `.terraform/` and state files.** The exception store is an input to every decision in the record, so a change to it must change the hash. Downloaded providers are not configuration, and Terraform state holds every attribute of every deployed resource in plaintext — not hashing it means not reading it.

**Surprise — hashing concatenated bytes would have been wrong.** A single stream over the file contents cannot tell `a.tf` + `b.tf` from one file holding both, so moving a resource between files would not change the hash. Per-file digests keyed by relative path, sorted, then hashed. There is a test for exactly that rename.

**Still open:** `signature` is reserved and always null. It is in the schema now so that adding Sigstore in v0.2 is not a breaking change for anything already parsing these records.

### 2026-09-01 — The whole-run timeout and exit code 4

**What changed:** `--timeout` is now the whole-run budget with a default of 600, as the CLI contract always said it was; `--check-timeout` (default 120) is the per-subprocess value `--timeout` used to mean. `VerifierLoop.run` and `Verifier` both take the run's `Deadline`, checked between findings and between patch attempts. `timed_out` maps to exit 4, checked ahead of every other condition, and both reporters lead with it.

**Now unblocked:** running Plimsoll in CI without a hang. Six adapters and three retries per finding can sit inside every per-check timeout and still take twenty minutes, which reads as a hang and gets the job cancelled with no output at all.

**Decision — exit 4 is checked first, ahead of exit 3.** Timing out is the reason the rest of the run is incomplete, so reporting anything else buries the cause. Both are non-zero and both mean "not verified", so either would be honest; 4 is the one that tells you what to do.

**Surprise — a partial re-verification is more dangerous than no re-verification.** `Verifier.verify` already refused to judge a patch when no check ran. It did not refuse when *some* ran: the adapter that would have caught the regression may be exactly the one the budget cut off, and the patch would have reached a human labelled "verified". `after.timed_out` is now `INVALID`.

**Decision — a finding past the budget still gets a decision.** Untried, and therefore still blocking. Dropping a finding because the clock ran out is the one thing a verification tool may not do.

**Next:** provenance.

### 2026-09-01 — The exception store

**What changed:** `core/exceptions.py` (`ExceptionEntry`, `ExceptionStore`), wired into `VerifierLoop.run` ahead of targeting, `plimsoll exceptions list | add | expire | prune`, and exemption-aware terminal and JSON reports. 19 new tests.

**Now unblocked:** provenance, which has to serialise an exemption, and the SARIF and GitHub reporters, which have to render one.

**Decision — matching happens before targeting, not after.** An exempted finding is decided before it is ever a candidate for the model. That is not an optimisation: an accepted violation must not cost a token and must not leave the machine. The test that asserts it hands the loop a provider that raises on any call at all.

**Decision — `expires` is the day the exception lapses *on*, not its last valid day.** `is_expired` is `expires <= today`. This makes `plimsoll exceptions expire` take effect the moment it is run, and makes the ambiguous case fail closed. Erring the other way keeps a lapsed sign-off in force for one more day, which is the wrong direction for this tool.

**Decision — `extra="forbid"` on `ExceptionEntry`.** A misspelled key that pydantic silently dropped would produce a file that looks like an exemption and is not. The person who wrote it would never find out.

**Decision — exemptions apply to `verify`, not to `scan`.** `scan` reports findings; it has no decisions to attach an `ExceptionRef` to. `verify --no-llm` is the deterministic path that is exemption-aware, and it is what CI should run when the repository has a store.

**Surprise — `fnmatch` folds case on Windows.** Terraform resource addresses are case-sensitive, so the same committed store would have exempted different findings on a developer's laptop than in CI. `fnmatchcase` throughout.

**Next:** the whole-run timeout and exit code 4.

---

## Phase 4 — Ship

### 2026-10-22 — First run with all four tools together, and adoption features

**Done:** The gate ran end to end on Linux against terraform 1.15.8, opa 1.20.1, and
checkov 3.3.15 at once, for the first time anywhere. `pytest -m slow` (10), `opa test`
(61), and SARIF and provenance schema validation all pass against real output. Three bugs
fixed from that run (below). Then the features that decide whether a team keeps a policy
gate switched on: `plimsoll baseline` and `--baseline`, exceptions honoured by `scan`,
`.plimsoll/config.yaml`, `--strict`, a JUnit reporter, a pre-commit hook, and the Action
inputs for all of them. Nine checkov IDs mapped from a real capture of the rule corpus.

**Surprises:**
- **The rule-corpus test failed the moment checkov was installed.** The gate was right:
  deduplication kept checkov's copy and recorded OPA in `also_reported_by`. The test
  counted `source` only. The handoff predicted this exact gap and nothing could test it.
- **Rejected AWS credentials became a HIGH finding.** The container had AWS keys that STS
  refused. `has_real_credentials()` correctly declined to write the override, the plan
  failed on `GetCallerIdentity`, and the adapter reported the diagnostic as a violation on
  the `provider` line while OPA skipped. Any CI runner with an expired key would have hit
  it. Now a failed check that says how to fix the runner.
- **`scan` never read the exception store.** The README promised the opposite. Only
  `verify` applied it.
- **The Action would have overridden the config file.** It passed `--fail-on high` and both
  timeouts unconditionally.
- **Checkov addresses a provider as `aws.default`.** The Rego pack says `provider.aws`, so
  one leaked key was two CRITICAL findings.
- `registry.terraform.io` was blocked on this machine; a filesystem mirror of
  `hashicorp/aws` 5.100.0 from `releases.hashicorp.com` got terraform through. Docker
  started, but Docker Hub rate-limited the base image, and with a mirror of the same
  digest the build containers still could not resolve the Debian mirrors. **The image is
  still unbuilt.**

**Decisions:**
- *Suppression moves findings, never deletes them.* `GateResult.suppressed` holds every
  held-back finding with its reason, and every reporter counts it.
- *Baselines key on a new `Finding.fingerprint`*, not `Finding.id`: the id has the line
  number in it, and a baseline that broke on every edit would be deleted in a week.
- *The baseline lives in `GateConfig` and is applied inside `Gate.run`*, so the verifier
  re-runs a patch under the same baseline (invariant 2), rather than as a step after the
  gate that re-verification would skip.
- *A baseline is not a decision.* Exempted findings go into `scan`'s provenance as
  `EXEMPTED`; baselined ones do not, because nobody reviewed them.
- *`--strict` is opt-in on the CLI and on by default in the Action.* A laptop without
  checkov is normal; an Action image without it is broken.

**Next:** unchanged at the top — run the benchmark against a live model. Then the image
build, on a runner with Docker Hub access.

**Exit criterion:** ten lines of workflow YAML in an unrelated repo produce a useful PR comment. **Built, not demonstrated** — see below.

### 2026-09-01 — The Action, the workflows, and one report per run

**What changed:** `action/Dockerfile`, `action/entrypoint.sh`, `action/post_comment.py`,
and `action.yml`; `.github/workflows/integration.yml` and `publish.yml`; CI hardened with
SHA-pinned actions and `permissions: contents: read`; and `--report FORMAT=PATH` on the
CLI. 43 new tests.

**Now unblocked:** everything in Phase 4 except the demonstration.

**Decision — `action.yml` at the repository root, not in `action/`.** A container action's
build context is the directory holding the metadata file. From `action/`, the image could
not see `src/` and so could not install the package it exists to run. The root also means
one repository: `uses: <owner>/plimsoll@v0` resolves here, and the separate
`plimsoll-action` repository the README promised would have been a synchronisation problem
nobody asked for. The README now points at this one.

**Surprise — the runner does not translate hyphens in input names.** An input called
`fail-on` arrives as `INPUT_FAIL-ON`, which no shell can expand: `$INPUT_FAIL-ON` reads as
`$INPUT_FAIL` followed by the literal text `-ON`. The failure is silent — the entrypoint
would have fallen back to every default and reported on a threshold nobody chose. Every
input is now mapped explicitly in `runs.env`, and a test asserts that none was forgotten.

**Decision — `--report`, because the gate is expensive.** The first entrypoint ran the
tool four times to get four formats, which for a `terraform init` plus a plan plus an
`opa eval` is minutes of duplicated work — and three formats rendered from three runs can
disagree if anything in the workspace moved between them. They are renderings of one
decision, so the CLI now produces them from one.

**Surprise — the SARIF `$schema` URL was a 404.** Trying to add the schema validation the
plan asked for revealed that the URL every emitted record carried pointed into a branch
layout the sarif-spec repository has since rearranged. Nothing in the suite had noticed,
because nothing fetched it. Now the OASIS errata URL, with a test tying it to the workflow
that validates against it. `check-jsonschema` has no vendored SARIF schema either, which
the workflow would have discovered in CI had the command not been tried locally first.

**Surprise — the exit-code handling was wrong under `set -e`.** The dogfood step asserted
"the violating corpus fails" by letting `set -e` catch a non-zero exit, which cannot tell
exit 1 (findings, correct) from exit 3 (nothing ran, a broken toolchain). It asserts the
exact code now. That distinction is the entire reason exit 3 exists.

**Still open — the image has never been built.** Docker Desktop was not running on this
machine and could not be started non-interactively. The wiring is asserted by
`tests/test_action.py` — every input reaches the container, every mapped variable is read,
every documented output is written, and the versions pinned in the Dockerfile match the
integration runner's — but that is static analysis of a shell script. The `action-image`
job builds the image and drives it against the fixture corpus, and will be the first thing
to have actually run it. **Until that job is green, this phase's exit criterion is
untested rather than met.**

---

## Phase 5 — The numbers

_The harness is built; see the Phase 0 entry. Nothing has been measured._

**Exit criterion:** an independent reader reproduces the headline delta from a clean checkout. **Not met.**

---

## Open questions

Move these into decisions as they resolve, with the reasoning.

- [x] **Does `terraform plan` work reliably enough with dummy credentials for greenfield AWS configs, or is LocalStack required in the default dev path?** Answered: placeholder credentials work, but not on their own. The AWS provider calls `sts:GetCallerIdentity` before it will plan anything, so the `skip_*` override file is required too — see the decision log. LocalStack stays the answer for configurations with `data` sources that genuinely reach the API, and is not in the default path. The remaining friction is not credentials at all: it is the cold provider download, which on a slow link exceeds the default per-check timeout.
- [ ] Is Checkov better as a subprocess or a direct import? Subprocess is the current call for version independence — revisit if parsing proves fragile. *(Still open, and now slightly worse: checkov's console script is broken on this machine with `ModuleNotFoundError: No module named 'checkov'`, so the adapter has never been exercised end to end here. The image and the integration runner install it properly, which is where it will first be tested.)*
- [ ] What is the realistic verified-patch rate, and how many attempts does it take? *(The harness now measures it — `benchmarks/runner.py` reports mean attempts per finding and the escalation rate. Nobody has run it. Still: do not guess it in the README.)*
- [ ] Which small hosted models clear the bar? The prompts are written to work on modest models, but that is an intention until it is measured across a few.
- [ ] How reliably can `generated_by` be detected without a flag? Commit trailers and PR labels are both weak signals.
- [ ] Does the dedup key `(resource_address, rule_class)` hold up when Checkov and OPA disagree on severity for the same violation? *(Untested in practice: checkov has not run alongside the pack on this machine. The corpus is where it will show up.)*
- [ ] Is `verify`'s per-patch-attempt `terraform init` worth optimising? `TF_PLUGIN_CACHE_DIR` makes it a local copy rather than a download, so it is a cost and not a bug. Measure before building a shared `TF_DATA_DIR`.

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

### 2026-08-31 — Hosted inference by default, no local model assumed

**Decision:** `openai_compat` is the only network provider and the default. No default endpoint or model ships. No code path, test, or documented workflow may require a GPU or a locally hosted model.
**Reasoning:** the zero-cost requirement in NFR1 exists so that contributors can run the tool. Making a local 7B model the default satisfied the cost half and broke the hardware half, excluding anyone without a discrete GPU — most people. Free-tier hosted endpoints satisfy both. Local inference is unaffected: Ollama speaks the same wire format and remains supported as an option.
**Cost:** the tool now sends Terraform source to a third party by default. Mitigated by a single audited egress point (`core/context.py`), mandatory redaction, a refusal to send state files, and `--no-llm` / `scan` remaining fully functional. Documented in `SECURITY.md` rather than left implicit.
**Supersedes:** the local-model default in `tech-stack.md` and the wording of NFR1.

### 2026-08-31 — Whole-file patches instead of unified diffs

**Decision:** `patch.txt` asks for the complete corrected file; `difflib` computes the diff we display.
**Reasoning:** model-authored diffs fail on hunk headers and context drift often enough that the applier becomes the failure mode, and each malformed diff costs a retry without testing comprehension of the rule. Whole-file output cannot be malformed in that way.
**Cost:** larger prompts, and a file-size ceiling above which a finding cannot be patched. Both are cheaper than an unreliable applier.
**Supersedes:** the `Patch` shape and `patch.txt` contract in `specifications.md`, both updated in place with the reasoning recorded there.

### 2026-09-01 — Terraform runs against a copy, with an injected provider override

**Decision:** when the workspace is not already initialised, terraform runs against a
throwaway copy in the run's scratch directory, and — only when no real credentials are
present *and* a default `provider "aws"` block exists — a `plimsoll_provider_override.tf`
is written into that copy setting `skip_credentials_validation`,
`skip_requesting_account_id`, and `skip_region_validation`.

**Reasoning:** two constraints that look separate and are not. `terraform init` writes
`.terraform/` and `.terraform.lock.hcl` into its working directory, and this tool promises
never to modify your repository; that promise does not get an exception for "but terraform
needs it". And placeholder credentials alone do not produce a plan: the AWS provider calls
`sts:GetCallerIdentity` first, gets `InvalidClientTokenId`, and fails the whole plan —
which would be reported as a finding about the user's configuration when it is really a
fact about the machine having no AWS account. Both are solved by the same throwaway copy.

**Cost:** the tool now writes a file into a copy of somebody's configuration, which is
exactly the kind of thing a security tool should not do quietly. Documented in
`SECURITY.md` and `architecture.md` with both preconditions spelled out, and the file's
own header explains itself to anyone who finds it. The three arguments relax checks about
*the caller* and change nothing about how any resource is planned, which is the only thing
the rule pack reads.

**Supersedes:** `architecture.md`'s "runs against dummy credentials", which was true and
insufficient.

### 2026-09-01 — Findings are redacted at construction, not at serialisation

**Decision:** `Finding.message` and `Finding.raw` are redacted by the model's own
validator. `Patch.new_content` never enters a provenance record at all.

**Reasoning:** the plan was to redact on the way into provenance. That leaves the secret in
memory and on every path that does not go through that boundary — the terminal report, the
JSON report, a log line. A finding carries a checker's verbatim message and its unmodified
output, and either can contain the credential the checker just found. Redacting in the
validator means there is no path on which an unredacted finding exists, because one cannot
be constructed. `Patch.new_content` is the deliberate opposite case: it is the whole
corrected file with secrets *restored*, which is what it has to be to be applied, so it is
excluded rather than redacted.

**Cost:** `Finding.id` had to stay derived from the unredacted identity fields, or
redaction would silently change what deduplicates and what an exception matches. Asserted
by a test.

**Supersedes:** the Phase 3 task "apply redaction before serialisation too", and the gap
`SECURITY.md` named.

---

## Blocked

_Nothing blocked._

Three things are **unverified rather than blocked**, each needing something this machine
does not have: a live model endpoint, a working Docker daemon, and a free-tier API key.
They are the benchmark run, the Action image build, and the first real provider call.
Nothing depends on them to continue; everything depends on them to be believed.
