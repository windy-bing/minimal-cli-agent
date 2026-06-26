from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any


PLAN_METADATA_KEY = "active_plan"


@dataclass(frozen=True)
class PlanArtifact:
    goal: str
    summary: str
    steps: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "summary": self.summary,
            "steps": self.steps,
            "evidence": self.evidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanArtifact:
        return cls(
            goal=str(data.get("goal", "")),
            summary=str(data.get("summary", "")),
            steps=[str(item) for item in data.get("steps", []) if str(item).strip()],
            evidence=[str(item) for item in data.get("evidence", []) if str(item).strip()],
            created_at=str(data.get("created_at", "")) or datetime.now(timezone.utc).isoformat(),
        )


def build_plan_prompt(goal: str) -> str:
    return (
        "Create an execution plan for the current project.\n"
        "Use read-only tools if workspace facts are needed.\n"
        "Do not modify files and do not run write or network actions.\n\n"
        "Return a concise plan with these sections:\n"
        "Summary:\n"
        "Steps:\n"
        "Evidence:\n\n"
        f"Goal: {goal}"
    )


def create_plan_artifact(goal: str, assistant_output: str) -> PlanArtifact:
    summary = extract_summary(assistant_output)
    return PlanArtifact(
        goal=goal,
        summary=summary or assistant_output.strip(),
        steps=extract_section_items(assistant_output, "steps"),
        evidence=extract_section_items(assistant_output, "evidence"),
    )


def extract_summary(value: str) -> str:
    match = re.search(r"(?im)^summary\s*:\s*(.*)$", value)
    if match and match.group(1).strip():
        return match.group(1).strip()
    for line in value.splitlines():
        cleaned = line.strip(" -\t")
        if cleaned and not cleaned.lower().startswith(("steps:", "evidence:")):
            return cleaned
    return ""


def extract_section_items(value: str, section: str) -> list[str]:
    pattern = re.compile(rf"(?ims)^{re.escape(section)}\s*:\s*(.*?)(?=^[A-Za-z][A-Za-z ]*\s*:|\Z)")
    match = pattern.search(value)
    if not match:
        return []
    items: list[str] = []
    for line in match.group(1).splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def format_plan_artifact(plan: PlanArtifact) -> str:
    lines = [
        f"goal: {plan.goal}",
        f"created_at: {plan.created_at}",
        f"summary: {plan.summary}",
    ]
    if plan.steps:
        lines.append("steps:")
        lines.extend(f"- {step}" for step in plan.steps)
    if plan.evidence:
        lines.append("evidence:")
        lines.extend(f"- {item}" for item in plan.evidence)
    return "\n".join(lines)
