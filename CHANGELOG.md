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

### Fixed

- **`terraform_validate` no longer reports an uninitialized workspace as a finding.**
  Without `terraform init` there is no provider schema, so terraform returns only
  "Missing required provider" and never reaches anything real in the file. The adapter
  mapped that to a HIGH finding, which made a check that verified nothing look like one
  that ran and found a single problem. It now raises, so the run reports that nothing was
  verified.

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
  carries the workspace, the config, and a memoized `plan_json()` so adapters that
  evaluate rules against `terraform plan` output can share one plan rather than each
  producing their own. Breaking for anyone who wrote an adapter against the old
  signature, which is the point of doing it while there is one adapter.
- Renamed the project from Bulwark to Plimsoll throughout, including the `PLIMSOLL_*`
  environment variables and the `.plimsoll/` exception store path.
- Replaced tfsec and Terrascan with Trivy and KICS wherever the documentation named a
  future check adapter. tfsec was merged into Trivy by Aqua in 2023 and Terrascan was
  archived in November 2025.

### Known gaps

- `exceptions` does not exist. It needs the exception store, so it is absent rather than
  stubbed. `verify` does not yet attest — `--provenance` is unimplemented.
- **The loop has little to reason about yet.** The bundled Rego pack is unwritten, so
  findings come only from `checkov` and `terraform validate`. The loop is built and
  tested; its real-world fix rate is unmeasured, and will stay unmeasured until there are
  rules and a benchmark harness.
- **No provider has been exercised against a live endpoint in this repository's history.**
  Request construction, response parsing, and every failure path are tested against
  recorded payloads with no socket opened, per the no-network-in-unit-tests rule. That is
  not the same as having talked to a real router.
- Redaction is pattern-based, so it is a mitigation and not a guarantee. A credential in
  a shape none of the patterns match will be transmitted.
- `Finding.raw` retains the source snippet an adapter reported and is not redacted on the
  reporting path, only on the prompt path.
- Two of the four v0.1 adapters exist. `opa` and `terraform_plan` are next.
- No plan provider is wired into `default_gate()` yet, so `ctx.plan_json()` is always
  `None` until the `terraform_plan` adapter lands.
- `--timeout` is per-check, not the whole-run budget `specifications.md` describes, and
  exit code 4 is not implemented.
- `rule_map.py` covers only the checkov IDs verified against real output. Everything else
  is reported unclassified, at a severity below the default threshold.
- `terraform_validate` needs the workspace initialized. It reports that it could not run
  rather than guessing, but nothing initializes the workspace for you yet.
- The bundled rule pack, the exception store, and provenance records do not exist. They
  are the remainder of Phase 1 and all of Phase 3 in
  [`implementation_plan.md`](implementation_plan.md).

[Unreleased]: https://github.com/trh-ds/plimsoll/commits/main
