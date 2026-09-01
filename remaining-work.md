# Remaining work

**Handoff document.** Written 2026-09-01 for a session starting cold on this repository.
Read [`CLAUDE.md`](CLAUDE.md) first for the invariants, then this for what is left and
what to watch out for.

Delete this file when the list is empty.

---

## Start here

```powershell
git log --oneline -14          # the session below ends at e62add1
uv sync
uv run pytest -m "not slow"    # 399, must be green before you change anything
uv run plimsoll doctor         # which of terraform, opa, checkov this machine has
```

If `doctor` shows `checkov` missing, that is expected on this machine — see
[Environment notes](#environment-notes).

---

## Where the tree is

Phases 1 through 4 of [`implementation_plan.md`](implementation_plan.md) are built.
`ruff`, `mypy src/`, `pytest -m "not slow"` (399), `pytest -m slow` (10), and
`opa test src/plimsoll/policies` (61) are green; `opa check --strict` is clean.

Three things have **never been exercised for real**. Every document in the repository says
so, and nothing you do should quietly imply otherwise:

| Unverified | Needs | First place it will run |
|---|---|---|
| A live model endpoint | An API key on a free tier | Anyone running `verify` |
| The Action's container image | A running Docker daemon | `action-image` in `integration.yml` |
| The benchmark | An API key and ten minutes | `benchmarks/runner.py` |

None of them blocks further work. All of them block anyone believing the tool works.

### What landed in the previous session

Do not redo any of this. `1cba0ba` was the starting point; `e62add1` is the tip.

| Commit | What |
|---|---|
| `cb467a0` | Compliant corpus encrypts with KMS, so it stays clean once checkov runs in CI |
| `ab0e2d8` | CI actions pinned to SHAs, `permissions: contents: read` |
| `fd94d4a` | SARIF `$schema` pointed at a 404; now the OASIS errata URL |
| `866c698` | `integration.yml` — real binaries, slow tests, `opa test`, SARIF validation, dogfood |
| `c96dec8` | `iam_test.rego`, `s3_test.rego`, `hygiene_test.rego` — 61 `opa test` cases, up from 19 |
| `1af7f98` | `terraform init` retries transport failures; network vs. configuration told apart |
| `664e713` | `--report FORMAT=PATH`, so one gate run emits several reports |
| `2771aa6` | The GitHub Action — Dockerfile, entrypoint, `post_comment.py`, root `action.yml` |
| `48d4255` | `publish.yml` — PyPI trusted publishing on tag |
| `eaea854` | `benchmarks/` — runner, 12 scenarios, stub BASELINE and RESULTS |
| `eef3296` | The retry above did not match the real failure. Fixed with the captured output |
| `26a17e5` | Every document brought back in line with the code |

---

## 1. Run the benchmark

**The most valuable remaining task by a wide margin.** It is the one thing that turns this
from a design into a claim, and it is ten minutes and a free-tier key.

```powershell
$env:PLIMSOLL_LLM_BASE_URL = "https://your-router.example/v1"
$env:PLIMSOLL_LLM_MODEL    = "your-model-id"
$env:PLIMSOLL_LLM_API_KEY  = "..."

uv run python benchmarks/runner.py --dry-run   # wiring only, spends nothing
uv run python benchmarks/runner.py
```

- [ ] Run it once and read `benchmarks/runs/<stamp>-<model>/results.json`.
- [ ] Fill in `benchmarks/BASELINE.md`. Every field its table asks for is in
      `meta` — this is transcription, not recollection.
- [ ] Repeat on two more models. One model is an anecdote.
- [ ] Fill in `benchmarks/RESULTS.md` with the delta, mean attempts per finding, and the
      escalation rate. `runner.py` prints that table in exactly the right shape.
- [ ] Only then, rewrite the README's opening around the measured number.

`benchmarks/RESULTS.md` already lists the reasons the result might be weak — twelve
scenarios, scenarios written by the same person who wrote the rule pack, one model, and
"fixed by the gate" not meaning "correct". They were written before any number existed so
they could not be chosen to avoid.

**Do not put a pass-rate figure anywhere until this is done.** `CLAUDE.md` forbids it.

If the delta turns out to be small, that is the result. Publish it. A low honest number is
the product.

## 2. Watch the first integration run

`integration.yml` and its `action-image` job have never run. On the first push:

- [ ] The Action image builds, and `doctor` inside it reports all four adapters `ok`.
- [ ] The SARIF schema validation passes against the live OASIS URL.
- [ ] `checkov` runs. It is broken locally, so the adapter, its severity mappings, and
      cross-tool deduplication between checkov and the Rego pack have never been
      exercised together anywhere.
- [ ] The compliant corpus still exits 0 **with checkov present**. `cb467a0` changed that
      fixture in anticipation of exactly this and could not be tested locally.
- [ ] `pytest -m slow` passes on a runner doing a cold provider download.

Expect something here to be wrong. Three of those five have never executed.

## 3. Needs the repository owner

- [ ] Reserve `plimsoll` on PyPI, and add a trusted-publishing entry: owner, repository,
      workflow `publish.yml`, environment `pypi`. The workflow is ready.
- [ ] Create the `good first issue` label and file the three tickets `CONTRIBUTING.md`
      links to: Trivy adapter, KICS adapter, three additional rules.
- [ ] Enable GitHub Discussions and private security advisories — `SECURITY.md` directs
      reporters to advisories and that path must exist.
- [ ] **Review the twenty rule classes and their severities** in `checks/rule_map.py`.
      They encode security judgement, by one person, unreviewed, and they are the thing a
      user is trusting.
- [ ] Publish the Action to the Marketplace once a `v0` tag exists.

## 4. Smaller, and genuinely optional

- [ ] **Exception precedent in prompts.** The store exists; `core/context.py` does not yet
      cite prior accepted exceptions. The last unticked box in Phase 3.
- [ ] **A third-party known-good corpus** for false-positive measurement.
      `tests/test_rule_corpus.py` asserts continuously against our own fixtures — a
      stronger guarantee than a sampled percentage, but still our own opinion of correct.
- [ ] **Real token counts.** The benchmark reports characters/4, labelled an estimate,
      because `LLMProvider.complete` returns text and does not carry the
      chat-completions `usage` block. If this is wanted it wants a different design, not a
      wider return type on the one interface that has to stay small.
- [ ] **`verify` re-initialises terraform per patch attempt.** Marked with a `ponytail:`
      comment in `terraform_cli._initialise`. `TF_PLUGIN_CACHE_DIR` keeps it to a local
      copy rather than a download, so it is a cost and not a bug. **Measure before
      building** the shared `TF_DATA_DIR`.
- [ ] **A prebuilt Action image.** `action.yml` builds on the runner, a few minutes on a
      cold cache. Publishing to GHCR and switching `image:` to a `docker://` reference
      removes that without any caller changing a line.

---

## Environment notes

Discovered the hard way in the previous session. Each of these costs an hour to
rediscover.

**Use PowerShell, not the Bash tool.** `bash -lc` hangs on this machine — a simple
`ls`/`find` did not return inside two minutes. Every command in this file is PowerShell.

**Commit with `git commit -F <file>`, not a here-string.** PowerShell here-strings passed
to `git commit -m @'...'@` broke apart on embedded quotes and turned the message body into
pathspecs. Write the message to a scratch file and pass `-F`.

**Do not round-trip source files through `Get-Content` / `Set-Content`.** Windows
PowerShell 5.1 guesses the encoding on read and mangles non-ASCII — it corrupted the emoji
in `reporters/github.py`'s test expectations and turned a green suite red. Use the editor
tools for file edits; use PowerShell for running things.

**`terraform init` is slow here and sometimes fails.** A cold AWS provider download can
exceed the 120s default per-check timeout, and this machine's IPv6 route to
`releases.hashicorp.com` intermittently times out. `--check-timeout 600 --timeout 900`
gets a scan through. The retry in `terraform_cli` now recognises the failure and the
message names the flag, but the underlying network is still the network.

**`checkov` is broken locally**: `checkov --version` fails with
`ModuleNotFoundError: No module named 'checkov'`. The adapter reports it missing and
continues, which is the designed behaviour, so scans work — but nothing checkov-related
can be tested end to end on this machine.

**Docker Desktop was not running** and could not be started non-interactively, so
`docker build -f action/Dockerfile .` was never executed. That is why the image is
unverified.

**Verify pinned SHAs before committing a workflow.** Action pins were checked against the
GitHub API and all resolved, but two of them had been written from memory and could easily
not have. `https://api.github.com/repos/<owner>/<repo>/commits/<sha>` is the check.

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

Commit feature-wise, one concern each, and append a `progress.md` entry per unit of work.
