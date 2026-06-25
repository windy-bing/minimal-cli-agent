from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from minimal_cli_agent.constants import FileToolDefaults, ToolPayloadFields, Tools
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

    def read_tail(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.READ_TAIL} {payload}", exit_code=1, output=str(exc))
        if not path.exists():
            return CommandResult(command=f"{Tools.READ_TAIL} {path}", exit_code=1, output="file does not exist")
        if not path.is_file():
            return CommandResult(command=f"{Tools.READ_TAIL} {path}", exit_code=1, output="path is not a file")

        lines = read_positive_int(
            data,
            ToolPayloadFields.LINES,
            default=FileToolDefaults.TAIL_LINES,
            minimum=1,
            maximum=FileToolDefaults.TAIL_MAX_LINES,
        )
        max_bytes = read_positive_int(
            data,
            ToolPayloadFields.MAX_BYTES,
            default=FileToolDefaults.TAIL_MAX_BYTES,
            minimum=1,
            maximum=self.config.max_output_chars,
        )
        output = tail_text(path, lines=lines, max_bytes=max_bytes)
        return CommandResult(command=f"{Tools.READ_TAIL} {path}", exit_code=0, output=output[-self.config.max_output_chars :])

    def read_forward(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.READ_FORWARD} {payload}", exit_code=1, output=str(exc))
        if not path.exists():
            return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=1, output="file does not exist")
        if not path.is_file():
            return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=1, output="path is not a file")

        offset = read_positive_int(data, ToolPayloadFields.OFFSET, default=0, minimum=0, maximum=max(path.stat().st_size, 0))
        limit = read_positive_int(
            data,
            ToolPayloadFields.LIMIT,
            default=FileToolDefaults.FORWARD_LIMIT,
            minimum=1,
            maximum=self.config.max_output_chars,
        )
        with path.open("rb") as file:
            file.seek(offset)
            chunk = file.read(limit)
        output = chunk.decode("utf-8", errors="replace")
        return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=0, output=output)

    def search(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            root = resolve_workspace_path(self.config.cwd, str(data.get(ToolPayloadFields.PATH, ".")))
            pattern = str(data[ToolPayloadFields.PATTERN])
        except (KeyError, ValueError) as exc:
            return CommandResult(command=f"{Tools.SEARCH} {payload}", exit_code=1, output=str(exc))

        if not pattern:
            return CommandResult(command=f"{Tools.SEARCH} {payload}", exit_code=1, output="pattern must not be empty")
        top_k = read_positive_int(
            data,
            ToolPayloadFields.TOP_K,
            default=FileToolDefaults.SEARCH_TOP_K,
            minimum=1,
            maximum=FileToolDefaults.SEARCH_MAX_TOP_K,
        )
        max_files = read_positive_int(
            data,
            ToolPayloadFields.MAX_FILES,
            default=FileToolDefaults.SEARCH_MAX_FILES,
            minimum=1,
            maximum=FileToolDefaults.SEARCH_MAX_FILES_LIMIT,
        )
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))

        matches = search_text(root, regex=regex, top_k=top_k, max_files=max_files, workspace=self.config.cwd.resolve())
        output = "\n".join(matches) if matches else "no matches"
        return CommandResult(command=f"{Tools.SEARCH} {root}", exit_code=0, output=output[-self.config.max_output_chars :])

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


def read_tail_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.READ_TAIL,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH,),
        expected_format=Tools.READ_TAIL_EXPECTED_FORMAT,
    )


def read_forward_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.READ_FORWARD,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH,),
        expected_format=Tools.READ_FORWARD_EXPECTED_FORMAT,
    )


def search_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.SEARCH,
        payload=payload,
        required_fields=(ToolPayloadFields.PATTERN,),
        expected_format=Tools.SEARCH_EXPECTED_FORMAT,
        path_required=False,
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
    path_required: bool = True,
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
    if path_required and not str(data[ToolPayloadFields.PATH]).strip():
        return ToolValidationError(
            tool_name=tool_name,
            message="path must not be empty",
            expected_format=expected_format,
            received=payload,
        )
    return None


def read_positive_int(data: dict[str, Any], field: str, default: int, minimum: int, maximum: int) -> int:
    raw = data.get(field, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def tail_text(path: Path, lines: int, max_bytes: int) -> str:
    file_size = path.stat().st_size
    read_size = min(file_size, max_bytes)
    with path.open("rb") as file:
        file.seek(file_size - read_size)
        data = file.read(read_size)
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def search_text(root: Path, regex: re.Pattern[str], top_k: int, max_files: int, workspace: Path) -> list[str]:
    files = [root] if root.is_file() else iter_text_files(root, max_files=max_files)
    matches: list[str] = []
    scanned = 0
    for path in files:
        if scanned >= max_files or len(matches) >= top_k:
            break
        scanned += 1
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for line_number, line in enumerate(file, start=1):
                    if regex.search(line):
                        relative = path.relative_to(workspace)
                        matches.append(f"{relative}:{line_number}: {line.strip()}")
                        if len(matches) >= top_k:
                            break
        except OSError:
            continue
    return matches


def iter_text_files(root: Path, max_files: int):
    yielded = 0
    for path in sorted(root.rglob("*")):
        if yielded >= max_files:
            break
        if any(part in FileToolDefaults.IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file():
            yielded += 1
            yield path


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
