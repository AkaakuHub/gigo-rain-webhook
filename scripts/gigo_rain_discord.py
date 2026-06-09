#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.gigo.co.jp"
SHOPS_URL = f"{BASE_URL}/shops"
GSI_ADDRESS_SEARCH_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CSV_FIELDS = ["store_id", "name", "prefecture", "address", "latitude", "longitude", "source_url"]
GEOCODE_CACHE_FIELDS = ["address", "latitude", "longitude"]
DISCORD_CONTENT_LIMIT = 2000
DEFAULT_MESSAGE_LIMIT = 1900

PREFECTURES: list[tuple[str, str]] = [
    ("01", "北海道"), ("02", "青森県"), ("03", "岩手県"), ("04", "宮城県"),
    ("05", "秋田県"), ("06", "山形県"), ("07", "福島県"), ("08", "茨城県"),
    ("09", "栃木県"), ("10", "群馬県"), ("11", "埼玉県"), ("12", "千葉県"),
    ("13", "東京都"), ("14", "神奈川県"), ("15", "新潟県"), ("16", "富山県"),
    ("17", "石川県"), ("18", "福井県"), ("19", "山梨県"), ("20", "長野県"),
    ("21", "岐阜県"), ("22", "静岡県"), ("23", "愛知県"), ("24", "三重県"),
    ("25", "滋賀県"), ("26", "京都府"), ("27", "大阪府"), ("28", "兵庫県"),
    ("29", "奈良県"), ("30", "和歌山県"), ("31", "鳥取県"), ("32", "島根県"),
    ("33", "岡山県"), ("34", "広島県"), ("35", "山口県"), ("36", "徳島県"),
    ("37", "香川県"), ("38", "愛媛県"), ("39", "高知県"), ("40", "福岡県"),
    ("41", "佐賀県"), ("42", "長崎県"), ("43", "熊本県"), ("44", "大分県"),
    ("45", "宮崎県"), ("46", "鹿児島県"), ("47", "沖縄県"),
]
PREF_ORDER = {pref: int(code) for code, pref in PREFECTURES}


@dataclass(frozen=True)
class StoreRecord:
    store_id: str
    name: str
    prefecture: str
    address: str
    source_url: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Store:
    store_id: str
    name: str
    prefecture: str
    address: str
    latitude: float
    longitude: float
    source_url: str


@dataclass(frozen=True)
class RainResult:
    store: Store
    probability: int


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {raw!r}") from exc


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer or empty: {raw!r}") from exc


def request_text(session: requests.Session, url: str, *, params: dict[str, str] | None = None, attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                params=params,
                timeout=30,
                headers={"User-Agent": "gigo-rain-discord/1.0 (+https://github.com/)"},
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


def parse_result_count(soup: BeautifulSoup) -> int | None:
    match = re.search(r"検索結果\s*([0-9,]+)\s*件", clean_text(soup.get_text(" ")))
    return int(match.group(1).replace(",", "")) if match else None


def has_more_link(soup: BeautifulSoup) -> bool:
    return any("もっと見る" in clean_text(a.get_text(" ")) for a in soup.select("a[href]"))


def store_id_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    if not path.startswith("/shops/"):
        return None
    slug = path.rsplit("/", 1)[-1]
    if not slug or slug == "shops":
        return None
    return slug


def extract_store_records(soup: BeautifulSoup, prefecture: str) -> list[StoreRecord]:
    records: list[StoreRecord] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = urljoin(BASE_URL, a.get("href", ""))
        store_id = store_id_from_url(href)
        if not store_id or store_id in seen:
            continue
        text = clean_text(a.get_text(" "))
        if prefecture not in text:
            continue
        idx = text.find(prefecture)
        name = clean_text(text[:idx])
        address = clean_text(text[idx:])
        if not name or not address:
            continue
        seen.add(store_id)
        records.append(StoreRecord(store_id=store_id, name=name, prefecture=prefecture, address=address, source_url=href))
    return records


def fetch_prefecture_records(
    session: requests.Session,
    area_code: str,
    prefecture: str,
    *,
    max_pages: int,
    sleep_seconds: float,
) -> list[StoreRecord]:
    records_by_id: dict[str, StoreRecord] = {}
    expected_count: int | None = None
    for page in range(1, max_pages + 1):
        params = {"area": area_code}
        if page > 1:
            params["page"] = str(page)
            params["q"] = ""
        html = request_text(session, SHOPS_URL, params=params)
        soup = BeautifulSoup(html, "html.parser")
        if expected_count is None:
            expected_count = parse_result_count(soup)
        for record in extract_store_records(soup, prefecture):
            records_by_id.setdefault(record.store_id, record)
        if expected_count is not None and len(records_by_id) >= expected_count:
            break
        if not has_more_link(soup):
            break
        time.sleep(sleep_seconds)
    else:
        raise RuntimeError(f"Reached max pages for {prefecture} before collecting all records")

    records = list(records_by_id.values())
    if expected_count is not None and len(records) != expected_count:
        raise RuntimeError(f"Official count mismatch for {prefecture}: expected {expected_count}, collected {len(records)}")
    return records


def fetch_all_records(session: requests.Session, *, max_pages: int, sleep_seconds: float) -> list[StoreRecord]:
    all_records: dict[str, StoreRecord] = {}
    for area_code, prefecture in PREFECTURES:
        records = fetch_prefecture_records(session, area_code, prefecture, max_pages=max_pages, sleep_seconds=sleep_seconds)
        for record in records:
            if record.store_id in all_records:
                raise RuntimeError(f"Duplicate store id across prefectures: {record.store_id}")
            all_records[record.store_id] = record
        print(f"{prefecture}: {len(records)} records", flush=True)
        time.sleep(sleep_seconds)
    return list(all_records.values())


def existing_coordinates(csv_path: Path) -> dict[str, tuple[str, float, float]]:
    if not csv_path.exists():
        return {}
    result: dict[str, tuple[str, float, float]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            store_id = (row.get("store_id") or "").strip()
            address = (row.get("address") or "").strip()
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            if not store_id or not lat_raw or not lon_raw:
                continue
            try:
                result[store_id] = (address, float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    return result


def load_geocode_cache(cache_path: Path | None) -> dict[str, tuple[float, float]]:
    if cache_path is None or not cache_path.exists():
        return {}
    result: dict[str, tuple[float, float]] = {}
    with cache_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            address = (row.get("address") or "").strip()
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            if not address or not lat_raw or not lon_raw:
                continue
            try:
                result[address] = (float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    return result


def write_geocode_cache(cache_path: Path | None, cache: dict[str, tuple[float, float]]) -> None:
    if cache_path is None:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=GEOCODE_CACHE_FIELDS)
        writer.writeheader()
        for address, (latitude, longitude) in sorted(cache.items()):
            writer.writerow({"address": address, "latitude": f"{latitude:.6f}", "longitude": f"{longitude:.6f}"})


def geocode_candidates(address: str) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        value = clean_text(value)
        if value and value not in candidates:
            candidates.append(value)

    add(address)
    add(re.split(r"\s+", clean_text(address), maxsplit=1)[0])
    no_floor = re.sub(r"(?:B?\d+F|地下\d+階|\d+階|[０-９]+階|[０-９]+F).*", "", address, flags=re.IGNORECASE)
    add(no_floor)
    add(re.split(r"(?:店内|内|区画|フロア|階)", address, maxsplit=1)[0])
    return candidates


def geocode_address(session: requests.Session, address: str, *, attempts: int = 3) -> tuple[float, float] | None:
    for candidate in geocode_candidates(address):
        for attempt in range(1, attempts + 1):
            try:
                response = session.get(
                    GSI_ADDRESS_SEARCH_URL,
                    params={"q": candidate},
                    timeout=30,
                    headers={"User-Agent": "gigo-rain-discord/1.0 (+https://github.com/)"},
                )
                if 500 <= response.status_code < 600 and attempt < attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, list) or not data:
                    break
                coordinates = (((data[0] or {}).get("geometry") or {}).get("coordinates") or [])
                if len(coordinates) >= 2:
                    lon = float(coordinates[0])
                    lat = float(coordinates[1])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        return lat, lon
            except Exception:
                if attempt == attempts:
                    break
                time.sleep(2 ** (attempt - 1))
    return None


def attach_coordinates(
    session: requests.Session,
    records: Iterable[StoreRecord],
    *,
    current_csv: Path,
    geocode_cache_path: Path | None,
    force_geocode: bool,
    allow_missing_coordinates: bool,
    sleep_seconds: float,
) -> list[StoreRecord]:
    current = existing_coordinates(current_csv)
    geocode_cache = load_geocode_cache(geocode_cache_path)
    output: list[StoreRecord] = []
    missing: list[StoreRecord] = []
    records = list(records)
    reused = 0
    geocoded = 0
    for index, record in enumerate(records, start=1):
        cached = current.get(record.store_id)
        if cached and not force_geocode and cached[0] == record.address:
            output.append(replace(record, latitude=cached[1], longitude=cached[2]))
            reused += 1
            continue
        cached_coords = geocode_cache.get(record.address)
        if cached_coords and not force_geocode:
            output.append(replace(record, latitude=cached_coords[0], longitude=cached_coords[1]))
            reused += 1
            continue
        print(f"Geocoding {index}/{len(records)}: {record.name}", flush=True)
        coords = geocode_address(session, record.address)
        if coords is None:
            missing.append(record)
            output.append(record)
        else:
            geocode_cache[record.address] = coords
            output.append(replace(record, latitude=coords[0], longitude=coords[1]))
            geocoded += 1
        time.sleep(sleep_seconds)
    write_geocode_cache(geocode_cache_path, geocode_cache)
    print(f"Coordinate cache reused: {reused}, geocoded: {geocoded}, missing: {len(missing)}", flush=True)
    if missing and not allow_missing_coordinates:
        details = "\n".join(f"- {r.name} / {r.prefecture} / {r.address}" for r in missing[:50])
        if len(missing) > 50:
            details += f"\n... and {len(missing) - 50} more"
        raise RuntimeError(f"Failed to geocode {len(missing)} stores:\n{details}")
    return output


def write_csv(records: list[StoreRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda r: (PREF_ORDER.get(r.prefecture, 999), r.name, r.store_id))
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "store_id": record.store_id,
                    "name": record.name,
                    "prefecture": record.prefecture,
                    "address": record.address,
                    "latitude": "" if record.latitude is None else f"{record.latitude:.6f}",
                    "longitude": "" if record.longitude is None else f"{record.longitude:.6f}",
                    "source_url": record.source_url,
                }
            )


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
    write_csv(with_coords, output_path)
    print(f"Wrote {len(with_coords)} stores to {output_path}", flush=True)
    return 0


def load_stores(csv_path: str | Path) -> list[Store]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Store CSV not found: {path}")
    stores: list[Store] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = set(CSV_FIELDS)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Store CSV is missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            pref = (row.get("prefecture") or "").strip()
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            if not name and not pref and not lat_raw and not lon_raw:
                continue
            if not name or not pref or not lat_raw or not lon_raw:
                raise ValueError(f"Store CSV row {row_number} has blank required values: {row}")
            try:
                lat = float(lat_raw)
                lon = float(lon_raw)
            except ValueError as exc:
                raise ValueError(f"Store CSV row {row_number} has invalid coordinates: {row}") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"Store CSV row {row_number} coordinates are out of range: {row}")
            stores.append(
                Store(
                    store_id=(row.get("store_id") or "").strip(),
                    name=name,
                    prefecture=pref,
                    address=(row.get("address") or "").strip(),
                    latitude=lat,
                    longitude=lon,
                    source_url=(row.get("source_url") or "").strip(),
                )
            )
    if not stores:
        raise ValueError("Store CSV has no stores. Run the manual update workflow first.")
    return stores


def chunked(items: Sequence[Store], size: int) -> Iterable[list[Store]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


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
                retry_after = 1.0
                try:
                    body = response.json()
                    retry_after = float(body.get("retry_after", retry_after))
                except Exception:
                    retry_after = float(response.headers.get("Retry-After", retry_after))
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


def notify(args: argparse.Namespace) -> int:
    csv_path = args.csv or os.getenv("GIGO_STORES_CSV", "data/gigo_stores.csv")
    webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    target_days_ahead = args.target_days_ahead if args.target_days_ahead is not None else _env_int("TARGET_DAYS_AHEAD", 1)
    morning_start_hour = args.morning_start_hour if args.morning_start_hour is not None else _env_int("MORNING_START_HOUR", 6)
    morning_end_hour = args.morning_end_hour if args.morning_end_hour is not None else _env_int("MORNING_END_HOUR", 12)
    min_probability = args.min_probability if args.min_probability is not None else _env_int("MIN_POP_PERCENT", 0)
    top_n = args.top_n if args.top_n is not None else _env_optional_int("TOP_N")
    batch_size = args.batch_size if args.batch_size is not None else _env_int("OPEN_METEO_BATCH_SIZE", 50)
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
