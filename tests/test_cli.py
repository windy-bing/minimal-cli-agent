import unittest
from unittest.mock import patch

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.cli import run_interactive, run_turn
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig, ChatContext, Message


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        return f"turn {self.calls} done\n```bash-action\nexit\n```"


class SequenceModel:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


class CliTest(unittest.TestCase):
    def test_run_turn_updates_context_messages(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.print"):
            exit_code = run_turn(agent, "first task", context)

        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 1)
        self.assertEqual(context.messages[-1].role, "assistant")
        self.assertIn("turn 1 done", context.messages[-1].content)

    def test_run_interactive_reuses_context_across_turns(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["second task", "/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, context, first_message="first task")

        user_messages = [message.content for message in context.messages if message.role == "user"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertIn("first task", user_messages)
        self.assertIn("second task", user_messages)

    def test_run_interactive_supports_plain_exit(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=["exit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, ChatContext())

        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 0)

    def test_run_interactive_shows_help_without_model_call(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=["/help", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 0)
        self.assertIn("Interactive commands:", printed)
        self.assertIn("/exit", printed)

    def test_run_interactive_slash_shows_quick_hint(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=["/", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 0)
        self.assertIn("Commands: /help, /exit, /quit", printed)

    def test_run_interactive_accepts_plain_text_reply(self) -> None:
        model = SequenceModel(["你好，我可以帮你看代码、改文件或排查问题。"])
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["你刚刚说你会什么来着?", "/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, context, first_message="你好")

        assistant_messages = [message.content for message in context.messages if message.role == "assistant"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(assistant_messages), 2)
        self.assertIn("排查问题", assistant_messages[-1])

    def test_run_interactive_allows_plain_summary_after_tool_call(self) -> None:
        model = SequenceModel([
            "```bash-action\nls -la\n```",
            "当前目录我已经看过了，可以继续问我具体文件。",
        ])
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, context, first_message="分析下当前项目")

        assistant_messages = [message.content for message in context.messages if message.role == "assistant"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertIn("当前目录", assistant_messages[-1])


if __name__ == "__main__":
    unittest.main()
