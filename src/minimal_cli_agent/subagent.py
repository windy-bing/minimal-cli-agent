from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from minimal_cli_agent.agent import Agent
from minimal_cli_agent.constants import PermissionModes
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.interfaces import Model
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions, Message


SUBAGENT_SYSTEM_PROMPT = """You are a scoped sub-agent for minimal-cli-agent.

Work only on the delegated task. Use read-only workspace tools when facts are needed.
Do not modify files. Return a concise result with:
Summary:
Evidence:
Open questions:
"""


@dataclass(frozen=True)
class SubAgentResult:
    task: str
    summary: str
    success: bool
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SubAgentRunner:
    def __init__(self, config: AgentConfig, model: Model) -> None:
        self.config = config
        self.model = model

    def run(self, task: str) -> SubAgentResult:
        config = copy.copy(self.config)
        config.permission_mode = PermissionModes.PLAN
        agent = Agent(config=config, harness=AgentHarness(config=config, model=self.model))
        result = agent.chat(
            task,
            ChatContext(),
            LoopOptions(allow_final_text=True, system_prompt=f"{SYSTEM_PROMPT}\n\n{SUBAGENT_SYSTEM_PROMPT}"),
        )
        return SubAgentResult(
            task=task,
            summary=summarize_subagent_messages(result.final_messages),
            success=result.success,
            messages=result.final_messages,
        )


def summarize_subagent_messages(messages: list[Message], limit: int = 500) -> str:
    for message in reversed(messages):
        if message.role == "assistant" and message.content.strip():
            content = " ".join(message.content.split())
            return content if len(content) <= limit else content[: limit - 3] + "..."
    return "No assistant summary."
