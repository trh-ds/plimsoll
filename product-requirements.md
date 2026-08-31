# Product requirements

## Problem statement

AI coding agents produce a rapidly growing share of infrastructure code, are measurably poor at it, and no open-source tool verifies their output before it reaches a cloud account. Two mature layers exist and do not talk to each other:

- **Deterministic policy tooling** (OPA/Rego, Conftest, Checkov, Trivy, KICS, `terraform plan`, Sentinel) is battle-tested but produces pass/fail plus a rule ID. It cannot explain a finding in context, cannot fix it, and cannot distinguish a real risk from an accepted exception. At scale it produces alert fatigue, and teams switch it off.
- **Generative tooling** writes Terraform all day with no verification loop.

Additionally, no tool answers the governance question. Teams cannot determine which infrastructure changes were AI-generated, policy is enforced after generation rather than upstream of it, and there is no audit trail recording who or what approved a production change.

## Who this is for

**Primary — the platform or DevOps engineer at a team that has adopted AI coding agents.** They already run Checkov or OPA in CI. They are seeing more Terraform PRs, from less experienced authors, at a higher volume than they can review carefully. They want the noise pre-filtered and the obvious fixes pre-drafted.

**Secondary — the compliance or security engineer.** They do not write Terraform. They need evidence that a control was enforced, and they need it in a form an auditor accepts.

**Tertiary — the solo developer or student.** They use Copilot or Claude to write infrastructure they do not fully understand. They benefit most from the explanation, least from the attestation.

The primary user is the one to optimise for. The tertiary user is the one most likely to try it first.

## Scope

### v0.1 (MVP)

- Terraform (HCL) targeting AWS. No other IaC format, no other cloud.
- Four check adapters: `terraform validate`, `terraform plan`, Checkov, `opa eval`.
- A bundled policy pack of roughly 20 rules covering the documented AI failure modes (see `specifications.md`).
- One LLM reasoning pass per finding, with bounded retries and mandatory escalation on exhaustion.
- Exception store, file-backed, keyed on rule + resource address.
- JSON provenance artifact per run with a content hash.
- CLI (`scan`, `verify`, `explain`) and a GitHub Action producing a PR comment.
- Benchmark harness reproducing IaC-Eval, with published before/after numbers.

### Explicitly out of scope for v0.1

| Not building | Why |
|---|---|
| Multi-cloud (Azure, GCP) | The Terraform + AWS path must work and be benchmarked first. Breadth before depth is how these projects die. |
| Kubernetes manifests, CloudFormation, Pulumi, Bicep | Same. The adapter interface makes these cheap to add later. |
| A hosted service, dashboard, or database | The tool runs in the user's CI. No backend means no cost, no data handling, no trust barrier. |
| Runtime or post-deployment scanning | Reactive tooling is a crowded, well-funded space. This project is preventive by definition. |
| Cross-repo dependency graph / blast radius | Genuinely valuable and genuinely unbuilt, but needs org-wide repo access to demonstrate. Candidate for v0.3. |
| Autonomous merge | A human approves. Always. Removing that is a different product with a different risk profile. |
| Sigstore/in-toto signing | v0.2. v0.1 ships an unsigned record with a content hash — the schema is designed so signing is additive. |

## Functional requirements

**FR1 — Detect.** Given a Terraform directory, run all available checks and produce a normalised `Finding` list. Unavailable tools are skipped and reported, not fatal.

**FR2 — Filter.** Findings matching a recorded exception are marked `EXEMPTED` and excluded from the failure count, with the exception's justification and approving PR carried into the output.

**FR3 — Explain.** For each active finding, produce prose stating what the rule is, why this specific resource violates it, and what the concrete risk is. The explanation cites the governing rule ID and, where available, the benchmark clause.

**FR4 — Fix.** Produce a minimal unified diff addressing the finding. Minimal means: touching only the resource in question, changing no unrelated attributes, and introducing no new resources unless the rule requires one.

**FR5 — Re-verify.** Apply the patch to a scratch copy of the workspace and re-run the full gate. Record the delta. A patch is `verified` only if it resolves the target finding and introduces no new finding at or above the original severity.

**FR6 — Escalate.** After `max_retries` failed patch attempts, emit the original finding with status `ESCALATED` and an explicit statement that no verified fix was found. Never present an unverified patch as a suggestion.

**FR7 — Attest.** Emit a JSON record per run containing every decision, its inputs, its outputs, the model and provider used, tool versions, and a content hash of the record.

**FR8 — Report.** Render results as a terminal summary, a JSON file, SARIF, and a GitHub PR comment. Exit code reflects the configured failure threshold.

## Non-functional requirements

**NFR1 — Zero-cost, zero-hardware operation.** The full loop must run end to end with no paid API key *and* no GPU. This is a hard requirement, not an aspiration: a project only its author can afford to run gets no contributors, and neither does one only its author has the hardware to run.

*Revised.* This originally required a **locally hosted** model. That satisfied the cost half and failed the hardware half — a laptop without a discrete GPU either cannot run a 7B model or runs it too slowly to wait for, which is most contributors' laptops. The requirement is now met by free-tier hosted endpoints, which need neither money nor hardware. Local inference stays supported for anyone who wants it, and is no longer assumed by any code path, test, or documented workflow.

**NFR2 — Deterministic-only mode.** `--no-llm` must yield a working scanner. Provider unavailability degrades to this mode with a warning, never a failure or a pass.

**NFR3 — Adoption cost near zero.** Works with the user's existing OPA and Checkov policies unmodified. Ten lines of workflow YAML to install.

**NFR4 — Runtime budget.** Under 3 minutes for a 50-resource module on a standard GitHub runner, deterministic stages only. LLM stages are bounded by an explicit per-run timeout.

**NFR5 — No cloud mutation.** The tool never creates, modifies, or destroys cloud resources. Read-only credentials are sufficient; dummy credentials or LocalStack must be viable for development.

**NFR6 — Reproducibility.** Two runs against the same input and the same tool versions produce the same findings. LLM output varies; findings and decisions do not.

## Success metrics

**Correctness — the number that matters most.** Measured pass-rate improvement on IaC-Eval compliance scenarios when a model's output passes through Plimsoll's loop versus raw. This is the headline claim and must be independently reproducible from `benchmarks/`.

**Precision.** False-positive rate on a corpus of known-good Terraform. A tool that cries wolf gets uninstalled in a week. Target under 5% before announcing.

**Fix quality.** Proportion of findings for which a *verified* patch is produced. Low is acceptable and honest; presenting unverified patches to inflate this number is not.

**Adoption signal.** Installs in real pipelines and, more meaningfully, external contributors adding check adapters or rules — that is the shape the project is designed to invite.

## Positioning risk

HashiCorp, Snyk, Sonar, and Spacelift can all build this, and Sonar has started — their pitch is semantic analysis of AI-written Terraform, with users reporting 44% fewer AI-code-related outages.

The edge is not resources. It is being open-source, CI-native, model-agnostic, and provenance-first while the incumbents build closed platforms. Those four properties are the moat, and each one is easy to give up accidentally — a hosted dashboard, a default model, a proprietary policy format. Guard them.

Do not compete on breadth. Solve one sharp problem completely — *AI-generated Terraform ships insecure and nobody can verify or attest to it* — publish the numbers, and let scope grow from real users rather than from the roadmap.
