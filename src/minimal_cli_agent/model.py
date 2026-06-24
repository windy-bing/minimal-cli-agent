from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from minimal_cli_agent.constants import Providers
from minimal_cli_agent.exceptions import ConfigurationError, ModelRequestError
from minimal_cli_agent.types import AgentConfig, Message


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

    def _complete_ollama(self, messages: list[Message]) -> str:
        url = self.config.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
            "stream": False,
        }
        data = post_json(url, payload, timeout=self.config.model_timeout)
        return str(data.get("message", {}).get("content", ""))

    def _complete_openai_compatible(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("openai-compatible provider requires --api-key or OPENAI_API_KEY.")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in messages],
        }
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = post_json(url, payload, headers=headers, timeout=self.config.model_timeout)
        choices = data.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", ""))

    def _complete_anthropic(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("anthropic provider requires --api-key, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN.")

        url = self.config.base_url.rstrip("/") + "/v1/messages"
        system, chat_messages = split_system_messages(messages)
        payload = {
            "model": self.config.model,
            "max_tokens": 4096,
            "messages": [{"role": message.role, "content": message.content} for message in chat_messages],
        }
        if system:
            payload["system"] = system

        headers = {"anthropic-version": "2023-06-01"}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        data = post_json(url, payload, headers=headers, timeout=self.config.model_timeout)
        blocks = data.get("content", [])
        return "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")

    def _complete_gemini(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ConfigurationError("gemini provider requires --api-key, GEMINI_API_KEY, or GOOGLE_API_KEY.")

        url = (
            self.config.base_url.rstrip("/")
            + f"/models/{self.config.model}:generateContent?key={self.config.api_key}"
        )
        system, chat_messages = split_system_messages(messages)
        payload = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in chat_messages
            ]
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        data = post_json(url, payload, timeout=self.config.model_timeout)
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(str(part.get("text", "")) for part in parts)

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


def render_messages_for_codex(messages: list[Message]) -> str:
    rendered = [
        "Continue the following conversation. Return only the assistant's next message.",
        "Do not mention that this prompt was transformed.",
        "You are being used only as a model adapter for minimal-agent.",
        "Do not inspect files or run commands yourself.",
        "If workspace action is needed, return a bash-action block and let minimal-agent execute it.",
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

    for fallback in (
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ):
        if Path(fallback).exists():
            candidates.append([fallback])

    if not candidates:
        raise ConfigurationError("codex provider requires Codex CLI. Install @openai/codex or set CODEX_COMMAND.")

    return candidates[0]


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 120) -> dict:
    try:
        with httpx.Client(timeout=timeout, trust_env=True) as client:
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
