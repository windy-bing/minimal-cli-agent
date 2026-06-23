import unittest

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message


class FakeModel:
    def complete(self, messages: list[Message]) -> str:
        return "Done.\n```bash-action\nexit\n```"


class PlainTextModel:
    def complete(self, messages: list[Message]) -> str:
        return "你好，我可以帮你看代码、改文件或排查问题。"


class AgentTest(unittest.TestCase):
    def test_chat_stream_is_context_driven(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config=config, model=FakeModel())
        agent = Agent(config=config, harness=harness)

        stream = agent.chat_stream("finish", ChatContext(session_id="s1"))
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        self.assertTrue(result.success)
        self.assertEqual(events[0].type, "step_start")
        self.assertEqual(events[-1].type, "done")

    def test_strict_chat_stream_keeps_format_recovery(self) -> None:
        config = AgentConfig(permission_mode="plan", max_steps=1)
        harness = AgentHarness(config=config, model=PlainTextModel())
        agent = Agent(config=config, harness=harness)

        stream = agent.chat_stream("hello", ChatContext())
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        observations = [event.data.get("observation", "") for event in events if event.type == "tool_call_result"]
        self.assertFalse(result.success)
        self.assertTrue(any("Your output was malformed." in observation for observation in observations))

    def test_interactive_chat_stream_accepts_plain_text(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config=config, model=PlainTextModel())
        agent = Agent(config=config, harness=harness)

        stream = agent.chat_stream("你好", ChatContext(), LoopOptions(allow_final_text=True))
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        self.assertTrue(result.success)
        self.assertEqual(events[-1].type, "turn_complete")
        self.assertFalse(any(event.type == "tool_call_result" for event in events))
        self.assertIn("排查问题", result.final_messages[-1].content)


if __name__ == "__main__":
    unittest.main()
