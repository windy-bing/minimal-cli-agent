from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import Message

CONTEXT_WINDOW_SUMMARY_OPEN = '<minimal_agent_context_window_summary schema="minimal_cli_agent.context_window_summary.v1">'
CONTEXT_WINDOW_SUMMARY_CLOSE = "</minimal_agent_context_window_summary>"


@dataclass(frozen=True)
class ContextWindowSummary:
    task_goal: str = ""
    files_read: tuple[str, ...] = ()
    files_written: tuple[str, ...] = ()
    key_observations: tuple[str, ...] = ()
    open_items: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    source_message_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "task_goal": self.task_goal,
            "files_read": list(self.files_read),
            "files_written": list(self.files_written),
            "key_observations": list(self.key_observations),
            "open_items": list(self.open_items),
            "risks": list(self.risks),
            "source_message_count": self.source_message_count,
        }


def open_context_window(messages: list[Message], extra_summary: str = "") -> tuple[list[Message], ContextWindowSummary]:
    summary = build_context_window_summary(messages, extra_summary=extra_summary)
    system = [messages[0]] if messages and messages[0].role == "system" else []
    return [*system, Message(role="user", content=format_context_window_summary(summary))], summary


def build_context_window_summary(messages: list[Message], extra_summary: str = "") -> ContextWindowSummary:
    task_goal = first_user_task(messages)
    files_read: set[str] = set()
    files_written: set[str] = set()
    observations: list[str] = []
    risks: list[str] = []
    for message in messages:
        content = message.content
        for path in re.findall(r'"path"\s*:\s*"([^"]+)"', content):
            if any(marker in content for marker in ("read_file", "read_forward", "read_tail", "file_info")):
                files_read.add(path)
            if any(marker in content for marker in ("write_file", "edit_file")):
                files_written.add(path)
        if "Tool observation for model context" in content or "Command " in content:
            compact = " ".join(content.split())
            if compact:
                observations.append(compact[:300])
        lowered = content.lower()
        if any(token in lowered for token in ("error", "failed", "denied", "risk", "blocked")):
            risks.append(" ".join(content.split())[:200])
    if extra_summary.strip():
        observations.insert(0, extra_summary.strip())
    return ContextWindowSummary(
        task_goal=task_goal,
        files_read=tuple(sorted(files_read)),
        files_written=tuple(sorted(files_written)),
        key_observations=tuple(observations[-8:]),
        open_items=(),
        risks=tuple(risks[-5:]),
        source_message_count=len(messages),
    )


def format_context_window_summary(summary: ContextWindowSummary) -> str:
    payload = redact_text(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return f"{CONTEXT_WINDOW_SUMMARY_OPEN}\n{payload}\n{CONTEXT_WINDOW_SUMMARY_CLOSE}"


def is_context_window_summary_message(message: Message) -> bool:
    content = message.content.strip()
    return content.startswith(CONTEXT_WINDOW_SUMMARY_OPEN) and content.endswith(CONTEXT_WINDOW_SUMMARY_CLOSE)


def first_user_task(messages: list[Message], limit: int = 500) -> str:
    for message in messages:
        if message.role != "user":
            continue
        content = " ".join(message.content.split())
        if not content or content.startswith("<minimal_agent_"):
            continue
        return content if len(content) <= limit else content[: limit - 3] + "..."
    return ""
