# Baseline — raw-model compliance

**Not yet measured.** This file is a stub with its method fixed and its results empty.

That state is deliberate and is the honest one. Phase 0 of
[`implementation_plan.md`](../implementation_plan.md) was meant to publish this before any
of the agent existed, and it did not; the ordering is now unrecoverable. What is still
recoverable is that the number gets measured before it gets claimed. **No pass-rate figure
belongs in the README, the CHANGELOG, or any announcement until the results table below
has real rows in it.**

The harness is built and tested — `runner.py`, exercised by `tests/test_benchmarks.py`.
What is missing is a configured endpoint and a run.

## Method

The question: **when someone asks a model for infrastructure and does not think to
mention security, what do they get?**

1. Prompt the model once per scenario in [`scenarios.yaml`](scenarios.yaml), with a
   system message asking for a single `.tf` file and nothing else. No scenario mentions
   a security control; a test enforces this.
2. Write the response to a workspace as `main.tf`.
3. Run the deterministic gate over it: `terraform validate`, `terraform plan` + the
   bundled Rego pack via `opa eval`, and `checkov`.
4. A scenario **passes** when no finding reaches `--fail-on` (default `high`).
   Deployability — whether `terraform validate` accepts it at all — is recorded
   separately, because a configuration that does not parse produces no findings and
   would otherwise score as a compliance pass.

The gate decides. Nothing the model said about its own output is consulted.

## Reproducing

```bash
export PLIMSOLL_LLM_BASE_URL=... PLIMSOLL_LLM_MODEL=... PLIMSOLL_LLM_API_KEY=...
uv run python benchmarks/runner.py
```

See [`README.md`](README.md). A free-tier hosted endpoint is sufficient; no GPU and no
local model are required by any part of this.

## Results

_Empty until a run happens._

| Model | Scenarios | Deployable | Baseline pass rate | Est. tokens |
|---|---|---|---|---|
| _not yet run_ | | | | |

## To record alongside the table

Every one of these comes out of `results.json`, so filling the table in is transcription
rather than recollection:

- Model identifier and provider base URL, and the date of the run
- `terraform`, `opa`, and `checkov` versions, and which adapters actually ran
- Scenario set digest and commit (`meta.dataset`)
- Plimsoll commit (`meta.plimsoll_commit`)
- Wall clock and estimated token usage
- How many scenarios were skipped on a provider error, and why
