from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from minimal_cli_agent.types import AgentConfig

WORLD_STATE_METADATA_KEY = "minimal_agent_world_state_snapshot"


@dataclass(frozen=True)
class WorldStateSnapshot:
    cwd: str
    permission_mode: str
    provider: str
    model: str
    sandbox: dict[str, Any]
    plugins: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)
    context_budget: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "permission_mode": self.permission_mode,
            "provider": self.provider,
            "model": self.model,
            "sandbox": self.sandbox,
            "plugins": list(self.plugins),
            "skills": list(self.skills),
            "policy": self.policy,
            "context_budget": self.context_budget,
        }

    @property
    def hash(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


def build_world_state_snapshot(config: AgentConfig, context_budget: dict[str, Any] | None = None) -> WorldStateSnapshot:
    return WorldStateSnapshot(
        cwd=str(config.cwd),
        permission_mode=config.permission_mode,
        provider=config.provider,
        model=config.model,
        sandbox={
            "kind": config.sandbox_kind,
            "image": config.sandbox_image,
            "network": config.sandbox_network,
            "read_only": config.sandbox_read_only,
            "allow_network": config.allow_network,
        },
        plugins=relative_paths(config.plugin_paths, config.cwd),
        skills=relative_paths(config.skill_paths, config.cwd),
        policy={
            "preset": config.policy_preset,
            "file": str(config.policy_file) if config.policy_file else None,
        },
        context_budget=context_budget or {},
    )


def diff_world_state(previous: dict[str, Any] | None, current: WorldStateSnapshot) -> dict[str, Any]:
    current_data = current.to_dict()
    if not previous:
        return current_data
    changed: dict[str, Any] = {}
    for key, value in current_data.items():
        if previous.get(key) != value:
            changed[key] = value
    return changed


def relative_paths(paths: tuple[Path, ...], cwd: Path) -> tuple[str, ...]:
    result: list[str] = []
    for path in paths:
        try:
            result.append(str(path.relative_to(cwd)))
        except ValueError:
            result.append(str(path))
    return tuple(sorted(result))
