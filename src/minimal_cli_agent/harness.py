from __future__ import annotations

from dataclasses import dataclass

from minimal_cli_agent.context import CompactingContextManager
from minimal_cli_agent.constants import Tools
from minimal_cli_agent.environment import LocalEnvironment
from minimal_cli_agent.file_tools import (
    FileToolEnvironment,
    READ_FILE_SCHEMA,
    READ_FORWARD_SCHEMA,
    READ_TAIL_SCHEMA,
    SEARCH_SCHEMA,
    WRITE_FILE_SCHEMA,
    read_file_validator,
    read_forward_validator,
    read_tail_validator,
    search_validator,
    write_file_validator,
)
from minimal_cli_agent.interfaces import ContextManager, Model, SessionStore
from minimal_cli_agent.model import ChatModel
from minimal_cli_agent.policy import ShellPermissionPolicy
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
    ) -> None:
        self.config = config
        self.model = model or ChatModel(config)
        self.context_manager = context_manager or CompactingContextManager(config, summarizer=self.model)
        self.session_store = session_store
        self.policy = ShellPermissionPolicy(config, audit_recorder=self.record_event)
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
            )
        )
        self.tool_pipeline = ToolExecutionPipeline(registry=self.tool_registry, permission_policy=self.policy)

    def load_messages(self) -> list[Message]:
        return self.session_store.load() if self.session_store else []

    def save_messages(self, messages: list[Message]) -> None:
        if self.session_store:
            self.session_store.save(messages)

    def record_event(self, kind: str, data: dict) -> None:
        if self.session_store:
            self.session_store.append_event(EventRecord(kind=kind, data=data))

    def prepare_context(self, messages: list[Message]) -> list[Message]:
        return self.context_manager.prepare(messages)

    def complete(self, messages: list[Message]) -> str:
        return self.model.complete(messages)

    def execute_shell(self, command: str) -> Observation:
        call = ToolCall(name=Tools.SHELL, payload=command)
        return self.execute_tool(call)

    def execute_tool(self, call: ToolCall) -> Observation:
        return Observation(action=call.name, payload=call.payload, result=self.tool_pipeline.execute(call))
