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
    def test_default_context_budget_compacts_large_local_history(self) -> None:
        model = SummaryModel()
        config = AgentConfig()
        manager = CompactingContextManager(config, summarizer=model)
        messages = [Message(role="system", content="system")]
        messages.extend(Message(role="user", content=f"old {index} " + ("x" * 1200)) for index in range(20))

        prepared = manager.prepare(messages)

        self.assertEqual(model.calls, 0)
        self.assertLess(sum(len(message.content) for message in prepared), sum(len(message.content) for message in messages))
        self.assertIn("Context was compacted locally", prepared[1].content)

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

    def test_model_summary_context_skips_prior_summary_as_initial_goal(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)
        messages = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="Initial user goal:\nold nested summary"),
            Message(role="user", content="real task"),
            Message(role="assistant", content="old assistant " * 10),
            Message(role="user", content="recent user"),
            Message(role="assistant", content="recent assistant"),
        ]

        prepared = manager.prepare(messages)

        self.assertIn("Initial user goal:\nreal task", prepared[1].content)
        self.assertNotIn("old nested summary", prepared[1].content)

    def test_model_summary_context_uses_cache_for_same_older_messages(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)

        manager.prepare(build_messages())
        manager.prepare(build_messages())

        self.assertEqual(model.calls, 1)

    def test_model_summary_cache_is_bounded(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)

        for index in range(manager.SUMMARY_CACHE_MAX_ENTRIES + 5):
            messages = build_messages()
            messages[1] = Message(role="user", content=f"old user {index} " * 10)
            manager.prepare(messages)

        self.assertEqual(len(manager.summary_cache), manager.SUMMARY_CACHE_MAX_ENTRIES)

    def test_context_does_not_compact_until_model_token_threshold(self) -> None:
        model = SummaryModel()
        config = AgentConfig(
            max_context_chars=10,
            model_context_tokens=10_000,
            context_compression_ratio=0.85,
            summarize_context=True,
        )
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(prepared, build_messages())
        self.assertEqual(model.calls, 0)

    def test_context_compacts_when_model_token_threshold_is_reached(self) -> None:
        model = SummaryModel()
        config = AgentConfig(
            max_context_chars=10_000,
            model_context_tokens=20,
            context_compression_ratio=0.5,
            summarize_context=True,
            context_tail_messages=2,
        )
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(model.calls, 1)
        self.assertIn("Initial user goal:", prepared[1].content)
        self.assertIn("old user", prepared[1].content)


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
