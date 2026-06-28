from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from typing import Any

from minimal_cli_agent.constants import Defaults, EventKinds, ToolPayloadFields, Tools
from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.logging_utils import get_logger
from minimal_cli_agent.redaction import redact_text
from minimal_cli_agent.tool_registry import ToolRegistry, ToolSpec
from minimal_cli_agent.types import CommandResult

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "minimal-cli-agent"
MCP_CLIENT_VERSION = "0.1.0"
logger = get_logger("mcp_tools")


class MCPRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = int(Defaults.MCP_TIMEOUT)
    discover_tools: bool = False

    @property
    def safe_name(self) -> str:
        return sanitize_tool_part(self.name)

    def has_unresolved_placeholders(self) -> bool:
        values = [self.url, *self.headers.values()]
        return any("${" in value for value in values)


class MCPHttpClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_initialized()
        result = self.call("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ensure_initialized()
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        try:
            self.call(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": MCP_CLIENT_NAME, "version": MCP_CLIENT_VERSION},
                },
            )
        except MCPRequestError:
            # Some HTTP MCP servers accept tools/list without an explicit initialize round.
            logger.warning("MCP initialize failed for %s; falling back to direct method calls.", self.config.name, exc_info=True)
        self._initialized = True

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.config.has_unresolved_placeholders():
            raise MCPRequestError("MCP config contains unresolved environment placeholders.")
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        response = self._post(payload)
        if "error" in response:
            raise MCPRequestError(json.dumps(response["error"], ensure_ascii=False))
        result = response.get("result", {})
        return result if isinstance(result, dict) else {"result": result}

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.config.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.config.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MCPRequestError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPRequestError(str(exc)) from exc
        except TimeoutError as exc:
            raise MCPRequestError(f"request timed out after {self.config.timeout}s") from exc
        return parse_mcp_response(body)


def register_mcp_tools(registry: ToolRegistry, configs: list[MCPServerConfig], audit_recorder=None) -> None:
    for config in configs:
        client = MCPHttpClient(config)
        register_generic_mcp_tools(registry, config, client)
        record_mcp_registration(
            audit_recorder,
            config,
            status="generic_registered",
            generic_tools=[
                build_mcp_tool_name(config.safe_name, "list_tools"),
                build_mcp_tool_name(config.safe_name, "call_tool"),
            ],
        )
        if not config.discover_tools or config.has_unresolved_placeholders():
            if config.has_unresolved_placeholders():
                record_mcp_registration(audit_recorder, config, status="discovery_skipped", reason="unresolved_placeholders")
            continue
        try:
            remote_tools = client.list_tools()
        except MCPRequestError as exc:
            record_mcp_registration(audit_recorder, config, status="discovery_failed", reason=str(exc))
            continue
        registered = []
        for remote_tool in remote_tools:
            remote_name = str(remote_tool.get("name") or "").strip()
            if not remote_name:
                continue
            register_concrete_mcp_tool(registry, config, client, remote_tool)
            registered.append(build_mcp_tool_name(config.safe_name, remote_name))
        record_mcp_registration(audit_recorder, config, status="discovery_registered", concrete_tools=registered)


def record_mcp_registration(audit_recorder, config: MCPServerConfig, **data) -> None:
    if audit_recorder is None:
        return
    audit_recorder(
        EventKinds.MCP_REGISTRATION,
        {
            "server": config.name,
            "url": redact_text(config.url),
            "discover_tools": config.discover_tools,
            **data,
        },
    )


def register_generic_mcp_tools(registry: ToolRegistry, config: MCPServerConfig, client: MCPHttpClient) -> None:
    list_name = build_mcp_tool_name(config.safe_name, "list_tools")
    call_name = build_mcp_tool_name(config.safe_name, "call_tool")

    registry.register(
        ToolSpec(
            name=list_name,
            description=f"List available tools from MCP server {config.name}.",
            handler=lambda payload, c=client, n=list_name: handle_mcp_list_tools(n, c),
            expected_format="{}",
        )
    )
    registry.register(
        ToolSpec(
            name=call_name,
            description=f"Call a tool on MCP server {config.name}.",
            handler=lambda payload, c=client, n=call_name: handle_mcp_call_tool(n, c, payload),
            expected_format='{"name":"remoteToolName","arguments":{}}',
        )
    )


def register_concrete_mcp_tool(
    registry: ToolRegistry,
    config: MCPServerConfig,
    client: MCPHttpClient,
    remote_tool: dict[str, Any],
) -> None:
    remote_name = str(remote_tool["name"])
    local_name = build_mcp_tool_name(config.safe_name, remote_name)
    description = str(remote_tool.get("description") or f"Call MCP tool {remote_name} on {config.name}.")
    registry.register(
        ToolSpec(
            name=local_name,
            description=f"{description} (MCP server: {config.name}, remote tool: {remote_name})",
            handler=lambda payload, c=client, rn=remote_name, ln=local_name: handle_concrete_mcp_tool(ln, c, rn, payload),
            expected_format='{"arguments":{}} or the remote tool argument object',
        )
    )


def handle_mcp_list_tools(tool_name: str, client: MCPHttpClient) -> CommandResult:
    try:
        tools = client.list_tools()
    except MCPRequestError as exc:
        return CommandResult(command=tool_name, exit_code=1, output=f"MCP tools/list failed: {exc}")
    return CommandResult(command=tool_name, exit_code=0, output=json.dumps(tools, ensure_ascii=False, indent=2))


def handle_mcp_call_tool(tool_name: str, client: MCPHttpClient, payload: str) -> CommandResult:
    try:
        raw = parse_json_object(payload)
        remote_name = raw.get(ToolPayloadFields.NAME)
        if not isinstance(remote_name, str) or not remote_name.strip():
            return CommandResult(command=tool_name, exit_code=2, output='MCP call payload requires string field "name".')
        arguments = raw.get(ToolPayloadFields.ARGUMENTS, {})
        if not isinstance(arguments, dict):
            return CommandResult(command=tool_name, exit_code=2, output='MCP call payload field "arguments" must be an object.')
        result = client.call_tool(remote_name, arguments)
    except (ValueError, MCPRequestError) as exc:
        return CommandResult(command=tool_name, exit_code=1, output=f"MCP tools/call failed: {exc}")
    return CommandResult(command=tool_name, exit_code=0, output=format_mcp_result(result))


def handle_concrete_mcp_tool(tool_name: str, client: MCPHttpClient, remote_name: str, payload: str) -> CommandResult:
    try:
        arguments = parse_mcp_arguments(payload)
        result = client.call_tool(remote_name, arguments)
    except (ValueError, MCPRequestError) as exc:
        return CommandResult(command=tool_name, exit_code=1, output=f"MCP tool {remote_name} failed: {exc}")
    return CommandResult(command=tool_name, exit_code=0, output=format_mcp_result(result))


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read MCP config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"MCP config must be valid JSON: {path}") from exc
    return parse_mcp_config(raw)


def parse_mcp_config(raw: Any) -> list[MCPServerConfig]:
    if not isinstance(raw, dict):
        raise ConfigurationError("MCP config must contain a JSON object.")
    if isinstance(raw.get("mcpServers"), dict):
        server_map = raw["mcpServers"]
    elif isinstance(raw.get("servers"), list):
        configs = []
        for index, item in enumerate(raw["servers"]):
            if not isinstance(item, dict):
                raise ConfigurationError(f"MCP servers[{index}] must be an object.")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ConfigurationError(f"MCP servers[{index}] requires a non-empty name.")
            configs.append(parse_server_entry(name, item))
        return configs
    else:
        server_map = raw
    return [parse_server_entry(name, value) for name, value in server_map.items() if isinstance(value, dict)]


def parse_server_entry(name: str, value: dict[str, Any]) -> MCPServerConfig:
    server_type = str(value.get("type") or "streamablehttp")
    if normalize_mcp_server_type(server_type) not in {"streamablehttp", "http"}:
        raise ConfigurationError(f"Unsupported MCP server type for {name}: {server_type}")
    url = value.get("url")
    if not isinstance(url, str) or not url:
        raise ConfigurationError(f"MCP server {name} requires a url.")
    headers = value.get("headers", {})
    if not isinstance(headers, dict):
        raise ConfigurationError(f"MCP server {name} headers must be an object.")
    timeout = value.get("timeout", int(Defaults.MCP_TIMEOUT))
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ConfigurationError(f"MCP server {name} timeout must be a positive integer.")
    discover_tools = value.get("discoverTools", False)
    if not isinstance(discover_tools, bool):
        raise ConfigurationError(f"MCP server {name} discoverTools must be a boolean.")
    return MCPServerConfig(
        name=name,
        url=expand_env_vars(url),
        headers={str(key): expand_env_vars(str(val)) for key, val in headers.items()},
        timeout=timeout,
        discover_tools=discover_tools,
    )


def normalize_mcp_server_type(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def parse_mcp_response(body: str) -> dict[str, Any]:
    stripped = body.strip()
    if not stripped:
        return {}
    if stripped.startswith("data:") or "\ndata:" in stripped:
        return parse_sse_json(stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise MCPRequestError("MCP response JSON must be an object.")
    return parsed


def parse_sse_json(body: str) -> dict[str, Any]:
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            return parsed
    raise MCPRequestError("MCP SSE response did not contain a JSON data event.")


def parse_json_object(payload: str) -> dict[str, Any]:
    try:
        raw = json.loads(payload or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"payload must be valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("payload must be a JSON object")
    return raw


def parse_mcp_arguments(payload: str) -> dict[str, Any]:
    raw = parse_json_object(payload)
    if ToolPayloadFields.ARGUMENTS in raw:
        arguments = raw[ToolPayloadFields.ARGUMENTS]
        if not isinstance(arguments, dict):
            raise ValueError('"arguments" must be a JSON object')
        return arguments
    return raw


def format_mcp_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
    structured = result.get("structuredContent")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
    if parts:
        return "\n\n".join(part for part in parts if part)
    return json.dumps(result, ensure_ascii=False, indent=2)


def build_mcp_tool_name(server_name: str, remote_name: str) -> str:
    return f"{Tools.MCP_PREFIX}{sanitize_tool_part(server_name)}_{sanitize_tool_part(remote_name)}"


def sanitize_tool_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
    return sanitized or "server"


def expand_env_vars(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        env_value = os.getenv(name)
        if env_value is not None:
            return env_value
        return match.group(0)

    return ENV_PATTERN.sub(replace, value)
