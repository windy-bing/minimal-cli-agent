from __future__ import annotations

from minimal_cli_agent.interfaces import SessionStore
from minimal_cli_agent.types import EventRecord


class TraceAsserter:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def events(self, kind: str) -> list[EventRecord]:
        return self.store.query_events(kind=kind, limit=100)

    def require_event(self, kind: str, **fields: object) -> EventRecord:
        for event in self.events(kind):
            if all(event.data.get(key) == value for key, value in fields.items()):
                return event
        raise AssertionError(f"missing {kind} event matching {fields}")

    def require_call_id(self, kind: str, call_id: str) -> list[EventRecord]:
        events = [event for event in self.events(kind) if event.data.get("call_id") == call_id]
        if not events:
            raise AssertionError(f"missing {kind} events for call_id={call_id}")
        return events
