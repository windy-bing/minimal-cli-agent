import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.profiles import nested_get, resolve_codex, resolve_gemini, resolve_ollama, read_json, read_toml
from minimal_cli_agent.types import AgentConfig


class ProfileTest(unittest.TestCase):
    def test_read_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('model = "gpt-test"\n', encoding="utf-8")
            self.assertEqual(read_toml(path)["model"], "gpt-test")

    def test_read_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"model": "claude-test"}', encoding="utf-8")
            self.assertEqual(read_json(path)["model"], "claude-test")

    def test_resolve_gemini_uses_env(self) -> None:
        original = AgentConfig(provider="ollama", model="original", api_key="old")
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-test", "GEMINI_API_KEY": "key"}, clear=False):
            config = resolve_gemini(original)

        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.model, "gemini-test")
        self.assertEqual(config.api_key, "key")
        self.assertEqual(original.provider, "ollama")
        self.assertEqual(original.model, "original")
        self.assertEqual(original.api_key, "old")

    def test_resolve_ollama_uses_env_when_cli_option_is_not_explicit(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "env-model", "OLLAMA_BASE_URL": "http://env-ollama:11434"},
            clear=False,
        ):
            config = resolve_ollama(AgentConfig(model="default-model", base_url="http://default:11434"))

        self.assertEqual(config.provider, "ollama")
        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.base_url, "http://env-ollama:11434")

    def test_resolve_ollama_keeps_explicit_cli_model_and_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "env-model", "OLLAMA_BASE_URL": "http://env-ollama:11434"},
            clear=False,
        ):
            config = resolve_ollama(
                AgentConfig(model="cli-model", base_url="http://cli-ollama:11434"),
                explicit_options={"model", "base_url"},
            )

        self.assertEqual(config.provider, "ollama")
        self.assertEqual(config.model, "cli-model")
        self.assertEqual(config.base_url, "http://cli-ollama:11434")

    def test_resolve_codex_requires_openai_key(self) -> None:
        with patch.dict(os.environ, {"HOME": "/tmp/no-such-home"}, clear=True):
            with self.assertRaises(ConfigurationError):
                resolve_codex(AgentConfig())

    def test_resolve_codex_uses_codex_provider_for_auth_json_access_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
            (codex_home / "auth.json").write_text(
                '{"tokens": {"access_token": "codex-token"}}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HOME": tmp}, clear=True):
                config = resolve_codex(AgentConfig())

        self.assertEqual(config.provider, "codex")
        self.assertEqual(config.model, "gpt-test")
        self.assertEqual(config.base_url, "codex-cli")
        self.assertIsNone(config.api_key)

    def test_resolve_codex_uses_openai_compatible_for_explicit_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
            (codex_home / "auth.json").write_text(
                '{"tokens": {"access_token": "codex-token"}}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HOME": tmp, "OPENAI_API_KEY": "openai-key"}, clear=True):
                config = resolve_codex(AgentConfig())

        self.assertEqual(config.provider, "openai-compatible")
        self.assertEqual(config.base_url, "https://api.openai.com/v1")
        self.assertEqual(config.api_key, "openai-key")

    def test_nested_get(self) -> None:
        self.assertEqual(nested_get({"a": {"b": "c"}}, ["a", "b"]), "c")
        self.assertIsNone(nested_get({"a": {}}, ["a", "b"]))


if __name__ == "__main__":
    unittest.main()
