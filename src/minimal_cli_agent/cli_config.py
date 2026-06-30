from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from minimal_cli_agent.constants import Defaults, PermissionModes, PolicyPresets, Profiles, Providers, SandboxKinds
from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.logging_utils import get_logger
from minimal_cli_agent.memory import JsonSessionStore, SQLiteSessionStore
from minimal_cli_agent.types import ModelRoute

logger = get_logger("cli_config")

CONFIG_SCHEMA_VERSION = 1
CONFIG_SCHEMA_VERSION_KEY = "config_schema_version"
CONFIG_KEYS = frozenset(
    {
        CONFIG_SCHEMA_VERSION_KEY,
        "allow_network",
        "api_key",
        "base_url",
        "bill_failed_requests",
        "context_compression_ratio",
        "cwd",
        "daily_cost_limit",
        "daily_token_limit",
        "max_input_tokens",
        "max_output_tokens",
        "max_request_cost",
        "max_request_tokens",
        "max_steps",
        "mcp_config",
        "model",
        "model_circuit_cooldown",
        "model_circuit_failure_threshold",
        "model_context_tokens",
        "model_fallback",
        "model_max_concurrency",
        "model_max_retries",
        "model_price_input_per_1m",
        "model_price_output_per_1m",
        "model_queue_timeout",
        "model_timeout",
        "monthly_cost_limit",
        "monthly_token_limit",
        "permission",
        "plugin",
        "plugin_discovery",
        "policy_file",
        "policy_preset",
        "profile",
        "prompt_version",
        "provider",
        "sandbox",
        "sandbox_image",
        "sandbox_network",
        "sandbox_read_only",
        "session",
        "session_db",
        "shell",
        "skill",
        "summarize_context",
        "timeout",
        "usage_ledger",
        "usage_subject",
        "usage_tenant",
    }
)
DEPRECATED_CONFIG_KEYS = {
    "command_timeout": "timeout",
    "model_context_window": "model_context_tokens",
    "session_path": "session",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimal-agent", description="Run a minimal terminal AI agent.")
    parser.add_argument("task", nargs="*", help="Task for the agent.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start a multi-turn interactive CLI session.")
    parser.add_argument("--config-file", type=Path, help="Read defaults from this JSON config file.")
    parser.add_argument("--profile", choices=Profiles.ALL, default=os.getenv("AGENT_PROFILE"))
    parser.add_argument("--provider", choices=Providers.ALL, default=os.getenv("AGENT_PROVIDER", Providers.OLLAMA))
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", Defaults.MODEL))
    parser.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", Defaults.BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("AGENT_API_KEY"))
    parser.add_argument("--cwd", type=Path, default=Path(os.getenv("AGENT_CWD", ".")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("AGENT_MAX_STEPS", Defaults.MAX_STEPS)))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AGENT_COMMAND_TIMEOUT", Defaults.COMMAND_TIMEOUT)))
    parser.add_argument("--shell", default=os.getenv("AGENT_SHELL", "system"), help="Shell adapter: system, bash, zsh, sh, powershell, cmd, git-bash, or a shell command.")
    parser.add_argument("--sandbox", choices=SandboxKinds.ALL, default=os.getenv("AGENT_SANDBOX", Defaults.SANDBOX_KIND), help="Command execution backend: host or docker.")
    parser.add_argument("--sandbox-image", default=os.getenv("AGENT_SANDBOX_IMAGE", Defaults.SANDBOX_IMAGE), help="Docker image used when --sandbox docker is enabled.")
    parser.add_argument("--sandbox-network", default=os.getenv("AGENT_SANDBOX_NETWORK", Defaults.SANDBOX_NETWORK), help="Docker network mode used by the command sandbox.")
    parser.add_argument("--sandbox-read-only", action="store_true", help="Mount the workspace read-only in the Docker command sandbox.")
    parser.add_argument("--model-timeout", type=int, default=int(os.getenv("AGENT_MODEL_TIMEOUT", Defaults.MODEL_TIMEOUT)))
    streaming_group = parser.add_mutually_exclusive_group()
    streaming_group.add_argument("--model-streaming", action="store_true", default=None, help="Stream model tokens when the provider supports it.")
    streaming_group.add_argument("--no-model-streaming", action="store_false", dest="model_streaming", help="Disable provider streaming and wait for complete responses.")
    parser.add_argument("--model-output-segment-chars", type=int, default=int(os.getenv("AGENT_MODEL_OUTPUT_SEGMENT_CHARS", "1200")), help="When not streaming, print complete model output in chunks of this many characters. 0 disables segmentation.")
    parser.add_argument("--model-fallback", action="append", default=parse_model_fallback_env(), help="JSON fallback route. Example: '{\"provider\":\"ollama\",\"model\":\"qwen3:1.7b\",\"base_url\":\"http://localhost:11434\"}'")
    parser.add_argument("--model-max-retries", type=int, default=int(os.getenv("AGENT_MODEL_MAX_RETRIES", "0")))
    parser.add_argument("--model-max-concurrency", type=int, default=int(os.getenv("AGENT_MODEL_MAX_CONCURRENCY", "4")))
    parser.add_argument("--model-queue-timeout", type=float, default=float(os.getenv("AGENT_MODEL_QUEUE_TIMEOUT", "5")))
    parser.add_argument("--model-circuit-failure-threshold", type=int, default=int(os.getenv("AGENT_MODEL_CIRCUIT_FAILURE_THRESHOLD", "3")))
    parser.add_argument("--model-circuit-cooldown", type=float, default=float(os.getenv("AGENT_MODEL_CIRCUIT_COOLDOWN", "60")))
    parser.add_argument("--model-price-input-per-1m", type=float, default=float(os.getenv("AGENT_MODEL_PRICE_INPUT_PER_1M", "0")))
    parser.add_argument("--model-price-output-per-1m", type=float, default=float(os.getenv("AGENT_MODEL_PRICE_OUTPUT_PER_1M", "0")))
    _usage_ledger_env = os.getenv("AGENT_USAGE_LEDGER")
    parser.add_argument("--usage-ledger", type=Path, default=Path(_usage_ledger_env) if _usage_ledger_env else None)
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
    parser.add_argument("--policy-preset", choices=PolicyPresets.ALL, default=os.getenv("AGENT_POLICY_PRESET", PolicyPresets.DEFAULT), help="Built-in safety preset: default or strict.")
    parser.add_argument("--mcp-config", type=Path, help="JSON config file with MCP servers.")
    parser.add_argument("--plugin", action="append", default=[], help="Load a plugin manifest by path or by name under plugins/<name>.")
    plugin_discovery_group = parser.add_mutually_exclusive_group()
    plugin_discovery_group.add_argument("--plugin-discovery", action="store_true", default=None, help="Enable automatic plugin discovery.")
    plugin_discovery_group.add_argument("--no-plugin-discovery", action="store_false", dest="plugin_discovery", help="Disable automatic plugin discovery.")
    parser.add_argument("--skill", action="append", default=[], help="Load a skill by path or by name under skills/<name>.")
    summarize_group = parser.add_mutually_exclusive_group()
    summarize_group.add_argument("--summarize-context", action="store_true", default=None, help="Use the model to summarize old context when compacting.")
    summarize_group.add_argument("--no-summarize-context", action="store_false", dest="summarize_context", help="Disable model context summaries.")
    parser.add_argument("--model-context-tokens", type=int, default=parse_optional_int_env("AGENT_MODEL_CONTEXT_TOKENS"), help="Approximate model context window. Context is compacted near this budget.")
    parser.add_argument("--context-compression-ratio", type=float, default=float(os.getenv("AGENT_CONTEXT_COMPRESSION_RATIO", Defaults.CONTEXT_COMPRESSION_RATIO)), help="Fraction of model context tokens that triggers compaction.")
    parser.add_argument("--show-config", action="store_true", help="Print resolved provider/model/base URL without secrets.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs for diagnostics.")
    parser.add_argument("--quiet", action="store_true", help="Only show error logs; CLI output is unchanged.")
    parser.add_argument("--permission", choices=PermissionModes.ALL, default=os.getenv("AGENT_PERMISSION", PermissionModes.DEFAULT))
    parser.add_argument("--session", type=Path, help="Persist messages to this JSON session file.")
    parser.add_argument("--session-db", type=Path, help="Persist full transcript and events to this SQLite database.")
    parser.add_argument("--no-session", action="store_true", help="Disable the default persistent session.")
    return parser


def load_cli_defaults(cwd: Path, explicit_config_file: Path | None = None) -> dict[str, Any]:
    paths = [default_user_config_path(), cwd / Defaults.LOCAL_CONFIG_FILE]
    if explicit_config_file is not None:
        paths = [explicit_config_file]
    merged: dict[str, Any] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"Unable to read config file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Config file must be valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"Config file must contain a JSON object: {path}")
        data = migrate_cli_defaults(data, path)
        for warning in validate_cli_defaults(data, path):
            logger.warning(warning)
        overwritten = sorted(set(merged).intersection(data))
        if overwritten:
            logger.info("Config %s overrides keys from earlier config files: %s", path, ", ".join(overwritten))
        merged.update(data)
    return merged


def migrate_cli_defaults(data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    migrated = dict(data)
    for old_key, new_key in DEPRECATED_CONFIG_KEYS.items():
        if old_key not in migrated or new_key in migrated:
            continue
        migrated[new_key] = migrated[old_key]
        location = f" in {path}" if path is not None else ""
        logger.warning("Config key %s%s is deprecated; use %s.", old_key, location, new_key)
    return migrated


def validate_cli_defaults(data: dict[str, Any], path: Path | None = None) -> list[str]:
    location = f" in {path}" if path is not None else ""
    warnings: list[str] = []
    unknown = sorted(set(data) - CONFIG_KEYS - set(DEPRECATED_CONFIG_KEYS))
    if unknown:
        warnings.append(f"Unknown config key(s){location}: {', '.join(unknown)}")
    version = data.get(CONFIG_SCHEMA_VERSION_KEY, CONFIG_SCHEMA_VERSION)
    if not isinstance(version, int):
        warnings.append(f"Config schema version{location} must be an integer.")
    elif version > CONFIG_SCHEMA_VERSION:
        warnings.append(f"Config schema version{location} is newer than this CLI supports: {version}>{CONFIG_SCHEMA_VERSION}")
    return warnings


def default_user_config_path() -> Path:
    return Path.home() / Defaults.USER_CONFIG_DIR / Defaults.USER_CONFIG_FILE


def default_project_session_path(cwd: Path) -> Path:
    return cwd / Defaults.SESSION_PATH


def resolve_default_session_path(
    args: argparse.Namespace,
    explicit_options: set[str],
    local_defaults: dict[str, Any],
    cwd: Path,
) -> Path | None:
    if args.no_session:
        return None
    raw_session = choose_config_value("session", args.session, local_defaults, explicit_options, ("AGENT_SESSION",))
    if raw_session is None:
        return default_project_session_path(cwd).resolve()
    return resolve_path_option(raw_session, cwd).resolve()


def build_session_store(session_path: Path | None, session_db_path: Path | None):
    if session_db_path is not None:
        return SQLiteSessionStore(session_db_path)
    if session_path is not None:
        return JsonSessionStore(session_path)
    return None


def choose_config_value(
    key: str,
    current: Any,
    local_defaults: dict[str, Any],
    explicit_options: set[str],
    env_names: tuple[str, ...] = (),
) -> Any:
    if key in explicit_options:
        return current
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value is not None:
            return env_value
    if key in local_defaults:
        return local_defaults[key]
    return current


def resolve_path_option(value: Any, base: Path) -> Path:
    path = value if isinstance(value, Path) else Path(str(value))
    path = path.expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def resolve_optional_path(value: Any, base: Path) -> Path | None:
    if value is None or value == "":
        return None
    return resolve_path_option(value, base).resolve()


def optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def bool_config_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def normalize_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return [str(value)]


def normalize_model_fallbacks(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [json.dumps(item) if isinstance(item, dict) else str(item) for item in value]
    if isinstance(value, dict):
        return [json.dumps(value)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [json.dumps(item) if isinstance(item, dict) else str(item) for item in parsed]
        if isinstance(parsed, dict):
            return [json.dumps(parsed)]
        return [value]
    return [str(value)]


def merge_paths(*groups: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    merged: list[Path] = []
    for group in groups:
        for path in group:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            merged.append(resolved)
    return tuple(merged)


def detect_explicit_options(argv: list[str]) -> set[str]:
    option_map = {
        "--provider": "provider",
        "--profile": "profile",
        "--model": "model",
        "--base-url": "base_url",
        "--api-key": "api_key",
        "--cwd": "cwd",
        "--max-steps": "max_steps",
        "--timeout": "timeout",
        "--shell": "shell",
        "--sandbox": "sandbox",
        "--sandbox-image": "sandbox_image",
        "--sandbox-network": "sandbox_network",
        "--model-timeout": "model_timeout",
        "--model-output-segment-chars": "model_output_segment_chars",
        "--model-fallback": "model_fallback",
        "--model-max-retries": "model_max_retries",
        "--model-max-concurrency": "model_max_concurrency",
        "--model-queue-timeout": "model_queue_timeout",
        "--model-circuit-failure-threshold": "model_circuit_failure_threshold",
        "--model-circuit-cooldown": "model_circuit_cooldown",
        "--model-price-input-per-1m": "model_price_input_per_1m",
        "--model-price-output-per-1m": "model_price_output_per_1m",
        "--usage-ledger": "usage_ledger",
        "--usage-subject": "usage_subject",
        "--usage-tenant": "usage_tenant",
        "--max-input-tokens": "max_input_tokens",
        "--max-output-tokens": "max_output_tokens",
        "--max-request-tokens": "max_request_tokens",
        "--daily-token-limit": "daily_token_limit",
        "--monthly-token-limit": "monthly_token_limit",
        "--max-request-cost": "max_request_cost",
        "--daily-cost-limit": "daily_cost_limit",
        "--monthly-cost-limit": "monthly_cost_limit",
        "--prompt-version": "prompt_version",
        "--permission": "permission",
        "--policy-file": "policy_file",
        "--policy-preset": "policy_preset",
        "--mcp-config": "mcp_config",
        "--plugin": "plugin",
        "--skill": "skill",
        "--session": "session",
        "--session-db": "session_db",
        "--config-file": "config_file",
    }
    flag_map = {
        "--bill-failed-requests": "bill_failed_requests",
        "--allow-network": "allow_network",
        "--sandbox-read-only": "sandbox_read_only",
        "--verbose": "verbose",
        "--quiet": "quiet",
        "--summarize-context": "summarize_context",
        "--no-summarize-context": "summarize_context",
        "--model-streaming": "model_streaming",
        "--no-model-streaming": "model_streaming",
        "--plugin-discovery": "plugin_discovery",
        "--no-plugin-discovery": "plugin_discovery",
        "--no-session": "no_session",
        "--show-config": "show_config",
        "--interactive": "interactive",
    }
    explicit = set()
    for item in argv:
        for option, name in option_map.items():
            if item == option or item.startswith(f"{option}="):
                explicit.add(name)
        if item in flag_map:
            explicit.add(flag_map[item])
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
