from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta
from typing import Iterable, Sequence

import requests

from scripts.gigo_constants import OPEN_METEO_FORECAST_URL, OPEN_METEO_SINGLE_RUN_URL
from scripts.gigo_models import DailyRainResult, RainResult, Store
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


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value!r}. Use YYYY-MM-DD.") from exc


def resolve_forecast_start_date(raw: str | None, *, now: date | None = None) -> date:
    if raw and raw.strip():
        return parse_iso_date(raw.strip())
    return now or datetime.now().date()


def default_previous_sunday_run(forecast_start: date, *, run_hour_utc: int = 0) -> str:
    if not (0 <= run_hour_utc <= 23):
        raise ValueError("OPEN_METEO_RUN_HOUR_UTC must be between 0 and 23")
    days_since_sunday = (forecast_start.weekday() + 1) % 7
    previous_sunday = forecast_start - timedelta(days=days_since_sunday)
    return f"{previous_sunday.isoformat()}T{run_hour_utc:02d}:00"


def _target_dates(forecast_start: date, week_days: int) -> list[date]:
    if week_days <= 0:
        raise ValueError("WEEK_DAYS must be positive")
    return [forecast_start + timedelta(days=i) for i in range(week_days)]


def fetch_open_meteo_single_run_week(
    stores: Sequence[Store],
    *,
    week_start: date,
    week_days: int,
    run: str,
    model: str = "jma_gsm",
    batch_size: int = 50,
    session: requests.Session | None = None,
) -> list[DailyRainResult]:
    http = session or requests.Session()
    days = _target_dates(week_start, week_days)
    run_date = parse_iso_date(run[:10])
    forecast_days = (days[-1] - run_date).days + 1
    if forecast_days <= 0:
        raise ValueError("OPEN_METEO_RUN must be earlier than or equal to the requested week")
    results: list[DailyRainResult] = []
    for group in chunked(list(stores), batch_size):
        params = {
            "latitude": ",".join(f"{store.latitude:.6f}" for store in group),
            "longitude": ",".join(f"{store.longitude:.6f}" for store in group),
            "daily": "precipitation_probability_max,precipitation_sum",
            "timezone": "Asia/Tokyo",
            "run": run,
            "models": model,
            "forecast_days": forecast_days,
        }
        payload = request_json_with_retry(http, OPEN_METEO_SINGLE_RUN_URL, params=params, timeout=60)
        weather_items = payload if isinstance(payload, list) else [payload]
        if len(weather_items) != len(group):
            raise RuntimeError(f"Open-Meteo Single Runs returned {len(weather_items)} location payloads for {len(group)} stores")
        for store, item in zip(group, weather_items):
            if not isinstance(item, dict):
                raise RuntimeError(f"Invalid Open-Meteo payload for {store.name}: {item!r}")
            daily = item.get("daily") or {}
            times = daily.get("time") or []
            probs = daily.get("precipitation_probability_max") or []
            precipitation_sums = daily.get("precipitation_sum") or []
            if len(times) != len(probs) or len(times) != len(precipitation_sums):
                raise RuntimeError(f"Open-Meteo daily length mismatch for {store.name}")
            by_date: dict[date, int] = {}
            precipitation_by_date: dict[date, float] = {}
            for time_text, prob, precipitation_sum in zip(times, probs, precipitation_sums):
                try:
                    target_date = date.fromisoformat(str(time_text))
                except (TypeError, ValueError):
                    continue
                if prob is not None:
                    try:
                        probability = int(round(float(prob)))
                    except (TypeError, ValueError):
                        probability = 0
                    by_date[target_date] = max(0, min(100, probability))
                if precipitation_sum is not None:
                    try:
                        precipitation_by_date[target_date] = max(0.0, round(float(precipitation_sum), 2))
                    except (TypeError, ValueError):
                        pass
            for target_date in days:
                results.append(
                    DailyRainResult(
                        store=store,
                        target_date=target_date,
                        probability=by_date.get(target_date, 0),
                        source=f"Open-Meteo Single Runs {model} run={run}",
                        precipitation_mm=precipitation_by_date.get(target_date),
                    )
                )
    return results


def group_weekly_results_by_date(results: Sequence[DailyRainResult]) -> dict[date, list[DailyRainResult]]:
    grouped: dict[date, list[DailyRainResult]] = defaultdict(list)
    for result in results:
        grouped[result.target_date].append(result)
    return dict(sorted(grouped.items()))
