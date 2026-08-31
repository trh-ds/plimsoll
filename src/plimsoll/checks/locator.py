"""Finds the HCL block a resource address came from.

`terraform plan` JSON has no filenames or line numbers in it. It describes what *would
exist*, not where it was written, which is the right shape for evaluating policy and the
wrong shape for reporting to a human -- and for building a patch prompt, which needs a
file to send and a file to write back.

So the `opa` adapter maps addresses back to source by searching the workspace's `.tf`
files for the block header. Text search, not an HCL parser: the header syntax is fixed
(`resource "type" "name" {`), the alternative is a parser dependency for one lookup, and
a miss degrades to a file-level finding rather than a wrong one.
"""

from __future__ import annotations

import re
from pathlib import Path

# `aws_s3_bucket.reports[0]`, `aws_s3_bucket.reports["a"]` -- the index is not part of
# the declaration, which is what we are looking for.
_INDEX = re.compile(r"\[[^\]]*\]")


class Location:
    """Where a resource is declared. Empty file means "not found"."""

    __slots__ = ("file", "line_end", "line_start")

    def __init__(self, file: Path, line_start: int = 0, line_end: int = 0) -> None:
        self.file = file
        self.line_start = line_start
        self.line_end = line_end


def _header_pattern(address: str) -> re.Pattern[str] | None:
    """The regex matching the block header for `address`, or None when unparseable."""
    parts = _INDEX.sub("", address).split(".")
    while len(parts) >= 2 and parts[0] == "module":
        parts = parts[2:]

    if len(parts) >= 3 and parts[0] == "data":
        kind, labels = "data", (parts[1], parts[2])
    elif len(parts) >= 2 and parts[0] == "provider":
        return re.compile(rf'^\s*provider\s+"{re.escape(parts[1])}"')
    elif len(parts) >= 2:
        kind, labels = "resource", (parts[0], parts[1])
    else:
        return None

    first, second = (re.escape(part) for part in labels)
    return re.compile(rf'^\s*{kind}\s+"{first}"\s+"{second}"')


def _block_end(lines: list[str], start: int) -> int:
    """1-indexed line the block opened at `start` (0-indexed) closes on."""
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0 and i > start:
            return i + 1
        if depth == 0 and i == start and "{" in lines[start]:
            return start + 1
    return len(lines)


def terraform_files(workspace: Path) -> list[Path]:
    """Every `.tf` file in the workspace, in a stable order.

    Sorted so a file-level finding lands on the same file every run: an address that
    cannot be located must still produce a stable `Finding.id`, or the same violation
    would look like a different one on the next scan.
    """
    return sorted(p for p in workspace.rglob("*.tf") if p.is_file())


def locate_resource(workspace: Path, address: str) -> Location:
    """Locate `address` in the workspace, falling back to the first `.tf` file.

    The fallback is deliberate rather than an error: a rule like
    `logging.cloudtrail.absent` fires on something the configuration does *not* contain,
    so there is no block to point at. Pointing at the first file gives the reporter a
    location and the patch prompt a file to add the resource to, which is what a human
    would do.
    """
    files = terraform_files(workspace)
    pattern = _header_pattern(address) if address else None

    if pattern is not None:
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines):
                if pattern.match(line):
                    relative = path.relative_to(workspace)
                    return Location(relative, number + 1, _block_end(lines, number))

    if files:
        return Location(files[0].relative_to(workspace), 0, 0)
    return Location(Path(""), 0, 0)
