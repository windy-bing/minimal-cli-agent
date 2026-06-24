# minimal-cli-agent

Language: English | [中文](README.zh-CN.md)

A small CLI agent inspired by the [Minimal AI agent tutorial](https://minimal-agent.com/). It implements the core loop from the article: ask a model for one action, parse the action, execute it in a terminal environment, append the observation, and repeat.

The project starts intentionally small, but the code is split into replaceable modules so it can grow toward sub-agents, group sessions, memory, permissions, skills, MCP, plugins, and workflow delegation.

![minimal-cli-agent Codex profile demo](docs/assets/codex-profile.png)

## What It Does Now

- Runs as a terminal CLI.
- Supports multi-turn interactive sessions with `--interactive`; running without a task starts the same REPL.
- Supports local Ollama chat models by default.
- Supports Ollama, Codex CLI login, Claude/Anthropic, and Gemini profiles.
- Supports OpenAI-compatible `/chat/completions` endpoints directly.
- Parses exactly one action formatted as:

````text
```bash-action
ls -la
```
````

- Executes commands with timeout and non-interactive environment variables.
- Redacts common API keys, bearer tokens, and secret-looking values from command observations.
- Blocks obvious network shell commands unless `--allow-network` is passed.
- Supports product permission modes: `default`, `autoEdit`, `plan`, and `yolo`.
- Persists session messages and permission audit events to JSON when `--session` is provided.
- Applies a simple context compaction guard when the transcript gets large.
- Exposes a stateless `Agent.chat_stream(message, context)` API that yields loop events.
- Returns recoverable tool discovery and validation observations instead of surfacing raw exceptions.
- Keeps the agent loop behind an `AgentHarness` boundary so tools, memory, policy, context, and environments can evolve independently.

## Why Start This Way

The article's key point is that a useful CLI agent does not need a large framework at first. The minimal loop is enough to create real behavior:

1. Keep messages.
2. Query the language model.
3. Parse the requested action.
4. Execute the action.
5. Return command output as the next observation.

This repository keeps that loop visible in `src/minimal_cli_agent/agent.py`, while separating the model, parser, environment, and memory layers so each part can be replaced later.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`httpx[socks]` is included so model requests can use SOCKS proxies from `http_proxy`, `https_proxy`, or `all_proxy`.

## Run With Ollama

```bash
ollama pull qwen3:4b
ollama serve
minimal-agent --permission default "List the files in this project, then exit"
```

Equivalent module form:

```bash
python -m minimal_cli_agent.cli --permission default "List the files in this project, then exit"
```

## Run Without Executing Commands

```bash
minimal-agent --permission plan "Inspect this repository structure"
```

## Interactive Session

Start a multi-turn CLI session:

```bash
minimal-agent --profile codex --permission plan --interactive
```

You can also pass the first message and then continue chatting:

```bash
minimal-agent --profile codex --permission plan --interactive "Analyze this project"
```

Type `/help` to list interactive commands. Type `/`, `/exit`, `/quit`, `exit`, or `quit` for quick command handling. If `--session path/to/session.json` is provided, messages are saved after each turn and loaded again on the next run.

In interactive mode, normal conversation can be answered directly. The model only needs a `bash-action` block when it wants to inspect files or run a command.

## OpenAI-Compatible Endpoint

```bash
AGENT_PROVIDER=openai-compatible \
AGENT_BASE_URL=https://api.openai.com/v1 \
AGENT_API_KEY=... \
AGENT_MODEL=gpt-4.1-mini \
minimal-agent --permission default "Check the tests and summarize failures"
```

## Profiles

Use `--profile` to read default local configuration for common CLI model setups:

```bash
minimal-agent --profile ollama "List files, then exit"
minimal-agent --profile codex "List files, then exit"
minimal-agent --profile claude "List files, then exit"
minimal-agent --profile gemini "List files, then exit"
```

Profile behavior:

- `ollama`: reads `OLLAMA_MODEL` and `OLLAMA_BASE_URL`, defaults to local Ollama.
- `codex`: reads `~/.codex/config.toml` for the model. If `OPENAI_API_KEY` or `OPENAI_BASE_URL` is explicitly set, it uses the OpenAI-compatible provider. Otherwise, when `~/.codex/auth.json` contains a Codex login `tokens.access_token`, it uses the local Codex CLI as the request adapter instead of sending that token to `api.openai.com`.
- `claude`: reads `~/.claude/settings.json` for model and Anthropic proxy env, plus `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`.
- `gemini`: reads `GEMINI_MODEL`, `GEMINI_BASE_URL`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

Explicit CLI options such as `--model`, `--base-url`, and `--api-key` take precedence over profile-specific environment variables or config files.

## CLI Options

```text
--provider       ollama, openai-compatible, anthropic, gemini, or codex
--profile        ollama, codex, claude, or gemini
--model          model name
--base-url       provider base URL
--api-key        API key for OpenAI-compatible endpoints
--cwd            working directory for commands
--max-steps      maximum agent loop iterations
--timeout        command timeout in seconds
--allow-network  allow shell commands with obvious network access
--interactive    start a multi-turn interactive CLI session
--permission     default, autoEdit, plan, or yolo
--session        JSON file for persisted messages
```

## Project Layout

```text
src/minimal_cli_agent/
  agent.py        control loop
  harness.py      runtime boundary for model, tools, memory, context, and policy
  interfaces.py   protocol contracts for extension points
  tool_registry.py tool registration and execution boundary
  tool_pipeline.py staged tool execution pipeline
  policy.py       shell permission policy
  context.py      context preparation boundary
  model.py        Ollama, OpenAI-compatible, Anthropic, Gemini HTTP clients, and Codex CLI adapter
  parser.py       bash-action parser
  environment.py  local shell execution
  memory.py       JSON session store and basic context compaction
  prompts.py      system prompt and format reminder
```

## Planned Extensions

See [docs/architecture.md](docs/architecture.md) for the larger design.
See [docs/harness-gap-analysis.zh-CN.md](docs/harness-gap-analysis.zh-CN.md) for a detailed harness gap analysis and roadmap.

- Context compression with model-generated summaries.
- SubAgent execution for explorer, worker, and verifier roles.
- GroupSession coordination for multi-agent work.
- Layered memory management.
- Safety and permission policies with audit records.
- Skill, MCP, and plugin registration.
- Workflow delegation primitives such as `plan`, `delegate`, `wait`, `merge`, and `verify`.

## Boundary Status

Implemented:

- Stateless `Agent.chat_stream(message, context)` entry point.
- `LoopEvent` / `LoopResult` for event-oriented loop output.
- `ToolRegistry` and staged `ToolExecutionPipeline`.
- Permission decision type with `allow`, `ask`, `deny`, and `skip`.
- Product permission modes: `default`, `autoEdit`, `plan`, `yolo`.
- JSON session event log for permission approval audit records.

Reserved but intentionally minimal:

- Context compression is local truncation, not model summarization yet.
- `autoEdit` currently behaves like `default` because no file-edit tools exist yet.
- Session persistence is JSON, not SQLite or a queryable event database.

Not implemented yet:

- Multiple tool calls per model turn.
- Concurrent tool execution and file locks.
- SubAgent and GroupSession runtime.
- MCP, plugin, and skill discovery.
- Workflow scheduler or delegation engine.

## Notes From The Reference Article

The reference tutorial emphasizes a few practical details this project keeps:

- Use an explicit action format instead of guessing intent.
- Feed command output back to the model as observations.
- Treat timeouts, format errors, and permission denials as recoverable observations.
- Set non-interactive environment variables such as `PAGER=cat` and `PIP_PROGRESS_BAR=off`.
- Keep model and environment as separate modules so either can be swapped.
