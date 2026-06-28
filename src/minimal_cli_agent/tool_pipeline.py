from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from minimal_cli_agent.interfaces import PermissionPolicy
from minimal_cli_agent.tool_registry import ToolRegistry
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.constants import EventKinds, ToolDecisionEventFields, ToolDecisionKinds, ToolExecutionEventFields
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.types import CommandResult, ToolCall, ToolDecision, ToolDiscoveryError

Hook = Callable[[ToolCall], None]
PostHook = Callable[[ToolCall, CommandResult], None]
DecisionHook = Callable[[ToolCall, ToolDecision], ToolDecision | None]
AuditRecorder = Callable[[str, dict], None]


@dataclass(frozen=True)
class DecisionHookSpec:
    hook: DecisionHook
    name: str = "decision_hook"
    priority: int = 100


@dataclass
class ToolPipelineHooks:
    pre_hooks: list[Hook] = field(default_factory=list)
    decision_hooks: list[DecisionHook | DecisionHookSpec] = field(default_factory=list)
    post_hooks: list[PostHook] = field(default_factory=list)


class ToolExecutionPipeline:
    """Discovery -> Validation -> Permission -> PreHook -> ResolveDecision -> Confirmation -> Execution -> PostHook -> AutoVerify -> Formatting."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy,
        hooks: ToolPipelineHooks | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy
        self.hooks = hooks or ToolPipelineHooks()
        self.audit_recorder = audit_recorder

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
        canonical_call = ToolCall(name=spec.name, payload=spec.prepare_payload(canonical_call.payload))
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
        formatted = self._formatting(result)
        self._record_execution_event(canonical_call, spec, formatted)
        return formatted

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
        applied: list[dict[str, str | int]] = []
        conflicting_kinds: set[str] = {decision.kind}
        for hook_spec in sorted_decision_hooks(self.hooks.decision_hooks):
            hook_decision = hook_spec.hook(call, resolved)
            if hook_decision is not None:
                conflicting_kinds.add(hook_decision.kind)
                applied.append(
                    {
                        "name": hook_spec.name,
                        "priority": hook_spec.priority,
                        "from": resolved.kind,
                        "to": hook_decision.kind,
                        "reason": hook_decision.reason,
                    }
                )
                resolved = hook_decision
        if applied:
            self._record_decision_event(call, decision, resolved, applied, conflicting_kinds)
        return resolved

    def _record_decision_event(
        self,
        call: ToolCall,
        initial: ToolDecision,
        final: ToolDecision,
        hooks: list[dict[str, str | int]],
        decision_kinds: set[str],
    ) -> None:
        if not self.audit_recorder:
            return
        kind = EventKinds.TOOL_DECISION_CONFLICT if len(decision_kinds) > 1 else EventKinds.TOOL_DECISION
        self.audit_recorder(
            kind,
            {
                ToolDecisionEventFields.ACTION: call.name,
                ToolDecisionEventFields.INITIAL_DECISION: initial.kind,
                ToolDecisionEventFields.FINAL_DECISION: final.kind,
                ToolDecisionEventFields.REASON: final.reason,
                ToolDecisionEventFields.HOOKS: hooks,
                ToolDecisionEventFields.PAYLOAD: redact_text(call.payload),
            },
        )

    def _confirmation(self, call: ToolCall, decision: ToolDecision) -> ToolDecision:
        return self.permission_policy.confirm(call.name, call.payload, decision)

    def _execution(self, call: ToolCall) -> CommandResult:
        spec = self.registry.require(call.name)
        total_attempts = max(0, spec.retry_count) + 1
        result: CommandResult | None = None
        for attempt in range(1, total_attempts + 1):
            result = self.registry.execute(call.name, call.payload)
            result.metadata.setdefault("attempts", attempt)
            if result.exit_code == 0 or result.skipped:
                return result
        assert result is not None
        result.metadata.setdefault("attempts", total_attempts)
        return result

    def _post_hook(self, call: ToolCall, result: CommandResult) -> None:
        for hook in self.hooks.post_hooks:
            hook(call, result)

    def _auto_verify(self, call: ToolCall, result: CommandResult) -> None:
        spec = self.registry.require(call.name)
        if result.output is None:
            raise ValueError(f"Tool {call.name} returned an invalid result.")
        output_errors = spec.validate_output(result) if result.exit_code == 0 and not result.skipped else []
        if output_errors:
            result.exit_code = 2
            result.skipped = True
            result.output = (
                f"Tool output validation failed for {call.name}:\n"
                + "\n".join(f"- {error}" for error in output_errors)
                + f"\noriginal_output:\n{result.output}"
            )

    def _formatting(self, result: CommandResult) -> CommandResult:
        return result

    def _record_execution_event(self, call: ToolCall, spec, result: CommandResult) -> None:
        if not self.audit_recorder:
            return
        status = "skipped" if result.skipped else "success" if result.exit_code == 0 else "failed"
        self.audit_recorder(
            EventKinds.TOOL_EXECUTION,
            {
                ToolExecutionEventFields.ACTION: call.name,
                ToolExecutionEventFields.EXIT_CODE: result.exit_code,
                ToolExecutionEventFields.STATUS: status,
                ToolExecutionEventFields.ATTEMPTS: result.metadata.get("attempts", 1),
                ToolExecutionEventFields.RISK: spec.risk_level,
                ToolExecutionEventFields.OUTPUT_SCHEMA: bool(spec.output_schema),
                ToolExecutionEventFields.METADATA: result.metadata,
                ToolExecutionEventFields.PAYLOAD: redact_text(call.payload),
            },
        )


def sorted_decision_hooks(hooks: list[DecisionHook | DecisionHookSpec]) -> list[DecisionHookSpec]:
    normalized: list[DecisionHookSpec] = []
    for index, hook in enumerate(hooks):
        if isinstance(hook, DecisionHookSpec):
            normalized.append(hook)
            continue
        hook_name = getattr(hook, "__name__", f"decision_hook_{index}")
        normalized.append(DecisionHookSpec(hook=hook, name=str(hook_name), priority=100))
    return sorted(normalized, key=lambda spec: (spec.priority, spec.name))
