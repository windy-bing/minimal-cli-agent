from __future__ import annotations

import json
import re

from minimal_cli_agent.constants import ToolPayloadFields, Tools
from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.prompts import FORMAT_REMINDER
from minimal_cli_agent.types import ToolCall

BASH_ACTION_RE = re.compile(r"```bash-action\s*\n(.*?)\n```", re.DOTALL)
TOOL_ACTION_RE = re.compile(r"```tool-action\s*\n(.*?)\n```", re.DOTALL)
ACTION_BLOCK_RE = re.compile(r"```(?P<kind>bash-action|tool-action)\s*\n(?P<body>.*?)\n```", re.DOTALL)


def parse_action(model_output: str) -> ToolCall:
    calls = parse_actions(model_output)
    if len(calls) != 1:
        raise FormatError(format_error(f"expected exactly one action block for parse_action, found {len(calls)}"))
    return calls[0]


def parse_actions(model_output: str) -> list[ToolCall]:
    matches = list(ACTION_BLOCK_RE.finditer(model_output))
    if not matches:
        raise FormatError(format_error("no bash-action or tool-action code block found"))

    calls: list[ToolCall] = []
    for match in matches:
        kind = match.group("kind")
        body = match.group("body")
        if kind == "bash-action":
            calls.append(parse_bash_action(body))
        else:
            calls.append(parse_tool_action(body))
    validate_action_sequence(calls)
    return calls


def parse_bash_action(raw_action: str) -> ToolCall:
    command = raw_action.strip()
    if command == "exit":
        return ToolCall(name=Tools.SHELL, payload="exit")
    if not command:
        raise FormatError(format_error("bash-action block is empty"))
    return ToolCall(name=Tools.SHELL, payload=command)


def parse_tool_action(raw_action: str) -> ToolCall:
    try:
        payload = json.loads(raw_action.strip())
    except json.JSONDecodeError as exc:
        raise FormatError(format_error(f"tool-action JSON is invalid: {exc.msg} at line {exc.lineno} column {exc.colno}")) from exc

    if not isinstance(payload, dict):
        raise FormatError(format_error("tool-action JSON must be an object"))
    tool_name = payload.pop(ToolPayloadFields.TOOL, None)
    if tool_name == "exit":
        raise AgentFinished("Model requested exit.")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise FormatError(format_error('tool-action must include a non-empty string field named "tool"'))
    return ToolCall(name=tool_name.strip(), payload=json.dumps(payload, ensure_ascii=False))


def validate_action_sequence(calls: list[ToolCall]) -> None:
    exit_calls = [call for call in calls if call.name == Tools.SHELL and call.payload.strip() == "exit"]
    if not exit_calls:
        return
    if len(calls) != 1:
        raise FormatError(format_error("exit must be the only action block in the response"))
    raise AgentFinished("Model requested exit.")


def format_error(detail: str) -> str:
    return f"{FORMAT_REMINDER}\nProblem:\n{detail}"
