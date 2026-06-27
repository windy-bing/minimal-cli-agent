from __future__ import annotations

from pathlib import Path

from minimal_cli_agent.constants import Defaults
from minimal_cli_agent.exceptions import ConfigurationError

SKILL_FILE_NAME = "SKILL.md"
PROJECT_RULE_FILES = ("AGENTS.md", ".agents/rules.md", ".minimal-agent-instructions.md")


def resolve_skill_paths(items: list[str], cwd: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for item in items:
        paths.append(resolve_skill_path(item, cwd))
    return tuple(paths)


def discover_skill_paths(cwd: Path) -> tuple[Path, ...]:
    skills_dir = cwd / "skills"
    if not skills_dir.is_dir():
        return ()
    paths = []
    for path in sorted(skills_dir.glob(f"*/{SKILL_FILE_NAME}")):
        if path.is_file():
            paths.append(path.resolve())
    return tuple(paths)


def resolve_skill_path(item: str, cwd: Path) -> Path:
    candidate = Path(item).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.is_dir():
        candidate = candidate / SKILL_FILE_NAME
    if candidate.exists():
        return candidate.resolve()

    named = cwd / "skills" / item / SKILL_FILE_NAME
    if named.exists():
        return named.resolve()

    raise ConfigurationError(f"Skill not found: {item}. Use a path or a name under skills/<name>/SKILL.md.")


def build_system_prompt(base_prompt: str, skill_paths: tuple[Path, ...], project_root: Path | None = None) -> str:
    project_blocks = discover_project_rule_blocks(project_root) if project_root is not None else []
    if not skill_paths and not project_blocks:
        return base_prompt
    blocks = [base_prompt.rstrip()]
    if skill_paths:
        blocks.append("Installed skills:")
        for path in skill_paths:
            blocks.append(format_skill_block(path))
    if project_blocks:
        blocks.append("Project rules:")
        blocks.extend(project_blocks)
    return "\n\n".join(blocks).strip()


def format_skill_block(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"Unable to read skill {path}: {exc}") from exc
    name = path.parent.name
    return f'<skill name="{name}" path="{path}">\n{content}\n</skill>'


def discover_project_rule_blocks(project_root: Path, max_chars: int = int(Defaults.PROJECT_RULES_MAX_CHARS)) -> list[str]:
    blocks: list[str] = []
    seen_content: set[str] = set()
    remaining = max(0, max_chars)
    for relative in PROJECT_RULE_FILES:
        path = project_root / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            raise ConfigurationError(f"Unable to read project rules {path}: {exc}") from exc
        normalized = "\n".join(line.rstrip() for line in content.splitlines()).strip()
        if not normalized or normalized in seen_content:
            continue
        seen_content.add(normalized)
        if remaining <= 0:
            break
        if len(normalized) > remaining:
            normalized = normalized[: max(0, remaining - 80)].rstrip() + "\n[truncated by project rule budget]"
        block = f'<project_rules path="{relative}">\n{normalized}\n</project_rules>'
        blocks.append(block)
        remaining -= len(normalized)
    return blocks
