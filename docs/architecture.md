# Architecture Notes

The first implementation follows the minimal-agent.com loop:

1. Keep a `messages` list.
2. Ask the model for the next response.
3. Parse one or more `bash-action` or `tool-action` blocks.
4. Execute actions sequentially in an environment.
5. Append observations back to messages.
6. Repeat until `exit` or `max_steps`.

## Current modules

- `Agent`: owns only the stateless reasoning/action/observation loop.
- `AgentHarness`: owns runtime wiring for model, context manager, session store, policy, tool registry, and environment.
- `ChatModel`: calls Ollama, OpenAI-compatible, Anthropic, Gemini chat endpoints, or the Codex CLI adapter.
- `LocalEnvironment`: executes shell commands through a `ShellAdapter` with timeout, non-interactive env vars, decoding, and shell metadata.
- `ShellPermissionPolicy`: approves, skips, or rejects shell and workspace file tools.
- `ToolRegistry`: registers executable tools behind one invocation boundary.
- `ToolExecutionPipeline`: runs Discovery, Validation, Permission, PreHook, ResolveDecision, Confirmation, Execution, PostHook, AutoVerify, and Formatting.
- `FileToolEnvironment`: reads, pages, tails, searches, and writes UTF-8 files inside the configured workspace.
- `mcp_tools`: loads streamable HTTP MCP configs and registers generic plus discovered MCP tools.
- `skills`: resolves local `SKILL.md` files and injects them into the system prompt.
- `parser`: extracts a single `bash-action` or `tool-action` code block.
- `memory`: persists sessions and applies a simple local compaction policy.
- `interfaces`: defines protocol boundaries for model, tool execution, sessions, context, and policy.

## Boundary Map

The project is meant to grow into a harness-style agent. The important rule is that `Agent` should not directly own runtime concerns.

| Concern | Current owner | Extension point |
| --- | --- | --- |
| Loop control | `Agent.chat_stream(message, context)` | Multi-action loop, planner loop, verifier loop |
| Model calls | `ModelGateway` through `AgentHarness` | LiteLLM, OpenAI Responses, more local models |
| Provider adapters | `ChatModel` | Additional provider SDKs, streaming adapters |
| Model profiles | `profiles.py` | cc-switch-compatible config discovery |
| Context window | `CompactingContextManager` | Model-based summarizer, retrieval-backed context |
| Session persistence | `JsonSessionStore` with lock-protected atomic JSON writes | SQLite, indexed event log, group session store |
| Tool invocation | `ToolRegistry` | MCP tools, browser tools, plugin tools |
| MCP adapters | `mcp_tools.py` | stdio MCP, authenticated config resolvers, plugin-managed MCP |
| Skills | `skills.py` | skill discovery, skill marketplace, scoped skill activation |
| Tool lifecycle | `ToolExecutionPipeline` | Hook arbitration, confirmation UI, retries, formatting |
| Shell execution | `LocalEnvironment` | Docker environment, remote sandbox, workspace fork |
| File tools | `FileToolEnvironment` | Patch hunks, format-aware validation, cross-process file locks |
| Permissions | `ShellPermissionPolicy` | Rule engine, approvals, audit log, scoped capabilities |
| Delegation | Not implemented | SubAgent runner and workflow scheduler |

This keeps the project extensible because new capabilities attach to harness boundaries instead of being hidden inside the agent loop.

## Harness Direction

The target shape is:

```text
Agent
  -> AgentHarness
      -> Model
      -> ContextManager
      -> SessionStore
      -> ToolRegistry
      -> PermissionPolicy
      -> Environment
```

The `Agent` asks for a model response and parses an action. The harness decides how context is prepared, which tool executes the action, whether permissions allow it, and where observations are stored.

## Implementation Status

Implemented:

- Stateless `Agent.chat_stream(message, context)` entry point.
- `ChatContext` carries session id, messages, and metadata from the caller.
- `LoopEvent` / `LoopResult` for stream-style UI integration.
- `max_steps=0` disables the loop step cap for long-running turns until model exit or user interruption.
- Multiple action blocks per model turn, executed sequentially in output order.
- Multi-turn CLI REPL that reuses one `ChatContext` across turns.
- REPL slash commands for runtime profile/provider/model/base URL/permission/network/context/history/plan/review control.
- Styled REPL prompts show active provider/model and permission mode; prompt `Ctrl-C` clears input, turn `Ctrl-C` interrupts the turn, and completed turns print elapsed time.
- Supplemental user input typed during a multi-step turn is added to the full conversation context before the next model call.
- Isolated `/plan` command that creates a typed plan artifact without merging planning transcript into active chat context.
- Context compaction triggers near the configured model context budget and preserves the initial user goal in compacted summaries.
- Optional model-generated context summaries with `--summarize-context`.
- Interactive prompt history is available through readline arrow keys and `/history [number]`.
- `ModelGateway` for provider/model abstraction, fallback routes, bounded retries, per-route concurrency, circuit breaking, usage ledgers, token/cost quotas, prompt version metadata, and API key pool rotation.
- JSON session event log for permission approval audit records.
- Lock-protected atomic JSON session writes with recent-message retention.
- Queryable recent session events through `/events`.
- `ToolRegistry` for tool discovery.
- Built-in workspace `read_file`, `read_tail`, `read_forward`, `search`, `write_file`, and `edit_file` tools.
- Manual MCP config loading with streamable HTTP JSON-RPC tools.
- Generic MCP list/call tools plus opt-in concrete tool registration from `tools/list` when `discoverTools` is enabled.
- Local `SKILL.md` loading into the system prompt through `--skill`.
- Workspace skill discovery and bulk loading through `/skills`.
- `search` has top-k, max-files, timeout, ignore-dir, extension, and `.gitignore` / `.agentignore` filters.
- Structured write validation for JSON, TOML, XML, and YAML when PyYAML is available.
- Tool aliases plus recoverable discovery and validation observations.
- Safe close-match suggestions for unknown tool names, without automatic fuzzy execution.
- Focused `ToolSpec.parameters_schema` JSON Schema validation with nested objects, arrays, enum, oneOf/anyOf, bounds, and field-level repair observations.
- File readers reject likely binary files and report file size, chars read, offsets, line paging state, and EOF metadata.
- `read_forward` supports both byte-window paging and line-window paging.
- `search` ranks matches before returning bounded top-k output.
- `ResolveDecision` decision hooks can override policy decisions before confirmation.
- Consistent tool observation formatting with `status`, `exit_code`, `command`, and `output`.
- Secret redaction for command output and observations.
- Network command hard gate with explicit `--allow-network` opt-in.
- Configurable policy file rules for shell allow prefixes, additional deny tokens, write allow paths, and write deny paths.
- Typed plan artifact stored in `ChatContext.metadata` and persisted in JSON sessions.
- Typed workflow state stored in `ChatContext.metadata` and persisted in JSON sessions.
- Read-only `SubAgentRunner` for isolated delegated tasks.
- Execute turns read the active plan, inject it into the system prompt, and constrain writer tools to planned paths when paths are known.
- ShellAdapter support for system shell, bash, zsh, sh, PowerShell, cmd, and Git Bash style execution with shell/cwd/encoding/path metadata in observations.
- Injectable permission confirmation handler; the CLI supports selectable allow-once, allow-session-action, and deny choices.
- Pyright `basic` type-checking configuration for `src` and `tests`.
- `ToolExecutionPipeline` with the full stage shape:

```text
Discovery -> Validation -> Permission -> PreHook -> ResolveDecision
  -> Confirmation -> Execution -> PostHook -> AutoVerify -> Formatting
```

- `ToolDecision` with `allow`, `ask`, `deny`, and `skip`.
- Product permission modes: `default`, `autoEdit`, `plan`, `yolo`.

Reserved:

- `ResolveDecision` has a decision hook baseline. Richer priority rules and conflict reporting remain reserved.
- `Confirmation` uses an injectable handler. CLI `input()` is the default adapter, and UI clients can provide their own handler.
- `autoEdit` automatically approves file writer tools; shell commands still ask until explicitly approved once or for the session.
- Tool schema validation is a focused JSON Schema subset, not a complete Draft implementation.
- The event log is JSON-backed and queryable for recent events, but not indexed like SQLite.
- MCP concrete tool discovery is opt-in at startup. Generic list/call tools remain available without touching the network.
- Skills can be discovered from the workspace and loaded manually or in bulk through `/skills`.

Not implemented yet:

- Parallel tool execution and cross-process file edit locks.
- File-level write locks.
- GroupSession event store.
- Workflow scheduler.
- Automatic MCP/plugin discovery.

## Roadmap

See [Harness Gap Analysis](harness-gap-analysis.zh-CN.md) for a more detailed comparison between the current implementation and production-grade agent harness requirements.

### Context Compression

The default `compact_messages` function trims older messages and inserts a local note only when the transcript approaches the configured context budget. When `--summarize-context` is enabled, the harness uses the active model to summarize older messages, then sends system prompt + summary + recent tail messages as working context.

- Keep system prompt, user goal, recent tool observations, and explicit decisions.
- Summarize older command/output history into a structured state block when enabled.
- Store both raw transcript and compacted working context.
- Use `--model-context-tokens` and `--context-compression-ratio` to compact by approximate model window instead of a fixed character threshold.

### SubAgent

Sub-agents should be separate `Agent` instances with scoped prompts, isolated sessions, and explicit outputs.

Suggested contract:

```text
delegate(task, scope, allowed_tools) -> SubAgentResult(summary, files_changed, confidence)
```

Use cases:

- Explorer agent for codebase reading.
- Worker agent for bounded file edits.
- Verifier agent for tests and review.

Current support:

- `SubAgentRunner` creates a separate `Agent` with isolated `ChatContext`.
- Delegated tasks run in `plan` permission mode by default, so they can inspect but not write.
- `/delegate <task>` runs the sub-agent and records the result in workflow state.

### GroupSession

A group session coordinates multiple agents around one user goal.

Core responsibilities:

- Shared objective and constraints.
- Per-agent transcript isolation.
- Shared memory/event log.
- Merge policy for results and conflicts.

### Memory Management

Memory should have layers:

- Transcript memory: full raw message log.
- Working memory: compacted context sent to the model.
- Project memory: durable facts about repo commands, conventions, and decisions.
- Task memory: current checklist, blockers, and delegated work.

### Safety and Permissions

Current product modes are `default`, `autoEdit`, `plan`, and `yolo`.

- `default`: shell commands ask for confirmation until approved once or for the session.
- `autoEdit`: automatically approves file writer tools; shell commands still ask until approved once or for the session.
- `plan`: allows read-only file tools, skips shell execution and file writes.
- `yolo`: allow execution unless a hard deny rule blocks it.

A production version should add:

- Richer allow rules and policy reports.
- Write scope restrictions.
- Destructive command detection.
- Queryable approval records and richer audit reports.

### Skills, MCP, and Plugins

Skills should be prompt-and-tool bundles discovered from a local directory.

Current support:

- `--mcp-config` loads streamable HTTP MCP servers from a common `mcpServers` JSON file.
- Every MCP server gets generic `mcp_<server>_list_tools` and `mcp_<server>_call_tool` actions.
- When `discoverTools` is enabled and `tools/list` works at startup, each remote tool is also exposed as a concrete local tool.
- `--skill` loads a local `SKILL.md` by name or path and injects it into the system prompt.
- `/skills` lists workspace skills under `skills/<name>/SKILL.md` and can load one skill or all discovered skills.
- `/mcp` and `/skill` can load configs or skills inside the interactive REPL.

Plugins should register:

- Skills and instructions.
- Tools or MCP server configs.
- Permission requirements.
- File ownership or workspace capabilities.

MCP support should sit behind a `ToolRegistry`, so shell tools and MCP tools share one invocation model.

### Workflow Delegation

Workflow delegation should be explicit rather than hidden in prompts.

Suggested primitives:

- `plan`: create a typed task graph.
- `delegate`: send a bounded task to a sub-agent.
- `wait`: collect a sub-agent result.
- `merge`: apply or reconcile results.
- `verify`: run tests, lint, or review checks.

The main agent remains responsible for final integration and user-facing status.

Current support:

- `/workflow create <goal>` creates a typed workflow state.
- `/workflow step <text>` and `/workflow done <number>` update workflow progress.
- `/delegate <task>` records isolated sub-agent results in the workflow.
- `/workflow show` and `/workflow clear` inspect or remove the active workflow.
- With `--session`, workflow state is persisted next to messages, plans, and events.
