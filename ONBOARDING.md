# Orientation for people new to this domain

This is for someone who can program but has never touched cloud infrastructure. It
explains what the project is, the background you need to understand why it exists, what
is actually built so far, and what you could pick up.

You do **not** need cloud experience to contribute. A good chunk of this codebase is
plain Python — parsing JSON, formatting output, sorting and deduplicating lists — and
there is a section below listing tasks by how much domain knowledge they need. Several
need none.

If you just want the short version: [Where the project actually is](#part-3--where-the-project-actually-is)
and [Pick something to work on](#part-6--pick-something-to-work-on).

---

## Part 1 — The background

### Cloud infrastructure

When a company runs software on Amazon Web Services (AWS), they aren't just running code.
They're renting *things*: virtual machines, storage, databases, networks, and the rules
about who can access what. A running product might be a few hundred of these, wired
together.

Three that show up constantly in this project:

- **S3 bucket** — a folder in the cloud you put files in. Every bucket has settings for
  who can read it, whether the contents are encrypted, whether deleted files are
  recoverable.
- **IAM policy** — a permission rule. "This program may read files from that one bucket."
  IAM is the system that decides who is allowed to do what.
- **Security group** — a firewall rule. "Allow connections to port 22 from this office's
  network."

Each of these has settings that are dangerous when wrong. A storage bucket set to public
means anyone on the internet can read it — this is the single most common cause of the
"company leaks 5 million customer records" news story. A permission rule that grants `*`
means "this program can do anything to anything," so if it's ever compromised, the
attacker inherits the entire account.

### Infrastructure as Code, and Terraform

Nobody clicks through a web console to set up hundreds of things. They write it down in
files and have a tool build it. That's **Infrastructure as Code**, and **Terraform** is
the most widely used tool for it.

Terraform files are `.tf` and use a language called HCL. Here's a real one from this
repo, `tests/fixtures/insecure_s3/main.tf`:

```hcl
resource "aws_s3_bucket" "reports" {
  bucket = "plimsoll-fixture-insecure-reports"
}

resource "aws_s3_bucket_acl" "reports" {
  bucket = aws_s3_bucket.reports.id
  acl    = "public-read"
}
```

Read that as: "make a storage bucket, call it `reports`; and set its access control to
`public-read`." That second line is the leak. It is one word.

Some vocabulary you'll see everywhere in the code:

- **Resource** — one thing being created. `aws_s3_bucket` is the *resource type*.
- **Resource address** — its unique name in the config: `aws_s3_bucket.reports`. This is
  the primary key for a finding in our data model.
- **`terraform validate`** — checks the config is *syntactically and structurally* valid:
  does this resource type exist, is this argument real, does this reference point at
  something. It does not talk to AWS.
- **`terraform plan`** — works out what *would* be created, as a preview. Talks to AWS
  (though as we discovered, not necessarily — see below).
- **`terraform apply`** — actually builds it. **This project never runs `apply`.** Not in
  code, not in tests. It's a pre-deployment tool; by the time `apply` runs, it's too late
  to be useful.

### Policy scanners

Because "is this configuration dangerous?" is a question with mostly known answers, there
are mature tools that read your `.tf` files and list the problems. This project uses:

- **Checkov** — ~1,000 built-in rules. Free, open source, very good.
- **OPA (Open Policy Agent)** — a general policy engine. You write your own rules in a
  language called Rego. Used everywhere in the industry, not just for cloud.

These tools work. They are not the problem.

### So what *is* the problem?

Those scanners tell you a rule ID and stop:

```
CKV_AWS_20  aws_s3_bucket.reports  S3 Bucket has an ACL which allows public READ access.
```

That's it. A human then has to understand it, work out the fix, and apply it. Meanwhile,
AI coding assistants now write a large share of committed code — and they are measurably
*worse* at infrastructure code than at application code. GPT-4 scored 19.36% on Terraform
tasks versus 86.6% on equivalent Python (IaC-Eval, NeurIPS 2024). Roughly 57.5% of
AI-generated deployment infrastructure was measured as vulnerable (IOActive).

So you have AI producing a lot of infrastructure code, scanners that can detect the
problems but not fix them, and humans in the middle doing tedious translation.

The obvious idea is "have the AI fix what the scanner found." The obvious problem is that
the AI might be wrong, and now you have a plausible-looking patch nobody has checked.

---

## Part 2 — What Plimsoll does

Plimsoll runs in CI — the automated checks that run when you open a pull request. The
loop:

```
1. Scan          run the real scanners, collect findings
2. Explain       give an AI model the finding and the code around it
3. Patch         the model drafts a fix
4. RE-VERIFY     run the exact same scanners on the patched code
5. Decide        passed? show the human. failed? throw it away and retry, then give up
                 honestly
6. Record        write down what changed, which rule, which model, who approved
```

**Step 4 is the whole point.** The model's output is a *proposal*. The scanner is the
*judge*. If the model hallucinates a resource type, or fixes one problem while creating
another, step 4 catches it and no human ever sees it.

### The rule that shapes everything

> **The AI never decides whether something is compliant.**

Only the deterministic tools decide. There is no code path where a model's opinion sets a
result status. This isn't a style preference — it's the reason the project exists. If you
find yourself writing code where a model's assessment becomes a verdict, that's a bug, no
matter how well it works.

There are six such rules, listed in [`CLAUDE.md`](CLAUDE.md#the-invariants). The others:
patches get re-checked by the *same* gate that found the problem; an unverified patch
never reaches a human; the tool works with no AI at all (`--no-llm`); `terraform apply` is
never invoked; secrets never reach the audit records.

### What "the gate" means

You'll see this word constantly. **The gate** is the deterministic half — the part that
runs the real tools and produces findings. It's `src/plimsoll/core/gate.py`. It runs
before the AI to find problems and again after the AI to judge its fix. Same code, both
times.

---

## Part 3 — Where the project actually is

Built and working today:

| Piece | What it is |
|---|---|
| Core data model | `Finding`, `Patch`, `Decision`, `GateResult` — the shapes everything passes around |
| The gate | Runs check adapters, isolates crashes, aggregates results |
| `terraform_validate` adapter | Wraps `terraform validate` |
| `checkov` adapter | Wraps Checkov |
| Deduplication | Two tools reporting one problem collapse into one finding |
| **The AI loop** | Steps 2–5 above. Explain, patch, re-verify, escalate |
| **Model providers** | Any OpenAI-compatible endpoint, plus a `null` one for tests |
| **Secret redaction** | Strips credentials from prompts, reversibly |
| `plimsoll scan` / `verify` / `explain` / `doctor` | Working CLI with correct exit codes |
| Terminal + JSON reporters | Human-readable and machine-readable output |
| Test fixture corpus | A deliberately broken Terraform config and a clean one |

Not built at all:

| Piece | Notes |
|---|---|
| `opa` and `terraform_plan` adapters | Next up. Interface is ready for them |
| The bundled rule pack | 20 Rego rules specified, zero written |
| Exception store | "We accepted this finding last week, don't ask again" |
| Provenance records | The audit trail |
| SARIF output | The standard format GitHub's security tab reads |
| GitHub Action, PR comments | How anyone would actually use this |
| Benchmarks | The numbers that would make anyone believe it works |

Roughly: **2,100 lines of source, 183 unit tests, 3 tests that shell out to real tools.**

The loop works. What it doesn't have yet is much to reason *about* — the bundled rule
pack is unwritten, so findings only come from Checkov and `terraform validate`. Writing
those rules is the single biggest chunk of work left, and it's the part that most needs
someone who's read what the rules are supposed to catch.

Honest framing: this is early. That's good for you — the parts left are self-contained
and there's no legacy to fight.

### You don't need a GPU, and you don't need to pay for anything

This matters enough to say plainly, because it's the assumption that stops people.

The AI part runs against a **hosted** model over an ordinary HTTPS call — three
environment variables, no local model, no graphics card. Free-tier endpoints work.

And you don't need any of that to contribute. **The entire test suite runs with no model,
no cloud tools, and no network.** Most of the task list below never touches a model at
all.

---

## Part 4 — The code

```
src/plimsoll/
  core/
    models.py      Every data shape. Read this first — it's the vocabulary
    gate.py        Runs the checks, collects findings, deduplicates
    context.py     Builds the prompts. The only place your code leaves your machine
    verifier.py    Applies a patch to a scratch copy and re-runs the gate on it
    loop.py        Sequences it: explain, patch, verify, retry, escalate
    redaction.py   Strips credentials out of anything model-bound
  checks/          One file per external tool
    terraform_validate.py
    checkov.py
    severity_map.py  Explicit tables: this tool's "error" means our HIGH
    rule_map.py      Explicit tables: Checkov's CKV_AWS_20 means "public bucket, CRITICAL"
    addresses.py     Parses "module.reports.aws_s3_bucket.data" into its parts
  llm/             Talking to models. Nothing outside this folder names a model
    openai_compat.py  Any endpoint speaking the OpenAI API — which is nearly all of them
    null.py           A provider that refuses. For tests and --no-llm
    prompts/          explain.txt and patch.txt. Content, not code
  reporters/       Output formats
  cli.py           The command line

tests/
  fixtures/        Real captured tool output + deliberately broken Terraform
  conftest.py      Fake tools and fake models, shared by the loop tests
  test_*.py        One file per module
```

If you read three files, read `core/models.py` (the vocabulary), `core/loop.py` (the
whole product in 200 lines), and `core/verifier.py` (the bit that makes it trustworthy).

### The one interesting design decision

Every external tool is wrapped by an **adapter** implementing the same tiny interface:

```python
class Check(Protocol):
    name: str

    def is_available(self) -> ToolAvailability: ...  # is this tool installed?
    def run(self, ctx: CheckContext) -> list[Finding]: ...  # what did it find?
```

Adapters shell out to a binary, parse its **JSON** output (never scraped text), and map
it into our `Finding` shape. If a tool isn't installed, `is_available()` says so and the
run continues without it. If an adapter crashes, it's recorded and the run continues.

Adding a new tool means writing one file against that interface. That's the intended
contribution shape, and [`CONTRIBUTING.md`](CONTRIBUTING.md#walkthrough-add-a-check-adapter)
walks through it step by step.

### One principle worth internalising

**Silence must never read as success.**

If no scanner could run — none installed, all crashed — the tool exits with code 3 and
says "nothing was verified." It does not report a clean pass. A security tool that
quietly fails while looking green is worse than no tool.

This has already caught a real bug. `terraform validate` in an uninitialised directory
returns a single unhelpful diagnostic and never sees the actual file contents. The
adapter used to report that as an ordinary finding, so a scan looked like it ran and
found one problem while every real violation was invisible. It now fails loudly. That's
the mindset the codebase wants.

---

## Part 5 — Run it in five minutes

You need [uv](https://docs.astral.sh/uv/) (Python package manager) and Python 3.11+.

```bash
git clone https://github.com/trh-ds/plimsoll
cd plimsoll
uv sync
uv run pytest        # 183 tests, needs no cloud tools, no model, no network
```

The test suite deliberately requires **nothing** external — no Terraform, no Checkov, no
model, no network. It runs against JSON output captured from real tool runs and committed
to the repo. So you can work on most of this project without installing anything
cloud-related at all.

To see it do real work, install Checkov (`uv tool install checkov`) and then:

```bash
uv run plimsoll doctor                            # which tools can run
uv run plimsoll scan tests/fixtures/insecure_s3   # exit code 1
uv run plimsoll scan tests/fixtures/compliant_s3  # exit code 0
```

Output looks like:

```
Checks run: checkov 3.3.15
  skipped terraform_validate: terraform not found on PATH

  CRITICAL s3.acl.public                      aws_s3_bucket.reports
           S3 Bucket has an ACL defined which allows public READ access.
           main.tf:28  [checkov CKV_AWS_20]

16 finding(s), 6 at or above HIGH.
```

### And to see the AI part

Point it at any OpenAI-compatible endpoint. Three variables, no local model:

```bash
export PLIMSOLL_LLM_BASE_URL=https://your-endpoint.example/v1
export PLIMSOLL_LLM_MODEL=your-model-id
export PLIMSOLL_LLM_API_KEY=...

uv run plimsoll explain tests/fixtures/insecure_s3   # prose, no patches
uv run plimsoll verify  tests/fixtures/insecure_s3   # the full loop
```

`verify` output has one shape worth understanding, because it *is* the product:

```
  FIXED      CRITICAL s3.acl.public
             aws_s3_bucket.reports  main.tf:28
             ...
             verified fix (attempt 1):
             -  acl    = "public-read"
             +  acl    = "private"

  ESCALATED  HIGH     iam.action.wildcard
             aws_iam_role_policy.reports
             could not fix after 3 attempt(s): iam.action.wildcard is still reported
             no unverified patch is shown; this needs a human.
```

The second block is the interesting one. The model *did* produce patches for it, three
times, and all three were thrown away because re-running the scanner showed they didn't
work. You never see them. Most AI tools would have shown you the best-looking one.

`verify` never edits your files — patches go to a temp copy.

---

## Part 6 — Pick something to work on

### Needs no cloud knowledge at all

These are pure Python against data structures that are already defined and tested.

1. **SARIF reporter.** SARIF is a standard JSON format for static-analysis results; GitHub
   reads it to show findings inline on pull requests. It's a documented spec and a
   mapping exercise from `GateResult`. Start from `reporters/json_report.py`, which is
   27 lines.
2. **Whole-run timeout and exit code 4.** Right now `--timeout` is per-check. The spec
   wants a budget for the entire run, exiting 4 if exceeded. Contained in `cli.py` and
   `gate.py`.
3. **Better terminal output.** Findings currently print as a flat list. Grouping by file,
   or a `--summary` mode that prints counts only, is self-contained in
   `reporters/terminal.py`.
4. **Deduplication edge cases.** `_deduplicate` in `gate.py` is about 20 lines with clear
   rules. Reading it and trying to break it with a test is a genuinely useful hour.
5. **More secret patterns.** `core/redaction.py` strips credentials out of anything sent
   to a model. It currently knows AWS keys, GitHub/Slack/OpenAI tokens, Google API keys,
   JWTs, and PEM blocks. Every pattern it doesn't know is a credential that gets
   transmitted. One regex plus one test, and it is real security work — probably the
   highest value-per-line task on this list.
6. **Break the loop.** `tests/conftest.py` has a fake scanner and a fake model, so you can
   script "the model returns *this*" and assert what the loop does. Try to find a
   sequence where an unverified patch reaches the output. If you find one, that's a
   serious bug and we'd want to know.

### Needs a little, and teaches you the rest

7. **A new check adapter — Trivy or KICS.** Both are scanners like Checkov. You install
   the tool, run it against `tests/fixtures/insecure_s3`, look at its JSON, and write a
   file that maps that JSON onto `Finding`. `checks/checkov.py` is the worked example.
   The step-by-step is in [`CONTRIBUTING.md`](CONTRIBUTING.md#walkthrough-add-a-check-adapter).
8. **Extend `rule_map.py`.** Checkov has ~1,000 rules; we've classified 10. Each addition
   means running Checkov against a config, reading what fires, and deciding which of our
   rule classes it means. Mechanical, and you learn the domain as you go.
9. **Improve the prompts.** They're plain text files in `llm/prompts/`, no code. If the
   model keeps making a particular mistake, the fix is usually a sentence here. Needs an
   endpoint to test against, but no cloud knowledge.

### Needs real cloud knowledge

10. **The Rego rule pack.** 20 rules specified in `specifications.md`, none written. This
    is the biggest remaining piece of work in the project.
11. **The `terraform_plan` adapter.** Runs a plan and hands the JSON to OPA.

### Before you start

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — it has the setup, the invariants, and the
commit conventions. The commands that must pass before any PR:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src/
uv run pytest -m "not slow"
```

Two house rules worth knowing early, both of which have already saved this project from
shipping bugs:

- **Test fixtures must be captured from a real tool run**, never written by hand against
  what the docs say the output looks like. Both times we've done this it found something
  the documentation didn't mention.
- **Never widen a rule to make a test pass.** Fix the test fixture instead.

---

## Glossary

| Term | Meaning |
|---|---|
| **AWS** | Amazon Web Services. The cloud provider this targets |
| **IaC** | Infrastructure as Code. Describing infrastructure in files |
| **Terraform** | The most common IaC tool. Files are `.tf`, language is HCL |
| **HCL** | HashiCorp Configuration Language. What `.tf` files are written in |
| **Resource** | One thing being created — a bucket, a database, a permission |
| **Resource address** | Its unique name, e.g. `aws_s3_bucket.reports` |
| **S3** | AWS's file storage. An "S3 bucket" is a cloud folder |
| **IAM** | AWS's permission system. Who may do what |
| **ACL** | Access Control List. Who can read/write a thing |
| **Wildcard `*`** | "Anything." In a permission rule, usually a serious problem |
| **Checkov** | An open-source scanner with ~1,000 built-in rules |
| **OPA / Rego** | A policy engine, and the language you write its rules in |
| **The gate** | Our deterministic half — runs the tools, produces findings |
| **Adapter** | One file wrapping one external tool |
| **Finding** | One normalised violation. See `core/models.py` |
| **`--no-llm`** | Run with no AI at all. Must always work |
| **Patch** | A candidate fix the model wrote. A proposal, never a verdict |
| **Decision** | What happened to one finding: FIXED, ESCALATED, BLOCKED, REPORTED |
| **Re-verification** | Re-running the scanner on the patched copy. The whole point |
| **Escalated** | We tried to fix it, failed, and are handing it to a human unaltered |
| **Provider** | Something that returns text from a model. See `llm/` |
| **Redaction** | Stripping credentials out before anything is sent to a model |
| **CI** | Continuous Integration. Checks that run automatically on a pull request |
| **SARIF** | A standard JSON format for static-analysis results |

## Where to read next

- [`README.md`](README.md) — the pitch and current status
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup and the adapter walkthrough
- [`architecture.md`](architecture.md) — why the design is shaped this way
- [`specifications.md`](specifications.md) — exact data shapes and the rule pack
- [`progress.md`](progress.md) — the running log, including everything that surprised us

Questions are welcome as GitHub issues. "I don't understand why X" is a legitimate issue
and usually means a document needs fixing.
