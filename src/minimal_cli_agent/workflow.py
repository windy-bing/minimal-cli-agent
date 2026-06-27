from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


WORKFLOW_METADATA_KEY = "active_workflow"


@dataclass(frozen=True)
class WorkflowStep:
    title: str
    status: str = "pending"

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "status": self.status}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        status = str(data.get("status", "pending"))
        return cls(title=str(data.get("title", "")), status=status if status in {"pending", "done"} else "pending")


@dataclass(frozen=True)
class WorkflowArtifact:
    goal: str
    steps: list[WorkflowStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowArtifact:
        raw_steps = data.get("steps", [])
        steps = [WorkflowStep.from_dict(item) for item in raw_steps if isinstance(item, dict)]
        return cls(
            goal=str(data.get("goal", "")),
            steps=steps,
            created_at=str(data.get("created_at", "")) or datetime.now(timezone.utc).isoformat(),
            updated_at=str(data.get("updated_at", "")) or datetime.now(timezone.utc).isoformat(),
        )


def create_workflow(goal: str) -> WorkflowArtifact:
    return WorkflowArtifact(goal=goal.strip())


def add_workflow_step(workflow: WorkflowArtifact, title: str) -> WorkflowArtifact:
    return WorkflowArtifact(
        goal=workflow.goal,
        steps=[*workflow.steps, WorkflowStep(title=title.strip())],
        created_at=workflow.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def complete_workflow_step(workflow: WorkflowArtifact, index: int) -> WorkflowArtifact:
    steps: list[WorkflowStep] = []
    for position, step in enumerate(workflow.steps, start=1):
        if position == index:
            steps.append(WorkflowStep(title=step.title, status="done"))
        else:
            steps.append(step)
    return WorkflowArtifact(
        goal=workflow.goal,
        steps=steps,
        created_at=workflow.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def format_workflow_artifact(workflow: WorkflowArtifact) -> str:
    lines = [
        f"goal: {workflow.goal}",
        f"created_at: {workflow.created_at}",
        f"updated_at: {workflow.updated_at}",
    ]
    if workflow.steps:
        lines.append("steps:")
        for index, step in enumerate(workflow.steps, start=1):
            marker = "x" if step.status == "done" else " "
            lines.append(f"{index}. [{marker}] {step.title}")
    return "\n".join(lines)
