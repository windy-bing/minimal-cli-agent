import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.constants import LoopEventTypes
from minimal_cli_agent.exceptions import ModelRequestError
from minimal_cli_agent.harness import AgentHarness, Observation
from minimal_cli_agent.model_gateway import ModelGateway
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message, ModelRoute, ToolCall
import minimal_cli_agent


class FakeModel:
    def complete(self, messages: list[Message]) -> str:
        return "Done.\n```bash-action\nexit\n```"


class PlainTextModel:
    def complete(self, messages: list[Message]) -> str:
        return "你好，我可以帮你看代码、改文件或排查问题。"


class StreamingModel:
    def complete(self, messages: list[Message]) -> str:
        return "Done.\n```bash-action\nexit\n```"

    def supports_streaming(self) -> bool:
        return True

    def stream_complete(self, messages: list[Message]) -> Iterator[str]:
        yield "Done."
        yield "\n```bash-action\nexit\n```"


class FailingModel:
    def complete(self, messages: list[Message]) -> str:
        raise ModelRequestError("timeout")


class RouteModel:
    def __init__(self, model: str, calls: list[str]) -> None:
        self.model = model
        self.calls = calls

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(self.model)
        if self.model == "primary":
            raise ModelRequestError("primary unavailable")
        return "fallback done\n```bash-action\nexit\n```"


class WriteThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            return 'Writing file.\n```tool-action\n{"tool":"write_file","path":"agent.txt","content":"done"}\n```'
        return "Done.\n```bash-action\nexit\n```"


class LongRunningThenExitModel:
    def __init__(self, exit_after: int) -> None:
        self.calls = 0
        self.exit_after = exit_after

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls >= self.exit_after:
            return "Done.\n```bash-action\nexit\n```"
        return "Continue.\n```tool-action\n{\"tool\":\"search\",\"pattern\":\"missing\",\"path\":\".\"}\n```"


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


class DuplicateReadThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                "Read twice.\n"
                '```tool-action\n{"tool":"read_file","path":"input.txt"}\n```\n'
                '```tool-action\n{"tool":"read_file","path":"input.txt"}\n```'
            )
        return "Done.\n```bash-action\nexit\n```"


class RepeatReadForwardThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls in {1, 2}:
            return '```tool-action\n{"tool":"read_forward","path":"input.txt","offset":0,"limit":5}\n```'
        return "Done.\n```bash-action\nexit\n```"


class TwoReadsThenExitModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                "Read two files.\n"
                '```tool-action\n{"tool":"read_file","path":"one.txt"}\n```\n'
                '```tool-action\n{"tool":"read_file","path":"two.txt"}\n```'
            )
        return "Done.\n```bash-action\nexit\n```"


class RecordingBatchHarness(AgentHarness):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.batches: list[list[str]] = []

    def execute_tools(self, calls: list[ToolCall]) -> list[Observation]:
        self.batches.append([call.name for call in calls])
        return super().execute_tools(calls)


class AgentTest(unittest.TestCase):
    def test_package_exports_public_api(self) -> None:
        self.assertIs(minimal_cli_agent.Agent, Agent)
        self.assertIs(minimal_cli_agent.AgentConfig, AgentConfig)

    def test_from_config_builds_default_harness_without_hiding_constructor_dependency(self) -> None:
        config = AgentConfig(permission_mode="plan")

        agent = Agent.from_config(config)

        self.assertIs(agent.config, config)
        self.assertIsInstance(agent.harness, AgentHarness)

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
        self.assertEqual(events[1].type, LoopEventTypes.MODEL_WAIT)
        self.assertEqual(events[-1].type, LoopEventTypes.DONE)

    def test_chat_stream_reports_actual_fallback_model_route(self) -> None:
        calls: list[str] = []
        config = AgentConfig(
            permission_mode="plan",
            model="primary",
            model_fallbacks=(ModelRoute(provider="ollama", model="fallback", base_url="http://fallback"),),
        )
        gateway = ModelGateway(config, model_factory=lambda route_config: RouteModel(route_config.model, calls))
        agent = Agent(config=config, harness=AgentHarness(config=config, model=gateway))

        events = list(agent.chat_stream("hello", ChatContext()))
        route_event = next(event for event in events if event.type == LoopEventTypes.MODEL_ROUTE)

        self.assertEqual(calls, ["primary", "fallback"])
        self.assertEqual(route_event.data["model"], "fallback")
        self.assertEqual(route_event.data["fallback_index"], 1)

    def test_chat_stream_emits_model_output_chunks_when_streaming(self) -> None:
        config = AgentConfig(permission_mode="plan", model_streaming=True)
        agent = Agent(config=config, harness=AgentHarness(config=config, model=StreamingModel()))

        stream = agent.chat_stream("finish", ChatContext())
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        chunks = [event.data["content"] for event in events if event.type == LoopEventTypes.MODEL_OUTPUT_CHUNK]
        self.assertTrue(result.success)
        self.assertEqual("".join(chunks).strip(), "Done.\n```bash-action\nexit\n```")
        self.assertFalse(any(event.type == LoopEventTypes.MODEL_OUTPUT for event in events))

    def test_chat_stream_segments_complete_output_by_default(self) -> None:
        config = AgentConfig(permission_mode="plan", model_output_segment_chars=5)
        agent = Agent(config=config, harness=AgentHarness(config=config, model=FakeModel()))

        stream = agent.chat_stream("finish", ChatContext())
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        chunks = [event.data["content"] for event in events if event.type == LoopEventTypes.MODEL_OUTPUT_CHUNK]
        self.assertTrue(result.success)
        self.assertFalse(config.model_streaming)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).strip(), "Done.\n```bash-action\nexit\n```")

    def test_chat_stream_deduplicates_identical_read_only_actions_before_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("alpha", encoding="utf-8")
            config = AgentConfig(cwd=root, permission_mode="plan")
            harness = RecordingBatchHarness(config=config, model=DuplicateReadThenExitModel())
            agent = Agent(config=config, harness=harness)

            stream = agent.chat_stream("read", ChatContext())
            events = []
            while True:
                try:
                    events.append(next(stream))
                except StopIteration:
                    break

        starts = [event for event in events if event.type == LoopEventTypes.TOOL_CALL_START]
        self.assertEqual(len(starts), 1)
        self.assertEqual(harness.batches[0], ["read_file"])

    def test_chat_stream_skips_repeated_read_forward_range_across_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("alpha beta", encoding="utf-8")
            config = AgentConfig(cwd=root, permission_mode="plan")
            harness = RecordingBatchHarness(config=config, model=RepeatReadForwardThenExitModel())
            agent = Agent(config=config, harness=harness)

            events = list(agent.chat_stream("read", ChatContext()))

        starts = [event for event in events if event.type == LoopEventTypes.TOOL_CALL_START]
        observations = [event.data.get("observation", "") for event in events if event.type == LoopEventTypes.TOOL_CALL_RESULT]
        self.assertEqual(len(starts), 1)
        self.assertEqual(harness.batches, [["read_forward"]])
        self.assertTrue(any("Repeated read_forward range skipped" in observation for observation in observations))

    def test_chat_stream_enforces_read_only_tool_budget_before_execution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.txt").write_text("one", encoding="utf-8")
            (root / "two.txt").write_text("two", encoding="utf-8")
            config = AgentConfig(cwd=root, permission_mode="plan", max_read_only_tool_calls_per_turn=1)
            harness = RecordingBatchHarness(config=config, model=TwoReadsThenExitModel())
            agent = Agent(config=config, harness=harness)

            events = list(agent.chat_stream("read", ChatContext()))

        observations = [event.data.get("observation", "") for event in events if event.type == LoopEventTypes.TOOL_CALL_RESULT]
        self.assertEqual(harness.batches, [["read_file"]])
        self.assertTrue(any("Read-only tool call budget reached" in observation for observation in observations))

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

    def test_chat_stream_allows_unlimited_steps_when_max_steps_is_zero(self) -> None:
        model = LongRunningThenExitModel(exit_after=25)
        config = AgentConfig(permission_mode="plan", max_steps=0, summarize_context=False)
        harness = AgentHarness(config=config, model=model)
        agent = Agent(config=config, harness=harness)

        result = agent.chat("long task", ChatContext())

        self.assertTrue(result.success)
        self.assertEqual(model.calls, 25)

    def test_chat_stream_still_enforces_positive_max_steps(self) -> None:
        model = LongRunningThenExitModel(exit_after=25)
        config = AgentConfig(permission_mode="plan", max_steps=3)
        harness = AgentHarness(config=config, model=model)
        agent = Agent(config=config, harness=harness)

        result = agent.chat("bounded task", ChatContext())

        self.assertFalse(result.success)
        self.assertEqual(model.calls, 3)

    def test_chat_stream_treats_negative_max_steps_as_one_step(self) -> None:
        model = LongRunningThenExitModel(exit_after=25)
        config = AgentConfig(permission_mode="plan", max_steps=-1)
        harness = AgentHarness(config=config, model=model)
        agent = Agent(config=config, harness=harness)

        result = agent.chat("long task", ChatContext())

        self.assertFalse(result.success)
        self.assertEqual(model.calls, 1)

    def test_chat_stream_returns_model_errors_as_observations(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config=config, model=FailingModel())
        agent = Agent(config=config, harness=harness)

        stream = agent.chat_stream("hello", ChatContext())
        events = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as exc:
                result = exc.value
                break

        self.assertFalse(result.success)
        self.assertTrue(any("Model request failed: timeout" in str(event.data) for event in events))

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
            harness = RecordingBatchHarness(config=config, model=MultiActionThenExitModel())
            agent = Agent(config=config, harness=harness)

            result = agent.chat("read and write", ChatContext())

            self.assertEqual((root / "output.txt").read_text(encoding="utf-8"), "done")

        observations = [message.content for message in result.final_messages if message.role == "user"]
        self.assertTrue(result.success)
        self.assertEqual(harness.batches[0], ["read_file", "write_file"])
        self.assertTrue(any("read_file" in observation and "write_file" in observation for observation in observations))


if __name__ == "__main__":
    unittest.main()
