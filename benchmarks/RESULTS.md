# Results — baseline versus post-loop

**Not yet measured.** A stub, like [`BASELINE.md`](BASELINE.md), and for the same reason:
the harness exists and no run has happened. Nothing here may be summarised anywhere else
until the table has rows.

This is the file the project's central claim rests on. If the delta turns out to be
small, that belongs here too — a benchmark that can only produce good news is not a
benchmark.

## Method

Everything in [`BASELINE.md`](BASELINE.md), and then, over the same generated Terraform:

5. Run the full loop — `VerifierLoop`, the same one `plimsoll verify` runs, with the same
   gate, the same adapters, and the same config.
6. A scenario **passes post-loop** when no decision is left unresolved at or above the
   threshold. A finding counts as resolved only when a patch was re-verified through the
   gate that produced it. A patch that failed re-verification was discarded and is not in
   any number here (invariants 2 and 3).

Both halves are scored by the same gate on the same workspace, in one pass of
`runner.py`, so the two rates cannot drift apart on tool versions or on anything that
changed between two invocations.

## Results

_Empty until a run happens._

| Model | Baseline | Post-Plimsoll | Delta | Mean attempts/finding | Escalation rate |
|---|---|---|---|---|---|
| _not yet run_ | n/a | n/a | n/a | n/a | n/a |

`runner.py` prints this table in exactly this shape at the end of a run.

## False positives

Separate from the table above, and not optional. The rate to publish is what the gate
reports against Terraform that is already correct:

```bash
uv run plimsoll scan tests/fixtures/rules/compliant   # must exit 0
uv run plimsoll scan tests/fixtures/compliant_s3      # must exit 0
```

Both are asserted continuously — `tests/test_rule_corpus.py` fails if any rule in the
pack fires against the fixed corpus. That is a stronger guarantee than a percentage, and
it is the number a team actually cares about: a scanner that flags correct code gets
switched off, whatever its detection rate.

A corpus of third-party known-good Terraform would be a better measurement than the
project's own fixtures, and is not built.

## What would make this claim weak

Written down before the numbers exist, so the numbers cannot be chosen to avoid it:

- **Twelve scenarios.** Small enough to run on a free tier, small enough that one
  scenario is eight percentage points. Confidence intervals belong beside any delta drawn
  from a set this size.
- **The scenarios are ours.** They were written against the rule pack, by the same person
  who wrote the rule pack. A model that fails them may be failing this pack's opinions
  rather than a broadly held standard. An external dataset run through
  `--scenarios` is the check on that, and is the more credible number.
- **One model is an anecdote.** The claim needs three, and at least two on free tiers, or
  it describes one endpoint on one day.
- **Fixed by the gate is not fixed.** A patch that satisfies every rule in the pack can
  still be wrong in ways nothing here checks. The claim is "compliant with these rules",
  and it must never be written as "correct".
