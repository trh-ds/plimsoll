# Specifications

Precise contracts. Change these deliberately and version them; other things depend on the shapes here.

## Core data models

All models are Pydantic v2. `schema_version` fields are mandatory on anything serialised to disk.

### Severity

```
CRITICAL | HIGH | MEDIUM | LOW | INFO
```

Mapped from source tools via an explicit table in `checks/severity_map.py`. Never inferred by a model.

### Finding

```python
class Finding(BaseModel):
    id: str  # stable hash of (source, rule_id, resource_address, file)
    source: CheckSource  # terraform_validate | terraform_plan | checkov | opa
    rule_id: str  # tool-native, e.g. "CKV_AWS_145"
    rule_class: str  # internal normalised class, e.g. "s3.encryption.missing"
    severity: Severity
    resource_address: str  # "aws_s3_bucket.reports"
    resource_type: str  # "aws_s3_bucket"
    file: Path
    line_start: int
    line_end: int
    message: str  # tool's own message, verbatim
    benchmark_refs: list[str]  # ["CIS-AWS-2.1.1"]
    also_reported_by: list[CheckSource]  # other tools deduplicated into this finding
    raw: dict  # unmodified tool output for this finding
```

`also_reported_by` exists so provenance can say "Checkov and OPA both flagged this".
Dropping the duplicate silently would lose that.

`rule_class` is what deduplication and exception matching key on. `rule_id` is what gets displayed. The mapping table is the single place tool-specific IDs meet internal semantics.

**Revised after implementation** — the table was specified as `checks/rule_classes.yaml` and is `checks/rule_map.py`. A YAML file would have needed loading, validating, and shipping in the wheel to express a constant dictionary, and a typo in it would surface at scan time rather than at import. It carries both directions: `CHECKOV` maps a check id to a rule class and severity, and `PLIMSOLL` carries the twenty bundled rule classes with their severities and benchmark references.

**Also revised** — `message` and `raw` are redacted by `Finding`'s own validator, at construction, rather than on the way into a provenance record. A finding holds a checker's verbatim message and its unmodified output, and both can contain a credential the checker found. Redacting at the serialisation boundary would leave the secret in memory and in every path that did not go through that boundary — the terminal report among them. `id` is computed from the unredacted identity fields and is unchanged by redaction, so a redacted finding still deduplicates and still matches an exception.

### Patch

```python
class Patch(BaseModel):
    target_finding_id: str
    attempt: int
    files_touched: list[Path]
    new_content: str  # the complete corrected file, as returned by the model
    diff: str  # unified diff, computed from new_content by difflib
    rationale: str  # one paragraph, model-authored
```

**Revised after implementation.** This originally carried only `diff`, with the model
asked to emit a unified diff directly. Models produce diffs with wrong hunk headers and
drifting context lines often enough that the diff applier becomes the component that
fails, and every malformed diff burns a retry without ever testing whether the model
understood the rule. Whole-file output moves the formatting burden onto code that cannot
get it wrong. The diff is still what a human reads and what provenance records.

### PatchVerification

```python
class PatchVerification(BaseModel):
    outcome: Literal["verified", "regressed", "ineffective", "invalid"]
    target_resolved: bool
    new_findings: list[Finding]
    removed_findings: list[Finding]
    failure_reason: str | None  # fed back into the retry prompt
```

### Decision

Constructed only by `VerifierLoop`. Never by a reasoner, never by a check.

```python
class Decision(BaseModel):
    finding: Finding
    status: DecisionStatus  # PASS | FIXED | ESCALATED | EXEMPTED | BLOCKED | REPORTED
    explanation: str | None
    patch: Patch | None  # present only when status == FIXED
    verification: PatchVerification | None
    attempts: int
    exception_ref: ExceptionRef | None  # arrives with the exception store
```

| Status | Meaning |
|---|---|
| `PASS` | No violation. |
| `FIXED` | Violation found, patch produced, patch passed re-verification. |
| `ESCALATED` | Violation found, attempted, no verified patch after `max_retries`. Human required. |
| `EXEMPTED` | Matched a recorded exception. Reported, not counted as failure. |
| `BLOCKED` | Violation at or above the threshold that was never attempted — `--no-llm`, no provider, or past `--max-llm-findings`. The run fails. |
| `REPORTED` | Violation below the threshold. Never sent to a model. The run does not fail on it. |

`REPORTED` was added during implementation. The original five had no honest home for "a
real violation, below `--fail-on`, never attempted": `PASS` means no violation, which
would be a lie, and `BLOCKED` means the run fails, which it does not. A tool whose thesis
is that silence must not read as success cannot mislabel its own output.

`FIXED` is only reachable through `PatchVerification.outcome == "verified"`. This is asserted in code, not merely intended.

### ProvenanceRecord

```python
class ProvenanceRecord(BaseModel):
    schema_version: Literal["0.1"]
    run_id: str  # UUIDv4
    timestamp: datetime  # UTC, ISO 8601
    workspace_hash: str  # sha256 over sorted file digests
    repository: RepoContext  # remote, commit, branch, pr_number
    generated_by: str | None  # "github-copilot" | "claude-code" | "human" | "unknown"
    tooling: list[ToolVersion]  # name + version for every check that ran
    checks_skipped: list[SkippedCheck]
    model: ModelIdentity | None  # provider, model, params — null in deterministic mode
    decisions: list[Decision]
    summary: RunSummary
    content_hash: str  # sha256 of canonical JSON of all fields above
    signature: Signature | None  # reserved, v0.2 (Sigstore keyless)
```

Canonicalisation: sorted keys, UTF-8, no insignificant whitespace, `content_hash` excluded from its own input.

`generated_by` is best-effort in v0.1 — read from a commit trailer, PR label, or the `--generated-by` flag. Detecting AI authorship reliably is unsolved; recording the claim when it is available is still more than any current tool does.

**Redaction runs before serialisation.** Any value matching the secret patterns in `core/redaction.py` is replaced with `<redacted:{pattern_name}>` in both `original_value` and `corrected_value` positions.

### Exception entry

`.plimsoll/exceptions.yaml`, committed to the repository:

```yaml
version: "0.1"
exceptions:
  - rule_class: "sg.ingress.world_open"
    resource_address: "aws_security_group.public_alb"
    justification: "Public ALB, intentional. Reviewed in security sync 2026-03-14."
    approved_by: "trh-ds"
    approved_in: "PR #442"
    created: "2026-03-15"
    expires: "2026-09-15"     # optional
```

Matching is exact on `rule_class`, glob on `resource_address`. An expired entry produces a distinct `EXPIRED_EXCEPTION` report line — it is not silently re-raised, because a reviewer who thought this was handled deserves to be told it lapsed.

## CLI contract

```
plimsoll scan <path>       Deterministic only. No model. Fast.                    [built]
plimsoll verify <path>     Full loop: scan, explain, fix, re-verify, attest.      [built]
plimsoll explain <path>    Scan plus explanations, no patches.                    [built]
plimsoll exceptions        Subcommands: list, add, expire, prune.                 [built]
plimsoll doctor            Report which external tools and providers are available. [built]
```

### Shared flags

| Flag | Default | Meaning | Status |
|---|---|---|---|
| `--policy-dir` | none | Additional Rego to load alongside the bundled pack | built |
| `--fail-on` | `high` | Minimum severity that sets a non-zero exit | built |
| `--format` | `terminal` | `terminal` \| `json` \| `sarif` \| `github`, on stdout | built |
| `--report` | none | `FORMAT=PATH`, repeatable. Several reports from one run | built |
| `--provenance` | none | Path to write the JSON record | built |
| `--no-llm` | false | Force deterministic mode | built |
| `--max-retries` | 2 | Patch attempts per finding before escalation | built |
| `--max-llm-findings` | 25 | Cap on findings sent to the model, highest severity first | built |
| `--generated-by` | `unknown` | Recorded in provenance | built |
| `--timeout` | 600 | Whole-run budget in seconds; a run that exceeds it exits 4 | built |
| `--check-timeout` | 120 | Per-subprocess timeout, capped by `--timeout` | built |

**Added after this contract was written.** `--check-timeout` did not exist: `--timeout`
originally meant the per-subprocess value, which is a different promise. A workspace with
four adapters and three retries per finding can sit inside every per-check timeout and
still take twenty minutes, which in CI reads as a hang. The two are now separate flags,
and `--timeout` means what this document always said it meant.

`--report` was also added. Rendering SARIF, markdown, and JSON from one run matters
because the gate is a `terraform init`, a plan, and an `opa eval`: three formats would
otherwise cost three plans and could return three different answers.

### Environment

```
PLIMSOLL_LLM_PROVIDER    openai_compat (default) | null
PLIMSOLL_LLM_MODEL       provider-specific model identifier
PLIMSOLL_LLM_BASE_URL    the endpoint; required by openai_compat
PLIMSOLL_LLM_API_KEY     optional -- some gateways authenticate by network position
```

`openai_compat` reaches any endpoint speaking the OpenAI chat-completions API: agent
routers, GitHub Models, Groq, vLLM, LM Studio, a local Ollama, or OpenAI itself. One
adapter covers all of them, which is why vendor-specific providers are not built. An
endpoint speaking a different wire format needs a new provider module in
`src/plimsoll/llm/`, and nothing else.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No findings at or above `--fail-on` |
| 1 | Findings at or above threshold remain unresolved |
| 2 | Configuration error (bad policy dir, invalid flags) |
| 3 | No checks available to run — nothing was verified |
| 4 | Run exceeded `--timeout` |

Code 3 exists so a broken toolchain cannot masquerade as a clean pass. Silence must never read as success. Code 4 is checked before code 1 for the same reason: a run that timed out never exits 0, because partial verification is not verification.

## Bundled rule pack — v0.1

**Built.** Twenty rules covering the documented AI-generated IaC failure modes, in
`src/plimsoll/policies/` — not a top-level `policies/`, because a bundled pack that does
not ship in the wheel is not bundled. Each is Rego evaluated against `terraform plan`
JSON via `opa eval`, each maps to a `rule_class` in `checks/rule_map.py`, and each has:

- a case in the `*_test.rego` file beside it, run by `opa test` without terraform in the
  loop;
- a violation in `tests/fixtures/rules/violating/`, and the same resource fixed in
  `tests/fixtures/rules/compliant/`. A slow test asserts that every rule class fires
  against the first and none fires against the second.

`terraform_plan` and `opa` are both built. The plan path is shared plumbing in
`checks/terraform_cli.py`: one `init`, one `plan`, one `show` per gate run, cached on the
`CheckContext` so two adapters cannot disagree about the flags a plan was built with.
Plan JSON carries no file or line, so `checks/locator.py` maps a resource address back to
one.

### IAM

| `rule_class` | Severity | Detects |
|---|---|---|
| `iam.action.wildcard` | HIGH | `Action: "*"` or `"service:*"` in a policy document |
| `iam.resource.wildcard` | HIGH | `Resource: "*"` on a non-global action |
| `iam.trust.wildcard_principal` | CRITICAL | AssumeRole trust policy with `Principal: "*"` |
| `iam.policy.admin_attached` | HIGH | `AdministratorAccess` attached to a role or user |

### S3

| `rule_class` | Severity | Detects |
|---|---|---|
| `s3.acl.public` | CRITICAL | `public-read` or `public-read-write` ACL |
| `s3.public_access_block.missing` | HIGH | No `aws_s3_bucket_public_access_block` for the bucket |
| `s3.encryption.missing` | HIGH | No server-side encryption configuration |
| `s3.versioning.disabled` | MEDIUM | Versioning absent or explicitly disabled |
| `s3.logging.missing` | MEDIUM | No access logging configured |

### Network

| `rule_class` | Severity | Detects |
|---|---|---|
| `sg.ingress.world_ssh` | CRITICAL | `0.0.0.0/0` on port 22 |
| `sg.ingress.world_rdp` | CRITICAL | `0.0.0.0/0` on port 3389 |
| `sg.ingress.world_open` | HIGH | `0.0.0.0/0` on any other port |
| `sg.ingress.all_ports` | HIGH | Ingress rule spanning the full port range |

### Data stores

| `rule_class` | Severity | Detects |
|---|---|---|
| `rds.encryption.missing` | HIGH | `storage_encrypted` false or absent |
| `rds.public.enabled` | CRITICAL | `publicly_accessible = true` |
| `rds.backup.disabled` | MEDIUM | `backup_retention_period = 0` |
| `ebs.encryption.missing` | HIGH | Unencrypted EBS volume or launch template block device |

### Secrets and hygiene

| `rule_class` | Severity | Detects |
|---|---|---|
| `secret.hardcoded` | CRITICAL | Credential-shaped literal in HCL |
| `provider.static_credentials` | CRITICAL | `access_key` or `secret_key` literal in a provider block |
| `logging.cloudtrail.absent` | MEDIUM | No CloudTrail in a config that provisions IAM or S3 |

Each entry needs: Rego implementation, a violating fixture, a compliant fixture, a `benchmark_refs` mapping, and a remediation hint.

## LLM prompt contracts

Two prompts, in `llm/prompts/`, as `.txt` files. They are content, not code.

Both must produce usable output from a small model. A prompt that only works on a
frontier model is the wrong prompt: it makes the tool's cost a function of the model
rather than the workload, and it cannot be validated by a contributor on a free tier.

Every prompt is assembled by `core/context.py` and passes through `core/redaction.py` on
the way out. That is the only place workspace content becomes network payload.

### `explain.txt`

**Input:** rule class and id, source tool, severity, resource address, location, the checker's message, and the offending HCL block.
**Output:** prose, under 120 words, stating the rule, why this resource violates it, and the concrete risk. No markdown headers, no preamble.
**Must not:** claim the finding is a false positive, suggest suppressing the rule, or state a compliance verdict.

*Not yet supplied:* benchmark clause text and prior accepted exceptions. Both need the
rule pack and the exception store.

### `patch.txt`

**Input:** everything above, plus the full current file content and, on retry, the previous attempt's `failure_reason`.
**Output:** the complete corrected file and nothing else. No fences, no commentary.
**Constraints stated in the prompt:** touch only the file and resource named; change no unrelated attributes; introduce a new resource only when the rule requires one; preserve existing comments and formatting; never weaken another resource to make this rule pass; copy `[REDACTED:n]` tokens through unchanged.

**Revised after implementation** — the output was originally specified as a unified diff.
See the `Patch` note above for why.

Validation before verification: output that is empty, or identical to what is on disk, is
rejected. A markdown fence wrapping the entire answer is stripped rather than rejected —
the prompt forbids fences and models add them anyway, and failing over one would report
"the model cannot fix this" when it could. Rejected output costs a retry without
consuming a verification run.

## Provenance record example

```json
{
  "schema_version": "0.1",
  "run_id": "3f2b8c14-...",
  "timestamp": "2026-08-30T09:12:44Z",
  "repository": {
    "remote": "github.com/acme/infra",
    "commit": "a3f9c21",
    "branch": "copilot/add-reports-bucket",
    "pr_number": 512
  },
  "generated_by": "github-copilot",
  "tooling": [
    {"name": "terraform", "version": "1.9.5"},
    {"name": "checkov", "version": "3.2.x"},
    {"name": "opa", "version": "0.68.0"}
  ],
  "model": {"provider": "openai_compat", "model": "your-model-id"},
  "decisions": [
    {
      "finding": {
        "rule_class": "iam.action.wildcard",
        "rule_id": "CKV_AWS_63",
        "severity": "HIGH",
        "resource_address": "aws_iam_policy.reports_access",
        "benchmark_refs": ["CIS-AWS-1.16"]
      },
      "status": "FIXED",
      "patch": {
        "rationale": "Scoped the policy to the two actions the application uses and to this bucket's ARN.",
        "attempt": 1
      },
      "verification": {
        "outcome": "verified",
        "target_resolved": true,
        "new_findings": []
      },
      "attempts": 1
    }
  ],
  "summary": {
    "total": 4, "fixed": 3, "escalated": 1,
    "exempted": 0, "blocked": 1
  },
  "content_hash": "sha256:7d1a..."
}
```

## Benchmark harness contract

`benchmarks/` must produce a reproducible table with one row per model and these columns:

- Baseline compliance pass rate (raw model output)
- Post-Plimsoll pass rate (after the verify → fix → re-verify loop)
- Delta
- Mean patch attempts per finding
- Escalation rate

Method, model versions, dataset commit, and total token counts are published alongside. The harness must complete on an ordinary laptop with no GPU and no paid inference — a free-tier hosted endpoint is enough. That is the claim, and the harness is where it is either true or not.
