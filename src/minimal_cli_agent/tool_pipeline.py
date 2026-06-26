from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from minimal_cli_agent.interfaces import PermissionPolicy
from minimal_cli_agent.tool_registry import ToolRegistry
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.constants import ToolDecisionKinds
from minimal_cli_agent.types import CommandResult, ToolCall, ToolDecision, ToolDiscoveryError

Hook = Callable[[ToolCall], None]
PostHook = Callable[[ToolCall, CommandResult], None]
DecisionHook = Callable[[ToolCall, ToolDecision], ToolDecision | None]


@dataclass
class ToolPipelineHooks:
    pre_hooks: list[Hook] = field(default_factory=list)
    decision_hooks: list[DecisionHook] = field(default_factory=list)
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
        try:
            spec = self._discovery(call)
        except KeyError:
            discovery_error = ToolDiscoveryError(
                tool_name=call.name,
                available_tools=self.registry.available_names(),
                suggested_tools=self.registry.suggested_names(call.name),
            )
            return CommandResult(command=call.payload, exit_code=127, output=discovery_error.as_observation(), skipped=True)
        canonical_call = ToolCall(name=spec.name, payload=call.payload)
        validation_error = self._validation(canonical_call)
        if validation_error is not None:
            return CommandResult(command=call.payload, exit_code=2, output=validation_error.as_observation(), skipped=True)
        decision = self._permission(canonical_call)
        self._pre_hook(canonical_call)
        decision = self._resolve_decision(canonical_call, decision)
        decision = self._confirmation(canonical_call, decision)
        if decision.kind == ToolDecisionKinds.SKIP:
            return CommandResult(command=call.payload, exit_code=0, output=decision.reason or "tool skipped", skipped=True)
        if decision.kind == ToolDecisionKinds.DENY:
            raise PermissionDenied(decision.reason or f"Denied tool call: {call.name}")
        result = self._execution(canonical_call)
        self._post_hook(canonical_call, result)
        self._auto_verify(canonical_call, result)
        return self._formatting(result)

    def _discovery(self, call: ToolCall):
        return self.registry.require(call.name)

    def _validation(self, call: ToolCall):
        return self.registry.require(call.name).validate(call.payload)

    def _permission(self, call: ToolCall) -> ToolDecision:
        return self.permission_policy.decide(call.name, call.payload)

    def _pre_hook(self, call: ToolCall) -> None:
        for hook in self.hooks.pre_hooks:
            hook(call)

    def _resolve_decision(self, call: ToolCall, decision: ToolDecision) -> ToolDecision:
        resolved = decision
        for hook in self.hooks.decision_hooks:
            hook_decision = hook(call, resolved)
            if hook_decision is not None:
                resolved = hook_decision
        return resolved

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
