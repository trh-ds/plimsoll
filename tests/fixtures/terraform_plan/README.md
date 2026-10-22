# terraform plan fixtures

**Captured from a real run.** terraform 1.15.8, hashicorp/aws 5.100.0, on Windows,
2026-09-01.

```powershell
terraform plan -json -no-color -input=false -lock=false -refresh=false -out=plan.tfplan
```

`terraform plan -json` is line-delimited JSON: one message object per line, of which only
`type == "diagnostic"` carries anything the adapter reports.

| File | Captured from | Why it is here |
|---|---|---|
| `insecure_s3.jsonl` | `tests/fixtures/insecure_s3` | A successful plan. Every line is `version`, `planned_change`, or `change_summary` — **no diagnostics at all**. A plan that succeeds produces no findings from this adapter, and that is the normal case. |
| `provider_diagnostics.jsonl` | a config with an invalid IAM policy document | The provider's own validation, which `terraform validate` never reaches. Carries `address`, `range`, and `snippet`. |
| `clean.jsonl` | a config with one `aws_sns_topic` | The smallest successful plan. |
| `rejected_credentials.jsonl` | `tests/fixtures/insecure_s3`, with the AWS documentation's example access key exported | The provider refusing the caller before any resource is planned. `address` is the provider block, never a resource. Captured on Linux, 2026-10-22, same terraform and provider versions. |

## What the captures taught us

**The diagnostic `snippet` contains the offending source line.** For a hardcoded-credential
finding that line *is* the credential, and the adapter puts the whole diagnostic into
`Finding.raw`. This is why `Finding` redacts `raw` in a model validator at construction
rather than at each serialisation boundary: there is no moment at which an unredacted
finding exists, so no future reporter can leak one by forgetting.

**A failed plan is not a crash.** An AI-generated configuration that cannot plan is
precisely the case this tool is for. The diagnostics explaining why become findings, and
`plan_json` is `None` so the `opa` adapter reports itself skipped rather than evaluating
rules against nothing.

**Non-JSON lines happen.** Terraform occasionally writes plain text to the same stream.
Losing a whole plan over one stray line would be a poor trade, so the parser skips
anything that does not start with `{`.

**Rejected credentials are the runner's problem, not a finding.** When real-looking
credentials are present, the placeholder override is rightly not written, and if AWS
refuses them the whole plan fails on `sts:GetCallerIdentity`. Reported as a diagnostic,
that became a HIGH violation pinned to the `provider` line and the Rego pack skipped
silently. `credential_failure` recognises it and the adapter fails as a check instead.
