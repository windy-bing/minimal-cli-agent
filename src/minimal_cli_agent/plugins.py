from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.mcp_tools import MCPServerConfig, parse_mcp_config
from minimal_cli_agent.skills import SKILL_FILE_NAME

PLUGIN_MANIFEST_FILE = "plugin.json"
PLUGIN_DIR_NAMES = ("plugins", ".minimal-agent/plugins")


@dataclass(frozen=True)
class PluginManifest:
    path: Path
    name: str
    skill_paths: tuple[Path, ...] = field(default_factory=tuple)
    mcp_config_paths: tuple[Path, ...] = field(default_factory=tuple)
    inline_mcp_configs: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def discover_plugin_paths(cwd: Path, include_user: bool = True) -> tuple[Path, ...]:
    roots = [cwd / dirname for dirname in PLUGIN_DIR_NAMES]
    if include_user:
        roots.append(Path.home() / ".minimal-agent" / "plugins")
    paths: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for manifest in sorted(root.glob(f"*/{PLUGIN_MANIFEST_FILE}")):
            if manifest.is_file():
                paths.append(manifest.resolve())
        for manifest in sorted(root.glob(PLUGIN_MANIFEST_FILE)):
            if manifest.is_file():
                paths.append(manifest.resolve())
    return tuple(deduplicate_paths(paths))


def resolve_plugin_paths(items: list[str], cwd: Path) -> tuple[Path, ...]:
    return tuple(resolve_plugin_path(item, cwd) for item in items)


def resolve_plugin_path(item: str, cwd: Path) -> Path:
    candidate = Path(item).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.is_dir():
        candidate = candidate / PLUGIN_MANIFEST_FILE
    if candidate.is_file():
        return candidate.resolve()

    for dirname in PLUGIN_DIR_NAMES:
        named = cwd / dirname / item / PLUGIN_MANIFEST_FILE
        if named.is_file():
            return named.resolve()

    raise ConfigurationError(f"Plugin not found: {item}. Use a manifest path or a name under plugins/<name>/plugin.json.")


def load_plugin_manifest(path: Path) -> PluginManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read plugin manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Plugin manifest must be valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Plugin manifest must contain a JSON object: {path}")
    name = raw.get("name") or path.parent.name
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"Plugin manifest requires a non-empty name: {path}")
    base = path.parent
    return PluginManifest(
        path=path.resolve(),
        name=name.strip(),
        skill_paths=tuple(resolve_declared_skill_paths(raw, base, path)),
        mcp_config_paths=tuple(resolve_declared_mcp_config_paths(raw, base, path)),
        inline_mcp_configs=tuple(resolve_inline_mcp_configs(raw)),
    )


def load_plugin_manifests(paths: tuple[Path, ...]) -> tuple[PluginManifest, ...]:
    return tuple(load_plugin_manifest(path) for path in paths)


def load_plugin_skill_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    skill_paths: list[Path] = []
    for manifest in load_plugin_manifests(paths):
        skill_paths.extend(manifest.skill_paths)
    return tuple(deduplicate_paths(skill_paths))


def load_plugin_mcp_configs(paths: tuple[Path, ...]) -> list[MCPServerConfig]:
    configs: list[MCPServerConfig] = []
    for manifest in load_plugin_manifests(paths):
        for config_path in manifest.mcp_config_paths:
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ConfigurationError(f"Unable to read plugin MCP config {config_path}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ConfigurationError(f"Plugin MCP config must be valid JSON: {config_path}") from exc
            configs.extend(parse_mcp_config(raw))
        for inline in manifest.inline_mcp_configs:
            configs.extend(parse_mcp_config(inline))
    return configs


def resolve_declared_skill_paths(raw: dict[str, Any], base: Path, manifest_path: Path) -> list[Path]:
    paths: list[Path] = []
    for item in normalize_manifest_list(raw.get("skill")) + normalize_manifest_list(raw.get("skills")):
        path = resolve_manifest_path(item, base)
        if path.is_dir():
            path = path / SKILL_FILE_NAME
        elif not path.name:
            path = path / SKILL_FILE_NAME
        if not path.is_file():
            named = base / "skills" / str(item) / SKILL_FILE_NAME
            if named.is_file():
                path = named
        if not path.is_file():
            raise ConfigurationError(f"Plugin skill not found in {manifest_path}: {item}")
        paths.append(path.resolve())
    return deduplicate_paths(paths)


def resolve_declared_mcp_config_paths(raw: dict[str, Any], base: Path, manifest_path: Path) -> list[Path]:
    paths: list[Path] = []
    for key in ("mcp_config", "mcpConfig", "mcp_configs", "mcpConfigs"):
        for item in normalize_manifest_list(raw.get(key)):
            path = resolve_manifest_path(item, base)
            if not path.is_file():
                raise ConfigurationError(f"Plugin MCP config not found in {manifest_path}: {item}")
            paths.append(path.resolve())
    mcp = raw.get("mcp")
    if isinstance(mcp, str):
        path = resolve_manifest_path(mcp, base)
        if not path.is_file():
            raise ConfigurationError(f"Plugin MCP config not found in {manifest_path}: {mcp}")
        paths.append(path.resolve())
    return deduplicate_paths(paths)


def resolve_inline_mcp_configs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    if isinstance(raw.get("mcpServers"), dict):
        configs.append({"mcpServers": raw["mcpServers"]})
    mcp = raw.get("mcp")
    if isinstance(mcp, dict):
        configs.append(mcp)
    return configs


def normalize_manifest_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                result.append(item["path"])
        return result
    return []


def resolve_manifest_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def deduplicate_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result
