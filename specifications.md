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
    raw: dict  # unmodified tool output for this finding
```

`rule_class` is what deduplication and exception matching key on. `rule_id` is what gets displayed. The mapping table lives in `checks/rule_classes.yaml` and is the single place tool-specific IDs meet internal semantics.

### Patch

```python
class Patch(BaseModel):
    diff: str  # unified diff
    files_touched: list[Path]
    rationale: str  # one paragraph, model-authored
    target_finding_id: str
    attempt: int
```

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
    status: DecisionStatus  # PASS | FIXED | ESCALATED | EXEMPTED | BLOCKED
    explanation: str | None
    patch: Patch | None  # present only when status == FIXED
    verification: PatchVerification | None
    attempts: int
    exception_ref: ExceptionRef | None
```

| Status | Meaning |
|---|---|
| `PASS` | No violation. |
| `FIXED` | Violation found, patch produced, patch passed re-verification. |
| `ESCALATED` | Violation found, no verified patch after `max_retries`. Human required. |
| `EXEMPTED` | Matched a recorded exception. Reported, not counted as failure. |
| `BLOCKED` | Violation at or above the configured threshold; the run fails. |

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
plimsoll scan <path>       Deterministic only. No model. Fast.
plimsoll verify <path>     Full loop: scan, explain, fix, re-verify, attest.
plimsoll explain <path>    Scan plus explanations, no patches.
plimsoll exceptions        Subcommands: list, add, expire, prune.
plimsoll doctor            Report which external tools and providers are available.
```

### Shared flags

| Flag | Default | Meaning |
|---|---|---|
| `--policy-dir` | `./policies` | Additional Rego to load alongside the bundled pack |
| `--fail-on` | `high` | Minimum severity that sets a non-zero exit |
| `--format` | `terminal` | `terminal` \| `json` \| `sarif` \| `github` |
| `--provenance` | none | Path to write the JSON record |
| `--no-llm` | false | Force deterministic mode |
| `--max-retries` | 2 | Patch attempts per finding before escalation |
| `--max-llm-findings` | 25 | Cap on findings sent to the model, highest severity first |
| `--generated-by` | `unknown` | Recorded in provenance |
| `--timeout` | 600 | Whole-run budget in seconds |

### Environment

```
PLIMSOLL_LLM_PROVIDER    ollama | openai_compat | anthropic | null
PLIMSOLL_LLM_MODEL       provider-specific model identifier
PLIMSOLL_LLM_BASE_URL    for openai_compat and ollama
PLIMSOLL_LLM_API_KEY     for hosted providers
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No findings at or above `--fail-on` |
| 1 | Findings at or above threshold remain unresolved |
| 2 | Configuration error (bad policy dir, invalid flags) |
| 3 | No checks available to run — nothing was verified |
| 4 | Run exceeded `--timeout` |

Code 3 exists so a broken toolchain cannot masquerade as a clean pass. Silence must never read as success.

## Bundled rule pack — v0.1

Twenty rules covering the documented AI-generated IaC failure modes. Each is Rego evaluated against `terraform plan` JSON, each maps to a `rule_class`, and each has a fixture in `tests/fixtures/`.

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

Two prompts, in `llm/prompts/`. Both must produce usable output from a 7B local model — if a prompt only works on a frontier model, it is the wrong prompt.

### `explain.txt`

**Input:** rule text, benchmark clause, offending HCL block, resource address, prior accepted exceptions for this rule class.
**Output:** prose, under 120 words, stating the rule, why this resource violates it, and the concrete risk. No markdown headers, no preamble.
**Must not:** claim the finding is a false positive, suggest suppressing the rule, or state a compliance verdict.

### `patch.txt`

**Input:** everything above, plus the full file content and, on retry, the previous attempt's `failure_reason`.
**Output:** a unified diff and nothing else. No fences, no commentary.
**Constraints stated in the prompt:** touch only the file and resource named; change no unrelated attributes; introduce a new resource only when the rule requires one; preserve existing comments and formatting.

Validation before verification: the diff must parse and apply cleanly. Malformed output is rejected without consuming a verification run, and counts as a retry.

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
  "model": {"provider": "ollama", "model": "qwen2.5-coder:7b"},
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

Method, model versions, dataset commit, and total token counts are published alongside. The harness must complete against local models with no paid inference — that is the claim, and the harness is where it is either true or not.
