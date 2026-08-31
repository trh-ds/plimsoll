"""Model providers.

Everything provider-specific lives in this package: base URLs, model identifiers,
request shapes, and the environment variables that select them. Nothing outside
`plimsoll.llm` names a model or a provider. That rule is what makes "model-agnostic" a
property of the code rather than an intention in a README.

The provider is never an authority. It returns text. `core.loop` turns that text into a
candidate patch and `core.verifier` re-runs the deterministic gate to judge it. There is
no path from a provider's output to `Decision.status`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field

from plimsoll.core.models import ToolAvailability

DEFAULT_PROVIDER = "openai_compat"
DEFAULT_TIMEOUT_SECONDS = 120


class LLMError(RuntimeError):
    """The provider could not produce a completion.

    Never fatal. `core.loop` records it, degrades to deterministic reporting, and the
    run still exits with a gate-derived code (invariant 4).
    """


class LLMUnavailable(LLMError):
    """The provider is not reachable or not configured. Distinguished from a bad
    response so `doctor` can tell "you have not set a key" from "the router 500ed"."""


class LLMConfig(BaseModel):
    """How to reach a provider. Populated from `PLIMSOLL_LLM_*` by `from_env`."""

    provider: str = DEFAULT_PROVIDER
    model: str = ""
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    temperature: float = 0.0
    max_retries: int = Field(default=2, ge=0)
    max_findings: int = Field(default=25, gt=0)

    @classmethod
    def from_env(cls) -> LLMConfig:
        """Read the documented environment variables.

        The API key is read here and nowhere else, is never logged, and never enters a
        provenance record or an error message.
        """
        return cls(
            provider=os.environ.get("PLIMSOLL_LLM_PROVIDER") or DEFAULT_PROVIDER,
            model=os.environ.get("PLIMSOLL_LLM_MODEL") or "",
            base_url=os.environ.get("PLIMSOLL_LLM_BASE_URL") or None,
            api_key=os.environ.get("PLIMSOLL_LLM_API_KEY") or None,
        )


class LLMProvider(Protocol):
    """One way of getting text out of a model.

    `is_available` mirrors `Check.is_available` deliberately, and reuses the same
    `ToolAvailability` model: an unconfigured model is the same class of fact as an
    uninstalled binary, and both must be reported rather than discovered mid-run.
    """

    name: str

    def is_available(self) -> ToolAvailability:
        """Whether this provider has what it needs. Never makes a network call."""
        ...

    def describe(self) -> str:
        """One line for `doctor`. Must never include the API key."""
        ...

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return the completion, or raise `LLMError`."""
        ...


ProviderFactory = Callable[[LLMConfig], LLMProvider]

_REGISTRY: dict[str, ProviderFactory] = {}


def register(name: str) -> Callable[[ProviderFactory], ProviderFactory]:
    def decorate(factory: ProviderFactory) -> ProviderFactory:
        if name in _REGISTRY:
            raise ValueError(f"provider already registered: {name}")
        _REGISTRY[name] = factory
        return factory

    return decorate


def _load_providers() -> None:
    # Imported for their registration side effects; deferred because they import this
    # module.
    from plimsoll.llm import null, openai_compat  # noqa: F401


def registered_names() -> list[str]:
    _load_providers()
    return sorted(_REGISTRY)


def build_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate the configured provider.

    Raises `LLMUnavailable` for an unknown name rather than falling back to a working
    provider. Silently substituting a different model than the one asked for would make
    provenance a lie.
    """
    _load_providers()
    factory = _REGISTRY.get(config.provider)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY))
        raise LLMUnavailable(f"unknown provider {config.provider!r} (known: {known})")
    return factory(config)


__all__ = [
    "DEFAULT_PROVIDER",
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMUnavailable",
    "build_provider",
    "register",
    "registered_names",
]
