import unittest
from unittest.mock import patch

from minimal_cli_agent.exceptions import ModelRequestError
from minimal_cli_agent.model import ChatModel
from minimal_cli_agent.types import AgentConfig, Message


class ChatModelTest(unittest.TestCase):
    def test_openai_compatible_sends_max_output_tokens(self) -> None:
        captured: dict = {}

        def fake_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> dict:
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = headers
            captured["timeout"] = timeout
            return {"choices": [{"message": {"content": "ok"}}]}

        config = AgentConfig(
            provider="openai-compatible",
            model="test-model",
            base_url="https://api.example.com/v1",
            api_key="key",
            max_output_tokens=123,
        )

        with patch("minimal_cli_agent.model.post_json", fake_post_json):
            output = ChatModel(config).complete([Message(role="user", content="hello")])

        self.assertEqual(output, "ok")
        self.assertEqual(captured["payload"]["max_tokens"], 123)

    def test_anthropic_uses_bearer_for_auth_token_and_parses_text(self) -> None:
        captured: dict = {}

        def fake_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> dict:
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = headers
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

        config = AgentConfig(
            provider="anthropic",
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="oauth-token",
        )

        with patch("minimal_cli_agent.model.post_json", fake_post_json):
            output = ChatModel(config).complete([Message(role="user", content="hello")])

        self.assertEqual(output, "ok")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer oauth-token")
        self.assertNotIn("x-api-key", captured["headers"])

    def test_anthropic_parses_openai_compatible_proxy_shape(self) -> None:
        def fake_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> dict:
            return {"choices": [{"message": {"content": "proxy ok"}}]}

        config = AgentConfig(
            provider="anthropic",
            model="claude-sonnet-4-5",
            base_url="https://proxy.example.com",
            api_key="token",
        )

        with patch("minimal_cli_agent.model.post_json", fake_post_json):
            output = ChatModel(config).complete([Message(role="user", content="hello")])

        self.assertEqual(output, "proxy ok")

    def test_anthropic_empty_text_error_includes_response_diagnostics(self) -> None:
        def fake_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> dict:
            return {"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "max_tokens"}

        config = AgentConfig(
            provider="anthropic",
            model="claude-sonnet-4-5",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
        )

        with patch("minimal_cli_agent.model.post_json", fake_post_json):
            with self.assertRaisesRegex(ModelRequestError, "stop_reason=max_tokens"):
                ChatModel(config).complete([Message(role="user", content="hello")])


if __name__ == "__main__":
    unittest.main()
