from __future__ import annotations

from collections.abc import Callable

from minimal_cli_agent.constants import PermissionModes, Tools
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import AgentConfig, ToolDecision

DANGEROUS_TOKENS = (
    "rm -rf /",
    "sudo rm",
    "mkfs",
    ":(){",
    "dd if=",
)

SENSITIVE_PATH_TOKENS = (
    ".env",
    ".env.",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".codex/auth.json",
    ".claude/settings.json",
)

NETWORK_COMMAND_TOKENS = (
    "curl ",
    "wget ",
    "http ",
    "https ",
    "ssh ",
    "scp ",
    "sftp ",
    "rsync ",
    "nc ",
    "ncat ",
    "telnet ",
)


class ShellPermissionPolicy:
    def __init__(self, config: AgentConfig, audit_recorder: Callable[[str, dict], None] | None = None) -> None:
        self.config = config
        self.audit_recorder = audit_recorder
        self.approved_shell_commands: set[str] = set()

    def decide(self, action: str, payload: str) -> ToolDecision:
        if action != Tools.SHELL:
            return ToolDecision(kind="deny", reason=f"Unknown action type: {action}")

        lowered = payload.lower()
        if any(token in lowered for token in DANGEROUS_TOKENS):
            return ToolDecision(kind="deny", reason=f"Blocked dangerous command: {payload}")
        if any(token in lowered for token in SENSITIVE_PATH_TOKENS):
            return ToolDecision(kind="deny", reason=f"Blocked command touching sensitive path: {payload}")
        if not self.config.allow_network and any(token in f" {lowered} " for token in NETWORK_COMMAND_TOKENS):
            return ToolDecision(kind="deny", reason=f"Blocked network command without --allow-network: {payload}")

        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind="allow", reason="yolo mode")

        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind="skip", reason="plan mode does not execute shell commands")

        if self.config.permission_mode in {PermissionModes.DEFAULT, PermissionModes.AUTO_EDIT}:
            if payload in self.approved_shell_commands:
                return ToolDecision(kind="allow", reason="session approval memory")
            return ToolDecision(kind="ask", reason=f"{self.config.permission_mode} mode requires shell confirmation")

        return ToolDecision(kind="ask", reason="confirmation required")

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        if decision.kind != "ask":
            return decision
        answer = input(f"\nAllow command?\n{payload}\n[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            self._record_permission_event(action, payload, "deny", "user denied")
            raise PermissionDenied(f"User denied command: {payload}")
        if action == Tools.SHELL:
            self.approved_shell_commands.add(payload)
        reason = f"user approved {action}"
        self._record_permission_event(action, payload, "allow", reason)
        return ToolDecision(kind="allow", reason=reason)

    def _record_permission_event(self, action: str, payload: str, decision: str, reason: str) -> None:
        if not self.audit_recorder:
            return
        self.audit_recorder(
            "permission_decision",
            {
                "action": action,
                "decision": decision,
                "reason": reason,
                "payload": redact_text(payload),
                "permission_mode": self.config.permission_mode,
            },
        )
