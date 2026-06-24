from __future__ import annotations

from minimal_cli_agent.constants import PermissionModes, Tools
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.types import AgentConfig, ToolDecision

DANGEROUS_TOKENS = (
    "rm -rf /",
    "sudo rm",
    "mkfs",
    ":(){",
    "dd if=",
)


class ShellPermissionPolicy:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.approved_shell_commands: set[str] = set()

    def decide(self, action: str, payload: str) -> ToolDecision:
        if action != Tools.SHELL:
            return ToolDecision(kind="deny", reason=f"Unknown action type: {action}")

        lowered = payload.lower()
        if any(token in lowered for token in DANGEROUS_TOKENS):
            return ToolDecision(kind="deny", reason=f"Blocked dangerous command: {payload}")

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
            raise PermissionDenied(f"User denied command: {payload}")
        if action == Tools.SHELL:
            self.approved_shell_commands.add(payload)
        return ToolDecision(kind="allow", reason=f"user approved {action}")
