# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Nothing is released yet. Everything below is on `main` and the shapes it describes can
still change without a major version bump — see [`specifications.md`](specifications.md)
for what is a contract and what is not.

## [Unreleased]

### Added

- **Project scaffold** — uv, ruff (lint and format), `mypy --strict` on `src/`, and
  pytest with a `slow` marker for tests that shell out to real binaries. CI runs lint,
  format, and types on one job and the unit suite on Python 3.11, 3.12, and 3.13, on a
  runner with no terraform, checkov, or opa installed.
- **Core models** (`core/models.py`) — `Finding`, `Severity`, `CheckSource`,
  `ToolAvailability`, `GateResult`, `ToolVersion`, `SkippedCheck`, `FailedCheck`, and the
  config models. Pydantic v2 throughout.
- **The gate** (`core/gate.py`) — the `Check` protocol, the adapter registry,
  availability probing, and per-adapter exception isolation. One crashing check is
  recorded and the run continues.
- **`terraform_validate` adapter** (`checks/terraform_validate.py`) — the first check.
  Parses `terraform validate -json` diagnostics into findings. Needs no credentials.
- **`checkov` adapter** (`checks/checkov.py`) — the broadest rule coverage. Handles all
  three shapes checkov's JSON takes, including the list it returns once a second
  framework reports and the bare summary a clean run emits. Fixtures captured from a
  real checkov 3.3.15 run.
- **`checks/rule_map.py`** — checkov check IDs to internal rule classes and severities.
  Checkov's community edition reports `severity: null` on every finding, so severity
  cannot come from the tool. Unmapped checks land below the default `--fail-on`.
- **Cross-tool deduplication** — one violation reported by several tools collapses to one
  finding on `(resource_address, rule_class)`, keeping the most severe assessment and
  recording the others in `Finding.also_reported_by`. Findings with no resource address
  are never merged.
- **`plimsoll scan` and `plimsoll doctor`** (`cli.py`) with terminal and JSON reporters,
  `--fail-on`, `--format`, `--policy-dir`, and `--timeout`. Exit codes 0, 1, 2, and 3 per
  the CLI contract; a run where no check could execute exits 3 rather than reporting
  clean.
- **Fixture corpus** — `tests/fixtures/insecure_s3` and `tests/fixtures/compliant_s3`,
  the smoke-test targets the README and CLAUDE.md already referenced.
- **`checks/severity_map.py`** — explicit tool-severity to `Severity` tables. Severity is
  never inferred.
- **Community documentation** — `LICENSE` (Apache 2.0), `CONTRIBUTING.md` with the
  add-a-check-adapter walkthrough, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and pull
  request templates, and `ONBOARDING.md` for contributors with no cloud background.
- **The reasoning loop** (`core/loop.py`) — verify, reason, re-verify, escalate. Bounded
  retries, with the re-verification failure fed back into the next prompt. An unverified
  patch is discarded, not downgraded to a suggestion; escalation carries the original
  finding and nothing that could be mistaken for a fix.
- **Patch re-verification** (`core/verifier.py`) — the patch is applied to a throwaway
  copy of the workspace and the *same* `Gate` object, with the *same* config, runs again
  over it. The workspace itself is never written to. Findings are compared on a
  line-independent identity, so a fix that shifts lines is not read as replacing every
  finding below it, and a baseline of pre-existing violations means a patch is not blamed
  for problems that were already there.
- **`Patch`, `PatchVerification`, `Decision`** in `core/models.py`, with a validator that
  makes `status=FIXED` unreachable without a `PatchVerification` whose outcome is
  `verified`. Invariants 1 and 3 are an assertion rather than a convention.
- **Model providers** (`llm/`) — the `LLMProvider` protocol, `openai_compat`, and `null`.
  One adapter reaches agent routers, GitHub Models, Groq, vLLM, LM Studio, a local
  Ollama, and OpenAI itself. Transport is `urllib`; no HTTP dependency was added.
- **Prompt construction** (`core/context.py`) — the only place workspace content becomes
  network payload. Refuses paths outside the workspace, never sends `terraform.tfstate`,
  and refuses oversized files rather than truncating them.
- **Secret redaction** (`core/redaction.py`) — applied to every prompt. Reversible on the
  prompt path, so a patch never overwrites a live credential with a placeholder.
- **`plimsoll verify` and `plimsoll explain`**, with `--no-llm`, `--max-retries`, and
  `--max-llm-findings`. `doctor` now reports the model provider alongside the check
  binaries, without contacting it.
- **`terraform_plan` adapter and the shared terraform plumbing**
  (`checks/terraform_plan.py`, `checks/terraform_cli.py`) — one `init`, `plan`, and
  `show` per gate run, cached on the `CheckContext` so `validate` and `plan` cannot
  disagree about the same files. The workspace is never written to: when it is not
  already initialised, terraform runs against a throwaway copy. Provider downloads are
  cached across runs, which matters because the verifier re-runs the gate once per patch
  attempt.
- **A provider override for machines with no AWS account.** The AWS provider calls
  `sts:GetCallerIdentity` before it will plan, so placeholder credentials alone produce
  `InvalidClientTokenId` and a failed plan that reads as a broken configuration. A
  `plimsoll_provider_override.tf` is written into the throwaway copy — never into your
  repository — and only when no real credentials are present and a default
  `provider "aws"` block exists to merge into.
- **`opa` adapter** (`checks/opa.py`) — evaluates the bundled Rego pack and any
  `--policy-dir` against the plan JSON.
- **The bundled rule pack** (`src/plimsoll/policies/`) — twenty rules across `iam`, `s3`,
  `network`, `datastore`, and `hygiene`, on a shared `lib`. Every rule has an `opa test`
  case beside it and a fixture in `tests/fixtures/rules/`, and a slow test asserts that
  every rule class fires against the violating corpus and none fires against the fixed
  one.
- **`checks/locator.py`** — resource address to file and line. Plan JSON carries neither.
- **`core/deadline.py` and the whole-run budget.** `--timeout` is now the run budget
  (default 600) and `--check-timeout` is the per-subprocess one (default 120). The
  deadline is checked between adapters, between findings, and between patch attempts, and
  caps each subprocess timeout so an adapter cannot outrun the budget it was admitted
  under. A run that exceeds it exits **4** and never exits 0.
- **Exception store** (`core/exceptions.py`) and `plimsoll exceptions list | add | expire
  | prune`. Matching is exact on `rule_class` and glob on `resource_address`. A matched,
  live exception is resolved before any model is consulted, so an accepted violation costs
  no tokens and does not leave the machine. An expired one does not exempt: it is attached
  to the decision and reported as lapsed.
- **Provenance records** (`core/provenance.py`), `--provenance PATH`, `--generated-by`,
  and a published `schemas/provenance-0.1.json` with a test that fails when the committed
  schema drifts from the model. Canonical JSON, sorted keys, `content_hash` computed over
  everything but itself and the reserved `signature` block.
- **SARIF 2.1.0 and GitHub PR comment reporters** (`reporters/sarif.py`,
  `reporters/github.py`), and `--format sarif|github`. SARIF carries skipped and failed
  checks in `invocations[].toolExecutionNotifications`, because code scanning shows
  results and nothing else, and silence there would read as success.
- **`--report FORMAT=PATH`**, repeatable. Several reports from one gate run: SARIF for
  code scanning, markdown for the pull request, JSON for a CI job's own outputs. The gate
  is an `init`, a plan, and an `opa eval`, so three formats would otherwise cost three
  plans and could return three different answers.
- **GitHub Action** — `action.yml` at the repository root, with `action/Dockerfile`,
  `action/entrypoint.sh`, and `action/post_comment.py`. Pinned terraform, opa, and
  checkov; one gate run; the CLI's exit code passed through untranslated; and a pull
  request comment that edits the previous one in place rather than adding another.
- **Benchmark harness** (`benchmarks/`) — `runner.py`, a twelve-scenario set covering all
  twenty rule classes, and stub `BASELINE.md` and `RESULTS.md`. It scores the raw model
  output and the post-loop output in one pass, with the gate deciding both.
- **`integration.yml` and `publish.yml`** — the first installs the real binaries and runs
  the slow tests, `opa test`, a SARIF schema validation, and Plimsoll against its own
  corpus; the second publishes to PyPI on tag via trusted publishing, with no stored
  token.
- **Baselines** (`core/baseline.py`, `plimsoll baseline`, `--baseline`) — record the
  findings a repository already has, so adopting the gate fails only on what a change
  introduces. Applied inside `Gate.run`, so a patch is re-verified under the same
  baseline. Never recorded in provenance as a decision.
- **`Finding.fingerprint`** — rule class, resource, and file, without the line number, so
  it survives edits. Keys baselines, and is published in SARIF as
  `partialFingerprints["plimsollFingerprint/v1"]` so code-scanning alerts stop reopening
  when unrelated lines move.
- **`GateResult.suppressed`** — findings held back by an exception or a baseline move here
  with their reason instead of disappearing. Every reporter counts them; SARIF marks them
  with its own `suppressions`.
- **`.plimsoll/config.yaml`** (`core/config.py`, `--config`) — per-repository defaults for
  `fail_on`, timeouts, `policy_dir`, `baseline`, `strict`, and the loop limits. A flag
  still wins. Unknown keys are an error.
- **`--strict`** — exit 3 unless every check ran, for CI that must not pass on a partial
  toolchain. On by default in the Action, whose image ships every tool.
- **JUnit XML reporter** (`--format junit`, `--report junit=PATH`) for GitLab, Jenkins,
  Azure Pipelines, and CircleCI. Blocking findings are failures; a check that could not
  run is an error, never an empty green suite.
- **pre-commit hook** (`.pre-commit-hooks.yaml`) — `plimsoll-scan`, deterministic only.
- **Action inputs** `config`, `baseline`, and `strict`, and a `suppressed` output.
- **Nine more checkov mappings** — security group, RDS, EBS, IAM trust, admin attachment,
  and provider credentials, read off a real capture against the rule corpus and matched
  to the Rego finding on the same resource. Cross-tool deduplication on that corpus went
  from 5 merged violations to 14. A test now enforces that every mapped ID appears in a
  captured fixture.

### Fixed

- **`scan` ignored `.plimsoll/exceptions.yaml`.** Only `verify` applied it, so a violation
  a reviewer had signed off on still failed `scan` in CI. `scan` now honours live
  exceptions, names expired ones, and records exempted findings in provenance as `EXEMPTED`
  decisions.
- **Rejected AWS credentials were reported as a HIGH policy finding.** With real-looking
  credentials present the placeholder override is rightly skipped, and when AWS refuses
  them `terraform plan` fails on `sts:GetCallerIdentity`. The adapter turned that into a
  violation on the `provider` line while the Rego pack quietly skipped. It is now a failed
  check with a message that says how to fix the runner.
- **The rule corpus test could not see the Rego pack once checkov was installed.**
  Deduplication keeps checkov's copy of a shared violation and records OPA in
  `also_reported_by`, which the test ignored; five rule classes looked silent, and a false
  positive on the compliant corpus could have hidden the same way.
- **The Action overrode the repository's config.** It passed `--fail-on`, `--timeout`, and
  `--check-timeout` on every run; those inputs now default to empty.
- **SARIF `informationUri` named an organisation this project does not own.**
- **Checkov's `aws.default` and the pack's `provider.aws` were two findings** for one
  hardcoded key in a provider block. The adapter normalises checkov's form.

- **`terraform_validate` no longer reports an uninitialized workspace as a finding.**
  Without `terraform init` there is no provider schema, so terraform returns only
  "Missing required provider" and never reaches anything real in the file. The adapter
  mapped that to a HIGH finding, which made a check that verified nothing look like one
  that ran and found a single problem. It now raises, so the run reports that nothing was
  verified.
- **`iam.resource.wildcard` no longer reads as nonsense when the action is `"*"`.** The
  message said "`*` can be scoped to an ARN", which says nothing.
- **A false positive in `datastore.rego`**, found by the compliant fixture corpus: the AWS
  provider types `encrypted` as a string on a launch template's block device, so plan JSON
  carries `"true"` rather than `true`, and every encrypted launch template was reported as
  unencrypted.
- **`terraform init` retries a dropped registry connection**, three times with backoff,
  and only when the output names a transport failure — a malformed `required_providers`
  block fails identically every time, so retrying it spends the budget three times to
  print the same message. The backoff never sleeps past the run deadline. The two failures
  used to print the same sentence and mean opposite things; they are now told apart, and
  an init timeout names the two knobs that fix it rather than sending the reader to look
  at their Terraform.
- **The SARIF `$schema` URL resolved to a 404.** It pointed into a branch layout the
  sarif-spec repository has since rearranged, so every record named a schema no validator
  could fetch. It is now the OASIS errata URL, and the integration workflow validates
  against the same constant.

### Changed

- **Inference is hosted by default; a local model is no longer assumed anywhere.** The
  earlier default was Ollama with `qwen2.5-coder:7b`, chosen because it costs nothing.
  It also requires hardware most contributors do not have: a laptop without a discrete
  GPU either cannot run a 7B model or runs it too slowly to wait for. A tool a
  contributor cannot run is a tool they cannot contribute to, which is a worse
  accessibility failure than the cost one. NFR1's zero-cost requirement is now met by
  free-tier hosted endpoints instead, which need neither money nor hardware. Local
  inference remains fully supported — Ollama speaks the same wire format — it is simply
  an option rather than an assumption. There is no default endpoint or model: guessing
  one would pick a vendor on the user's behalf.
- **Patches are whole files, not unified diffs.** `specifications.md` originally had the
  model emit a diff directly. Models get hunk headers wrong and drift context lines often
  enough that the diff applier becomes the failing component, and every malformed diff
  burns a retry without testing whether the model understood the rule. The model now
  returns the corrected file and `difflib` computes the diff. The diff is still what a
  human reads. This also dropped two planned dependencies, `python-hcl2` and `unidiff`,
  neither of which turned out to be needed.
- **`DecisionStatus.REPORTED` added.** The five statuses in `specifications.md` had no
  honest home for "a real violation, below `--fail-on`, never attempted": `PASS` means no
  violation and `BLOCKED` means the run fails. A tool whose thesis is that silence must
  not read as success cannot mislabel its own output.
- **`LLMProvider.is_available()` returns `ToolAvailability`**, the same model the check
  adapters use, rather than `bool`. An unconfigured model is the same class of fact as an
  uninstalled binary, and `doctor` should report both the same way.
- **`terraform validate` fixtures are captured from real runs**, terraform 1.15.8 with
  `hashicorp/aws ~> 5.0`, replacing fixtures reconstructed from the documented schema.
- **`Check.run` now takes a `CheckContext`** instead of `(workspace, config)`. The context
  carries the workspace, the config, a scratch directory, the run deadline, and a `once()`
  cache so adapters that evaluate rules against `terraform plan` output share one plan
  rather than each producing their own. Breaking for anyone who wrote an adapter against
  the old signature, which is the point of doing it while there is one adapter.
- **`Finding.message` and `Finding.raw` are redacted at construction**, by the model's own
  validator, rather than on the way into a provenance record. A finding carries a
  checker's verbatim message and its unmodified output, and either can contain a
  credential the checker found; redacting only at the serialisation boundary left the
  secret in memory and on every other path, the terminal report included. `Finding.id` is
  derived from the unredacted identity fields, so redaction changes neither deduplication
  nor exception matching.
- **The rule-class mapping is `checks/rule_map.py`, not `checks/rule_classes.yaml`.** A
  YAML file would need loading, validating, and shipping in the wheel to express a
  constant dictionary, and a typo in it would surface at scan time rather than at import.
- **The rule pack lives at `src/plimsoll/policies/`**, not a top-level `policies/`. A
  bundled pack that does not ship in the wheel is not bundled.
- **Third-party GitHub Actions are pinned to commit SHAs** and workflows declare
  `permissions: contents: read`.
- Renamed the project from Bulwark to Plimsoll throughout, including the `PLIMSOLL_*`
  environment variables and the `.plimsoll/` exception store path.
- Replaced tfsec and Terrascan with Trivy and KICS wherever the documentation named a
  future check adapter. tfsec was merged into Trivy by Aqua in 2023 and Terrascan was
  archived in November 2025.

### Known gaps

- **There are no published numbers.** The benchmark harness is built and tested and no run
  has happened, so `benchmarks/BASELINE.md` and `benchmarks/RESULTS.md` are stubs. Nothing
  anywhere in this repository may quote a pass rate until they have rows. This is the
  largest gap and the one most worth reading first.
- **No provider has been exercised against a live endpoint in this repository's history.**
  Request construction, response parsing, and every failure path are tested against
  recorded payloads with no socket opened, per the no-network-in-unit-tests rule. That is
  not the same as having talked to a real router.
- **The GitHub Action has not run on a real pull request.** Its image builds in the
  `action-image` job of `integration.yml`, where `doctor` finds all four adapters and the
  entrypoint writes every report; that is the whole of its verification so far. It is not
  published to the Marketplace and has no `v0` tag to reference.
- Redaction is pattern-based, so it is a mitigation and not a guarantee. A credential in
  a shape none of the patterns match will be transmitted.
- `rule_map.py` covers only the checkov IDs verified against real output. Everything else
  is reported unclassified, at a severity below the default threshold.
- **A baseline is not recorded in provenance.** Baselined findings appear in every report
  but not as decisions in the record, because a snapshot nobody reviewed is not a
  decision. An auditor reading only the record will not see them.
- **The twenty rule classes and their severities encode security judgement** and have not
  been reviewed by a second person. They are the thing a user is trusting.
- **`verify` re-initialises terraform once per patch attempt.** `TF_PLUGIN_CACHE_DIR`
  keeps that to a local copy rather than a download, so it is a cost and not a bug. The
  upgrade is a `TF_DATA_DIR` shared across runs and keyed by the workspace's provider
  requirements; measure before building it.
- **Benchmark token counts are estimates.** `LLMProvider.complete` returns text and does
  not carry the chat-completions `usage` block. The harness counts characters exactly and
  publishes `estimated_tokens` as characters/4, labelled as an estimate.
- **Plimsoll is not on PyPI**, so `uv tool install plimsoll` does not work yet. The
  publish workflow is ready; the name has not been reserved.

[Unreleased]: https://github.com/trh-ds/plimsoll/commits/main
