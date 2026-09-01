# Benchmarks

The harness that produces the only number this project is entitled to advertise.

It does two things in one pass. It prompts a model for Terraform, scores the raw output
with the gate — that is the **baseline** — and then runs the full `verify` loop over the
same output and scores it again — that is the **post-loop** rate. The difference between
them is the headline.

## Running it

The harness uses the same three environment variables as the tool, so any
OpenAI-compatible endpoint works and a free-tier hosted one is enough. No GPU, no local
model, no paid inference.

```bash
export PLIMSOLL_LLM_BASE_URL=https://your-router.example/v1
export PLIMSOLL_LLM_MODEL=your-model-id
export PLIMSOLL_LLM_API_KEY=...

uv run python benchmarks/runner.py --dry-run          # check the wiring, spend nothing
uv run python benchmarks/runner.py                    # the full set
uv run python benchmarks/runner.py --only web-server-sg
uv run python benchmarks/runner.py --scenarios path/to/other-set.yaml
```

`terraform` and `opa` must be on `PATH`, and `checkov` should be. A run where an adapter
is missing is scored by fewer checks than a run where it is present, and the two are not
comparable — `results.json` records which ran, and the harness refuses to score a
scenario where none did.

Each run writes `benchmarks/runs/<timestamp>-<model>/`: the generated `main.tf` per
scenario, so a reader can see what was actually produced, and `results.json` with the
per-scenario detail, the summary, and the metadata.

## What is measured

| Column | Meaning |
|---|---|
| Baseline pass rate | Scenarios where the raw model output had no finding at or above `--fail-on` |
| Post-Plimsoll pass rate | The same, after the verify → fix → re-verify loop |
| Delta | Percentage points between them |
| Mean attempts per finding | Patch attempts, counted per finding rather than per scenario |
| Escalation rate | Findings the loop could not fix, over findings it tried to fix |

Alongside: deployability (does `terraform validate` accept it at all), which checks ran,
model and provider, temperature, the scenario set's digest and commit, wall clock, and
usage.

Three things about the method are deliberate:

**The gate scores both halves.** Nothing a model says sets a pass. A scenario passes
because `Gate` found nothing at the threshold, and for no other reason. This is invariant
1, and it applies to the benchmark exactly as it applies to the tool.

**Deployability is separate from compliance.** Terraform that does not parse produces no
findings and would otherwise score a perfect compliance rate on configurations that
cannot exist.

**A provider error is not a failure.** A router that rate-limits halfway through removes
that scenario from both numerators and the denominator, and the summary says how many.
Counting it as a compliance failure would attribute a network problem to a model.

## The scenario set

`scenarios.yaml` is twelve outcome-phrased requests covering all twenty rule classes in
the bundled pack. No scenario mentions encryption, logging, least privilege, or any other
control — a test asserts this. That omission is the experiment: a prompt that says "and
make it secure" measures instruction-following, and what this measures is what a model
writes when nobody thinks to ask.

`expect` records which controls each scenario was written to probe. It is documentation,
not an assertion: the gate decides what is a violation, here as everywhere.

Twelve is small on purpose. It runs on a free tier in a few minutes, which is the
difference between a benchmark contributors run and one they read about. `--scenarios`
points at a larger external set; the harness records that set's digest and git commit so
a run against it is reproducible.

## Status

**Nothing has been measured yet.** `BASELINE.md` and `RESULTS.md` are stubs and say so.
No pass-rate figure belongs in the README, or anywhere else, until they are filled in
from a real run — and Phase 0 was meant to ship before the agent existed and did not,
which makes this the one part of the plan where the temptation to guess is highest.

## Known gaps

- **Token counts are estimates.** `LLMProvider.complete` returns text; the
  chat-completions `usage` block is not carried through it, and widening that protocol to
  serve a benchmark would put reporting machinery in the one interface that has to stay
  small. The harness counts characters exactly and reports `estimated_tokens` as
  characters/4, labelled as an estimate everywhere it appears.
- **One model per run.** The per-model table is assembled by hand from several runs. A
  matrix runner is worth building once there is more than one model configured.
