from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, cast

from minimal_cli_agent.constants import ToolPayloadFields, Tools
from minimal_cli_agent.types import CommandResult, ToolCall


@dataclass(frozen=True)
class SkippedToolCall:
    call: ToolCall
    result: CommandResult


@dataclass
class ReadForwardPageState:
    payload: dict[str, Any]
    next_offset: int | None = None
    next_line_offset: int | None = None


class ToolCallLedger:
    def __init__(self, max_tool_calls: int = 50, max_read_only_tool_calls: int = 20) -> None:
        self.max_tool_calls = max(1, max_tool_calls)
        self.max_read_only_tool_calls = max(1, max_read_only_tool_calls)
        self.tool_calls_seen = 0
        self.read_only_tool_calls_seen = 0
        self.successful_read_keys: dict[tuple[str, str], CommandResult] = {}
        self.read_forward_pages: dict[str, ReadForwardPageState] = {}

    def filter_before_execution(self, calls: list[ToolCall]) -> tuple[list[ToolCall], list[SkippedToolCall]]:
        allowed: list[ToolCall] = []
        skipped: list[SkippedToolCall] = []
        for call in calls:
            skip = self.skip_reason(call)
            if skip is None:
                allowed.append(call)
            else:
                skipped.append(SkippedToolCall(call=call, result=skip))
        return allowed, skipped

    def skip_reason(self, call: ToolCall) -> CommandResult | None:
        if self.tool_calls_seen >= self.max_tool_calls:
            return budget_exceeded_result(
                call,
                budget_name="max_tool_calls_per_turn",
                limit=self.max_tool_calls,
                message="Tool call budget reached for this turn.",
            )
        self.tool_calls_seen += 1
        if call.name not in Tools.READ_ONLY:
            return None
        if self.read_only_tool_calls_seen >= self.max_read_only_tool_calls:
            return budget_exceeded_result(
                call,
                budget_name="max_read_only_tool_calls_per_turn",
                limit=self.max_read_only_tool_calls,
                message="Read-only tool call budget reached for this turn.",
            )
        self.read_only_tool_calls_seen += 1
        if call.name == Tools.READ_FORWARD:
            result = self.skip_repeated_read_forward(call)
            if result is not None:
                return result
        key = (call.name, canonical_payload(call.payload))
        if key not in self.successful_read_keys:
            return None
        return CommandResult(
            command=f"{call.name} {call.payload}",
            exit_code=0,
            output=(
                "Repeated read-only tool call skipped. The same tool call already succeeded in this turn; "
                "use the prior observation or request a different path, query, or range."
            ),
            skipped=True,
            metadata={"repeated_tool_call": True},
        )

    def skip_repeated_read_forward(self, call: ToolCall) -> CommandResult | None:
        payload = parse_json_payload(call.payload)
        if payload is None:
            return None
        path = str(payload.get(ToolPayloadFields.PATH, ""))
        if not path:
            return None
        state = self.read_forward_pages.get(path)
        if state is None:
            return None
        mode = str(payload.get(ToolPayloadFields.MODE, "bytes")).lower()
        if mode == "lines":
            line_offset = int_or_default(payload.get(ToolPayloadFields.LINE_OFFSET), 0)
            previous_next = state.next_line_offset
            if previous_next is not None and line_offset < previous_next:
                return CommandResult(
                    command=f"{call.name} {call.payload}",
                    exit_code=0,
                    output=(
                        f"Repeated read_forward range skipped for {path}. "
                        f"The next unread line_offset is {previous_next}; use that value to continue paging."
                    ),
                    skipped=True,
                    metadata={"repeated_tool_call": True, "next_line_offset": previous_next},
                )
            return None
        offset = int_or_default(payload.get(ToolPayloadFields.OFFSET), 0)
        previous_next = state.next_offset
        if previous_next is not None and offset < previous_next:
            return CommandResult(
                command=f"{call.name} {call.payload}",
                exit_code=0,
                output=(
                    f"Repeated read_forward range skipped for {path}. "
                    f"The next unread offset is {previous_next}; use that value to continue paging."
                ),
                skipped=True,
                metadata={"repeated_tool_call": True, "next_offset": previous_next},
            )
        return None

    def record_result(self, call: ToolCall, result: CommandResult) -> None:
        if call.name not in Tools.READ_ONLY or result.exit_code != 0 or result.skipped:
            return
        self.successful_read_keys[(call.name, canonical_payload(call.payload))] = result
        if call.name != Tools.READ_FORWARD:
            return
        payload = parse_json_payload(call.payload)
        if payload is None:
            return
        path = str(payload.get(ToolPayloadFields.PATH, ""))
        if not path:
            return
        state = self.read_forward_pages.setdefault(path, ReadForwardPageState(payload=payload))
        if "next_offset" in result.metadata:
            state.next_offset = int_or_default(result.metadata.get("next_offset"), 0)
        if "next_line_offset" in result.metadata:
            state.next_line_offset = int_or_default(result.metadata.get("next_line_offset"), 0)


def canonical_payload(payload: str) -> str:
    parsed = parse_json_payload(payload)
    if parsed is None:
        return payload
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_payload(payload: str) -> dict[str, Any] | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def int_or_default(value: object, default: int) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def budget_exceeded_result(call: ToolCall, budget_name: str, limit: int, message: str) -> CommandResult:
    return CommandResult(
        command=f"{call.name} {call.payload}",
        exit_code=0,
        output=(
            f"{message} {budget_name}={limit}. "
            "Summarize the evidence already gathered, narrow the next request, or ask the user before continuing."
        ),
        skipped=True,
        metadata={"tool_budget_exceeded": True, "budget": budget_name, "limit": limit},
    )
