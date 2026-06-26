import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.constants import LoopEventTypes
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message


class FakeModel:
    def complete(self, messages: list[Message]) -> str:
        return "Done.\n```bash-action\nexit\n```"


class PlainTextModel:
    def complete(self, messages: list[Message]) -> str:
        return "你好，我可以帮你看代码、改文件或排查问题。"


class WriteThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            return 'Writing file.\n```tool-action\n{"tool":"write_file","path":"agent.txt","content":"done"}\n```'
        return "Done.\n```bash-action\nexit\n```"


class MultiActionThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                "Read then write.\n"
                '```tool-action\n{"tool":"read_file","path":"input.txt"}\n```\n'
                '```tool-action\n{"tool":"write_file","path":"output.txt","content":"done"}\n```'
            )
        return "Done.\n```bash-action\nexit\n```"


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
        self.assertEqual(events[0].type, LoopEventTypes.STEP_START)
        self.assertEqual(events[-1].type, LoopEventTypes.DONE)

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

        observations = [event.data.get("observation", "") for event in events if event.type == LoopEventTypes.TOOL_CALL_RESULT]
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
        self.assertEqual(events[-1].type, LoopEventTypes.TURN_COMPLETE)
        self.assertFalse(any(event.type == LoopEventTypes.TOOL_CALL_RESULT for event in events))
        self.assertIn("排查问题", result.final_messages[-1].content)

    def test_agent_loop_can_modify_workspace_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config = AgentConfig(cwd=Path(tmp), permission_mode="autoEdit")
            harness = AgentHarness(config=config, model=WriteThenExitModel())
            agent = Agent(config=config, harness=harness)

            result = agent.chat("write a file", ChatContext())

            self.assertEqual((Path(tmp) / "agent.txt").read_text(encoding="utf-8"), "done")

        self.assertTrue(result.success)

    def test_agent_loop_executes_multiple_actions_in_one_model_turn(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("hello", encoding="utf-8")
            config = AgentConfig(cwd=root, permission_mode="autoEdit")
            harness = AgentHarness(config=config, model=MultiActionThenExitModel())
            agent = Agent(config=config, harness=harness)

            result = agent.chat("read and write", ChatContext())

            self.assertEqual((root / "output.txt").read_text(encoding="utf-8"), "done")

        observations = [message.content for message in result.final_messages if message.role == "user"]
        self.assertTrue(result.success)
        self.assertTrue(any("read_file" in observation and "write_file" in observation for observation in observations))


if __name__ == "__main__":
    unittest.main()
