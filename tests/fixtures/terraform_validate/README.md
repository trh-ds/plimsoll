# `terraform validate -json` fixtures

Recorded tool output, committed so adapter tests run with no binaries installed.

**Provenance: these three files are reconstructed against terraform's documented
`-json` diagnostic schema, not captured from a live `terraform validate` run.** No
terraform binary was available on the machine where the adapter was written. They must
be re-captured from a real run before Phase 1 is called done — the schema is right, but
only a real capture proves the parser against terraform's actual output.

To re-capture:

```bash
cd <a workspace with the violation>
terraform init -backend=false
terraform validate -json > path/to/fixture.json
```

| File | Covers |
|---|---|
| `valid.json` | Clean config. No diagnostics. |
| `unsupported_argument.json` | Hallucinated attribute on a resource — the dominant AI-generated HCL failure. |
| `mixed_diagnostics.json` | Error plus warning plus a file-level diagnostic with no `range` or `snippet`. |
