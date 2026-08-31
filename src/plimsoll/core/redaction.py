"""Secret redaction.

Two call sites, and the second is newer than this project's original design:

1. Provenance records, per invariant 6.
2. **Every prompt.** Inference is hosted, so the offending HCL block and the whole file
   around it leave this machine. Terraform files are exactly where a hardcoded
   credential lives -- `provider.static_credentials` and `secret.hardcoded` are two of
   the twenty rules in the bundled pack -- so the content most likely to trip a rule is
   also the content most dangerous to transmit.

Redaction is deliberately over-eager. A false positive costs the model a little context
and may cost one retry. A false negative posts a live credential to a third party.

Prompt redaction has to be **reversible**. The model returns a whole file; if we wrote
that back with the placeholder still in it, we would hand the user a patch that silently
destroys a working credential. `Redactor` numbers its placeholders and restores them
after the model answers. `redact()` is the one-way version, for provenance and logs.
"""

from __future__ import annotations

import re
from collections.abc import Callable

REDACTED = "[REDACTED]"

_PLACEHOLDER = re.compile(r"\[REDACTED:(\d+)\]")

# Ordered: the first pattern to match a span wins. The keyed-assignment rule comes first
# so `secret_key = "AKIA..."` is caught with its attribute name left intact, rather than
# by the bare AWS-key rule.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b\w*(?:secret|password|passwd|token|api[_-]?key|access[_-]?key
            |private[_-]?key|credential|auth)\w*\b \s* [:=] \s*)
    (["'])(?P<value>(?:[^"'\\]|\\.){4,})\2
    """
)

_BARE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Provider-issued identifiers recognisable on their own.
    re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),  # GitHub token
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),  # Slack token
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),  # OpenAI-style key
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API key
    re.compile(r"(?s)-----BEGIN[^-]{0,40}PRIVATE KEY-----.*?-----END[^-]{0,40}PRIVATE KEY-----"),
    # JWT: three base64url segments, long enough not to collide with ordinary HCL.
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def _scrub(text: str, token_for: Callable[[str], str]) -> str:
    """Walk every pattern, replacing matched secrets with `token_for(secret)`."""

    def mask_assignment(match: re.Match[str]) -> str:
        # Keep the attribute name and the quotes. The model still needs to see *that*
        # `secret_key` is set inline -- that is the violation -- just not what it is.
        return f"{match.group(1)}{match.group(2)}{token_for(match.group('value'))}{match.group(2)}"

    text = _ASSIGNMENT.sub(mask_assignment, text)
    for pattern in _BARE_PATTERNS:
        text = pattern.sub(lambda m: token_for(m.group(0)), text)
    return text


def redact(text: str) -> str:
    """One-way redaction. For provenance records, logs, and error messages."""
    if not text:
        return text
    return _scrub(text, lambda _: REDACTED)


def contains_secret(text: str) -> bool:
    """True when `redact` would change `text`."""
    return redact(text) != text


class Redactor:
    """Reversible redaction for one finding's prompt cycle.

    Placeholders are numbered so distinct secrets stay distinct: a file with two
    different keys must not have them collapse into one value on the way back.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._tokens: dict[str, str] = {}

    @property
    def count(self) -> int:
        return len(self._values)

    def _token_for(self, secret: str) -> str:
        # Same secret twice in a file gets the same placeholder, so the model sees the
        # two occurrences as equal -- which they are.
        existing = self._tokens.get(secret)
        if existing is not None:
            return existing
        token = f"[REDACTED:{len(self._values) + 1}]"
        self._values[token] = secret
        self._tokens[secret] = token
        return token

    def redact(self, text: str) -> str:
        if not text:
            return text
        return _scrub(text, self._token_for)

    def restore(self, text: str) -> str:
        """Put the real secrets back into model output.

        An unknown placeholder -- the model invented `[REDACTED:9]` -- is left as-is.
        The verifier will then see a literal that does not parse as a credential, the
        patch will fail re-verification, and it will never reach a human. Guessing which
        secret was meant would be worse than failing.
        """
        if not self._values:
            return text
        return _PLACEHOLDER.sub(lambda m: self._values.get(m.group(0), m.group(0)), text)
