from __future__ import annotations

import difflib
import json
import re
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
    output_schema: dict[str, Any] | None = None
    risk_level: str = "low"
    retry_count: int = 0

    def validate(self, payload: str) -> ToolValidationError | None:
        if self.parameters_schema is not None:
            schema_error = validate_payload_schema(self.name, payload, self.expected_format, self.parameters_schema)
            if schema_error is not None:
                return schema_error
        validator = self.validator or non_empty_payload_validator(self.name, self.expected_format)
        return validator(payload)

    def prepare_payload(self, payload: str) -> str:
        if self.parameters_schema is None:
            return payload
        return apply_schema_defaults_to_payload(payload, self.parameters_schema)

    def schema_documentation(self) -> str:
        if self.parameters_schema is None:
            return self.expected_format
        return describe_schema(self.parameters_schema)

    def validate_output(self, result: CommandResult) -> list[str]:
        if self.output_schema is None:
            return []
        data: Any
        if result.metadata:
            data = result.metadata
        else:
            try:
                data = json.loads(result.output)
            except json.JSONDecodeError:
                data = result.output
        return validate_object_schema(data, self.output_schema)


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
        return "\n".join(
            f"- {spec.name}: {spec.description} Risk: {spec.risk_level}. Parameters: {spec.schema_documentation()}"
            for spec in self._tools.values()
        )


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


def apply_schema_defaults_to_payload(payload: str, schema: dict[str, Any]) -> str:
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return payload
    prepared = apply_schema_defaults(data, schema)
    if prepared == data:
        return payload
    return json.dumps(prepared, ensure_ascii=False)


def apply_schema_defaults(value: Any, schema: dict[str, Any]) -> Any:
    if not isinstance(schema, dict):
        return value
    if schema.get("type") == "object" and isinstance(value, dict):
        prepared = dict(value)
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    continue
                if field_name not in prepared and "default" in field_schema:
                    prepared[field_name] = field_schema["default"]
                elif field_name in prepared:
                    prepared[field_name] = apply_schema_defaults(prepared[field_name], field_schema)
        return prepared
    if schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [apply_schema_defaults(item, item_schema) for item in value]
    return value


def describe_schema(schema: dict[str, Any]) -> str:
    if schema.get("type") != "object":
        return json.dumps(schema, ensure_ascii=False, sort_keys=True)
    required = set(schema.get("required", [])) if isinstance(schema.get("required"), list) else set()
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return "{}"
    fields = []
    for field_name, rules in properties.items():
        if not isinstance(rules, dict):
            continue
        parts = [str(rules.get("type", "any"))]
        if field_name in required:
            parts.append("required")
        if "default" in rules:
            parts.append(f"default={json.dumps(rules['default'], ensure_ascii=False)}")
        if isinstance(rules.get("enum"), list):
            parts.append("enum=" + "|".join(json.dumps(item, ensure_ascii=False) for item in rules["enum"]))
        bounds = describe_bounds(rules)
        if bounds:
            parts.append(bounds)
        fields.append(f"{field_name}({', '.join(parts)})")
    return "{" + ", ".join(fields) + "}"


def validate_object_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    return validate_schema_value("payload", data, schema, root=True, root_schema=schema)


def validate_schema_value(
    path: str,
    value: Any,
    schema: dict[str, Any],
    root: bool = False,
    root_schema: dict[str, Any] | None = None,
) -> list[str]:
    root_schema = root_schema or schema
    errors: list[str] = []

    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = resolve_local_schema_ref(ref, root_schema)
        if resolved is None:
            return [f"{path}: unresolved schema reference {ref}"]
        return validate_schema_value(path, value, resolved, root=root, root_schema=root_schema)

    if "const" in schema and value != schema["const"]:
        return [f"{path}: expected constant {json.dumps(schema['const'])}"]

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        allowed = ", ".join(json.dumps(item) for item in enum_values)
        return [f"{path}: expected one of {allowed}"]

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for candidate in all_of:
            if isinstance(candidate, dict):
                errors.extend(validate_schema_value(path, value, candidate, root, root_schema))
        if errors:
            return errors

    not_schema = schema.get("not")
    if isinstance(not_schema, dict) and not validate_schema_value(path, value, not_schema, root, root_schema):
        return [f"{path}: must not match forbidden schema"]

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = [
            candidate
            for candidate in one_of
            if isinstance(candidate, dict) and not validate_schema_value(path, value, candidate, root, root_schema)
        ]
        if len(matches) != 1:
            return [f"{path}: expected exactly one oneOf schema to match"]
        return []

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        if not any(
            isinstance(candidate, dict) and not validate_schema_value(path, value, candidate, root, root_schema)
            for candidate in any_of
        ):
            return [f"{path}: expected at least one anyOf schema to match"]
        return []

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        expected_types = [item for item in expected_type if isinstance(item, str)]
        if not any(matches_json_type(value, item) for item in expected_types):
            return [f"{path}: expected {' or '.join(expected_types)}"]
        expected_type = next((item for item in expected_types if matches_json_type(value, item)), None)
    elif expected_type is None:
        expected_type = infer_applicable_schema_type(value, schema)

    if expected_type == "object":
        if not isinstance(value, dict):
            return ["payload must be a JSON object"] if root else [f"{path}: expected object"]

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        required = schema.get("required", [])
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            errors.append(f"{path}: must contain >= {min_properties} properties")
        if isinstance(max_properties, int) and len(value) > max_properties:
            errors.append(f"{path}: must contain <= {max_properties} properties")
        if isinstance(required, list):
            for field_name in required:
                if field_name not in value or value[field_name] is None:
                    errors.append(f"{join_schema_path(path, str(field_name), root)}: missing required field")

        if isinstance(properties, dict):
            for field_name, rules in properties.items():
                if field_name not in value or not isinstance(rules, dict):
                    continue
                errors.extend(validate_schema_value(join_schema_path(path, field_name, root), value[field_name], rules, root_schema=root_schema))

        if isinstance(pattern_properties, dict):
            for pattern, rules in pattern_properties.items():
                if not isinstance(pattern, str) or not isinstance(rules, dict):
                    continue
                try:
                    regex = re.compile(pattern)
                except re.error:
                    errors.append(f"{path}: schema patternProperties key is invalid")
                    continue
                for field_name, field_value in value.items():
                    if regex.search(str(field_name)):
                        errors.extend(validate_schema_value(join_schema_path(path, str(field_name), root), field_value, rules, root_schema=root_schema))

        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for field_name in value:
                errors.extend(validate_schema_value(join_schema_path(path, str(field_name), root), str(field_name), property_names, root_schema=root_schema))

        dependent_required = schema.get("dependentRequired")
        if isinstance(dependent_required, dict):
            for field_name, dependencies in dependent_required.items():
                if field_name not in value or not isinstance(dependencies, list):
                    continue
                for dependency in dependencies:
                    if isinstance(dependency, str) and dependency not in value:
                        errors.append(f"{join_schema_path(path, dependency, root)}: required when {field_name} is present")

        additional = schema.get("additionalProperties", True)
        known = set(properties.keys()) if isinstance(properties, dict) else set()
        pattern_matched = fields_matching_pattern_properties(value, pattern_properties)
        if additional is False and isinstance(properties, dict):
            for field_name in value:
                if field_name not in known and field_name not in pattern_matched:
                    errors.append(f"{join_schema_path(path, str(field_name), root)}: unexpected field")
        elif isinstance(additional, dict):
            for field_name, field_value in value.items():
                if field_name not in known and field_name not in pattern_matched:
                    errors.extend(validate_schema_value(join_schema_path(path, str(field_name), root), field_value, additional, root_schema=root_schema))
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
        if schema.get("uniqueItems") is True and not has_unique_json_items(value):
            errors.append(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            item_type = item_schema.get("type")
            if item_type == "string" and any(not isinstance(item, str) for item in value):
                errors.append(f"{path}: expected array of strings")
            else:
                for index, item in enumerate(value):
                    errors.extend(validate_schema_value(f"{path}[{index}]", item, item_schema, root_schema=root_schema))
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, item_schema in enumerate(prefix_items[: len(value)]):
                if isinstance(item_schema, dict):
                    errors.extend(validate_schema_value(f"{path}[{index}]", value[index], item_schema, root_schema=root_schema))
        contains = schema.get("contains")
        if isinstance(contains, dict):
            matches = [item for item in value if not validate_schema_value(path, item, contains, root_schema=root_schema)]
            min_contains = schema.get("minContains", 1)
            max_contains = schema.get("maxContains")
            if isinstance(min_contains, int) and len(matches) < min_contains:
                errors.append(f"{path}: must contain at least {min_contains} matching items")
            if isinstance(max_contains, int) and len(matches) > max_contains:
                errors.append(f"{path}: must contain at most {max_contains} matching items")
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
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{path}: must match pattern {pattern}")
            except re.error:
                errors.append(f"{path}: schema pattern is invalid")
        string_format = schema.get("format")
        if isinstance(string_format, str):
            format_error = validate_string_format(path, value, string_format)
            if format_error:
                errors.append(format_error)
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
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(f"{path}: must be >= {minimum}")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(f"{path}: must be <= {maximum}")
    if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
        errors.append(f"{path}: must be > {exclusive_minimum}")
    if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
        errors.append(f"{path}: must be < {exclusive_maximum}")
    multiple_of = schema.get("multipleOf")
    if isinstance(multiple_of, (int, float)) and multiple_of != 0:
        quotient = value / multiple_of
        if abs(quotient - round(quotient)) > 1e-9:
            errors.append(f"{path}: must be a multiple of {multiple_of}")
    return errors


def resolve_local_schema_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = root_schema
    for part in ref.removeprefix("#/").split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def infer_applicable_schema_type(value: Any, schema: dict[str, Any]) -> str | None:
    if isinstance(value, dict) and any(key in schema for key in ("properties", "required", "additionalProperties", "patternProperties", "propertyNames")):
        return "object"
    if isinstance(value, list) and any(key in schema for key in ("items", "prefixItems", "contains", "minItems", "maxItems", "uniqueItems")):
        return "array"
    if isinstance(value, str) and any(key in schema for key in ("minLength", "maxLength", "pattern", "format")):
        return "string"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and any(
        key in schema for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")
    ):
        return "integer" if isinstance(value, int) else "number"
    return None


def fields_matching_pattern_properties(value: dict[str, Any], pattern_properties: Any) -> set[str]:
    matched: set[str] = set()
    if not isinstance(pattern_properties, dict):
        return matched
    for pattern in pattern_properties:
        if not isinstance(pattern, str):
            continue
        try:
            regex = re.compile(pattern)
        except re.error:
            continue
        for field_name in value:
            if regex.search(str(field_name)):
                matched.add(str(field_name))
    return matched


def validate_string_format(path: str, value: str, string_format: str) -> str | None:
    if string_format == "email" and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is None:
        return f"{path}: must be a valid email"
    if string_format == "uri" and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", value) is None:
        return f"{path}: must be a valid uri"
    if string_format == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return f"{path}: must be a valid date"
    if string_format == "date-time" and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\S+", value) is None:
        return f"{path}: must be a valid date-time"
    return None


def has_unique_json_items(values: list[Any]) -> bool:
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if marker in seen:
            return False
        seen.add(marker)
    return True


def describe_bounds(schema: dict[str, Any]) -> str:
    bounds = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if isinstance(minimum, (int, float)):
        bounds.append(f">={minimum}")
    if isinstance(maximum, (int, float)):
        bounds.append(f"<={maximum}")
    if isinstance(min_length, int):
        bounds.append(f"len>={min_length}")
    if isinstance(max_length, int):
        bounds.append(f"len<={max_length}")
    if isinstance(min_items, int):
        bounds.append(f"items>={min_items}")
    if isinstance(max_items, int):
        bounds.append(f"items<={max_items}")
    return " ".join(bounds)


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
