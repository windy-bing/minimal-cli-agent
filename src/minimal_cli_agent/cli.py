from __future__ import annotations

import copy
from dataclasses import dataclass, field
from dataclasses import replace
from importlib import import_module
import json
import os
import select
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

try:
    import readline
except ImportError:  # pragma: no cover - readline is platform-dependent.
    readline = None

from minimal_cli_agent.agent import Agent, print_event
from minimal_cli_agent.cli_config import (
    CONFIG_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION_KEY,
    bool_config_value,
    build_parser,
    build_session_store,
    choose_config_value,
    default_user_config_path,
    detect_explicit_options,
    load_cli_defaults,
    merge_paths,
    normalize_model_fallbacks,
    normalize_string_list,
    optional_float,
    optional_int,
    optional_str,
    parse_json_object,
    parse_model_routes,
    resolve_default_session_path,
    resolve_optional_path,
    resolve_path_option,
    validate_cli_defaults,
)
from minimal_cli_agent.cli_events import parse_events_query
from minimal_cli_agent.cli_format import format_duration, is_plan_mode_write_block, print_compact_event, render_prompt
from minimal_cli_agent.constants import Defaults, EventKinds, InteractiveCommands, LoopEventData, LoopEventTypes, PermissionModes, PolicyPresets, Profiles, Providers, ToolDecisionKinds, ToolPayloadFields, Tools
from minimal_cli_agent.context import estimate_context_tokens, total_message_chars
from minimal_cli_agent.exceptions import AgentError, ConfigurationError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.interfaces import SessionStore
from minimal_cli_agent.logging_utils import configure_logging
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.memory import JsonSessionStore, SQLiteSessionStore

SessionStoreType = SessionStore | None
from minimal_cli_agent.mcp_tools import load_mcp_config
from minimal_cli_agent.model_gateway import ModelGateway, estimate_message_tokens
from minimal_cli_agent.plan import PLAN_METADATA_KEY, PlanArtifact, build_plan_prompt, create_plan_artifact, extract_plan_paths, format_plan_artifact, format_plan_execution_context
from minimal_cli_agent.plugins import (
    discover_plugin_paths,
    load_plugin_manifest,
    load_plugin_skill_paths,
    resolve_plugin_path,
    resolve_plugin_paths,
)
from minimal_cli_agent.prompts import INTERACTIVE_SYSTEM_PROMPT, SYSTEM_PROMPT
from minimal_cli_agent.profiles import resolve_profile
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.skills import build_system_prompt, discover_skill_paths, resolve_skill_path, resolve_skill_paths
from minimal_cli_agent.subagent import SUBAGENT_ROLES, SubAgentRunner
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, Message, ToolCall, ToolDecision
from minimal_cli_agent.workflow import (
    WORKFLOW_METADATA_KEY,
    WorkflowArtifact,
    add_workflow_delegation,
    add_workflow_step,
    complete_workflow_step,
    create_workflow,
    format_workflow_artifact,
    merge_workflow_delegations,
    schedule_next_workflow_step,
    verify_workflow_step,
    workflow_status_counts,
)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)
    explicit_options = detect_explicit_options(raw_argv)
    local_defaults = load_cli_defaults(args.cwd.resolve(), args.config_file.resolve() if args.config_file else None)

    try:
        cwd = resolve_path_option(
            choose_config_value("cwd", args.cwd, local_defaults, explicit_options, ("AGENT_CWD",)),
            Path.cwd(),
        )
        skill_values = choose_config_value("skill", args.skill, local_defaults, explicit_options, ("AGENT_SKILL", "AGENT_SKILLS"))
        skill_inputs = normalize_string_list(skill_values)
        plugin_values = choose_config_value("plugin", args.plugin, local_defaults, explicit_options, ("AGENT_PLUGIN", "AGENT_PLUGINS"))
        plugin_inputs = normalize_string_list(plugin_values)
        plugin_discovery = bool_config_value(
            choose_config_value(
                "plugin_discovery",
                args.plugin_discovery,
                local_defaults,
                explicit_options,
                ("AGENT_PLUGIN_DISCOVERY",),
            ),
            default=True,
        )
        discovered_plugin_paths = discover_plugin_paths(cwd.resolve()) if plugin_discovery else ()
        explicit_plugin_paths = resolve_plugin_paths(plugin_inputs, cwd.resolve())
        plugin_paths = merge_paths(discovered_plugin_paths, explicit_plugin_paths)
        plugin_skill_paths = load_plugin_skill_paths(plugin_paths)
        fallback_values = choose_config_value(
            "model_fallback",
            args.model_fallback,
            local_defaults,
            explicit_options,
            ("AGENT_MODEL_FALLBACKS",),
        )
        session_db_path = None if args.no_session else resolve_optional_path(choose_config_value("session_db", args.session_db, local_defaults, explicit_options, ("AGENT_SESSION_DB",)), cwd)
        session_path = None if session_db_path is not None else resolve_default_session_path(args, explicit_options, local_defaults, cwd)
        profile = optional_str(choose_config_value("profile", args.profile, local_defaults, explicit_options, ("AGENT_PROFILE",)))
        summarize_context = bool_config_value(
            choose_config_value(
                "summarize_context",
                args.summarize_context,
                local_defaults,
                explicit_options,
                ("AGENT_SUMMARIZE_CONTEXT",),
            ),
            default=True,
        )
        config = resolve_profile(AgentConfig(
            provider=str(choose_config_value("provider", args.provider, local_defaults, explicit_options, ("AGENT_PROVIDER",))),
            model=str(choose_config_value("model", args.model, local_defaults, explicit_options, ("AGENT_MODEL",))),
            base_url=str(choose_config_value("base_url", args.base_url, local_defaults, explicit_options, ("AGENT_BASE_URL",))),
            api_key=optional_str(choose_config_value("api_key", args.api_key, local_defaults, explicit_options, ("AGENT_API_KEY",))),
            cwd=cwd.resolve(),
            max_steps=int(choose_config_value("max_steps", args.max_steps, local_defaults, explicit_options, ("AGENT_MAX_STEPS",))),
            command_timeout=int(choose_config_value("timeout", args.timeout, local_defaults, explicit_options, ("AGENT_COMMAND_TIMEOUT",))),
            shell_kind=str(choose_config_value("shell", args.shell, local_defaults, explicit_options, ("AGENT_SHELL",))),
            sandbox_kind=str(choose_config_value("sandbox", args.sandbox, local_defaults, explicit_options, ("AGENT_SANDBOX",))),
            sandbox_image=str(choose_config_value("sandbox_image", args.sandbox_image, local_defaults, explicit_options, ("AGENT_SANDBOX_IMAGE",))),
            sandbox_network=str(choose_config_value("sandbox_network", args.sandbox_network, local_defaults, explicit_options, ("AGENT_SANDBOX_NETWORK",))),
            sandbox_read_only=bool_config_value(choose_config_value("sandbox_read_only", args.sandbox_read_only, local_defaults, explicit_options, ("AGENT_SANDBOX_READ_ONLY",)), default=False),
            model_timeout=int(choose_config_value("model_timeout", args.model_timeout, local_defaults, explicit_options, ("AGENT_MODEL_TIMEOUT",))),
            model_streaming=bool_config_value(choose_config_value("model_streaming", args.model_streaming, local_defaults, explicit_options, ("AGENT_MODEL_STREAMING",)), default=False),
            model_output_segment_chars=max(0, int(choose_config_value("model_output_segment_chars", args.model_output_segment_chars, local_defaults, explicit_options, ("AGENT_MODEL_OUTPUT_SEGMENT_CHARS",)))),
            ollama_options=parse_json_object(choose_config_value("ollama_options", args.ollama_options, local_defaults, explicit_options, ("AGENT_OLLAMA_OPTIONS",)), "ollama_options"),
            model_fallbacks=parse_model_routes(normalize_model_fallbacks(fallback_values)),
            model_max_retries=int(choose_config_value("model_max_retries", args.model_max_retries, local_defaults, explicit_options, ("AGENT_MODEL_MAX_RETRIES",))),
            model_max_concurrency=int(choose_config_value("model_max_concurrency", args.model_max_concurrency, local_defaults, explicit_options, ("AGENT_MODEL_MAX_CONCURRENCY",))),
            model_queue_timeout=float(choose_config_value("model_queue_timeout", args.model_queue_timeout, local_defaults, explicit_options, ("AGENT_MODEL_QUEUE_TIMEOUT",))),
            model_circuit_failure_threshold=int(choose_config_value("model_circuit_failure_threshold", args.model_circuit_failure_threshold, local_defaults, explicit_options, ("AGENT_MODEL_CIRCUIT_FAILURE_THRESHOLD",))),
            model_circuit_cooldown=float(choose_config_value("model_circuit_cooldown", args.model_circuit_cooldown, local_defaults, explicit_options, ("AGENT_MODEL_CIRCUIT_COOLDOWN",))),
            model_price_input_per_1m=float(choose_config_value("model_price_input_per_1m", args.model_price_input_per_1m, local_defaults, explicit_options, ("AGENT_MODEL_PRICE_INPUT_PER_1M",))),
            model_price_output_per_1m=float(choose_config_value("model_price_output_per_1m", args.model_price_output_per_1m, local_defaults, explicit_options, ("AGENT_MODEL_PRICE_OUTPUT_PER_1M",))),
            usage_ledger_path=resolve_optional_path(choose_config_value("usage_ledger", args.usage_ledger, local_defaults, explicit_options, ("AGENT_USAGE_LEDGER",)), cwd),
            usage_subject=str(choose_config_value("usage_subject", args.usage_subject, local_defaults, explicit_options, ("AGENT_USAGE_SUBJECT",))),
            usage_tenant=str(choose_config_value("usage_tenant", args.usage_tenant, local_defaults, explicit_options, ("AGENT_USAGE_TENANT",))),
            max_input_tokens=optional_int(choose_config_value("max_input_tokens", args.max_input_tokens, local_defaults, explicit_options, ("AGENT_MAX_INPUT_TOKENS",))),
            max_output_tokens=optional_int(choose_config_value("max_output_tokens", args.max_output_tokens, local_defaults, explicit_options, ("AGENT_MAX_OUTPUT_TOKENS",))),
            max_request_tokens=optional_int(choose_config_value("max_request_tokens", args.max_request_tokens, local_defaults, explicit_options, ("AGENT_MAX_REQUEST_TOKENS",))),
            daily_token_limit=optional_int(choose_config_value("daily_token_limit", args.daily_token_limit, local_defaults, explicit_options, ("AGENT_DAILY_TOKEN_LIMIT",))),
            monthly_token_limit=optional_int(choose_config_value("monthly_token_limit", args.monthly_token_limit, local_defaults, explicit_options, ("AGENT_MONTHLY_TOKEN_LIMIT",))),
            max_request_cost=optional_float(choose_config_value("max_request_cost", args.max_request_cost, local_defaults, explicit_options, ("AGENT_MAX_REQUEST_COST",))),
            daily_cost_limit=optional_float(choose_config_value("daily_cost_limit", args.daily_cost_limit, local_defaults, explicit_options, ("AGENT_DAILY_COST_LIMIT",))),
            monthly_cost_limit=optional_float(choose_config_value("monthly_cost_limit", args.monthly_cost_limit, local_defaults, explicit_options, ("AGENT_MONTHLY_COST_LIMIT",))),
            prompt_version=str(choose_config_value("prompt_version", args.prompt_version, local_defaults, explicit_options, ("AGENT_PROMPT_VERSION",))),
            bill_failed_requests=bool_config_value(choose_config_value("bill_failed_requests", args.bill_failed_requests, local_defaults, explicit_options, ("AGENT_BILL_FAILED_REQUESTS",)), default=False),
            permission_mode=str(choose_config_value("permission", args.permission, local_defaults, explicit_options, ("AGENT_PERMISSION",))),
            allow_network=bool_config_value(choose_config_value("allow_network", args.allow_network, local_defaults, explicit_options, ("AGENT_ALLOW_NETWORK",)), default=False),
            policy_file=resolve_optional_path(choose_config_value("policy_file", args.policy_file, local_defaults, explicit_options, ("AGENT_POLICY_FILE",)), cwd),
            policy_preset=str(choose_config_value("policy_preset", args.policy_preset, local_defaults, explicit_options, ("AGENT_POLICY_PRESET",))),
            mcp_config=resolve_optional_path(choose_config_value("mcp_config", args.mcp_config, local_defaults, explicit_options, ("AGENT_MCP_CONFIG",)), cwd),
            plugin_paths=plugin_paths,
            skill_paths=merge_paths(resolve_skill_paths(skill_inputs, cwd.resolve()), plugin_skill_paths),
            summarize_context=summarize_context,
            model_context_tokens=optional_int(choose_config_value("model_context_tokens", args.model_context_tokens, local_defaults, explicit_options, ("AGENT_MODEL_CONTEXT_TOKENS",))),
            context_compression_ratio=float(choose_config_value("context_compression_ratio", args.context_compression_ratio, local_defaults, explicit_options, ("AGENT_CONTEXT_COMPRESSION_RATIO",))),
        ), profile, explicit_options=explicit_options)
        if args.show_config:
            print(f"profile: {profile or '<none>'}")
            print(f"provider: {config.provider}")
            print(f"model: {config.model}")
            print(f"base_url: {redact_text(config.base_url)}")
            print(f"api_key_present: {bool(config.api_key)}")
            print(f"api_key_length: {len(config.api_key or '')}")
            print(f"fallback_routes: {len(config.model_fallbacks)}")
            print(f"shell: {config.shell_kind}")
            print(f"sandbox: {config.sandbox_kind}")
            print(f"sandbox_image: {config.sandbox_image}")
            print(f"sandbox_network: {config.sandbox_network}")
            print(f"sandbox_read_only: {config.sandbox_read_only}")
            print(f"policy_preset: {config.policy_preset}")
            print(f"model_streaming: {config.model_streaming}")
            print(f"model_output_segment_chars: {config.model_output_segment_chars}")
            print(f"ollama_options: {redact_text(json.dumps(config.ollama_options, ensure_ascii=False, sort_keys=True)) if config.ollama_options else '{}'}")
            print(f"model_price_input_per_1m: {config.model_price_input_per_1m}")
            print(f"model_price_output_per_1m: {config.model_price_output_per_1m}")
            print(f"usage_ledger: {config.usage_ledger_path or '<none>'}")
            print(f"usage_subject: {config.usage_subject}")
            print(f"usage_tenant: {config.usage_tenant}")
            print(f"model_context_tokens: {config.model_context_tokens or '<none>'}")
            print(f"context_compression_ratio: {config.context_compression_ratio}")
            print(f"session: {session_path or '<none>'}")
            print(f"session_db: {session_db_path or '<none>'}")
            plugins = ", ".join(path.parent.name for path in config.plugin_paths) if config.plugin_paths else "<none>"
            print(f"plugins: {plugins}")
            return 0

        task = " ".join(args.task).strip()
        session_store = build_session_store(session_path=session_path, session_db_path=session_db_path)
        harness = AgentHarness(config=config, session_store=session_store)
        context = ChatContext(messages=session_store.load() if session_store else [])
        if session_store:
            plan = session_store.load_plan()
            if plan is not None:
                context.metadata[PLAN_METADATA_KEY] = plan
            workflow = session_store.load_workflow()
            if workflow is not None:
                context.metadata[WORKFLOW_METADATA_KEY] = workflow
        agent = Agent(config=config, harness=harness)
        if args.interactive or not task:
            return run_interactive(agent, context, session_store, first_message=task or None)
        return run_turn(agent, task, context, session_store)
    except AgentError as exc:
        print(f"error: {exc}")
        return 1


def run_turn(
    agent: Agent,
    message: str,
    context: ChatContext,
    session_store: SessionStoreType = None,
    options: LoopOptions | None = None,
) -> int:
    return run_turn_with_summary(agent, message, context, session_store, options, compact_output=False).exit_code


@dataclass(frozen=True)
class TurnExecutionSummary:
    exit_code: int
    plan_blocked: bool = False
    elapsed_seconds: float = 0.0


def run_turn_with_summary(
    agent: Agent,
    message: str,
    context: ChatContext,
    session_store: SessionStoreType = None,
    options: LoopOptions | None = None,
    compact_output: bool = False,
) -> TurnExecutionSummary:
    started = time.monotonic()
    options = with_configured_skills(agent.config, options)
    options = with_active_plan_context(context, options)
    upsert_system_prompt(context, options.system_prompt)
    stream = agent.chat_stream(message, context, options)
    plan_blocked = False
    plan_hook = build_plan_decision_hook(context)
    if plan_hook is not None:
        agent.harness.tool_pipeline.hooks.decision_hooks.append(plan_hook)
    try:
        while True:
            try:
                event = next(stream)
            except KeyboardInterrupt:
                print("\nTurn interrupted.")
                return TurnExecutionSummary(exit_code=130, plan_blocked=plan_blocked, elapsed_seconds=time.monotonic() - started)
            except AgentError as exc:
                print(f"error: {exc}")
                return TurnExecutionSummary(exit_code=1, plan_blocked=plan_blocked, elapsed_seconds=time.monotonic() - started)
            except StopIteration as exc:
                result = exc.value
                context.messages = result.final_messages
                if session_store:
                    session_store.save(context.messages)
                return TurnExecutionSummary(
                    exit_code=0 if result.success else 1,
                    plan_blocked=plan_blocked,
                    elapsed_seconds=time.monotonic() - started,
                )
            if compact_output:
                print_compact_event(event)
            else:
                print_event(event)
            if event.type == LoopEventTypes.TOOL_CALL_RESULT and is_plan_mode_write_block(event.data.get(LoopEventData.OBSERVATION, "")):
                plan_blocked = True
    finally:
        if plan_hook is not None and plan_hook in agent.harness.tool_pipeline.hooks.decision_hooks:
            agent.harness.tool_pipeline.hooks.decision_hooks.remove(plan_hook)


def with_active_plan_context(context: ChatContext, options: LoopOptions) -> LoopOptions:
    plan = active_plan_from_context(context)
    if plan is None:
        return options
    prompt = options.system_prompt or SYSTEM_PROMPT
    prompt = f"{prompt}\n\n{format_plan_execution_context(plan)}"
    return LoopOptions(
        allow_final_text=options.allow_final_text,
        system_prompt=prompt,
        interrupt_input_reader=options.interrupt_input_reader,
    )


def active_plan_from_context(context: ChatContext) -> PlanArtifact | None:
    raw = context.metadata.get(PLAN_METADATA_KEY)
    if isinstance(raw, PlanArtifact):
        return raw
    if isinstance(raw, dict):
        return PlanArtifact.from_dict(raw)
    return None


def build_plan_decision_hook(context: ChatContext):
    plan = active_plan_from_context(context)
    if plan is None:
        return None
    allowed_paths = extract_plan_paths(plan)
    if not allowed_paths:
        return None

    def hook(call: ToolCall, decision: ToolDecision) -> ToolDecision | None:
        if call.name not in Tools.WRITERS:
            return None
        path = payload_path(call.payload)
        if path and is_path_allowed_by_plan(path, allowed_paths):
            return None
        allowed = ", ".join(allowed_paths)
        return ToolDecision(
            kind=ToolDecisionKinds.DENY,
            reason=f"Active plan restricts file edits to planned paths: {allowed}",
        )

    return hook


def payload_path(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get(ToolPayloadFields.PATH, ""))


def is_path_allowed_by_plan(path: str, allowed_paths: tuple[str, ...]) -> bool:
    normalized = Path(path).as_posix().lstrip("./")
    for allowed in allowed_paths:
        allowed_normalized = Path(allowed).as_posix().lstrip("./")
        if normalized == allowed_normalized or normalized.startswith(f"{allowed_normalized}/"):
            return True
    return False


def run_interactive(
    agent: Agent,
    context: ChatContext,
    session_store: SessionStoreType = None,
    first_message: str | None = None,
) -> int:
    session = InteractiveSession(
        agent=agent,
        context=context,
        session_store=session_store,
        user_history=build_user_history(context.messages),
    )
    configure_readline_history(session.user_history)
    print("minimal-agent interactive mode. Type /help for commands, /exit to stop.")
    pending = first_message
    while True:
        if pending is None:
            try:
                pending = read_interactive_prompt(session).strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print("\nInput cleared. Type /exit to stop.")
                pending = None
                continue

        if pending in InteractiveCommands.EXIT_COMMANDS:
            return 0
        try:
            command_result = handle_interactive_command(pending, session)
        except AgentError as exc:
            print(f"error: {exc}")
            pending = None
            continue
        if command_result.should_exit:
            return 0
        if command_result.handled:
            if command_result.replay_message:
                pending = command_result.replay_message
                continue
            pending = None
            continue
        if pending == InteractiveCommands.QUICK_HINT:
            print_quick_command_hint()
        elif pending == InteractiveCommands.HELP:
            print_interactive_help()
        elif pending.startswith("/") and not is_known_slash_command(pending):
            print(f"Unknown command: {pending}")
            print_quick_command_hint()
        elif pending:
            remember_user_prompt(session, pending)
            summary = run_turn_with_summary(
                session.agent,
                pending,
                session.context,
                session.session_store,
                LoopOptions(
                    allow_final_text=True,
                    system_prompt=INTERACTIVE_SYSTEM_PROMPT,
                    interrupt_input_reader=read_supplemental_stdin,
                ),
                compact_output=True,
            )
            print(f"[time] task elapsed {format_duration(summary.elapsed_seconds)}")
            if should_retry_with_auto_edit(session, pending, summary):
                pending = retry_in_auto_edit(session, pending)
                continue
            if summary.exit_code == 130:
                pending = None
                continue
            if summary.exit_code != 0:
                print("Turn failed. You can retry, adjust options, or type /exit.")
        pending = None


def should_retry_with_auto_edit(session: InteractiveSession, message: str, summary: TurnExecutionSummary) -> bool:
    if not summary.plan_blocked:
        return False
    if session.agent.config.permission_mode != PermissionModes.PLAN:
        return False
    answer = input("\nPlan mode blocked required file edits. Switch to autoEdit and retry this request? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def retry_in_auto_edit(session: InteractiveSession, message: str) -> str:
    model = session.agent.harness.model
    session.agent.config.permission_mode = PermissionModes.AUTO_EDIT
    config = session.agent.config
    session.agent = Agent(
        config=config,
        harness=AgentHarness(config=config, model=model, session_store=session.session_store),
    )
    print("permission: autoEdit")
    return message


def read_supplemental_stdin() -> str | None:
    if not sys.stdin.isatty():
        return None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    line = sys.stdin.readline()
    text = line.strip()
    if text:
        print(f"[input] queued supplemental user input ({len(text)} chars)")
    return text or None


def read_interactive_prompt(session: InteractiveSession) -> str:
    if not sys.stdin.isatty():
        return input(render_prompt(session.agent.config))
    try:
        prompt_toolkit = import_module("prompt_toolkit")
        completion_module = import_module("prompt_toolkit.completion")
        formatted_text_module = import_module("prompt_toolkit.formatted_text")
        key_binding_module = import_module("prompt_toolkit.key_binding")
    except ImportError:
        return input(render_prompt(session.agent.config))
    key_bindings = key_binding_module.KeyBindings()

    @key_bindings.add("c-j")
    def insert_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    class SlashCommandCompleter(completion_module.Completer):  # type: ignore[misc]
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = str(document.text_before_cursor)
            for suggestion in interactive_command_suggestions(text):
                yield completion_module.Completion(
                    suggestion.value,
                    start_position=-suggestion.replace_length,
                    display=suggestion.value,
                    display_meta=suggestion.description,
                )

    return prompt_toolkit.prompt(
        formatted_text_module.ANSI(render_prompt(session.agent.config)),
        completer=SlashCommandCompleter(),
        complete_while_typing=True,
        enable_history_search=True,
        key_bindings=key_bindings,
        bottom_toolbar="Ctrl-R search history | Ctrl-J newline",
        reserve_space_for_menu=8,
    )


def command_prefix(text: str) -> str:
    stripped = text.lstrip()
    if not stripped.startswith("/") or " " in stripped:
        return ""
    return stripped


@dataclass(frozen=True)
class InteractiveSuggestion:
    value: str
    description: str
    replace_length: int

    def __iter__(self):
        yield self.value
        yield self.description


def interactive_command_suggestions(text: str) -> list[InteractiveSuggestion]:
    stripped = text.lstrip()
    prefix = command_prefix(text)
    if prefix:
        commands = [
            InteractiveSuggestion(command, description, len(prefix))
            for command, description in InteractiveCommands.DESCRIPTIONS.items()
            if command.startswith("/") and command != InteractiveCommands.QUICK_HINT
        ]
        if prefix == InteractiveCommands.QUICK_HINT:
            return commands
        return [suggestion for suggestion in commands if suggestion.value.startswith(prefix)]
    if not stripped.startswith("/") or stripped.endswith("  "):
        return []
    parts = stripped.split()
    if len(parts) < 2 and not stripped.endswith(" "):
        return []
    command = parts[0]
    token = "" if stripped.endswith(" ") else parts[-1]
    return [
        InteractiveSuggestion(value, description, len(token))
        for value, description in interactive_argument_suggestions(command)
        if value.startswith(token)
    ]


def interactive_argument_suggestions(command: str) -> list[tuple[str, str]]:
    return {
        InteractiveCommands.CONFIG: [
            ("show", "Show runtime config."),
            ("explain", "Explain config precedence."),
            ("capabilities", "Show runtime capabilities."),
            ("save", "Save runtime config."),
        ],
        InteractiveCommands.PROFILE: [(profile, "Switch model profile.") for profile in Profiles.ALL],
        InteractiveCommands.PROVIDER: [(provider, "Switch provider.") for provider in Providers.ALL],
        InteractiveCommands.PERMISSION: [(mode, "Switch permission mode.") for mode in PermissionModes.ALL],
        InteractiveCommands.POLICY: [("json", "Print policy report."), ("explain", "Explain a tool decision.")],
        InteractiveCommands.NETWORK: [("on", "Allow network shell commands."), ("off", "Block network shell commands.")],
        InteractiveCommands.SUMMARIZE: [("on", "Enable model context summaries."), ("off", "Disable model context summaries.")],
        InteractiveCommands.CONTEXT: [("status", "Show context size."), ("compact", "Compact context."), ("clear", "Clear context.")],
        InteractiveCommands.DOCTOR: [("json", "Print JSON health report.")],
        InteractiveCommands.DEBUG: [("prompt", "Show next prompt size."), ("bundle", "Write redacted diagnostic bundle.")],
        InteractiveCommands.METRICS: [("json", "Print JSON metrics report.")],
        InteractiveCommands.SESSION: [("stats", "Show session stats."), ("export", "Export session."), ("import", "Import session.")],
        InteractiveCommands.PLAN: [("show", "Show active plan."), ("clear", "Clear active plan.")],
        InteractiveCommands.WORKFLOW: [
            ("create", "Create workflow."),
            ("step", "Add step."),
            ("schedule", "Schedule next step."),
            ("done", "Mark step done."),
            ("merge", "Merge delegations."),
            ("verify", "Verify step."),
            ("wait", "Show workflow wait status."),
            ("show", "Show workflow."),
            ("clear", "Clear workflow."),
        ],
        InteractiveCommands.PLUGINS: [("load", "Load plugin by name or all.")],
        InteractiveCommands.SKILLS: [("load", "Load skill by name or all.")],
    }.get(command, [])


def configure_readline_history(history: list[str]) -> None:
    if readline is None:
        return
    try:
        readline.clear_history()
        for item in history:
            readline.add_history(item)
    except (AttributeError, OSError):
        return


def remember_user_prompt(session: InteractiveSession, prompt: str) -> None:
    if not is_user_prompt_history_entry(prompt):
        return
    if session.user_history and session.user_history[-1] == prompt:
        return
    session.user_history.append(prompt)
    if readline is not None:
        try:
            readline.add_history(prompt)
        except (AttributeError, OSError):
            return


def build_user_history(messages: list[Message], limit: int = 100) -> list[str]:
    history: list[str] = []
    for message in messages:
        if message.role != "user" or not is_user_prompt_history_entry(message.content):
            continue
        if not history or history[-1] != message.content:
            history.append(message.content)
    return history[-limit:]


def is_user_prompt_history_entry(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return False
    observation_prefixes = (
        "Command finished",
        "Command skipped",
        "Tool validation failed.",
        "Tool discovery failed.",
        "Context was compacted locally.",
        "Context summary from earlier messages:",
        "Max steps reached.",
    )
    return not any(stripped.startswith(prefix) for prefix in observation_prefixes)


def print_quick_command_hint() -> None:
    print("Commands: /help, /config, /profile, /permission, /policy, /metrics, /mcp, /plugin, /plugins, /skill, /skills, /context, /doctor, /debug, /history, /events, /session, /memory, /plan, /workflow, /delegate, /review, /exit")


def print_interactive_help() -> None:
    print("Interactive commands:")
    for command, description in InteractiveCommands.DESCRIPTIONS.items():
        print(f"  {command:<12} {description}")


@dataclass
class InteractiveSession:
    agent: Agent
    context: ChatContext
    session_store: SessionStoreType = None
    user_history: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InteractiveCommandResult:
    handled: bool = False
    should_exit: bool = False
    replay_message: str | None = None


def handle_interactive_command(raw: str, session: InteractiveSession) -> InteractiveCommandResult:
    if not raw.startswith("/") or raw == InteractiveCommands.QUICK_HINT:
        return InteractiveCommandResult()
    parts = raw.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == InteractiveCommands.HELP:
        print_interactive_help()
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.CONFIG:
        handle_config_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PROFILE:
        update_profile(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PROVIDER:
        update_provider(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.MODEL:
        update_model(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.BASE_URL:
        update_base_url(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PERMISSION:
        update_permission(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.POLICY:
        handle_policy_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.METRICS:
        handle_metrics_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.NETWORK:
        update_boolean_option(session, "allow_network", argument, "network shell commands")
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.SUMMARIZE:
        update_boolean_option(session, "summarize_context", argument, "model context summaries")
        rebuild_agent(session)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.CONTEXT:
        handle_context_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.DOCTOR:
        handle_doctor_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.DEBUG:
        handle_debug_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.HISTORY:
        replay = handle_history_command(session, argument)
        return InteractiveCommandResult(handled=True, replay_message=replay)
    if command == InteractiveCommands.EVENTS:
        handle_events_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.SESSION:
        handle_session_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PLAN:
        handle_plan_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.WORKFLOW:
        handle_workflow_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.MEMORY:
        handle_memory_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.DELEGATE:
        handle_delegate_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.REVIEW:
        review_target = argument or "the current project"
        prompt = (
            f"Review {review_target}. Prioritize bugs, behavioral risks, missing tests, "
            "and concrete improvement suggestions. Use tools if workspace facts are needed."
        )
        exit_code = run_turn(
            session.agent,
            prompt,
            session.context,
            session.session_store,
            LoopOptions(allow_final_text=True, system_prompt=INTERACTIVE_SYSTEM_PROMPT),
        )
        if exit_code != 0:
            print("Review failed. You can adjust options and retry.")
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.MCP:
        update_mcp_config(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PLUGIN:
        update_plugin(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.PLUGINS:
        handle_plugins_command(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.SKILL:
        update_skill(session, argument)
        return InteractiveCommandResult(handled=True)
    if command == InteractiveCommands.SKILLS:
        handle_skills_command(session, argument)
        return InteractiveCommandResult(handled=True)
    return InteractiveCommandResult()


def is_known_slash_command(raw: str) -> bool:
    command = raw.split(maxsplit=1)[0]
    return command in InteractiveCommands.DESCRIPTIONS


def update_profile(session: InteractiveSession, profile: str) -> None:
    if profile not in Profiles.ALL:
        print(f"Usage: {InteractiveCommands.PROFILE} {'|'.join(Profiles.ALL)}")
        return
    config = resolve_profile(session.agent.config, profile, explicit_options={"profile"})
    rebuild_agent(session, config)
    print(f"profile: {profile}")
    print_config(session.agent.config)


def update_provider(session: InteractiveSession, provider: str) -> None:
    if provider not in Providers.ALL:
        print(f"Usage: {InteractiveCommands.PROVIDER} {'|'.join(Providers.ALL)}")
        return
    session.agent.config.provider = provider
    rebuild_agent(session)
    print_config(session.agent.config)


def update_model(session: InteractiveSession, model: str) -> None:
    if not model:
        print(f"Usage: {InteractiveCommands.MODEL} <model-name>")
        return
    session.agent.config.model = model
    rebuild_agent(session)
    print_config(session.agent.config)


def handle_config_command(session: InteractiveSession, argument: str) -> None:
    action, value = split_command_argument(argument)
    if action in {"", "show"}:
        print_config(session.agent.config)
        if session.session_store:
            print(f"session: {session.session_store.path}")
            print(f"session_backend: {'sqlite' if isinstance(session.session_store, SQLiteSessionStore) else 'json'}")
        else:
            print("session: <none>")
        print(f"project_config: {session.agent.config.cwd / Defaults.LOCAL_CONFIG_FILE}")
        print(f"user_config: {default_user_config_path()}")
        return
    if action == "explain":
        print_config_explanation(session)
        return
    if action == "capabilities":
        print_runtime_capabilities(session)
        return
    if action == "save":
        target = value or "project"
        if target not in {"project", "user"}:
            print(f"Usage: {InteractiveCommands.CONFIG} save [project|user]")
            return
        path = default_user_config_path() if target == "user" else session.agent.config.cwd / Defaults.LOCAL_CONFIG_FILE
        save_cli_config(path, session)
        print(f"config saved: {path}")
        return
    print(f"Usage: {InteractiveCommands.CONFIG} [show|explain|capabilities|save [project|user]]")


def print_config_explanation(session: InteractiveSession) -> None:
    config = session.agent.config
    print("config_precedence:")
    print("1: command line flags")
    print("2: environment variables")
    print(f"3: project config {config.cwd / Defaults.LOCAL_CONFIG_FILE}")
    print(f"4: user config {default_user_config_path()}")
    print("5: built-in defaults")
    print(f"active_provider: {config.provider}")
    print(f"active_model: {config.model}")
    print(f"active_permission: {config.permission_mode}")
    print(f"active_sandbox: {config.sandbox_kind}")
    print(f"active_policy_preset: {config.policy_preset}")


def print_runtime_capabilities(session: InteractiveSession) -> None:
    config = session.agent.config
    session_backend = "none"
    if isinstance(session.session_store, SQLiteSessionStore):
        session_backend = "sqlite"
    elif isinstance(session.session_store, JsonSessionStore):
        session_backend = "json"
    searcher = getattr(session.session_store, "search_memory", None)
    print(f"session_backend: {session_backend}")
    print(f"retrieval_memory: {'yes' if searcher else 'no'}")
    print(f"session_export: {'yes' if getattr(session.session_store, 'export_data', None) else 'no'}")
    print(f"session_import: {'yes' if getattr(session.session_store, 'import_data', None) else 'no'}")
    print(f"plugins_loaded: {len(config.plugin_paths)}")
    print(f"skills_loaded: {len(config.skill_paths)}")
    print(f"sandbox: {config.sandbox_kind}")
    print(f"policy_preset: {config.policy_preset}")
    print(f"network_shell_commands: {'yes' if config.allow_network else 'no'}")


def update_base_url(session: InteractiveSession, base_url: str) -> None:
    if not base_url:
        print(f"Usage: {InteractiveCommands.BASE_URL} <url>")
        return
    session.agent.config.base_url = base_url
    rebuild_agent(session)
    print_config(session.agent.config)


def update_mcp_config(session: InteractiveSession, path_text: str) -> None:
    if not path_text:
        print(f"Usage: {InteractiveCommands.MCP} path/to/mcp.json")
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = session.agent.config.cwd / path
    session.agent.config.mcp_config = path.resolve()
    rebuild_agent(session)
    print_config(session.agent.config)


def update_plugin(session: InteractiveSession, plugin_text: str) -> None:
    if not plugin_text:
        print(f"Usage: {InteractiveCommands.PLUGIN} my-plugin|path/to/plugin.json")
        return
    path = resolve_plugin_path(plugin_text, session.agent.config.cwd)
    manifest = load_plugin_manifest(path)
    if path not in session.agent.config.plugin_paths:
        session.agent.config.plugin_paths = (*session.agent.config.plugin_paths, path)
    for skill_path in manifest.skill_paths:
        if skill_path not in session.agent.config.skill_paths:
            session.agent.config.skill_paths = (*session.agent.config.skill_paths, skill_path)
    prompt = build_system_prompt(INTERACTIVE_SYSTEM_PROMPT, session.agent.config.skill_paths, session.agent.config.cwd)
    upsert_system_prompt(session.context, prompt)
    persist_session_messages(session)
    rebuild_agent(session)
    print_config(session.agent.config)


def handle_plugins_command(session: InteractiveSession, argument: str) -> None:
    discovered = discover_plugin_paths(session.agent.config.cwd, include_user=False)
    if not argument:
        if not discovered:
            print("no workspace plugins found")
            return
        for index, path in enumerate(discovered, start=1):
            loaded = " (loaded)" if path in session.agent.config.plugin_paths else ""
            print(f"{index}: {path.parent.name}{loaded} - {relative_or_absolute_path(path, session.agent.config.cwd)}")
        return
    action, value = split_command_argument(argument)
    if action != "load" or not value:
        print(f"Usage: {InteractiveCommands.PLUGINS} [load <name>|load all]")
        return
    targets = discovered if value == "all" else (resolve_plugin_path(value, session.agent.config.cwd),)
    loaded = 0
    for path in targets:
        manifest = load_plugin_manifest(path)
        if path not in session.agent.config.plugin_paths:
            session.agent.config.plugin_paths = (*session.agent.config.plugin_paths, path)
            loaded += 1
        for skill_path in manifest.skill_paths:
            if skill_path not in session.agent.config.skill_paths:
                session.agent.config.skill_paths = (*session.agent.config.skill_paths, skill_path)
    prompt = build_system_prompt(INTERACTIVE_SYSTEM_PROMPT, session.agent.config.skill_paths, session.agent.config.cwd)
    upsert_system_prompt(session.context, prompt)
    persist_session_messages(session)
    rebuild_agent(session)
    print(f"loaded plugins: {loaded}")
    print_config(session.agent.config)


def update_skill(session: InteractiveSession, skill_text: str) -> None:
    if not skill_text:
        print(f"Usage: {InteractiveCommands.SKILL} my-coffee|path/to/SKILL.md")
        return
    path = resolve_skill_path(skill_text, session.agent.config.cwd)
    if path not in session.agent.config.skill_paths:
        session.agent.config.skill_paths = (*session.agent.config.skill_paths, path)
    prompt = build_system_prompt(INTERACTIVE_SYSTEM_PROMPT, session.agent.config.skill_paths, session.agent.config.cwd)
    upsert_system_prompt(session.context, prompt)
    persist_session_messages(session)
    rebuild_agent(session)
    print_config(session.agent.config)


def handle_skills_command(session: InteractiveSession, argument: str) -> None:
    discovered = discover_skill_paths(session.agent.config.cwd)
    if not argument:
        if not discovered:
            print("no workspace skills found")
            return
        for index, path in enumerate(discovered, start=1):
            loaded = " (loaded)" if path in session.agent.config.skill_paths else ""
            print(f"{index}: {path.parent.name}{loaded} - {relative_skill_path(path, session.agent.config.cwd)}")
        return

    parts = argument.split(maxsplit=1)
    if parts[0] != "load" or len(parts) != 2:
        print(f"Usage: {InteractiveCommands.SKILLS} [load <name>|load all]")
        return
    target = parts[1].strip()
    if target == "all":
        paths = discovered
    else:
        paths = tuple(path for path in discovered if path.parent.name == target)
    if not paths:
        print(f"skill not found: {target}")
        return
    for path in paths:
        if path not in session.agent.config.skill_paths:
            session.agent.config.skill_paths = (*session.agent.config.skill_paths, path)
    prompt = build_system_prompt(INTERACTIVE_SYSTEM_PROMPT, session.agent.config.skill_paths, session.agent.config.cwd)
    upsert_system_prompt(session.context, prompt)
    persist_session_messages(session)
    rebuild_agent(session)
    print_config(session.agent.config)


def update_permission(session: InteractiveSession, mode: str) -> None:
    if mode not in PermissionModes.ALL:
        print(f"Usage: {InteractiveCommands.PERMISSION} {'|'.join(PermissionModes.ALL)}")
        return
    session.agent.config.permission_mode = mode
    rebuild_agent(session)
    print_config(session.agent.config)


def handle_policy_command(session: InteractiveSession, argument: str) -> None:
    if argument.startswith("explain "):
        handle_policy_explain_command(session, argument.removeprefix("explain ").strip())
        return
    if argument and argument != "json":
        print(f"Usage: {InteractiveCommands.POLICY} [json]|explain <tool> <payload>")
        return
    report = build_policy_report(session)
    if argument == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"permission_mode: {report['permission_mode']}")
    print(f"allow_network: {report['allow_network']}")
    print(f"policy_preset: {report['policy_preset']}")
    print(f"policy_file: {report['policy_file']}")
    print(f"dangerous_tokens: {report['dangerous_tokens_count']}")
    print(f"sensitive_path_tokens: {report['sensitive_path_tokens_count']}")
    print(f"network_command_tokens: {report['network_command_tokens_count']}")
    print(f"allow_command_prefixes: {format_policy_list(report['allow_command_prefixes'])}")
    print(f"write_allow_paths: {format_policy_list(report['write_allow_paths'])}")
    print(f"write_deny_paths: {format_policy_list(report['write_deny_paths'])}")
    print(f"approved_actions: {format_policy_list(report['approved_actions'])}")
    print(f"approved_tool_calls: {report['approved_tool_calls_count']}")


def handle_policy_explain_command(session: InteractiveSession, argument: str) -> None:
    if not argument:
        print(f"Usage: {InteractiveCommands.POLICY} explain <tool> <payload>")
        return
    parts = argument.split(maxsplit=1)
    action = parts[0]
    payload = parts[1] if len(parts) > 1 else ""
    report = session.agent.harness.policy.explain(action, payload)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def build_policy_report(session: InteractiveSession) -> dict[str, Any]:
    policy = session.agent.harness.policy
    rules = policy.rules
    return {
        "permission_mode": session.agent.config.permission_mode,
        "allow_network": session.agent.config.allow_network,
        "policy_preset": session.agent.config.policy_preset,
        "policy_file": str(session.agent.config.policy_file) if session.agent.config.policy_file else "<none>",
        "dangerous_tokens_count": len(rules.dangerous_tokens),
        "sensitive_path_tokens_count": len(rules.sensitive_path_tokens),
        "network_command_tokens_count": len(rules.network_command_tokens),
        "allow_command_prefixes": list(rules.allow_command_prefixes),
        "write_allow_paths": list(rules.write_allow_paths),
        "write_deny_paths": list(rules.write_deny_paths),
        "approved_actions": sorted(policy.approved_actions),
        "approved_tool_calls_count": len(policy.approved_tool_calls),
    }


def format_policy_list(values: Any) -> str:
    if not values:
        return "<none>"
    return ", ".join(str(value) for value in values)


def relative_skill_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def update_boolean_option(session: InteractiveSession, field_name: str, value: str, label: str) -> None:
    if value not in {"on", "off"}:
        print("Usage: on|off")
        return
    setattr(session.agent.config, field_name, value == "on")
    print(f"{label}: {value}")


def handle_doctor_command(session: InteractiveSession, argument: str) -> None:
    if argument and argument != "json":
        print(f"Usage: {InteractiveCommands.DOCTOR} [json]")
        return
    report = build_doctor_report(session)
    if argument == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"overall: {report['overall']}")
    for check in report["checks"]:
        detail = f" - {check['detail']}" if check["detail"] else ""
        print(f"{check['status']}: {check['name']}{detail}")


def handle_debug_command(session: InteractiveSession, argument: str) -> None:
    parts = argument.split(maxsplit=1)
    if parts and parts[0] == "prompt":
        message = parts[1] if len(parts) > 1 else "<next user message>"
        print_prompt_debug_report(session, message)
        return
    if not parts or parts[0] != "bundle":
        print(f"Usage: {InteractiveCommands.DEBUG} prompt [message]|bundle [path]")
        return
    default_path = session.agent.config.cwd / ".agent" / "debug-bundle.json"
    output_path = resolve_path_option(parts[1], session.agent.config.cwd) if len(parts) > 1 else default_path
    bundle = build_debug_bundle(session)
    write_debug_bundle(output_path, bundle)
    print(f"debug_bundle: {output_path}")


def print_prompt_debug_report(session: InteractiveSession, message: str) -> None:
    messages = build_prompt_debug_messages(session, message)
    role_counts: dict[str, int] = {}
    role_chars: dict[str, int] = {}
    for item in messages:
        role_counts[item.role] = role_counts.get(item.role, 0) + 1
        role_chars[item.role] = role_chars.get(item.role, 0) + len(item.content)
    system = messages[0].content if messages and messages[0].role == "system" else ""
    user_tail = next((item.content for item in reversed(messages) if item.role == "user"), "")

    print(f"prompt_messages: {len(messages)}")
    print(f"prompt_chars: {sum(len(item.content) for item in messages)}")
    print(f"prompt_estimated_tokens: {estimate_message_tokens(messages)}")
    print(f"roles: {format_role_report(role_counts, role_chars)}")
    print(f"system_chars: {len(system)}")
    print(f"system_preview: {preview_debug_text(system)}")
    print(f"last_user_chars: {len(user_tail)}")
    print(f"last_user_preview: {preview_debug_text(user_tail)}")
    print(f"model_streaming: {session.agent.config.model_streaming}")
    print(f"model_output_segment_chars: {session.agent.config.model_output_segment_chars}")
    print(f"ollama_options: {redact_text(json.dumps(session.agent.config.ollama_options, ensure_ascii=False, sort_keys=True)) if session.agent.config.ollama_options else '{}'}")


def build_prompt_debug_messages(session: InteractiveSession, message: str) -> list[Message]:
    options = LoopOptions(
        allow_final_text=True,
        system_prompt=INTERACTIVE_SYSTEM_PROMPT,
        interrupt_input_reader=read_supplemental_stdin,
    )
    options = with_configured_skills(session.agent.config, options)
    options = with_active_plan_context(session.context, options)
    messages = list(session.context.messages)
    if options.system_prompt:
        if messages and messages[0].role == "system":
            messages[0] = Message(role="system", content=options.system_prompt)
        else:
            messages.insert(0, Message(role="system", content=options.system_prompt))
    if message:
        messages.append(Message(role="user", content=message))
    return messages


def format_role_report(role_counts: dict[str, int], role_chars: dict[str, int]) -> str:
    parts = []
    for role in sorted(role_counts):
        parts.append(f"{role}={role_counts[role]}({role_chars.get(role, 0)} chars)")
    return ", ".join(parts) if parts else "<none>"


def preview_debug_text(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return redact_text(compact)
    return redact_text(compact[: limit - 3] + "...")


def build_debug_bundle(session: InteractiveSession) -> dict[str, Any]:
    config = session.agent.config
    events = session.session_store.query_events(limit=50) if session.session_store else []
    bundle = {
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "cwd": str(config.cwd),
        },
        "config": {
            "provider": config.provider,
            "model": config.model,
            "base_url": redact_text(config.base_url),
            "api_key_present": bool(config.api_key),
            "permission": config.permission_mode,
            "allow_network": config.allow_network,
            "policy_preset": config.policy_preset,
            "shell": config.shell_kind,
            "sandbox": config.sandbox_kind,
            "sandbox_image": config.sandbox_image,
            "sandbox_network": config.sandbox_network,
            "sandbox_read_only": config.sandbox_read_only,
            "model_streaming": config.model_streaming,
            "model_output_segment_chars": config.model_output_segment_chars,
            "ollama_options": redact_debug_value(config.ollama_options),
            "mcp_config": str(config.mcp_config) if config.mcp_config else None,
            "plugins": [str(path) for path in config.plugin_paths],
            "skills": [str(path) for path in config.skill_paths],
            "session_store": str(session.session_store.path) if session.session_store else None,
        },
        "doctor": build_doctor_report(session),
        "policy": build_policy_report(session),
        "events": [redact_debug_value(event.to_dict()) for event in events],
    }
    return redact_debug_value(bundle)


def write_debug_bundle(output_path: Path, bundle: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("debug-bundle.json", json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            archive.writestr("doctor.json", json.dumps(bundle["doctor"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            archive.writestr("policy.json", json.dumps(bundle["policy"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            archive.writestr("events.json", json.dumps(bundle["events"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact_debug_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_debug_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_debug_value(item) for key, item in value.items()}
    return value


def build_doctor_report(session: InteractiveSession) -> dict[str, Any]:
    checks = [
        doctor_check_workspace(session),
        *doctor_check_config_files(session),
        doctor_check_session(session),
        doctor_check_model(session),
        doctor_check_policy(session),
        *doctor_check_mcp(session),
        *doctor_check_plugins(session),
    ]
    overall = "error" if any(check["status"] == "error" for check in checks) else "warn" if any(check["status"] == "warn" for check in checks) else "ok"
    return {"overall": overall, "checks": checks}


def doctor_item(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def doctor_check_workspace(session: InteractiveSession) -> dict[str, str]:
    cwd = session.agent.config.cwd
    if not cwd.exists():
        return doctor_item("workspace", "error", f"cwd does not exist: {cwd}")
    if not cwd.is_dir():
        return doctor_item("workspace", "error", f"cwd is not a directory: {cwd}")
    if not os.access(cwd, os.R_OK | os.W_OK | os.X_OK):
        return doctor_item("workspace", "warn", f"cwd may not be fully readable/writable: {cwd}")
    return doctor_item("workspace", "ok", str(cwd))


def doctor_check_config_files(session: InteractiveSession) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for label, path in (
        ("user_config", default_user_config_path()),
        ("project_config", session.agent.config.cwd / Defaults.LOCAL_CONFIG_FILE),
    ):
        if not path.exists():
            checks.append(doctor_item(label, "ok", f"not present: {path}"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            checks.append(doctor_item(label, "error", f"unable to read {path}: {exc}"))
            continue
        except json.JSONDecodeError as exc:
            checks.append(doctor_item(label, "error", f"invalid JSON {path}: {exc}"))
            continue
        if not isinstance(data, dict):
            checks.append(doctor_item(label, "error", f"config must contain a JSON object: {path}"))
            continue
        warnings = validate_cli_defaults(data, path)
        if warnings:
            checks.extend(doctor_item(label, "warn", warning) for warning in warnings)
        else:
            version = data.get(CONFIG_SCHEMA_VERSION_KEY, CONFIG_SCHEMA_VERSION)
            checks.append(doctor_item(label, "ok", f"schema={version} path={path}"))
    return checks


def doctor_check_session(session: InteractiveSession) -> dict[str, str]:
    store = session.session_store
    if store is None:
        return doctor_item("session", "warn", "session persistence is disabled")
    path = getattr(store, "path", None)
    backend = "sqlite" if isinstance(store, SQLiteSessionStore) else "json"
    if path is None:
        return doctor_item("session", "ok", f"backend={backend}")
    path = Path(path)
    parent = path.parent
    if not parent.exists():
        return doctor_item("session", "warn", f"parent directory will be created: {parent}")
    if not os.access(parent, os.W_OK):
        return doctor_item("session", "error", f"session parent is not writable: {parent}")
    return doctor_item("session", "ok", f"backend={backend} path={path}")


def doctor_check_model(session: InteractiveSession) -> dict[str, str]:
    config = session.agent.config
    if not config.model.strip():
        return doctor_item("model", "error", "model name is empty")
    if config.provider in {Providers.OPENAI_COMPATIBLE, Providers.ANTHROPIC, Providers.GEMINI} and not config.api_key:
        return doctor_item("model", "warn", f"{config.provider} usually requires an API key base_url={redact_text(config.base_url)}")
    return doctor_item("model", "ok", f"{config.provider}:{config.model} base_url={redact_text(config.base_url)}")


def doctor_check_policy(session: InteractiveSession) -> dict[str, str]:
    config = session.agent.config
    if config.policy_file and not config.policy_file.is_file():
        return doctor_item("policy", "error", f"policy file not found: {config.policy_file}")
    if config.policy_preset not in PolicyPresets.ALL:
        return doctor_item("policy", "error", f"unknown policy preset: {config.policy_preset}")
    rules = session.agent.harness.policy.rules
    detail = (
        f"mode={config.permission_mode} preset={config.policy_preset} allow_network={config.allow_network} "
        f"write_allow={len(rules.write_allow_paths)} write_deny={len(rules.write_deny_paths)}"
    )
    return doctor_item("policy", "ok", detail)


def doctor_check_mcp(session: InteractiveSession) -> list[dict[str, str]]:
    config = session.agent.config
    if config.mcp_config is None:
        return [doctor_item("mcp", "ok", "no explicit MCP config")]
    try:
        servers = load_mcp_config(config.mcp_config)
    except ConfigurationError as exc:
        return [doctor_item("mcp", "error", str(exc))]
    checks = []
    for server in servers:
        status = "warn" if server.has_unresolved_placeholders() else "ok"
        detail = f"{server.name} url={redact_text(server.url)} discover_tools={server.discover_tools}"
        if status == "warn":
            detail += " unresolved environment placeholders"
        checks.append(doctor_item("mcp", status, detail))
    return checks or [doctor_item("mcp", "warn", "MCP config contains no servers")]


def doctor_check_plugins(session: InteractiveSession) -> list[dict[str, str]]:
    paths = session.agent.config.plugin_paths
    if not paths:
        return [doctor_item("plugins", "ok", "no plugins loaded")]
    checks: list[dict[str, str]] = []
    for path in paths:
        try:
            manifest = load_plugin_manifest(path)
        except ConfigurationError as exc:
            checks.append(doctor_item("plugins", "error", str(exc)))
            continue
        checks.append(
            doctor_item(
                "plugins",
                "ok",
                f"{manifest.name} skills={len(manifest.skill_paths)} mcp_configs={len(manifest.mcp_config_paths) + len(manifest.inline_mcp_configs)}",
            )
        )
    return checks


def handle_context_command(session: InteractiveSession, action: str) -> None:
    if action == "status" or not action:
        print(f"context_messages: {len(session.context.messages)}")
        print(f"context_chars: {total_message_chars(session.context.messages)}")
        estimated_tokens = estimate_context_tokens(session.context.messages)
        print(f"context_estimated_tokens: {estimated_tokens}")
        if session.agent.config.model_context_tokens is not None:
            threshold = int(session.agent.config.model_context_tokens * session.agent.config.context_compression_ratio)
            print(f"context_token_threshold: {threshold}")
            print(f"context_token_budget: {session.agent.config.model_context_tokens}")
        return
    if action == "clear":
        session.context.messages = []
        if session.session_store:
            session.session_store.save(session.context.messages)
        print("context cleared")
        return
    if action == "compact":
        before_messages = len(session.context.messages)
        before_chars = total_message_chars(session.context.messages)
        session.context.messages = compact_messages(session.context.messages, max(1, session.agent.config.max_context_chars // 2))
        if session.session_store:
            session.session_store.save(session.context.messages)
        print(
            "context compacted: "
            f"{before_messages}->{len(session.context.messages)} messages, "
            f"{before_chars}->{total_message_chars(session.context.messages)} chars"
        )
        return
    print(f"Usage: {InteractiveCommands.CONTEXT} status|compact|clear")


def handle_history_command(session: InteractiveSession, argument: str) -> str | None:
    if not session.user_history:
        print("history is empty")
        return None
    if argument:
        try:
            index = int(argument)
        except ValueError:
            print(f"Usage: {InteractiveCommands.HISTORY} [number]")
            return None
        if index < 1 or index > len(session.user_history):
            print(f"history index out of range: {index}")
            return None
        prompt = session.user_history[index - 1]
        print(f"replay history[{index}]: {prompt}")
        return prompt

    for index, prompt in enumerate(session.user_history[-20:], start=max(1, len(session.user_history) - 19)):
        print(f"{index}: {prompt}")
    return None


def handle_events_command(session: InteractiveSession, argument: str) -> None:
    if session.session_store is None:
        print("no session store configured")
        return
    try:
        query = parse_events_query(argument)
    except ValueError as exc:
        print(str(exc))
        return
    events = session.session_store.query_events(kind=query["kind"] or None, limit=query["limit"], offset=query["offset"])
    if not events:
        print("no events")
        return
    if query["format"] == "json":
        print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=2))
        return
    for index, event in enumerate(events, start=1):
        print(f"{index}: {event.timestamp} {event.kind} {json.dumps(event.data, ensure_ascii=False, sort_keys=True)}")


def handle_metrics_command(session: InteractiveSession, argument: str) -> None:
    if argument and argument != "json":
        print(f"Usage: {InteractiveCommands.METRICS} [json]")
        return
    if session.session_store is None:
        print("no session store configured")
        return
    report = build_metrics_report(session)
    if argument == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"events: {report['events']}")
    print(f"traces: {report['traces']}")
    print(f"tool_executions: {report['tool_executions']}")
    print(f"tool_success: {report['tool_success']}")
    print(f"tool_failed: {report['tool_failed']}")
    print(f"tool_skipped: {report['tool_skipped']}")
    print(f"permission_allows: {report['permission_allows']}")
    print(f"permission_denies: {report['permission_denies']}")
    print(f"tool_batches: {report['tool_batches']}")
    print(f"tool_batch_avg_ms: {report['tool_batch_avg_ms']}")


def build_metrics_report(session: InteractiveSession) -> dict[str, Any]:
    events = session.session_store.load_events() if session.session_store else []
    tool_events = [event for event in events if event.kind == EventKinds.TOOL_EXECUTION]
    permission_events = [event for event in events if event.kind == EventKinds.PERMISSION_DECISION]
    batch_events = [event for event in events if event.kind == EventKinds.TOOL_BATCH]
    batch_durations = [
        int(event.data["duration_ms"])
        for event in batch_events
        if isinstance(event.data.get("duration_ms"), int)
    ]
    traces = {str(event.data.get("trace_id")) for event in events if event.data.get("trace_id")}
    statuses = [str(event.data.get("status", "")) for event in tool_events]
    permission_decisions = [str(event.data.get("decision", "")) for event in permission_events]
    return {
        "events": len(events),
        "traces": len(traces),
        "tool_executions": len(tool_events),
        "tool_success": statuses.count("success"),
        "tool_failed": statuses.count("failed"),
        "tool_skipped": statuses.count("skipped"),
        "permission_allows": permission_decisions.count(ToolDecisionKinds.ALLOW),
        "permission_denies": permission_decisions.count(ToolDecisionKinds.DENY),
        "tool_batches": len(batch_events),
        "tool_batch_avg_ms": int(sum(batch_durations) / len(batch_durations)) if batch_durations else 0,
    }


def handle_session_command(session: InteractiveSession, argument: str) -> None:
    action, value = split_command_argument(argument)
    if action in {"", "stats"}:
        print_session_stats(session)
        return
    if session.session_store is None:
        print("no session store configured")
        return
    if action == "export":
        exporter = getattr(session.session_store, "export_data", None)
        if exporter is None:
            print("session export is not supported by this session store")
            return
        output_path = resolve_path_option(value, session.agent.config.cwd) if value else session.agent.config.cwd / ".agent" / "session-export.json"
        data = exporter()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"session_export: {output_path}")
        return
    if action == "import":
        if not value:
            print(f"Usage: {InteractiveCommands.SESSION} import <path>")
            return
        importer = getattr(session.session_store, "import_data", None)
        if importer is None:
            print("session import is not supported by this session store")
            return
        input_path = resolve_path_option(value, session.agent.config.cwd)
        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"unable to read session import: {exc}")
            return
        except json.JSONDecodeError as exc:
            print(f"session import must be valid JSON: {exc}")
            return
        if not isinstance(data, dict):
            print("session import must contain a JSON object")
            return
        importer(data)
        session.context.messages = session.session_store.load()
        plan = session.session_store.load_plan()
        workflow = session.session_store.load_workflow()
        if plan is not None:
            session.context.metadata[PLAN_METADATA_KEY] = plan
        else:
            session.context.metadata.pop(PLAN_METADATA_KEY, None)
        if workflow is not None:
            session.context.metadata[WORKFLOW_METADATA_KEY] = workflow
        else:
            session.context.metadata.pop(WORKFLOW_METADATA_KEY, None)
        print(f"session_imported: {input_path}")
        print_session_stats(session)
        return
    print(f"Usage: {InteractiveCommands.SESSION} stats|export [path]|import <path>")


def print_session_stats(session: InteractiveSession) -> None:
    messages = session.context.messages
    events = session.session_store.load_events() if session.session_store else []
    print(f"messages: {len(messages)}")
    print(f"message_chars: {total_message_chars(messages)}")
    print(f"events: {len(events)}")
    print(f"plan: {'yes' if session.context.metadata.get(PLAN_METADATA_KEY) else 'no'}")
    print(f"workflow: {'yes' if session.context.metadata.get(WORKFLOW_METADATA_KEY) else 'no'}")
    if session.session_store:
        backend = "sqlite" if isinstance(session.session_store, SQLiteSessionStore) else "json"
        print(f"session_backend: {backend}")
        print(f"session_path: {session.session_store.path}")
    else:
        print("session_backend: none")


def handle_memory_command(session: InteractiveSession, query: str) -> None:
    if not query:
        print(f"Usage: {InteractiveCommands.MEMORY} <query>")
        return
    searcher = getattr(session.session_store, "search_memory", None)
    if searcher is None:
        print("memory retrieval requires --session-db")
        return
    results = searcher(query, limit=10)
    if not results:
        print("no memory matches")
        return
    for index, result in enumerate(results, start=1):
        timestamp = f" {result.timestamp}" if result.timestamp else ""
        print(f"{index}: [{result.kind} score={result.score}{timestamp}] {result.text}")


def handle_plan_command(session: InteractiveSession, argument: str) -> None:
    if not argument or argument == "show":
        plan = session.context.metadata.get(PLAN_METADATA_KEY)
        if plan is None:
            print("no active plan")
            return
        print(format_plan_artifact(plan))
        return
    if argument == "clear":
        session.context.metadata.pop(PLAN_METADATA_KEY, None)
        if session.session_store:
            session.session_store.save_plan(None)
        print("plan cleared")
        return

    planning_config = copy.copy(session.agent.config)
    planning_config.permission_mode = PermissionModes.PLAN
    planning_agent = Agent(
        config=planning_config,
        harness=AgentHarness(config=planning_config, model=session.agent.harness.model),
    )
    planning_context = ChatContext()
    exit_code = run_turn(
        planning_agent,
        build_plan_prompt(argument),
        planning_context,
        session_store=None,
        options=LoopOptions(allow_final_text=True, system_prompt=INTERACTIVE_SYSTEM_PROMPT),
    )
    if exit_code != 0:
        print("Plan creation failed. You can adjust options and retry.")
        return

    assistant_messages = [message.content for message in planning_context.messages if message.role == "assistant"]
    plan = create_plan_artifact(argument, assistant_messages[-1] if assistant_messages else "")
    session.context.metadata[PLAN_METADATA_KEY] = plan
    if session.session_store:
        session.session_store.save_plan(plan)
    print("plan saved")
    print(format_plan_artifact(plan))


def handle_workflow_command(session: InteractiveSession, argument: str) -> None:
    action, value = split_command_argument(argument)
    workflow = active_workflow_from_context(session.context)
    if action in {"", "show"}:
        if workflow is None:
            print("no active workflow")
            return
        print(format_workflow_artifact(workflow))
        return
    if action == "clear":
        session.context.metadata.pop(WORKFLOW_METADATA_KEY, None)
        if session.session_store:
            session.session_store.save_workflow(None)
        print("workflow cleared")
        return
    if action == "create":
        if not value:
            print(f"Usage: {InteractiveCommands.WORKFLOW} create <goal>")
            return
        workflow = create_workflow(value)
        save_workflow(session, workflow)
        print("workflow saved")
        print(format_workflow_artifact(workflow))
        return
    if action == "step":
        if workflow is None:
            print("no active workflow")
            return
        if not value:
            print(f"Usage: {InteractiveCommands.WORKFLOW} step <text>")
            return
        workflow = add_workflow_step(workflow, value)
        save_workflow(session, workflow)
        print("workflow step added")
        print(format_workflow_artifact(workflow))
        return
    if action == "done":
        if workflow is None:
            print("no active workflow")
            return
        try:
            index = int(value)
        except ValueError:
            print(f"Usage: {InteractiveCommands.WORKFLOW} done <number>")
            return
        if index < 1 or index > len(workflow.steps):
            print(f"workflow step out of range: {index}")
            return
        workflow = complete_workflow_step(workflow, index)
        save_workflow(session, workflow)
        print("workflow step completed")
        print(format_workflow_artifact(workflow))
        return
    if action == "schedule":
        if workflow is None:
            print("no active workflow")
            return
        workflow, index = schedule_next_workflow_step(workflow)
        save_workflow(session, workflow)
        if index is None:
            print("no pending workflow steps")
        else:
            print(f"workflow step scheduled: {index}")
        print(format_workflow_artifact(workflow))
        return
    if action == "wait":
        if workflow is None:
            print("no active workflow")
            return
        counts = workflow_status_counts(workflow)
        print("workflow status:")
        for key in sorted(counts):
            if counts[key]:
                print(f"- {key}: {counts[key]}")
        if workflow.steps and all(step.status in {"done", "verified"} for step in workflow.steps):
            print("workflow complete")
        elif any(step.status == "running" for step in workflow.steps):
            print("workflow has running steps")
        else:
            print("workflow waiting for pending work")
        return
    if action == "merge":
        if workflow is None:
            print("no active workflow")
            return
        workflow = merge_workflow_delegations(workflow)
        save_workflow(session, workflow)
        print("workflow delegations merged")
        print(format_workflow_artifact(workflow))
        return
    if action == "verify":
        if workflow is None:
            print("no active workflow")
            return
        if value in {"", "all"}:
            for index, step in enumerate(workflow.steps, start=1):
                if step.status == "done":
                    workflow = verify_workflow_step(workflow, index)
        else:
            try:
                index = int(value)
            except ValueError:
                print(f"Usage: {InteractiveCommands.WORKFLOW} verify [number|all]")
                return
            if index < 1 or index > len(workflow.steps):
                print(f"workflow step out of range: {index}")
                return
            workflow = verify_workflow_step(workflow, index)
        save_workflow(session, workflow)
        print("workflow verified")
        print(format_workflow_artifact(workflow))
        return
    print(f"Usage: {InteractiveCommands.WORKFLOW} create <goal>|step <text>|schedule|done <number>|merge|verify [number|all]|wait|show|clear")


def handle_delegate_command(session: InteractiveSession, task: str) -> None:
    if not task:
        print(f"Usage: {InteractiveCommands.DELEGATE} [explorer|worker|verifier] <task>")
        return
    role, task = parse_delegate_role(task)
    runner = SubAgentRunner(session.agent.config, session.agent.harness.model)
    result = runner.run(task, role=role)
    workflow = active_workflow_from_context(session.context) or create_workflow("Delegated work")
    summary = result.summary
    if result.changed_files:
        summary = f"{summary} Changed files: {', '.join(result.changed_files)}"
    if result.role != "explorer":
        summary = f"Role: {result.role}. {summary}"
    workflow = add_workflow_delegation(workflow, task=result.task, summary=summary, success=result.success)
    save_workflow(session, workflow)
    status = "success" if result.success else "failed"
    print(f"delegation {status} ({result.role})")
    if result.changed_files:
        print(f"changed_files: {', '.join(result.changed_files)}")
    print(result.summary)


def parse_delegate_role(value: str) -> tuple[str, str]:
    role, task = split_command_argument(value)
    if role in SUBAGENT_ROLES and task:
        return role, task
    return "explorer", value


def split_command_argument(argument: str) -> tuple[str, str]:
    parts = argument.split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def active_workflow_from_context(context: ChatContext) -> WorkflowArtifact | None:
    raw = context.metadata.get(WORKFLOW_METADATA_KEY)
    if isinstance(raw, WorkflowArtifact):
        return raw
    if isinstance(raw, dict):
        return WorkflowArtifact.from_dict(raw)
    return None


def save_workflow(session: InteractiveSession, workflow: WorkflowArtifact) -> None:
    session.context.metadata[WORKFLOW_METADATA_KEY] = workflow
    if session.session_store:
        session.session_store.save_workflow(workflow)


def rebuild_agent(session: InteractiveSession, config: AgentConfig | None = None) -> None:
    config = replace(config or session.agent.config)
    current_model = session.agent.harness.model
    model = None if isinstance(current_model, ModelGateway) else current_model
    session.agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=session.session_store))


def print_config(config: AgentConfig) -> None:
    print(f"provider: {config.provider}")
    print(f"model: {config.model}")
    print(f"base_url: {redact_text(config.base_url)}")
    print(f"permission: {config.permission_mode}")
    print(f"policy_preset: {config.policy_preset}")
    print(f"shell: {config.shell_kind}")
    print(f"sandbox: {config.sandbox_kind}")
    print(f"sandbox_image: {config.sandbox_image}")
    print(f"sandbox_network: {config.sandbox_network}")
    print(f"sandbox_read_only: {config.sandbox_read_only}")
    print(f"allow_network: {config.allow_network}")
    print(f"summarize_context: {config.summarize_context}")
    print(f"model_streaming: {config.model_streaming}")
    print(f"model_output_segment_chars: {config.model_output_segment_chars}")
    print(f"ollama_options: {redact_text(json.dumps(config.ollama_options, ensure_ascii=False, sort_keys=True)) if config.ollama_options else '{}'}")
    print(f"fallback_routes: {len(config.model_fallbacks)}")
    print(f"model_price_input_per_1m: {config.model_price_input_per_1m}")
    print(f"model_price_output_per_1m: {config.model_price_output_per_1m}")
    print(f"usage_ledger: {config.usage_ledger_path or '<none>'}")
    print(f"usage_subject: {config.usage_subject}")
    print(f"usage_tenant: {config.usage_tenant}")
    print(f"model_context_tokens: {config.model_context_tokens or '<none>'}")
    print(f"context_compression_ratio: {config.context_compression_ratio}")
    print(f"mcp_config: {config.mcp_config or '<none>'}")
    plugins = ", ".join(path.parent.name for path in config.plugin_paths) if config.plugin_paths else "<none>"
    print(f"plugins: {plugins}")
    skills = ", ".join(path.parent.name for path in config.skill_paths) if config.skill_paths else "<none>"
    print(f"skills: {skills}")


def save_cli_config(path: Path, session: InteractiveSession) -> None:
    config = session.agent.config
    data: dict[str, Any] = {
        CONFIG_SCHEMA_VERSION_KEY: CONFIG_SCHEMA_VERSION,
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "permission": config.permission_mode,
        "policy_preset": config.policy_preset,
        "shell": config.shell_kind,
        "sandbox": config.sandbox_kind,
        "sandbox_image": config.sandbox_image,
        "sandbox_network": config.sandbox_network,
        "sandbox_read_only": config.sandbox_read_only,
        "allow_network": config.allow_network,
        "summarize_context": config.summarize_context,
        "model_streaming": config.model_streaming,
        "model_output_segment_chars": config.model_output_segment_chars,
        "max_steps": config.max_steps,
        "model_timeout": config.model_timeout,
        "context_compression_ratio": config.context_compression_ratio,
        "prompt_version": config.prompt_version,
    }
    if config.api_key:
        data["api_key"] = config.api_key
    if config.ollama_options:
        data["ollama_options"] = config.ollama_options
    if config.policy_file:
        data["policy_file"] = relative_or_absolute_path(config.policy_file, config.cwd)
    if config.mcp_config:
        data["mcp_config"] = relative_or_absolute_path(config.mcp_config, config.cwd)
    if config.plugin_paths:
        data["plugin"] = [relative_or_absolute_path(path, config.cwd) for path in config.plugin_paths]
    if config.skill_paths:
        data["skill"] = [relative_or_absolute_path(path, config.cwd) for path in config.skill_paths]
    if config.model_fallbacks:
        data["model_fallback"] = [
            {
                "provider": route.provider,
                "model": route.model,
                "base_url": route.base_url,
                **({"api_key": route.api_key} if route.api_key else {}),
                **({"timeout": route.timeout} if route.timeout is not None else {}),
                "max_retries": route.max_retries,
                "price_input_per_1m": route.price_input_per_1m,
                "price_output_per_1m": route.price_output_per_1m,
                "weight": route.weight,
            }
            for route in config.model_fallbacks
        ]
    if config.model_context_tokens is not None:
        data["model_context_tokens"] = config.model_context_tokens
    if config.usage_ledger_path is not None:
        data["usage_ledger"] = relative_or_absolute_path(config.usage_ledger_path, config.cwd)
    if config.usage_subject != "default":
        data["usage_subject"] = config.usage_subject
    if config.usage_tenant != "default":
        data["usage_tenant"] = config.usage_tenant
    if session.session_store is not None:
        if isinstance(session.session_store, SQLiteSessionStore):
            data["session_db"] = relative_or_absolute_path(session.session_store.path, config.cwd)
        else:
            data["session"] = relative_or_absolute_path(session.session_store.path, config.cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_or_absolute_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def with_configured_skills(config: AgentConfig, options: LoopOptions | None) -> LoopOptions:
    base = options or LoopOptions()
    prompt = build_system_prompt(base.system_prompt or SYSTEM_PROMPT, config.skill_paths, config.cwd)
    return LoopOptions(
        allow_final_text=base.allow_final_text,
        system_prompt=prompt,
        interrupt_input_reader=base.interrupt_input_reader,
    )


def upsert_system_prompt(context: ChatContext, system_prompt: str | None) -> None:
    if not system_prompt:
        return
    if context.messages and context.messages[0].role == "system":
        context.messages[0] = Message(role="system", content=system_prompt)
        return
    context.messages.insert(0, Message(role="system", content=system_prompt))


def persist_session_messages(session: InteractiveSession) -> None:
    if session.session_store:
        session.session_store.save(session.context.messages)


if __name__ == "__main__":
    import sys
    sys.exit(main())
