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
        if not should_compact_context(messages, self.config):
            return messages
        if not self.config.summarize_context or self.summarizer is None:
            return compact_messages(messages, self.config.max_context_chars)

        system = [message for message in messages if message.role == "system"][:1]
        tail = messages[-self.config.context_tail_messages :]
        older = messages[len(system) : max(len(system), len(messages) - len(tail))]
        if not older:
            return compact_messages(messages, self.config.max_context_chars)

        cache_key = context_cache_key(older)
        summary = self.summary_cache.get(cache_key)
        if summary is None:
            initial_goal = first_user_content(messages)
            summary = Message(role="user", content=build_summary_message(self.summarizer.complete(build_summary_prompt(older)), initial_goal))
            self.summary_cache[cache_key] = summary
        return system + [summary] + tail


def should_compact_context(messages: list[Message], config: AgentConfig) -> bool:
    if len(messages) <= 4:
        return False
    if config.model_context_tokens is not None:
        threshold = max(1, int(config.model_context_tokens * config.context_compression_ratio))
        return estimate_context_tokens(messages) >= threshold
    return total_message_chars(messages) > config.max_context_chars


def estimate_context_tokens(messages: list[Message]) -> int:
    return sum(max(1, (len(message.content) + 3) // 4) + 4 for message in messages)


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
        Message(
            role="user",
            content=(
                "Summarize this prior transcript. Preserve the original user goal, open decisions, files touched, "
                f"and current next step:\n\n{transcript}"
            ),
        ),
    ]


def build_summary_message(summary: str, initial_goal: str = "") -> str:
    goal = f"Initial user goal:\n{initial_goal.strip()}\n\n" if initial_goal.strip() else ""
    return f"{goal}Context summary from earlier messages:\n{summary.strip()}"


def first_user_content(messages: list[Message]) -> str:
    for message in messages:
        if message.role == "user" and message.content.strip():
            return message.content
    return ""
