import unittest

from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig


class HarnessTest(unittest.TestCase):
    def test_plan_shell_uses_tool_pipeline_boundary(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config)

        observation = harness.execute_shell("echo hello")

        self.assertEqual(observation.action, "shell")
        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.to_message().content)


if __name__ == "__main__":
    unittest.main()
