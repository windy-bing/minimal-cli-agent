from __future__ import annotations

from typing import Protocol

from minimal_cli_agent.types import CommandResult, EventRecord, Message, ToolDecision


class Model(Protocol):
    def complete(self, messages: list[Message]) -> str: ...


class ToolExecutor(Protocol):
    def execute(self, command: str) -> CommandResult: ...


class SessionStore(Protocol):
    def load(self) -> list[Message]: ...

    def save(self, messages: list[Message]) -> None: ...

    def append_event(self, event: EventRecord) -> None: ...


class ContextManager(Protocol):
    def prepare(self, messages: list[Message]) -> list[Message]: ...


class PermissionPolicy(Protocol):
    def decide(self, action: str, payload: str) -> ToolDecision: ...

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision: ...
