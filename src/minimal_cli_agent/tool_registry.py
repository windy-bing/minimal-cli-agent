from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from minimal_cli_agent.types import CommandResult

ToolHandler = Callable[[str], CommandResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def require(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def execute(self, name: str, payload: str) -> CommandResult:
        if name not in self._tools:
            return CommandResult(command=payload, exit_code=127, output=f"Unknown tool: {name}")
        return self._tools[name].handler(payload)

    def descriptions(self) -> str:
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self._tools.values())
