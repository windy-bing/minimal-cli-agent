from __future__ import annotations

from pathlib import Path

from minimal_cli_agent.exceptions import ConfigurationError

SKILL_FILE_NAME = "SKILL.md"


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


def build_system_prompt(base_prompt: str, skill_paths: tuple[Path, ...]) -> str:
    if not skill_paths:
        return base_prompt
    blocks = [base_prompt.rstrip(), "\n\nInstalled skills:"]
    for path in skill_paths:
        blocks.append(format_skill_block(path))
    return "\n\n".join(blocks).strip()


def format_skill_block(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"Unable to read skill {path}: {exc}") from exc
    name = path.parent.name
    return f'<skill name="{name}" path="{path}">\n{content}\n</skill>'
