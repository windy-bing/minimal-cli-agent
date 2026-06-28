from __future__ import annotations

import shlex
from typing import Any

from minimal_cli_agent.constants import InteractiveCommands


def parse_events_query(argument: str) -> dict[str, Any]:
    query: dict[str, Any] = {"kind": "", "limit": 20, "offset": 0, "format": "text"}
    positional: list[str] = []
    try:
        tokens = shlex.split(argument)
    except ValueError as exc:
        raise ValueError(events_usage()) from exc
    for token in tokens:
        if "=" not in token:
            positional.append(token)
            continue
        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "kind":
            query["kind"] = value
        elif key == "limit":
            query["limit"] = parse_non_negative_int(value, "limit", minimum=1)
        elif key == "offset":
            query["offset"] = parse_non_negative_int(value, "offset")
        elif key == "format":
            if value not in {"text", "json"}:
                raise ValueError(events_usage())
            query["format"] = value
        else:
            raise ValueError(f"Unknown events option: {key}")
    apply_positional_events_query(query, positional)
    return query


def apply_positional_events_query(query: dict[str, Any], positional: list[str]) -> None:
    if positional:
        if positional[0].isdigit():
            query["limit"] = parse_non_negative_int(positional[0], "limit", minimum=1)
        elif not query["kind"]:
            query["kind"] = positional[0]
        else:
            raise ValueError(events_usage())
    if len(positional) >= 2:
        query["limit"] = parse_non_negative_int(positional[1], "limit", minimum=1)
    if len(positional) >= 3:
        query["offset"] = parse_non_negative_int(positional[2], "offset")
    if len(positional) > 3:
        raise ValueError(events_usage())


def parse_non_negative_int(value: str, field: str, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def events_usage() -> str:
    return f"Usage: {InteractiveCommands.EVENTS} [kind] [limit] [offset] [format=json]"
