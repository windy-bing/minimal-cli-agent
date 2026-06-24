from __future__ import annotations

import re

SECRET_KEY_RE = re.compile(
    r"(?P<key>\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION)[A-Z0-9_]*\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^\s'\",;]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)

BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")
GOOGLE_API_KEY_RE = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def redact_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = SECRET_KEY_RE.sub(redact_key_value, text)
    text = ANTHROPIC_KEY_RE.sub("<redacted:anthropic-key>", text)
    text = OPENAI_KEY_RE.sub("<redacted:openai-key>", text)
    text = GOOGLE_API_KEY_RE.sub("<redacted:google-api-key>", text)
    text = JWT_RE.sub("<redacted:jwt>", text)
    return text


def redact_key_value(match: re.Match[str]) -> str:
    key = match.group("key")
    sep = match.group("sep")
    return f"{key}{sep}<redacted>"
