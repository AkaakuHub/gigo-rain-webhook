from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scripts.gigo_constants import BASE_URL, PREFECTURES, SHOPS_URL
from scripts.gigo_models import StoreRecord
from scripts.http_client import request_text
from scripts.text_utils import clean_text


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
