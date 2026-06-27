import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from minimal_cli_agent.mcp_tools import (
    build_mcp_tool_name,
    expand_env_vars,
    format_mcp_result,
    load_mcp_config,
    parse_mcp_arguments,
    parse_mcp_config,
    parse_sse_json,
    register_mcp_tools,
)
from minimal_cli_agent.constants import EventKinds
from minimal_cli_agent.exceptions import ConfigurationError
from minimal_cli_agent.tool_registry import ToolRegistry


class MCPToolsTest(unittest.TestCase):
    def test_parse_mcp_servers_config(self) -> None:
        config = parse_mcp_config(
            {
                "mcpServers": {
                    "my-coffee": {
                        "type": "streamableHttp",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer ${TOKEN}"},
                    }
                }
            }
        )

        self.assertEqual(len(config), 1)
        self.assertEqual(config[0].name, "my-coffee")
        self.assertEqual(config[0].url, "https://example.test/mcp")

    def test_parse_servers_list_requires_name(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "non-empty name"):
            parse_mcp_config({"servers": [{"url": "https://example.test/mcp"}]})

    def test_parse_server_entry_rejects_invalid_timeout(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "timeout"):
            parse_mcp_config({"mcpServers": {"coffee": {"url": "https://example.test/mcp", "timeout": 0}}})

    def test_parse_server_entry_requires_boolean_discovery(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "discoverTools"):
            parse_mcp_config({"mcpServers": {"coffee": {"url": "https://example.test/mcp", "discoverTools": "yes"}}})

    def test_load_mcp_config_from_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text(
                json.dumps({"mcpServers": {"coffee": {"url": "https://example.test/mcp"}}}),
                encoding="utf-8",
            )

            config = load_mcp_config(path)

        self.assertEqual(config[0].name, "coffee")

    def test_expand_env_vars(self) -> None:
        with patch.dict(os.environ, {"TOKEN": "secret"}, clear=False):
            self.assertEqual(expand_env_vars("Bearer ${TOKEN}"), "Bearer secret")

    def test_parse_sse_json(self) -> None:
        payload = 'event: message\ndata: {"jsonrpc":"2.0","result":{"ok":true},"id":1}\n\n'

        parsed = parse_sse_json(payload)

        self.assertEqual(parsed["result"]["ok"], True)

    def test_parse_mcp_arguments_accepts_arguments_wrapper(self) -> None:
        arguments = parse_mcp_arguments('{"arguments":{"query":"拿铁"}}')

        self.assertEqual(arguments, {"query": "拿铁"})

    def test_parse_mcp_arguments_accepts_raw_object(self) -> None:
        arguments = parse_mcp_arguments('{"query":"拿铁"}')

        self.assertEqual(arguments, {"query": "拿铁"})

    def test_build_mcp_tool_name_sanitizes_parts(self) -> None:
        self.assertEqual(build_mcp_tool_name("my-coffee", "queryShopList"), "mcp_my_coffee_queryshoplist")

    def test_format_mcp_result_prefers_text_and_structured_content(self) -> None:
        output = format_mcp_result(
            {
                "content": [{"type": "text", "text": "hello"}],
                "structuredContent": {"ok": True},
            }
        )

        self.assertIn("hello", output)
        self.assertIn('"ok": true', output)

    def test_register_mcp_tools_records_registration_events(self) -> None:
        events = []
        with patch.dict(os.environ, {"TOKEN": "super-secret-token"}, clear=False):
            configs = parse_mcp_config(
                {
                    "mcpServers": {
                        "coffee": {
                            "url": "https://example.test/mcp?token=${TOKEN}",
                            "headers": {"Authorization": "Bearer ${MISSING_TOKEN}"},
                            "discoverTools": True,
                        }
                    }
                }
            )

        register_mcp_tools(ToolRegistry(), configs, audit_recorder=lambda kind, data: events.append((kind, data)))

        self.assertEqual(events[0][0], EventKinds.MCP_REGISTRATION)
        self.assertEqual(events[0][1]["status"], "generic_registered")
        self.assertNotIn("super-secret-token", events[0][1]["url"])
        self.assertIn("token=<redacted>", events[0][1]["url"])
        self.assertEqual(events[1][1]["status"], "discovery_skipped")
        self.assertEqual(events[1][1]["reason"], "unresolved_placeholders")


if __name__ == "__main__":
    unittest.main()
