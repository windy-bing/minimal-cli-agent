from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import fnmatch
import json
from pathlib import PurePath
import shlex
import sys
from typing import Literal

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - platform-dependent.
    termios = None
    tty = None

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

ConfirmationResult = Literal["allow_once", "allow_session_action", "deny"]
ConfirmationHandler = Callable[[str, str], bool | str]


@dataclass(frozen=True)
class ShellPolicyRules:
    dangerous_tokens: tuple[str, ...] = PolicyDefaults.DANGEROUS_TOKENS
    sensitive_path_tokens: tuple[str, ...] = PolicyDefaults.SENSITIVE_PATH_TOKENS
    network_command_tokens: tuple[str, ...] = PolicyDefaults.NETWORK_COMMAND_TOKENS
    allow_command_prefixes: tuple[str, ...] = ()
    write_allow_paths: tuple[str, ...] = ()
    write_deny_paths: tuple[str, ...] = ()


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
        allow_command_prefixes=read_token_list(raw, PolicyFileFields.ALLOW_COMMAND_PREFIXES),
        write_allow_paths=read_path_list(raw, PolicyFileFields.WRITE_ALLOW_PATHS),
        write_deny_paths=read_path_list(raw, PolicyFileFields.WRITE_DENY_PATHS),
    )


def read_token_list(raw: dict, field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"Policy field {field} must be a list of non-empty strings.")
    return tuple(item.lower() for item in value)


def read_path_list(raw: dict, field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ConfigurationError(f"Policy field {field} must be a list of non-empty strings.")
    return tuple(normalize_policy_path(item) for item in value)


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
        self.approved_actions: set[str] = set()
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
        if action == Tools.SHELL and not self.config.allow_network and command_uses_network_tool(payload, self.rules.network_command_tokens):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked network command without --allow-network: {payload}")
        if action in Tools.WRITERS:
            scoped_decision = self._decide_write_scope(lowered, payload)
            if scoped_decision is not None:
                return scoped_decision

        if action in Tools.READ_ONLY:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="read-only file tool")
        if action == Tools.SHELL and command_prefix_allowed(lowered, self.rules.allow_command_prefixes):
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="policy allow_command_prefixes")

        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="yolo mode")

        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind=ToolDecisionKinds.SKIP, reason=f"plan mode does not execute {action}")

        if action in Tools.WRITERS and self.config.permission_mode == PermissionModes.AUTO_EDIT:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="autoEdit mode allows file edits")

        if self.config.permission_mode in {PermissionModes.DEFAULT, PermissionModes.AUTO_EDIT}:
            if action in self.approved_actions:
                return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session action approval memory")
            if (action, payload) in self.approved_tool_calls:
                return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session approval memory")
            return ToolDecision(kind=ToolDecisionKinds.ASK, reason=f"{self.config.permission_mode} mode requires {action} confirmation")

        return ToolDecision(kind=ToolDecisionKinds.ASK, reason="confirmation required")

    def explain(self, action: str, payload: str) -> dict[str, object]:
        decision = self.decide(action, payload)
        return {
            "action": action,
            "payload": redact_text(payload),
            "decision": decision.kind,
            "reason": redact_text(decision.reason),
            "permission_mode": self.config.permission_mode,
            "allow_network": self.config.allow_network,
            "approved_action": action in self.approved_actions,
            "approved_tool_call": (action, payload) in self.approved_tool_calls,
            "rules": {
                "dangerous_tokens": len(self.rules.dangerous_tokens),
                "sensitive_path_tokens": len(self.rules.sensitive_path_tokens),
                "network_command_tokens": len(self.rules.network_command_tokens),
                "allow_command_prefixes": list(self.rules.allow_command_prefixes),
                "write_allow_paths": list(self.rules.write_allow_paths),
                "write_deny_paths": list(self.rules.write_deny_paths),
            },
        }

    def _decide_mcp(self, action: str, payload: str) -> ToolDecision:
        if action.endswith("_list_tools"):
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="MCP tool discovery")
        if self.config.permission_mode == PermissionModes.YOLO:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="yolo mode")
        if self.config.permission_mode == PermissionModes.PLAN:
            return ToolDecision(kind=ToolDecisionKinds.SKIP, reason=f"plan mode does not execute {action}")
        if action in self.approved_actions:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session action approval memory")
        if (action, payload) in self.approved_tool_calls:
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason="session approval memory")
        return ToolDecision(kind=ToolDecisionKinds.ASK, reason=f"{self.config.permission_mode} mode requires {action} confirmation")

    def _decide_write_scope(self, normalized_path: str, payload: str) -> ToolDecision | None:
        if any(path_matches_policy(normalized_path, pattern) for pattern in self.rules.write_deny_paths):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked write by policy write_deny_paths: {payload}")
        if self.rules.write_allow_paths and not any(path_matches_policy(normalized_path, pattern) for pattern in self.rules.write_allow_paths):
            return ToolDecision(kind=ToolDecisionKinds.DENY, reason=f"Blocked write outside policy write_allow_paths: {payload}")
        return None

    def confirm(self, action: str, payload: str, decision: ToolDecision) -> ToolDecision:
        if decision.kind != ToolDecisionKinds.ASK:
            return decision
        confirmation = normalize_confirmation_result(self.confirmation_handler(action, payload))
        if confirmation == "deny":
            self._record_permission_event(action, payload, ToolDecisionKinds.DENY, "user denied")
            raise PermissionDenied(f"User denied {action}: {payload}")
        if confirmation == "allow_session_action":
            self.approved_actions.add(action)
            reason = f"user approved all {action} calls for this session"
            self._record_permission_event(action, payload, ToolDecisionKinds.ALLOW, reason)
            return ToolDecision(kind=ToolDecisionKinds.ALLOW, reason=reason)
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


def command_prefix_allowed(command: str, prefixes: tuple[str, ...]) -> bool:
    normalized = command.strip().lower()
    if command_has_shell_control_operator(normalized):
        return False
    return any(normalized.startswith(prefix.strip().lower()) for prefix in prefixes)


def command_has_shell_control_operator(command: str) -> bool:
    return any(operator in command for operator in ("&&", "||", ";", "|", "\n"))


def command_uses_network_tool(command: str, network_tokens: tuple[str, ...]) -> bool:
    normalized_tokens = {normalize_command_token(token) for token in network_tokens if token.strip()}
    if not normalized_tokens:
        return False
    expect_command = True
    for word in split_command_words(command.lower()):
        normalized = normalize_command_token(word)
        if normalized in {"&&", "||", ";", "|"}:
            expect_command = True
            continue
        if expect_command and "=" in word and not word.startswith(("=", "-")):
            continue
        if expect_command and normalized in {"sudo", "env", "command", "builtin", "nohup", "time"}:
            continue
        if expect_command and normalized in normalized_tokens:
            return True
        expect_command = False
    return False


def split_command_words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def normalize_command_token(token: str) -> str:
    name = PurePath(token.strip().strip("\"'")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def normalize_policy_path(value: str) -> str:
    return value.replace("\\", "/").strip().lstrip("./").lower()


def path_matches_policy(path: str, pattern: str) -> bool:
    normalized_path = normalize_policy_path(path)
    normalized_pattern = normalize_policy_path(pattern)
    if not normalized_pattern:
        return False
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if "**" in normalized_pattern:
        prefix, suffix = normalized_pattern.split("**", 1)
        prefix = prefix.rstrip("/")
        suffix = suffix.lstrip("/")
        if normalized_path.startswith(prefix) and (not suffix or normalized_path.endswith(suffix)):
            return True
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
    return normalized_path == normalized_pattern or normalized_path.startswith(f"{normalized_pattern}/")


def normalize_confirmation_result(value: bool | str) -> ConfirmationResult:
    if value is True:
        return "allow_once"
    if value is False:
        return "deny"
    normalized = value.strip().lower()
    if normalized in {"allow_session_action", "always", "all", "a"}:
        return "allow_session_action"
    if normalized in {"allow_once", "once", "yes", "y"}:
        return "allow_once"
    return "deny"


def input_confirmation_handler(action: str, payload: str) -> ConfirmationResult:
    options: tuple[tuple[str, ConfirmationResult], ...] = (
        ("Allow once", "allow_once"),
        (f"Allow all {action} this session", "allow_session_action"),
        ("Deny", "deny"),
    )
    if termios is not None and tty is not None and sys.stdin.isatty() and sys.stdout.isatty():
        return select_confirmation_with_arrows(action, payload, options)
    answer = input(f"\nAllow {action}?\n{payload}\n[once/all/deny] ").strip().lower()
    return normalize_confirmation_result(answer)


def select_confirmation_with_arrows(action: str, payload: str, options: tuple[tuple[str, ConfirmationResult], ...]) -> ConfirmationResult:
    print(f"\nPermission required for {action}:")
    print(payload)
    print("Use Up/Down and Enter. Shortcuts: o=once, a=all session, d=deny.")
    selected = 0
    while True:
        render_confirmation_options(options, selected)
        key = read_key()
        clear_confirmation_options(len(options))
        if key in {"\r", "\n"}:
            return options[selected][1]
        if key.lower() == "o":
            return "allow_once"
        if key.lower() == "a":
            return "allow_session_action"
        if key.lower() in {"d", "n", "\x03"}:
            return "deny"
        if key in {"\x1b[A", "k"}:
            selected = (selected - 1) % len(options)
        elif key in {"\x1b[B", "j"}:
            selected = (selected + 1) % len(options)


def render_confirmation_options(options: tuple[tuple[str, ConfirmationResult], ...], selected: int) -> None:
    for index, (label, _) in enumerate(options):
        prefix = ">" if index == selected else " "
        print(f"{prefix} {label}")


def clear_confirmation_options(lines: int) -> None:
    if not sys.stdout.isatty():
        return
    for _ in range(lines):
        print("\033[F\033[K", end="")


def read_key() -> str:
    if termios is None or tty is None:
        return sys.stdin.read(1)
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return sys.stdin.read(1)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x1b":
            rest = sys.stdin.read(1)
            if rest == "[":
                rest += sys.stdin.read(1)
                third = sys.stdin.read(1)
                rest += third
                if third in ("1", "2", "3", "4", "5", "6"):
                    rest += sys.stdin.read(1) if third != "6" else ""
            elif rest == "O":
                rest += sys.stdin.read(1)
            return first + rest
        return first
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass
