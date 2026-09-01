# Architecture

## The central idea

Two walls that do not move, with a language model between them.

The gate is deterministic and authoritative. It runs before the model to produce findings, and again after the model to judge its patch. Both invocations are the same code, the same adapters, the same configuration. The model's output is a proposal that must survive the second wall to reach a human.

Existing tools hand humans unverified model output for review. Plimsoll hands humans only output that has already passed the machine check.

## Pipeline

```
                  ┌──────────────────────────────┐
                  │  Workspace (Terraform dir)   │
                  └──────────────┬───────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │        Gate             │  ← deterministic, authoritative
                    │  Check adapters, fanned │
                    │  out, normalised        │
                    └────────────┬────────────┘
                                 │ Finding[]
                    ┌────────────▼────────────┐
                    │    ExceptionStore       │
                    │  drops known-accepted   │
                    └────────────┬────────────┘
                                 │ active Finding[]
                    ┌────────────▼────────────┐
                    │    ContextBuilder       │
                    │  rule text, HCL slice,  │
                    │  docs, exception history│
                    └────────────┬────────────┘
                                 │ FindingContext
                    ┌────────────▼────────────┐
                    │       Reasoner          │  ← the only LLM call site
                    │  explanation + patch    │
                    └────────────┬────────────┘
                                 │ Patch (unverified)
                    ┌────────────▼────────────┐
                    │      PatchVerifier      │
                    │  scratch copy → Gate    │  ← same gate, second wall
                    └────────────┬────────────┘
                       verified? │
              ┌──────────────────┼──────────────────┐
              │ no, retries left │ yes              │ no, exhausted
              ▼                  ▼                  ▼
        back to Reasoner    Decision(FIXED)   Decision(ESCALATED)
                                 │
                    ┌────────────▼────────────┐
                    │   ProvenanceRecorder    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │       Reporters         │
                    │  terminal · json · sarif│
                    │  · github-pr            │
                    └─────────────────────────┘
```

## Components

### Gate (`core/gate.py`)

Owns the adapter registry. On `run(workspace)` it queries each registered `Check` for availability, executes the available ones, normalises their output into `Finding` objects, deduplicates across sources, and returns a `GateResult` carrying findings plus a record of which checks ran and at what versions.

Deduplication matters: Checkov and a custom Rego rule will both flag a public S3 bucket. Two findings for one problem doubles the LLM calls and confuses the reviewer. The dedup key is `(resource_address, normalised_rule_class)`, with a mapping table from tool-specific rule IDs to internal rule classes.

The Gate is pure with respect to the workspace — it never writes to it.

### Check adapters (`checks/`)

```python
class Check(Protocol):
    name: str

    def is_available(self) -> ToolAvailability: ...
    def run(self, ctx: CheckContext) -> list[Finding]: ...
```

**Revised after implementation** — `run` takes a `CheckContext` rather than a path and a
config. Two adapters need the same `terraform plan` output, and passing loose arguments
meant either computing the plan twice or letting the two disagree about the flags it was
built with. The context carries the workspace, the config, a scratch directory, the run
deadline, and a `once()` cache, so every shared artifact is produced at most once per gate
run no matter how many adapters ask for it.

Each adapter shells out to one external binary, parses structured output, and maps it into `Finding`. Four in v0.1:

| Adapter | Binary | Output parsed | Notes |
|---|---|---|---|
| `terraform_validate` | `terraform` | JSON diagnostics | No credentials needed. Catches syntax and type errors, hallucinated attributes. |
| `terraform_plan` | `terraform` | JSON plan | Needs provider init. Works with dummy credentials for greenfield configs, or LocalStack. Catches hallucinated resource types and invalid references. |
| `checkov` | `checkov` | JSON results | The broadest rule coverage. Python package, so it can be a direct dependency rather than a subprocess if that proves more reliable. |
| `opa` | `opa` | `opa eval` JSON | Runs the bundled policy pack and any user policy directory against the plan JSON. |

Adding a fifth (Trivy, KICS) is one file plus one fixture. This is deliberate — it is the contribution shape the project wants.

**A missing binary is not an error.** `is_available()` returns false, the run continues, and the report states which checks were skipped. A user without Terraform installed still gets Checkov results.

A binary that is installed but had nothing to evaluate is a third state, distinct from
both. `opa` with no plan to work on did not fail — it never started, and it is recorded as
skipped rather than as a failure. A run where only skipped adapters were present still
exits 3.

#### The plan path (`checks/terraform_cli.py`)

`terraform plan` is what makes the rule pack possible and is also the only part of the
gate that needs an AWS account it will not be given. Four decisions hold it together:

**The workspace is never written to.** `terraform init` writes `.terraform/` and
`.terraform.lock.hcl` into its working directory. Plimsoll reports; it does not edit your
repository, and that promise has no exception for "but terraform needs it". So when the
workspace is not already initialised, terraform runs against a throwaway copy in the
run's scratch directory. When it *is* already initialised, and no injected file is
needed, it runs in place — the user did the work, and re-doing it in a copy would
re-download hundreds of megabytes to reach the same answer.

**Placeholder credentials, only where none are set.** `terraform plan` with
`-refresh=false` on resources that do not exist yet needs credentials to be *present*,
not valid. If the caller already has real ones — an access key, a profile, a container
role, a web identity token — they are used untouched. A CI job under a read-only role
produces a more accurate plan than any placeholder, and this tool has no business
silently swapping out a caller's identity.

**A provider override file, because placeholders alone are not enough.** The AWS provider
calls `sts:GetCallerIdentity` before it will plan anything, and with placeholder
credentials that returns `InvalidClientTokenId` and the whole plan fails — which would be
reported as a finding about your configuration when it is really a fact about this
machine having no AWS account. So `plimsoll_provider_override.tf` is written into the
throwaway copy, setting `skip_credentials_validation`, `skip_requesting_account_id`, and
`skip_region_validation`. Those three turn off checks about *the caller*. None of them
changes how any resource is planned, which is the only thing the rule pack reads.

It is written only when both conditions hold: no real credentials, and a default
`provider "aws"` block exists to merge into. Terraform's override files merge into an
existing block and error when there is none, so an aliased-only configuration gets the
honest plan failure instead of a broken one.

**For configurations that genuinely reach the API at plan time** — a `data` source
reading a real AMI or a hosted zone is the common case — none of the above helps, because
the call has to succeed rather than be skipped. Point the provider at
[LocalStack](https://localstack.cloud) with a `custom_endpoints` override and real-looking
test credentials, or run the job under a read-only role. Plimsoll needs no configuration
for either: `has_real_credentials()` sees the credentials and steps out of the way.

Provider downloads are cached in `TF_PLUGIN_CACHE_DIR` across runs, because the verifier
re-runs the gate once per patch attempt and each attempt would otherwise re-download the
provider. `init` is retried up to three times with backoff, but only when the failure
names a transport problem: a malformed `required_providers` block fails identically every
time, and retrying it spends the run budget three times over to print the same message.

### ExceptionStore (`core/exceptions.py`)

File-backed (`.plimsoll/exceptions.yaml`, committed to the repo). Each entry records rule, resource address, justification, approver, approving PR, and optional expiry.

Matching is on `(rule_class, resource_address)` with glob support on the address. An expired exception becomes active again and is reported as newly expired rather than silently re-raised — the difference matters to a reviewer who thought it was handled.

This component is small and unglamorous and probably determines whether anyone keeps the tool installed. The dominant reason policy-as-code gets disabled is the same finding reappearing after someone already decided it was acceptable.

### ContextBuilder (`core/context.py`)

Assembles what the model needs and nothing more. For a given finding:

- The exact HCL block that triggered it, plus surrounding lines
- The rule's source text — the Rego body, or the Checkov check's description
- The relevant benchmark clause where the rule maps to one
- Provider documentation for the resource type, from a bundled cache
- Prior accepted exceptions for the same rule class in this repo, as precedent

v0.1 uses direct lookup, not embedding retrieval. A rule ID maps to a rule; a resource type maps to a doc page. Vector search is a solution to a problem this does not have yet, and adding it early buys latency and a dependency in exchange for nothing.

### Reasoner (`core/reasoner.py`, `llm/`)

The only place a model is invoked.

```python
class LLMProvider(Protocol):
    name: str

    def is_available(self) -> ToolAvailability: ...
    def complete(self, system: str, user: str, max_tokens: int) -> str: ...
```

Implementations: `openai_compat` (covers agent routers, Groq, Cerebras, OpenRouter, vLLM, LM Studio, GitHub Models, a local Ollama, and OpenAI itself) and `null`. The `null` provider refuses every call and exists so tests and `--no-llm` traverse the same code path rather than a special case. `is_available` reuses `ToolAvailability` from the check adapters: an unconfigured model is the same class of fact as an uninstalled binary.

Inference is hosted by default and no endpoint ships as a default. Requiring a local model would make the project unusable on a laptop without a GPU, which is most laptops; see NFR1 in `product-requirements.md` for the trade.

Two prompts, both in `llm/prompts/` as files: one asking for an explanation, one asking for the corrected file. Separating them keeps each output format simple to validate. Output that is empty or unchanged is rejected before verification — a useless answer should not consume a verification run.

Every prompt is assembled by `core/context.py`, which is the **only** place workspace content becomes network payload, and every prompt is scrubbed by `core/redaction.py` on the way out. One egress point is the reason that scrubbing can be audited at all.

The Reasoner has no access to `Decision`. It cannot set status, cannot mark anything resolved, cannot see the final report. This is enforced by the type signature, not by convention.

### PatchVerifier (`core/verifier.py`)

Copies the workspace to a temp directory, applies the patch, and re-runs the Gate. Compares the resulting findings to the original set:

- Target finding gone, no new findings at or above its severity → `verified`
- Target finding gone, new finding introduced → `regressed`, discard
- Target finding remains → `ineffective`, discard
- Patch fails to apply or workspace fails to parse → `invalid`, discard

Anything other than `verified` returns to the Reasoner with the failure reason appended to context, up to `max_retries` (default 2). Then `ESCALATED`.

The scratch copy is essential. The verifier must never mutate the user's working tree.

### ProvenanceRecorder (`core/provenance.py`)

Builds one record per run containing run metadata, tool versions, model and provider identity, and every `Decision` with its full before/after state. The record is canonicalised (sorted keys, stable formatting) and hashed with SHA-256 so it can be referenced immutably.

Schema is versioned from day one and designed so a `signature` block can be added without breaking consumers. Sigstore keyless signing is v0.2; shipping the schema without it now is fine, shipping a schema that cannot accommodate it is not.

Secret redaction runs before serialisation. Provenance records get committed to repos and attached to PRs — a leaked credential in a "corrected value" field would be a serious own goal.

**Revised after implementation** — redaction moved earlier for findings. `Finding.message`
and `Finding.raw` are redacted by the model's own validator, at construction, not at the
serialisation boundary. A finding carries a checker's verbatim message and its unmodified
output, and either can contain a credential the checker found; redacting only on the way
into provenance would leave the secret in memory and on every other path, the terminal
report included. `Finding.id` is computed from the unredacted identity fields, so a
redacted finding still deduplicates and still matches an exception.

`Patch.new_content` is the one thing that does not enter a record at all. It is the whole
corrected file with secrets *restored* — that is what a patch has to be to be applied —
so it is excluded rather than redacted, and only `Patch.diff` is carried, through
`redact()`.

### Reporters (`reporters/`)

Pure functions from `RunResult` to output. Terminal (rich text), JSON, SARIF (for GitHub code scanning), and GitHub PR comment. Adding a reporter requires no changes elsewhere.

## Key decisions

**Why the same gate twice rather than a cheaper post-check.** A lighter second check is a different judge, which reintroduces exactly the gap the design exists to close. The cost is one extra gate run per patch, which is seconds. Not worth optimising away.

**Why the model cannot set status.** Enforced structurally rather than by discipline. `Reasoner.reason()` returns `Explanation | Patch`; only `VerifierLoop` constructs a `Decision`. An agent working in this codebase cannot casually break invariant 1 because the types will not allow it.

**Why file-backed exceptions rather than a database.** The exception list belongs in the repo, reviewed in PRs, versioned with the code it exempts. A database would be a backend, and a backend would be a hosting cost and a trust barrier.

**Why no cloud SDK.** Plimsoll reads HCL and shells out. It never authenticates to AWS itself. This keeps the security surface near zero and means a user can adopt it without a credentials conversation.

**Why subprocess rather than importing tools as libraries.** Version independence. Users run the Checkov and OPA versions their organisation has pinned. Checkov is the one plausible exception — it is a Python package and importing it would be faster — but a version conflict with the user's environment is a worse failure than a slow subprocess.

## Failure modes to design against

| Failure | Mitigation |
|---|---|
| Model produces a plausible patch that breaks something unrelated | PatchVerifier compares the full finding set, not just the target |
| Model's patch is syntactically valid but semantically wrong | `terraform plan` in the re-verify catches invalid references and types |
| Same finding re-raised after acceptance | ExceptionStore |
| Provider unreachable mid-run | Degrade to deterministic mode with a warning, complete the run |
| One check crashes | Per-adapter exception isolation; the run continues, the report says which check failed |
| Findings explode on a large module | Configurable cap on LLM-processed findings, prioritised by severity, remainder reported without explanation |
| Secret appears in a finding's value | Redaction pass before provenance serialisation and before any prompt is sent |
