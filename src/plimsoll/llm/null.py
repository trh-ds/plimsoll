"""The provider that does nothing.

Selected by `--no-llm` and by every test that is not specifically testing a provider.
It exists so the loop has a real implementation to run against before any network code
does, and so invariant 4 -- deterministic-only mode always works -- has something to be
true of.

`complete` raises rather than returning empty text. A provider that silently returns ""
would push a nonsense patch into the verifier and waste a gate run proving it is
nonsense; refusing up front is honest and cheaper.
"""

from __future__ import annotations

from plimsoll.core.models import ToolAvailability
from plimsoll.llm import LLMConfig, LLMUnavailable, register


@register("null")
class NullProvider:
    name = "null"

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig(provider="null")

    def is_available(self) -> ToolAvailability:
        return ToolAvailability.missing("model", "the null provider makes no calls")

    def describe(self) -> str:
        return "null (no model; deterministic reporting only)"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMUnavailable("the null provider does not call a model")
