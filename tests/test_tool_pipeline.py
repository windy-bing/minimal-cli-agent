import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from minimal_cli_agent.constants import EventKinds, PermissionEventFields, PermissionModes, ToolDecisionKinds, Tools
from minimal_cli_agent.exceptions import ConfigurationError, PermissionDenied
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.tool_pipeline import DecisionHookSpec
from minimal_cli_agent.tool_registry import ToolRegistry, ToolSpec
from minimal_cli_agent.types import AgentConfig, CommandResult, ToolCall, ToolDecision


class ToolPipelineTest(unittest.TestCase):
    def test_plan_mode_skips_shell_execution(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        observation = harness.execute_shell("echo should-not-run")

        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.result.output)

    def test_yolo_mode_executes_shell(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        observation = harness.execute_shell("printf hello")

        self.assertFalse(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 0)
        self.assertEqual(observation.result.output, "hello")

    def test_decision_hook_can_allow_before_confirmation(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="default"))
        harness.tool_pipeline.hooks.decision_hooks.append(
            lambda call, decision: ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="trusted test hook")
        )

        observation = harness.execute_shell("printf hello")

        self.assertFalse(observation.result.skipped)
        self.assertEqual(observation.result.output, "hello")

    def test_decision_hook_can_deny_allowed_policy(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))
        harness.tool_pipeline.hooks.decision_hooks.append(
            lambda call, decision: ToolDecision(kind=ToolDecisionKinds.DENY, reason="hook denied")
        )

        with self.assertRaisesRegex(PermissionDenied, "hook denied"):
            harness.execute_shell("printf hello")

    def test_decision_hooks_run_by_priority_and_record_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            harness = AgentHarness(AgentConfig(permission_mode="yolo"), session_store=store)
            seen: list[str] = []

            def first(call: ToolCall, decision: ToolDecision) -> ToolDecision:
                seen.append("first")
                return ToolDecision(kind=ToolDecisionKinds.ASK, reason="review")

            def second(call: ToolCall, decision: ToolDecision) -> ToolDecision:
                seen.append("second")
                return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="trusted")

            harness.tool_pipeline.hooks.decision_hooks.extend(
                [
                    DecisionHookSpec(hook=second, name="second", priority=20),
                    DecisionHookSpec(hook=first, name="first", priority=10),
                ]
            )

            observation = harness.execute_shell("printf hello")
            events = store.load_events()

        self.assertEqual(seen, ["first", "second"])
        self.assertEqual(observation.result.output, "hello")
        self.assertEqual(events[0].kind, EventKinds.TOOL_DECISION_CONFLICT)
        self.assertEqual(events[0].data["hooks"][0]["name"], "first")

    def test_schema_defaults_are_applied_before_execution(self) -> None:
        registry = ToolRegistry()
        captured: list[str] = []
        registry.register(
            ToolSpec(
                name="demo",
                description="Demo tool.",
                expected_format='{"path":"x","limit":5}',
                parameters_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                },
                handler=lambda payload: captured.append(payload) or CommandResult("demo", 0, "ok"),
            )
        )
        harness = AgentHarness(AgentConfig(permission_mode="yolo"), tool_registry=registry)
        harness.tool_pipeline.hooks.decision_hooks.append(
            lambda call, decision: ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="test tool")
        )

        result = harness.tool_pipeline.execute(ToolCall(name="demo", payload=json.dumps({"path": "x"})))

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(captured[0]), {"path": "x", "limit": 5})

    def test_tool_descriptions_include_schema_documentation(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        descriptions = harness.tool_registry.descriptions()

        self.assertIn("read_tail", descriptions)
        self.assertIn("default=100", descriptions)
        self.assertIn("mode(string, default=\"bytes\", enum=\"bytes\"|\"lines\")", descriptions)

    def test_validation_error_returns_repair_observation(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        result = harness.tool_pipeline.execute(ToolCall(name="shell", payload=""))

        self.assertTrue(result.skipped)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Tool validation failed.", result.output)
        self.assertIn("expected:", result.output)
        self.assertIn("A non-empty shell command string", result.output)

    def test_schema_validation_reports_field_errors(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        result = harness.tool_pipeline.execute(
            ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "notes.txt", "offset": "zero"}))
        )

        self.assertTrue(result.skipped)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("field_errors:", result.output)
        self.assertIn("offset: expected integer", result.output)

    def test_schema_validation_reports_missing_required_field(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        result = harness.tool_pipeline.execute(ToolCall(name=Tools.SEARCH, payload=json.dumps({"path": "."})))

        self.assertTrue(result.skipped)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("pattern: missing required field", result.output)

    def test_schema_validation_reports_array_item_type(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        result = harness.tool_pipeline.execute(
            ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "x", "include_extensions": [".py", 3]}))
        )

        self.assertTrue(result.skipped)
        self.assertIn("include_extensions: expected array of strings", result.output)

    def test_schema_validation_reports_nested_object_errors(self) -> None:
        from minimal_cli_agent.tool_registry import validate_object_schema

        schema = {
            "type": "object",
            "required": ["config"],
            "properties": {
                "config": {
                    "type": "object",
                    "required": ["mode"],
                    "properties": {
                        "mode": {"type": "string", "enum": ["fast", "safe"]},
                        "retry": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                }
            },
        }

        errors = validate_object_schema({"config": {"mode": "other", "retry": -1, "extra": True}}, schema)

        self.assertIn('config.mode: expected one of "fast", "safe"', errors)
        self.assertIn("config.retry: must be >= 0", errors)
        self.assertIn("config.extra: unexpected field", errors)

    def test_schema_validation_reports_array_item_paths(self) -> None:
        from minimal_cli_agent.tool_registry import validate_object_schema

        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}}},
                }
            },
        }

        errors = validate_object_schema({"items": [{"name": "ok"}, {"name": 3}]}, schema)

        self.assertIn("items[1].name: expected string", errors)

    def test_schema_validation_supports_one_of(self) -> None:
        from minimal_cli_agent.tool_registry import validate_object_schema

        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "oneOf": [
                        {"type": "integer"},
                        {"type": "string", "minLength": 3},
                    ]
                }
            },
        }

        self.assertEqual(validate_object_schema({"value": "abc"}, schema), [])
        self.assertIn("value: expected exactly one oneOf schema to match", validate_object_schema({"value": "x"}, schema))

    def test_tool_alias_resolves_to_registered_tool(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        result = harness.tool_pipeline.execute(ToolCall(name="bash", payload="echo hello"))

        self.assertTrue(result.skipped)
        self.assertIn("plan mode", result.output)

    def test_unknown_tool_returns_discovery_observation(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        result = harness.tool_pipeline.execute(ToolCall(name="missing", payload="value"))

        self.assertTrue(result.skipped)
        self.assertEqual(result.exit_code, 127)
        self.assertIn("Tool discovery failed.", result.output)
        self.assertIn("available_tools:", result.output)
        self.assertIn("shell", result.output)

    def test_unknown_tool_returns_safe_suggestions(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        result = harness.tool_pipeline.execute(ToolCall(name="read_fil", payload=json.dumps({"path": "README.md"})))

        self.assertTrue(result.skipped)
        self.assertEqual(result.exit_code, 127)
        self.assertIn("suggested_tools:", result.output)
        self.assertIn("read_file", result.output)

    def test_default_mode_asks_before_write_file(self) -> None:
        with TemporaryDirectory() as tmp:
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="default"))

            with patch("builtins.input", side_effect=["y"]) as input_mock:
                observation = harness.execute_tool(
                    ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "notes.txt", "content": "hello"}))
                )

        self.assertEqual(input_mock.call_count, 1)
        self.assertEqual(observation.result.exit_code, 0)

    def test_default_mode_can_use_confirmation_callback(self) -> None:
        approvals: list[tuple[str, str]] = []

        def approve(action: str, payload: str) -> bool:
            approvals.append((action, payload))
            return True

        with TemporaryDirectory() as tmp:
            harness = AgentHarness(
                AgentConfig(cwd=Path(tmp), permission_mode="default"),
                confirmation_handler=approve,
            )
            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "notes.txt", "content": "hello"}))
            )

        self.assertEqual(observation.result.exit_code, 0)
        self.assertEqual(approvals[0][0], Tools.WRITE_FILE)

    def test_sensitive_file_paths_are_hard_denied_even_in_auto_edit(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="autoEdit"))

        with self.assertRaisesRegex(PermissionDenied, "sensitive path"):
            harness.execute_tool(ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": ".env", "content": "x"})))

    def test_plan_mode_allows_read_only_file_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("hello", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "notes.txt"})))

        self.assertFalse(observation.result.skipped)
        self.assertEqual(observation.result.output, "hello")

    def test_sensitive_paths_are_hard_denied_for_read_only_tools(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))

        with self.assertRaisesRegex(PermissionDenied, "sensitive path"):
            harness.execute_tool(ToolCall(name=Tools.READ_TAIL, payload=json.dumps({"path": ".env", "lines": 10})))

    def test_default_mode_remembers_approved_shell_command_in_session(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="default"))

        with patch("builtins.input", side_effect=["y"]) as input_mock:
            first = harness.execute_shell("printf hello")
            second = harness.execute_shell("printf hello")

        self.assertEqual(input_mock.call_count, 1)
        self.assertEqual(first.result.output, "hello")
        self.assertEqual(second.result.output, "hello")

    def test_default_mode_can_approve_all_shell_calls_for_session(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="default"))

        with patch("builtins.input", side_effect=["all"]) as input_mock:
            first = harness.execute_shell("printf hello")
            second = harness.execute_shell("printf goodbye")

        self.assertEqual(input_mock.call_count, 1)
        self.assertEqual(first.result.output, "hello")
        self.assertEqual(second.result.output, "goodbye")

    def test_confirmation_callback_can_approve_all_shell_calls_for_session(self) -> None:
        approvals: list[tuple[str, str]] = []

        def approve_all(action: str, payload: str) -> str:
            approvals.append((action, payload))
            return "allow_session_action"

        harness = AgentHarness(
            AgentConfig(permission_mode="default"),
            confirmation_handler=approve_all,
        )

        first = harness.execute_shell("printf hello")
        second = harness.execute_shell("printf goodbye")

        self.assertEqual(len(approvals), 1)
        self.assertEqual(first.result.output, "hello")
        self.assertEqual(second.result.output, "goodbye")

    def test_sensitive_paths_are_hard_denied_even_in_yolo(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        with self.assertRaisesRegex(PermissionDenied, "sensitive path"):
            harness.execute_shell("cat .env")

    def test_network_commands_are_denied_without_network_permission(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="yolo"))

        with self.assertRaisesRegex(PermissionDenied, "--allow-network"):
            harness.execute_shell("curl https://example.com")

    def test_network_commands_can_be_explicitly_allowed(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan", allow_network=True))

        observation = harness.execute_shell("curl https://example.com")

        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.result.output)

    def test_policy_file_adds_custom_deny_command_tokens(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(json.dumps({"deny_command_tokens": ["custom-danger"]}), encoding="utf-8")
            harness = AgentHarness(AgentConfig(permission_mode="yolo", policy_file=policy_file))

            with self.assertRaisesRegex(PermissionDenied, "dangerous command"):
                harness.execute_shell("echo custom-danger")

    def test_policy_file_can_allow_shell_prefix_without_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(json.dumps({"allow_command_prefixes": ["printf "]}), encoding="utf-8")
            harness = AgentHarness(AgentConfig(permission_mode="default", policy_file=policy_file))

            with patch("builtins.input") as input_mock:
                observation = harness.execute_shell("printf hello")

        input_mock.assert_not_called()
        self.assertEqual(observation.result.output, "hello")

    def test_policy_file_write_allow_paths_restricts_workspace_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(json.dumps({"write_allow_paths": ["allowed/**"]}), encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit", policy_file=policy_file))

            with self.assertRaisesRegex(PermissionDenied, "write_allow_paths"):
                harness.execute_tool(ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "blocked.txt", "content": "x"})))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "allowed/notes.txt", "content": "ok"}))
            )

        self.assertEqual(observation.result.exit_code, 0)

    def test_policy_file_write_deny_paths_blocks_specific_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(
                json.dumps({"write_allow_paths": ["notes/**"], "write_deny_paths": ["notes/private/**"]}),
                encoding="utf-8",
            )
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit", policy_file=policy_file))

            with self.assertRaisesRegex(PermissionDenied, "write_deny_paths"):
                harness.execute_tool(
                    ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "notes/private/secret.txt", "content": "x"}))
                )

    def test_policy_file_rejects_invalid_token_list(self) -> None:
        with TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(json.dumps({"deny_command_tokens": "custom-danger"}), encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "deny_command_tokens"):
                AgentHarness(AgentConfig(policy_file=policy_file))

    def test_permission_confirmation_is_recorded_in_session_events(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            harness = AgentHarness(AgentConfig(permission_mode="default"), session_store=store)

            with patch("builtins.input", side_effect=["y"]):
                harness.execute_shell("printf hello")

            events = store.load_events()

        self.assertEqual(events[0].kind, EventKinds.PERMISSION_DECISION)
        self.assertEqual(events[0].data[PermissionEventFields.DECISION], "allow")
        self.assertEqual(events[0].data[PermissionEventFields.ACTION], Tools.SHELL)
        self.assertEqual(events[0].data[PermissionEventFields.PERMISSION_MODE], PermissionModes.DEFAULT)


if __name__ == "__main__":
    unittest.main()
