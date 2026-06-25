from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minimal_cli_agent.constants import ToolPayloadFields, Tools
from minimal_cli_agent.types import AgentConfig, CommandResult, ToolValidationError


class FileToolEnvironment:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def read_file(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.READ_FILE} {payload}", exit_code=1, output=str(exc))
        if not path.exists():
            return CommandResult(command=f"{Tools.READ_FILE} {path}", exit_code=1, output="file does not exist")
        if not path.is_file():
            return CommandResult(command=f"{Tools.READ_FILE} {path}", exit_code=1, output="path is not a file")

        content = path.read_text(encoding="utf-8", errors="replace")
        output = content[-self.config.max_output_chars :]
        return CommandResult(command=f"{Tools.READ_FILE} {path}", exit_code=0, output=output)

    def write_file(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.WRITE_FILE} {payload}", exit_code=1, output=str(exc))
        content = str(data[ToolPayloadFields.CONTENT])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        relative = path.relative_to(self.config.cwd.resolve())
        return CommandResult(
            command=f"{Tools.WRITE_FILE} {relative}",
            exit_code=0,
            output=f"Wrote {relative} ({len(content)} chars).",
        )


def read_file_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.READ_FILE,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH,),
        expected_format=Tools.READ_FILE_EXPECTED_FORMAT,
    )


def write_file_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.WRITE_FILE,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH, ToolPayloadFields.CONTENT),
        expected_format=Tools.WRITE_FILE_EXPECTED_FORMAT,
    )


def validate_json_fields(
    tool_name: str,
    payload: str,
    required_fields: tuple[str, ...],
    expected_format: str,
) -> ToolValidationError | None:
    try:
        data = parse_payload(payload)
    except ValueError as exc:
        return ToolValidationError(tool_name=tool_name, message=str(exc), expected_format=expected_format, received=payload)

    for field in required_fields:
        if field not in data or data[field] is None:
            return ToolValidationError(
                tool_name=tool_name,
                message=f"missing required field: {field}",
                expected_format=expected_format,
                received=payload,
            )
    if not str(data[ToolPayloadFields.PATH]).strip():
        return ToolValidationError(
            tool_name=tool_name,
            message="path must not be empty",
            expected_format=expected_format,
            received=payload,
        )
    return None


def parse_payload(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("payload must be a JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def resolve_workspace_path(cwd: Path, raw_path: str) -> Path:
    requested = Path(raw_path)
    if requested.is_absolute():
        candidate = requested.resolve()
    else:
        candidate = (cwd / requested).resolve()
    workspace = cwd.resolve()
    if not candidate.is_relative_to(workspace):
        raise ValueError(f"path escapes workspace: {raw_path}")
    return candidate
