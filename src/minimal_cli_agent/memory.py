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
from minimal_cli_agent.workflow import WorkflowArtifact


class JsonSessionStore:
    def __init__(self, path: Path, max_messages: int = int(Defaults.SESSION_MAX_MESSAGES)) -> None:
        self.path = path
        self.max_messages = max_messages

    def load(self) -> list[Message]:
        return parse_messages(self._read_raw())

    def save(self, messages: list[Message]) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            existing_events = parse_events(raw)
            existing_plan = parse_plan(raw)
            existing_workflow = parse_workflow(raw)
            data = self._build_data(messages, existing_events, existing_plan, existing_workflow)
            self._write_raw_unlocked(data)

    def load_events(self) -> list[EventRecord]:
        return parse_events(self._read_raw())

    def append_event(self, event: EventRecord) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            messages = parse_messages(raw)
            events = [*parse_events(raw), event]
            existing_plan = parse_plan(raw)
            existing_workflow = parse_workflow(raw)
            data = self._build_data(messages, events, existing_plan, existing_workflow)
            self._write_raw_unlocked(data)

    def query_events(self, kind: str | None = None, limit: int = 20) -> list[EventRecord]:
        events = self.load_events()
        if kind:
            events = [event for event in events if event.kind == kind]
        return events[-max(1, limit) :]

    def load_plan(self) -> PlanArtifact | None:
        return parse_plan(self._read_raw())

    def save_plan(self, plan: PlanArtifact | None) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            messages = parse_messages(raw)
            events = parse_events(raw)
            existing_workflow = parse_workflow(raw)
            data = self._build_data(messages, events, plan, existing_workflow)
            self._write_raw_unlocked(data)

    def load_workflow(self) -> WorkflowArtifact | None:
        return parse_workflow(self._read_raw())

    def save_workflow(self, workflow: WorkflowArtifact | None) -> None:
        with self._locked():
            raw = self._read_raw_unlocked()
            messages = parse_messages(raw)
            events = parse_events(raw)
            existing_plan = parse_plan(raw)
            data = self._build_data(messages, events, existing_plan, workflow)
            self._write_raw_unlocked(data)

    def _build_data(
        self,
        messages: list[Message],
        events: list[EventRecord],
        plan: PlanArtifact | None,
        workflow: WorkflowArtifact | None,
    ) -> dict:
        data = {
            SessionFields.MESSAGES: [message.to_dict() for message in messages[-self.max_messages :]],
            SessionFields.EVENTS: [event.to_dict() for event in events],
        }
        if plan is not None:
            data[SessionFields.PLAN] = plan.to_dict()
        if workflow is not None:
            data[SessionFields.WORKFLOW] = workflow.to_dict()
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
    raw_messages = raw.get(SessionFields.MESSAGES, []) if isinstance(raw, dict) else raw
    if not isinstance(raw_messages, list):
        return []
    return [
        Message(role=item[SessionFields.ROLE], content=item[SessionFields.CONTENT])
        for item in raw_messages
        if is_message_record(item)
    ]


def parse_events(raw) -> list[EventRecord]:
    if not isinstance(raw, dict):
        return []
    raw_events = raw.get(SessionFields.EVENTS, [])
    if not isinstance(raw_events, list):
        return []
    return [
        EventRecord(
            kind=item[SessionFields.KIND],
            data=item.get(SessionFields.DATA, {}),
            timestamp=item[SessionFields.TIMESTAMP],
        )
        for item in raw_events
        if is_event_record(item)
    ]


def parse_plan(raw) -> PlanArtifact | None:
    if not isinstance(raw, dict):
        return None
    raw_plan = raw.get(SessionFields.PLAN)
    if not isinstance(raw_plan, dict):
        return None
    return PlanArtifact.from_dict(raw_plan)


def parse_workflow(raw) -> WorkflowArtifact | None:
    if not isinstance(raw, dict):
        return None
    raw_workflow = raw.get(SessionFields.WORKFLOW)
    if not isinstance(raw_workflow, dict):
        return None
    return WorkflowArtifact.from_dict(raw_workflow)


def is_message_record(item) -> bool:
    return (
        isinstance(item, dict)
        and item.get(SessionFields.ROLE) in {"system", "user", "assistant"}
        and isinstance(item.get(SessionFields.CONTENT), str)
    )


def is_event_record(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get(SessionFields.KIND), str)
        and isinstance(item.get(SessionFields.TIMESTAMP), str)
        and isinstance(item.get(SessionFields.DATA, {}), dict)
    )


def compact_messages(messages: list[Message], max_chars: int) -> list[Message]:
    total = sum(len(message.content) for message in messages)
    if total <= max_chars or len(messages) <= 4:
        return messages

    system = [message for message in messages if message.role == "system"][:1]
    tail = messages[-8:]
    omitted = len(messages) - len(system) - len(tail)
    initial_goal = first_user_content(messages)
    goal_text = f" Initial user goal: {initial_goal}" if initial_goal else ""
    summary = Message(
        role="user",
        content=(
            f"Context was compacted locally. {omitted} older messages were omitted. "
            f"Continue using the latest observations and task state.{goal_text}"
        ),
    )
    return system + [summary] + tail


def first_user_content(messages: list[Message], limit: int = 500) -> str:
    for message in messages:
        if message.role == "user" and message.content.strip():
            content = " ".join(message.content.split())
            return content if len(content) <= limit else content[: limit - 3] + "..."
    return ""
