from __future__ import annotations

from typing import Sequence

import requests

from scripts.gigo_constants import DEFAULT_MESSAGE_LIMIT, DISCORD_CONTENT_LIMIT
from scripts.gigo_models import RainResult
from scripts.http_client import request_json_with_retry


def format_line(result: RainResult) -> str:
    return f"{result.store.name}/{result.store.prefecture} {result.probability}%"


def build_message_lines(results: Sequence[RainResult], *, min_probability: int = 0, top_n: int | None = None) -> list[str]:
    if not (0 <= min_probability <= 100):
        raise ValueError("MIN_POP_PERCENT must be between 0 and 100")
    filtered = [r for r in results if r.probability >= min_probability]
    filtered.sort(key=lambda r: (-r.probability, r.store.prefecture, r.store.name))
    if top_n is not None:
        if top_n <= 0:
            raise ValueError("TOP_N must be positive when set")
        filtered = filtered[:top_n]
    return [format_line(result) for result in filtered]


def split_discord_messages(lines: Sequence[str], *, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[str]:
    if limit <= 0 or limit > DISCORD_CONTENT_LIMIT:
        raise ValueError("Discord message limit must be between 1 and 2000")
    if not lines:
        return ["対象店舗なし"]
    messages: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) > limit:
            raise ValueError(f"Line is too long for Discord content: {line[:80]}...")
        added_len = len(line) if not current else len(line) + 1
        if current and current_len + added_len > limit:
            messages.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added_len
    if current:
        messages.append("\n".join(current))
    return messages or ["対象店舗なし"]


def send_discord_messages(webhook_url: str, messages: Sequence[str], *, session: requests.Session | None = None) -> None:
    if not webhook_url or not webhook_url.startswith("https://"):
        raise ValueError("DISCORD_WEBHOOK_URL is not set correctly")
    http = session or requests.Session()
    for message in messages:
        body = {"content": message, "allowed_mentions": {"parse": []}}
        request_json_with_retry(http, webhook_url, json_body=body, method="POST")
