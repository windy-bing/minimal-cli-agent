from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from minimal_cli_agent.types import CommandResult, ToolValidationError

ToolHandler = Callable[[str], CommandResult]
ToolValidator = Callable[[str], ToolValidationError | None]


def non_empty_payload_validator(tool_name: str, expected_format: str) -> ToolValidator:
    def validate(payload: str) -> ToolValidationError | None:
        if payload.strip():
            return None
        return ToolValidationError(
            tool_name=tool_name,
            message="payload must not be empty",
            expected_format=expected_format,
            received=payload,
        )

    return validate


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: ToolHandler
    expected_format: str = "non-empty text payload"
    aliases: tuple[str, ...] = field(default_factory=tuple)
    validator: ToolValidator | None = None

    def validate(self, payload: str) -> ToolValidationError | None:
        validator = self.validator or non_empty_payload_validator(self.name, self.expected_format)
        return validator(payload)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = spec.name

    def require(self, name: str) -> ToolSpec:
        canonical_name = self.resolve_name(name)
        if canonical_name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[canonical_name]

    def resolve_name(self, name: str) -> str:
        return self._aliases.get(name, name)

    def available_names(self) -> tuple[str, ...]:
        return tuple(sorted([*self._tools.keys(), *self._aliases.keys()]))

    def execute(self, name: str, payload: str) -> CommandResult:
        canonical_name = self.resolve_name(name)
        if canonical_name not in self._tools:
            return CommandResult(command=payload, exit_code=127, output=f"Unknown tool: {name}")
        return self._tools[canonical_name].handler(payload)

    def descriptions(self) -> str:
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self._tools.values())
