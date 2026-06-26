import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.constants import EventKinds, PermissionEventFields
from minimal_cli_agent.memory import JsonSessionStore, compact_messages
from minimal_cli_agent.plan import PlanArtifact
from minimal_cli_agent.types import EventRecord, Message


class MemoryTest(unittest.TestCase):
    def test_compact_messages_keeps_system_and_tail(self) -> None:
        messages = [Message("system", "s" * 10)]
        messages += [Message("user", str(i) * 20) for i in range(20)]

        compacted = compact_messages(messages, max_chars=80)

        self.assertEqual(compacted[0].role, "system")
        self.assertIn("compacted", compacted[1].content)
        self.assertEqual(compacted[-1].content, "19" * 20)

    def test_json_session_store_reads_legacy_message_list(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text('[{"role": "user", "content": "hello"}]', encoding="utf-8")
            store = JsonSessionStore(path)

            messages = store.load()

        self.assertEqual(messages, [Message(role="user", content="hello")])

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
        self.assertIsNotNone(plan)
        self.assertEqual(plan.goal, "ship feature")
        self.assertEqual(plan.steps, ["code", "test"])
        self.assertIsNone(cleared)


if __name__ == "__main__":
    unittest.main()
