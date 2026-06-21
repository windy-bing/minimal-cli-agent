from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from minimal_cli_agent.interfaces import PermissionPolicy
from minimal_cli_agent.tool_registry import ToolRegistry
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.types import CommandResult, ToolCall, ToolDecision

Hook = Callable[[ToolCall], None]
PostHook = Callable[[ToolCall, CommandResult], None]


@dataclass
class ToolPipelineHooks:
    pre_hooks: list[Hook] = field(default_factory=list)
    post_hooks: list[PostHook] = field(default_factory=list)


class ToolExecutionPipeline:
    """Discovery -> Validation -> Permission -> PreHook -> ResolveDecision -> Confirmation -> Execution -> PostHook -> AutoVerify -> Formatting."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy,
        hooks: ToolPipelineHooks | None = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy
        self.hooks = hooks or ToolPipelineHooks()

    def execute(self, call: ToolCall) -> CommandResult:
        self._discovery(call)
        self._validation(call)
        decision = self._permission(call)
        self._pre_hook(call)
        decision = self._resolve_decision(call, decision)
        decision = self._confirmation(call, decision)
        if decision.kind == "skip":
            return CommandResult(command=call.payload, exit_code=0, output=decision.reason or "tool skipped", skipped=True)
        if decision.kind == "deny":
            raise PermissionDenied(decision.reason or f"Denied tool call: {call.name}")
        result = self._execution(call)
        self._post_hook(call, result)
        self._auto_verify(call, result)
        return self._formatting(result)

    def _discovery(self, call: ToolCall) -> None:
        self.registry.require(call.name)

    def _validation(self, call: ToolCall) -> None:
        if not call.payload.strip():
            raise ValueError(f"Tool payload for {call.name} is empty.")

    def _permission(self, call: ToolCall) -> ToolDecision:
        return self.permission_policy.decide(call.name, call.payload)

    def _pre_hook(self, call: ToolCall) -> None:
        for hook in self.hooks.pre_hooks:
            hook(call)

    def _resolve_decision(self, call: ToolCall, decision: ToolDecision) -> ToolDecision:
        return decision

    def _confirmation(self, call: ToolCall, decision: ToolDecision) -> ToolDecision:
        return self.permission_policy.confirm(call.name, call.payload, decision)

    def _execution(self, call: ToolCall) -> CommandResult:
        return self.registry.execute(call.name, call.payload)

    def _post_hook(self, call: ToolCall, result: CommandResult) -> None:
        for hook in self.hooks.post_hooks:
            hook(call, result)

    def _auto_verify(self, call: ToolCall, result: CommandResult) -> None:
        if result.output is None:
            raise ValueError(f"Tool {call.name} returned an invalid result.")

    def _formatting(self, result: CommandResult) -> CommandResult:
        return result
