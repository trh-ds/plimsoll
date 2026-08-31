"""Any endpoint that speaks the OpenAI chat-completions API.

That covers agent routers (OpenRouter, LiteLLM, Portkey), self-hosted gateways, and the
OpenAI API itself. One adapter reaches all of them, which is the reason this is the
default provider rather than a vendor-specific one.

Uses `urllib` from the standard library. An HTTP client is not worth a dependency for a
single POST, and every dependency in a security tool is a dependency someone has to
audit.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from plimsoll.core.models import ToolAvailability
from plimsoll.llm import LLMConfig, LLMError, LLMUnavailable, register

# How much of an error body to surface. Enough to see a provider's error message,
# bounded so a HTML error page does not fill the terminal.
_ERROR_BODY_CHARS = 400


def _endpoint(base_url: str) -> str:
    """Normalise a router base URL to a chat-completions endpoint.

    Routers are documented inconsistently: some publish `https://host/api/v1`, some
    publish the full completions path. Accept both rather than making the user guess.
    """
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def parse_completion(payload: object) -> str:
    """Pull the assistant text out of a chat-completions response.

    Separated from the request so it is testable without a network call, and strict
    about shape: a response we cannot read is an error, never an empty patch.
    """
    if not isinstance(payload, dict):
        raise LLMError(f"expected a JSON object, got {type(payload).__name__}")

    if "error" in payload:
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise LLMError(f"provider returned an error: {message}")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("response contained no choices")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise LLMError("first choice contained no message")

    content = message.get("content")
    # Some routers return content as a list of typed parts rather than a string.
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    if not isinstance(content, str) or not content.strip():
        finish = choices[0].get("finish_reason")
        raise LLMError(f"response contained no text (finish_reason={finish!r})")
    return content


@register("openai_compat")
class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def is_available(self) -> ToolAvailability:
        """Configuration only. Contacting the router would spend tokens to answer a
        question the user asked about setup."""
        missing = [
            name
            for name, value in (
                ("PLIMSOLL_LLM_BASE_URL", self._config.base_url),
                ("PLIMSOLL_LLM_MODEL", self._config.model),
            )
            if not value
        ]
        if missing:
            return ToolAvailability.missing("model", f"{' and '.join(missing)} not set")
        if not self._config.api_key:
            # Not fatal: some gateways authenticate by network position. Said out loud
            # because "401 from the router" is otherwise a confusing first failure.
            return ToolAvailability.ok(
                "model", f"{self._config.model} (no PLIMSOLL_LLM_API_KEY set)"
            )
        return ToolAvailability.ok("model", self._config.model)

    def describe(self) -> str:
        # Never the key. `doctor` output ends up pasted into bug reports.
        base = self._config.base_url or "<PLIMSOLL_LLM_BASE_URL unset>"
        model = self._config.model or "<PLIMSOLL_LLM_MODEL unset>"
        keyed = "key set" if self._config.api_key else "no key"
        return f"openai_compat ({model} via {base}, {keyed})"

    def _require_config(self) -> tuple[str, str]:
        if not self._config.base_url:
            raise LLMUnavailable("PLIMSOLL_LLM_BASE_URL is not set")
        if not self._config.model:
            raise LLMUnavailable("PLIMSOLL_LLM_MODEL is not set")
        return self._config.base_url, self._config.model

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        base_url, model = self._require_config()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                # Determinism matters more than variety here. Two runs over the same
                # finding should not produce different patches for reasons the
                # provenance record cannot explain.
                "temperature": self._config.temperature,
            }
        ).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        request = urllib.request.Request(
            _endpoint(base_url), data=body, headers=headers, method="POST"
        )

        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:_ERROR_BODY_CHARS]
            raise LLMError(f"provider returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMUnavailable(f"could not reach {base_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMUnavailable(
                f"provider timed out after {self._config.timeout_seconds}s"
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"provider did not return JSON: {raw[:_ERROR_BODY_CHARS]}") from exc

        return parse_completion(payload)
