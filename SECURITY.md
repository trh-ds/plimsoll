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
- **No backend, no database, no dashboard.** Nothing is uploaded to us. Output is
  stdout, files, and PR comments, and the exception store is a YAML file in your repo.
- **No network access except the model provider you configure.** Plimsoll ships with no
  default endpoint, so it cannot contact anything until you set `PLIMSOLL_LLM_BASE_URL`.
  `plimsoll scan` never contacts a provider at all, and `--no-llm` makes that
  unconditional for `verify` too.
- **Never writes to your workspace.** `verify` copies the workspace to a temporary
  directory, applies the patch there, and reports. Your files are not modified.
- **Unit tests make no network calls**, loopback included. Provider behaviour is tested
  against recorded responses with no socket opened.

### What leaves your machine when you run `verify`

This is the part worth reading before you point Plimsoll at a private repository.

`verify` and `explain` send Terraform source to whatever endpoint you configured:
`explain` sends the offending block, and `verify` sends the **whole file** containing the
finding, because a model cannot return a corrected file it has not seen. That endpoint is
yours to choose and yours to trust — Plimsoll has no opinion about it and no default.

Three mitigations are built in:

- **Everything passes through one redaction point.** `core/context.py` is the only place
  workspace content becomes network payload, and every prompt is scrubbed by
  `core/redaction.py` first. It matches keyed assignments (`secret_key = "..."`), AWS
  access key ids, GitHub, Slack and OpenAI-style tokens, Google API keys, JWTs, and PEM
  private key blocks. Redaction is deliberately over-eager: a false positive costs the
  model some context, a false negative posts a live credential to a third party.
- **`terraform.tfstate` is never sent**, at any size, for any finding. State holds every
  attribute of every deployed resource in plaintext, generated passwords included.
- **Files over 60,000 characters are refused** rather than truncated.

Redaction is pattern-based, so it is a mitigation and not a guarantee. A credential in a
shape none of those patterns match will be transmitted. If that is unacceptable for a
given repository, `plimsoll scan` and `plimsoll verify --no-llm` do useful work without
sending anything anywhere.

A gap worth naming: `Finding.raw` retains the source snippet an adapter reported, and is
not yet redacted on the reporting path. It is redacted on the prompt path. Closing that
is part of the provenance work.

## Dependencies

The runtime dependency surface is deliberately small. If you are reporting a
vulnerability in a dependency rather than in Plimsoll itself, an issue is fine — no
private advisory needed.
