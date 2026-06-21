import unittest

from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig


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


if __name__ == "__main__":
    unittest.main()

