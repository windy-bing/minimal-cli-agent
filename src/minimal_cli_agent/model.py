from __future__ import annotations

from collections.abc import Iterator
import glob
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from minimal_cli_agent.constants import Providers
from minimal_cli_agent.exceptions import ConfigurationError, ModelRequestError
from minimal_cli_agent.types import AgentConfig, Message

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


class ChatModel:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def complete(self, messages: list[Message]) -> str:
        if self.config.provider == Providers.OLLAMA:
            return self._complete_ollama(messages)
        if self.config.provider == Providers.ANTHROPIC:
            return self._complete_anthropic(messages)
        if self.config.provider == Providers.GEMINI:
            return self._complete_gemini(messages)
        if self.config.provider == Providers.CODEX:
            return self._complete_codex(messages)
        return self._complete_openai_compatible(messages)

    def supports_streaming(self) -> bool:
        return self.config.provider in {Providers.OLLAMA, Providers.OPENAI_COMPATIBLE, Providers.ANTHROPIC, Providers.GEMINI}

    def stream_complete(self, messages: list[Message]) -> Iterator[str]:
        if self.config.provider == Providers.OLLAMA:
            yield from self._stream_ollama(messages)
            return
        if self.config.provider == Providers.ANTHROPIC:
            yield from self._stream_anthropic(messages)
            return
        if self.config.provider == Providers.GEMINI:
            yield from self._stream_gemini(messages)
            return
        if self.config.provider == Providers.OPENAI_COMPATIBLE:
            yield from self._stream_openai_compatible(messages)
            return
        yield self.complete(messages)

    def _complete_ollama(self, messages: list[Message]) -> str:
        url = self.config.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
            "stream": False,
        }
        if self.config.max_output_tokens:
            payload["options"] = {"num_predict": self.config.max_output_tokens}
        data = post_json(url, payload, timeout=self.config.model_timeout)
        return str(data.get("message", {}).get("content", ""))

    def _stream_ollama(self, messages: list[Message]) -> Iterator[str]:
        url = self.config.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
            "stream": True,
        }
        if self.config.max_output_tokens:
            payload["options"] = {"num_predict": self.config.max_output_tokens}
        for data in stream_json(url, payload, timeout=self.config.model_timeout):
            message = data.get("message")
            if isinstance(message, dict):
                content = message.get("content", "")
                if content:
                    yield str(content)
            if data.get("done") is True:
                return

    def _complete_openai_compatible(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("openai-compatible provider requires --api-key or OPENAI_API_KEY.")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
        }
        if self.config.max_output_tokens:
            payload["max_tokens"] = self.config.max_output_tokens
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = post_json(url, payload, headers=headers, timeout=self.config.model_timeout)
        choices = data.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", ""))

    def _stream_openai_compatible(self, messages: list[Message]) -> Iterator[str]:
        if not self.config.api_key:
            raise ConfigurationError("openai-compatible provider requires --api-key or OPENAI_API_KEY.")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
            "stream": True,
        }
        if self.config.max_output_tokens:
            payload["max_tokens"] = self.config.max_output_tokens
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        for data in stream_sse_json(url, payload, headers=headers, timeout=self.config.model_timeout):
            choices = data.get("choices", [])
            if not isinstance(choices, list):
                continue
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    yield delta["content"]
                elif isinstance(choice.get("text"), str):
                    yield choice["text"]

    def _complete_anthropic(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("anthropic provider requires --api-key, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN.")

        url = self.config.base_url.rstrip("/") + "/v1/messages"
        system, chat_messages = split_system_messages(messages)
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens or 4096,
            "messages": [{"role": message.role, "content": message.content} for message in chat_messages],
        }
        if system:
            payload["system"] = system

        headers = {"anthropic-version": os.getenv("ANTHROPIC_VERSION", DEFAULT_ANTHROPIC_VERSION)}
        if self.config.api_key:
            headers.update(anthropic_auth_headers(self.config.api_key))

        data = post_json(url, payload, headers=headers, timeout=self.config.model_timeout)
        output = extract_anthropic_text(data)
        if not output.strip():
            raise ModelRequestError(describe_empty_anthropic_response(data))
        return output

    def _stream_anthropic(self, messages: list[Message]) -> Iterator[str]:
        if not self.config.api_key:
            raise ConfigurationError("anthropic provider requires --api-key, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN.")

        url = self.config.base_url.rstrip("/") + "/v1/messages"
        system, chat_messages = split_system_messages(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens or 4096,
            "messages": [{"role": message.role, "content": message.content} for message in chat_messages],
            "stream": True,
        }
        if system:
            payload["system"] = system

        headers = {"anthropic-version": os.getenv("ANTHROPIC_VERSION", DEFAULT_ANTHROPIC_VERSION)}
        headers.update(anthropic_auth_headers(self.config.api_key))
        for data in stream_sse_json(url, payload, headers=headers, timeout=self.config.model_timeout):
            event_type = data.get("type")
            if event_type == "content_block_delta":
                delta = data.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                    yield delta["text"]
            elif event_type == "content_block_start":
                block = data.get("content_block")
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    yield block["text"]

    def _complete_gemini(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("gemini provider requires --api-key, GEMINI_API_KEY, or GOOGLE_API_KEY.")

        url = self.config.base_url.rstrip("/") + f"/models/{self.config.model}:generateContent"
        system, chat_messages = split_system_messages(messages)
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in chat_messages
            ]
        }
        if self.config.max_output_tokens:
            payload["generationConfig"] = {"maxOutputTokens": self.config.max_output_tokens}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        data = post_json(url, payload, headers={"x-goog-api-key": self.config.api_key}, timeout=self.config.model_timeout)
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(str(part.get("text", "")) for part in parts)

    def _stream_gemini(self, messages: list[Message]) -> Iterator[str]:
        if not self.config.api_key:
            raise ConfigurationError("gemini provider requires --api-key, GEMINI_API_KEY, or GOOGLE_API_KEY.")

        url = self.config.base_url.rstrip("/") + f"/models/{self.config.model}:streamGenerateContent?alt=sse"
        system, chat_messages = split_system_messages(messages)
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in chat_messages
            ]
        }
        if self.config.max_output_tokens:
            payload["generationConfig"] = {"maxOutputTokens": self.config.max_output_tokens}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        for data in stream_sse_json(url, payload, headers={"x-goog-api-key": self.config.api_key}, timeout=self.config.model_timeout):
            candidates = data.get("candidates", [])
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts", [])
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        yield part["text"]

    def _complete_codex(self, messages: list[Message]) -> str:
        command = find_codex_command()
        prompt = render_messages_for_codex(messages)
        with tempfile.TemporaryDirectory(prefix="minimal-cli-agent-codex-") as tmp:
            output_path = Path(tmp) / "last-message.txt"
            args = [
                *command,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_path),
            ]
            if self.config.model:
                args.extend(["--model", self.config.model])
            args.append(prompt)

            try:
                result = subprocess.run(
                    args,
                    cwd=self.config.cwd,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.config.model_timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ModelRequestError(f"Codex CLI request failed: {exc}") from exc

            if result.returncode != 0:
                raise ModelRequestError(f"Codex CLI request failed with exit code {result.returncode}:\n{result.stdout}")

            if output_path.exists():
                content = output_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
            return result.stdout.strip()


def split_system_messages(messages: list[Message]) -> tuple[str, list[Message]]:
    system_parts = [message.content for message in messages if message.role == "system"]
    chat_messages = [message for message in messages if message.role != "system"]
    return "\n\n".join(system_parts), chat_messages


def anthropic_auth_headers(api_key: str) -> dict[str, str]:
    if api_key.startswith("sk-ant-"):
        return {"x-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def extract_anthropic_text(data: dict) -> str:
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]

    completion = data.get("completion")
    if isinstance(completion, str):
        return completion
    return ""


def describe_empty_anthropic_response(data: dict) -> str:
    content = data.get("content")
    content_types: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                content_types.append(str(block.get("type", "<unknown>")))
            else:
                content_types.append(type(block).__name__)
    return (
        "Anthropic response contained no text content "
        f"(stop_reason={data.get('stop_reason', '<none>')}, "
        f"content_types={content_types or '<none>'}, "
        f"response_keys={sorted(str(key) for key in data.keys())})."
    )


def render_messages_for_codex(messages: list[Message]) -> str:
    rendered = [
        "Continue the following conversation. Return only the assistant's next message.",
        "Do not mention that this prompt was transformed.",
        "You are being used only as a model adapter for minimal-agent.",
        "Do not inspect files or run commands yourself.",
        "If workspace action is needed, return a bash-action or tool-action block and let minimal-agent execute it.",
        "",
    ]
    for message in messages:
        rendered.append(f"<{message.role}>")
        rendered.append(message.content)
        rendered.append(f"</{message.role}>")
        rendered.append("")
    return "\n".join(rendered).strip()


def find_codex_command() -> list[str]:
    configured = os.getenv("CODEX_COMMAND")
    if configured:
        return shlex.split(configured)

    candidates: list[list[str]] = []
    for path in sorted(glob.glob(str(Path.home() / ".nvm/versions/node/*/lib/node_modules/@openai/codex/bin/codex.js")), reverse=True):
        candidates.append(["node", path])

    path = shutil.which("codex")
    if path:
        candidates.append([path])

    if not candidates:
        raise ConfigurationError("codex provider requires Codex CLI on PATH, an nvm @openai/codex install, or CODEX_COMMAND.")

    return candidates[0]


def _make_http_client(timeout: int) -> httpx.Client:
    return httpx.Client(timeout=timeout, trust_env=True)


_http_clients: dict[int, httpx.Client] = {}


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 120) -> dict:
    client = _http_clients.get(timeout)
    if client is None:
        client = _make_http_client(timeout)
        _http_clients[timeout] = client
    try:
        response = client.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"Model request failed: HTTP {exc.response.status_code}: {exc.response.text}") from exc
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ModelRequestError(f"Model request failed for {url}: {exc}") from exc


def stream_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 120) -> Iterator[dict]:
    client = _http_clients.get(timeout)
    if client is None:
        client = _make_http_client(timeout)
        _http_clients[timeout] = client
    try:
        with client.stream(
            "POST",
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                **(headers or {}),
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                yield json.loads(line)
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"Model request failed: HTTP {exc.response.status_code}: {exc.response.text}") from exc
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ModelRequestError(f"Model stream failed for {url}: {exc}") from exc


def stream_sse_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 120) -> Iterator[dict]:
    client = _http_clients.get(timeout)
    if client is None:
        client = _make_http_client(timeout)
        _http_clients[timeout] = client
    try:
        with client.stream(
            "POST",
            url,
            json=payload,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        ) as response:
            response.raise_for_status()
            data_lines: list[str] = []
            for line in response.iter_lines():
                if line == "":
                    if data_lines:
                        yield from parse_sse_data_lines(data_lines)
                        data_lines = []
                    continue
                if line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
            if data_lines:
                yield from parse_sse_data_lines(data_lines)
    except httpx.HTTPStatusError as exc:
        raise ModelRequestError(f"Model request failed: HTTP {exc.response.status_code}: {exc.response.text}") from exc
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ModelRequestError(f"Model stream failed for {url}: {exc}") from exc


def parse_sse_data_lines(lines: list[str]) -> Iterator[dict]:
    raw = "\n".join(line for line in lines if line)
    if not raw or raw == "[DONE]":
        return
    yield json.loads(raw)
