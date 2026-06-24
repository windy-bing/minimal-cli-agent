from __future__ import annotations

from collections.abc import Generator

from minimal_cli_agent.constants import LoopEventData, LoopEventTypes, Tools
from minimal_cli_agent.exceptions import AgentFinished, FormatError, NonTerminatingAgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.parser import parse_action
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, LoopResult, Message


class Agent:
    def __init__(self, config: AgentConfig, harness: AgentHarness | None = None) -> None:
        self.config = config
        self.harness = harness or AgentHarness(config)

    def chat_stream(
        self,
        message: str,
        context: ChatContext | None = None,
        options: LoopOptions | None = None,
    ) -> Generator[LoopEvent, None, LoopResult]:
        options = options or LoopOptions()
        context = context or ChatContext()
        messages = list(context.messages)
        if not messages:
            messages.append(Message(role="system", content=options.system_prompt or SYSTEM_PROMPT))
        messages.append(Message(role="user", content=message))

        for step in range(1, self.config.max_steps + 1):
            messages = self.harness.prepare_context(messages)
            yield LoopEvent(
                type=LoopEventTypes.STEP_START,
                data={LoopEventData.STEP: step, LoopEventData.MAX_STEPS: self.config.max_steps},
            )
            output = self.harness.complete(messages)
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT, data={LoopEventData.CONTENT: output})
            messages.append(Message(role="assistant", content=output))

            try:
                command = parse_action(output)
                yield LoopEvent(
                    type=LoopEventTypes.TOOL_CALL_START,
                    data={LoopEventData.TOOL: Tools.SHELL, LoopEventData.PAYLOAD: command},
                )
                observation = self.harness.execute_shell(command).to_message().content
            except AgentFinished as exc:
                yield LoopEvent(type=LoopEventTypes.DONE, data={LoopEventData.REASON: str(exc)})
                return LoopResult(success=True, final_messages=messages)
            except FormatError as exc:
                if options.allow_final_text:
                    yield LoopEvent(type=LoopEventTypes.TURN_COMPLETE, data={LoopEventData.REASON: "final text"})
                    return LoopResult(success=True, final_messages=messages)
                observation = str(exc)
            except NonTerminatingAgentError as exc:
                observation = str(exc)

            yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
            messages.append(Message(role="user", content=observation))

        messages.append(Message(role="user", content="Max steps reached. Stop and summarize current state."))
        yield LoopEvent(type=LoopEventTypes.MAX_STEPS, data={LoopEventData.MAX_STEPS: self.config.max_steps})
        return LoopResult(success=False, final_messages=messages)

    def chat(
        self,
        message: str,
        context: ChatContext | None = None,
        options: LoopOptions | None = None,
    ) -> LoopResult:
        stream = self.chat_stream(message, context, options)
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
    if event.type == LoopEventTypes.STEP_START:
        print(f"\n--- step {event.data[LoopEventData.STEP]}/{event.data[LoopEventData.MAX_STEPS]} ---")
    elif event.type == LoopEventTypes.MODEL_OUTPUT:
        print(event.data[LoopEventData.CONTENT])
    elif event.type == LoopEventTypes.TOOL_CALL_START:
        print(f"\n[action]\n{event.data[LoopEventData.PAYLOAD]}")
    elif event.type == LoopEventTypes.TOOL_CALL_RESULT:
        print(f"\n[observation]\n{event.data[LoopEventData.OBSERVATION]}")
    elif event.type == LoopEventTypes.DONE:
        print(f"\n[done] {event.data[LoopEventData.REASON]}")
    elif event.type == LoopEventTypes.TURN_COMPLETE:
        return
    elif event.type == LoopEventTypes.MAX_STEPS:
        print(f"\n[max_steps] {event.data[LoopEventData.MAX_STEPS]}")
