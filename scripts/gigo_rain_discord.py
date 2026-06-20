#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from scripts.discord_message import build_message_lines, build_weekly_message_lines, send_discord_messages, split_discord_messages
from scripts.geocoding import attach_coordinates
from scripts.jma_weekly import build_jma_weekly_store_results
from scripts.shop_scraper import fetch_all_records
from scripts.store_repository import load_stores, write_store_csv
from scripts.weather import (
    default_previous_sunday_run,
    fetch_open_meteo_single_run_week,
    fetch_precipitation_probabilities,
    resolve_forecast_start_date,
)
from scripts.wn_prefecture import build_wnews_prefecture_store_results


JST = ZoneInfo("Asia/Tokyo")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {raw!r}") from exc


def env_optional_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer or empty: {raw!r}") from exc


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def update_stores(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    geocode_cache_path = Path(args.geocode_cache) if args.geocode_cache else None
    store_name_pattern = re.compile(args.store_name_regex)
    session = requests.Session()
    print(f"Fetching official GiGO shop listings at {datetime.now().isoformat(timespec='seconds')}", flush=True)
    all_records = fetch_all_records(session, max_pages=args.max_pages, sleep_seconds=args.sleep_seconds)
    filtered = [record for record in all_records if store_name_pattern.search(record.name)]
    print(f"Official listing records: {len(all_records)}", flush=True)
    print(f"Filtered records written to CSV: {len(filtered)}", flush=True)
    if not filtered:
        raise RuntimeError(f"No stores matched --store-name-regex {args.store_name_regex!r}")
    with_coords = attach_coordinates(
        session,
        filtered,
        current_csv=output_path,
        geocode_cache_path=geocode_cache_path,
        force_geocode=args.force_geocode,
        allow_missing_coordinates=args.allow_missing_coordinates,
        sleep_seconds=args.sleep_seconds,
    )
    write_store_csv(with_coords, output_path)
    print(f"Wrote {len(with_coords)} stores to {output_path}", flush=True)
    return 0


def notify(args: argparse.Namespace) -> int:
    csv_path = args.csv or os.getenv("GIGO_STORES_CSV", "data/gigo_stores.csv")
    webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    target_days_ahead = args.target_days_ahead if args.target_days_ahead is not None else env_int("TARGET_DAYS_AHEAD", 1)
    morning_start_hour = args.morning_start_hour if args.morning_start_hour is not None else env_int("MORNING_START_HOUR", 6)
    morning_end_hour = args.morning_end_hour if args.morning_end_hour is not None else env_int("MORNING_END_HOUR", 12)
    min_probability = args.min_probability if args.min_probability is not None else env_int("MIN_POP_PERCENT", 0)
    top_n = args.top_n if args.top_n is not None else env_optional_int("TOP_N", 10)
    batch_size = args.batch_size if args.batch_size is not None else env_int("OPEN_METEO_BATCH_SIZE", 50)
    dry_run = args.dry_run or env_bool("DRY_RUN", False)
    target = datetime.now(JST).date() + timedelta(days=target_days_ahead)
    stores = load_stores(csv_path)
    results = fetch_precipitation_probabilities(
        stores,
        target,
        morning_start_hour=morning_start_hour,
        morning_end_hour=morning_end_hour,
        batch_size=batch_size,
    )
    lines = build_message_lines(results, min_probability=min_probability, top_n=top_n)
    messages = split_discord_messages(lines)
    if dry_run:
        print("\n\n--- Discord message ---\n\n".join(messages))
    else:
        send_discord_messages(webhook_url, messages)
    print(json.dumps({"target_date": target.isoformat(), "stores": len(stores), "messages": len(messages)}, ensure_ascii=False))
    return 0


def _source_values(raw: str) -> list[str]:
    value = (raw or "all").strip().lower().replace("-", "_")
    if value in {"all", "all_sources"}:
        return ["jma_weekly", "open_meteo_single_run", "weathernews_prefecture"]
    if value in {"jma", "jma_weekly"}:
        return ["jma_weekly"]
    if value in {"open_meteo", "open_meteo_single_run", "single_run"}:
        return ["open_meteo_single_run"]
    if value in {"weathernews", "weathernews_prefecture"}:
        return ["weathernews_prefecture"]
    raise ValueError("FORECAST_SOURCE must be all, jma_weekly, open_meteo_single_run, or weathernews_prefecture")


def notify_weekly(args: argparse.Namespace) -> int:
    csv_path = args.csv or os.getenv("GIGO_STORES_CSV", "data/gigo_stores.csv")
    webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    source_raw = args.source or os.getenv("FORECAST_SOURCE", "all")
    forecast_start_raw = args.forecast_start_date or os.getenv("FORECAST_START_DATE", "")
    week_days = args.week_days if args.week_days is not None else env_int("WEEK_DAYS", 7)
    min_probability = args.min_probability if args.min_probability is not None else env_int("MIN_POP_PERCENT", 0)
    top_n_per_day = args.top_n_per_day if args.top_n_per_day is not None else env_optional_int("TOP_N_PER_DAY", 10)
    batch_size = args.batch_size if args.batch_size is not None else env_int("OPEN_METEO_BATCH_SIZE", 50)
    dry_run = args.dry_run or env_bool("DRY_RUN", False)

    today_jst = datetime.now(JST).date()
    forecast_start = resolve_forecast_start_date(forecast_start_raw, now=today_jst)
    stores = load_stores(csv_path)
    sources = _source_values(source_raw)
    all_summary: list[dict[str, object]] = []

    for source in sources:
        if source == "jma_weekly":
            results = build_jma_weekly_store_results(stores, week_start=forecast_start, week_days=week_days)
            title = f"【GiGO週間雨予報 / 気象庁府県週間天気予報 / {forecast_start.isoformat()}から{week_days}日】"
        elif source == "open_meteo_single_run":
            model = args.open_meteo_model or os.getenv("OPEN_METEO_MODEL", "jma_gsm")
            run = args.open_meteo_run or os.getenv("OPEN_METEO_RUN", "").strip()
            if not run:
                run_hour_utc = args.open_meteo_run_hour_utc
                if run_hour_utc is None:
                    run_hour_utc = env_int("OPEN_METEO_RUN_HOUR_UTC", 0)
                run = default_previous_sunday_run(forecast_start, run_hour_utc=run_hour_utc)
            results = fetch_open_meteo_single_run_week(
                stores,
                week_start=forecast_start,
                week_days=week_days,
                run=run,
                model=model,
                batch_size=batch_size,
            )
            title = f"【GiGO週間雨予報 / Open-Meteo Single Runs {model} run={run} / {forecast_start.isoformat()}から{week_days}日】"
        elif source == "weathernews_prefecture":
            results = build_wnews_prefecture_store_results(stores, week_start=forecast_start, week_days=week_days)
            title = f"【GiGO週間雨予報 / Weathernews県庁所在地代表 / {forecast_start.isoformat()}から{week_days}日】"
        else:
            raise AssertionError(source)

        lines = build_weekly_message_lines(results, title=title, min_probability=min_probability, top_n_per_day=top_n_per_day)
        messages = split_discord_messages(lines)
        if dry_run:
            print("\n\n--- Discord message ---\n\n".join(messages))
        else:
            send_discord_messages(webhook_url, messages)
        all_summary.append({"source": source, "forecast_start": forecast_start.isoformat(), "stores": len(stores), "messages": len(messages)})

    print(json.dumps({"weekly": all_summary}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Notify Discord of GiGO rain probability from static store coordinates.")
    sub = parser.add_subparsers(dest="command", required=True)

    update = sub.add_parser("update-stores", help="Update static GiGO store CSV from official shop pages.")
    update.add_argument("--output", default=os.getenv("GIGO_STORES_CSV", "data/gigo_stores.csv"))
    update.add_argument("--store-name-regex", default=os.getenv("STORE_NAME_REGEX", r"^GiGO"))
    update.add_argument("--force-geocode", action="store_true")
    update.add_argument("--allow-missing-coordinates", action="store_true")
    update.add_argument("--geocode-cache", default=os.getenv("GEOCODE_CACHE", ""))
    update.add_argument("--max-pages", type=int, default=20)
    update.add_argument("--sleep-seconds", type=float, default=0.25)
    update.set_defaults(func=update_stores)

    notify_parser = sub.add_parser("notify", help="Read static CSV, fetch next-morning weather, and post plain text to Discord.")
    notify_parser.add_argument("--csv", default=None)
    notify_parser.add_argument("--discord-webhook-url", default=None)
    notify_parser.add_argument("--target-days-ahead", type=int, default=None)
    notify_parser.add_argument("--morning-start-hour", type=int, default=None)
    notify_parser.add_argument("--morning-end-hour", type=int, default=None)
    notify_parser.add_argument("--min-probability", type=int, default=None)
    notify_parser.add_argument("--top-n", type=int, default=None)
    notify_parser.add_argument("--batch-size", type=int, default=None)
    notify_parser.add_argument("--dry-run", action="store_true")
    notify_parser.set_defaults(func=notify)

    weekly = sub.add_parser("notify-weekly", help="Fetch a fixed one-week forecast and post it to Discord.")
    weekly.add_argument("--csv", default=None)
    weekly.add_argument("--discord-webhook-url", default=None)
    weekly.add_argument(
        "--source",
        choices=["all", "jma_weekly", "open_meteo_single_run", "weathernews_prefecture", "weathernews"],
        default=None,
    )
    weekly.add_argument("--forecast-start-date", default=None, help="Forecast start date in YYYY-MM-DD. Empty means today JST.")
    weekly.add_argument("--week-days", type=int, default=None)
    weekly.add_argument("--min-probability", type=int, default=None)
    weekly.add_argument("--top-n-per-day", type=int, default=None)
    weekly.add_argument("--batch-size", type=int, default=None)
    weekly.add_argument("--open-meteo-run", default=None, help="UTC model initialisation time, for example 2026-06-07T00:00.")
    weekly.add_argument("--open-meteo-run-hour-utc", type=int, default=None)
    weekly.add_argument("--open-meteo-model", default=None)
    weekly.add_argument("--dry-run", action="store_true")
    weekly.set_defaults(func=notify_weekly)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
