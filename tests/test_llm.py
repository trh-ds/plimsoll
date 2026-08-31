from __future__ import annotations

import json
import urllib.error

import pytest

from plimsoll.llm import (
    LLMConfig,
    LLMError,
    LLMUnavailable,
    build_provider,
    openai_compat,
    registered_names,
)
from plimsoll.llm.openai_compat import _endpoint, parse_completion


def test_registry_exposes_every_provider():
    assert registered_names() == ["null", "openai_compat"]


def test_unknown_provider_raises_rather_than_falling_back():
    """Silently substituting a different model would make provenance a lie."""
    with pytest.raises(LLMUnavailable, match="unknown provider"):
        build_provider(LLMConfig(provider="gpt-9"))


def test_null_provider_refuses_instead_of_returning_empty_text():
    provider = build_provider(LLMConfig(provider="null"))
    with pytest.raises(LLMUnavailable):
        provider.complete("anything")


def test_config_reads_the_documented_environment(monkeypatch):
    monkeypatch.setenv("PLIMSOLL_LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("PLIMSOLL_LLM_MODEL", "claude-opus-5")
    monkeypatch.setenv("PLIMSOLL_LLM_BASE_URL", "https://router.example/api/v1")
    monkeypatch.setenv("PLIMSOLL_LLM_API_KEY", "sk-secret-value")

    config = LLMConfig.from_env()

    assert config.model == "claude-opus-5"
    assert config.base_url == "https://router.example/api/v1"
    assert config.api_key == "sk-secret-value"


def test_config_defaults_when_nothing_is_set(monkeypatch):
    for name in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(f"PLIMSOLL_LLM_{name}", raising=False)
    config = LLMConfig.from_env()
    assert config.provider == "openai_compat"
    assert config.api_key is None


def test_describe_never_leaks_the_key(monkeypatch):
    """`doctor` output gets pasted into bug reports."""
    config = LLMConfig(
        model="claude-opus-5",
        base_url="https://router.example/api/v1",
        api_key="sk-secret-value",
    )
    described = build_provider(config).describe()
    assert "sk-secret-value" not in described
    assert "key set" in described
    assert "claude-opus-5" in described


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://r.example/api/v1", "https://r.example/api/v1/chat/completions"),
        ("https://r.example/api/v1/", "https://r.example/api/v1/chat/completions"),
        (
            "https://r.example/api/v1/chat/completions",
            "https://r.example/api/v1/chat/completions",
        ),
    ],
)
def test_endpoint_accepts_either_documented_form(base, expected):
    assert _endpoint(base) == expected


def test_missing_configuration_is_unavailable_not_a_crash():
    provider = build_provider(LLMConfig(model="m"))
    with pytest.raises(LLMUnavailable, match="PLIMSOLL_LLM_BASE_URL"):
        provider.complete("hi")

    provider = build_provider(LLMConfig(base_url="https://r.example"))
    with pytest.raises(LLMUnavailable, match="PLIMSOLL_LLM_MODEL"):
        provider.complete("hi")


def test_parse_completion_reads_a_normal_response():
    payload = {"choices": [{"message": {"content": "hello"}}]}
    assert parse_completion(payload) == "hello"


def test_parse_completion_reads_typed_content_parts():
    """Some routers return content as a list of parts rather than a string."""
    payload = {
        "choices": [{"message": {"content": [{"type": "text", "text": "hel"}, {"text": "lo"}]}}]
    }
    assert parse_completion(payload) == "hello"


class FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_request_carries_the_key_the_model_and_the_prompt(monkeypatch):
    """No socket is opened. The rule is no network in unit tests, loopback included."""
    captured = {}

    def fake_urlopen(request, timeout=None) -> FakeResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"choices": [{"message": {"content": "patched"}}]}))

    monkeypatch.setattr(openai_compat.urllib.request, "urlopen", fake_urlopen)

    provider = build_provider(
        LLMConfig(
            model="claude-opus-5",
            base_url="https://router.example/api/v1",
            api_key="sk-secret-value",
            timeout_seconds=42,
        )
    )
    assert provider.complete("fix this", system="be terse") == "patched"

    assert captured["url"] == "https://router.example/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-secret-value"
    assert captured["body"]["model"] == "claude-opus-5"
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "fix this"},
    ]
    assert captured["timeout"] == 42


def test_an_unreachable_router_is_unavailable_not_a_crash(monkeypatch):
    def fake_urlopen(request, timeout=None) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(openai_compat.urllib.request, "urlopen", fake_urlopen)
    provider = build_provider(LLMConfig(model="m", base_url="https://router.example/v1"))

    with pytest.raises(LLMUnavailable, match="could not reach"):
        provider.complete("hi")


def test_a_non_json_body_is_an_error_not_an_empty_patch(monkeypatch):
    monkeypatch.setattr(
        openai_compat.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse("<html>502 Bad Gateway</html>"),
    )
    provider = build_provider(LLMConfig(model="m", base_url="https://router.example/v1"))

    with pytest.raises(LLMError, match="did not return JSON"):
        provider.complete("hi")


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"message": "insufficient credits"}},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": "  "}, "finish_reason": "length"}]},
        [],
    ],
)
def test_unreadable_responses_raise_instead_of_returning_empty(payload):
    """An empty patch would burn a gate run proving it is empty."""
    with pytest.raises(LLMError):
        parse_completion(payload)
