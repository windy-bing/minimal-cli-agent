# minimal-cli-agent

Language: English | [中文](README.zh-CN.md)

A small CLI agent inspired by the [Minimal AI agent tutorial](https://minimal-agent.com/). It implements the core loop from the article: ask a model for one or more actions, parse the actions, execute them in a terminal environment, append observations, and repeat.

The project starts intentionally small, but the code is split into replaceable modules so it can grow toward sub-agents, group sessions, memory, permissions, skills, MCP, plugins, and workflow delegation.

![minimal-cli-agent Codex profile demo](docs/assets/codex-profile.png)

## What It Does Now

- Runs as a terminal CLI.
- Supports multi-turn interactive sessions with `--interactive`; running without a task starts the same REPL.
- Supports slash commands for runtime profile/model/permission/context/plan/review control.
- Supports local Ollama chat models by default.
- Supports Ollama, Codex CLI login, Claude/Anthropic, and Gemini profiles.
- Supports OpenAI-compatible `/chat/completions` endpoints directly.
- Parses one or more actions per model turn, formatted as shell or file tool actions:

````text
```bash-action
ls -la
```

```tool-action
{"tool":"read_file","path":"README.md"}
```

```tool-action
{"tool":"read_tail","path":"README.md","lines":80}
```

```tool-action
{"tool":"read_forward","path":"README.md","offset":0,"limit":8192}
```

```tool-action
{"tool":"search","pattern":"permission","path":".","top_k":20,"timeout_ms":2000,"ignore_dirs":["dist"],"include_extensions":[".py"]}
```

```tool-action
{"tool":"write_file","path":"notes/todo.txt","content":"hello"}
```
````

- Executes commands with timeout and non-interactive environment variables.
- Reads, pages, tails, searches, and writes workspace files through structured tools instead of forcing file operations through shell commands.
- Search supports `top_k`, `max_files`, `timeout_ms`, extra `ignore_dirs`, and `include_extensions`.
- Search reads workspace `.gitignore` and `.agentignore` files for common directory and glob ignore patterns.
- Validates JSON, TOML, and XML before `write_file` writes them; YAML is validated when PyYAML is available.
- Redacts common API keys, bearer tokens, and secret-looking values from command observations.
- Blocks obvious network shell commands unless `--allow-network` is passed.
- Supports additional shell policy deny rules through `--policy-file`.
- Supports product permission modes: `default`, `autoEdit`, `plan`, and `yolo`.
- Persists session messages and permission audit events to JSON when `--session` is provided.
- Applies a simple context compaction guard when the transcript gets large.
- Can use model-generated context summaries with `--summarize-context`.
- Exposes a stateless `Agent.chat_stream(message, context)` API that yields loop events.
- Returns recoverable tool discovery and validation observations instead of surfacing raw exceptions.
- Unknown tools return safe close-match suggestions without automatically executing guesses.
- Tool parameter validation returns field-level repair observations for structured payloads.
- Tool pipeline decision hooks can arbitrate `allow` / `ask` / `deny` / `skip` decisions before confirmation.
- Tool observations use a consistent `status`, `exit_code`, `command`, and `output` format.
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

In interactive mode, normal conversation can be answered directly. The model only needs an action block when it wants to inspect files, edit files, or run a command.

Use `--permission autoEdit` when you want the loop to modify project files through `write_file` without asking every time. `plan` remains read-only: it can read files but skips shell commands and file writes.

Most startup options can also be changed inside the REPL:

```text
/config
/profile codex
/provider ollama
/model qwen3:4b
/base-url http://localhost:11434
/permission autoEdit
/network on
/summarize on
/context status
/context compact
/context clear
/plan improve test coverage
/plan show
/plan clear
/review src/minimal_cli_agent
```

`/plan <goal>` runs an isolated planning turn with `plan` permissions, saves a typed plan artifact, and does not merge the planning transcript into the active chat context. With `--session`, the active plan is persisted alongside messages and audit events.

`/review [path]` runs a review turn through the same agent loop, so it can inspect files with `read_file` and use the current permission mode.

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
--model-timeout  model request timeout in seconds
--allow-network  allow shell commands with obvious network access
--policy-file    JSON file with additional shell policy deny tokens
--summarize-context use the model to summarize old context when compacting
--interactive    start a multi-turn interactive CLI session
--permission     default, autoEdit, plan, or yolo
--session        JSON file for persisted messages
```

## Project Layout

Policy files add deny rules without weakening the built-in hard gates:

```json
{
  "deny_command_tokens": ["custom-danger"],
  "sensitive_path_tokens": ["secrets.local"],
  "network_command_tokens": ["my-net-tool "]
}
```

When using `--profile codex`, the Codex CLI is used only as a model adapter. It is prompted to return the next assistant message, including `bash-action` or `tool-action` blocks when workspace work is needed. The minimal-agent loop remains responsible for executing commands and editing files. Increase `--model-timeout` if the adapter needs more time.

```text
src/minimal_cli_agent/
  agent.py        control loop
  harness.py      runtime boundary for model, tools, memory, context, and policy
  interfaces.py   protocol contracts for extension points
  tool_registry.py tool registration and execution boundary
  tool_pipeline.py staged tool execution pipeline
  policy.py       shell permission policy
  context.py      context preparation boundary
  file_tools.py   workspace read_file, read_tail, read_forward, search, and write_file tools
  model.py        Ollama, OpenAI-compatible, Anthropic, Gemini HTTP clients, and Codex CLI adapter
  parser.py       bash-action and tool-action parser
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
- Multiple action blocks per model turn are executed sequentially in output order.
- `ToolRegistry` and staged `ToolExecutionPipeline`.
- Built-in `read_file`, `read_tail`, `read_forward`, `search`, and `write_file` tools for bounded workspace file access.
- `search` respects built-in ignore dirs, explicit `ignore_dirs`, and workspace `.gitignore` / `.agentignore` patterns.
- Structured write validation for JSON, TOML, XML, and optional PyYAML-backed YAML.
- `ToolSpec` supports lightweight parameter schemas with field-level validation errors.
- `ResolveDecision` supports decision hooks that can override policy decisions before confirmation.
- Permission decision type with `allow`, `ask`, `deny`, and `skip`.
- Product permission modes: `default`, `autoEdit`, `plan`, `yolo`.
- JSON session event log for permission approval audit records.
- `/plan` creates an isolated typed plan artifact that can be shown, cleared, and persisted in the session file.
- Optional model-generated context summaries with `--summarize-context`.

Reserved but intentionally minimal:

- Context compression defaults to local truncation; model summarization is opt-in.
- `autoEdit` automatically approves `write_file`; shell commands still ask for confirmation.
- Session persistence is JSON, not SQLite or a queryable event database.

Not implemented yet:

- Parallel tool execution and file locks.
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
