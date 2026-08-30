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
  request templates.

### Changed

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

- `verify`, `explain`, and `exceptions` do not exist. They need the reasoning loop, the
  exception store, and provenance, so they are absent rather than stubbed.
- Two of the four v0.1 adapters exist. `opa` and `terraform_plan` are next.
- No plan provider is wired into `default_gate()` yet, so `ctx.plan_json()` is always
  `None` until the `terraform_plan` adapter lands.
- `--timeout` is per-check, not the whole-run budget `specifications.md` describes, and
  exit code 4 is not implemented.
- `rule_map.py` covers only the checkov IDs verified against real output. Everything else
  is reported unclassified, at a severity below the default threshold.
- The bundled rule pack, the exception store, provenance records, and the reasoning loop
  are Phases 1–3 of [`implementation_plan.md`](implementation_plan.md) and do not exist.
- The committed `terraform validate` fixtures were reconstructed from terraform's
  documented JSON schema rather than captured from a live run. They need re-capturing.

[Unreleased]: https://github.com/trh-ds/plimsoll/commits/main
