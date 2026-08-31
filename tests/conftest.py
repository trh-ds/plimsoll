"""Fakes shared by the loop and verifier tests.

The loop cannot be tested against a real check adapter without a real binary, and it
cannot be tested against a stub that returns fixed findings either -- the whole point is
that findings *change* when a file changes. `MarkerCheck` is the smallest thing that has
that property: it re-reads the workspace on every run, so a patch genuinely alters what
the gate reports, exactly as terraform or checkov would.
"""

from __future__ import annotations

from pathlib import Path

from plimsoll.core.gate import CheckContext
from plimsoll.core.models import CheckSource, Finding, Severity, ToolAvailability
from plimsoll.llm import LLMError, LLMUnavailable

# marker text -> (rule_class, severity, resource_address)
DEFAULT_MARKERS: dict[str, tuple[str, Severity, str]] = {
    'acl    = "public-read"': ("s3.acl.public", Severity.CRITICAL, "aws_s3_bucket.reports"),
    "0.0.0.0/0": ("sg.ingress.world_open", Severity.HIGH, "aws_security_group.web"),
    "versioning = false": ("s3.versioning.disabled", Severity.LOW, "aws_s3_bucket.reports"),
}


class MarkerCheck:
    """Reports a finding for each marker string present in the workspace's .tf files."""

    name = "marker"

    def __init__(
        self,
        markers: dict[str, tuple[str, Severity, str]] | None = None,
        *,
        available: bool = True,
    ) -> None:
        self._markers = DEFAULT_MARKERS if markers is None else markers
        self._available = available
        self.runs = 0

    def is_available(self) -> ToolAvailability:
        if not self._available:
            return ToolAvailability.missing("marker", "not installed")
        return ToolAvailability.ok("marker", "1.0")

    def run(self, ctx: CheckContext) -> list[Finding]:
        self.runs += 1
        findings: list[Finding] = []
        for path in sorted(ctx.workspace.rglob("*.tf")):
            lines = path.read_text(encoding="utf-8").splitlines()
            relative = path.relative_to(ctx.workspace)
            for number, line in enumerate(lines, start=1):
                for marker, (rule_class, severity, address) in self._markers.items():
                    if marker in line:
                        findings.append(
                            Finding(
                                source=CheckSource.CHECKOV,
                                rule_id=f"MARK_{rule_class}",
                                rule_class=rule_class,
                                severity=severity,
                                resource_address=address,
                                resource_type=address.split(".")[0],
                                file=relative,
                                line_start=number,
                                line_end=number,
                                message=f"{marker.strip()} found",
                            )
                        )
        return findings


class ScriptedProvider:
    """Returns canned completions in order.

    A response may be an exception instance, which is raised instead of returned -- that
    is how the unreachable-provider and malformed-output paths are exercised.
    """

    name = "scripted"

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def describe(self) -> str:
        return "scripted (test double)"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise LLMError("scripted provider exhausted")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DeadProvider:
    """Never reachable. For the degradation path in invariant 4."""

    name = "dead"

    def describe(self) -> str:
        return "dead (test double)"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMUnavailable("connection refused")


INSECURE_TF = """resource "aws_s3_bucket" "reports" {
  bucket = "acme-reports"
  acl    = "public-read"
}

resource "aws_sns_topic" "alerts" {
  name = "alerts"
}
"""

SECURE_TF = """resource "aws_s3_bucket" "reports" {
  bucket = "acme-reports"
  acl    = "private"
}

resource "aws_sns_topic" "alerts" {
  name = "alerts"
}
"""


def write_workspace(root: Path, content: str = INSECURE_TF) -> Path:
    (root / "main.tf").write_text(content, encoding="utf-8")
    return root
