from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.constants import EventKinds, PermissionModes
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.interfaces import Model
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, ChatContext, EventRecord, LoopOptions, Message


SUBAGENT_SYSTEM_PROMPT = """You are a scoped sub-agent for minimal-cli-agent.

Work only on the delegated task. Use read-only workspace tools when facts are needed.
Do not modify files. Return a concise result with:
Summary:
Evidence:
Open questions:
"""

WORKER_SYSTEM_PROMPT = """You are a scoped worker sub-agent for minimal-cli-agent.

Work only on the delegated implementation task. You may edit workspace files when needed.
Keep changes minimal and aligned with the task. Return:
Summary:
Changed files:
Verification:
"""

VERIFIER_SYSTEM_PROMPT = """You are a scoped verifier sub-agent for minimal-cli-agent.

Work only on verification. Use read-only tools and commands allowed by policy.
Do not modify files. Return:
Summary:
Evidence:
Risks:
"""

SUBAGENT_ROLES = {"explorer", "worker", "verifier"}


@dataclass(frozen=True)
class SubAgentResult:
    task: str
    summary: str
    success: bool
    role: str = "explorer"
    changed_files: tuple[str, ...] = ()
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class GroupSessionTask:
    task: str
    role: str = "explorer"


@dataclass(frozen=True)
class WriteMergeReport:
    changed_files: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupSessionResult:
    results: tuple[SubAgentResult, ...]
    merge_report: WriteMergeReport
    success: bool


class SubAgentRunner:
    def __init__(self, config: AgentConfig, model: Model) -> None:
        self.config = config
        self.model = model

    def run(self, task: str, role: str = "explorer") -> SubAgentResult:
        role = normalize_subagent_role(role)
        config = copy.copy(self.config)
        config.permission_mode = PermissionModes.AUTO_EDIT if role == "worker" else PermissionModes.PLAN
        before = snapshot_workspace(config.cwd)
        agent = Agent(config=config, harness=AgentHarness(config=config, model=self.model))
        result = agent.chat(
            task,
            ChatContext(),
            LoopOptions(allow_final_text=True, system_prompt=f"{SYSTEM_PROMPT}\n\n{subagent_prompt_for_role(role)}"),
        )
        changed_files = changed_workspace_files(before, snapshot_workspace(config.cwd))
        return SubAgentResult(
            task=task,
            summary=summarize_subagent_messages(result.final_messages),
            success=result.success,
            role=role,
            changed_files=changed_files,
            messages=result.final_messages,
        )


class GroupSessionRunner:
    def __init__(
        self,
        config: AgentConfig,
        model: Model,
        event_recorder: Callable[[EventRecord], None] | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.event_recorder = event_recorder

    def run(self, tasks: list[GroupSessionTask]) -> GroupSessionResult:
        runner = SubAgentRunner(self.config, self.model)
        results: list[SubAgentResult] = []
        writers_by_file: dict[str, list[str]] = {}
        for task in tasks:
            self._record("task_started", {"role": task.role, "task": task.task})
            result = runner.run(task.task, role=task.role)
            results.append(result)
            for path in result.changed_files:
                writers_by_file.setdefault(path, []).append(task.task)
            self._record(
                "task_finished",
                {"role": result.role, "task": result.task, "success": result.success, "changed_files": list(result.changed_files)},
            )
        conflicts = tuple(sorted(path for path, writers in writers_by_file.items() if len(writers) > 1))
        changed_files = tuple(sorted(writers_by_file))
        merge_report = WriteMergeReport(changed_files=changed_files, conflicts=conflicts)
        success = all(result.success for result in results) and not conflicts
        self._record("group_finished", {"success": success, "changed_files": list(changed_files), "conflicts": list(conflicts)})
        return GroupSessionResult(results=tuple(results), merge_report=merge_report, success=success)

    def _record(self, action: str, data: dict) -> None:
        if self.event_recorder is None:
            return
        self.event_recorder(EventRecord(kind=EventKinds.GROUP_SESSION, data={"action": action, **data}))


def summarize_subagent_messages(messages: list[Message], limit: int = 500) -> str:
    for message in reversed(messages):
        if message.role == "assistant" and message.content.strip():
            content = " ".join(message.content.split())
            return content if len(content) <= limit else content[: limit - 3] + "..."
    return "No assistant summary."


def normalize_subagent_role(role: str) -> str:
    return role if role in SUBAGENT_ROLES else "explorer"


def subagent_prompt_for_role(role: str) -> str:
    if role == "worker":
        return WORKER_SYSTEM_PROMPT
    if role == "verifier":
        return VERIFIER_SYSTEM_PROMPT
    return SUBAGENT_SYSTEM_PROMPT


def snapshot_workspace(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if not path.is_file() or should_skip_snapshot_path(path, root):
            continue
        try:
            snapshot[path.relative_to(root).as_posix()] = hash_file(path)
        except OSError:
            continue
    return snapshot


def changed_workspace_files(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    changed = [path for path, digest in after.items() if before.get(path) != digest]
    deleted = [path for path in before if path not in after]
    return tuple(sorted([*changed, *deleted]))


def should_skip_snapshot_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root).parts
    return any(part in {".git", ".agent", ".venv", "__pycache__", ".pytest_cache"} for part in relative)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
