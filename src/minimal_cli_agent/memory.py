from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from minimal_cli_agent.constants import Defaults, SessionFields
from minimal_cli_agent.plan import PlanArtifact
from minimal_cli_agent.types import EventRecord, Message


class JsonSessionStore:
    def __init__(self, path: Path, max_messages: int = int(Defaults.SESSION_MAX_MESSAGES)) -> None:
        self.path = path
        self.max_messages = max_messages

    def load(self) -> list[Message]:
        raw = self._read_raw()
        if raw is None:
            return []
        raw_messages = raw[SessionFields.MESSAGES] if isinstance(raw, dict) else raw
        return [
            Message(role=item[SessionFields.ROLE], content=item[SessionFields.CONTENT])
            for item in raw_messages
        ]

    def save(self, messages: list[Message]) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            existing_events = parse_events(raw)
            existing_plan = parse_plan(raw)
            data = self._build_data(messages, existing_events, existing_plan)
            self._write_raw_unlocked(data)

    def load_events(self) -> list[EventRecord]:
        return parse_events(self._read_raw())

    def append_event(self, event: EventRecord) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            messages = parse_messages(raw)
            events = [*parse_events(raw), event]
            existing_plan = parse_plan(raw)
            data = self._build_data(messages, events, existing_plan)
            self._write_raw_unlocked(data)

    def load_plan(self) -> PlanArtifact | None:
        return parse_plan(self._read_raw())

    def save_plan(self, plan: PlanArtifact | None) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            messages = parse_messages(raw)
            events = parse_events(raw)
            data = self._build_data(messages, events, plan)
            self._write_raw_unlocked(data)

    def _build_data(self, messages: list[Message], events: list[EventRecord], plan: PlanArtifact | None) -> dict:
        data = {
            SessionFields.MESSAGES: [message.to_dict() for message in messages[-self.max_messages :]],
            SessionFields.EVENTS: [event.to_dict() for event in events],
        }
        if plan is not None:
            data[SessionFields.PLAN] = plan.to_dict()
        return data

    def _read_raw(self):
        with self._locked():
            return self._read_raw_unlocked()

    def _read_raw_unlocked(self):
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_raw_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def parse_messages(raw) -> list[Message]:
    if raw is None:
        return []
    raw_messages = raw[SessionFields.MESSAGES] if isinstance(raw, dict) else raw
    return [
        Message(role=item[SessionFields.ROLE], content=item[SessionFields.CONTENT])
        for item in raw_messages
    ]


def parse_events(raw) -> list[EventRecord]:
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


def parse_plan(raw) -> PlanArtifact | None:
    if not isinstance(raw, dict):
        return None
    raw_plan = raw.get(SessionFields.PLAN)
    if not isinstance(raw_plan, dict):
        return None
    return PlanArtifact.from_dict(raw_plan)


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
