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
- **`checks/severity_map.py`** — explicit tool-severity to `Severity` tables. Severity is
  never inferred.
- **Community documentation** — `LICENSE` (Apache 2.0), `CONTRIBUTING.md` with the
  add-a-check-adapter walkthrough, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and pull
  request templates.

### Changed

- Renamed the project from Bulwark to Plimsoll throughout, including the `PLIMSOLL_*`
  environment variables and the `.plimsoll/` exception store path.
- Replaced tfsec and Terrascan with Trivy and KICS wherever the documentation named a
  future check adapter. tfsec was merged into Trivy by Aqua in 2023 and Terrascan was
  archived in November 2025.

### Known gaps

- No CLI. `plimsoll scan`, `verify`, `explain`, `exceptions`, and `doctor` are all
  unwritten, so the quick start in the README does not work yet.
- Only one of the four v0.1 adapters exists. `checkov`, `opa`, and `terraform_plan` are
  pending review of the `Check` interface.
- No deduplication across sources yet, and no `rule_classes.yaml`. Both land with the
  second adapter, where they can be tested against something.
- The bundled rule pack, the exception store, provenance records, and the reasoning loop
  are Phases 1–3 of [`implementation_plan.md`](implementation_plan.md) and do not exist.
- The committed `terraform validate` fixtures were reconstructed from terraform's
  documented JSON schema rather than captured from a live run. They need re-capturing.

[Unreleased]: https://github.com/trh-ds/plimsoll/commits/main
