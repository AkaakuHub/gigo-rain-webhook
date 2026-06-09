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

from scripts.discord_message import build_message_lines, send_discord_messages, split_discord_messages
from scripts.geocoding import attach_coordinates
from scripts.shop_scraper import fetch_all_records
from scripts.store_repository import load_stores, write_store_csv
from scripts.weather import fetch_precipitation_probabilities


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {raw!r}") from exc


def env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer or empty: {raw!r}") from exc


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
    top_n = args.top_n if args.top_n is not None else env_optional_int("TOP_N")
    batch_size = args.batch_size if args.batch_size is not None else env_int("OPEN_METEO_BATCH_SIZE", 50)
    target = datetime.now(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=target_days_ahead)
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
    send_discord_messages(webhook_url, messages)
    print(json.dumps({"target_date": target.isoformat(), "stores": len(stores), "messages": len(messages)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Notify Discord of next-morning rain probability for static GiGO stores.")
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

    notify_parser = sub.add_parser("notify", help="Read static CSV, fetch weather, and post plain text to Discord.")
    notify_parser.add_argument("--csv", default=None)
    notify_parser.add_argument("--discord-webhook-url", default=None)
    notify_parser.add_argument("--target-days-ahead", type=int, default=None)
    notify_parser.add_argument("--morning-start-hour", type=int, default=None)
    notify_parser.add_argument("--morning-end-hour", type=int, default=None)
    notify_parser.add_argument("--min-probability", type=int, default=None)
    notify_parser.add_argument("--top-n", type=int, default=None)
    notify_parser.add_argument("--batch-size", type=int, default=None)
    notify_parser.set_defaults(func=notify)
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
