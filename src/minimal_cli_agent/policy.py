from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from minimal_cli_agent.constants import (
    EventKinds,
    PermissionEventFields,
    PermissionModes,
    PolicyDefaults,
    PolicyFileFields,
    ToolDecisionKinds,
    ToolPayloadFields,
    Tools,
)
from minimal_cli_agent.exceptions import ConfigurationError, PermissionDenied
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import AgentConfig, ToolDecision

ConfirmationHandler = Callable[[str, str], bool]


@dataclass(frozen=True)
class ShellPolicyRules:
    dangerous_tokens: tuple[str, ...] = PolicyDefaults.DANGEROUS_TOKENS
    sensitive_path_tokens: tuple[str, ...] = PolicyDefaults.SENSITIVE_PATH_TOKENS
    network_command_tokens: tuple[str, ...] = PolicyDefaults.NETWORK_COMMAND_TOKENS


def load_shell_policy_rules(config: AgentConfig) -> ShellPolicyRules:
    rules = ShellPolicyRules()
    if config.policy_file is None:
        return rules

    try:
        raw = json.loads(config.policy_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read policy file {config.policy_file}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Policy file must be valid JSON: {config.policy_file}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("Policy file must contain a JSON object.")

    return ShellPolicyRules(
        dangerous_tokens=rules.dangerous_tokens + read_token_list(raw, PolicyFileFields.DENY_COMMAND_TOKENS),
        sensitive_path_tokens=rules.sensitive_path_tokens + read_token_list(raw, PolicyFileFields.SENSITIVE_PATH_TOKENS),
        network_command_tokens=rules.network_command_tokens + read_token_list(raw, PolicyFileFields.NETWORK_COMMAND_TOKENS),
    )


def read_token_list(raw: dict, field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"Policy field {field} must be a list of non-empty strings.")
    return tuple(item.lower() for item in value)


class ShellPermissionPolicy:
    def __init__(
        self,
        config: AgentConfig,
        audit_recorder: Callable[[str, dict], None] | None = None,
        confirmation_handler: ConfirmationHandler | None = None,
    ) -> None:
        self.config = config
        self.audit_recorder = audit_recorder
        self.confirmation_handler = confirmation_handler or input_confirmation_handler
        self.approved_tool_calls: set[tuple[str, str]] = set()
        self.rules = load_shell_policy_rules(config)

    def decide(self, action: str, payload: str) -> ToolDecision:
        if action.startswith(Tools.MCP_PREFIX):
            return self._decide_mcp(action, payload)

        if action not in {Tools.SHELL, *Tools.WRITERS, *Tools.READ_ONLY}:
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Unknown action type: {action}")

        lowered = permission_target(action, payload).lower()
        if action == Tools.SHELL and any(token in lowered for token in self.rules.dangerous_tokens):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked dangerous command: {payload}")
        if any(token in lowered for token in self.rules.sensitive_path_tokens):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked tool touching sensitive path: {payload}")
        if action == Tools.SHELL and not self.config.allow_network and any(token in f" {lowered} " for token in self.rules.network_command_tokens):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked network command without --allow-network: {payload}")

        if action in Tools.READ_ONLY:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="read-only file tool")

        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="yolo mode")

        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind=ToolDecisionKinds.SKIP, reason=f"plan mode does not execute {action}")

        if action in Tools.WRITERS and self.config.permission_mode == PermissionModes.AUTO_EDIT:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="autoEdit mode allows file edits")

        if self.config.permission_mode in {PermissionModes.DEFAULT, PermissionModes.AUTO_EDIT}:
            if (action, payload) in self.approved_tool_calls:
                return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session approval memory")
            return ToolDecision(kind=ToolDecisionKinds.ASK, reason=f"{self.config.permission_mode} mode requires {action} confirmation")

        return ToolDecision(kind=ToolDecisionKinds.ASK, reason="confirmation required")

    def _decide_mcp(self, action: str, payload: str) -> ToolDecision:
        if action.endswith("_list_tools"):
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="MCP tool discovery")
        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="yolo mode")
        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind=ToolDecisionKinds.SKIP, reason=f"plan mode does not execute {action}")
        if (action, payload) in self.approved_tool_calls:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session approval memory")
        return ToolDecision(kind=ToolDecisionKinds.ASK, reason=f"{self.config.permission_mode} mode requires {action} confirmation")

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        if decision.kind != ToolDecisionKinds.ASK:
            return decision
        if not self.confirmation_handler(action, payload):
            self._record_permission_event(action, payload, ToolDecisionKinds.DENY, "user denied")
            raise PermissionDenied(f"User denied {action}: {payload}")
        self.approved_tool_calls.add((action, payload))
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


def permission_target(action: str, payload: str) -> str:
    if action in {*Tools.WRITERS, *Tools.READ_ONLY}:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            return payload
        if isinstance(raw, dict):
            return str(raw.get(ToolPayloadFields.PATH, ""))
    return payload


def input_confirmation_handler(action: str, payload: str) -> bool:
    answer = input(f"\nAllow {action}?\n{payload}\n[y/N] ").strip().lower()
    return answer in {"y", "yes"}
