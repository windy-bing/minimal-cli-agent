from __future__ import annotations

from enum import StrEnum
from typing import Final


class Provider(StrEnum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    CODEX = "codex"


class Profile(StrEnum):
    OLLAMA = "ollama"
    CODEX = "codex"
    CLAUDE = "claude"
    GEMINI = "gemini"


class PermissionMode(StrEnum):
    DEFAULT = "default"
    AUTO_EDIT = "autoEdit"
    PLAN = "plan"
    YOLO = "yolo"


class ToolDecisionKind(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    SKIP = "skip"


class Providers:
    OLLAMA: Final = Provider.OLLAMA.value
    OPENAI_COMPATIBLE: Final = Provider.OPENAI_COMPATIBLE.value
    ANTHROPIC: Final = Provider.ANTHROPIC.value
    GEMINI: Final = Provider.GEMINI.value
    CODEX: Final = Provider.CODEX.value
    ALL: Final = (OLLAMA, OPENAI_COMPATIBLE, ANTHROPIC, GEMINI, CODEX)


class Profiles:
    OLLAMA: Final = Profile.OLLAMA.value
    CODEX: Final = Profile.CODEX.value
    CLAUDE: Final = Profile.CLAUDE.value
    GEMINI: Final = Profile.GEMINI.value
    ALL: Final = (OLLAMA, CODEX, CLAUDE, GEMINI)


class PermissionModes:
    DEFAULT: Final = PermissionMode.DEFAULT.value
    AUTO_EDIT: Final = PermissionMode.AUTO_EDIT.value
    PLAN: Final = PermissionMode.PLAN.value
    YOLO: Final = PermissionMode.YOLO.value
    ALL: Final = (DEFAULT, AUTO_EDIT, PLAN, YOLO)


class PolicyPresets:
    DEFAULT: Final = "default"
    STRICT: Final = "strict"
    ALL: Final = (DEFAULT, STRICT)


class ToolDecisionKinds:
    ALLOW: Final = ToolDecisionKind.ALLOW.value
    ASK: Final = ToolDecisionKind.ASK.value
    DENY: Final = ToolDecisionKind.DENY.value
    SKIP: Final = ToolDecisionKind.SKIP.value
    ALL: Final = (ALLOW, ASK, DENY, SKIP)


class LoopEventTypes:
    STEP_START: Final = "step_start"
    MODEL_WAIT: Final = "model_wait"
    MODEL_ROUTE: Final = "model_route"
    MODEL_OUTPUT_CHUNK: Final = "model_output_chunk"
    MODEL_OUTPUT: Final = "model_output"
    TOOL_CALL_START: Final = "tool_call_start"
    TOOL_CALL_RESULT: Final = "tool_call_result"
    DONE: Final = "done"
    TURN_COMPLETE: Final = "turn_complete"
    MAX_STEPS: Final = "max_steps"


class LoopEventData:
    STEP: Final = "step"
    MAX_STEPS: Final = "max_steps"
    CONTENT: Final = "content"
    PROVIDER: Final = "provider"
    MODEL: Final = "model"
    STATUS: Final = "status"
    FALLBACK_INDEX: Final = "fallback_index"
    ATTEMPT: Final = "attempt"
    TOOL: Final = "tool"
    PAYLOAD: Final = "payload"
    REASON: Final = "reason"
    OBSERVATION: Final = "observation"


class SessionFields:
    MESSAGES: Final = "messages"
    EVENTS: Final = "events"
    PLAN: Final = "plan"
    WORKFLOW: Final = "workflow"
    ROLE: Final = "role"
    CONTENT: Final = "content"
    KIND: Final = "kind"
    DATA: Final = "data"
    TIMESTAMP: Final = "timestamp"
    INDEX: Final = "index"


class EventKinds:
    PERMISSION_DECISION: Final = "permission_decision"
    TOOL_DECISION: Final = "tool_decision"
    TOOL_DECISION_CONFLICT: Final = "tool_decision_conflict"
    TOOL_EXECUTION: Final = "tool_execution"
    TOOL_BATCH: Final = "tool_batch"
    GROUP_SESSION: Final = "group_session"
    MCP_REGISTRATION: Final = "mcp_registration"


class PermissionEventFields:
    ACTION: Final = "action"
    DECISION: Final = "decision"
    REASON: Final = "reason"
    PAYLOAD: Final = "payload"
    PERMISSION_MODE: Final = "permission_mode"


class ToolDecisionEventFields:
    ACTION: Final = "action"
    INITIAL_DECISION: Final = "initial_decision"
    FINAL_DECISION: Final = "final_decision"
    REASON: Final = "reason"
    HOOKS: Final = "hooks"
    PAYLOAD: Final = "payload"


class ToolExecutionEventFields:
    ACTION: Final = "action"
    EXIT_CODE: Final = "exit_code"
    STATUS: Final = "status"
    ATTEMPTS: Final = "attempts"
    RISK: Final = "risk"
    OUTPUT_SCHEMA: Final = "output_schema"
    METADATA: Final = "metadata"
    PAYLOAD: Final = "payload"


class PolicyDefaults:
    DANGEROUS_TOKENS: Final = (
        "rm -rf /",
        "sudo rm",
        "mkfs",
        ":(){",
        "dd if=",
    )
    SENSITIVE_PATH_TOKENS: Final = (
        ".env",
        ".env.",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".codex/auth.json",
        ".claude/settings.json",
    )
    NETWORK_COMMAND_TOKENS: Final = (
        "curl ",
        "wget ",
        "http ",
        "https ",
        "nc ",
        "ncat ",
        "telnet ",
    )


class PolicyFileFields:
    DENY_COMMAND_TOKENS: Final = "deny_command_tokens"
    ALLOW_COMMAND_PREFIXES: Final = "allow_command_prefixes"
    ALLOW_COMMAND_PREFIX_FILES: Final = "allow_command_prefix_files"
    WRITE_ALLOW_PATHS: Final = "write_allow_paths"
    WRITE_DENY_PATHS: Final = "write_deny_paths"
    SENSITIVE_PATH_TOKENS: Final = "sensitive_path_tokens"
    NETWORK_COMMAND_TOKENS: Final = "network_command_tokens"


class Defaults:
    MODEL: Final = "qwen3:4b"
    BASE_URL: Final = "http://localhost:11434"
    MAX_STEPS: Final = "0"
    COMMAND_TIMEOUT: Final = "30"
    MODEL_TIMEOUT: Final = "300"
    MCP_TIMEOUT: Final = "30"
    CONTEXT_TAIL_MESSAGES: Final = "8"
    CONTEXT_COMPRESSION_RATIO: Final = "0.85"
    SESSION_MAX_MESSAGES: Final = "200"
    SESSION_PATH: Final = ".agent/session.json"
    LOCAL_CONFIG_FILE: Final = ".minimal-agent.json"
    USER_CONFIG_DIR: Final = ".minimal-agent"
    USER_CONFIG_FILE: Final = "config.json"
    PROJECT_RULES_MAX_CHARS: Final = "8000"
    SANDBOX_KIND: Final = "host"
    SANDBOX_IMAGE: Final = "python:3.11-slim"
    SANDBOX_NETWORK: Final = "none"


class SandboxKinds:
    HOST: Final = "host"
    DOCKER: Final = "docker"
    ALL: Final = (HOST, DOCKER)


class FileToolDefaults:
    TAIL_LINES: Final = 100
    TAIL_MAX_BYTES: Final = 65536
    TAIL_MAX_LINES: Final = 2000
    FORWARD_LIMIT: Final = 8192
    SEARCH_TOP_K: Final = 20
    SEARCH_MAX_FILES: Final = 200
    SEARCH_MAX_TOP_K: Final = 200
    SEARCH_MAX_FILES_LIMIT: Final = 5000
    SEARCH_PRIORITY_EXTENSIONS: Final = (".py", ".md", ".toml", ".json", ".yaml", ".yml")
    SEARCH_TIMEOUT_MS: Final = 2000
    SEARCH_MAX_TIMEOUT_MS: Final = 30000
    IGNORED_DIRS: Final = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache")
    IGNORE_FILES: Final = (".gitignore", ".agentignore")
    JSON_SUFFIXES: Final = (".json",)
    TOML_SUFFIXES: Final = (".toml",)
    XML_SUFFIXES: Final = (".xml",)
    YAML_SUFFIXES: Final = (".yaml", ".yml")


class Tools:
    MCP_PREFIX: Final = "mcp_"
    SHELL: Final = "shell"
    SHELL_ALIASES: Final = ("bash", "sh", "command")
    SHELL_EXPECTED_FORMAT: Final = "A non-empty shell command string, for example: ls -la"
    READ_FILE: Final = "read_file"
    READ_FILE_ALIASES: Final = ("read", "readFile")
    READ_FILE_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt"}'
    READ_TAIL: Final = "read_tail"
    READ_TAIL_ALIASES: Final = ("tail", "readTail")
    READ_TAIL_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt","lines":100,"max_bytes":65536}'
    READ_FORWARD: Final = "read_forward"
    READ_FORWARD_ALIASES: Final = ("readForward",)
    READ_FORWARD_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt","offset":0,"limit":8192}'
    FILE_INFO: Final = "file_info"
    FILE_INFO_ALIASES: Final = ("stat_file", "fileInfo")
    FILE_INFO_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt"}'
    SEARCH: Final = "search"
    SEARCH_ALIASES: Final = ("grep", "rg")
    SEARCH_EXPECTED_FORMAT: Final = '{"pattern":"needle","path":".","top_k":20,"max_files":200,"timeout_ms":2000}'
    GET_CONTEXT_REMAINING: Final = "get_context_remaining"
    GET_CONTEXT_REMAINING_ALIASES: Final = ("context_remaining",)
    GET_CONTEXT_REMAINING_EXPECTED_FORMAT: Final = "{}"
    WRITE_FILE: Final = "write_file"
    WRITE_FILE_ALIASES: Final = ("write", "writeFile")
    WRITE_FILE_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt","content":"new file content"}'
    EDIT_FILE: Final = "edit_file"
    EDIT_FILE_ALIASES: Final = ("edit", "patch_file", "patchFile")
    EDIT_FILE_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt","start_line":10,"end_line":12,"content":"replacement"}'
    READ_ONLY: Final = (READ_FILE, READ_TAIL, READ_FORWARD, FILE_INFO, SEARCH, GET_CONTEXT_REMAINING)
    WRITERS: Final = (WRITE_FILE, EDIT_FILE)


class ToolPayloadFields:
    TOOL: Final = "tool"
    NAME: Final = "name"
    ARGUMENTS: Final = "arguments"
    PATH: Final = "path"
    CONTENT: Final = "content"
    START_LINE: Final = "start_line"
    END_LINE: Final = "end_line"
    LINES: Final = "lines"
    MAX_BYTES: Final = "max_bytes"
    MODE: Final = "mode"
    OFFSET: Final = "offset"
    LIMIT: Final = "limit"
    LINE_OFFSET: Final = "line_offset"
    LINE_LIMIT: Final = "line_limit"
    PATTERN: Final = "pattern"
    TOP_K: Final = "top_k"
    MAX_FILES: Final = "max_files"
    TIMEOUT_MS: Final = "timeout_ms"
    IGNORE_DIRS: Final = "ignore_dirs"
    INCLUDE_EXTENSIONS: Final = "include_extensions"


class InteractiveCommands:
    HELP: Final = "/help"
    CONFIG: Final = "/config"
    PROFILE: Final = "/profile"
    PROVIDER: Final = "/provider"
    MODEL: Final = "/model"
    BASE_URL: Final = "/base-url"
    PERMISSION: Final = "/permission"
    POLICY: Final = "/policy"
    METRICS: Final = "/metrics"
    NETWORK: Final = "/network"
    SUMMARIZE: Final = "/summarize"
    CONTEXT: Final = "/context"
    DOCTOR: Final = "/doctor"
    DEBUG: Final = "/debug"
    HISTORY: Final = "/history"
    EVENTS: Final = "/events"
    SESSION: Final = "/session"
    PLAN: Final = "/plan"
    WORKFLOW: Final = "/workflow"
    MEMORY: Final = "/memory"
    DELEGATE: Final = "/delegate"
    REVIEW: Final = "/review"
    MCP: Final = "/mcp"
    PLUGIN: Final = "/plugin"
    PLUGINS: Final = "/plugins"
    SKILL: Final = "/skill"
    SKILLS: Final = "/skills"
    EXIT: Final = "/exit"
    QUIT: Final = "/quit"
    PLAIN_EXIT: Final = "exit"
    PLAIN_QUIT: Final = "quit"
    QUICK_HINT: Final = "/"
    DESCRIPTIONS: Final = {
        HELP: "Show interactive commands.",
        CONFIG: "Show, explain, or save runtime config. Usage: /config [show|explain|capabilities|save [project|user]]",
        PROFILE: "Switch model profile. Usage: /profile codex|ollama|claude|gemini",
        PROVIDER: "Switch provider. Usage: /provider ollama|codex|openai-compatible|anthropic|gemini",
        MODEL: "Switch model. Usage: /model <model-name>",
        BASE_URL: "Switch provider base URL. Usage: /base-url <url>",
        PERMISSION: "Switch permission mode. Usage: /permission default|autoEdit|plan|yolo",
        POLICY: "Show active permission policy and session approvals. Usage: /policy [json]|explain <tool> <payload>",
        METRICS: "Show session metrics from persisted events. Usage: /metrics [json]",
        NETWORK: "Toggle network shell commands. Usage: /network on|off",
        SUMMARIZE: "Toggle model context summaries. Usage: /summarize on|off",
        CONTEXT: "Manage context. Usage: /context status|compact|clear",
        DOCTOR: "Run local health checks for config, workspace, session, policy, MCP, and plugins. Usage: /doctor [json]",
        DEBUG: "Inspect diagnostics. Usage: /debug prompt [message]|bundle [path]",
        HISTORY: "Show or replay user prompt history. Usage: /history [number]",
        EVENTS: "Show persisted session events. Usage: /events [kind] [limit] [offset] [format=json]",
        SESSION: "Export, import, or inspect session state. Usage: /session stats|export [path]|import <path>",
        PLAN: "Create, show, or clear an isolated plan. Usage: /plan <goal>|show|clear",
        WORKFLOW: "Manage typed workflow state. Usage: /workflow create <goal>|step <text>|schedule|done <number>|merge|verify|wait|show|clear",
        MEMORY: "Search persisted SQLite memory. Usage: /memory <query>",
        DELEGATE: "Run an isolated read-only sub-agent task. Usage: /delegate <task>",
        REVIEW: "Ask the agent to review the current project or a path. Usage: /review [path]",
        MCP: "Load an MCP config file and rebuild tools. Usage: /mcp path/to/mcp.json",
        PLUGIN: "Load a plugin manifest by name or path. Usage: /plugin my-plugin",
        PLUGINS: "Discover workspace plugins. Usage: /plugins [load <name>|load all]",
        SKILL: "Load a skill by name or path. Usage: /skill my-coffee",
        SKILLS: "Discover workspace skills. Usage: /skills [load <name>|load all]",
        EXIT: "Exit interactive mode.",
        QUIT: "Exit interactive mode.",
        PLAIN_EXIT: "Exit interactive mode.",
        PLAIN_QUIT: "Exit interactive mode.",
    }
    EXIT_COMMANDS: Final = (EXIT, QUIT, PLAIN_EXIT, PLAIN_QUIT)
    COMMANDS_WITH_ARGS: Final = (
        CONFIG,
        PROFILE,
        PROVIDER,
        MODEL,
        BASE_URL,
        PERMISSION,
        POLICY,
        METRICS,
        NETWORK,
        SUMMARIZE,
        CONTEXT,
        DOCTOR,
        DEBUG,
        HISTORY,
        EVENTS,
        SESSION,
        PLAN,
        WORKFLOW,
        DELEGATE,
        REVIEW,
        MCP,
        PLUGIN,
        PLUGINS,
        SKILL,
        SKILLS,
    )
