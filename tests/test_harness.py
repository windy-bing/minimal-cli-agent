import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.types import AgentConfig, ToolCall


class HarnessTest(unittest.TestCase):
    def test_plan_shell_uses_tool_pipeline_boundary(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config)

        observation = harness.execute_shell("echo hello")

        self.assertEqual(observation.action, "shell")
        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.to_message().content)

    def test_auto_edit_can_write_and_read_workspace_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config = AgentConfig(cwd=Path(tmp), permission_mode="autoEdit")
            harness = AgentHarness(config)

            write = harness.execute_tool(
                ToolCall(
                    name=Tools.WRITE_FILE,
                    payload=json.dumps({"path": "notes/todo.txt", "content": "hello"}),
                )
            )
            read = harness.execute_tool(ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "notes/todo.txt"})))

        self.assertEqual(write.result.exit_code, 0)
        self.assertIn("Wrote notes/todo.txt", write.result.output)
        self.assertEqual(read.result.output, "hello")

    def test_plan_mode_skips_workspace_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "notes.txt", "content": "hello"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.result.output)


if __name__ == "__main__":
    unittest.main()
