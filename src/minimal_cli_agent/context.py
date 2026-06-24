from __future__ import annotations

import hashlib

from minimal_cli_agent.interfaces import Model
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.prompts import CONTEXT_SUMMARY_SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, Message


class CompactingContextManager:
    def __init__(self, config: AgentConfig, summarizer: Model | None = None) -> None:
        self.config = config
        self.summarizer = summarizer
        self.summary_cache: dict[str, Message] = {}

    def prepare(self, messages: list[Message]) -> list[Message]:
        if not self.config.summarize_context or self.summarizer is None:
            return compact_messages(messages, self.config.max_context_chars)
        if total_message_chars(messages) <= self.config.max_context_chars:
            return messages

        system = [message for message in messages if message.role == "system"][:1]
        tail = messages[-self.config.context_tail_messages :]
        older = messages[len(system) : max(len(system), len(messages) - len(tail))]
        if not older:
            return compact_messages(messages, self.config.max_context_chars)

        cache_key = context_cache_key(older)
        summary = self.summary_cache.get(cache_key)
        if summary is None:
            summary = Message(role="user", content=build_summary_message(self.summarizer.complete(build_summary_prompt(older))))
            self.summary_cache[cache_key] = summary
        return system + [summary] + tail


def total_message_chars(messages: list[Message]) -> int:
    return sum(len(message.content) for message in messages)


def context_cache_key(messages: list[Message]) -> str:
    digest = hashlib.sha256()
    for message in messages:
        digest.update(message.role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(message.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_summary_prompt(messages: list[Message]) -> list[Message]:
    transcript = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
    return [
        Message(role="system", content=CONTEXT_SUMMARY_SYSTEM_PROMPT),
        Message(role="user", content=f"Summarize this prior transcript:\n\n{transcript}"),
    ]


def build_summary_message(summary: str) -> str:
    return f"Context summary from earlier messages:\n{summary.strip()}"
