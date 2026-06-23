from __future__ import annotations

from collections.abc import Generator

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.exceptions import AgentFinished, NonTerminatingAgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.parser import parse_action
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopResult, Message


class Agent:
    def __init__(self, config: AgentConfig, harness: AgentHarness | None = None) -> None:
        self.config = config
        self.harness = harness or AgentHarness(config)

    def chat_stream(self, message: str, context: ChatContext | None = None) -> Generator[LoopEvent, None, LoopResult]:
        context = context or ChatContext()
        messages = list(context.messages)
        if not messages:
            messages.append(Message(role="system", content=SYSTEM_PROMPT))
        messages.append(Message(role="user", content=message))

        for step in range(1, self.config.max_steps + 1):
            messages = self.harness.prepare_context(messages)
            yield LoopEvent(type="step_start", data={"step": step, "max_steps": self.config.max_steps})
            output = self.harness.complete(messages)
            yield LoopEvent(type="model_output", data={"content": output})
            messages.append(Message(role="assistant", content=output))

            try:
                command = parse_action(output)
                yield LoopEvent(type="tool_call_start", data={"tool": Tools.SHELL, "payload": command})
                observation = self.harness.execute_shell(command).to_message().content
            except AgentFinished as exc:
                yield LoopEvent(type="done", data={"reason": str(exc)})
                return LoopResult(success=True, final_messages=messages)
            except NonTerminatingAgentError as exc:
                observation = str(exc)

            yield LoopEvent(type="tool_call_result", data={"observation": observation})
            messages.append(Message(role="user", content=observation))

        messages.append(Message(role="user", content="Max steps reached. Stop and summarize current state."))
        yield LoopEvent(type="max_steps", data={"max_steps": self.config.max_steps})
        return LoopResult(success=False, final_messages=messages)

    def chat(self, message: str, context: ChatContext | None = None) -> LoopResult:
        stream = self.chat_stream(message, context)
        while True:
            try:
                next(stream)
            except StopIteration as exc:
                return exc.value

    def run(self, task: str) -> list[Message]:
        context = ChatContext(messages=self.harness.load_messages())
        stream = self.chat_stream(task, context)
        while True:
            try:
                event = next(stream)
            except StopIteration as exc:
                result = exc.value
                self.harness.save_messages(result.final_messages)
                return result.final_messages
            print_event(event)


def print_event(event: LoopEvent) -> None:
    if event.type == "step_start":
        print(f"\n--- step {event.data['step']}/{event.data['max_steps']} ---")
    elif event.type == "model_output":
        print(event.data["content"])
    elif event.type == "tool_call_start":
        print(f"\n[action]\n{event.data['payload']}")
    elif event.type == "tool_call_result":
        print(f"\n[observation]\n{event.data['observation']}")
    elif event.type == "done":
        print(f"\n[done] {event.data['reason']}")
    elif event.type == "max_steps":
        print(f"\n[max_steps] {event.data['max_steps']}")
