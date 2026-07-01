from __future__ import annotations

from collections import OrderedDict
import hashlib
import json

from minimal_cli_agent.interfaces import Model
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.prompts import CONTEXT_SUMMARY_SYSTEM_PROMPT
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import AgentConfig, Message

RUNTIME_CONTEXT_OPEN = "<minimal_agent_runtime_context>"
RUNTIME_CONTEXT_CLOSE = "</minimal_agent_runtime_context>"


class CompactingContextManager:
    SUMMARY_CACHE_MAX_ENTRIES = 64

    def __init__(self, config: AgentConfig, summarizer: Model | None = None) -> None:
        self.config = config
        self.summarizer = summarizer
        self.summary_cache: OrderedDict[str, Message] = OrderedDict()

    def prepare(self, messages: list[Message]) -> list[Message]:
        messages_without_runtime_context = strip_runtime_context(messages)
        if not should_compact_context(messages_without_runtime_context, self.config):
            return upsert_runtime_context(messages_without_runtime_context, self.config)
        if not self.config.summarize_context or self.summarizer is None:
            compacted = compact_messages(messages_without_runtime_context, self.config.max_context_chars, self.config.context_tail_messages)
            return upsert_runtime_context(compacted, self.config)

        system = [message for message in messages_without_runtime_context if message.role == "system"][:1]
        tail = messages_without_runtime_context[-self.config.context_tail_messages :]
        older = messages_without_runtime_context[len(system) : max(len(system), len(messages_without_runtime_context) - len(tail))]
        if not older:
            compacted = compact_messages(messages_without_runtime_context, self.config.max_context_chars, self.config.context_tail_messages)
            return upsert_runtime_context(compacted, self.config)

        cache_key = context_cache_key(older)
        summary = self.summary_cache.get(cache_key)
        if summary is None:
            initial_goal = first_user_content(messages_without_runtime_context)
            try:
                summary_text = self.summarizer.complete(build_summary_prompt(older))
            except Exception:
                compacted = compact_messages(messages_without_runtime_context, self.config.max_context_chars, self.config.context_tail_messages)
                return upsert_runtime_context(compacted, self.config)
            summary = Message(role="user", content=build_summary_message(summary_text, initial_goal))
            self.summary_cache[cache_key] = summary
            self.summary_cache.move_to_end(cache_key)
            while len(self.summary_cache) > self.SUMMARY_CACHE_MAX_ENTRIES:
                self.summary_cache.popitem(last=False)
        else:
            self.summary_cache.move_to_end(cache_key)
        return upsert_runtime_context(system + [summary] + tail, self.config)


def upsert_runtime_context(messages: list[Message], config: AgentConfig) -> list[Message]:
    cleaned = strip_runtime_context(messages)
    fragment = build_runtime_context_message(config)
    insert_at = 1 if cleaned and cleaned[0].role == "system" else 0
    return [*cleaned[:insert_at], fragment, *cleaned[insert_at:]]


def strip_runtime_context(messages: list[Message]) -> list[Message]:
    return [message for message in messages if not is_runtime_context_message(message)]


def is_runtime_context_message(message: Message) -> bool:
    content = message.content.strip()
    return content.startswith(RUNTIME_CONTEXT_OPEN) and content.endswith(RUNTIME_CONTEXT_CLOSE)


def build_runtime_context_message(config: AgentConfig) -> Message:
    snapshot = {
        "cwd": str(config.cwd),
        "provider": config.provider,
        "model": config.model,
        "permission": config.permission_mode,
        "sandbox": config.sandbox_kind,
        "allow_network": config.allow_network,
        "context": {
            "max_context_chars": config.max_context_chars,
            "context_tail_messages": config.context_tail_messages,
            "model_context_tokens": config.model_context_tokens,
            "model_observation_output_chars": config.model_observation_output_chars,
        },
        "tool_budget": {
            "max_tool_calls_per_turn": config.max_tool_calls_per_turn,
            "max_read_only_tool_calls_per_turn": config.max_read_only_tool_calls_per_turn,
        },
    }
    body = redact_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
    return Message(
        role="user",
        content=(
            f"{RUNTIME_CONTEXT_OPEN}\n"
            "Runtime context visible to the model. This is managed by the CLI; use it as current state, not as user task text.\n"
            f"{body}\n"
            f"{RUNTIME_CONTEXT_CLOSE}"
        ),
    )


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


def first_user_content(messages: list[Message], limit: int = 500) -> str:
    for message in messages:
        if message.role == "user" and message.content.strip():
            content = " ".join(message.content.split())
            if is_compacted_context_message(content):
                continue
            return content if len(content) <= limit else content[: limit - 3] + "..."
    return ""


def is_compacted_context_message(content: str) -> bool:
    return content.startswith(
        (
            "Context was compacted locally.",
            "Initial user goal:",
            "Context summary from earlier messages:",
            RUNTIME_CONTEXT_OPEN,
        )
    )
