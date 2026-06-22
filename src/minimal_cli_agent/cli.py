from __future__ import annotations

import argparse
import os
from pathlib import Path

from minimal_cli_agent.agent import Agent, print_event
from minimal_cli_agent.exceptions import AgentError
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.profiles import resolve_profile
from minimal_cli_agent.types import AgentConfig, ChatContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimal-agent", description="Run a minimal terminal AI agent.")
    parser.add_argument("task", nargs="*", help="Task for the agent.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start a multi-turn interactive CLI session.")
    parser.add_argument("--profile", choices=["ollama", "codex", "claude", "gemini"], default=os.getenv("AGENT_PROFILE"))
    parser.add_argument("--provider", choices=["ollama", "openai-compatible", "anthropic", "gemini", "codex"], default=os.getenv("AGENT_PROVIDER", "ollama"))
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", "qwen3:4b"))
    parser.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--api-key", default=os.getenv("AGENT_API_KEY"))
    parser.add_argument("--cwd", type=Path, default=Path(os.getenv("AGENT_CWD", ".")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("AGENT_MAX_STEPS", "20")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AGENT_COMMAND_TIMEOUT", "30")))
    parser.add_argument("--show-config", action="store_true", help="Print resolved provider/model/base URL without secrets.")
    parser.add_argument(
        "--permission",
        choices=["default", "autoEdit", "plan", "yolo"],
        default=os.getenv("AGENT_PERMISSION", "default"),
    )
    parser.add_argument("--session", type=Path, help="Persist messages to this JSON session file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = resolve_profile(AgentConfig(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            cwd=args.cwd.resolve(),
            max_steps=args.max_steps,
            command_timeout=args.timeout,
            permission_mode=args.permission,
        ), args.profile)
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
) -> int:
    stream = agent.chat_stream(message, context)
    while True:
        try:
            event = next(stream)
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
    print("minimal-agent interactive mode. Type /exit or /quit to stop.")
    pending = first_message
    while True:
        if pending is None:
            try:
                pending = input("\nminimal-agent> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

        if pending in {"/exit", "/quit"}:
            return 0
        if pending:
            run_turn(agent, pending, context, session_store)
        pending = None


if __name__ == "__main__":
    raise SystemExit(main())
