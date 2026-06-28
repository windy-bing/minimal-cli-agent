from __future__ import annotations

from collections.abc import Generator, Iterator

from minimal_cli_agent.constants import LoopEventData, LoopEventTypes
from minimal_cli_agent.exceptions import AgentFinished, FormatError, NonTerminatingAgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.parser import parse_actions
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, LoopResult, Message


class Agent:
    def __init__(self, config: AgentConfig, harness: AgentHarness) -> None:
        self.config = config
        self.harness = harness

    @classmethod
    def from_config(cls, config: AgentConfig) -> Agent:
        return cls(config=config, harness=AgentHarness(config))

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

        for step in iter_steps(self.config.max_steps):
            messages = self.harness.prepare_context(messages)
            yield LoopEvent(
                type=LoopEventTypes.STEP_START,
                data={LoopEventData.STEP: step, LoopEventData.MAX_STEPS: format_max_steps(self.config.max_steps)},
            )
            output = self.harness.complete(messages)
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT, data={LoopEventData.CONTENT: output})
            messages.append(Message(role="assistant", content=output))

            observations: list[str] = []
            try:
                calls = parse_actions(output)
            except AgentFinished as exc:
                yield LoopEvent(type=LoopEventTypes.DONE, data={LoopEventData.REASON: str(exc)})
                return LoopResult(success=True, final_messages=messages)
            except FormatError as exc:
                if options.allow_final_text:
                    yield LoopEvent(type=LoopEventTypes.TURN_COMPLETE, data={LoopEventData.REASON: "final text"})
                    return LoopResult(success=True, final_messages=messages)
                observation = str(exc)
                observations.append(observation)
                yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
            else:
                for call in calls:
                    yield LoopEvent(
                        type=LoopEventTypes.TOOL_CALL_START,
                        data={LoopEventData.TOOL: call.name, LoopEventData.PAYLOAD: call.payload},
                    )
                try:
                    tool_observations = self.harness.execute_tools(calls)
                except NonTerminatingAgentError as exc:
                    observation = str(exc)
                    observations.append(observation)
                    yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
                else:
                    for tool_observation in tool_observations:
                        observation = tool_observation.to_message().content
                        observations.append(observation)
                        yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})

            combined_observation = "\n\n".join(observations)
            messages.append(Message(role="user", content=combined_observation))
            supplemental_input = read_supplemental_input(options)
            if supplemental_input:
                messages.append(Message(role="user", content=f"User supplemental input during this task:\n{supplemental_input}"))

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


def read_supplemental_input(options: LoopOptions) -> str:
    if options.interrupt_input_reader is None:
        return ""
    value = options.interrupt_input_reader()
    return value.strip() if value else ""


def iter_steps(max_steps: int) -> Iterator[int]:
    step = 1
    while max_steps <= 0 or step <= max_steps:
        yield step
        step += 1


def format_max_steps(max_steps: int) -> int | str:
    return max_steps if max_steps > 0 else "unlimited"


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
