from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
import os
import re
import sys
from pathlib import Path

from minimal_cli_agent.agent import Agent, print_event
from minimal_cli_agent.constants import Defaults, InteractiveCommands, LoopEventData, LoopEventTypes, PermissionModes, Profiles, Providers, ToolDecisionKinds, ToolPayloadFields, Tools
from minimal_cli_agent.context import total_message_chars
from minimal_cli_agent.exceptions import AgentError, ConfigurationError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.plan import PLAN_METADATA_KEY, PlanArtifact, build_plan_prompt, create_plan_artifact, extract_plan_paths, format_plan_artifact, format_plan_execution_context
from minimal_cli_agent.prompts import INTERACTIVE_SYSTEM_PROMPT, SYSTEM_PROMPT
from minimal_cli_agent.profiles import resolve_profile
from minimal_cli_agent.skills import build_system_prompt, resolve_skill_path, resolve_skill_paths
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopEvent, LoopOptions, Message, ModelRoute, ToolCall, ToolDecision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimal-agent", description="Run a minimal terminal AI agent.")
    parser.add_argument("task", nargs="*", help="Task for the agent.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start a multi-turn interactive CLI session.")
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
    parser.add_argument("--skill", action="append", default=[], help="Load a skill by path or by name under skills/<name>.")
    parser.add_argument("--summarize-context", action="store_true", help="Use the model to summarize old context when compacting.")
    parser.add_argument("--show-config", action="store_true", help="Print resolved provider/model/base URL without secrets.")
    parser.add_argument(
        "--permission",
        choices=PermissionModes.ALL,
        default=os.getenv("AGENT_PERMISSION", PermissionModes.DEFAULT),
    )
    parser.add_argument("--session", type=Path, help="Persist messages to this JSON session file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    explicit_options = detect_explicit_options(raw_argv)

    try:
        config = resolve_profile(AgentConfig(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            cwd=args.cwd.resolve(),
            max_steps=args.max_steps,
            command_timeout=args.timeout,
            shell_kind=args.shell,
            model_timeout=args.model_timeout,
            model_fallbacks=parse_model_routes(args.model_fallback),
            model_max_retries=args.model_max_retries,
            model_max_concurrency=args.model_max_concurrency,
            model_queue_timeout=args.model_queue_timeout,
            model_circuit_failure_threshold=args.model_circuit_failure_threshold,
            model_circuit_cooldown=args.model_circuit_cooldown,
            model_price_input_per_1m=args.model_price_input_per_1m,
            model_price_output_per_1m=args.model_price_output_per_1m,
            usage_ledger_path=args.usage_ledger.resolve() if args.usage_ledger else None,
            usage_subject=args.usage_subject,
            usage_tenant=args.usage_tenant,
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
            max_request_tokens=args.max_request_tokens,
            daily_token_limit=args.daily_token_limit,
            monthly_token_limit=args.monthly_token_limit,
            max_request_cost=args.max_request_cost,
            daily_cost_limit=args.daily_cost_limit,
            monthly_cost_limit=args.monthly_cost_limit,
            prompt_version=args.prompt_version,
            bill_failed_requests=args.bill_failed_requests,
            permission_mode=args.permission,
            allow_network=args.allow_network,
            policy_file=args.policy_file.resolve() if args.policy_file else None,
            mcp_config=args.mcp_config.resolve() if args.mcp_config else None,
            skill_paths=resolve_skill_paths(args.skill, args.cwd.resolve()),
            summarize_context=args.summarize_context,
        ), args.profile, explicit_options=explicit_options)
        if args.show_config:
            print(f"profile: {args.profile or '<none>'}")
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
            return 0

        task = " ".join(args.task).strip()
        session_store = JsonSessionStore(args.session) if args.session else None
        harness = AgentHarness(config=config, session_store=session_store)
        context = ChatContext(messages=session_store.load() if session_store else [])
        if session_store:
            plan = session_store.load_plan()
            if plan is not None:
                context.metadata[PLAN_METADATA_KEY] = plan
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


def run_turn_with_summary(
    agent: Agent,
    message: str,
    context: ChatContext,
    session_store: JsonSessionStore | None = None,
    options: LoopOptions | None = None,
    compact_output: bool = False,
) -> TurnExecutionSummary:
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
                return TurnExecutionSummary(exit_code=130, plan_blocked=plan_blocked)
            except AgentError as exc:
                print(f"error: {exc}")
                return TurnExecutionSummary(exit_code=1, plan_blocked=plan_blocked)
            except StopIteration as exc:
                result = exc.value
                context.messages = result.final_messages
                if session_store:
                    session_store.save(context.messages)
                return TurnExecutionSummary(exit_code=0 if result.success else 1, plan_blocked=plan_blocked)
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
    return LoopOptions(allow_final_text=options.allow_final_text, system_prompt=prompt)


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
    session = InteractiveSession(agent=agent, context=context, session_store=session_store)
    print("minimal-agent interactive mode. Type /help for commands, /exit to stop.")
    pending = first_message
    while True:
        if pending is None:
            try:
                pending = input("\nminimal-agent> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

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
            summary = run_turn_with_summary(
                session.agent,
                pending,
                session.context,
                session.session_store,
                LoopOptions(allow_final_text=True, system_prompt=INTERACTIVE_SYSTEM_PROMPT),
                compact_output=True,
            )
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

    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD}:
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
    if tool in {Tools.READ_FILE, Tools.READ_TAIL, Tools.READ_FORWARD, Tools.SEARCH, Tools.WRITE_FILE, Tools.EDIT_FILE}:
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


def print_quick_command_hint() -> None:
    print("Commands: /help, /config, /profile, /permission, /mcp, /skill, /context, /plan, /review, /exit")


def print_interactive_help() -> None:
    print("Interactive commands:")
    for command, description in InteractiveCommands.DESCRIPTIONS.items():
        print(f"  {command:<12} {description}")


@dataclass
class InteractiveSession:
    agent: Agent
    context: ChatContext
    session_store: JsonSessionStore | None = None


@dataclass(frozen=True)
class InteractiveCommandResult:
    handled: bool = False
    should_exit: bool = False


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
        print_config(session.agent.config)
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
    if command == InteractiveCommands.PLAN:
        handle_plan_command(session, argument)
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
    if command == InteractiveCommands.SKILL:
        update_skill(session, argument)
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


def update_skill(session: InteractiveSession, skill_text: str) -> None:
    if not skill_text:
        print(f"Usage: {InteractiveCommands.SKILL} my-coffee|path/to/SKILL.md")
        return
    path = resolve_skill_path(skill_text, session.agent.config.cwd)
    if path not in session.agent.config.skill_paths:
        session.agent.config.skill_paths = (*session.agent.config.skill_paths, path)
    prompt = build_system_prompt(INTERACTIVE_SYSTEM_PROMPT, session.agent.config.skill_paths)
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


def rebuild_agent(session: InteractiveSession, config: AgentConfig | None = None) -> None:
    config = config or session.agent.config
    session.agent = Agent(config=config, harness=AgentHarness(config=config, session_store=session.session_store))


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
    print(f"mcp_config: {config.mcp_config or '<none>'}")
    skills = ", ".join(path.parent.name for path in config.skill_paths) if config.skill_paths else "<none>"
    print(f"skills: {skills}")


def with_configured_skills(config: AgentConfig, options: LoopOptions | None) -> LoopOptions:
    base = options or LoopOptions()
    prompt = build_system_prompt(base.system_prompt or SYSTEM_PROMPT, config.skill_paths)
    return LoopOptions(allow_final_text=base.allow_final_text, system_prompt=prompt)


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
    }
    explicit = set()
    for item in argv:
        for option, name in option_map.items():
            if item == option or item.startswith(f"{option}="):
                explicit.add(name)
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
