from __future__ import annotations

import json
from pathlib import Path

from minimal_cli_agent.types import Message


class JsonSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Message]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [Message(role=item["role"], content=item["content"]) for item in raw]

    def save(self, messages: list[Message]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [message.to_dict() for message in messages]
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

