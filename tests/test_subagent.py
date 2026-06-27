import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.subagent import GroupSessionRunner, GroupSessionTask, SubAgentRunner
from minimal_cli_agent.types import AgentConfig, EventRecord, Message


class CapturingModel:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def complete(self, messages: list[Message]) -> str:
        self.messages = list(messages)
        return "Summary: inspected docs\nEvidence:\n- README.md"


class WorkerModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls % 2 == 1:
            return f'```tool-action\n{{"tool":"write_file","path":"notes.txt","content":"done-{self.calls}"}}\n```'
        return "Summary: wrote notes\nChanged files:\n- notes.txt\nVerification:\n- pending"


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

    def test_worker_subagent_can_edit_and_reports_changed_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = WorkerModel()
            config = AgentConfig(cwd=root, permission_mode="plan")

            result = SubAgentRunner(config, model).run("write notes", role="worker")

            self.assertEqual((root / "notes.txt").read_text(encoding="utf-8"), "done-1")

        self.assertTrue(result.success)
        self.assertEqual(result.role, "worker")
        self.assertEqual(result.changed_files, ("notes.txt",))

    def test_group_session_records_events_and_merge_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = WorkerModel()
            events: list[EventRecord] = []
            config = AgentConfig(cwd=root, permission_mode="plan")
            runner = GroupSessionRunner(config, model, event_recorder=events.append)

            result = runner.run(
                [
                    GroupSessionTask(role="worker", task="write notes once"),
                    GroupSessionTask(role="worker", task="write notes twice"),
                ]
            )

        self.assertFalse(result.success)
        self.assertEqual(result.merge_report.changed_files, ("notes.txt",))
        self.assertEqual(result.merge_report.conflicts, ("notes.txt",))
        self.assertTrue(events)
        self.assertEqual(events[-1].kind, "group_session")


if __name__ == "__main__":
    unittest.main()
