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

    def test_read_tail_reads_bounded_last_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_text("\n".join(f"line-{index}" for index in range(200)), encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.READ_TAIL, payload=json.dumps({"path": "large.txt", "lines": 3}))
            )

        self.assertEqual(observation.result.output, "line-197\nline-198\nline-199")

    def test_read_forward_reads_bounded_range(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.txt"
            path.write_text("abcdef", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "page.txt", "offset": 2, "limit": 3}))
            )

        self.assertEqual(observation.result.output, "cde")

    def test_search_returns_top_k_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("needle one\nmiss\nneedle two\n", encoding="utf-8")
            (root / "b.txt").write_text("needle three\nneedle four\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "needle", "path": ".", "top_k": 2}))
            )

        self.assertIn("a.txt:1: needle one", observation.result.output)
        self.assertIn("a.txt:3: needle two", observation.result.output)
        self.assertNotIn("needle three", observation.result.output)

    def test_write_file_rejects_invalid_json_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.json", "content": '{"bad":'}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 2)
        self.assertIn("Structured file validation failed", observation.result.output)

    def test_write_file_accepts_valid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.json", "content": '{"ok": true}'}))
            )

            self.assertEqual(path.read_text(encoding="utf-8"), '{"ok": true}')

        self.assertEqual(observation.result.exit_code, 0)

    def test_write_file_rejects_invalid_toml_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "pyproject.toml", "content": "[project\n"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("Structured file validation failed", observation.result.output)

    def test_write_file_rejects_invalid_xml_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.xml"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.xml", "content": "<root>"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("Structured file validation failed", observation.result.output)


if __name__ == "__main__":
    unittest.main()
