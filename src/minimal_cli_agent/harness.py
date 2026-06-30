from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import time
from typing import cast

from minimal_cli_agent.context import CompactingContextManager
from minimal_cli_agent.constants import EventKinds, Tools
from minimal_cli_agent.environment import LocalEnvironment
from minimal_cli_agent.file_tools import (
    FileToolEnvironment,
    EDIT_FILE_SCHEMA,
    FILE_INFO_OUTPUT_SCHEMA,
    FILE_INFO_SCHEMA,
    READ_FILE_SCHEMA,
    READ_FORWARD_SCHEMA,
    READ_TAIL_SCHEMA,
    SEARCH_SCHEMA,
    WRITE_FILE_SCHEMA,
    edit_file_validator,
    file_info_validator,
    read_file_validator,
    read_forward_validator,
    read_tail_validator,
    search_validator,
    write_file_validator,
)
from minimal_cli_agent.interfaces import ContextManager, Model, SessionStore
from minimal_cli_agent.model_gateway import ModelGateway, UsageRecord
from minimal_cli_agent.mcp_tools import load_mcp_config, register_mcp_tools
from minimal_cli_agent.policy import ConfirmationHandler, ShellPermissionPolicy
from minimal_cli_agent.plugins import load_plugin_mcp_configs
from minimal_cli_agent.tool_pipeline import ToolExecutionPipeline
from minimal_cli_agent.tool_registry import ToolRegistry, ToolSpec
from minimal_cli_agent.types import AgentConfig, CommandResult, EventRecord, Message, ToolCall


@dataclass
class Observation:
    action: str
    payload: str
    result: CommandResult

    def to_message(self) -> Message:
        return Message(role="user", content=self.result.as_observation())


class AgentHarness:
    def __init__(
        self,
        config: AgentConfig,
        model: Model | None = None,
        context_manager: ContextManager | None = None,
        session_store: SessionStore | None = None,
        tool_registry: ToolRegistry | None = None,
        confirmation_handler: ConfirmationHandler | None = None,
    ) -> None:
        self.config = config
        self.model = model or ModelGateway(config)
        self.context_manager = context_manager or CompactingContextManager(config, summarizer=self.model)
        self.session_store = session_store
        self.trace_id: str | None = None
        self.policy = ShellPermissionPolicy(config, audit_recorder=self.record_event, confirmation_handler=confirmation_handler)
        self.environment = LocalEnvironment(config)
        self.file_environment = FileToolEnvironment(config)
        self.tool_registry = tool_registry or ToolRegistry()
        self.tool_registry.register(
            ToolSpec(
                name=Tools.SHELL,
                description="Execute a non-interactive shell command in the configured workspace.",
                handler=self.environment.execute,
                expected_format=Tools.SHELL_EXPECTED_FORMAT,
                aliases=Tools.SHELL_ALIASES,
                risk_level="high",
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.READ_FILE,
                description="Read a UTF-8 text file inside the configured workspace.",
                handler=self.file_environment.read_file,
                expected_format=Tools.READ_FILE_EXPECTED_FORMAT,
                aliases=Tools.READ_FILE_ALIASES,
                validator=read_file_validator,
                parameters_schema=READ_FILE_SCHEMA,
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.READ_TAIL,
                description="Read the last N lines from a UTF-8 text file inside the workspace without loading the whole file.",
                handler=self.file_environment.read_tail,
                expected_format=Tools.READ_TAIL_EXPECTED_FORMAT,
                aliases=Tools.READ_TAIL_ALIASES,
                validator=read_tail_validator,
                parameters_schema=READ_TAIL_SCHEMA,
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.READ_FORWARD,
                description="Read a bounded UTF-8 byte range from a file inside the workspace.",
                handler=self.file_environment.read_forward,
                expected_format=Tools.READ_FORWARD_EXPECTED_FORMAT,
                aliases=Tools.READ_FORWARD_ALIASES,
                validator=read_forward_validator,
                parameters_schema=READ_FORWARD_SCHEMA,
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.FILE_INFO,
                description="Summarize file metadata, binary status, hash, and a small safe preview inside the workspace.",
                handler=self.file_environment.file_info,
                expected_format=Tools.FILE_INFO_EXPECTED_FORMAT,
                aliases=Tools.FILE_INFO_ALIASES,
                validator=file_info_validator,
                parameters_schema=FILE_INFO_SCHEMA,
                output_schema=FILE_INFO_OUTPUT_SCHEMA,
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.SEARCH,
                description="Search workspace text files with top-k and file-count limits.",
                handler=self.file_environment.search,
                expected_format=Tools.SEARCH_EXPECTED_FORMAT,
                aliases=Tools.SEARCH_ALIASES,
                validator=search_validator,
                parameters_schema=SEARCH_SCHEMA,
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.WRITE_FILE,
                description="Write a UTF-8 text file inside the configured workspace.",
                handler=self.file_environment.write_file,
                expected_format=Tools.WRITE_FILE_EXPECTED_FORMAT,
                aliases=Tools.WRITE_FILE_ALIASES,
                validator=write_file_validator,
                parameters_schema=WRITE_FILE_SCHEMA,
                risk_level="medium",
            )
        )
        self.tool_registry.register(
            ToolSpec(
                name=Tools.EDIT_FILE,
                description="Replace a 1-based inclusive line range inside a UTF-8 workspace file.",
                handler=self.file_environment.edit_file,
                expected_format=Tools.EDIT_FILE_EXPECTED_FORMAT,
                aliases=Tools.EDIT_FILE_ALIASES,
                validator=edit_file_validator,
                parameters_schema=EDIT_FILE_SCHEMA,
                risk_level="medium",
            )
        )
        mcp_configs = []
        if self.config.mcp_config is not None:
            mcp_configs.extend(load_mcp_config(self.config.mcp_config))
        if self.config.plugin_paths:
            mcp_configs.extend(load_plugin_mcp_configs(self.config.plugin_paths))
        if mcp_configs:
            register_mcp_tools(self.tool_registry, mcp_configs, audit_recorder=self.record_event)
        self.tool_pipeline = ToolExecutionPipeline(
            registry=self.tool_registry,
            permission_policy=self.policy,
            audit_recorder=self.record_event,
        )

    def load_messages(self) -> list[Message]:
        return self.session_store.load() if self.session_store else []

    def save_messages(self, messages: list[Message]) -> None:
        if self.session_store:
            self.session_store.save(messages)

    def record_event(self, kind: str, data: dict) -> None:
        if self.session_store:
            if self.trace_id and "trace_id" not in data:
                data = {**data, "trace_id": self.trace_id}
            self.session_store.append_event(EventRecord(kind=kind, data=data))

    def prepare_context(self, messages: list[Message]) -> list[Message]:
        return self.context_manager.prepare(messages)

    def complete(self, messages: list[Message]) -> str:
        return self.model.complete(messages)

    def stream_complete(self, messages: list[Message]) -> Iterator[str] | None:
        supports_streaming = getattr(self.model, "supports_streaming", None)
        if callable(supports_streaming) and not supports_streaming():
            return None
        stream_method = getattr(self.model, "stream_complete", None)
        if callable(stream_method):
            return cast(Callable[[list[Message]], Iterator[str]], stream_method)(messages)
        return None

    def latest_model_record(self) -> UsageRecord | None:
        if isinstance(self.model, ModelGateway):
            return self.model.last_record
        return None

    def execute_shell(self, command: str) -> Observation:
        call = ToolCall(name=Tools.SHELL, payload=command)
        return self.execute_tool(call)

    def execute_tool(self, call: ToolCall) -> Observation:
        return Observation(action=call.name, payload=call.payload, result=self.tool_pipeline.execute(call))

    def execute_tools(self, calls: list[ToolCall]) -> list[Observation]:
        observations: list[Observation] = []
        for batch in bucket_tool_calls(calls, self.is_parallel_safe):
            observations.extend(self._execute_batch(batch))
        return observations

    def consolidate_tool_calls(self, calls: list[ToolCall]) -> list[ToolCall]:
        consolidated: list[ToolCall] = []
        seen_read_keys: set[tuple[str, str]] = set()
        for call in calls:
            key = self.read_only_dedupe_key(call)
            if key is not None:
                if key in seen_read_keys:
                    continue
                seen_read_keys.add(key)
            consolidated.append(call)
        return consolidated

    def read_only_dedupe_key(self, call: ToolCall) -> tuple[str, str] | None:
        try:
            spec = self.tool_registry.require(call.name)
        except KeyError:
            return None
        if spec.name not in Tools.READ_ONLY:
            return None
        if spec.validate(call.payload) is not None:
            return None
        prepared = spec.prepare_payload(call.payload)
        return (spec.name, canonical_payload(prepared))

    def _execute_batch(self, calls: list[ToolCall]) -> list[Observation]:
        started = time.monotonic()
        parallel = len(calls) > 1
        try:
            observations = self._execute_parallel_batch(calls) if parallel else [self.execute_tool(calls[0])]
        except Exception as exc:
            self.record_event(
                EventKinds.TOOL_BATCH,
                {
                    "actions": [call.name for call in calls],
                    "parallel": parallel,
                    "size": len(calls),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "status": "error",
                    "error": str(exc),
                },
            )
            raise
        self.record_event(
            EventKinds.TOOL_BATCH,
            {
                "actions": [observation.action for observation in observations],
                "parallel": parallel,
                "size": len(calls),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "status": "ok",
                "exit_codes": [observation.result.exit_code for observation in observations],
            },
        )
        return observations

    def is_parallel_safe(self, call: ToolCall) -> bool:
        try:
            spec = self.tool_registry.require(call.name)
        except KeyError:
            return False
        return spec.name in Tools.READ_ONLY

    def _execute_parallel_batch(self, calls: list[ToolCall]) -> list[Observation]:
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = [executor.submit(self.execute_tool, call) for call in calls]
            observations: list[Observation] = []
            try:
                for future in futures:
                    observations.append(future.result())
            except Exception:
                for future in futures:
                    future.cancel()
                raise
            return observations


def bucket_tool_calls(
    calls: list[ToolCall],
    is_parallel_safe: Callable[[ToolCall], bool] | None = None,
) -> list[list[ToolCall]]:
    is_parallel_safe = is_parallel_safe or (lambda call: call.name in Tools.READ_ONLY)
    buckets: list[list[ToolCall]] = []
    read_bucket: list[ToolCall] = []
    for call in calls:
        if is_parallel_safe(call):
            read_bucket.append(call)
            continue
        if read_bucket:
            buckets.append(read_bucket)
            read_bucket = []
        buckets.append([call])
    if read_bucket:
        buckets.append(read_bucket)
    return buckets


def canonical_payload(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
