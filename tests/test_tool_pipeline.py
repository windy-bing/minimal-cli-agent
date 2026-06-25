import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from minimal_cli_agent.constants import EventKinds, PermissionEventFields, PermissionModes, Tools
from minimal_cli_agent.exceptions import ConfigurationError, PermissionDenied
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.types import AgentConfig, ToolCall


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
