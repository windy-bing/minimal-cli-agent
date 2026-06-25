from __future__ import annotations

import json
import re

from minimal_cli_agent.constants import ToolPayloadFields, Tools
from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.prompts import FORMAT_REMINDER
from minimal_cli_agent.types import ToolCall

BASH_ACTION_RE = re.compile(r"```bash-action\s*\n(.*?)\n```", re.DOTALL)
TOOL_ACTION_RE = re.compile(r"```tool-action\s*\n(.*?)\n```", re.DOTALL)


def parse_action(model_output: str) -> ToolCall:
    bash_matches = BASH_ACTION_RE.findall(model_output)
    tool_matches = TOOL_ACTION_RE.findall(model_output)
    if len(bash_matches) + len(tool_matches) != 1:
        raise FormatError(FORMAT_REMINDER)

    if bash_matches:
        return parse_bash_action(bash_matches[0])
    return parse_tool_action(tool_matches[0])


def parse_bash_action(raw_action: str) -> ToolCall:
    command = raw_action.strip()
    if command == "exit":
        raise AgentFinished("Model requested exit.")
    if not command:
        raise FormatError(FORMAT_REMINDER)
    return ToolCall(name=Tools.SHELL, payload=command)


def parse_tool_action(raw_action: str) -> ToolCall:
    try:
        payload = json.loads(raw_action.strip())
    except json.JSONDecodeError as exc:
        raise FormatError(FORMAT_REMINDER) from exc

    if not isinstance(payload, dict):
        raise FormatError(FORMAT_REMINDER)
    tool_name = payload.pop(ToolPayloadFields.TOOL, None)
    if tool_name == "exit":
        raise AgentFinished("Model requested exit.")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise FormatError(FORMAT_REMINDER)
    return ToolCall(name=tool_name.strip(), payload=json.dumps(payload, ensure_ascii=False))
