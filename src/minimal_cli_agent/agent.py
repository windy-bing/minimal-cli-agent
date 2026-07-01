from __future__ import annotations

from collections.abc import Generator, Iterator
from uuid import uuid4

from minimal_cli_agent.constants import LoopEventData, LoopEventTypes
from minimal_cli_agent.exceptions import AgentFinished, FormatError, ModelRequestError, NonTerminatingAgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.parser import parse_actions
from minimal_cli_agent.prompts import SYSTEM_PROMPT
from minimal_cli_agent.tool_ledger import ToolCallLedger
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, LoopResult, Message, ToolCall

OBSERVATION_TRUNCATED_MESSAGE = "Observation output truncated because the per-step budget was reached."


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
        previous_trace_id = self.harness.trace_id
        self.harness.trace_id = context.metadata.get("trace_id") if isinstance(context.metadata.get("trace_id"), str) else uuid4().hex[:12]
        messages = list(context.messages)
        if not messages:
            messages.append(Message(role="system", content=options.system_prompt or SYSTEM_PROMPT))
        messages.append(Message(role="user", content=message))
        tool_ledger = ToolCallLedger(
            max_tool_calls=self.config.max_tool_calls_per_turn,
            max_read_only_tool_calls=self.config.max_read_only_tool_calls_per_turn,
        )

        for step in iter_steps(self.config.max_steps):
            self.harness.update_context_budget(messages)
            messages = self.harness.prepare_context(messages, context.metadata)
            self.harness.update_context_budget(messages)
            yield LoopEvent(
                type=LoopEventTypes.STEP_START,
                data={LoopEventData.STEP: step, LoopEventData.MAX_STEPS: format_max_steps(self.config.max_steps)},
            )
            yield LoopEvent(type=LoopEventTypes.MODEL_WAIT, data={LoopEventData.CONTENT: "waiting for model response"})
            try:
                output, streamed = yield from self._complete_model(messages)
            except ModelRequestError as exc:
                observation = f"Model request failed: {exc}"
                messages.append(Message(role="user", content=observation))
                yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
                result = LoopResult(success=False, final_messages=messages)
                self.harness.trace_id = previous_trace_id
                return result
            if model_record := self.harness.latest_model_record():
                yield LoopEvent(
                    type=LoopEventTypes.MODEL_ROUTE,
                    data={
                        LoopEventData.PROVIDER: model_record.provider,
                        LoopEventData.MODEL: model_record.model,
                        LoopEventData.STATUS: model_record.status,
                        LoopEventData.FALLBACK_INDEX: model_record.fallback_index,
                        LoopEventData.ATTEMPT: model_record.attempt,
                    },
                )
            if not streamed:
                yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT, data={LoopEventData.CONTENT: output})
            messages.append(Message(role="assistant", content=output))

            model_observations: list[str] = []
            try:
                calls = self.harness.consolidate_tool_calls(parse_actions(output))
            except AgentFinished as exc:
                yield LoopEvent(type=LoopEventTypes.DONE, data={LoopEventData.REASON: str(exc)})
                result = LoopResult(success=True, final_messages=messages)
                self.harness.trace_id = previous_trace_id
                return result
            except FormatError as exc:
                if options.allow_final_text:
                    yield LoopEvent(type=LoopEventTypes.TURN_COMPLETE, data={LoopEventData.REASON: "final text"})
                    result = LoopResult(success=True, final_messages=messages)
                    self.harness.trace_id = previous_trace_id
                    return result
                observation = str(exc)
                append_observation(model_observations, observation, self.config.max_output_chars)
                yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
            else:
                calls = assign_tool_call_ids(calls, trace_id=self.harness.trace_id or "trace", step=step)
                calls, skipped_calls = tool_ledger.filter_before_execution(calls)
                for call in calls:
                    self.harness.record_tool_dispatch_start(call, step)
                    yield LoopEvent(
                        type=LoopEventTypes.TOOL_CALL_START,
                        data={LoopEventData.TOOL: call.name, LoopEventData.PAYLOAD: call.payload, LoopEventData.CALL_ID: call.call_id},
                    )
                for skipped in skipped_calls:
                    observation = skipped.result.as_observation()
                    output_artifact = self.harness.save_tool_observation_artifact(
                        skipped.call.call_id,
                        skipped.result,
                        self.config.model_observation_output_chars,
                    )
                    model_observation = skipped.result.as_model_observation(
                        self.config.model_observation_output_chars,
                        call_id=skipped.call.call_id,
                        output_artifact=output_artifact,
                    )
                    self.harness.record_tool_dispatch_result(
                        skipped.call,
                        skipped.result,
                        step,
                        output_artifact=output_artifact,
                        output_limit=self.config.model_observation_output_chars,
                    )
                    append_observation(model_observations, model_observation, self.config.max_output_chars)
                    yield LoopEvent(
                        type=LoopEventTypes.TOOL_CALL_RESULT,
                        data={LoopEventData.OBSERVATION: observation, LoopEventData.CALL_ID: skipped.call.call_id},
                    )
                try:
                    tool_observations = self.harness.execute_tools(calls) if calls else []
                except NonTerminatingAgentError as exc:
                    observation = str(exc)
                    append_observation(model_observations, observation, self.config.max_output_chars)
                    yield LoopEvent(type=LoopEventTypes.TOOL_CALL_RESULT, data={LoopEventData.OBSERVATION: observation})
                else:
                    for tool_observation in tool_observations:
                        tool_ledger.record_result(
                            ToolCall(name=tool_observation.action, payload=tool_observation.payload, call_id=tool_observation.call_id),
                            tool_observation.result,
                        )
                        observation = tool_observation.result.as_observation()
                        output_artifact = self.harness.save_tool_observation_artifact(
                            tool_observation.call_id,
                            tool_observation.result,
                            self.config.model_observation_output_chars,
                        )
                        model_observation = tool_observation.result.as_model_observation(
                            self.config.model_observation_output_chars,
                            call_id=tool_observation.call_id,
                            output_artifact=output_artifact,
                        )
                        self.harness.record_tool_dispatch_result(
                            ToolCall(name=tool_observation.action, payload=tool_observation.payload, call_id=tool_observation.call_id),
                            tool_observation.result,
                            step,
                            output_artifact=output_artifact,
                            output_limit=self.config.model_observation_output_chars,
                        )
                        append_observation(model_observations, model_observation, self.config.max_output_chars)
                        yield LoopEvent(
                            type=LoopEventTypes.TOOL_CALL_RESULT,
                            data={LoopEventData.OBSERVATION: observation, LoopEventData.CALL_ID: tool_observation.call_id},
                        )

            combined_observation = "\n\n".join(model_observations)
            messages.append(Message(role="user", content=combined_observation))
            supplemental_input = read_supplemental_input(options)
            if supplemental_input:
                messages.append(Message(role="user", content=f"User supplemental input during this task:\n{supplemental_input}"))

        messages.append(Message(role="user", content="Max steps reached. Stop and summarize current state."))
        yield LoopEvent(type=LoopEventTypes.MAX_STEPS, data={LoopEventData.MAX_STEPS: self.config.max_steps})
        result = LoopResult(success=False, final_messages=messages)
        self.harness.trace_id = previous_trace_id
        return result

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

    def _complete_model(self, messages: list[Message]) -> Generator[LoopEvent, None, tuple[str, bool]]:
        if not self.config.model_streaming:
            output = self.harness.complete(messages)
            yield from self._emit_segmented_model_output(output)
            return output, True
        stream = self.harness.stream_complete(messages)
        if stream is None:
            output = self.harness.complete(messages)
            yield from self._emit_segmented_model_output(output)
            return output, True
        chunks: list[str] = []
        for chunk in stream:
            if not chunk:
                continue
            chunks.append(chunk)
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT_CHUNK, data={LoopEventData.CONTENT: chunk})
        output = "".join(chunks)
        if output and not output.endswith("\n"):
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT_CHUNK, data={LoopEventData.CONTENT: "\n"})
        return output, True

    def _emit_segmented_model_output(self, output: str) -> Generator[LoopEvent, None, None]:
        segment_chars = self.config.model_output_segment_chars
        if segment_chars <= 0:
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT, data={LoopEventData.CONTENT: output})
            return
        for start in range(0, len(output), segment_chars):
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT_CHUNK, data={LoopEventData.CONTENT: output[start : start + segment_chars]})
        if output and not output.endswith("\n"):
            yield LoopEvent(type=LoopEventTypes.MODEL_OUTPUT_CHUNK, data={LoopEventData.CONTENT: "\n"})

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


def assign_tool_call_ids(calls: list[ToolCall], trace_id: str, step: int) -> list[ToolCall]:
    assigned: list[ToolCall] = []
    for index, call in enumerate(calls, start=1):
        call_id = call.call_id or f"{trace_id}-s{step}-t{index}"
        assigned.append(ToolCall(name=call.name, payload=call.payload, call_id=call_id))
    return assigned


def iter_steps(max_steps: int) -> Iterator[int]:
    if max_steps < 0:
        max_steps = 1
    elif max_steps == 0:
        max_steps = 10**9
    step = 1
    while step <= max_steps:
        yield step
        step += 1


def format_max_steps(max_steps: int) -> int | str:
    return max_steps if max_steps > 0 else "unlimited"


def print_event(event: LoopEvent) -> None:
    if event.type == LoopEventTypes.STEP_START:
        print(f"\n--- step {event.data[LoopEventData.STEP]}/{event.data[LoopEventData.MAX_STEPS]} ---")
    elif event.type == LoopEventTypes.MODEL_WAIT:
        print(f"[thinking] {event.data[LoopEventData.CONTENT]}...")
    elif event.type == LoopEventTypes.MODEL_ROUTE:
        print(format_model_route_event(event))
    elif event.type == LoopEventTypes.MODEL_OUTPUT_CHUNK:
        print(event.data[LoopEventData.CONTENT], end="", flush=True)
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
    else:
        print(f"\n[event:{event.type}] {event.data}")


def format_model_route_event(event: LoopEvent) -> str:
    fallback_index = int(event.data[LoopEventData.FALLBACK_INDEX])
    route = "primary" if fallback_index == 0 else f"fallback#{fallback_index}"
    return (
        f"[model] {event.data[LoopEventData.PROVIDER]}/{event.data[LoopEventData.MODEL]} "
        f"status={event.data[LoopEventData.STATUS]} route={route} attempt={event.data[LoopEventData.ATTEMPT]}"
    )


def append_observation(observations: list[str], observation: str, max_chars: int) -> None:
    current_size = sum(len(item) for item in observations)
    remaining = max_chars - current_size
    if remaining <= 0:
        if not observations or observations[-1] != OBSERVATION_TRUNCATED_MESSAGE:
            observations.append(OBSERVATION_TRUNCATED_MESSAGE)
        return
    if len(observation) <= remaining:
        observations.append(observation)
    elif remaining > 3:
        observations.append(observation[: remaining - 3] + "...")
    elif remaining > 0:
        observations.append(observation[:remaining])
