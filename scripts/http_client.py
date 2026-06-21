from __future__ import annotations

import time

import requests


USER_AGENT = "gigo-rain-discord/1.0 (+https://github.com/)"


def request_text(session: requests.Session, url: str, *, params: dict[str, str] | None = None, attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                params=params,
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            if 500 <= response.status_code < 600 and attempt < attempts:
                time.sleep(2 ** (attempt - 1))
                continue
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(2 ** (attempt - 1))
    assert last_error is not None
    raise last_error


def request_json_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
    method: str = "GET",
    timeout: int = 30,
    max_attempts: int = 4,
) -> object:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if method == "GET":
                response = session.get(url, params=params, timeout=timeout)
            elif method == "POST":
                response = session.post(url, json=json_body, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            if response.status_code == 429:
                retry_after = 10.0
                try:
                    body = response.json()
                    retry_after = float(body.get("retry_after", retry_after))
                except Exception:
                    retry_after = float(response.headers.get("Retry-After", retry_after))
                last_error = RuntimeError(f"HTTP 429 Too Many Requests for {url}")
                if attempt == max_attempts:
                    break
                time.sleep(min(max(retry_after, 0.5), 30.0))
                continue
            if 500 <= response.status_code < 600 and attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue
            response.raise_for_status()
            if response.content:
                return response.json()
            return {}
        except Exception as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            time.sleep(2 ** (attempt - 1))
    assert last_error is not None
    raise last_error
