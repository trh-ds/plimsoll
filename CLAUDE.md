# CLAUDE.md

Instructions for AI coding agents working in this repository.

## What this project is

Plimsoll is a verification agent for AI-generated Infrastructure-as-Code. It sits in CI between a code-generating agent and a cloud account, finds policy violations, drafts fixes, **re-verifies those fixes with deterministic tools**, and emits a provenance record for every decision.

Read `architecture.md` before writing code. Read `specifications.md` before changing any schema.

## The invariants

These are not style preferences. Breaking one of them breaks the project's reason to exist. If a task appears to require breaking one, stop and say so instead of proceeding.

1. **The LLM never decides whether something is compliant.** Only `Check` adapters decide. The model's output is a *proposal*; the gate is the *judge*. There is no code path where a model's assessment sets `Decision.status`.

2. **Every LLM-authored patch is re-run through the same gate that produced the finding.** Same code path, same adapters, same config. Not a lighter check, not a second model, not a heuristic.

3. **A patch that fails re-verification never reaches a human as a suggestion.** It is discarded. After `max_retries` the run escalates with the *original* finding and an explicit "could not fix" status.

4. **Deterministic-only mode must always work.** Running with `--no-llm` gives a functioning policy scanner. If the LLM provider is unreachable, the tool degrades to scanning — it never fails open, and it never blocks on inference.

5. **`terraform apply` is never invoked.** Not in code, not in tests, not in examples. This is a pre-deployment tool. If you find yourself needing `apply`, the design is wrong.

6. **No secrets in provenance records.** Provenance captures rule IDs, resource addresses, before/after values, and approver identity. Redact anything matching a secret pattern before writing.

## Repository layout

```
src/plimsoll/
  checks/         Check adapters (terraform_validate, terraform_plan, checkov, opa)
  llm/            LLMProvider implementations (ollama, openai_compat, anthropic, null)
  core/
    models.py     Pydantic schemas — Finding, Patch, Decision, ProvenanceRecord
    gate.py       Runs checks, aggregates findings
    loop.py       The verify -> reason -> re-verify -> escalate loop
    exceptions.py ExceptionStore
    provenance.py Record construction and hashing
  reporters/      github_pr, sarif, json, terminal
  cli.py          Typer entrypoint
policies/         Bundled Rego rules
tests/
  fixtures/       Terraform configs with known violations
action/           Dockerfile + action.yml for the GitHub Action
```

## Commands

```bash
uv sync --all-extras          # install
uv run pytest                 # tests
uv run pytest -m "not slow"   # skip tests that shell out to terraform
uv run ruff check . --fix     # lint
uv run ruff format .          # format
uv run mypy src/              # types
uv run plimsoll doctor                              # which external tools are installed
uv run plimsoll scan ./tests/fixtures/insecure_s3   # manual smoke test, exits 1
uv run plimsoll scan ./tests/fixtures/compliant_s3  # the false-positive check, exits 0
```

Run `ruff`, `mypy`, and `pytest` before declaring any task complete.

## Code conventions

- Python 3.11+. Full type annotations on every public function. `mypy --strict` on `src/`.
- Pydantic v2 for all data crossing a boundary (adapter output, provenance, config).
- Adapters shell out via `subprocess.run` with an explicit timeout and `check=False`. Parse structured output (JSON) — never scrape human-readable text.
- A missing external tool is not an error. `Check.is_available()` returns `False`, the run logs it and continues with the remaining checks. Report which checks ran in the output.
- Prompts live in `src/plimsoll/llm/prompts/` as `.txt` files, not inline strings. They are content, not code.
- No network calls in unit tests. LLM tests use the `null` provider or recorded fixtures.

## Things to not do

- Do not add cloud provider SDKs (boto3 etc.). This tool reads HCL and shells out to CLIs. It does not talk to AWS.
- Do not add a web server, dashboard, or database. Output is stdout, files, and PR comments.
- Do not add a second cloud or IaC format until the Terraform + AWS path is complete and benchmarked. Multi-cloud is the most common way this kind of project dies.
- Do not hardcode a model name or provider anywhere outside `src/plimsoll/llm/`.
- Do not widen a rule's scope to make a test pass. Fix the test fixture.

## Working style

Small commits, one concern each. Conventional commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).

When you finish a unit of work, append an entry to `progress.md` under the current phase — what changed, what's now unblocked, what you hit that wasn't in the plan.

If a task in `implementation_plan.md` turns out to be wrong or mis-scoped, say so before building it. The plan is a hypothesis, not an instruction.
