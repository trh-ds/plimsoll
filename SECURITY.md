# Security Policy

Plimsoll is a security tool that runs in CI. A defect in it can let an insecure
infrastructure change through while telling someone it was checked, which is worse than
not running it at all. Reports are taken seriously.

## Reporting a vulnerability

**Use GitHub's private reporting: go to the [Security tab](https://github.com/trh-ds/plimsoll/security)
and choose "Report a vulnerability".** This opens a private advisory visible only to the
maintainer.

Please do not open a public issue for a security report.

What helps, roughly in order:

- The version or commit you are on.
- A minimal Terraform fixture or tool output that reproduces it.
- What you expected the gate to do, and what it did.
- Whether it fails open (something insecure passes) or fails closed (something safe is
  blocked). Fail-open is the more serious direction.

**What to expect.** This is a pre-alpha project maintained by one person. You should get
an acknowledgement within a week. There is no bounty programme. If you would like credit
in the advisory and the changelog, say so and give the name you want used.

Please give a reasonable window to ship a fix before publishing. If you get no reply in
30 days, disclose — an unmaintained security tool is itself a finding worth publishing.

## Supported versions

| Version | Supported |
|---|---|
| `main` | Yes |
| Anything else | No — nothing is released yet |

Pre-alpha. There are no tagged releases and no backports; fixes land on `main`.

## What counts as a vulnerability here

Plimsoll's security properties are the invariants in [`CLAUDE.md`](CLAUDE.md). A way to
break one of these is a vulnerability, not a bug:

- **A finding is silently dropped.** The gate reports clean, or exits 0, when a check
  crashed, timed out, or never ran. Silence must never read as success — this is why
  exit code 3 exists.
- **An unverified patch reaches a human as a suggestion.** Anything that produces
  `FIXED` without `PatchVerification.outcome == "verified"`.
- **The model's output sets a decision status**, by any path.
- **A secret survives redaction** into a provenance record, a PR comment, or a prompt
  sent to a model provider. Provenance records get committed to repositories.
- **Patch application escapes the scratch copy** and mutates the user's working tree, or
  writes outside the temp directory via a crafted diff path.
- **Code execution** from crafted tool output, crafted HCL, or a crafted policy file.
- **A crafted Terraform configuration suppresses a finding** that would otherwise fire.

A missing rule, or a rule that does not catch a particular misconfiguration, is a normal
issue rather than a vulnerability — unless it is reachable by deliberately evading the
rule, in which case it is the last item above.

## Security posture

Things Plimsoll deliberately does not do, which bound what can go wrong:

- **No cloud credentials and no cloud SDK.** It reads HCL and shells out to CLIs. It
  never authenticates to AWS. `terraform plan`, when it lands, runs against dummy
  credentials or LocalStack.
- **`terraform apply` is never invoked.** Not in code, not in tests, not in fixtures.
  Plimsoll cannot create, modify, or destroy a cloud resource.
- **No backend, no database, no dashboard.** Nothing is uploaded anywhere. Output is
  stdout, files, and PR comments, and the exception store is a YAML file in your repo.
- **No network access except the model provider you configure** — and the default
  provider is a local Ollama instance, so the default configuration makes no outbound
  connection at all. `--no-llm` makes that guarantee unconditional.
- **Unit tests make no network calls.**

## Dependencies

The runtime dependency surface is deliberately small. If you are reporting a
vulnerability in a dependency rather than in Plimsoll itself, an issue is fine — no
private advisory needed.
