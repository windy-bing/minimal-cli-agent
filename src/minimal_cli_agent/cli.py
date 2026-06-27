from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
import json
import os
import re
import select
import sys
import time
from pathlib import Path
from typing import Any

try:
    import readline
except ImportError:  # pragma: no cover - readline is platform-dependent.
    readline = None

from minimal_cli_agent.agent import Agent, print_event
from minimal_cli_agent.constants import Defaults, InteractiveCommands, LoopEventData, LoopEventTypes, PermissionModes, Profiles, Providers, ToolDecisionKinds, ToolPayloadFields, Tools
from minimal_cli_agent.context import estimate_context_tokens, total_message_chars
from minimal_cli_agent.exceptions import AgentError, ConfigurationError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.memory import JsonSessionStore, SQLiteSessionStore
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
from minimal_cli_agent.skills import build_system_prompt, discover_skill_paths, resolve_skill_path, resolve_skill_paths
from minimal_cli_agent.subagent import SUBAGENT_ROLES, SubAgentRunner
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, Message, ModelRoute, ToolCall, ToolDecision
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimal-agent", description="Run a minimal terminal AI agent.")
    parser.add_argument("task", nargs="*", help="Task for the agent.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start a multi-turn interactive CLI session.")
    parser.add_argument("--config-file", type=Path, help="Read defaults from this JSON config file.")
    parser.add_argument("--profile", choices=Profiles.ALL, default=os.getenv("AGENT_PROFILE"))
    parser.add_argument("--provider", choices=Providers.ALL, default=os.getenv("AGENT_PROVIDER", Providers.OLLAMA))
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", Defaults.MODEL))
    parser.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", Defaults.BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("AGENT_API_KEY"))
    parser.add_argument("--cwd", type=Path, default=Path(os.getenv("AGENT_CWD", ".")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("AGENT_MAX_STEPS", Defaults.MAX_STEPS)))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AGENT_COMMAND_TIMEOUT", Defaults.COMMAND_TIMEOUT)))
    parser.add_argument("--shell", default=os.getenv("AGENT_SHELL", "system"), help="Shell adapter: system, bash, zsh, sh, powershell, cmd, git-bash, or a shell command.")
    parser.add_argument("--model-timeout", type=int, default=int(os.getenv("AGENT_MODEL_TIMEOUT", Defaults.MODEL_TIMEOUT)))
    parser.add_argument("--model-fallback", action="append", default=parse_model_fallback_env(), help="JSON fallback route. Example: '{\"provider\":\"ollama\",\"model\":\"qwen3:1.7b\",\"base_url\":\"http://localhost:11434\"}'")
    parser.add_argument("--model-max-retries", type=int, default=int(os.getenv("AGENT_MODEL_MAX_RETRIES", "0")))
    parser.add_argument("--model-max-concurrency", type=int, default=int(os.getenv("AGENT_MODEL_MAX_CONCURRENCY", "4")))
    parser.add_argument("--model-queue-timeout", type=float, default=float(os.getenv("AGENT_MODEL_QUEUE_TIMEOUT", "5")))
    parser.add_argument("--model-circuit-failure-threshold", type=int, default=int(os.getenv("AGENT_MODEL_CIRCUIT_FAILURE_THRESHOLD", "3")))
    parser.add_argument("--model-circuit-cooldown", type=float, default=float(os.getenv("AGENT_MODEL_CIRCUIT_COOLDOWN", "60")))
    parser.add_argument("--model-price-input-per-1m", type=float, default=float(os.getenv("AGENT_MODEL_PRICE_INPUT_PER_1M", "0")))
    parser.add_argument("--model-price-output-per-1m", type=float, default=float(os.getenv("AGENT_MODEL_PRICE_OUTPUT_PER_1M", "0")))
    parser.add_argument("--usage-ledger", type=Path, default=Path(os.getenv("AGENT_USAGE_LEDGER")) if os.getenv("AGENT_USAGE_LEDGER") else None)
    parser.add_argument("--usage-subject", default=os.getenv("AGENT_USAGE_SUBJECT", "default"))
    parser.add_argument("--usage-tenant", default=os.getenv("AGENT_USAGE_TENANT", "default"))
    parser.add_argument("--max-input-tokens", type=int, default=parse_optional_int_env("AGENT_MAX_INPUT_TOKENS"))
    parser.add_argument("--max-output-tokens", type=int, default=parse_optional_int_env("AGENT_MAX_OUTPUT_TOKENS"))
    parser.add_argument("--max-request-tokens", type=int, default=parse_optional_int_env("AGENT_MAX_REQUEST_TOKENS"))
    parser.add_argument("--daily-token-limit", type=int, default=parse_optional_int_env("AGENT_DAILY_TOKEN_LIMIT"))
    parser.add_argument("--monthly-token-limit", type=int, default=parse_optional_int_env("AGENT_MONTHLY_TOKEN_LIMIT"))
    parser.add_argument("--max-request-cost", type=float, default=parse_optional_float_env("AGENT_MAX_REQUEST_COST"))
    parser.add_argument("--daily-cost-limit", type=float, default=parse_optional_float_env("AGENT_DAILY_COST_LIMIT"))
    parser.add_argument("--monthly-cost-limit", type=float, default=parse_optional_float_env("AGENT_MONTHLY_COST_LIMIT"))
    parser.add_argument("--prompt-version", default=os.getenv("AGENT_PROMPT_VERSION", "default"))
    parser.add_argument("--bill-failed-requests", action="store_true", help="Count failed model attempts against usage budgets.")
    parser.add_argument("--allow-network", action="store_true", help="Allow shell commands with obvious network access.")
    parser.add_argument("--policy-file", type=Path, help="JSON file with additional shell policy deny tokens.")
    parser.add_argument("--mcp-config", type=Path, help="JSON config file with MCP servers.")
    parser.add_argument("--plugin", action="append", default=[], help="Load a plugin manifest by path or by name under plugins/<name>.")
    parser.add_argument("--no-plugin-discovery", action="store_true", help="Disable automatic plugin discovery.")
    parser.add_argument("--skill", action="append", default=[], help="Load a skill by path or by name under skills/<name>.")
    parser.add_argument("--summarize-context", action="store_true", default=None, help="Use the model to summarize old context when compacting.")
    parser.add_argument("--no-summarize-context", action="store_false", dest="summarize_context", help="Disable model context summaries.")
    parser.add_argument("--model-context-tokens", type=int, default=parse_optional_int_env("AGENT_MODEL_CONTEXT_TOKENS"), help="Approximate model context window. Context is compacted near this budget.")
    parser.add_argument("--context-compression-ratio", type=float, default=float(os.getenv("AGENT_CONTEXT_COMPRESSION_RATIO", Defaults.CONTEXT_COMPRESSION_RATIO)), help="Fraction of model context tokens that triggers compaction.")
    parser.add_argument("--show-config", action="store_true", help="Print resolved provider/model/base URL without secrets.")
    parser.add_argument(
        "--permission",
        choices=PermissionModes.ALL,
        default=os.getenv("AGENT_PERMISSION", PermissionModes.DEFAULT),
    )
    parser.add_argument("--session", type=Path, help="Persist messages to this JSON session file.")
    parser.add_argument("--session-db", type=Path, help="Persist full transcript and events to this SQLite database.")
    parser.add_argument("--no-session", action="store_true", help="Disable the default persistent session.")
    return parser


def load_cli_defaults(cwd: Path, explicit_config_file: Path | None = None) -> dict[str, Any]:
    paths = [default_user_config_path(), cwd / Defaults.LOCAL_CONFIG_FILE]
    if explicit_config_file is not None:
        paths = [explicit_config_file]
    merged: dict[str, Any] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"Unable to read config file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Config file must be valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"Config file must contain a JSON object: {path}")
        merged.update(data)
    return merged


def default_user_config_path() -> Path:
    return Path.home() / Defaults.USER_CONFIG_DIR / Defaults.USER_CONFIG_FILE


def default_project_session_path(cwd: Path) -> Path:
    return cwd / Defaults.SESSION_PATH


def resolve_default_session_path(
    args: argparse.Namespace,
    explicit_options: set[str],
    local_defaults: dict[str, Any],
    cwd: Path,
) -> Path | None:
    if args.no_session:
        return None
    raw_session = choose_config_value("session", args.session, local_defaults, explicit_options, ("AGENT_SESSION",))
    if raw_session is None:
        return default_project_session_path(cwd).resolve()
    return resolve_path_option(raw_session, cwd).resolve()


def build_session_store(session_path: Path | None, session_db_path: Path | None):
    if session_db_path is not None:
        return SQLiteSessionStore(session_db_path)
    if session_path is not None:
        return JsonSessionStore(session_path)
    return None


def choose_config_value(
    key: str,
    current: Any,
    local_defaults: dict[str, Any],
    explicit_options: set[str],
    env_names: tuple[str, ...] = (),
) -> Any:
    if key in explicit_options:
        return current
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value is not None:
            return env_value
    if key in local_defaults:
        return local_defaults[key]
    return current


def resolve_path_option(value: Any, base: Path) -> Path:
    path = value if isinstance(value, Path) else Path(str(value))
    path = path.expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def resolve_optional_path(value: Any, base: Path) -> Path | None:
    if value is None or value == "":
        return None
    return resolve_path_option(value, base).resolve()


def optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def bool_config_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def normalize_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return [str(value)]


def normalize_model_fallbacks(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [json.dumps(item) if isinstance(item, dict) else str(item) for item in value]
    if isinstance(value, dict):
        return [json.dumps(value)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [json.dumps(item) if isinstance(item, dict) else str(item) for item in parsed]
        if isinstance(parsed, dict):
            return [json.dumps(parsed)]
        return [value]
    return [str(value)]


def merge_paths(*groups: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    merged: list[Path] = []
    for group in groups:
        for path in group:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            merged.append(resolved)
    return tuple(merged)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
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
        plugin_discovery = not bool_config_value(
            choose_config_value(
                "no_plugin_discovery",
                args.no_plugin_discovery,
                local_defaults,
                explicit_options,
                ("AGENT_NO_PLUGIN_DISCOVERY",),
            ),
            default=False,
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
            model_timeout=int(choose_config_value("model_timeout", args.model_timeout, local_defaults, explicit_options, ("AGENT_MODEL_TIMEOUT",))),
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
            print(f"base_url: {config.base_url}")
            print(f"api_key_present: {bool(config.api_key)}")
            print(f"api_key_length: {len(config.api_key or '')}")
            print(f"fallback_routes: {len(config.model_fallbacks)}")
            print(f"shell: {config.shell_kind}")
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
    session_store: JsonSessionStore | None = None,
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
    session_store: JsonSessionStore | None = None,
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
    session_store: JsonSessionStore | None = None,
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
                pending = input(render_prompt(session.agent.config)).strip()
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


ACTION_BLOCK_PATTERN = re.compile(r"```(?:bash-action|tool-action)\n.*?```", re.DOTALL)


def print_compact_event(event: LoopEvent) -> None:
    if event.type == LoopEventTypes.STEP_START:
        print(f"\n--- step {event.data[LoopEventData.STEP]}/{event.data[LoopEventData.MAX_STEPS]} ---")
    elif event.type == LoopEventTypes.MODEL_OUTPUT:
        print_compact_model_output(str(event.data[LoopEventData.CONTENT]))
    elif event.type == LoopEventTypes.TOOL_CALL_START:
        print(f"[action] {summarize_tool_call(str(event.data[LoopEventData.TOOL]), str(event.data[LoopEventData.PAYLOAD]))}")
    elif event.type == LoopEventTypes.TOOL_CALL_RESULT:
        summary = summarize_observation(str(event.data[LoopEventData.OBSERVATION]))
        if summary:
            print(f"[observation] {summary}")
    elif event.type == LoopEventTypes.DONE:
        print(f"[done] {event.data[LoopEventData.REASON]}")
    elif event.type == LoopEventTypes.MAX_STEPS:
        print(f"[max_steps] {event.data[LoopEventData.MAX_STEPS]}")


def print_compact_model_output(content: str) -> None:
    stripped = ACTION_BLOCK_PATTERN.sub("", content).strip()
    action_count = len(ACTION_BLOCK_PATTERN.findall(content))
    if stripped:
        print(stripped)
    elif action_count:
        print(f"model requested {action_count} action(s)")


def summarize_tool_call(tool: str, payload: str) -> str:
    if tool == Tools.SHELL:
        return f"shell: {first_line(payload)}"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return f"{tool}: {first_line(payload)}"
    if not isinstance(data, dict):
        return f"{tool}: {first_line(payload)}"

    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD, Tools.FILE_INFO}:
        return f"{tool}: {compact_path(str(data.get('path', '<missing>')))}"
    if tool == Tools.SEARCH:
        pattern = str(data.get("pattern", ""))
        path = compact_path(str(data.get("path", ".")))
        return f"search: {path} for {pattern!r}"
    if tool == Tools.WRITE_FILE:
        content = str(data.get("content", ""))
        return f"write_file: {compact_path(str(data.get('path', '<missing>')))} ({len(content)} chars)"
    if tool == Tools.EDIT_FILE:
        return (
            f"edit_file: {compact_path(str(data.get('path', '<missing>')))} "
            f"lines {data.get('start_line', '?')}-{data.get('end_line', '?')}"
        )
    return f"{tool}: {first_line(payload)}"


def summarize_observation(observation: str) -> str:
    if is_plan_mode_block(observation):
        reason = extract_output_block(observation).strip() or "plan mode blocked execution"
        return f"skipped: {reason}"

    status = extract_field(observation, "status")
    exit_code = extract_field(observation, "exit_code")
    command = extract_command_block(observation)
    output = extract_output_block(observation)
    prefix = summarize_command(command)
    metrics = summarize_output(output)
    pieces = [piece for piece in (prefix, f"status={status}" if status else "", f"exit={exit_code}" if exit_code else "", metrics) if piece]
    return ", ".join(pieces) if pieces else first_line(observation)


def summarize_command(command: str) -> str:
    if not command:
        return ""
    parts = command.split(maxsplit=1)
    tool = parts[0]
    target = compact_path(parts[1]) if len(parts) > 1 else ""
    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD, Tools.FILE_INFO, Tools.SEARCH, Tools.WRITE_FILE, Tools.EDIT_FILE}:
        return f"{tool} {target}".strip()
    return first_line(command)


def summarize_output(output: str) -> str:
    if not output:
        return "output=0 chars"
    lines = output.splitlines()
    if output.strip() == "no matches":
        return "no matches"
    if output.startswith("search timed out") or "search timed out" in output:
        return f"{len(lines)} lines, timed out"
    return f"{len(lines)} lines, {len(output)} chars"


def extract_field(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_command_block(text: str) -> str:
    return extract_named_block(text, "command")


def extract_output_block(text: str) -> str:
    return extract_named_block(text, "output")


def extract_named_block(text: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\n```text\n(.*?)\n```", text, flags=re.DOTALL)
    return match.group(1) if match else ""


def first_line(text: str, limit: int = 160) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def compact_path(path_text: str) -> str:
    path = Path(path_text)
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except (OSError, ValueError):
        return path.name if path.is_absolute() else path_text


def is_plan_mode_block(observation: object) -> bool:
    text = str(observation)
    return "plan mode does not execute" in text


def is_plan_mode_write_block(observation: object) -> bool:
    text = str(observation)
    return "plan mode does not execute write_file" in text or "plan mode does not execute edit_file" in text


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


def render_prompt(config: AgentConfig) -> str:
    cyan = "\033[36m" if sys.stdout.isatty() else ""
    dim = "\033[2m" if sys.stdout.isatty() else ""
    reset = "\033[0m" if sys.stdout.isatty() else ""
    cwd = compact_path(str(config.cwd))
    model = f"{config.provider}/{config.model}"
    return (
        f"\n{cyan}╭─ minimal-agent{reset} {dim}{cwd}{reset}\n"
        f"{dim}│ model: {model}  permission: {config.permission_mode}{reset}\n"
        f"{cyan}╰─>{reset} "
    )


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


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}m{remainder:.1f}s"


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
    print("Commands: /help, /config, /profile, /permission, /mcp, /plugin, /plugins, /skill, /skills, /context, /history, /events, /memory, /plan, /workflow, /delegate, /review, /exit")


def print_interactive_help() -> None:
    print("Interactive commands:")
    for command, description in InteractiveCommands.DESCRIPTIONS.items():
        print(f"  {command:<12} {description}")


@dataclass
class InteractiveSession:
    agent: Agent
    context: ChatContext
    session_store: JsonSessionStore | None = None
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
    if command == InteractiveCommands.HISTORY:
        replay = handle_history_command(session, argument)
        return InteractiveCommandResult(handled=True, replay_message=replay)
    if command == InteractiveCommands.EVENTS:
        handle_events_command(session, argument)
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
    if action == "save":
        target = value or "project"
        if target not in {"project", "user"}:
            print(f"Usage: {InteractiveCommands.CONFIG} save [project|user]")
            return
        path = default_user_config_path() if target == "user" else session.agent.config.cwd / Defaults.LOCAL_CONFIG_FILE
        save_cli_config(path, session)
        print(f"config saved: {path}")
        return
    print(f"Usage: {InteractiveCommands.CONFIG} [show|save [project|user]]")


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
    rebuild_agent(session)
    print_config(session.agent.config)


def update_permission(session: InteractiveSession, mode: str) -> None:
    if mode not in PermissionModes.ALL:
        print(f"Usage: {InteractiveCommands.PERMISSION} {'|'.join(PermissionModes.ALL)}")
        return
    session.agent.config.permission_mode = mode
    rebuild_agent(session)
    print_config(session.agent.config)


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
    kind = ""
    limit = 20
    if argument:
        if argument.isdigit():
            limit = int(argument)
        else:
            kind = argument
    events = session.session_store.query_events(kind=kind or None, limit=limit)
    if not events:
        print("no events")
        return
    for index, event in enumerate(events, start=1):
        print(f"{index}: {event.timestamp} {event.kind} {json.dumps(event.data, ensure_ascii=False, sort_keys=True)}")


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
    config = config or session.agent.config
    model = session.agent.harness.model
    session.agent = Agent(config=config, harness=AgentHarness(config=config, model=model, session_store=session.session_store))


def print_config(config: AgentConfig) -> None:
    print(f"provider: {config.provider}")
    print(f"model: {config.model}")
    print(f"base_url: {config.base_url}")
    print(f"permission: {config.permission_mode}")
    print(f"shell: {config.shell_kind}")
    print(f"allow_network: {config.allow_network}")
    print(f"summarize_context: {config.summarize_context}")
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
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "permission": config.permission_mode,
        "shell": config.shell_kind,
        "allow_network": config.allow_network,
        "summarize_context": config.summarize_context,
        "max_steps": config.max_steps,
        "model_timeout": config.model_timeout,
        "context_compression_ratio": config.context_compression_ratio,
        "prompt_version": config.prompt_version,
    }
    if config.api_key:
        data["api_key"] = config.api_key
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


def detect_explicit_options(argv: list[str]) -> set[str]:
    option_map = {
        "--provider": "provider",
        "--profile": "profile",
        "--model": "model",
        "--base-url": "base_url",
        "--api-key": "api_key",
        "--cwd": "cwd",
        "--max-steps": "max_steps",
        "--timeout": "timeout",
        "--shell": "shell",
        "--model-timeout": "model_timeout",
        "--model-max-retries": "model_max_retries",
        "--model-max-concurrency": "model_max_concurrency",
        "--model-queue-timeout": "model_queue_timeout",
        "--model-circuit-failure-threshold": "model_circuit_failure_threshold",
        "--model-circuit-cooldown": "model_circuit_cooldown",
        "--model-price-input-per-1m": "model_price_input_per_1m",
        "--model-price-output-per-1m": "model_price_output_per_1m",
        "--usage-ledger": "usage_ledger",
        "--usage-subject": "usage_subject",
        "--usage-tenant": "usage_tenant",
        "--max-input-tokens": "max_input_tokens",
        "--max-output-tokens": "max_output_tokens",
        "--max-request-tokens": "max_request_tokens",
        "--daily-token-limit": "daily_token_limit",
        "--monthly-token-limit": "monthly_token_limit",
        "--max-request-cost": "max_request_cost",
        "--daily-cost-limit": "daily_cost_limit",
        "--monthly-cost-limit": "monthly_cost_limit",
        "--prompt-version": "prompt_version",
        "--permission": "permission",
        "--policy-file": "policy_file",
        "--mcp-config": "mcp_config",
        "--skill": "skill",
        "--session": "session",
        "--session-db": "session_db",
        "--config-file": "config_file",
    }
    flag_map = {
        "--bill-failed-requests": "bill_failed_requests",
        "--allow-network": "allow_network",
        "--summarize-context": "summarize_context",
        "--no-summarize-context": "summarize_context",
        "--no-session": "session",
    }
    explicit = set()
    for item in argv:
        for option, name in option_map.items():
            if item == option or item.startswith(f"{option}="):
                explicit.add(name)
        if item in flag_map:
            explicit.add(flag_map[item])
    return explicit


def parse_optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


def parse_optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    return float(value) if value else None


def parse_model_fallback_env() -> list[str]:
    raw = os.getenv("AGENT_MODEL_FALLBACKS")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if isinstance(data, list):
        return [json.dumps(item) if isinstance(item, dict) else str(item) for item in data]
    return [raw]


def parse_model_routes(raw_routes: list[str]) -> tuple[ModelRoute, ...]:
    routes: list[ModelRoute] = []
    for raw in raw_routes:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid --model-fallback JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError("--model-fallback must be a JSON object")
        try:
            routes.append(
                ModelRoute(
                    provider=data["provider"],
                    model=data["model"],
                    base_url=data["base_url"],
                    api_key=data.get("api_key"),
                    timeout=data.get("timeout"),
                    max_retries=int(data.get("max_retries", 0)),
                    price_input_per_1m=float(data.get("price_input_per_1m", 0.0)),
                    price_output_per_1m=float(data.get("price_output_per_1m", 0.0)),
                    weight=int(data.get("weight", 1)),
                )
            )
        except KeyError as exc:
            raise ConfigurationError(f"--model-fallback missing required field: {exc}") from exc
    return tuple(routes)


if __name__ == "__main__":
    raise SystemExit(main())
