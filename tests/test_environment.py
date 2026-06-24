import unittest

from minimal_cli_agent.environment import LocalEnvironment
from minimal_cli_agent.types import AgentConfig


class EnvironmentTest(unittest.TestCase):
    def test_execute_redacts_secret_output(self) -> None:
        environment = LocalEnvironment(AgentConfig(permission_mode="yolo"))

        result = environment.execute("printf 'OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\\n'")

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", result.output)
        self.assertIn("OPENAI_API_KEY=<redacted>", result.output)


if __name__ == "__main__":
    unittest.main()
