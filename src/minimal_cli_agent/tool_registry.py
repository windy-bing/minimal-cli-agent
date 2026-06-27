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
    return validate_schema_value("payload", data, schema, root=True)


def validate_schema_value(path: str, value: Any, schema: dict[str, Any], root: bool = False) -> list[str]:
    errors: list[str] = []

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        allowed = ", ".join(json.dumps(item) for item in enum_values)
        return [f"{path}: expected one of {allowed}"]

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = [candidate for candidate in one_of if isinstance(candidate, dict) and not validate_schema_value(path, value, candidate, root)]
        if len(matches) != 1:
            return [f"{path}: expected exactly one oneOf schema to match"]
        return []

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        if not any(isinstance(candidate, dict) and not validate_schema_value(path, value, candidate, root) for candidate in any_of):
            return [f"{path}: expected at least one anyOf schema to match"]
        return []

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        expected_types = [item for item in expected_type if isinstance(item, str)]
        if not any(matches_json_type(value, item) for item in expected_types):
            return [f"{path}: expected {' or '.join(expected_types)}"]
        return []

    if expected_type == "object":
        if not isinstance(value, dict):
            return ["payload must be a JSON object"] if root else [f"{path}: expected object"]

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if isinstance(required, list):
            for field_name in required:
                if field_name not in value or value[field_name] is None:
                    errors.append(f"{join_schema_path(path, str(field_name), root)}: missing required field")

        if isinstance(properties, dict):
            for field_name, rules in properties.items():
                if field_name not in value or not isinstance(rules, dict):
                    continue
                errors.extend(validate_schema_value(join_schema_path(path, field_name, root), value[field_name], rules))

        additional = schema.get("additionalProperties", True)
        if additional is False and isinstance(properties, dict):
            for field_name in value:
                if field_name not in properties:
                    errors.append(f"{join_schema_path(path, str(field_name), root)}: unexpected field")
        elif isinstance(additional, dict):
            known = set(properties.keys()) if isinstance(properties, dict) else set()
            for field_name, field_value in value.items():
                if field_name not in known:
                    errors.extend(validate_schema_value(join_schema_path(path, str(field_name), root), field_value, additional))
        return errors

    if expected_type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: must contain >= {min_items} items")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: must contain <= {max_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            item_type = item_schema.get("type")
            if item_type == "string" and any(not isinstance(item, str) for item in value):
                errors.append(f"{path}: expected array of strings")
            else:
                for index, item in enumerate(value):
                    errors.extend(validate_schema_value(f"{path}[{index}]", item, item_schema))
        return errors

    if expected_type == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string"]
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: length must be >= {min_length}")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: length must be <= {max_length}")
        return errors

    if expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{path}: expected integer"]
        errors.extend(validate_number_bounds(path, value, schema))
        return errors

    if expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return [f"{path}: expected number"]
        errors.extend(validate_number_bounds(path, float(value), schema))
        return errors

    if expected_type == "boolean" and not isinstance(value, bool):
        return [f"{path}: expected boolean"]

    if expected_type == "null" and value is not None:
        return [f"{path}: expected null"]

    return errors


def validate_number_bounds(path: str, value: int | float, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(f"{path}: must be >= {minimum}")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(f"{path}: must be <= {maximum}")
    return errors


def matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def join_schema_path(parent: str, field_name: str, root: bool) -> str:
    if root:
        return field_name
    return f"{parent}.{field_name}"
