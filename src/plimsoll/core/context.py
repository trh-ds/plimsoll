"""Turns a `Finding` into the text a model is shown.

Every prompt is assembled here, and every prompt passes through `Redactor` on the way
out. That is a single choke point on purpose: there is exactly one place where workspace
content becomes network payload, so there is exactly one place to audit.

Prompts themselves live in `llm/prompts/*.txt`. They are content, not code.
"""

from __future__ import annotations

import re
from pathlib import Path

from plimsoll.core.models import Finding
from plimsoll.core.redaction import Redactor

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"

# Terraform top-level block openers. `locals` and `terraform` take no labels.
_BLOCK_OPENER = re.compile(
    r"^\s*(resource|data|module|provider|variable|output|locals|terraform)\b"
)

# A file we will not send. Terraform state contains every attribute of every deployed
# resource in plaintext, including generated passwords; no finding is worth posting it.
_NEVER_SEND = ("terraform.tfstate", "terraform.tfstate.backup", ".terraform.lock.hcl")

MAX_FILE_CHARS = 60_000
"""Above this a file is not sent at all.

Truncating would be worse than refusing: the model would return a "complete" file that
silently drops everything past the cut, the verifier would see resources vanish, and the
patch would be discarded after a wasted gate run. Refuse up front instead.
"""


class ContextError(RuntimeError):
    """The context for this finding cannot be built, so the model is not called."""


def _read_prompt(name: str) -> str:
    try:
        return (_PROMPT_DIR / name).read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - packaging failure, not a runtime path
        raise ContextError(f"prompt template {name} is missing: {exc}") from exc


def resolve_file(workspace: Path, finding: Finding) -> Path:
    """Locate the finding's file inside the workspace, refusing to escape it.

    Adapters report paths from their own working directory and checkov reports absolute
    ones. A `..` segment or an absolute path pointing outside the workspace is a bug or
    an attack; either way the answer is to refuse, not to normalise and continue.
    """
    root = workspace.resolve()
    candidate = finding.file if finding.file.is_absolute() else root / finding.file
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root):
        raise ContextError(f"{finding.file} resolves outside the workspace")
    if resolved.name in _NEVER_SEND:
        raise ContextError(f"{resolved.name} is never sent to a model")
    if not resolved.is_file():
        raise ContextError(f"{finding.file} does not exist in the workspace")
    return resolved


def read_workspace_file(workspace: Path, finding: Finding) -> str:
    path = resolve_file(workspace, finding)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContextError(f"cannot read {finding.file}: {exc}") from exc
    if len(content) > MAX_FILE_CHARS:
        raise ContextError(
            f"{finding.file} is {len(content)} characters, over the {MAX_FILE_CHARS} limit"
        )
    return content


def extract_block(content: str, line_start: int) -> str:
    """The smallest enclosing top-level HCL block around `line_start`.

    Brace counting, not an HCL parser. Adding a parser dependency to quote a snippet
    into a prompt would not pay for itself -- the block is context for a human-readable
    explanation, and the patch prompt gets the whole file regardless. When the heuristic
    cannot find a block it returns a few surrounding lines, which is still useful.
    """
    lines = content.splitlines()
    if not lines:
        return ""
    index = max(0, min(line_start - 1, len(lines) - 1))

    start = None
    for i in range(index, -1, -1):
        if _BLOCK_OPENER.match(lines[i]):
            start = i
            break
    if start is None:
        lo = max(0, index - 3)
        return "\n".join(lines[lo : index + 4])

    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0 and i > start:
            return "\n".join(lines[start : i + 1])
        # An opener whose braces balance on its own line, e.g. `locals {}`.
        if depth == 0 and i == start and "{" in lines[start]:
            return lines[start]
    return "\n".join(lines[start:])


def _location(finding: Finding) -> str:
    where = finding.file.as_posix() or "<unknown file>"
    return f"{where}:{finding.line_start}" if finding.line_start else where


def _base_fields(finding: Finding) -> dict[str, str]:
    return {
        "rule_class": finding.rule_class,
        "rule_id": finding.rule_id,
        "source": finding.source.value,
        "severity": finding.severity.value,
        "resource_address": finding.resource_address or "(file-level finding)",
        "location": _location(finding),
        "message": finding.message,
        "file": finding.file.as_posix(),
    }


def build_explain_prompt(finding: Finding, workspace: Path, redactor: Redactor) -> str:
    """Prompt for a prose explanation. Sends the offending block, not the whole file."""
    try:
        content = read_workspace_file(workspace, finding)
        snippet = extract_block(content, finding.line_start)
    except ContextError:
        # The explanation is still worth having from the checker's own message. A patch
        # without the file is not, which is why only this path degrades.
        snippet = "(source unavailable)"

    fields = _base_fields(finding)
    fields["snippet"] = redactor.redact(snippet)
    return _read_prompt("explain.txt").format(**fields)


def build_patch_prompt(
    finding: Finding,
    workspace: Path,
    redactor: Redactor,
    failure_reason: str | None = None,
) -> str:
    """Prompt for a corrected file. Raises `ContextError` rather than guessing."""
    content = read_workspace_file(workspace, finding)

    retry_block = ""
    if failure_reason:
        # The previous attempt's failure is the highest-value context there is: it is
        # the deterministic gate's own words about why the last answer was wrong.
        retry_block = (
            "\nA previous attempt at this fix was rejected by the checker. "
            "Do not repeat it.\nReason it was rejected:\n"
            f"{redactor.redact(failure_reason)}\n"
        )

    fields = _base_fields(finding)
    fields["file_content"] = redactor.redact(content)
    fields["retry_block"] = retry_block
    return _read_prompt("patch.txt").format(**fields)
