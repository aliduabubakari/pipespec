from __future__ import annotations

import sys
import types

from pipespec_validator.llm_runtime import (
    LLMConfig,
    build_llm_config,
    call_llm_json,
    detect_api_key_source,
    default_base_url_for_provider,
    default_model_for_provider,
    normalize_provider,
    parse_json_object,
)


def test_provider_alias_normalization():
    assert normalize_provider("claude") == "anthropic"


def test_default_provider_model_and_base_url():
    assert default_model_for_provider("openai") == "gpt-4o-mini"
    assert default_base_url_for_provider("deepinfra") == "https://api.deepinfra.com/v1/openai"


def test_explicit_config_overrides():
    cfg = build_llm_config(
        provider="openai_compatible",
        model="my-model",
        api_key="abc123",
        base_url="https://example.com/v1",
    )
    assert cfg.provider == "openai_compatible"
    assert cfg.model == "my-model"
    assert cfg.api_key == "abc123"
    assert cfg.base_url == "https://example.com/v1"


def test_api_key_env_resolution(monkeypatch):
    monkeypatch.setenv("CUSTOM_KEY_ENV", "from-env")
    cfg = build_llm_config(
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="CUSTOM_KEY_ENV",
    )
    assert cfg.api_key == "from-env"


def test_parse_json_object_with_fenced_text():
    payload = """```json
{"ok": true, "count": 3}
```"""
    parsed = parse_json_object(payload)
    assert parsed["ok"] is True
    assert parsed["count"] == 3


def test_parse_json_object_with_preface_suffix():
    payload = 'Some text before {"alpha": 1, "beta": "x"} trailing text'
    parsed = parse_json_object(payload)
    assert parsed == {"alpha": 1, "beta": "x"}


def test_detect_api_key_source_with_override(monkeypatch):
    monkeypatch.setenv("MY_KEY_ENV", "x")
    assert detect_api_key_source("openai", api_key_env="MY_KEY_ENV") == "env:MY_KEY_ENV"


def test_openrouter_headers_injected(monkeypatch):
    seen_kwargs = {}

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            msg = types.SimpleNamespace(content='{"ok": true}')
            choice = types.SimpleNamespace(message=msg)
            return types.SimpleNamespace(choices=[choice])

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen_kwargs.update(kwargs)
            self.chat = _FakeChat()

    fake_module = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    monkeypatch.setenv("PIPESPEC_OPENROUTER_SITE_URL", "https://example.com")
    monkeypatch.setenv("PIPESPEC_OPENROUTER_APP_NAME", "PipeSpecApp")

    cfg = LLMConfig(
        provider="openrouter",
        model="openai/gpt-4o-mini",
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
    )
    out = call_llm_json(
        config=cfg,
        system_prompt="sys",
        user_prompt="usr",
        max_tokens=64,
        temperature=0.1,
    )

    assert out == '{"ok": true}'
    assert seen_kwargs["default_headers"]["HTTP-Referer"] == "https://example.com"
    assert seen_kwargs["default_headers"]["X-Title"] == "PipeSpecApp"
