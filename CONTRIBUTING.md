# Contributing to Plimsoll

Thanks for looking. This project is designed to be contributed to in two specific
shapes, and both are self-contained enough to finish in an evening.

> **Current state: pre-alpha.** Phase 1 of `implementation_plan.md` is partly done —
> the models, the gate, and one adapter (`terraform_validate`) exist. There is no CLI
> yet, and the rule pack is not written. If you want to add an adapter now, the
> interface it plugs into is real and tested; if you want to add a rule, the harness
> for it does not exist yet, so open an issue first and we will sequence it.

## What is most useful

| Contribution | Effort | Where |
|---|---|---|
| **A new check adapter** — Trivy, KICS, or anything with structured output | One module, one test, one fixture | `src/plimsoll/checks/` |
| **A policy rule** for the bundled Rego pack | One rule, two fixtures, one mapping | `policies/` *(not yet created)* |
| A bug report with a reproducing Terraform fixture | Small | Issues |
| Documentation that was wrong or unclear | Small | Anywhere |

Adapters and rules are the point. The architecture exists to make them cheap, and if
one of them is not cheap, that is a bug in the architecture and worth reporting as one.

## The invariants

Six rules hold this project up. They are listed in full in [`CLAUDE.md`](CLAUDE.md), and
a change that breaks one will not be merged regardless of how good it is otherwise:

1. **The model never decides whether something is compliant.** Only `Check` adapters do.
2. **Every model-authored patch is re-run through the same gate that produced the finding.**
3. **A patch that fails re-verification never reaches a human as a suggestion.**
4. **Deterministic-only mode must always work.** `--no-llm` is a working scanner.
5. **`terraform apply` is never invoked.** Not in code, not in tests, not in fixtures.
6. **No secrets in provenance records.**

If a change you want to make seems to require breaking one, open an issue and say so.
That is a design conversation, not a rejected PR.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.11+. That is all — terraform,
checkov, and opa are optional, and the test suite is built to pass without them.

```bash
git clone https://github.com/trh-ds/plimsoll
cd plimsoll
uv sync
```

Before you push anything:

```bash
uv run ruff check . --fix
uv run ruff format .
uv run mypy src/
uv run pytest -m "not slow"
```

All four must be clean. CI runs the same commands on Python 3.11, 3.12, and 3.13, on a
runner with none of the external binaries installed.

`uv run pytest` without the marker filter also runs the `slow` tests, which shell out to
real binaries and skip themselves when the binary is absent.

## Walkthrough: add a check adapter

An adapter wraps one external tool. It answers two questions — "are you installed?" and
"what did you find?" — and nothing else. It does not decide severity policy, does not
deduplicate, does not know about the LLM, and never writes to the workspace.

Use [`src/plimsoll/checks/terraform_validate.py`](src/plimsoll/checks/terraform_validate.py)
as the reference implementation. It is about 150 lines including comments.

### 1. Know the interface

```python
class Check(Protocol):
    name: str
    def is_available(self) -> ToolAvailability: ...
    def run(self, workspace: Path, config: CheckConfig) -> list[Finding]: ...
```

`run` returns findings or raises. It does not report skips or versions — those are the
gate's business. Raise `CheckExecutionError` for anything that went wrong with the tool
itself; the gate catches it, records a `FailedCheck`, and continues with the other
adapters. One crashing check must never kill the run, and there is a test that proves it.

### 2. Capture real tool output

Run the tool by hand against a Terraform config with a known violation, and commit its
JSON to `tests/fixtures/<your_tool>/`.

```bash
cd tests/fixtures/some_violating_config
trivy config --format json . > ../../fixtures/trivy/public_bucket.json
```

**Capture it from a real run.** Hand-written JSON that matches your reading of the
schema will pass your tests and fail on the tool's actual output. Commit at least a
clean case, a violating case, and one awkward case — a finding with no line number, or
one the tool reports at file level rather than resource level.

Add a short `README.md` to your fixture directory recording the tool version the output
came from and the command that produced it.

### 3. Write the adapter

Keep parsing separate from the subprocess call:

```python
def parse_output(payload: Mapping[str, Any]) -> list[Finding]: ...   # pure, tested directly

@register("trivy")
class TrivyCheck:
    name = "trivy"
    def is_available(self) -> ToolAvailability: ...
    def run(self, workspace: Path, config: CheckConfig) -> list[Finding]: ...
```

That split is why the tests need no binaries. Every mapping decision is exercised
against the committed JSON; only the thin `subprocess` shell goes untested in the unit
run, and a `slow` test covers that.

Rules for the shell-out:

- `subprocess.run` with `capture_output=True`, `text=True`, `check=False`, and an
  explicit `timeout=config.timeout_seconds`.
- Always request structured output. Never scrape human-readable text — if the tool has
  no JSON mode, say so on the issue before writing the adapter.
- A non-zero exit code usually means "found violations", which is the interesting case.
  Only unparseable output is a failure.
- `is_available()` returning `available=False` is normal, not an error. Include the
  version when you can get it: it goes into the provenance record.

### 4. Register the pieces

- Add a member to `CheckSource` in `src/plimsoll/core/models.py`.
- Import your module in `src/plimsoll/checks/__init__.py` so the registry picks it up.
- Add the tool's severity vocabulary to `src/plimsoll/checks/severity_map.py` as an
  explicit table. **Never infer severity**, and never map an unrecognised value to
  `INFO` — an unknown severity should stay visible, not get buried.
- Map the tool's rule IDs to internal `rule_class` values in `checks/rule_classes.yaml`
  *(created with the second adapter)*. `rule_class` is what deduplication and exception
  matching key on; `rule_id` is what gets displayed.

### 5. Test it

Mirror `tests/test_terraform_validate.py`. At minimum:

- The clean fixture produces no findings.
- The violating fixture maps to the right `rule_class`, `severity`, `resource_address`,
  file, and line.
- The awkward fixture parses instead of crashing.
- `is_available()` returns unavailable when the binary is missing (`monkeypatch` on
  `shutil.which`).
- Unparseable output raises `CheckExecutionError`.
- One `@pytest.mark.slow` test that runs the real binary and skips when it is absent.

## Walkthrough: add a policy rule

Not yet possible — `policies/` and the Rego evaluation path land with the `opa` adapter.
The shape each rule will need is already fixed in `specifications.md`:

- a Rego implementation evaluated against `terraform plan` JSON,
- a violating fixture and a compliant fixture,
- a `rule_class` and a `benchmark_refs` mapping,
- a remediation hint.

If you want to write one now, open an issue and we will get the harness in ahead of it.

## Testing conventions

- **No network calls in unit tests. Ever.** Adapter tests read committed JSON. LLM tests
  use the `null` provider or recorded fixtures.
- **No `terraform apply`.** Not in code, not in tests, not in fixtures. This is a
  pre-deployment tool; if a test seems to need `apply`, the test is wrong.
- **No cloud SDKs.** Plimsoll reads HCL and shells out to CLIs. It never authenticates
  to AWS. A PR that adds `boto3` will be closed.
- Tests that shell out to a real binary are marked `@pytest.mark.slow` and skip
  themselves when the binary is absent.
- Do not widen a rule's scope to make a test pass. Fix the fixture.

## Commits and pull requests

- Conventional prefixes: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
  Scope them where it helps — `feat(checks):`, `fix(core):`.
- Small commits, one concern each.
- Explain *why* in the commit body. The diff already says what.
- Append an entry to [`progress.md`](progress.md) for anything more than a typo fix. The
  "surprises" and "decisions" lines are the valuable part — git history reconstructs
  what was built, never why.
- If your change contradicts `architecture.md`, `specifications.md`, or
  `implementation_plan.md`, update that doc in the same PR. Docs that drift from the
  code are worse than no docs.

Open an issue before a large change. The plan is a hypothesis, and if it is wrong about
something you are better placed to see, that is worth knowing before you have written
the code.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions are accepted under the [Apache License 2.0](LICENSE), the same licence the
project ships under. You keep copyright on what you write.
