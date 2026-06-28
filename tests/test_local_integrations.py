from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import unittest

from minimal_cli_agent.mcp_tools import MCPServerConfig, register_mcp_tools
from minimal_cli_agent.model import ChatModel
from minimal_cli_agent.tool_registry import ToolRegistry
from minimal_cli_agent.types import AgentConfig, Message


class JsonHandler(BaseHTTPRequestHandler):
    routes: dict[str, object] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/v1/chat/completions":
            self.server.received_auth = self.headers.get("Authorization")  # type: ignore[attr-defined]
            response = {"choices": [{"message": {"content": f"echo:{request['messages'][-1]['content']}"}}]}
        else:
            method = request.get("method")
            if method == "initialize":
                response = {"jsonrpc": "2.0", "id": request["id"], "result": {"capabilities": {}}}
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"tools": [{"name": "lookup", "description": "Lookup a value"}]},
                }
            elif method == "tools/call":
                response = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"content": [{"type": "text", "text": f"lookup:{request['params']['arguments']['query']}"}]},
                }
            else:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"message": "unknown method"}}
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class LocalHttpServer:
    def __enter__(self) -> HTTPServer:
        try:
            self.server = HTTPServer(("127.0.0.1", 0), JsonHandler)
        except PermissionError as exc:
            raise unittest.SkipTest(f"local socket binding is unavailable: {exc}") from exc
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


class LocalIntegrationTest(unittest.TestCase):
    def test_openai_compatible_model_uses_local_http_adapter(self) -> None:
        with LocalHttpServer() as server:
            config = AgentConfig(
                provider="openai-compatible",
                model="local-model",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="test-key",
            )

            output = ChatModel(config).complete([Message(role="user", content="hello")])

        self.assertEqual(output, "echo:hello")
        self.assertEqual(server.received_auth, "Bearer test-key")  # type: ignore[attr-defined]

    def test_mcp_discovery_and_concrete_tool_call_use_local_http_server(self) -> None:
        with LocalHttpServer() as server:
            registry = ToolRegistry()
            register_mcp_tools(
                registry,
                [MCPServerConfig(name="demo", url=f"http://127.0.0.1:{server.server_port}/mcp", discover_tools=True)],
            )

            result = registry.execute("mcp_demo_lookup", '{"arguments":{"query":"latte"}}')

        self.assertEqual(result.exit_code, 0)
        self.assertIn("lookup:latte", result.output)


if __name__ == "__main__":
    unittest.main()
