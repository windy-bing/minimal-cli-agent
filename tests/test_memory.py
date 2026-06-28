import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.constants import EventKinds, PermissionEventFields
from minimal_cli_agent.memory import JsonSessionStore, SQLiteSessionStore, build_fts_query, compact_messages
from minimal_cli_agent.plan import PlanArtifact
from minimal_cli_agent.types import EventRecord, Message
from minimal_cli_agent.workflow import WorkflowArtifact, WorkflowDelegation, WorkflowStep


class MemoryTest(unittest.TestCase):
    def test_compact_messages_keeps_system_and_tail(self) -> None:
        messages = [Message("system", "s" * 10)]
        messages += [Message("user", str(i) * 20) for i in range(20)]

        compacted = compact_messages(messages, max_chars=80)

        self.assertEqual(compacted[0].role, "system")
        self.assertIn("compacted", compacted[1].content)
        self.assertEqual(compacted[-1].content, "19" * 20)

    def test_compact_messages_preserves_initial_user_goal(self) -> None:
        messages = [Message("system", "system")]
        messages.append(Message("user", "original task: implement history"))
        messages += [Message("assistant", str(i) * 20) for i in range(20)]

        compacted = compact_messages(messages, max_chars=80)

        self.assertIn("Initial user goal: original task: implement history", compacted[1].content)

    def test_json_session_store_reads_legacy_message_list(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text('[{"role": "user", "content": "hello"}]', encoding="utf-8")
            store = JsonSessionStore(path)

            messages = store.load()

        self.assertEqual(messages, [Message(role="user", content="hello")])

    def test_json_session_store_skips_malformed_message_records(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(
                '{"messages":[{"role":"user","content":"hello"},{"role":"bad","content":"x"},{"role":"assistant"}]}',
                encoding="utf-8",
            )
            store = JsonSessionStore(path)

            messages = store.load()

        self.assertEqual(messages, [Message(role="user", content="hello")])

    def test_json_session_store_skips_malformed_event_records(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(
                '{"events":[{"kind":"ok","timestamp":"2026-01-01T00:00:00+00:00","data":{}},{"kind":"bad"}]}',
                encoding="utf-8",
            )
            store = JsonSessionStore(path)

            events = store.load_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "ok")

    def test_json_session_store_persists_messages_and_events(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = JsonSessionStore(path)
            store.save([Message(role="user", content="hello")])
            store.append_event(EventRecord(kind=EventKinds.PERMISSION_DECISION, data={PermissionEventFields.DECISION: "allow"}))

            messages = store.load()
            events = store.load_events()

        self.assertEqual(messages, [Message(role="user", content="hello")])
        self.assertEqual(events[0].kind, EventKinds.PERMISSION_DECISION)
        self.assertEqual(events[0].data[PermissionEventFields.DECISION], "allow")

    def test_json_session_store_persists_and_clears_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = JsonSessionStore(path)
            store.save([Message(role="user", content="hello")])
            store.save_plan(PlanArtifact(goal="ship feature", summary="Implement in small steps", steps=["code", "test"]))

            plan = store.load_plan()
            messages = store.load()
            store.save_plan(None)
            cleared = store.load_plan()

        self.assertEqual(messages, [Message(role="user", content="hello")])
        assert plan is not None
        self.assertEqual(plan.goal, "ship feature")
        self.assertEqual(plan.steps, ["code", "test"])
        self.assertIsNone(cleared)

    def test_json_session_store_persists_and_queries_events(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = JsonSessionStore(path)
            store.append_event(EventRecord(kind="first", data={"value": 1}))
            store.append_event(EventRecord(kind="second", data={"value": 2}))
            store.append_event(EventRecord(kind="first", data={"value": 3}))

            recent = store.query_events(limit=2)
            filtered = store.query_events(kind="first", limit=10)
            paged = store.query_events(limit=1, offset=1)

        self.assertEqual([event.data["value"] for event in recent], [2, 3])
        self.assertEqual([event.data["value"] for event in filtered], [1, 3])
        self.assertEqual([event.data["value"] for event in paged], [2])

    def test_json_session_store_persists_and_clears_workflow(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = JsonSessionStore(path)
            store.save([Message(role="user", content="hello")])
            store.save_workflow(
                WorkflowArtifact(
                    goal="ship",
                    steps=[WorkflowStep(title="test")],
                    delegations=[WorkflowDelegation(task="inspect", summary="ok", success=True)],
                )
            )

            workflow = store.load_workflow()
            messages = store.load()
            store.save_workflow(None)
            cleared = store.load_workflow()

        self.assertEqual(messages, [Message(role="user", content="hello")])
        assert workflow is not None
        self.assertEqual(workflow.goal, "ship")
        self.assertEqual(workflow.steps[0].title, "test")
        self.assertEqual(workflow.delegations[0].summary, "ok")
        self.assertIsNone(cleared)

    def test_json_session_store_keeps_recent_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = JsonSessionStore(path, max_messages=2)
            store.save([
                Message(role="user", content="one"),
                Message(role="assistant", content="two"),
                Message(role="user", content="three"),
            ])

            messages = store.load()

        self.assertEqual([message.content for message in messages], ["two", "three"])

    def test_sqlite_session_store_persists_full_state_and_retrieves_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.sqlite"
            store = SQLiteSessionStore(path, max_messages=2)
            store.save(
                [
                    Message(role="user", content="first topic alpha"),
                    Message(role="assistant", content="second topic beta"),
                    Message(role="user", content="third topic alpha beta"),
                ]
            )
            store.append_event(EventRecord(kind="tool_execution", data={"command": "alpha check"}))
            store.append_event(EventRecord(kind="tool_execution", data={"command": "beta check"}))
            store.save_plan(PlanArtifact(goal="ship", summary="alpha plan"))
            store.save_workflow(WorkflowArtifact(goal="workflow"))

            messages = store.load()
            events = store.query_events(kind="tool_execution", limit=5)
            paged = store.query_events(kind="tool_execution", limit=1, offset=1)
            matches = store.search_memory("alpha", limit=5)
            plan = store.load_plan()
            workflow = store.load_workflow()

        self.assertEqual([message.content for message in messages], ["second topic beta", "third topic alpha beta"])
        self.assertEqual(events[0].kind, "tool_execution")
        self.assertEqual(paged[0].data["command"], "alpha check")
        self.assertTrue(any(match.kind.startswith("message:") for match in matches))
        self.assertTrue(any(match.kind.startswith("event:") for match in matches))
        assert plan is not None
        self.assertEqual(plan.goal, "ship")
        assert workflow is not None
        self.assertEqual(workflow.goal, "workflow")

    def test_sqlite_session_store_uses_fts_memory_when_available(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.sqlite"
            store = SQLiteSessionStore(path)
            store.save(
                [
                    Message(role="user", content="release checklist"),
                    Message(role="assistant", content="rollback strategy and release notes"),
                ]
            )
            store.append_event(EventRecord(kind="tool_execution", data={"command": "release checklist"}))

            matches = store.search_memory("release checklist", limit=5)

        self.assertTrue(any(match.kind == "message:user" for match in matches))
        self.assertTrue(any(match.kind == "event:tool_execution" for match in matches))

    def test_build_fts_query_quotes_terms(self) -> None:
        self.assertEqual(build_fts_query(['alpha"', "beta"]), '"alpha""" "beta"')


if __name__ == "__main__":
    unittest.main()
