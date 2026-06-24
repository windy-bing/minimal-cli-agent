from __future__ import annotations

from typing import Protocol

from minimal_cli_agent.types import CommandResult, EventRecord, Message, ToolDecision


class Model(Protocol):
    def complete(self, messages: list[Message]) -> str:
        pass


class ToolExecutor(Protocol):
    def execute(self, command: str) -> CommandResult:
        pass


class SessionStore(Protocol):
    def load(self) -> list[Message]:
        pass

    def save(self, messages: list[Message]) -> None:
        pass

    def append_event(self, event: EventRecord) -> None:
        pass


class ContextManager(Protocol):
    def prepare(self, messages: list[Message]) -> list[Message]:
        pass


class PermissionPolicy(Protocol):
    def decide(self, action: str, payload: str) -> ToolDecision:
        pass

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        pass
