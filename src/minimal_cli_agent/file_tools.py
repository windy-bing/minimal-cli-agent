from __future__ import annotations

from contextlib import contextmanager
import fcntl
import fnmatch
import hashlib
import json
import re
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from minimal_cli_agent.constants import FileToolDefaults, ToolPayloadFields, Tools
from minimal_cli_agent.tool_registry import validate_object_schema
from minimal_cli_agent.types import AgentConfig, CommandResult, ToolValidationError

READ_FILE_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH],
    "properties": {ToolPayloadFields.PATH: {"type": "string"}},
}

READ_TAIL_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH],
    "properties": {
        ToolPayloadFields.PATH: {"type": "string"},
        ToolPayloadFields.LINES: {
            "type": "integer",
            "minimum": 1,
            "maximum": FileToolDefaults.TAIL_MAX_LINES,
            "default": FileToolDefaults.TAIL_LINES,
        },
        ToolPayloadFields.MAX_BYTES: {"type": "integer", "minimum": 1, "default": FileToolDefaults.TAIL_MAX_BYTES},
    },
}

READ_FORWARD_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH],
    "properties": {
        ToolPayloadFields.PATH: {"type": "string"},
        ToolPayloadFields.MODE: {"type": "string", "enum": ["bytes", "lines"], "default": "bytes"},
        ToolPayloadFields.OFFSET: {"type": "integer", "minimum": 0, "default": 0},
        ToolPayloadFields.LIMIT: {"type": "integer", "minimum": 1, "default": FileToolDefaults.FORWARD_LIMIT},
        ToolPayloadFields.LINE_OFFSET: {"type": "integer", "minimum": 0},
        ToolPayloadFields.LINE_LIMIT: {
            "type": "integer",
            "minimum": 1,
            "maximum": FileToolDefaults.TAIL_MAX_LINES,
        },
    },
}

FILE_INFO_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH],
    "properties": {ToolPayloadFields.PATH: {"type": "string"}},
}

FILE_INFO_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["file_size", "is_binary", "sha256", "path"],
    "properties": {
        "path": {"type": "string"},
        "file_size": {"type": "integer", "minimum": 0},
        "is_binary": {"type": "boolean"},
        "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    },
}

SEARCH_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATTERN],
    "properties": {
        ToolPayloadFields.PATTERN: {"type": "string"},
        ToolPayloadFields.PATH: {"type": "string", "default": "."},
        ToolPayloadFields.TOP_K: {
            "type": "integer",
            "minimum": 1,
            "maximum": FileToolDefaults.SEARCH_MAX_TOP_K,
            "default": FileToolDefaults.SEARCH_TOP_K,
        },
        ToolPayloadFields.MAX_FILES: {
            "type": "integer",
            "minimum": 1,
            "maximum": FileToolDefaults.SEARCH_MAX_FILES_LIMIT,
            "default": FileToolDefaults.SEARCH_MAX_FILES,
        },
        ToolPayloadFields.TIMEOUT_MS: {
            "type": "integer",
            "minimum": 1,
            "maximum": FileToolDefaults.SEARCH_MAX_TIMEOUT_MS,
            "default": FileToolDefaults.SEARCH_TIMEOUT_MS,
        },
        ToolPayloadFields.IGNORE_DIRS: {"type": "array", "items": {"type": "string"}},
        ToolPayloadFields.INCLUDE_EXTENSIONS: {"type": "array", "items": {"type": "string"}},
    },
}

WRITE_FILE_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH, ToolPayloadFields.CONTENT],
    "properties": {
        ToolPayloadFields.PATH: {"type": "string"},
        ToolPayloadFields.CONTENT: {"type": "string"},
    },
}

EDIT_FILE_SCHEMA = {
    "type": "object",
    "required": [ToolPayloadFields.PATH, ToolPayloadFields.START_LINE, ToolPayloadFields.END_LINE, ToolPayloadFields.CONTENT],
    "properties": {
        ToolPayloadFields.PATH: {"type": "string"},
        ToolPayloadFields.START_LINE: {"type": "integer", "minimum": 1},
        ToolPayloadFields.END_LINE: {"type": "integer", "minimum": 1},
        ToolPayloadFields.CONTENT: {"type": "string"},
    },
}


class FileToolEnvironment:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._write_locks: dict[Path, threading.Lock] = {}

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
        if is_probably_binary(path):
            return CommandResult(command=f"{Tools.READ_FILE} {path}", exit_code=1, output="file appears to be binary; use a binary-aware tool")

        content = path.read_text(encoding="utf-8", errors="replace")
        output = content[-self.config.max_output_chars :]
        return CommandResult(
            command=f"{Tools.READ_FILE} {path}",
            exit_code=0,
            output=output,
            metadata=file_read_metadata(path, chars_read=len(output), truncated=len(output) < len(content)),
        )

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
        if is_probably_binary(path):
            return CommandResult(command=f"{Tools.READ_TAIL} {path}", exit_code=1, output="file appears to be binary; use a binary-aware tool")

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
        output = output[-self.config.max_output_chars :]
        return CommandResult(
            command=f"{Tools.READ_TAIL} {path}",
            exit_code=0,
            output=output,
            metadata=file_read_metadata(path, chars_read=len(output), mode="tail", lines=lines),
        )

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
        if is_probably_binary(path):
            return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=1, output="file appears to be binary; use a binary-aware tool")

        mode = str(data.get(ToolPayloadFields.MODE, "bytes")).lower()
        if mode not in {"bytes", "lines"}:
            return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=1, output='mode must be "bytes" or "lines"')
        if mode == "bytes" and (ToolPayloadFields.LINE_OFFSET in data or ToolPayloadFields.LINE_LIMIT in data):
            return CommandResult(command=f"{Tools.READ_FORWARD} {path}", exit_code=1, output='line_offset and line_limit require mode "lines"')
        if mode == "lines":
            return self._read_forward_lines(path, data)

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
        next_offset = min(offset + len(chunk), path.stat().st_size)
        return CommandResult(
            command=f"{Tools.READ_FORWARD} {path}",
            exit_code=0,
            output=output,
            metadata=file_read_metadata(
                path,
                chars_read=len(output),
                mode="bytes",
                offset=offset,
                next_offset=next_offset,
                bytes_read=len(chunk),
                eof=next_offset >= path.stat().st_size,
            ),
        )

    def _read_forward_lines(self, path: Path, data: dict[str, Any]) -> CommandResult:
        line_offset = read_positive_int(data, ToolPayloadFields.LINE_OFFSET, default=0, minimum=0, maximum=10**9)
        line_limit = read_positive_int(
            data,
            ToolPayloadFields.LINE_LIMIT,
            default=FileToolDefaults.TAIL_LINES,
            minimum=1,
            maximum=FileToolDefaults.TAIL_MAX_LINES,
        )
        selected: list[str] = []
        next_line_offset = line_offset
        eof = True
        with path.open("r", encoding="utf-8", errors="replace") as file:
            for index, line in enumerate(file):
                if index < line_offset:
                    continue
                if len(selected) >= line_limit:
                    eof = False
                    break
                selected.append(line)
                next_line_offset = index + 1
        output = "".join(selected)[-self.config.max_output_chars :]
        return CommandResult(
            command=f"{Tools.READ_FORWARD} {path}",
            exit_code=0,
            output=output,
            metadata=file_read_metadata(
                path,
                chars_read=len(output),
                mode="lines",
                line_offset=line_offset,
                next_line_offset=next_line_offset,
                lines_read=len(selected),
                eof=eof,
            ),
        )

    def file_info(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.FILE_INFO} {payload}", exit_code=1, output=str(exc))
        if not path.exists():
            return CommandResult(command=f"{Tools.FILE_INFO} {path}", exit_code=1, output="file does not exist")
        if not path.is_file():
            return CommandResult(command=f"{Tools.FILE_INFO} {path}", exit_code=1, output="path is not a file")

        metadata = file_info_metadata(path, self.config.cwd.resolve())
        return CommandResult(
            command=f"{Tools.FILE_INFO} {path}",
            exit_code=0,
            output=json.dumps(metadata, ensure_ascii=False, indent=2),
            metadata=metadata,
        )

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
        timeout_ms = read_positive_int(
            data,
            ToolPayloadFields.TIMEOUT_MS,
            default=FileToolDefaults.SEARCH_TIMEOUT_MS,
            minimum=1,
            maximum=FileToolDefaults.SEARCH_MAX_TIMEOUT_MS,
        )
        ignore_dirs = FileToolDefaults.IGNORED_DIRS + read_string_tuple(data, ToolPayloadFields.IGNORE_DIRS)
        include_extensions = normalize_extensions(read_string_tuple(data, ToolPayloadFields.INCLUDE_EXTENSIONS))
        ignore_patterns = load_ignore_patterns(self.config.cwd.resolve(), root)
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))

        result = search_text(
            root,
            pattern=pattern,
            regex=regex,
            top_k=top_k,
            max_files=max_files,
            workspace=self.config.cwd.resolve(),
            timeout_ms=timeout_ms,
            ignore_dirs=ignore_dirs,
            ignore_patterns=ignore_patterns,
            include_extensions=include_extensions,
        )
        output = "\n".join(result.matches) if result.matches else "no matches"
        if result.timed_out:
            output = f"{output}\nsearch timed out after {timeout_ms}ms; scanned_files={result.scanned_files}".strip()
        return CommandResult(
            command=f"{Tools.SEARCH} {root}",
            exit_code=0,
            output=output[-self.config.max_output_chars :],
            metadata={"scanned_files": result.scanned_files, "timed_out": result.timed_out, "matches": len(result.matches)},
        )

    def write_file(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.WRITE_FILE} {payload}", exit_code=1, output=str(exc))
        content = str(data[ToolPayloadFields.CONTENT])
        structured_error = validate_structured_content(path, content)
        if structured_error is not None:
            return CommandResult(
                command=f"{Tools.WRITE_FILE} {path}",
                exit_code=2,
                output=structured_error,
                skipped=True,
            )
        with self._lock_for(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        relative = path.relative_to(self.config.cwd.resolve())
        return CommandResult(
            command=f"{Tools.WRITE_FILE} {relative}",
            exit_code=0,
            output=f"Wrote {relative} ({len(content)} chars).",
            metadata={"write_lock": "cross_process"},
        )

    def edit_file(self, payload: str) -> CommandResult:
        try:
            data = parse_payload(payload)
            path = resolve_workspace_path(self.config.cwd, str(data[ToolPayloadFields.PATH]))
        except ValueError as exc:
            return CommandResult(command=f"{Tools.EDIT_FILE} {payload}", exit_code=1, output=str(exc))
        if not path.exists():
            return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=1, output="file does not exist")
        if not path.is_file():
            return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=1, output="path is not a file")

        start_line = read_positive_int(data, ToolPayloadFields.START_LINE, default=1, minimum=1, maximum=10**9)
        end_line = read_positive_int(data, ToolPayloadFields.END_LINE, default=start_line, minimum=1, maximum=10**9)
        if end_line < start_line:
            return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=2, output="end_line must be >= start_line", skipped=True)

        replacement = str(data[ToolPayloadFields.CONTENT])
        with self._lock_for(path):
            original = path.read_text(encoding="utf-8", errors="replace")
            lines = original.splitlines(keepends=True)
            if start_line > len(lines) + 1:
                return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=2, output="start_line is beyond end of file", skipped=True)
            if end_line > len(lines):
                return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=2, output="end_line is beyond end of file", skipped=True)

            replacement_lines = split_replacement_lines(replacement)
            edited = "".join(lines[: start_line - 1] + replacement_lines + lines[end_line:])
            structured_error = validate_structured_content(path, edited)
            if structured_error is not None:
                return CommandResult(command=f"{Tools.EDIT_FILE} {path}", exit_code=2, output=structured_error, skipped=True)
            path.write_text(edited, encoding="utf-8")

        relative = path.relative_to(self.config.cwd.resolve())
        return CommandResult(
            command=f"{Tools.EDIT_FILE} {relative}",
            exit_code=0,
            output=f"Edited {relative} lines {start_line}-{end_line}.",
            metadata={"write_lock": "cross_process"},
        )

    @contextmanager
    def _lock_for(self, path: Path):
        resolved = path.resolve()
        lock = self._write_locks.setdefault(resolved, threading.Lock())
        with lock:
            lock_path = workspace_lock_path(self.config.cwd.resolve(), resolved)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("w", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


def file_info_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.FILE_INFO,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH,),
        expected_format=Tools.FILE_INFO_EXPECTED_FORMAT,
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


def edit_file_validator(payload: str) -> ToolValidationError | None:
    return validate_json_fields(
        tool_name=Tools.EDIT_FILE,
        payload=payload,
        required_fields=(ToolPayloadFields.PATH, ToolPayloadFields.START_LINE, ToolPayloadFields.END_LINE, ToolPayloadFields.CONTENT),
        expected_format=Tools.EDIT_FILE_EXPECTED_FORMAT,
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


def read_string_tuple(data: dict[str, Any], field: str) -> tuple[str, ...]:
    raw = data.get(field, ())
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if str(item).strip())


def normalize_extensions(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value if value.startswith(".") else f".{value}" for value in values)


def tail_text(path: Path, lines: int, max_bytes: int) -> str:
    file_size = path.stat().st_size
    read_size = min(file_size, max_bytes)
    with path.open("rb") as file:
        file.seek(file_size - read_size)
        data = file.read(read_size)
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def is_probably_binary(path: Path, sample_size: int = 4096) -> bool:
    try:
        with path.open("rb") as file:
            sample = file.read(sample_size)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control_bytes = sum(1 for byte in sample if byte < 9 or 13 < byte < 32)
    return control_bytes / len(sample) > 0.30


def file_read_metadata(path: Path, chars_read: int, **extra: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "file_size": path.stat().st_size,
        "chars_read": chars_read,
    }
    metadata.update(extra)
    return metadata


def file_info_metadata(path: Path, workspace: Path) -> dict[str, Any]:
    sample = read_file_sample(path)
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    is_binary = is_probably_binary(path)
    metadata: dict[str, Any] = {
        "path": path.relative_to(workspace).as_posix(),
        "file_size": path.stat().st_size,
        "is_binary": is_binary,
        "sha256": digest.hexdigest(),
        "sample_bytes": len(sample),
    }
    if not is_binary:
        text = sample.decode("utf-8", errors="replace")
        metadata["line_count_sample"] = len(text.splitlines())
        metadata["preview"] = text[:500]
    else:
        metadata["hex_preview"] = sample[:64].hex()
    return metadata


def read_file_sample(path: Path, sample_size: int = 4096) -> bytes:
    try:
        with path.open("rb") as file:
            return file.read(sample_size)
    except OSError:
        return b""


def split_replacement_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.splitlines(keepends=True)
    if content.endswith("\n"):
        return lines
    return lines + ["\n"]


class SearchResult:
    def __init__(self, matches: list[str], scanned_files: int, timed_out: bool) -> None:
        self.matches = matches
        self.scanned_files = scanned_files
        self.timed_out = timed_out


class SearchMatch:
    def __init__(self, rendered: str, score: int, path_key: str, line_number: int) -> None:
        self.rendered = rendered
        self.score = score
        self.path_key = path_key
        self.line_number = line_number


def search_text(
    root: Path,
    pattern: str,
    regex: re.Pattern[str],
    top_k: int,
    max_files: int,
    workspace: Path,
    timeout_ms: int,
    ignore_dirs: tuple[str, ...],
    ignore_patterns: tuple[str, ...],
    include_extensions: tuple[str, ...],
) -> SearchResult:
    deadline = time.monotonic() + (timeout_ms / 1000)
    files = (
        [root]
        if root.is_file()
        else iter_text_files(
            root,
            max_files=max_files,
            workspace=workspace,
            ignore_dirs=ignore_dirs,
            ignore_patterns=ignore_patterns,
            include_extensions=include_extensions,
        )
    )
    matches: list[SearchMatch] = []
    pattern_lower = pattern.lower()
    scanned = 0
    timed_out = False
    for path in files:
        if time.monotonic() > deadline:
            timed_out = True
            break
        if scanned >= max_files:
            break
        scanned += 1
        if is_probably_binary(path):
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for line_number, line in enumerate(file, start=1):
                    if time.monotonic() > deadline:
                        timed_out = True
                        break
                    if regex.search(line):
                        relative = path.relative_to(workspace)
                        matches.append(
                            SearchMatch(
                                rendered=f"{relative}:{line_number}: {line.strip()}",
                                score=search_score(relative, line, pattern_lower),
                                path_key=relative.as_posix(),
                                line_number=line_number,
                            )
                        )
        except OSError:
            continue
    ranked = sorted(matches, key=lambda match: (-match.score, match.path_key, match.line_number))
    return SearchResult(matches=[match.rendered for match in ranked[:top_k]], scanned_files=scanned, timed_out=timed_out)


def search_score(path: Path, line: str, pattern_lower: str) -> int:
    score = 0
    path_text = path.as_posix().lower()
    name = path.name.lower()
    line_text = line.strip().lower()
    if pattern_lower and pattern_lower in name:
        score += 40
    if pattern_lower and pattern_lower in path_text:
        score += 20
    if pattern_lower and line_text.startswith(pattern_lower):
        score += 15
    if pattern_lower and pattern_lower in line_text:
        score += 5
    if path.suffix in FileToolDefaults.SEARCH_PRIORITY_EXTENSIONS:
        score += 2
    return score


def iter_text_files(
    root: Path,
    max_files: int,
    workspace: Path,
    ignore_dirs: tuple[str, ...],
    ignore_patterns: tuple[str, ...],
    include_extensions: tuple[str, ...],
):
    yielded = 0
    for path in sorted(root.rglob("*")):
        if yielded >= max_files:
            break
        if should_ignore_path(path, workspace=workspace, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns):
            continue
        if path.is_file() and (not include_extensions or path.suffix in include_extensions):
            yielded += 1
            yield path


def load_ignore_patterns(workspace: Path, root: Path) -> tuple[str, ...]:
    search_dirs = [workspace]
    if root.is_dir() and root.resolve() != workspace:
        search_dirs.append(root.resolve())

    patterns: list[str] = []
    seen_files: set[Path] = set()
    for directory in search_dirs:
        for name in FileToolDefaults.IGNORE_FILES:
            path = directory / name
            if path in seen_files or not path.is_file():
                continue
            seen_files.add(path)
            patterns.extend(parse_ignore_file(path))
    return tuple(patterns)


def parse_ignore_file(path: Path) -> list[str]:
    patterns: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return patterns
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped)
    return patterns


def should_ignore_path(path: Path, workspace: Path, ignore_dirs: tuple[str, ...], ignore_patterns: tuple[str, ...]) -> bool:
    relative = path.relative_to(workspace).as_posix()
    parts = relative.split("/")
    if any(part in ignore_dirs for part in parts):
        return True

    for pattern in ignore_patterns:
        normalized = pattern.strip().lstrip("/")
        if not normalized:
            continue
        if normalized.endswith("/"):
            directory = normalized.rstrip("/")
            if directory in parts or relative.startswith(f"{directory}/"):
                return True
            continue
        if "/" in normalized:
            if fnmatch.fnmatch(relative, normalized):
                return True
            continue
        if fnmatch.fnmatch(path.name, normalized) or any(fnmatch.fnmatch(part, normalized) for part in parts):
            return True
    return False


def validate_structured_content(path: Path, content: str) -> str | None:
    suffix = path.suffix.lower()
    parsed_data: Any = None
    try:
        if suffix in FileToolDefaults.JSON_SUFFIXES:
            parsed_data = json.loads(content)
        elif suffix in FileToolDefaults.TOML_SUFFIXES:
            tomllib.loads(content)
        elif suffix in FileToolDefaults.XML_SUFFIXES:
            ET.fromstring(content)
        elif suffix in FileToolDefaults.YAML_SUFFIXES:
            parsed_data = parse_yaml_content(content)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, ET.ParseError) as exc:
        return append_formatting_suggestion(f"Structured file validation failed for {path.name}: {exc}", path)
    except ValueError as exc:
        return append_formatting_suggestion(f"Structured file validation failed for {path.name}: {exc}", path)
    if suffix in (*FileToolDefaults.JSON_SUFFIXES, *FileToolDefaults.YAML_SUFFIXES):
        schema_error = validate_sidecar_schema(path, parsed_data)
        if schema_error is not None:
            return schema_error
    return None


def validate_sidecar_schema(path: Path, data: Any) -> str | None:
    schema_path = find_schema_path(path)
    if schema_path is None:
        return None
    try:
        schema = load_schema_file(schema_path)
    except (OSError, ValueError) as exc:
        return f"Structured file schema validation failed for {path.name}: invalid schema {schema_path.name}: {exc}"
    if not isinstance(schema, dict):
        return f"Structured file schema validation failed for {path.name}: schema {schema_path.name} must be a JSON object"
    errors = validate_object_schema(data, schema)
    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        return append_formatting_suggestion(
            f"Structured file schema validation failed for {path.name} using {schema_path.name}:\n{rendered}",
            path,
        )
    return None


def find_schema_path(path: Path) -> Path | None:
    candidates = (
        path.with_suffix(".schema.json"),
        path.with_suffix(".schema.yaml"),
        path.with_suffix(".schema.yml"),
        path.with_name(f"{path.name}.schema.json"),
        path.with_name(f"{path.name}.schema.yaml"),
        path.with_name(f"{path.name}.schema.yml"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_schema_file(path: Path) -> Any:
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() in FileToolDefaults.JSON_SUFFIXES:
        return json.loads(content)
    if path.suffix.lower() in FileToolDefaults.YAML_SUFFIXES:
        return parse_yaml_content(content)
    return json.loads(content)


def parse_yaml_content(content: str) -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return parse_simple_yaml(content)
    try:
        return yaml.safe_load(content)
    except Exception as exc:  # pragma: no cover - exact PyYAML exception type depends on optional dependency.
        raise ValueError(f"YAML content is invalid: {exc}") from exc


def parse_simple_yaml(content: str) -> Any:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("\t"):
            raise ValueError(f"YAML line {line_number}: tabs are not supported")
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"YAML line {line_number}: list item without a key")
            value = parse_simple_yaml_scalar(stripped[2:].strip())
            assert isinstance(result[current_list_key], list)
            result[current_list_key].append(value)
            continue
        current_list_key = None
        if ":" not in stripped:
            raise ValueError(f"YAML line {line_number}: expected key: value")
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"YAML line {line_number}: empty key")
        value = value.strip()
        if value == "":
            result[key] = []
            current_list_key = key
        else:
            result[key] = parse_simple_yaml_scalar(value)
    return result


def parse_simple_yaml_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_simple_yaml_scalar(item.strip()) for item in inner.split(",")]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value.strip("\"'")


def append_formatting_suggestion(message: str, path: Path) -> str:
    suggestion = formatting_suggestion(path)
    return f"{message}\nFormatting suggestion: {suggestion}" if suggestion else message


def formatting_suggestion(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in FileToolDefaults.JSON_SUFFIXES:
        return "fix JSON syntax, then format with `python -m json.tool` or your editor formatter; JSON requires double quotes and no trailing commas."
    if suffix in FileToolDefaults.YAML_SUFFIXES:
        return "use spaces instead of tabs, keep indentation consistent, then format with `prettier --parser yaml` or `yamlfmt`."
    if suffix in FileToolDefaults.TOML_SUFFIXES:
        return "check TOML table headers and quote rules, then format with `taplo fmt` if available."
    if suffix in FileToolDefaults.XML_SUFFIXES:
        return "make sure tags are balanced, then format with `xmllint --format` if available."
    return ""


def workspace_lock_path(workspace: Path, target: Path) -> Path:
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    return workspace / ".agent" / "locks" / f"{digest}.lock"


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
