from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

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
    parameters_schema: dict[str, Any] | None = None

    def validate(self, payload: str) -> ToolValidationError | None:
        if self.parameters_schema is not None:
            schema_error = validate_payload_schema(self.name, payload, self.expected_format, self.parameters_schema)
            if schema_error is not None:
                return schema_error
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

    def suggested_names(self, name: str, limit: int = 3) -> tuple[str, ...]:
        return tuple(difflib.get_close_matches(name, self.available_names(), n=limit, cutoff=0.55))

    def execute(self, name: str, payload: str) -> CommandResult:
        canonical_name = self.resolve_name(name)
        if canonical_name not in self._tools:
            return CommandResult(command=payload, exit_code=127, output=f"Unknown tool: {name}")
        return self._tools[canonical_name].handler(payload)

    def descriptions(self) -> str:
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self._tools.values())


def validate_payload_schema(
    tool_name: str,
    payload: str,
    expected_format: str,
    schema: dict[str, Any],
) -> ToolValidationError | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return ToolValidationError(
            tool_name=tool_name,
            message="payload must be valid JSON",
            expected_format=expected_format,
            received=payload,
            field_errors=(str(exc),),
        )

    errors = validate_object_schema(data, schema)
    if not errors:
        return None
    return ToolValidationError(
        tool_name=tool_name,
        message="payload does not match tool parameter schema",
        expected_format=expected_format,
        received=payload,
        field_errors=tuple(errors),
    )


def validate_object_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    if schema.get("type") != "object":
        return []
    if not isinstance(data, dict):
        return ["payload must be a JSON object"]

    errors: list[str] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for field_name in required:
            if field_name not in data or data[field_name] is None:
                errors.append(f"{field_name}: missing required field")

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for field_name, rules in properties.items():
            if field_name not in data or not isinstance(rules, dict):
                continue
            errors.extend(validate_field(field_name, data[field_name], rules))
    return errors


def validate_field(field_name: str, value: Any, rules: dict[str, Any]) -> list[str]:
    expected_type = rules.get("type")
    errors: list[str] = []
    if expected_type == "string" and not isinstance(value, str):
        errors.append(f"{field_name}: expected string")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{field_name}: expected integer")
        else:
            minimum = rules.get("minimum")
            maximum = rules.get("maximum")
            if isinstance(minimum, int) and value < minimum:
                errors.append(f"{field_name}: must be >= {minimum}")
            if isinstance(maximum, int) and value > maximum:
                errors.append(f"{field_name}: must be <= {maximum}")
    elif expected_type == "array":
        if not isinstance(value, list):
            errors.append(f"{field_name}: expected array")
        else:
            item_type = rules.get("items", {}).get("type") if isinstance(rules.get("items"), dict) else None
            if item_type == "string" and not all(isinstance(item, str) for item in value):
                errors.append(f"{field_name}: expected array of strings")
    return errors
