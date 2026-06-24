from __future__ import annotations

from collections.abc import Callable

from minimal_cli_agent.constants import EventKinds, PermissionEventFields, PermissionModes, ToolDecisionKinds, Tools
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
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Unknown action type: {action}")

        lowered = payload.lower()
        if any(token in lowered for token in DANGEROUS_TOKENS):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked dangerous command: {payload}")
        if any(token in lowered for token in SENSITIVE_PATH_TOKENS):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked command touching sensitive path: {payload}")
        if not self.config.allow_network and any(token in f" {lowered} " for token in NETWORK_COMMAND_TOKENS):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked network command without --allow-network: {payload}")

        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="yolo mode")

        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind=ToolDecisionKinds.SKIP, reason="plan mode does not execute shell commands")

        if self.config.permission_mode in {PermissionModes.DEFAULT, PermissionModes.AUTO_EDIT}:
            if payload in self.approved_shell_commands:
                return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session approval memory")
            return ToolDecision(kind=ToolDecisionKinds.ASK, reason=f"{self.config.permission_mode} mode requires shell confirmation")

        return ToolDecision(kind=ToolDecisionKinds.ASK, reason="confirmation required")

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        if decision.kind != ToolDecisionKinds.ASK:
            return decision
        answer = input(f"\nAllow command?\n{payload}\n[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            self._record_permission_event(action, payload, ToolDecisionKinds.DENY, "user denied")
            raise PermissionDenied(f"User denied command: {payload}")
        if action == Tools.SHELL:
            self.approved_shell_commands.add(payload)
        reason = f"user approved {action}"
        self._record_permission_event(action, payload, ToolDecisionKinds.ALLOW, reason)
        return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason=reason)

    def _record_permission_event(self, action: str, payload: str, decision: str, reason: str) -> None:
        if not self.audit_recorder:
            return
        self.audit_recorder(
            EventKinds.PERMISSION_DECISION,
            {
                PermissionEventFields.ACTION: action,
                PermissionEventFields.DECISION: decision,
                PermissionEventFields.REASON: reason,
                PermissionEventFields.PAYLOAD: redact_text(payload),
                PermissionEventFields.PERMISSION_MODE: self.config.permission_mode,
            },
        )
