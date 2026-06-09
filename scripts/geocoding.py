from __future__ import annotations

import csv
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import requests

from scripts.gigo_constants import GEOCODE_CACHE_FIELDS, GSI_ADDRESS_SEARCH_URL
from scripts.gigo_models import StoreRecord
from scripts.http_client import USER_AGENT
from scripts.store_repository import existing_coordinates
from scripts.text_utils import clean_text


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
                    headers={"User-Agent": USER_AGENT},
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
