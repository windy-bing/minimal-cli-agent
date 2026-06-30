from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from minimal_cli_agent.types import CommandResult, EventRecord, Message, ToolDecision


class Model(Protocol):
    def complete(self, messages: list[Message]) -> str:
        raise NotImplementedError


class StreamingModel(Protocol):
    def stream_complete(self, messages: list[Message]) -> Iterator[str]:
        raise NotImplementedError


class ToolExecutor(Protocol):
    def execute(self, command: str) -> CommandResult:
        raise NotImplementedError


class SessionStore(Protocol):
    path: Any

    def load(self) -> list[Message]:
        raise NotImplementedError

    def save(self, messages: list[Message]) -> None:
        raise NotImplementedError

    def append_event(self, event: EventRecord) -> None:
        raise NotImplementedError

    def load_events(self) -> list[EventRecord]:
        raise NotImplementedError

    def query_events(self, kind: str | None = None, limit: int = 20, offset: int = 0) -> list[EventRecord]:
        raise NotImplementedError

    def load_plan(self) -> Any | None:
        raise NotImplementedError

    def save_plan(self, plan: Any | None) -> None:
        raise NotImplementedError

    def load_workflow(self) -> Any | None:
        raise NotImplementedError

    def save_workflow(self, workflow: Any | None) -> None:
        raise NotImplementedError


class ContextManager(Protocol):
    def prepare(self, messages: list[Message]) -> list[Message]:
        raise NotImplementedError


class PermissionPolicy(Protocol):
    def decide(self, action: str, payload: str) -> ToolDecision:
        raise NotImplementedError

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        raise NotImplementedError
