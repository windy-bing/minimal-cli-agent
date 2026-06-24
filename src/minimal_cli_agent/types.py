from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from minimal_cli_agent.constants import Defaults, PermissionModes, Providers
from minimal_cli_agent.redaction import redact_text

Role = Literal["system", "user", "assistant"]
Provider = Literal["ollama", "openai-compatible", "anthropic", "gemini", "codex"]
ProfileName = Literal["ollama", "codex", "claude", "gemini"]
PermissionMode = Literal["default", "autoEdit", "plan", "yolo"]
DecisionKind = Literal["allow", "ask", "deny", "skip"]


@dataclass
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class EventRecord:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "data": self.data, "timestamp": self.timestamp}


@dataclass
class ChatContext:
    session_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopResult:
    success: bool
    final_messages: list[Message]


@dataclass(frozen=True)
class LoopOptions:
    allow_final_text: bool = False
    system_prompt: str | None = None


@dataclass
class AgentConfig:
    provider: Provider = Providers.OLLAMA
    model: str = Defaults.MODEL
    base_url: str = Defaults.BASE_URL
    api_key: str | None = None
    cwd: Path = field(default_factory=Path.cwd)
    max_steps: int = 20
    command_timeout: int = 30
    permission_mode: PermissionMode = PermissionModes.DEFAULT
    allow_network: bool = False
    max_output_chars: int = 12000
    max_context_chars: int = 60000


@dataclass(frozen=True)
class ToolCall:
    name: str
    payload: str


@dataclass(frozen=True)
class ToolValidationError:
    tool_name: str
    message: str
    expected_format: str
    received: str

    def as_observation(self) -> str:
        return (
            "Tool validation failed.\n"
            f"tool: {self.tool_name}\n"
            f"error: {self.message}\n"
            f"expected:\n{self.expected_format}\n"
            f"received:\n{self.received}"
        )


@dataclass(frozen=True)
class ToolDiscoveryError:
    tool_name: str
    available_tools: tuple[str, ...]

    def as_observation(self) -> str:
        available = ", ".join(self.available_tools) if self.available_tools else "<none>"
        return (
            "Tool discovery failed.\n"
            f"tool: {self.tool_name}\n"
            f"available_tools: {available}\n"
            "Use one of the available tool names or aliases."
        )


@dataclass(frozen=True)
class ToolDecision:
    kind: DecisionKind
    reason: str = ""


@dataclass
class CommandResult:
    command: str
    exit_code: int
    output: str
    skipped: bool = False

    def as_observation(self) -> str:
        command = redact_text(self.command)
        output = redact_text(self.output)
        if self.skipped:
            return f"Command skipped:\n{command}\n\n{output}"
        return (
            f"Command finished with exit code {self.exit_code}:\n"
            f"```text\n{output}\n```"
        )
