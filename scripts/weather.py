from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Iterable, Sequence

import requests

from scripts.gigo_constants import OPEN_METEO_FORECAST_URL
from scripts.gigo_models import RainResult, Store
from scripts.http_client import request_json_with_retry


def chunked(items: Sequence[Store], size: int) -> Iterable[list[Store]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def fetch_precipitation_probabilities(
    stores: Sequence[Store],
    target: date,
    *,
    morning_start_hour: int,
    morning_end_hour: int,
    batch_size: int = 50,
    session: requests.Session | None = None,
) -> list[RainResult]:
    if not (0 <= morning_start_hour <= 23):
        raise ValueError("MORNING_START_HOUR must be between 0 and 23")
    if not (1 <= morning_end_hour <= 24):
        raise ValueError("MORNING_END_HOUR must be between 1 and 24")
    if morning_start_hour >= morning_end_hour:
        raise ValueError("MORNING_START_HOUR must be smaller than MORNING_END_HOUR")
    http = session or requests.Session()
    results: list[RainResult] = []
    window_start = datetime.combine(target, dt_time(morning_start_hour, 0))
    window_end = datetime.combine(target, dt_time(0, 0)) + timedelta(hours=morning_end_hour)
    for group in chunked(list(stores), batch_size):
        params = {
            "latitude": ",".join(f"{store.latitude:.6f}" for store in group),
            "longitude": ",".join(f"{store.longitude:.6f}" for store in group),
            "hourly": "precipitation_probability",
            "timezone": "Asia/Tokyo",
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
        }
        payload = request_json_with_retry(http, OPEN_METEO_FORECAST_URL, params=params)
        weather_items = payload if isinstance(payload, list) else [payload]
        if len(weather_items) != len(group):
            raise RuntimeError(f"Open-Meteo returned {len(weather_items)} location payloads for {len(group)} stores")
        for store, item in zip(group, weather_items):
            if not isinstance(item, dict):
                raise RuntimeError(f"Invalid Open-Meteo payload for {store.name}: {item!r}")
            hourly = item.get("hourly") or {}
            times = hourly.get("time") or []
            probs = hourly.get("precipitation_probability") or []
            if len(times) != len(probs):
                raise RuntimeError(f"Open-Meteo time/probability length mismatch for {store.name}")
            window_values: list[int] = []
            for time_text, prob in zip(times, probs):
                if prob is None:
                    continue
                try:
                    local_dt = datetime.fromisoformat(str(time_text))
                    probability = int(round(float(prob)))
                except (TypeError, ValueError):
                    continue
                if window_start <= local_dt < window_end:
                    window_values.append(max(0, min(100, probability)))
            results.append(RainResult(store=store, probability=max(window_values) if window_values else 0))
    return results
