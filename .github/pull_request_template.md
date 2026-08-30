<!--
Thanks for contributing. Delete any section that does not apply.
Small PRs with one concern each get reviewed fastest.
-->

## What this changes

<!-- One or two sentences. The diff says what; say why. -->

## Related issue

<!-- Fixes #123, or "none" for a small self-contained change. -->

## Checks

- [ ] `uv run ruff check .` clean
- [ ] `uv run ruff format .` applied
- [ ] `uv run mypy src/` clean
- [ ] `uv run pytest -m "not slow"` passes **with no terraform, checkov, or opa installed**
- [ ] `progress.md` updated, unless this is a typo fix
- [ ] Any doc this change contradicts (`architecture.md`, `specifications.md`, `implementation_plan.md`) updated in this PR

## Invariants

These are the six rules in [CLAUDE.md](../CLAUDE.md) that hold the project up. Confirm
the ones your change touches, and if you had to break one, say so here — it is a design
conversation, not an automatic rejection.

- [ ] No code path lets a model decide whether something is compliant
- [ ] Model-authored patches still go back through the *same* gate, unchanged
- [ ] No unverified patch can reach a human as a suggestion
- [ ] `--no-llm` still gives a working scanner
- [ ] No `terraform apply` anywhere, including tests and fixtures
- [ ] No secrets can reach a provenance record or a prompt

## For a new check adapter

- [ ] Fixtures are **captured from a real run**, not written against the schema
- [ ] Fixture directory has a README recording the tool version and the command
- [ ] Parsing is a pure function, tested directly, so unit tests need no binary
- [ ] `is_available()` returns unavailable rather than raising when the binary is missing
- [ ] Severity mapping is an explicit table in `checks/severity_map.py`
- [ ] `CheckSource` member added and the module imported in `checks/__init__.py`
- [ ] One `@pytest.mark.slow` test against the real binary, skipping when it is absent
