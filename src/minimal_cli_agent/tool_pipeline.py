from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

from minimal_cli_agent.interfaces import PermissionPolicy
from minimal_cli_agent.tool_registry import ToolRegistry
from minimal_cli_agent.exceptions import PermissionDenied
from minimal_cli_agent.constants import EventKinds, SandboxAttemptEventFields, ToolDecisionEventFields, ToolDecisionKinds, ToolExecutionEventFields
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
        sandbox_context: dict[str, object] | None = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy
        self.hooks = hooks or ToolPipelineHooks()
        self.audit_recorder = audit_recorder
        self.sandbox_context = sandbox_context or {}

    def execute(self, call: ToolCall) -> CommandResult:
        try:
            spec = self._discovery(call)
        except KeyError:
            discovery_error = ToolDiscoveryError(
                tool_name=call.name,
                available_tools=self.registry.available_names(),
                suggested_tools=self.registry.suggested_names(call.name),
            )
            result = CommandResult(command=call.payload, exit_code=127, output=discovery_error.as_observation(), skipped=True)
            self._record_sandbox_attempt(call, phase="result", status="skipped", exit_code=result.exit_code, error_class="ToolDiscoveryError")
            return result
        canonical_call = ToolCall(name=spec.name, payload=call.payload, call_id=call.call_id)
        validation_error = self._validation(canonical_call)
        if validation_error is not None:
            result = CommandResult(command=call.payload, exit_code=2, output=validation_error.as_observation(), skipped=True)
            self._record_sandbox_attempt(canonical_call, phase="result", status="skipped", exit_code=result.exit_code, error_class="ToolValidationError")
            return result
        canonical_call = ToolCall(name=spec.name, payload=spec.prepare_payload(canonical_call.payload), call_id=call.call_id)
        decision = self._permission(canonical_call)
        self._record_sandbox_attempt(canonical_call, phase="permission", decision=decision.kind, status=decision.kind)
        self._pre_hook(canonical_call)
        decision = self._resolve_decision(canonical_call, decision)
        decision = self._confirmation(canonical_call, decision)
        self._record_sandbox_attempt(canonical_call, phase="permission_final", decision=decision.kind, status=decision.kind)
        if decision.kind == ToolDecisionKinds.SKIP:
            result = CommandResult(command=call.payload, exit_code=0, output=decision.reason or "tool skipped", skipped=True)
            self._record_sandbox_attempt(canonical_call, phase="result", decision=decision.kind, status="skipped", exit_code=result.exit_code)
            return result
        if decision.kind == ToolDecisionKinds.DENY:
            self._record_sandbox_attempt(canonical_call, phase="result", decision=decision.kind, status="denied", error_class="PermissionDenied")
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
            started = time.monotonic()
            result = self.registry.execute(call.name, call.payload)
            duration_ms = int((time.monotonic() - started) * 1000)
            result.metadata.setdefault("attempts", attempt)
            status = "skipped" if result.skipped else "success" if result.exit_code == 0 else "failed"
            self._record_sandbox_attempt(
                call,
                phase="attempt",
                attempt=attempt,
                status=status,
                exit_code=result.exit_code,
                duration_ms=duration_ms,
            )
            if result.exit_code == 0 or result.skipped:
                return result
            if not should_retry_result(result):
                return result
        assert result is not None
        result.metadata.setdefault("attempts", total_attempts)
        return result

    def _post_hook(self, call: ToolCall, result: CommandResult) -> None:
        for hook in self.hooks.post_hooks:
            hook(call, result)

    def _auto_verify(self, call: ToolCall, result: CommandResult) -> None:
        spec = self.registry.require(call.name)
        if result.exit_code == 0 and not result.skipped:
            output_errors = spec.validate_output(result)
        else:
            output_errors = []
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
                ToolExecutionEventFields.CALL_ID: call.call_id,
                ToolExecutionEventFields.EXIT_CODE: result.exit_code,
                ToolExecutionEventFields.STATUS: status,
                ToolExecutionEventFields.ATTEMPTS: result.metadata.get("attempts", 1),
                ToolExecutionEventFields.RISK: spec.risk_level,
                ToolExecutionEventFields.OUTPUT_SCHEMA: bool(spec.output_schema),
                ToolExecutionEventFields.METADATA: result.metadata,
                ToolExecutionEventFields.PAYLOAD: redact_text(call.payload),
            },
        )

    def _record_sandbox_attempt(
        self,
        call: ToolCall,
        phase: str,
        attempt: int = 1,
        status: str = "",
        decision: str | None = None,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        error_class: str | None = None,
    ) -> None:
        if not self.audit_recorder:
            return
        self.audit_recorder(
            EventKinds.SANDBOX_ATTEMPT,
            {
                SandboxAttemptEventFields.CALL_ID: call.call_id,
                SandboxAttemptEventFields.TOOL: call.name,
                SandboxAttemptEventFields.ATTEMPT: attempt,
                SandboxAttemptEventFields.PHASE: phase,
                SandboxAttemptEventFields.SANDBOX_KIND: self.sandbox_context.get("sandbox_kind"),
                SandboxAttemptEventFields.NETWORK: self.sandbox_context.get("network"),
                SandboxAttemptEventFields.PERMISSION_MODE: self.sandbox_context.get("permission_mode"),
                SandboxAttemptEventFields.DECISION: decision,
                SandboxAttemptEventFields.EXIT_CODE: exit_code,
                SandboxAttemptEventFields.DURATION_MS: duration_ms,
                SandboxAttemptEventFields.ERROR_CLASS: error_class,
                SandboxAttemptEventFields.STATUS: status,
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


def should_retry_result(result: CommandResult) -> bool:
    if result.metadata.get("retryable") is False:
        return False
    return result.exit_code not in {2, 126, 127}
