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
class WorkflowDelegation:
    task: str
    summary: str
    success: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "summary": self.summary,
            "success": self.success,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDelegation:
        return cls(
            task=str(data.get("task", "")),
            summary=str(data.get("summary", "")),
            success=bool(data.get("success", False)),
            created_at=str(data.get("created_at", "")) or datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True)
class WorkflowArtifact:
    goal: str
    steps: list[WorkflowStep] = field(default_factory=list)
    delegations: list[WorkflowDelegation] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
            "delegations": [delegation.to_dict() for delegation in self.delegations],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowArtifact:
        raw_steps = data.get("steps", [])
        raw_delegations = data.get("delegations", [])
        steps = [WorkflowStep.from_dict(item) for item in raw_steps if isinstance(item, dict)]
        delegations = [WorkflowDelegation.from_dict(item) for item in raw_delegations if isinstance(item, dict)]
        return cls(
            goal=str(data.get("goal", "")),
            steps=steps,
            delegations=delegations,
            created_at=str(data.get("created_at", "")) or datetime.now(timezone.utc).isoformat(),
            updated_at=str(data.get("updated_at", "")) or datetime.now(timezone.utc).isoformat(),
        )


def create_workflow(goal: str) -> WorkflowArtifact:
    return WorkflowArtifact(goal=goal.strip())


def add_workflow_step(workflow: WorkflowArtifact, title: str) -> WorkflowArtifact:
    return WorkflowArtifact(
        goal=workflow.goal,
        steps=[*workflow.steps, WorkflowStep(title=title.strip())],
        delegations=workflow.delegations,
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
        delegations=workflow.delegations,
        created_at=workflow.created_at,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def add_workflow_delegation(workflow: WorkflowArtifact, task: str, summary: str, success: bool) -> WorkflowArtifact:
    return WorkflowArtifact(
        goal=workflow.goal,
        steps=workflow.steps,
        delegations=[
            *workflow.delegations,
            WorkflowDelegation(task=task.strip(), summary=summary.strip(), success=success),
        ],
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
    if workflow.delegations:
        lines.append("delegations:")
        for index, delegation in enumerate(workflow.delegations, start=1):
            status = "success" if delegation.success else "failed"
            lines.append(f"{index}. [{status}] {delegation.task} - {delegation.summary}")
    return "\n".join(lines)
