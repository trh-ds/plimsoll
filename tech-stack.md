# Tech stack

Every choice here optimises for two constraints: a solo developer shipping in 6–10 weeks, and a project that costs nothing to run.

## Language — Python 3.11+

Chosen because the IaC ecosystem is already Python. Checkov is a Python package. HCL parsers, LLM SDKs, and the testing ecosystem are all mature. Contributors in the DevOps space read Python.

**Rejected: Go.** A single static binary would be genuinely better for distribution — that is how Terraform, OPA, and Trivy ship, and it is what users expect from a CI tool. But it costs more development time per feature and gives up the Checkov integration. Revisit if adoption justifies a rewrite of the CLI shell; the adapter contract is language-agnostic by design.

**Rejected: TypeScript.** GitHub Actions are frequently written in it, but it is the wrong ecosystem for the tools being wrapped.

Minimum 3.11 for `tomllib`, exception groups, and better typing ergonomics.

## Packaging — uv

Fast, handles Python version pinning, single lockfile, one tool for envs and dependencies. Removes the "which virtualenv" question from contributor onboarding entirely.

**Rejected: Poetry** (slower, heavier), **pip + requirements.txt** (no lockfile discipline).

## Data models — Pydantic v2

Every structure crossing a boundary is a Pydantic model: adapter output, config, provenance records, LLM-parsed responses. Validation at the boundary catches malformed tool output early rather than three layers deep, and JSON schema generation comes free — which matters because the provenance record is a published contract other tools will consume.

**Rejected: dataclasses + manual validation.** Lighter, but the validation is the point. **Rejected: attrs.** Fine, but Pydantic's schema export is decisive here.

## CLI — Typer

Type-hint driven, so the CLI signature and the function signature are the same thing. Good help output for free.

**Rejected: Click** (Typer wraps it with less ceremony), **argparse** (too much boilerplate for a multi-command tool).

## External tools — subprocess

| Tool | Purpose |
|---|---|
| `terraform` | `validate` and `plan` (never `apply`) |
| `checkov` | Broad static rule coverage |
| `opa` | Custom Rego policy evaluation |

All invoked via `subprocess.run` with explicit timeouts, `check=False`, and JSON output flags. Version independence is the reason: users run the versions their organisation pins.

Checkov could be imported directly as a Python package and would be faster. It is still a subprocess, because a dependency conflict between Plimsoll's Checkov and the user's Checkov is a worse failure than a slow call.

## HCL handling — no parser, no diff library

**Both dependencies this section used to name were dropped when the loop was built, and neither turned out to be needed.**

`python-hcl2` was for locating the block that triggered a finding, to slice context into the prompt. Brace counting does that in twenty lines. The block is context for a human-readable explanation, and the patch prompt gets the whole file anyway, so a parse-accurate boundary buys nothing a heuristic does not.

`unidiff` was for applying model-authored diffs. The model now returns the complete corrected file and `difflib` computes the diff for display. Asking a model for a unified diff makes the diff applier the component that fails: hunk headers come back wrong, context lines drift, and every malformed diff burns a retry without testing whether the model understood the rule.

The reasons for preferring diffs still hold — a diff is what a reviewer expects to see in a PR, and an AST round-trip would reformat unrelated code and destroy comments. That is why the diff is still what gets *shown*. It is just computed from two known strings rather than parsed from generated text.

Both are still available if a later need is real. Neither is currently a dependency.

## LLM providers — thin adapter, no framework

```python
class LLMProvider(Protocol):
    def complete(self, system: str, user: str, max_tokens: int) -> str: ...
```

Implementations: `openai_compat` and `null`. That is the entire abstraction. `openai_compat` covers agent routers, Groq, Cerebras, OpenRouter, GitHub Models, vLLM, LM Studio, a local Ollama, and OpenAI itself, because they all speak the same wire format.

Vendor-specific providers are deliberately **not** built. One adapter reaches every endpoint that matters, and a second one covering the same endpoints by a different route is a second thing to keep working. An endpoint speaking a genuinely different wire format gets its own module when someone needs it — that is roughly forty lines against the protocol.

The transport is `urllib` from the standard library. A single POST does not justify a dependency, and every dependency in a security tool is one somebody has to audit.

**Rejected: LangChain and LlamaIndex.** The project makes exactly two kinds of model call. A framework would add a large dependency tree, a moving API surface, and abstractions over a problem that does not have them. The orchestration logic here is a bounded retry loop — roughly forty lines, and it is the most important forty lines in the codebase. It should be readable, not delegated.

**Rejected: instructor / structured-output libraries.** Prompts return prose or a file. Neither needs schema-constrained decoding, and both need to work on small models where constrained decoding support is uneven.

### Default configuration

`openai_compat`, pointed at whatever endpoint the user configures. There is no default model identifier and no default base URL: guessing one would either pick a vendor for the user or fail with a confusing error from somebody's router. `doctor` reports the model as missing until all of it is set, and `scan` keeps working meanwhile.

**Hosted by default, not local.** An earlier revision made a local Ollama the default, on the grounds that it costs nothing. It also requires hardware most contributors do not have — a laptop with no discrete GPU either cannot run a 7B model or runs it slowly enough that nobody will wait. A tool a contributor cannot run is a tool they cannot contribute to, and that is a worse accessibility failure than the cost one.

The zero-cost requirement survives intact by a different route: free-tier hosted endpoints, GitHub Models among them, run the full loop on any machine. Local inference remains fully supported — Ollama speaks this wire format — it is simply an option rather than an assumption.

For CI, GitHub Models via `openai_compat` — free tier, and the project already lives inside GitHub Actions.

Frontier models are supported and used for the benchmark's upper bound, never required.

## Testing — pytest

- `tests/fixtures/` holds Terraform configs with deliberately known violations, one directory per failure mode. These are the corpus everything is tested against.
- Adapter tests use recorded tool output — real JSON captured from real runs, committed. Fast, hermetic, no binaries required in CI.
- Loop tests use the `null` provider and scripted fake providers that return canned patches, so retry, regression, and escalation paths are all deterministically testable.
- `-m slow` marks tests that shell out to real binaries. They run in a separate CI job.
- **No network calls in unit tests.** Ever.

## Quality — ruff and mypy

`ruff` for lint and format (one tool, replaces black, isort, flake8). `mypy --strict` on `src/`. Both gate CI.

Strict typing carries specific weight in this project: the invariant that the model cannot set a decision status is enforced by type signatures. Losing type checking loses the enforcement.

## Distribution

- **PyPI** — `uv tool install plimsoll` / `pipx install plimsoll`
- **Docker image** — pinned Terraform, Checkov, and OPA versions bundled. This is what the GitHub Action runs, so users get a known-good tool matrix rather than whatever their runner happens to have.
- **GitHub Action** — Docker-based, in `action/`

## CI — GitHub Actions

Free unlimited minutes on public repos. Jobs: lint + types, unit tests across Python 3.11/3.12/3.13, slow tests with real binaries, and Plimsoll run against its own fixture corpus as a regression check.

## Deliberately absent

| Not used | Why |
|---|---|
| boto3 or any cloud SDK | The tool never authenticates to a cloud. Reads HCL, shells out. Near-zero security surface. |
| Database | Exceptions live in a committed YAML file. A database means a backend, hosting, and cost. |
| Web framework | No dashboard, no server. Output is stdout, files, and PR comments. |
| Vector store / embeddings | Context retrieval is direct lookup — rule ID to rule, resource type to docs. Add embeddings when there is a retrieval problem, not before. |
| Message queue, worker pool | Single-process CLI. Findings are processed in a loop with a concurrency cap. |
| LangChain / agent frameworks | See above. Two prompt types and a retry loop. |

## Local development requirements

Docker (optional, for the Action image), `uv`, and Python 3.11+. Terraform, Checkov, and OPA are optional — the test suite runs without them via recorded fixtures.

To exercise the full loop you need an OpenAI-compatible endpoint and three environment
variables. A free-tier hosted endpoint is enough; so is a local Ollama if you have the
hardware and prefer it.

**No GPU is required to develop this project**, and the unit suite needs no model at all
— provider behaviour is tested against recorded responses with no socket opened.

**No AWS account is required to develop this project.** `terraform apply` is never called; `terraform plan` runs against dummy credentials or LocalStack. This is stated explicitly because the assumption that an AWS project needs an AWS bill is the most likely reason a contributor bounces.
