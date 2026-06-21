from __future__ import annotations

from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.types import AgentConfig, Message


class CompactingContextManager:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def prepare(self, messages: list[Message]) -> list[Message]:
        return compact_messages(messages, self.config.max_context_chars)

