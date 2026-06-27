import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.cli import detect_explicit_options, format_duration, main, render_prompt, run_interactive, run_turn
from minimal_cli_agent.constants import EventKinds, PermissionEventFields
from minimal_cli_agent.exceptions import ModelRequestError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.plan import PLAN_METADATA_KEY, PlanArtifact
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message
from minimal_cli_agent.types import EventRecord
from minimal_cli_agent.workflow import WORKFLOW_METADATA_KEY


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


class InterruptThenCountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls == 1:
            raise KeyboardInterrupt
        return "recovered\n```bash-action\nexit\n```"


class WriteBlockedThenRetryModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls in {1, 3}:
            return '```tool-action\n{"tool":"write_file","path":"result.txt","content":"done"}\n```'
        if self.calls == 2:
            return "Plan mode blocked the edit."
        return "Done.\n```bash-action\nexit\n```"


class CapturingModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.messages: list[Message] = []

    def complete(self, messages: list[Message]) -> str:
        self.messages = list(messages)
        return self.output


class MultiStepCaptureModel:
    def __init__(self) -> None:
        self.calls = 0
        self.messages_by_call: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        self.messages_by_call.append(list(messages))
        if self.calls == 1:
            return "```bash-action\nprintf first\n```"
        return "done\n```bash-action\nexit\n```"


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

    def test_run_interactive_ctrl_c_at_prompt_clears_input_without_exit(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

        with patch("builtins.input", side_effect=[KeyboardInterrupt, "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 0)
        self.assertIn("Input cleared", printed)

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
        self.assertIn("Commands: /help, /config, /profile, /permission, /policy, /mcp, /plugin, /plugins, /skill, /skills, /context, /history, /events, /memory, /plan, /workflow, /delegate, /review, /exit", printed)

    def test_detect_explicit_options_supports_space_and_equals_forms(self) -> None:
        explicit = detect_explicit_options([
            "--profile",
            "ollama",
            "--model=model-a",
            "--base-url",
            "http://localhost:11434",
            "--no-session",
        ])

        self.assertIn("profile", explicit)
        self.assertIn("model", explicit)
        self.assertIn("base_url", explicit)
        self.assertIn("session", explicit)

    def test_main_reads_project_config_and_defaults_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".minimal-agent.json").write_text(
                '{"provider":"openai-compatible","model":"configured-model","base_url":"http://configured","permission":"plan"}',
                encoding="utf-8",
            )

            with patch("builtins.print") as print_mock:
                exit_code = main(["--cwd", str(root), "--show-config"])

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("provider: openai-compatible", printed)
        self.assertIn("model: configured-model", printed)
        self.assertIn(f"session: {(root / '.agent' / 'session.json').resolve()}", printed)

    def test_main_can_disable_default_session(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch("builtins.print") as print_mock:
                exit_code = main(["--cwd", tmp, "--no-session", "--show-config"])

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("session: <none>", printed)

    def test_main_can_use_sqlite_session_store(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "session.sqlite"
            with patch("builtins.print") as print_mock:
                exit_code = main(["--cwd", tmp, "--session-db", str(db), "--show-config"])

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn(f"session_db: {db.resolve()}", printed)

    def test_run_interactive_can_save_project_config(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AgentConfig(cwd=root, permission_mode="plan", model="saved-model", summarize_context=True)
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.input", side_effect=["/config save", "/quit"]), patch("builtins.print"):
                exit_code = run_interactive(agent, ChatContext(), session_store=JsonSessionStore(root / ".agent" / "session.json"))

            saved = (root / ".minimal-agent.json").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn('"model": "saved-model"', saved)
        self.assertIn('"session": ".agent/session.json"', saved)

    def test_model_timeout_can_be_configured_on_agent_config(self) -> None:
        config = AgentConfig(model_timeout=7)

        self.assertEqual(config.model_timeout, 7)

    def test_render_prompt_includes_model_and_permission(self) -> None:
        config = AgentConfig(provider="anthropic", model="claude-sonnet-4-5", permission_mode="plan")

        prompt = render_prompt(config)

        self.assertIn("minimal-agent", prompt)
        self.assertIn("model: anthropic/claude-sonnet-4-5", prompt)
        self.assertIn("permission: plan", prompt)

    def test_format_duration_uses_human_units(self) -> None:
        self.assertEqual(format_duration(0.25), "250ms")
        self.assertEqual(format_duration(1.5), "1.50s")
        self.assertEqual(format_duration(65), "1m5.0s")

    def test_run_turn_includes_supplemental_input_before_next_model_call(self) -> None:
        model = MultiStepCaptureModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()
        inputs = ["please also inspect tests"]

        def read_input() -> str | None:
            return inputs.pop(0) if inputs else None

        with patch("builtins.print"):
            exit_code = run_turn(
                agent,
                "first task",
                context,
                options=LoopOptions(allow_final_text=True, interrupt_input_reader=read_input),
            )

        second_call_messages = "\n".join(message.content for message in model.messages_by_call[1])
        self.assertEqual(exit_code, 0)
        self.assertIn("User supplemental input during this task", second_call_messages)
        self.assertIn("please also inspect tests", second_call_messages)

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

    def test_run_interactive_compacts_tool_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("alpha\nbeta\nsecret-file-content\n", encoding="utf-8")
            model = SequenceModel([
                '```tool-action\n{"tool":"read_file","path":"notes.txt"}\n```',
                "Read complete.",
            ])
            config = AgentConfig(cwd=root, permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.input", side_effect=["/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext(), first_message="read notes")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("[action] read_file: notes.txt", printed)
        self.assertIn("read_file notes.txt", printed)
        self.assertIn("3 lines", printed)
        self.assertNotIn("secret-file-content", printed)

    def test_run_interactive_can_retry_plan_block_in_auto_edit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = WriteBlockedThenRetryModel()
            config = AgentConfig(cwd=root, permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.input", side_effect=["y", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext(), first_message="write result")

            output = (root / "result.txt").read_text(encoding="utf-8")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "done")
        self.assertEqual(model.calls, 4)
        self.assertIn("permission: autoEdit", printed)

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

    def test_run_interactive_continues_after_turn_interrupt(self) -> None:
        model = InterruptThenCountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["retry", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context, first_message="first")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertIn("Turn interrupted", printed)
        self.assertNotIn("Turn failed", printed)
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
        self.assertIn("context_estimated_tokens:", printed)
        self.assertIn("context cleared", printed)

    def test_run_interactive_history_lists_and_replays_prompts(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext()

        with patch("builtins.input", side_effect=["/history", "/history 1", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context, first_message="first task")

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 2)
        self.assertIn("1: first task", printed)
        self.assertIn("replay history[1]: first task", printed)

    def test_run_interactive_history_seeds_from_session_user_prompts_only(self) -> None:
        model = CountingModel()
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext(
            messages=[
                Message(role="user", content="real question"),
                Message(role="user", content="Command finished with exit code 0:\nstatus: success"),
            ]
        )

        with patch("builtins.input", side_effect=["/history", "/quit"]), patch("builtins.print") as print_mock:
            exit_code = run_interactive(agent, context)

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(model.calls, 0)
        self.assertIn("1: real question", printed)
        self.assertNotIn("Command finished", printed)

    def test_run_interactive_events_lists_session_events(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            store.append_event(EventRecord(kind=EventKinds.PERMISSION_DECISION, data={PermissionEventFields.DECISION: "allow"}))
            config = AgentConfig(permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))

            with patch("builtins.input", side_effect=["/events", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext(), session_store=store)

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn(EventKinds.PERMISSION_DECISION, printed)
        self.assertIn("allow", printed)

    def test_run_interactive_events_filters_pages_and_outputs_json(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            store.append_event(EventRecord(kind="tool_execution", data={"value": 1}))
            store.append_event(EventRecord(kind="tool_execution", data={"value": 2}))
            store.append_event(EventRecord(kind="permission_decision", data={"value": 3}))
            config = AgentConfig(permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))

            with patch("builtins.input", side_effect=["/events kind=tool_execution limit=1 offset=1 format=json", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext(), session_store=store)

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn('"kind": "tool_execution"', printed)
        self.assertIn('"value": 1', printed)
        self.assertNotIn('"value": 2', printed)
        self.assertNotIn('"value": 3', printed)

    def test_run_interactive_policy_reports_runtime_rules(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_file = root / "policy.json"
            policy_file.write_text(
                json.dumps({"allow_command_prefixes": ["git status"], "write_allow_paths": ["src/**"]}),
                encoding="utf-8",
            )
            config = AgentConfig(cwd=root, permission_mode="default", policy_file=policy_file)
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
            agent.harness.policy.approved_actions.add("shell")

            with patch("builtins.input", side_effect=["/policy", "/policy json", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("permission_mode: default", printed)
        self.assertIn("allow_command_prefixes: git status", printed)
        self.assertIn("write_allow_paths: src/**", printed)
        self.assertIn('"approved_actions": [\n    "shell"\n  ]', printed)

    def test_run_interactive_memory_search_uses_sqlite_store(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            from minimal_cli_agent.memory import SQLiteSessionStore

            store = SQLiteSessionStore(Path(tmp) / "session.sqlite")
            store.save([Message(role="user", content="remember alpha topic")])
            config = AgentConfig(permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))

            with patch("builtins.input", side_effect=["/memory alpha", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext(), session_store=store)

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("remember alpha topic", printed)

    def test_run_interactive_skills_discovers_and_loads_workspace_skill(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo Skill", encoding="utf-8")
            config = AgentConfig(cwd=root, permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.input", side_effect=["/skills", "/skills load demo", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("demo", printed)
        self.assertEqual(agent.config.skill_paths[0].parent.name, "demo")

    def test_run_interactive_plugins_discovers_and_loads_workspace_plugin(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            skill = plugin / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo Plugin Skill", encoding="utf-8")
            (plugin / "plugin.json").write_text(
                '{"name":"demo","skills":["demo"],"mcpServers":{"coffee":{"url":"https://example.test/mcp"}}}',
                encoding="utf-8",
            )
            config = AgentConfig(cwd=root, permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.input", side_effect=["/plugins", "/plugins load demo", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, ChatContext())

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIn("demo", printed)
        self.assertEqual(agent.config.plugin_paths[0].parent.name, "demo")
        self.assertEqual(agent.config.skill_paths[0].parent.name, "demo")

    def test_run_interactive_workflow_manages_typed_state(self) -> None:
        model = CountingModel()
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            config = AgentConfig(permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))
            context = ChatContext()

            with patch(
                "builtins.input",
                side_effect=[
                    "/workflow step inspect docs",
                    "/workflow done 1",
                    "/workflow show",
                    "/quit",
                ],
            ), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, context, session_store=store, first_message="/workflow create ship feature")

            persisted = store.load_workflow()

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(persisted)
        self.assertEqual(context.metadata[WORKFLOW_METADATA_KEY].goal, "ship feature")
        self.assertEqual(context.metadata[WORKFLOW_METADATA_KEY].steps[0].status, "done")
        self.assertIn("workflow saved", printed)
        self.assertIn("1. [x] inspect docs", printed)

    def test_run_interactive_workflow_schedule_wait_verify_and_merge(self) -> None:
        model = SequenceModel(["Summary: inspected\nEvidence:\n- README.md"])
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            config = AgentConfig(permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))
            context = ChatContext()

            with patch(
                "builtins.input",
                side_effect=[
                    "/workflow step implement parser",
                    "/workflow schedule",
                    "/workflow wait",
                    "/workflow done 1",
                    "/workflow verify 1",
                    "/delegate inspect docs",
                    "/workflow merge",
                    "/quit",
                ],
            ), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, context, session_store=store, first_message="/workflow create ship workflow")

            workflow = store.load_workflow()

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.steps[0].status, "verified")
        self.assertTrue(any(step.title == "Merge delegation: inspect docs" for step in workflow.steps))
        self.assertIn("workflow step scheduled: 1", printed)
        self.assertIn("workflow has running steps", printed)
        self.assertIn("workflow verified", printed)
        self.assertIn("workflow delegations merged", printed)

    def test_run_interactive_delegate_records_subagent_result_in_workflow(self) -> None:
        model = SequenceModel(["Summary: inspected README\nEvidence:\n- README.md"])
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            config = AgentConfig(permission_mode="autoEdit")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=store))
            context = ChatContext()

            with patch("builtins.input", side_effect=["/workflow show", "/quit"]), patch("builtins.print") as print_mock:
                exit_code = run_interactive(agent, context, session_store=store, first_message="/delegate inspect README")

            persisted = store.load_workflow()

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.delegations[0].task, "inspect README")
        self.assertIn("delegation success", printed)
        self.assertIn("Summary: inspected README", printed)
        self.assertIn("delegations:", printed)

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

    def test_run_turn_injects_active_plan_into_system_prompt(self) -> None:
        model = CapturingModel("Done.\n```bash-action\nexit\n```")
        config = AgentConfig(permission_mode="plan")
        agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
        context = ChatContext(
            metadata={
                PLAN_METADATA_KEY: PlanArtifact(
                    goal="update docs",
                    summary="Update README only.",
                    steps=["Edit README.md"],
                    evidence=["README.md"],
                )
            }
        )

        with patch("builtins.print"):
            exit_code = run_turn(agent, "execute plan", context)

        self.assertEqual(exit_code, 0)
        self.assertIn("Active execution plan:", model.messages[0].content)
        self.assertIn("README.md", model.messages[0].content)

    def test_run_turn_injects_project_rules_into_system_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Always run focused tests.", encoding="utf-8")
            model = CapturingModel("Done.\n```bash-action\nexit\n```")
            config = AgentConfig(cwd=root, permission_mode="plan")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))

            with patch("builtins.print"):
                exit_code = run_turn(agent, "follow project rules", ChatContext())

        self.assertEqual(exit_code, 0)
        self.assertIn("Project rules:", model.messages[0].content)
        self.assertIn("Always run focused tests.", model.messages[0].content)

    def test_active_plan_restricts_writer_paths_when_paths_are_known(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = SequenceModel([
                '```tool-action\n{"tool":"write_file","path":"other.txt","content":"bad"}\n```',
                "Done.\n```bash-action\nexit\n```",
            ])
            config = AgentConfig(cwd=root, permission_mode="autoEdit")
            agent = Agent(config=config, harness=AgentHarness(config=config, model=model))
            context = ChatContext(
                metadata={
                    PLAN_METADATA_KEY: PlanArtifact(
                        goal="write planned file",
                        summary="Only planned.txt should be edited.",
                        steps=["Update planned.txt"],
                        evidence=["planned.txt"],
                    )
                }
            )

            with patch("builtins.print"):
                exit_code = run_turn(agent, "execute plan", context)

            blocked_observations = [message.content for message in context.messages if "Active plan restricts" in message.content]

        self.assertEqual(exit_code, 0)
        self.assertFalse((root / "other.txt").exists())
        self.assertTrue(blocked_observations)


if __name__ == "__main__":
    unittest.main()
