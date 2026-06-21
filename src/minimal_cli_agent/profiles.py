from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.types import AgentConfig, ProfileName


def resolve_profile(config: AgentConfig, profile: ProfileName | None) -> AgentConfig:
    if profile is None:
        return config
    if profile == "ollama":
        return resolve_ollama(config)
    if profile == "codex":
        return resolve_codex(config)
    if profile == "claude":
        return resolve_claude(config)
    if profile == "gemini":
        return resolve_gemini(config)
    return config


def resolve_ollama(config: AgentConfig) -> AgentConfig:
    config.provider = "ollama"
    config.model = os.getenv("OLLAMA_MODEL", config.model)
    config.base_url = os.getenv("OLLAMA_BASE_URL", config.base_url)
    return config


def resolve_codex(config: AgentConfig) -> AgentConfig:
    codex_config = Path.home() / ".codex" / "config.toml"
    codex_auth = Path.home() / ".codex" / "auth.json"
    data = read_toml(codex_config)
    auth = read_json(codex_auth)

    config.model = os.getenv("OPENAI_MODEL") or str(data.get("model") or config.model)

    user_openai_key = os.getenv("OPENAI_API_KEY") or config.api_key or auth.get("OPENAI_API_KEY")
    user_openai_base_url = os.getenv("OPENAI_BASE_URL")
    if user_openai_key or user_openai_base_url:
        config.provider = "openai-compatible"
        config.base_url = user_openai_base_url or "https://api.openai.com/v1"
        config.api_key = user_openai_key
        return config

    codex_access_token = nested_get(auth, ["tokens", "access_token"])
    if codex_access_token:
        config.provider = "codex"
        config.base_url = "codex-cli"
        config.api_key = None
        return config

    if not user_openai_key:
        raise ConfigurationError(
            "--profile codex read ~/.codex/config.toml, but no usable credential was found. "
            "Set OPENAI_API_KEY/OPENAI_BASE_URL for an OpenAI-compatible endpoint, or sign in with Codex "
            "so ~/.codex/auth.json contains tokens.access_token."
        )
    return config


def resolve_claude(config: AgentConfig) -> AgentConfig:
    settings = read_json(Path.home() / ".claude" / "settings.json")
    env = settings.get("env", {}) if isinstance(settings.get("env"), dict) else {}

    config.provider = "anthropic"
    config.model = os.getenv("ANTHROPIC_MODEL") or str(settings.get("model") or "claude-sonnet-4-5")
    config.base_url = os.getenv("ANTHROPIC_BASE_URL") or str(env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
    config.api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or str(env.get("ANTHROPIC_AUTH_TOKEN") or "")
    return config


def resolve_gemini(config: AgentConfig) -> AgentConfig:
    config.provider = "gemini"
    config.model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
    config.base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    config.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or config.api_key
    return config


def read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def nested_get(data: dict, path: list[str]) -> str | None:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None
