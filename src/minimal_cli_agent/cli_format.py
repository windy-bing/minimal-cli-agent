from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from minimal_cli_agent.constants import LoopEventData, LoopEventTypes, Tools
from minimal_cli_agent.types import AgentConfig, LoopEvent


ACTION_BLOCK_PATTERN = re.compile(r"```(?:bash-action|tool-action)\n.*?```", re.DOTALL)


def print_compact_event(event: LoopEvent) -> None:
    if event.type == LoopEventTypes.STEP_START:
        print(f"\n--- step {event.data[LoopEventData.STEP]}/{event.data[LoopEventData.MAX_STEPS]} ---")
    elif event.type == LoopEventTypes.MODEL_OUTPUT:
        print_compact_model_output(str(event.data[LoopEventData.CONTENT]))
    elif event.type == LoopEventTypes.TOOL_CALL_START:
        print(f"[action] {summarize_tool_call(str(event.data[LoopEventData.TOOL]), str(event.data[LoopEventData.PAYLOAD]))}")
    elif event.type == LoopEventTypes.TOOL_CALL_RESULT:
        summary = summarize_observation(str(event.data[LoopEventData.OBSERVATION]))
        if summary:
            print(f"[observation] {summary}")
    elif event.type == LoopEventTypes.DONE:
        print(f"[done] {event.data[LoopEventData.REASON]}")
    elif event.type == LoopEventTypes.MAX_STEPS:
        print(f"[max_steps] {event.data[LoopEventData.MAX_STEPS]}")


def print_compact_model_output(content: str) -> None:
    stripped = ACTION_BLOCK_PATTERN.sub("", content).strip()
    action_count = len(ACTION_BLOCK_PATTERN.findall(content))
    if stripped:
        print(stripped)
    elif action_count:
        print(f"model requested {action_count} action(s)")


def summarize_tool_call(tool: str, payload: str) -> str:
    if tool == Tools.SHELL:
        return f"shell: {first_line(payload)}"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return f"{tool}: {first_line(payload)}"
    if not isinstance(data, dict):
        return f"{tool}: {first_line(payload)}"

    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD, Tools.FILE_INFO}:
        return f"{tool}: {compact_path(str(data.get('path', '<missing>')))}"
    if tool == Tools.SEARCH:
        pattern = str(data.get("pattern", ""))
        path = compact_path(str(data.get("path", ".")))
        return f"search: {path} for {pattern!r}"
    if tool == Tools.WRITE_FILE:
        content = str(data.get("content", ""))
        return f"write_file: {compact_path(str(data.get('path', '<missing>')))} ({len(content)} chars)"
    if tool == Tools.EDIT_FILE:
        return (
            f"edit_file: {compact_path(str(data.get('path', '<missing>')))} "
            f"lines {data.get('start_line', '?')}-{data.get('end_line', '?')}"
        )
    return f"{tool}: {first_line(payload)}"


def summarize_observation(observation: str) -> str:
    if observation.startswith("Model request failed:"):
        return first_line(observation)
    if is_plan_mode_block(observation):
        reason = extract_output_block(observation).strip() or "plan mode blocked execution"
        return f"skipped: {reason}"

    status = extract_field(observation, "status")
    exit_code = extract_field(observation, "exit_code")
    command = extract_command_block(observation)
    output = extract_output_block(observation)
    prefix = summarize_command(command)
    metrics = summarize_output(output)
    pieces = [piece for piece in (prefix, f"status={status}" if status else "", f"exit={exit_code}" if exit_code else "", metrics) if piece]
    return ", ".join(pieces) if pieces else first_line(observation)


def summarize_command(command: str) -> str:
    if not command:
        return ""
    parts = command.split(maxsplit=1)
    tool = parts[0]
    target = compact_path(parts[1]) if len(parts) > 1 else ""
    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD, Tools.FILE_INFO, Tools.SEARCH, Tools.WRITE_FILE, Tools.EDIT_FILE}:
        return f"{tool} {target}".strip()
    return first_line(command)


def summarize_output(output: str) -> str:
    if not output:
        return "output=0 chars"
    lines = output.splitlines()
    if output.strip() == "no matches":
        return "no matches"
    if output.startswith("search timed out") or "search timed out" in output:
        return f"{len(lines)} lines, timed out"
    return f"{len(lines)} lines, {len(output)} chars"


def extract_field(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_command_block(text: str) -> str:
    return extract_named_block(text, "command")


def extract_output_block(text: str) -> str:
    return extract_named_block(text, "output")


def extract_named_block(text: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\n```text\n(.*?)\n```", text, flags=re.DOTALL)
    return match.group(1) if match else ""


def first_line(text: str, limit: int = 160) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def compact_path(path_text: str, base: Path | None = None) -> str:
    path = Path(path_text)
    cwd = base or Path.cwd()
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except (OSError, ValueError):
        return path.name if path.is_absolute() else path_text


def is_plan_mode_block(observation: object) -> bool:
    text = str(observation)
    return "plan mode does not execute" in text


def is_plan_mode_write_block(observation: object) -> bool:
    text = str(observation)
    return "plan mode does not execute write_file" in text or "plan mode does not execute edit_file" in text


def render_prompt(config: AgentConfig) -> str:
    cyan = "\033[36m" if sys.stdout.isatty() else ""
    dim = "\033[2m" if sys.stdout.isatty() else ""
    reset = "\033[0m" if sys.stdout.isatty() else ""
    cwd = compact_path(str(config.cwd), base=config.cwd)
    model = f"{config.provider}/{config.model}"
    return (
        f"\n{cyan}╭─ minimal-agent{reset} {dim}{cwd}{reset}\n"
        f"{dim}│ model: {model}  permission: {config.permission_mode}{reset}\n"
        f"{cyan}╰─>{reset} "
    )


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}m{remainder:.1f}s"
