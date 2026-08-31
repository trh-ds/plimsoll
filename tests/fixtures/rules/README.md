# Rule-pack corpus

Two Terraform workspaces. `violating/` trips every one of the twenty rule classes in
`checks/rule_map.PLIMSOLL`; `compliant/` is the same resources with every violation
fixed and trips none of them. `tests/test_rule_corpus.py` asserts exactly that, against
real `terraform` and `opa`, and is marked `slow`.

## Why two directories and not forty

Terraform merges every `.tf` in a directory into one configuration, so each side plans as
a unit and one `opa eval` sees all twenty classes at once. One directory per rule would be
forty `providers.tf` blocks, forty `terraform init` runs, and forty places for a rule
rename to be half-applied. The assertion `set(PLIMSOLL) - fired == set()` is also a
stronger claim than forty independent tests: it fails when someone adds a rule to the
table and no fixture for it, which is the failure worth catching.

| File | Rule classes |
|---|---|
| `providers.tf` | `provider.static_credentials` |
| `iam.tf` | `iam.action.wildcard`, `iam.resource.wildcard`, `iam.trust.wildcard_principal`, `iam.policy.admin_attached` |
| `s3.tf` | `s3.acl.public`, `s3.public_access_block.missing`, `s3.encryption.missing`, `s3.versioning.disabled`, `s3.logging.missing` |
| `network.tf` | `sg.ingress.world_ssh`, `sg.ingress.world_rdp`, `sg.ingress.world_open`, `sg.ingress.all_ports` |
| `datastore.tf` | `rds.encryption.missing`, `rds.public.enabled`, `rds.backup.disabled`, `ebs.encryption.missing` |
| `hygiene.tf` | `secret.hardcoded` |

`logging.cloudtrail.absent` fires on the absence of an `aws_cloudtrail` across the whole
directory, so it belongs to no single file: `violating/` has none and `compliant/`
has one.

## The compliant half is the one that earns its keep

A rule that fires is easy to write. A rule that fires *only* when it should is the one a
team leaves switched on, and this corpus has already caught one that did not: the AWS
provider types `encrypted` as a **string** on a launch template's block device, so plan
JSON carries `"true"` and not `true`. `ebs.encryption.missing` compared to `true` and
reported every correctly-encrypted launch template as unencrypted. See `device_encrypted`
in `policies/datastore.rego` and the regression test in `policies/datastore_test.rego`.

## Nothing here is a real credential

`violating/` needs credential-shaped literals for `secret.hardcoded` and
`provider.static_credentials` to have anything to find. The AWS key pair is the vendor's
own documented example pair; the passwords are passphrases from a webcomic. None of them
authenticates anywhere.

`compliant/` sources its passwords from `variable` blocks. Those carry defaults so the
fixture plans without a `.tfvars` file, and the defaults satisfy the providers' own
password-format validation, which runs at plan time. A default on a `variable` is not
what `secret.hardcoded` looks at — the rule walks resource expressions, and a resource
whose argument is `var.x` carries a reference rather than a `constant_value`. That is the
actual fix, not a way of hiding the literal from the rule.

## Rules

- **Do not widen a rule to make a fixture pass.** Fix the fixture. The one change to a
  rule this corpus has caused was a rule that was wrong about a provider's own type.
- **`terraform apply` is never invoked.** Neither directory is ever applied.
- Adding a rule to `PLIMSOLL` without adding a violation here fails
  `test_every_rule_class_in_the_pack_fires`, which is deliberate.
