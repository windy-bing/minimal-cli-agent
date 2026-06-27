from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from minimal_cli_agent.constants import Defaults, PermissionModes, Providers, SessionFields
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
        return {SessionFields.ROLE: self.role, SessionFields.CONTENT: self.content}


@dataclass(frozen=True)
class EventRecord:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            SessionFields.KIND: self.kind,
            SessionFields.DATA: self.data,
            SessionFields.TIMESTAMP: self.timestamp,
        }


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
    model_timeout: int = int(Defaults.MODEL_TIMEOUT)
    permission_mode: PermissionMode = PermissionModes.DEFAULT
    allow_network: bool = False
    policy_file: Path | None = None
    mcp_config: Path | None = None
    skill_paths: tuple[Path, ...] = field(default_factory=tuple)
    summarize_context: bool = False
    context_tail_messages: int = int(Defaults.CONTEXT_TAIL_MESSAGES)
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
    field_errors: tuple[str, ...] = ()

    def as_observation(self) -> str:
        field_errors = ""
        if self.field_errors:
            field_errors = "field_errors:\n" + "\n".join(f"- {error}" for error in self.field_errors) + "\n"
        return (
            "Tool validation failed.\n"
            f"tool: {self.tool_name}\n"
            f"error: {self.message}\n"
            f"{field_errors}"
            f"expected:\n{self.expected_format}\n"
            f"received:\n{self.received}"
        )


@dataclass(frozen=True)
class ToolDiscoveryError:
    tool_name: str
    available_tools: tuple[str, ...]
    suggested_tools: tuple[str, ...] = ()

    def as_observation(self) -> str:
        available = ", ".join(self.available_tools) if self.available_tools else "<none>"
        suggestions = ""
        if self.suggested_tools:
            suggestions = f"suggested_tools: {', '.join(self.suggested_tools)}\n"
        return (
            "Tool discovery failed.\n"
            f"tool: {self.tool_name}\n"
            f"available_tools: {available}\n"
            f"{suggestions}"
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
        status = "skipped" if self.skipped else "success" if self.exit_code == 0 else "failed"
        if self.skipped:
            header = "Command skipped:"
        else:
            header = f"Command finished with exit code {self.exit_code}:"
        return (
            f"{header}\n"
            f"status: {status}\n"
            f"exit_code: {self.exit_code}\n"
            "command:\n"
            f"```text\n{command}\n```\n"
            "output:\n"
            f"```text\n{output}\n```"
        )
