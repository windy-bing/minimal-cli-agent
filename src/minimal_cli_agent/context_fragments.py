from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import json
from typing import Any

from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import Message

CONTEXT_FRAGMENTS_OPEN = '<minimal_agent_context_fragments schema="minimal_cli_agent.context_fragments.v2">'
CONTEXT_FRAGMENTS_CLOSE = "</minimal_agent_context_fragments>"


@dataclass(frozen=True)
class ContextFragment:
    kind: str
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    ttl: str | None = None

    @property
    def hash(self) -> str:
        payload = {
            "kind": self.kind,
            "id": self.id,
            "content": self.content,
            "metadata": self.metadata,
            "priority": self.priority,
            "ttl": self.ttl,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


def assemble_context_fragments(fragments: list[ContextFragment], max_chars: int | None = None) -> list[ContextFragment]:
    unique: dict[tuple[str, str], ContextFragment] = {}
    for fragment in fragments:
        key = (fragment.kind, fragment.id)
        existing = unique.get(key)
        if existing is None or (fragment.priority, fragment.hash) <= (existing.priority, existing.hash):
            unique[key] = fragment
    ordered = sorted(unique.values(), key=lambda fragment: (fragment.priority, fragment.kind, fragment.id, fragment.hash))
    if max_chars is None or max_chars <= 0:
        return ordered
    kept: list[ContextFragment] = []
    remaining = max_chars
    for fragment in ordered:
        if remaining <= 0:
            break
        rendered_len = len(render_context_fragment(fragment))
        if rendered_len <= remaining:
            kept.append(fragment)
            remaining -= rendered_len
            continue
        if remaining > 160:
            trimmed = fragment.content[: max(0, remaining - 120)].rstrip() + "\n[truncated by context fragment budget]"
            kept.append(
                ContextFragment(
                    kind=fragment.kind,
                    id=fragment.id,
                    content=trimmed,
                    metadata={**fragment.metadata, "truncated": True},
                    priority=fragment.priority,
                    ttl=fragment.ttl,
                )
            )
        break
    return kept


def build_context_fragments_message(fragments: list[ContextFragment], max_chars: int | None = None) -> Message | None:
    assembled = assemble_context_fragments(fragments, max_chars=max_chars)
    if not assembled:
        return None
    body = "\n".join(render_context_fragment(fragment) for fragment in assembled)
    return Message(role="user", content=f"{CONTEXT_FRAGMENTS_OPEN}\n{body}\n{CONTEXT_FRAGMENTS_CLOSE}")


def render_context_fragment(fragment: ContextFragment) -> str:
    attrs = {
        "kind": fragment.kind,
        "id": fragment.id,
        "priority": str(fragment.priority),
        "hash": fragment.hash,
    }
    if fragment.ttl is not None:
        attrs["ttl"] = fragment.ttl
    attr_text = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
    metadata = redact_text(json.dumps(fragment.metadata, ensure_ascii=False, sort_keys=True, default=str))
    content = redact_text(fragment.content.strip())
    return f"<fragment {attr_text}>\n<metadata>{html.escape(metadata)}</metadata>\n{content}\n</fragment>"


def strip_context_fragment_messages(messages: list[Message]) -> list[Message]:
    return [message for message in messages if not is_context_fragments_message(message)]


def is_context_fragments_message(message: Message) -> bool:
    content = message.content.strip()
    return content.startswith(CONTEXT_FRAGMENTS_OPEN) and content.endswith(CONTEXT_FRAGMENTS_CLOSE)
