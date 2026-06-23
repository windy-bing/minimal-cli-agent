# Architecture Notes

The first implementation follows the minimal-agent.com loop:

1. Keep a `messages` list.
2. Ask the model for the next response.
3. Parse exactly one `bash-action`.
4. Execute the action in an environment.
5. Append the observation back to messages.
6. Repeat until `exit` or `max_steps`.

## Current modules

- `Agent`: owns only the stateless reasoning/action/observation loop.
- `AgentHarness`: owns runtime wiring for model, context manager, session store, policy, tool registry, and environment.
- `ChatModel`: calls Ollama, OpenAI-compatible, Anthropic, Gemini chat endpoints, or the Codex CLI adapter.
- `LocalEnvironment`: executes shell commands with timeout and non-interactive env vars.
- `ShellPermissionPolicy`: approves or rejects shell commands.
- `ToolRegistry`: registers executable tools behind one invocation boundary.
- `ToolExecutionPipeline`: runs Discovery, Validation, Permission, PreHook, ResolveDecision, Confirmation, Execution, PostHook, AutoVerify, and Formatting.
- `parser`: extracts a single `bash-action` code block.
- `memory`: persists sessions and applies a simple local compaction policy.
- `interfaces`: defines protocol boundaries for model, tool execution, sessions, context, and policy.

## Boundary Map

The project is meant to grow into a harness-style agent. The important rule is that `Agent` should not directly own runtime concerns.

| Concern | Current owner | Extension point |
| --- | --- | --- |
| Loop control | `Agent.chat_stream(message, context)` | Multi-action loop, planner loop, verifier loop |
| Model calls | `ChatModel` through `AgentHarness` | LiteLLM, OpenAI Responses, more local models |
| Model profiles | `profiles.py` | cc-switch-compatible config discovery |
| Context window | `CompactingContextManager` | Model-based summarizer, retrieval-backed context |
| Session persistence | `JsonSessionStore` | SQLite, event log, group session store |
| Tool invocation | `ToolRegistry` | MCP tools, browser tools, file tools, plugin tools |
| Tool lifecycle | `ToolExecutionPipeline` | Hook arbitration, confirmation UI, retries, formatting |
| Shell execution | `LocalEnvironment` | Docker environment, remote sandbox, workspace fork |
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
- Multi-turn CLI REPL that reuses one `ChatContext` across turns.
- `ToolRegistry` for tool discovery.
- Tool aliases plus recoverable discovery and validation observations.
- `ToolExecutionPipeline` with the full stage shape:

```text
Discovery -> Validation -> Permission -> PreHook -> ResolveDecision
  -> Confirmation -> Execution -> PostHook -> AutoVerify -> Formatting
```

- `ToolDecision` with `allow`, `ask`, `deny`, and `skip`.
- Product permission modes: `default`, `autoEdit`, `plan`, `yolo`.

Reserved:

- `ResolveDecision` is currently a pass-through stage. It exists so hooks, session approvals, and policy decisions can be arbitrated later.
- `Confirmation` is currently CLI `input()`. A UI client can replace the policy/harness boundary later.
- `autoEdit` is present, but with only a shell tool it currently asks for confirmation like `default`.
- Tool schema validation is intentionally minimal. It currently supports per-tool expected format and validator callbacks, not full JSON Schema.

Not implemented yet:

- Parallel tool calls.
- File-level write locks.
- MCP/plugin/skill discovery.
- SubAgent runner.
- GroupSession event store.
- Workflow scheduler.

## Roadmap

See [Harness Gap Analysis](harness-gap-analysis.zh-CN.md) for a more detailed comparison between the current implementation and production-grade agent harness requirements.

### Context Compression

The current `compact_messages` function trims older messages and inserts a local note. The next version should add model-based summarization:

- Keep system prompt, user goal, recent tool observations, and explicit decisions.
- Summarize older command/output history into a structured state block.
- Store both raw transcript and compacted working context.

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

- `default`: shell commands ask for confirmation.
- `autoEdit`: reserved for automatic file edits; shell commands still ask.
- `plan`: skip shell execution and return a skipped observation.
- `yolo`: allow execution unless a hard deny rule blocks it.

A production version should add:

- Command allow/deny rules.
- Write scope restrictions.
- Destructive command detection.
- Network access policy.
- Secret redaction in observations.
- Approval records in the session log.

### Skills, MCP, and Plugins

Skills should be prompt-and-tool bundles discovered from a local directory.

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
