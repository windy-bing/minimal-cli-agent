from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
import re

from minimal_cli_agent.constants import Defaults
from minimal_cli_agent.exceptions import ConfigurationError

SKILL_FILE_NAME = "SKILL.md"


@dataclass(frozen=True)
class ProjectRuleSource:
    relative_path: str
    layer: str
    precedence: int


@dataclass(frozen=True)
class ProjectRuleDocument:
    relative_path: str
    layer: str
    precedence: int
    content: str


@dataclass(frozen=True)
class ProjectRuleConflict:
    subject: str
    positive_source: str
    positive_line: str
    negative_source: str
    negative_line: str


PROJECT_RULE_SOURCES = (
    ProjectRuleSource("AGENTS.md", "project", 10),
    ProjectRuleSource(".agents/rules.md", "project-policy", 20),
    ProjectRuleSource(".minimal-agent-instructions.md", "local-override", 30),
)
PROJECT_RULE_FILES = tuple(source.relative_path for source in PROJECT_RULE_SOURCES)
POSITIVE_DIRECTIVES = ("always", "must", "use", "prefer", "enable", "require", "required")
NEGATIVE_DIRECTIVES = ("never", "must not", "do not", "don't", "avoid", "disable", "forbid", "forbidden")


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
    documents = discover_project_rule_documents(project_root, max_chars=max_chars)
    if not documents:
        return []
    blocks: list[str] = []
    conflicts = detect_project_rule_conflicts(documents)
    if conflicts:
        blocks.append(format_project_rule_conflicts(conflicts))
    blocks.extend(format_project_rule_block(document) for document in documents)
    return blocks


def discover_project_rule_documents(project_root: Path, max_chars: int = int(Defaults.PROJECT_RULES_MAX_CHARS)) -> list[ProjectRuleDocument]:
    documents: list[ProjectRuleDocument] = []
    seen_content: set[str] = set()
    remaining = max(0, max_chars)
    for source in iter_project_rule_sources(project_root):
        path = project_root / source.relative_path
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
        documents.append(
            ProjectRuleDocument(
                relative_path=source.relative_path,
                layer=source.layer,
                precedence=source.precedence,
                content=normalized,
            )
        )
        remaining -= len(normalized)
    return documents


def iter_project_rule_sources(project_root: Path) -> tuple[ProjectRuleSource, ...]:
    sources = list(PROJECT_RULE_SOURCES)
    rules_dir = project_root / ".agents" / "rules.d"
    if rules_dir.is_dir():
        for index, path in enumerate(sorted(rules_dir.glob("*.md")), start=1):
            sources.append(ProjectRuleSource(str(path.relative_to(project_root)), "rules.d", 20 + index))
    return tuple(sorted(sources, key=lambda source: (source.precedence, source.relative_path)))


def format_project_rule_block(document: ProjectRuleDocument) -> str:
    path = html.escape(document.relative_path, quote=True)
    layer = html.escape(document.layer, quote=True)
    return (
        f'<project_rules path="{path}" layer="{layer}" precedence="{document.precedence}">\n'
        f"{document.content}\n"
        "</project_rules>"
    )


def detect_project_rule_conflicts(documents: list[ProjectRuleDocument]) -> list[ProjectRuleConflict]:
    positives: dict[str, list[tuple[str, str]]] = {}
    negatives: dict[str, list[tuple[str, str]]] = {}
    conflicts: list[ProjectRuleConflict] = []
    for document in documents:
        source = f"{document.relative_path} ({document.layer})"
        for line in meaningful_rule_lines(document.content):
            polarity, subject = classify_rule_directive(line)
            if polarity == "positive":
                positives.setdefault(subject, []).append((source, line))
                if subject in negatives:
                    for negative_source, negative_line in negatives[subject]:
                        conflicts.append(ProjectRuleConflict(subject, source, line, negative_source, negative_line))
            elif polarity == "negative":
                negatives.setdefault(subject, []).append((source, line))
                if subject in positives:
                    for positive_source, positive_line in positives[subject]:
                        conflicts.append(ProjectRuleConflict(subject, positive_source, positive_line, source, line))
    return deduplicate_rule_conflicts(conflicts)


def meaningful_rule_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def classify_rule_directive(line: str) -> tuple[str | None, str]:
    normalized = normalize_rule_text(line)
    for prefix in NEGATIVE_DIRECTIVES:
        if normalized.startswith(prefix + " "):
            return "negative", normalize_rule_subject(normalized.removeprefix(prefix).strip())
    for prefix in POSITIVE_DIRECTIVES:
        if normalized.startswith(prefix + " "):
            return "positive", normalize_rule_subject(normalized.removeprefix(prefix).strip())
    return None, ""


def normalize_rule_text(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("’", "'")
    value = re.sub(r"[.;:!]+$", "", value)
    return re.sub(r"\s+", " ", value)


def normalize_rule_subject(value: str) -> str:
    value = re.sub(r"^(to|the|a|an)\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def deduplicate_rule_conflicts(conflicts: list[ProjectRuleConflict]) -> list[ProjectRuleConflict]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ProjectRuleConflict] = []
    for conflict in conflicts:
        marker = (conflict.subject, conflict.positive_source, conflict.negative_source)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(conflict)
    return result


def format_project_rule_conflicts(conflicts: list[ProjectRuleConflict]) -> str:
    lines = ["<project_rule_conflicts>"]
    for conflict in conflicts:
        subject = html.escape(conflict.subject, quote=True)
        lines.append(f'<conflict subject="{subject}">')
        lines.append(f"- positive {conflict.positive_source}: {conflict.positive_line}")
        lines.append(f"- negative {conflict.negative_source}: {conflict.negative_line}")
        lines.append("</conflict>")
    lines.append("</project_rule_conflicts>")
    return "\n".join(lines)
