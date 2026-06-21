from __future__ import annotations

import re

from minimal_cli_agent.exceptions import AgentFinished, FormatError
from minimal_cli_agent.prompts import FORMAT_REMINDER

ACTION_RE = re.compile(r"```bash-action\s*\n(.*?)\n```", re.DOTALL)


def parse_action(model_output: str) -> str:
    matches = ACTION_RE.findall(model_output)
    if len(matches) != 1:
        raise FormatError(FORMAT_REMINDER)

    command = matches[0].strip()
    if command == "exit":
        raise AgentFinished("Model requested exit.")
    if not command:
        raise FormatError(FORMAT_REMINDER)
    return command

