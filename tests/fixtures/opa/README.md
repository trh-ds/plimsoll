# opa fixtures

**Captured from a real run.** opa 1.20.1, terraform 1.15.8, hashicorp/aws 5.100.0, on
Windows, 2026-09-01. Nothing here was written from documentation — the project's PR
template forbids that for new adapters and the project holds itself to it.

The capture is a full pipeline, because that is the only way the shapes are real:

```powershell
# in a scratch copy of the fixture, with the provider override written and
# placeholder AWS credentials set (see checks/terraform_cli.py for both)
terraform init -input=false -backend=false -no-color
terraform plan -json -no-color -input=false -lock=false -refresh=false -out=plan.tfplan
terraform show -json plan.tfplan > plan.json
opa eval --format json --data src/plimsoll/policies --input plan.json data.plimsoll.findings
```

| File | Captured from | Why it is here |
|---|---|---|
| `insecure_s3.json` | `tests/fixtures/insecure_s3` | Eight findings across five rule classes. The ordinary case. |
| `compliant_s3.json` | `tests/fixtures/compliant_s3` | One finding — `logging.cloudtrail.absent`, MEDIUM. Everything the pack covers about the buckets themselves is satisfied. |
| `no_findings.json` | a config with one `aws_sns_topic` | A clean result is `"value": []`, **not** an undefined query. |
| `undefined_query.json` | `opa eval` against an empty `--data` directory | `{}`, with no `result` key at all. This is what a packaging failure looks like, not a clean workspace. |
| `insecure_s3_plan.json` | the same fixture | The `terraform show -json` input the rules are evaluated against. Kept so a rule can be developed without running terraform. |

## What the captures taught us

**A clean workspace does not produce an undefined query.** `findings` is a partial set
rule, so it is always defined and simply empty. The `{}` shape happens when the rule
package is not loaded at all — which is a broken install, and is exactly the case that
must not be reported as "no findings". The two are distinguishable and the adapter
distinguishes them.

**`resource_address` is empty for whole-configuration rules.** `logging.cloudtrail.absent`
fires on something the configuration does *not* contain, so there is no block to point
at. `checks/locator.py` falls back to the first `.tf` file, which is what a human would
do; the gate declines to deduplicate findings with no address, because two of those are
not necessarily the same problem.

## Re-capturing

Re-run the pipeline above. The findings are order-dependent on OPA's set iteration, so
compare by rule class rather than by list index if you are diffing.
