# terraform validate fixtures

**Captured from real runs.** terraform 1.15.8, AWS provider `hashicorp/aws ~> 5.0`, on
Windows, 2026-08-30.

```bash
terraform init          # provider schema is required; see uninitialized.json
terraform validate -json
```

terraform emits relative filenames, so no path normalisation was needed. These files are
exactly what terraform printed.

| File | What it captures |
|---|---|
| `valid.json` | A clean config. `diagnostics` is an empty list. |
| `unsupported_argument.json` | One error with a `range` and a `snippet`, addressed only by `snippet.context`. The dominant AI failure mode: an attribute that does not exist. |
| `mixed_diagnostics.json` | Three errors — an invalid resource type, a reference to an undeclared resource, and one inside a `data` block, which exercises the `data.<type>.<name>` address path. |
| `deprecation_warnings.json` | Two `severity: "warning"` diagnostics, both carrying an `address` field. This is the case where the adapter prefers `address` over `snippet.context`. |
| `uninitialized.json` | `terraform validate` in a directory where `terraform init` was never run. |

## What the captures corrected

These fixtures replace hand-written ones reconstructed from terraform's documented JSON
schema. Two things the reconstruction had wrong:

1. **An uninitialized workspace returns exactly one diagnostic** — `Missing required
   provider` — with **no `range` and no `snippet`**, and terraform never reaches any real
   error in the file. The adapter previously mapped this to an ordinary HIGH finding, so
   a scan of a fresh CI checkout looked like a check that ran and found one problem while
   every actual violation stayed invisible. It now raises `CheckExecutionError`, which
   makes the run report that nothing was verified.
2. **The reconstructed warning did not exist.** It used the "interpolation-only
   expression is deprecated" warning, which terraform 1.15 no longer emits. Real
   warnings in this combination come from the provider — a deprecated argument or a
   deprecated resource — and unlike the reconstruction they carry an `address` field.

## Re-capturing

```bash
cd <a scratch directory>
# providers.tf declaring hashicorp/aws ~> 5.0, then the case under test in main.tf
terraform init
terraform validate -json > valid.json
```

Exit code 1 means diagnostics were reported, which is expected for every file here
except `valid.json`. `terraform apply` is not run, here or anywhere else in this project.
