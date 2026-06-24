import unittest

from minimal_cli_agent.context import CompactingContextManager
from minimal_cli_agent.types import AgentConfig, Message


class SummaryModel:
    def __init__(self) -> None:
        self.calls = 0
        self.last_messages: list[Message] = []

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        self.last_messages = messages
        return "Older context says the user wants tests kept green."


class ContextTest(unittest.TestCase):
    def test_model_summary_context_is_opt_in(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=False)
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(model.calls, 0)
        self.assertIn("Context was compacted locally", prepared[1].content)

    def test_model_summary_context_keeps_system_summary_and_tail(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(model.calls, 1)
        self.assertEqual(prepared[0].role, "system")
        self.assertIn("Context summary from earlier messages", prepared[1].content)
        self.assertIn("tests kept green", prepared[1].content)
        self.assertEqual([message.content for message in prepared[-2:]], ["recent user", "recent assistant"])
        self.assertIn("Summarize this prior transcript", model.last_messages[-1].content)

    def test_model_summary_context_uses_cache_for_same_older_messages(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)

        manager.prepare(build_messages())
        manager.prepare(build_messages())

        self.assertEqual(model.calls, 1)


def build_messages() -> list[Message]:
    return [
        Message(role="system", content="system prompt"),
        Message(role="user", content="old user " * 10),
        Message(role="assistant", content="old assistant " * 10),
        Message(role="user", content="recent user"),
        Message(role="assistant", content="recent assistant"),
    ]


if __name__ == "__main__":
    unittest.main()
