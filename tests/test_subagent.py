import unittest

from minimal_cli_agent.subagent import SubAgentRunner
from minimal_cli_agent.types import AgentConfig, Message


class CapturingModel:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def complete(self, messages: list[Message]) -> str:
        self.messages = list(messages)
        return "Summary: inspected docs\nEvidence:\n- README.md"


class SubAgentTest(unittest.TestCase):
    def test_subagent_runner_uses_isolated_plan_context(self) -> None:
        model = CapturingModel()
        config = AgentConfig(permission_mode="autoEdit")

        result = SubAgentRunner(config, model).run("inspect docs")

        self.assertTrue(result.success)
        self.assertIn("Summary: inspected docs", result.summary)
        self.assertEqual(config.permission_mode, "autoEdit")
        self.assertIn("scoped sub-agent", model.messages[0].content)
        self.assertEqual(model.messages[-1].content, "inspect docs")


if __name__ == "__main__":
    unittest.main()
