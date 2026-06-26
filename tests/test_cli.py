import unittest
from unittest.mock import patch

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.cli import detect_explicit_options, run_interactive, run_turn
from minimal_cli_agent.exceptions import ModelRequestError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.plan import PLAN_METADATA_KEY
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


class FailingThenCountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            raise ModelRequestError("temporary model failure")
        return "recovered\n```bash-action\nexit\n```"


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
        self.assertIn("Commands: /help, /config, /profile, /permission, /context, /plan, /review, /exit", printed)

    def test_detect_explicit_options_supports_space_and_equals_forms(self) -> None:
        explicit = detect_explicit_options([
            "--profile",
            "ollama",
            "--model=model-a",
            "--base-url",
            "http://localhost:11434",
        ])

        self.assertIn("profile", explicit)
        self.assertIn("model", explicit)
        self.assertIn("base_url", explicit)

    def test_model_timeout_can_be_configured_on_agent_config(self) -> None:
        config = AgentConfig(model_timeout=7)

        self.assertEqual(config.model_timeout, 7)

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

    def test_run_interactive_continues_after_model_error(self) -> None:
        model = FailingThenCountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["retry", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context, first_message="first")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertIn("temporary model failure", printed)
        self.assertIn("Turn failed", printed)
        self.assertIn("recovered", context.messages[-1].content)

    def test_run_interactive_can_switch_permission_with_slash_command(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=["/permission autoEdit", "/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, ChatContext())

        self.assertEqual(exit_code, 0)
        self.assertEqual(agent.config.permission_mode, "autoEdit")
        self.assertEqual(model.calls, 0)

    def test_run_interactive_can_switch_model_with_slash_command(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan", model="old-model")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=["/model new-model", "/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, ChatContext())

        self.assertEqual(exit_code, 0)
        self.assertEqual(agent.config.model, "new-model")

    def test_run_interactive_context_status_and_clear(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext(messages=[Message(role="user", content="hello")])

        with patch("builtins.input", side_effect=["/context status", "/context clear", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context)

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(context.messages, [])
        self.assertIn("context_messages: 1", printed)
        self.assertIn("context cleared", printed)

    def test_run_interactive_review_command_runs_agent_turn(self) -> None:
        model = SequenceModel(["review done"])
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["/quit"]), patch("builtins.print"):
            exit_code = run_interactive(agent, context, first_message="/review src")

        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 1)
        self.assertTrue(any(message.role == "user" and "Review src" in message.content for message in context.messages))

    def test_run_interactive_plan_command_uses_isolated_context(self) -> None:
        model = SequenceModel([
            "Summary: Improve test coverage.\nSteps:\n- Inspect tests\n- Add focused cases\nEvidence:\n- docs/architecture.md"
        ])
        config = AgentConfig(permission_mode="autoEdit")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext(messages=[Message(role="user", content="existing chat")])

        with patch("builtins.input", side_effect=["/plan show", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context, first_message="/plan improve tests")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        plan = context.metadata[PLAN_METADATA_KEY]
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 1)
        self.assertEqual(agent.config.permission_mode, "autoEdit")
        self.assertEqual([message.content for message in context.messages], ["existing chat"])
        self.assertEqual(plan.goal, "improve tests")
        self.assertIn("Inspect tests", plan.steps)
        self.assertIn("plan saved", printed)
        self.assertIn("goal: improve tests", printed)

    def test_run_interactive_plan_clear_removes_active_plan(self) -> None:
        model = SequenceModel(["Summary: Keep it small.\nSteps:\n- One"])
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["/plan clear", "/plan show", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context, first_message="/plan small change")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertNotIn(PLAN_METADATA_KEY, context.metadata)
        self.assertIn("plan cleared", printed)
        self.assertIn("no active plan", printed)


if __name__ == "__main__":
    unittest.main()
