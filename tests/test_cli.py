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


if __name__ == "__main__":
    unittest.main()
