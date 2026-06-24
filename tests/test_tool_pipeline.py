import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from minimal_cli_agent.exceptions import PermissionDenied
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

    def test_permission_confirmation_is_recorded_in_session_events(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            harness = AgentHarness(AgentConfig(permission_mode="default"), session_store=store)

            with patch("builtins.input", side_effect=["y"]):
                harness.execute_shell("printf hello")

            events = store.load_events()

        self.assertEqual(events[0].kind, "permission_decision")
        self.assertEqual(events[0].data["decision"], "allow")
        self.assertEqual(events[0].data["action"], "shell")
        self.assertEqual(events[0].data["permission_mode"], "default")


if __name__ == "__main__":
    unittest.main()
