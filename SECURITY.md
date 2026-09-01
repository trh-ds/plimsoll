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
  never authenticates to AWS. `terraform plan` runs against placeholder credentials, or
  LocalStack, or whatever credentials you already had — see below.
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

### The provider override file

To produce a plan, Plimsoll may write one file — `plimsoll_provider_override.tf` — into a
**throwaway copy** of your configuration in the run's scratch directory. It is never
written to your repository, and the copy is deleted when the run ends.

It contains three arguments and nothing else:

```hcl
provider "aws" {
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}
```

It exists because the AWS provider calls `sts:GetCallerIdentity` before it will plan
anything. With placeholder credentials that returns `InvalidClientTokenId` and the plan
fails, which would be reported as a problem with your configuration when it is a fact
about the machine having no AWS account. All three arguments relax checks about *the
caller*; none of them changes how any resource is planned, which is the only thing the
rule pack reads.

Two conditions must both hold before it is written:

- **No real credentials are present.** If `AWS_ACCESS_KEY_ID`, `AWS_PROFILE`,
  `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`, or `AWS_WEB_IDENTITY_TOKEN_FILE` is set, your
  identity is used exactly as it stands and nothing is injected. A CI job under a
  read-only role produces a more accurate plan than any placeholder, and this tool has no
  business silently swapping out a caller's identity.
- **A default `provider "aws"` block exists to merge into.** An aliased-only setup is not
  overridable, and gets the honest plan failure rather than a broken one.

The placeholder credentials themselves are inert strings (`AKIAPLIMSOLLDUMMYKEY0` and a
literal that says it is not a secret). They are set only where nothing was set already,
and they never reach a provenance record, a prompt, or a report.

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

**That gap is closed.** `Finding.message` and `Finding.raw` used to be redacted on the
prompt path only, so a credential an adapter had echoed back could still reach a terminal
report or a JSON file. Both are now redacted by `Finding`'s own validator, at
construction — there is no path on which an unredacted one exists, because one cannot be
built. `Finding.id` is derived from the unredacted identity fields, so redaction does not
change what deduplicates or what an exception matches.

`Patch.new_content` is the deliberate exception. It is the whole corrected file with
secrets *restored*, which is what it has to be in order to be applied, and it is excluded
from provenance records entirely rather than redacted. Only `Patch.diff` is recorded, and
it goes through `redact()` first.

## Dependencies

The runtime dependency surface is deliberately small. If you are reporting a
vulnerability in a dependency rather than in Plimsoll itself, an issue is fine — no
private advisory needed.
