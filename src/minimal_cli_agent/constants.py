from __future__ import annotations

from typing import Final


class Providers:
    OLLAMA: Final = "ollama"
    OPENAI_COMPATIBLE: Final = "openai-compatible"
    ANTHROPIC: Final = "anthropic"
    GEMINI: Final = "gemini"
    CODEX: Final = "codex"
    ALL: Final = (OLLAMA, OPENAI_COMPATIBLE, ANTHROPIC, GEMINI, CODEX)


class Profiles:
    OLLAMA: Final = "ollama"
    CODEX: Final = "codex"
    CLAUDE: Final = "claude"
    GEMINI: Final = "gemini"
    ALL: Final = (OLLAMA, CODEX, CLAUDE, GEMINI)


class PermissionModes:
    DEFAULT: Final = "default"
    AUTO_EDIT: Final = "autoEdit"
    PLAN: Final = "plan"
    YOLO: Final = "yolo"
    ALL: Final = (DEFAULT, AUTO_EDIT, PLAN, YOLO)


class ToolDecisionKinds:
    ALLOW: Final = "allow"
    ASK: Final = "ask"
    DENY: Final = "deny"
    SKIP: Final = "skip"
    ALL: Final = (ALLOW, ASK, DENY, SKIP)


class LoopEventTypes:
    STEP_START: Final = "step_start"
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
    TOOL: Final = "tool"
    PAYLOAD: Final = "payload"
    REASON: Final = "reason"
    OBSERVATION: Final = "observation"


class SessionFields:
    MESSAGES: Final = "messages"
    EVENTS: Final = "events"
    ROLE: Final = "role"
    CONTENT: Final = "content"
    KIND: Final = "kind"
    DATA: Final = "data"
    TIMESTAMP: Final = "timestamp"


class EventKinds:
    PERMISSION_DECISION: Final = "permission_decision"


class PermissionEventFields:
    ACTION: Final = "action"
    DECISION: Final = "decision"
    REASON: Final = "reason"
    PAYLOAD: Final = "payload"
    PERMISSION_MODE: Final = "permission_mode"


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
        "ssh ",
        "scp ",
        "sftp ",
        "rsync ",
        "nc ",
        "ncat ",
        "telnet ",
    )


class PolicyFileFields:
    DENY_COMMAND_TOKENS: Final = "deny_command_tokens"
    SENSITIVE_PATH_TOKENS: Final = "sensitive_path_tokens"
    NETWORK_COMMAND_TOKENS: Final = "network_command_tokens"


class Defaults:
    MODEL: Final = "qwen3:4b"
    BASE_URL: Final = "http://localhost:11434"
    MAX_STEPS: Final = "20"
    COMMAND_TIMEOUT: Final = "30"
    MODEL_TIMEOUT: Final = "300"
    CONTEXT_TAIL_MESSAGES: Final = "8"


class FileToolDefaults:
    TAIL_LINES: Final = 100
    TAIL_MAX_BYTES: Final = 65536
    TAIL_MAX_LINES: Final = 2000
    FORWARD_LIMIT: Final = 8192
    SEARCH_TOP_K: Final = 20
    SEARCH_MAX_FILES: Final = 200
    SEARCH_MAX_TOP_K: Final = 200
    SEARCH_MAX_FILES_LIMIT: Final = 5000
    IGNORED_DIRS: Final = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache")
    JSON_SUFFIXES: Final = (".json",)
    TOML_SUFFIXES: Final = (".toml",)
    XML_SUFFIXES: Final = (".xml",)
    YAML_SUFFIXES: Final = (".yaml", ".yml")


class Tools:
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
    SEARCH: Final = "search"
    SEARCH_ALIASES: Final = ("grep", "rg")
    SEARCH_EXPECTED_FORMAT: Final = '{"pattern":"needle","path":".","top_k":20,"max_files":200}'
    WRITE_FILE: Final = "write_file"
    WRITE_FILE_ALIASES: Final = ("write", "writeFile")
    WRITE_FILE_EXPECTED_FORMAT: Final = '{"path":"relative/path.txt","content":"new file content"}'
    READ_ONLY: Final = (READ_FILE, READ_TAIL, READ_FORWARD, SEARCH)


class ToolPayloadFields:
    TOOL: Final = "tool"
    PATH: Final = "path"
    CONTENT: Final = "content"
    LINES: Final = "lines"
    MAX_BYTES: Final = "max_bytes"
    OFFSET: Final = "offset"
    LIMIT: Final = "limit"
    PATTERN: Final = "pattern"
    TOP_K: Final = "top_k"
    MAX_FILES: Final = "max_files"


class InteractiveCommands:
    HELP: Final = "/help"
    CONFIG: Final = "/config"
    PROFILE: Final = "/profile"
    PROVIDER: Final = "/provider"
    MODEL: Final = "/model"
    BASE_URL: Final = "/base-url"
    PERMISSION: Final = "/permission"
    NETWORK: Final = "/network"
    SUMMARIZE: Final = "/summarize"
    CONTEXT: Final = "/context"
    REVIEW: Final = "/review"
    EXIT: Final = "/exit"
    QUIT: Final = "/quit"
    PLAIN_EXIT: Final = "exit"
    PLAIN_QUIT: Final = "quit"
    QUICK_HINT: Final = "/"
    DESCRIPTIONS: Final = {
        HELP: "Show interactive commands.",
        CONFIG: "Show or change runtime config. Usage: /config",
        PROFILE: "Switch model profile. Usage: /profile codex|ollama|claude|gemini",
        PROVIDER: "Switch provider. Usage: /provider ollama|codex|openai-compatible|anthropic|gemini",
        MODEL: "Switch model. Usage: /model <model-name>",
        BASE_URL: "Switch provider base URL. Usage: /base-url <url>",
        PERMISSION: "Switch permission mode. Usage: /permission default|autoEdit|plan|yolo",
        NETWORK: "Toggle network shell commands. Usage: /network on|off",
        SUMMARIZE: "Toggle model context summaries. Usage: /summarize on|off",
        CONTEXT: "Manage context. Usage: /context status|compact|clear",
        REVIEW: "Ask the agent to review the current project or a path. Usage: /review [path]",
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
        NETWORK,
        SUMMARIZE,
        CONTEXT,
        REVIEW,
    )
