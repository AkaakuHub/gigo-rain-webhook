from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

import requests

from scripts.gigo_constants import JMA_FORECAST_URL_TEMPLATE, JMA_PREF_OFFICE_CODES
from scripts.gigo_models import DailyRainResult, Store
from scripts.http_client import request_json_with_retry
from scripts.weather import parse_iso_date


def _parse_jma_datetime_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return parse_iso_date(value[:10])
        except ValueError:
            return None


def _parse_probability(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, min(100, int(round(float(text)))))
    except ValueError:
        return None


def extract_jma_weekly_probabilities(payload: object, *, week_start: date, week_days: int) -> dict[date, int]:
    """Extract day-level precipitation probability from JMA bosai forecast JSON.

    The JMA forecast JSON has several timeSeries blocks. This function intentionally
    accepts either the official full payload list or a compact test payload, and uses
    the timeSeries/areas pair that provides pops aligned with date-like timeDefines.
    """
    target_dates = {week_start + timedelta(days=i) for i in range(week_days)}
    candidates: list[tuple[int, dict[date, int]]] = []
    reports = payload if isinstance(payload, list) else [payload]
    for report in reports:
        if not isinstance(report, dict):
            continue
        for series in report.get("timeSeries") or []:
            if not isinstance(series, dict):
                continue
            time_defines = series.get("timeDefines") or []
            dates = [_parse_jma_datetime_date(value) for value in time_defines]
            if not dates:
                continue
            for area in series.get("areas") or []:
                if not isinstance(area, dict):
                    continue
                pops = area.get("pops") or []
                if not pops or len(pops) != len(dates):
                    continue
                extracted: dict[date, int] = {}
                for target_date, raw_pop in zip(dates, pops):
                    if target_date not in target_dates:
                        continue
                    probability = _parse_probability(raw_pop)
                    if probability is not None:
                        extracted[target_date] = probability
                if extracted:
                    candidates.append((len(dates), extracted))
    merged: dict[date, int] = {}
    for _, extracted in sorted(candidates, key=lambda item: item[0]):
        merged.update(extracted)
    return {target: merged.get(target, 0) for target in sorted(target_dates)}


def fetch_jma_weekly_prefecture_probabilities(
    prefectures: Sequence[str],
    *,
    week_start: date,
    week_days: int,
    session: requests.Session | None = None,
) -> dict[str, dict[date, int]]:
    http = session or requests.Session()
    output: dict[str, dict[date, int]] = {}
    for prefecture in sorted(set(prefectures)):
        office_code = JMA_PREF_OFFICE_CODES.get(prefecture)
        if not office_code:
            raise ValueError(f"Unsupported prefecture for JMA weekly forecast: {prefecture}")
        payload = request_json_with_retry(http, JMA_FORECAST_URL_TEMPLATE.format(office_code=office_code), timeout=60)
        output[prefecture] = extract_jma_weekly_probabilities(payload, week_start=week_start, week_days=week_days)
    return output


def build_jma_weekly_store_results(
    stores: Sequence[Store],
    *,
    week_start: date,
    week_days: int,
    session: requests.Session | None = None,
) -> list[DailyRainResult]:
    by_prefecture = fetch_jma_weekly_prefecture_probabilities(
        [store.prefecture for store in stores],
        week_start=week_start,
        week_days=week_days,
        session=session,
    )
    results: list[DailyRainResult] = []
    for store in stores:
        probs = by_prefecture.get(store.prefecture, {})
        for index in range(week_days):
            target_date = week_start + timedelta(days=index)
            results.append(
                DailyRainResult(
                    store=store,
                    target_date=target_date,
                    probability=probs.get(target_date, 0),
                    source="JMA prefectural weekly forecast",
                )
            )
    return results
