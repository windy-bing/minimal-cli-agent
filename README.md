# minimal-cli-agent

Language: English | [中文](README.zh-CN.md)

A terminal-first AI coding agent with persistent sessions, structured tools, permissions, local skills, MCP/plugin loading, workflow state, and model routing. It keeps the core loop small: ask the model for explicit actions, execute those actions through a harness, append observations, and continue until the model exits or the user interrupts.

![minimal-cli-agent Codex profile demo](docs/assets/codex-profile.png)

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
minimal-agent
```

The default run starts an interactive session, loads local config, persists transcript state in `.agent/session.json`, and uses unlimited loop steps until exit.

With Ollama:

```bash
ollama pull qwen3:4b
ollama serve
minimal-agent
```

Read-only inspection:

```bash
minimal-agent --permission plan "Inspect this repository and summarize risks"
```

OpenAI-compatible endpoint:

```bash
AGENT_PROVIDER=openai-compatible \
AGENT_BASE_URL=https://api.openai.com/v1 \
AGENT_API_KEY=... \
AGENT_MODEL=gpt-4.1-mini \
minimal-agent "Check the tests and summarize failures"
```

## Daily Commands

Run without arguments to enter the REPL:

```bash
minimal-agent
```

Useful slash commands:

```text
/help
/model qwen3:4b
/provider ollama
/base-url http://localhost:11434
/permission autoEdit
/config
/config save
/context status
/context compact
/history 20
/events
/doctor
/policy
/skills
/skills load all
/mcp examples/mcp/my-coffee.json
/plugin my-plugin
/plan improve test coverage
/workflow create improve test coverage
/delegate inspect README risks
/review src/minimal_cli_agent
```

Session files are enabled by default. Use `--session path/to/session.json` to choose a file or `--no-session` to run without persistence.

## Configuration

Startup defaults are read in this order:

1. Built-in defaults.
2. User config: `~/.minimal-agent/config.json`.
3. Project config: `.minimal-agent.json`.
4. Environment variables.
5. CLI flags.
6. REPL changes saved with `/config save`.

Common options:

```text
--profile        ollama, codex, claude, or gemini
--provider       ollama, openai-compatible, anthropic, gemini, or codex
--model          model name
--base-url       provider base URL
--api-key        API key for OpenAI-compatible endpoints
--permission     default, autoEdit, plan, or yolo
--cwd            workspace directory
--max-steps      maximum loop iterations; 0 means unlimited
--timeout        command timeout in seconds
--model-timeout  model request timeout in seconds
--session        JSON or SQLite session path
--no-session     disable persistence
--policy-file    shell and write-scope policy JSON
--mcp-config     MCP server config JSON
--plugin         plugin manifest name or path
--skill          local SKILL.md name or path
```

Profiles:

- `ollama`: local Ollama defaults from `OLLAMA_MODEL` and `OLLAMA_BASE_URL`.
- `codex`: reads `~/.codex/config.toml` and can use Codex CLI login as a model adapter.
- `claude`: reads Claude local settings plus Anthropic environment variables.
- `gemini`: reads Gemini model, base URL, and API key environment variables.

## Action Format

The model must request tools explicitly:

````text
```bash-action
ls -la
```

```tool-action
{"tool":"read_file","path":"README.md"}
```

```tool-action
{"tool":"edit_file","path":"notes.txt","start_line":2,"end_line":3,"content":"replacement"}
```
````

Built-in structured tools include `read_file`, `read_tail`, `read_forward`, `file_info`, `search`, `write_file`, and `edit_file`. Read tools can run in parallel when safe; write tools remain ordered behind write barriers.

## Safety Model

Permission modes:

- `plan`: read-only. Shell and file writes are skipped as observations.
- `default`: asks before shell commands and writes.
- `autoEdit`: allows file writer tools, while shell commands still pass policy checks.
- `yolo`: executes approved tool classes without interactive confirmation.

Policy files can add command allow prefixes, deny tokens, workspace write allow/deny globs, sensitive path tokens, and network command tokens. Built-in hard gates remain active.

## MCP, Skills, And Plugins

MCP config uses the common `mcpServers` JSON shape:

```json
{
  "mcpServers": {
    "my-coffee": {
      "type": "streamablehttp",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${TOKEN}"
      }
    }
  }
}
```

Load it at startup or in the REPL:

```bash
minimal-agent --mcp-config examples/mcp/my-coffee.json --skill my-coffee
```

```text
/mcp examples/mcp/my-coffee.json
/skill my-coffee
/plugin my-plugin
```

Skills are local `SKILL.md` instruction bundles. Plugins can contribute skills and MCP server configs through manifests.

## Architecture

Core modules:

```text
agent.py          loop and event stream
harness.py        model, tools, memory, context, and policy boundary
cli.py            REPL and command orchestration
cli_config.py     argument and config resolution
cli_format.py     compact terminal formatting
parser.py         action block parsing and sequence validation
tool_registry.py  tool specs and schemas
tool_pipeline.py  validation, policy, retries, and execution events
file_tools.py     structured workspace file tools
memory.py         JSON and SQLite transcript/event stores
model_gateway.py  model routing, fallback, quotas, and usage ledger
mcp_tools.py      streamable HTTP MCP adapter
plugins.py        manifest loading and discovery
workflow.py       typed workflow state
subagent.py       scoped sub-agent execution
```

More detail:

- [Architecture](docs/architecture.md)
- [Harness gap analysis and roadmap](docs/harness-gap-analysis.zh-CN.md)

## Development

Run tests:

```bash
python -m unittest discover tests
```

Run type checks:

```bash
pyright
```

CI runs both unit tests and Pyright through GitHub Actions.
