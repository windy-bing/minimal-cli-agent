import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
