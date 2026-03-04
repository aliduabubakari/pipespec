from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal


ProviderName = Literal[
    "openai_compatible",
    "openai",
    "deepinfra",
    "deepseek",
    "openrouter",
    "ollama",
    "anthropic",
    "claude",
]


_PROVIDER_ALIASES: dict[str, str] = {
    "claude": "anthropic",
}

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai_compatible": "https://api.openai.com/v1",
    "openai": "https://api.openai.com/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

_DEFAULT_MODELS: dict[str, str] = {
    "openai_compatible": "gpt-4o-mini",
    "openai": "gpt-4o-mini",
    "deepinfra": "Qwen/Qwen2.5-Coder-32B-Instruct",
    "deepseek": "deepseek-chat",
    "openrouter": "openai/gpt-4o-mini",
    "ollama": "llama3.1",
    "anthropic": "claude-3-5-sonnet-latest",
}

_KEY_ENV_PRIORITY: dict[str, list[str]] = {
    "openai_compatible": ["OPENAI_API_KEY", "PIPESPEC_LLM_API_KEY"],
    "openai": ["OPENAI_API_KEY", "PIPESPEC_LLM_API_KEY"],
    "deepinfra": ["DEEPINFRA_API_TOKEN", "DEEPINFRA_API_KEY", "PIPESPEC_LLM_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY", "PIPESPEC_LLM_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY", "PIPESPEC_LLM_API_KEY"],
    "ollama": [],
    "anthropic": ["ANTHROPIC_API_KEY", "PIPESPEC_LLM_API_KEY"],
}


def supported_providers() -> list[str]:
    return [
        "openai_compatible",
        "openai",
        "deepinfra",
        "deepseek",
        "openrouter",
        "ollama",
        "anthropic",
    ]


def normalize_provider(provider: str) -> str:
    key = provider.strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def default_model_for_provider(provider: str) -> str:
    normalized = normalize_provider(provider)
    if normalized not in _DEFAULT_MODELS:
        raise ValueError(f"Unsupported provider: {provider}")
    return _DEFAULT_MODELS[normalized]


def default_base_url_for_provider(provider: str) -> str | None:
    normalized = normalize_provider(provider)
    return _DEFAULT_BASE_URLS.get(normalized)


def resolve_api_key(provider: str, explicit_api_key: str | None) -> str | None:
    normalized = normalize_provider(provider)
    if explicit_api_key:
        return explicit_api_key
    for env_name in _KEY_ENV_PRIORITY.get(normalized, []):
        value = os.environ.get(env_name)
        if value:
            return value
    if normalized == "ollama":
        return "ollama"
    return None


def detect_api_key_source(provider: str, api_key_env: str | None = None) -> str:
    normalized = normalize_provider(provider)
    if normalized == "ollama":
        return "not-required"
    if api_key_env:
        if os.environ.get(api_key_env):
            return f"env:{api_key_env}"
    for env_name in _KEY_ENV_PRIORITY.get(normalized, []):
        if os.environ.get(env_name):
            return f"env:{env_name}"
    return "missing"


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str | None


def build_llm_config(
    *,
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> LLMConfig:
    normalized = normalize_provider(provider)
    if normalized not in _DEFAULT_MODELS:
        raise ValueError(
            "Unsupported provider. Allowed: openai, claude, deepinfra, deepseek, "
            "openrouter, ollama, openai_compatible."
        )

    resolved_model = model or default_model_for_provider(normalized)
    resolved_base_url = base_url or default_base_url_for_provider(normalized)
    resolved_api_key = api_key
    if not resolved_api_key and api_key_env:
        resolved_api_key = os.environ.get(api_key_env)
    if not resolved_api_key:
        resolved_api_key = resolve_api_key(normalized, api_key)
    return LLMConfig(
        provider=normalized,
        model=resolved_model,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
    )


def _anthropic_text_from_response(resp: object) -> str:
    content = getattr(resp, "content", [])
    text_chunks: list[str] = []
    for chunk in content:
        if getattr(chunk, "type", None) == "text":
            text = getattr(chunk, "text", None)
            if text:
                text_chunks.append(text)
    return "\n".join(text_chunks).strip()


def call_llm_json(
    *,
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> str:
    provider = config.provider
    if provider == "anthropic":
        if not config.api_key:
            raise ValueError("Missing API key for provider 'anthropic'.")
        try:
            from anthropic import Anthropic
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "The 'anthropic' package is required for provider=claude/anthropic. "
                "Install with: pip install anthropic"
            ) from e

        client = Anthropic(api_key=config.api_key)
        resp = client.messages.create(
            model=config.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return _anthropic_text_from_response(resp)

    if not config.api_key and provider != "ollama":
        raise ValueError(f"Missing API key for provider '{provider}'.")
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "The 'openai' package is required for OpenAI-compatible providers. "
            "Install with: pip install openai"
        ) from e

    default_headers = None
    if provider == "openrouter":
        site_url = os.environ.get("PIPESPEC_OPENROUTER_SITE_URL")
        app_name = os.environ.get("PIPESPEC_OPENROUTER_APP_NAME")
        if site_url or app_name:
            default_headers = {}
            if site_url:
                default_headers["HTTP-Referer"] = site_url
            if app_name:
                default_headers["X-Title"] = app_name

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, default_headers=default_headers)
    resp = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=temperature,
    )
    msg = resp.choices[0].message
    return msg.content or ""


def parse_json_object(content: str) -> dict:
    text = content.strip()

    # Common wrapper from some models.
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object, got {type(value).__name__}")
        return value
    except Exception:
        pass

    # Fallback: find first balanced {...} block and parse that.
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model output.")

    depth = 0
    in_str = False
    escape = False
    end = -1
    for i, ch in enumerate(text[start:], start=start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end < 0:
        raise ValueError("Could not locate a complete JSON object in model output.")

    candidate = text[start : end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object, got {type(value).__name__}")
    return value
