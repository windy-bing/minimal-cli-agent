from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import os
import sys
from pathlib import Path

from minimal_cli_agent.agent import Agent, print_event
from minimal_cli_agent.constants import Defaults, InteractiveCommands, PermissionModes, Profiles, Providers
from minimal_cli_agent.context import total_message_chars
from minimal_cli_agent.exceptions import AgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import compact_messages
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.plan import PLAN_METADATA_KEY, build_plan_prompt, create_plan_artifact, format_plan_artifact
from minimal_cli_agent.prompts import INTERACTIVE_SYSTEM_PROMPT
from minimal_cli_agent.profiles import resolve_profile
from minimal_cli_agent.types import AgentConfig, ChatContext, LoopOptions


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
    parser.add_argument("--model-timeout", type=int, default=int(os.getenv("AGENT_MODEL_TIMEOUT", Defaults.MODEL_TIMEOUT)))
    parser.add_argument("--allow-network", action="store_true", help="Allow shell commands with obvious network access.")
    parser.add_argument("--policy-file", type=Path, help="JSON file with additional shell policy deny tokens.")
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
            model_timeout=args.model_timeout,
            permission_mode=args.permission,
            allow_network=args.allow_network,
            policy_file=args.policy_file.resolve() if args.policy_file else None,
            summarize_context=args.summarize_context,
        ), args.profile, explicit_options=explicit_options)
        if args.show_config:
            print(f"profile: {args.profile or '<none>'}")
            print(f"provider: {config.provider}")
            print(f"model: {config.model}")
            print(f"base_url: {config.base_url}")
            print(f"api_key_present: {bool(config.api_key)}")
            print(f"api_key_length: {len(config.api_key or '')}")
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
    stream = agent.chat_stream(message, context, options)
    while True:
        try:
            event = next(stream)
        except AgentError as exc:
            print(f"error: {exc}")
            return 1
        except StopIteration as exc:
            result = exc.value
            context.messages = result.final_messages
            if session_store:
                session_store.save(context.messages)
            return 0 if result.success else 1
        print_event(event)


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
            exit_code = run_turn(
                session.agent,
                pending,
                session.context,
                session.session_store,
                LoopOptions(allow_final_text=True, system_prompt=INTERACTIVE_SYSTEM_PROMPT),
            )
            if exit_code != 0:
                print("Turn failed. You can retry, adjust options, or type /exit.")
        pending = None


def print_quick_command_hint() -> None:
    print("Commands: /help, /config, /profile, /permission, /context, /plan, /review, /exit")


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
    print(f"allow_network: {config.allow_network}")
    print(f"summarize_context: {config.summarize_context}")


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


if __name__ == "__main__":
    raise SystemExit(main())
