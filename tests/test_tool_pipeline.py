import unittest

from minimal_cli_agent.harness import AgentHarness
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


if __name__ == "__main__":
    unittest.main()
