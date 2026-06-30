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

    def test_ollama_streams_message_chunks(self) -> None:
        captured: dict = {}

        def fake_stream_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120):
            captured["url"] = url
            captured["payload"] = payload
            yield {"message": {"content": "hel"}}
            yield {"message": {"content": "lo"}, "done": True}

        config = AgentConfig(provider="ollama", model="qwen", base_url="http://ollama")

        with patch("minimal_cli_agent.model.stream_json", fake_stream_json):
            chunks = list(ChatModel(config).stream_complete([Message(role="user", content="hello")]))

        self.assertEqual(chunks, ["hel", "lo"])
        self.assertTrue(captured["payload"]["stream"])

    def test_openai_compatible_streams_delta_chunks(self) -> None:
        def fake_stream_sse_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120):
            yield {"choices": [{"delta": {"content": "hel"}}]}
            yield {"choices": [{"delta": {"content": "lo"}}]}

        config = AgentConfig(provider="openai-compatible", model="test-model", base_url="https://api.example.com/v1", api_key="key")

        with patch("minimal_cli_agent.model.stream_sse_json", fake_stream_sse_json):
            chunks = list(ChatModel(config).stream_complete([Message(role="user", content="hello")]))

        self.assertEqual(chunks, ["hel", "lo"])

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

    def test_anthropic_streams_text_deltas(self) -> None:
        def fake_stream_sse_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120):
            yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hel"}}
            yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}}

        config = AgentConfig(provider="anthropic", model="claude-test", base_url="https://api.anthropic.com", api_key="sk-ant-test")

        with patch("minimal_cli_agent.model.stream_sse_json", fake_stream_sse_json):
            chunks = list(ChatModel(config).stream_complete([Message(role="user", content="hello")]))

        self.assertEqual(chunks, ["hel", "lo"])

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

    def test_gemini_sends_api_key_in_header_not_url(self) -> None:
        captured: dict = {}

        def fake_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> dict:
            captured["url"] = url
            captured["headers"] = headers
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        config = AgentConfig(
            provider="gemini",
            model="gemini-test",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="secret-key",
        )

        with patch("minimal_cli_agent.model.post_json", fake_post_json):
            output = ChatModel(config).complete([Message(role="user", content="hello")])

        self.assertEqual(output, "ok")
        self.assertNotIn("secret-key", captured["url"])
        self.assertEqual(captured["headers"], {"x-goog-api-key": "secret-key"})

    def test_gemini_streams_part_text(self) -> None:
        def fake_stream_sse_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120):
            yield {"candidates": [{"content": {"parts": [{"text": "hel"}]}}]}
            yield {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]}

        config = AgentConfig(provider="gemini", model="gemini-test", base_url="https://generativelanguage.googleapis.com/v1beta", api_key="secret-key")

        with patch("minimal_cli_agent.model.stream_sse_json", fake_stream_sse_json):
            chunks = list(ChatModel(config).stream_complete([Message(role="user", content="hello")]))

        self.assertEqual(chunks, ["hel", "lo"])


if __name__ == "__main__":
    unittest.main()
