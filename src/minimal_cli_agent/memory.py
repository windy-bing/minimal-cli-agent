from __future__ import annotations

import json
from pathlib import Path

from minimal_cli_agent.constants import SessionFields
from minimal_cli_agent.types import EventRecord, Message


class JsonSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Message]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw_messages = raw[SessionFields.MESSAGES] if isinstance(raw, dict) else raw
        return [
            Message(role=item[SessionFields.ROLE], content=item[SessionFields.CONTENT])
            for item in raw_messages
        ]

    def save(self, messages: list[Message]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_events = self.load_events()
        data = {
            SessionFields.MESSAGES: [message.to_dict() for message in messages],
            SessionFields.EVENTS: [event.to_dict() for event in existing_events],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_events(self) -> list[EventRecord]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        raw_events = raw.get(SessionFields.EVENTS, [])
        return [
            EventRecord(
                kind=item[SessionFields.KIND],
                data=item.get(SessionFields.DATA, {}),
                timestamp=item[SessionFields.TIMESTAMP],
            )
            for item in raw_events
        ]

    def append_event(self, event: EventRecord) -> None:
        messages = self.load()
        events = [*self.load_events(), event]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            SessionFields.MESSAGES: [message.to_dict() for message in messages],
            SessionFields.EVENTS: [item.to_dict() for item in events],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_messages(messages: list[Message], max_chars: int) -> list[Message]:
    total = sum(len(message.content) for message in messages)
    if total <= max_chars or len(messages) <= 4:
        return messages

    system = [message for message in messages if message.role == "system"][:1]
    tail = messages[-8:]
    omitted = len(messages) - len(system) - len(tail)
    summary = Message(
        role="user",
        content=(
            f"Context was compacted locally. {omitted} older messages were omitted. "
            "Continue using the latest observations and task state."
        ),
    )
    return system + [summary] + tail
