# checkov fixtures

**Captured from a real run.** checkov 3.3.15, on Windows, 2026-08-30.

```bash
checkov --directory <fixture> --output json --compact --quiet
```

One edit was made to the captured output: absolute paths were rewritten from the capture
machine's home directory to `C:\repo`, so the fixtures do not carry a developer's
username. Nothing else was touched — field order, null fields, and the Windows path
separators are all as checkov emitted them.

| File | Captured from | Why it is here |
|---|---|---|
| `insecure_s3.json` | `tests/fixtures/insecure_s3` | 17 failures across two resources. The ordinary case. |
| `compliant_s3.json` | `tests/fixtures/compliant_s3` | 6 failures on a config that satisfies the whole v0.1 rule pack. Checkov's defaults are broader than this project's scope; these are the leftovers, and they must not be classified as violations. |
| `nothing_found.json` | a config with only a `variable` block | A clean run emits **a bare summary object with no `results` key**. `payload["results"]` raises `KeyError` on it. |
| `multi_framework.json` | a config with a provider block holding literal credentials | Checkov emits a **JSON list** when more than one framework produces findings — here `terraform` and `secrets`. Any repository with a hardcoded credential produces this shape. |
| `rules_violating.json` | `tests/fixtures/rules/violating` | Captured on Linux, 2026-10-22, checkov 3.3.15, same command. Absolute paths rewritten from the checkout to `/repo`. The source for the security group, RDS, EBS, IAM trust, admin-attachment, and provider-credential mappings in `rule_map.py`: each was read off this file *and* matched to the Rego pack's finding on the same resource address in the same run. Also the source of the provider address rule: checkov names a provider block `aws.default`, the pack `provider.aws`. |

## What the captures taught us

Three things that a fixture written against the documented schema would have got wrong,
and that the adapter now handles:

1. **The top level is sometimes a list.** One framework gives an object, two or more give
   an array of objects. The `secrets` framework runs by default, so this is not an edge
   case — it is what a repository with a leaked key looks like.
2. **`severity` is always `null`.** Severity is a Bridgecrew platform feature; the
   community edition never populates it. Severity therefore comes from
   `checks/rule_map.py`, not from the tool.
3. **The `secrets` framework reuses `resource` for a SHA-1 hash** of the matched string
   rather than a terraform address.

`--compact` is not just a size choice. Without it, findings carry a `code_block` holding
the offending source lines — for a hardcoded-credential finding, that is the credential.
Note that the captured secrets finding contains no credential material, which is exactly
why the flag is passed in the adapter and not only here.

## Re-capturing

```bash
uv tool install checkov
checkov --directory tests/fixtures/insecure_s3 --output json --compact --quiet \
  > tests/fixtures/checkov/insecure_s3.json
```

Exit code 1 means findings were reported, which is the expected result for
`insecure_s3`. Re-normalise the absolute paths afterwards.
