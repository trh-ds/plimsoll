# Remaining work

Rewritten 2026-09-01. The previous version of this file was the task-level list for
finishing v0.1; everything on it that could be done from a keyboard has been done, and
what is left needs either credentials, a running Docker daemon, or the repository owner.

Delete this file when the list is empty.

---

## Where the tree is

Phases 1 through 4 of [`implementation_plan.md`](implementation_plan.md) are built.
`ruff`, `mypy src/`, `pytest -m "not slow"` (399), `pytest -m slow` (10), and
`opa test src/plimsoll/policies` (61) are green, and `opa check --strict` is clean.

Three things have **never been exercised for real**, and every document in the repository
says so:

| Unverified | Needs | First place it will run |
|---|---|---|
| A live model endpoint | An API key on a free tier | Anyone running `verify` |
| The Action's container image | A running Docker daemon | `action-image` in `integration.yml` |
| The benchmark | An API key and ten minutes | `benchmarks/runner.py` |

None of them blocks further work. All of them block anyone believing the tool works.

---

## 1. Run the benchmark

**This is the most valuable remaining task by a wide margin**, and it is the one thing
that turns the project from a design into a claim.

- [ ] Configure a free-tier endpoint and run `uv run python benchmarks/runner.py`.
- [ ] Fill in `benchmarks/BASELINE.md` from `results.json`. It is transcription, not
      recollection — every field the table asks for is in the metadata.
- [ ] Repeat on two more models. One model is an anecdote.
- [ ] Fill in `benchmarks/RESULTS.md`, including the delta and the escalation rate.
- [ ] Only then, rewrite the README's opening around the measured number.

`benchmarks/RESULTS.md` already lists the reasons the result might be weak — twelve
scenarios, scenarios written by the same person who wrote the rule pack, one model, and
"fixed by the gate" not meaning "correct". They were written down before any number
existed so they could not be chosen to avoid.

**Do not put a pass-rate figure anywhere until this is done.** `CLAUDE.md` says the same.

## 2. Watch the first integration run

The `integration.yml` and `action-image` jobs have never run. When they first do:

- [ ] Confirm the Action image builds and the pinned tool versions are all present.
- [ ] Confirm the SARIF schema validation passes against the live OASIS URL.
- [ ] Confirm `checkov` runs — it is broken locally with
      `ModuleNotFoundError: No module named 'checkov'`, so the adapter and its severity
      mappings have never been exercised end to end on this machine.
- [ ] Confirm the compliant corpus still exits 0 with checkov present. The KMS fixture
      change was made in anticipation of exactly that and is untested.

## 3. Needs the repository owner

- [ ] Reserve `plimsoll` on PyPI. `.github/workflows/publish.yml` is ready and needs a
      trusted-publishing entry: owner, repository, `publish.yml`, environment `pypi`.
- [ ] Create the `good first issue` label and file the three tickets `CONTRIBUTING.md`
      links to: Trivy adapter, KICS adapter, three additional rules.
- [ ] Enable GitHub Discussions and private security advisories — `SECURITY.md` directs
      reporters to advisories and that path must exist.
- [ ] **Review the twenty rule classes and their severities** in `checks/rule_map.py`.
      They encode security judgement, by one person, unreviewed, and they are the thing a
      user is trusting.
- [ ] Publish the Action to the Marketplace once a `v0` tag exists.

## 4. Smaller, and genuinely optional

- [ ] **Exception precedent in prompts.** The store exists and the prompt does not yet
      cite prior accepted exceptions. The last unticked box in Phase 3.
- [ ] **A third-party known-good corpus** for false-positive measurement.
      `tests/test_rule_corpus.py` asserts continuously against our own fixtures, which is
      a stronger guarantee than a sampled percentage but is still our own opinion of
      correct.
- [ ] **Real token counts.** The benchmark reports characters/4, labelled an estimate,
      because `LLMProvider.complete` returns text and does not carry the
      chat-completions `usage` block. Widening that protocol to serve a benchmark would
      put reporting machinery in the one interface that has to stay small — so if this is
      wanted, it wants a different design, not a wider return type.
- [ ] **`verify` re-initialises terraform per patch attempt.** Marked with a `ponytail:`
      comment in `terraform_cli._initialise`. `TF_PLUGIN_CACHE_DIR` keeps it to a local
      copy rather than a download, so it is a cost and not a bug. **Measure before
      building** the shared `TF_DATA_DIR`.
- [ ] **A prebuilt Action image.** `action.yml` builds on the runner, which costs a few
      minutes on a cold cache. Publishing to GHCR and switching `image:` to a `docker://`
      reference removes that without any caller changing a line.

---

## Verification, after every change

```powershell
uv run ruff check . --fix
uv run ruff format .
uv run mypy src/
uv run pytest -m "not slow"
opa check --strict src\plimsoll\policies
opa test src\plimsoll\policies
```

With terraform and opa on PATH:

```powershell
uv run plimsoll doctor
uv run plimsoll scan .\tests\fixtures\insecure_s3     # exit 1, eight findings
uv run plimsoll scan .\tests\fixtures\compliant_s3    # exit 0, no false positives
uv run pytest -m slow
```

A cold `terraform init` downloads the AWS provider and can exceed the 120s default on a
slow link. Raise `--check-timeout`; the message will tell you the same thing.

Commit feature-wise, one concern each, and append a `progress.md` entry per unit of work.
