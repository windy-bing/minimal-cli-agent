import unittest

from minimal_cli_agent.context import CompactingContextManager, RUNTIME_CONTEXT_OPEN, build_runtime_context_fragments, is_runtime_context_message
from minimal_cli_agent.context_fragments import ContextFragment, assemble_context_fragments, build_context_fragments_message, is_context_fragments_message
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
        self.assertTrue(is_runtime_context_message(prepared[1]))
        self.assertTrue(any("Context was compacted locally" in message.content for message in prepared))

    def test_model_summary_context_is_opt_in(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=False)
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(model.calls, 0)
        self.assertTrue(any("Context was compacted locally" in message.content for message in prepared))

    def test_model_summary_context_keeps_system_summary_and_tail(self) -> None:
        model = SummaryModel()
        config = AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2)
        manager = CompactingContextManager(config, summarizer=model)

        prepared = manager.prepare(build_messages())

        self.assertEqual(model.calls, 1)
        self.assertEqual(prepared[0].role, "system")
        self.assertTrue(is_runtime_context_message(prepared[1]))
        self.assertIn("Context summary from earlier messages", prepared[2].content)
        self.assertIn("tests kept green", prepared[2].content)
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

        summary = next(message.content for message in prepared if "Context summary from earlier messages" in message.content)
        self.assertIn("Initial user goal:\nreal task", summary)
        self.assertNotIn("old nested summary", summary)

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

        self.assertEqual(prepared[0], build_messages()[0])
        self.assertTrue(is_runtime_context_message(prepared[1]))
        self.assertEqual(prepared[2:], build_messages()[1:])
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
        summary = next(message.content for message in prepared if "Context summary from earlier messages" in message.content)
        self.assertIn("Initial user goal:", summary)
        self.assertIn("old user", summary)

    def test_runtime_context_is_inserted_after_system(self) -> None:
        manager = CompactingContextManager(AgentConfig(model="demo-model", permission_mode="plan"))

        prepared = manager.prepare([Message(role="system", content="system"), Message(role="user", content="task")])

        self.assertEqual(prepared[0].content, "system")
        self.assertTrue(is_runtime_context_message(prepared[1]))
        self.assertTrue(is_context_fragments_message(prepared[1]))
        self.assertIn('"model": "demo-model"', prepared[1].content)
        self.assertIn('"permission_mode": "plan"', prepared[1].content)
        self.assertEqual(prepared[2].content, "task")

    def test_runtime_context_is_replaced_not_duplicated(self) -> None:
        first = CompactingContextManager(AgentConfig(model="first", permission_mode="plan"))
        prepared = first.prepare([Message(role="system", content="system"), Message(role="user", content="task")])
        second = CompactingContextManager(AgentConfig(model="second", permission_mode="autoEdit"))

        prepared_again = second.prepare(prepared)

        runtime_contexts = [message for message in prepared_again if is_runtime_context_message(message)]
        self.assertEqual(len(runtime_contexts), 1)
        self.assertIn('"model": "second"', runtime_contexts[0].content)
        self.assertNotIn('"model": "first"', runtime_contexts[0].content)
        self.assertEqual([message.content for message in prepared_again if message.content == "task"], ["task"])

    def test_runtime_context_is_not_used_as_initial_goal(self) -> None:
        manager = CompactingContextManager(AgentConfig(max_context_chars=10, summarize_context=True, context_tail_messages=2), summarizer=SummaryModel())
        messages = [
            Message(role="system", content="system prompt"),
            Message(role="user", content=f"{RUNTIME_CONTEXT_OPEN}\nstale\n</minimal_agent_runtime_context>"),
            Message(role="user", content="real task"),
            Message(role="assistant", content="old assistant " * 10),
            Message(role="user", content="recent user"),
            Message(role="assistant", content="recent assistant"),
        ]

        prepared = manager.prepare(messages)

        summary = next(message.content for message in prepared if "Context summary from earlier messages" in message.content)
        self.assertIn("Initial user goal:\nreal task", summary)

    def test_context_fragments_are_stably_sorted_and_deduplicated(self) -> None:
        fragments = [
            ContextFragment(kind="environment_state", id="runtime", content="old", priority=30),
            ContextFragment(kind="permission_policy", id="runtime", content="policy", priority=20),
            ContextFragment(kind="environment_state", id="runtime", content="new", priority=10),
        ]

        assembled = assemble_context_fragments(fragments)

        self.assertEqual([(fragment.kind, fragment.content) for fragment in assembled], [("environment_state", "new"), ("permission_policy", "policy")])

    def test_context_fragment_message_truncates_to_budget(self) -> None:
        message = build_context_fragments_message(
            [ContextFragment(kind="project_rules", id="rules", content="x" * 1000, priority=10)],
            max_chars=220,
        )

        self.assertIsNotNone(message)
        if message is None:
            return
        self.assertIn("truncated by context fragment budget", message.content)

    def test_runtime_fragments_include_expected_kinds(self) -> None:
        fragments = build_runtime_context_fragments(AgentConfig(permission_mode="plan"), world_state_delta={"hash": "abc", "changed": {}})

        self.assertIn("permission_policy", {fragment.kind for fragment in fragments})
        self.assertIn("environment_state", {fragment.kind for fragment in fragments})
        self.assertIn("context_budget", {fragment.kind for fragment in fragments})


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
