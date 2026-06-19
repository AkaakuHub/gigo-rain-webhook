from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Sequence
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scripts.gigo_models import DailyRainResult, Store
from scripts.http_client import request_json_with_retry, request_text

WNEWS_BASE_URL = "https://weathernews.jp"
WNEWS_LOCATION_SEARCH_URL = "https://weathernews.jp/onebox/api_search.cgi"
WNEWS_SOURCE_LABEL = "Weathernews onebox prefecture representative"
JST = ZoneInfo("Asia/Tokyo")

WNEWS_PREFECTURE_REPRESENTATIVE_QUERIES: dict[str, str] = {
    "北海道": "北海道札幌市",
    "青森県": "青森県青森市",
    "岩手県": "岩手県盛岡市",
    "宮城県": "宮城県仙台市",
    "秋田県": "秋田県秋田市",
    "山形県": "山形県山形市",
    "福島県": "福島県福島市",
    "茨城県": "茨城県水戸市",
    "栃木県": "栃木県宇都宮市",
    "群馬県": "群馬県前橋市",
    "埼玉県": "埼玉県さいたま市",
    "千葉県": "千葉県千葉市",
    "東京都": "東京都千代田区",
    "神奈川県": "神奈川県横浜市",
    "新潟県": "新潟県新潟市",
    "富山県": "富山県富山市",
    "石川県": "石川県金沢市",
    "福井県": "福井県福井市",
    "山梨県": "山梨県甲府市",
    "長野県": "長野県長野市",
    "岐阜県": "岐阜県岐阜市",
    "静岡県": "静岡県静岡市",
    "愛知県": "愛知県名古屋市",
    "三重県": "三重県津市",
    "滋賀県": "滋賀県大津市",
    "京都府": "京都府京都市",
    "大阪府": "大阪府大阪市",
    "兵庫県": "兵庫県神戸市",
    "奈良県": "奈良県奈良市",
    "和歌山県": "和歌山県和歌山市",
    "鳥取県": "鳥取県鳥取市",
    "島根県": "島根県松江市",
    "岡山県": "岡山県岡山市",
    "広島県": "広島県広島市",
    "山口県": "山口県山口市",
    "徳島県": "徳島県徳島市",
    "香川県": "香川県高松市",
    "愛媛県": "愛媛県松山市",
    "高知県": "高知県高知市",
    "福岡県": "福岡県福岡市",
    "佐賀県": "佐賀県佐賀市",
    "長崎県": "長崎県長崎市",
    "熊本県": "熊本県熊本市",
    "大分県": "大分県大分市",
    "宮崎県": "宮崎県宮崎市",
    "鹿児島県": "鹿児島県鹿児島市",
    "沖縄県": "沖縄県那覇市",
}


def _parse_probability(value: object) -> int | None:
    text = str(value or "").strip().replace("％", "%")
    if not text or text in {"-", "ー", "--"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    return max(0, min(100, int(round(float(match.group(0))))))


def _date_from_month_day(month: int, day: int, *, base_date: date) -> date | None:
    for year in (base_date.year, base_date.year + 1, base_date.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if base_date - timedelta(days=7) <= candidate <= base_date + timedelta(days=370):
            return candidate
    return None


def _parse_wnews_date(value: object, *, base_date: date, fallback_offset: int) -> date:
    text = str(value or "").strip()
    if "今日" in text:
        return base_date
    if "明日" in text:
        return base_date + timedelta(days=1)
    iso_match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if iso_match:
        return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
    month_day_match = re.search(r"(\d{1,2})\s*[月/]\s*(\d{1,2})", text)
    if month_day_match:
        parsed = _date_from_month_day(int(month_day_match.group(1)), int(month_day_match.group(2)), base_date=base_date)
        if parsed:
            return parsed
    return base_date + timedelta(days=fallback_offset)


def _extract_legacy_10day(soup: BeautifulSoup, *, base_date: date) -> dict[date, int]:
    output: dict[date, int] = {}
    for offset, item in enumerate(soup.select(".weather-10day__item")):
        date_node = item.select_one(".weather-10day__day")
        probability_node = item.select_one(".weather-10day__r")
        probability = _parse_probability(probability_node.get_text(" ", strip=True) if probability_node else None)
        if probability is None:
            continue
        target_date = _parse_wnews_date(
            date_node.get_text(" ", strip=True) if date_node else "",
            base_date=base_date,
            fallback_offset=offset,
        )
        output[target_date] = probability
    return output


def _extract_table_probabilities(soup: BeautifulSoup, *, base_date: date) -> dict[date, int]:
    candidates = soup.select(".wTable.week, [id*=week], [class*=week], [class*=Week]")
    output: dict[date, int] = {}
    for container in candidates:
        text = container.get_text(" ", strip=True)
        if "%" not in text and "％" not in text:
            continue
        cells = [cell.get_text(" ", strip=True) for cell in container.select("th, td, li, div, p, span")] or [text]
        dates: list[date] = []
        probabilities: list[int] = []
        for cell_text in cells:
            if re.search(r"今日|明日|\d{1,2}\s*[月/]\s*\d{1,2}|20\d{2}[-/年]\d{1,2}", cell_text):
                target_date = _parse_wnews_date(cell_text, base_date=base_date, fallback_offset=len(dates))
                if not dates or dates[-1] != target_date:
                    dates.append(target_date)
            if "%" in cell_text or "％" in cell_text or "降水" in cell_text:
                probability = _parse_probability(cell_text)
                if probability is not None:
                    probabilities.append(probability)
        for target_date, probability in zip(dates, probabilities):
            output[target_date] = probability
        if output:
            return output
    return output


def extract_wnews_weekly_probabilities(
    html: str,
    *,
    week_start: date,
    week_days: int,
    base_date: date | None = None,
) -> dict[date, int]:
    if week_days <= 0:
        raise ValueError("WEEK_DAYS must be positive")
    base = base_date or datetime.now(JST).date()
    soup = BeautifulSoup(html, "html.parser")
    extracted = _extract_legacy_10day(soup, base_date=base)
    if not extracted:
        extracted = _extract_table_probabilities(soup, base_date=base)
    target_dates = [week_start + timedelta(days=i) for i in range(week_days)]
    return {target_date: extracted.get(target_date, 0) for target_date in target_dates}


def fetch_wnews_prefecture_location_urls(
    prefectures: Sequence[str],
    *,
    session: requests.Session | None = None,
) -> dict[str, str]:
    http = session or requests.Session()
    output: dict[str, str] = {}
    for prefecture in sorted(set(prefectures)):
        query = WNEWS_PREFECTURE_REPRESENTATIVE_QUERIES.get(prefecture)
        if not query:
            raise ValueError(f"Unsupported prefecture for Weathernews forecast: {prefecture}")
        payload = request_json_with_retry(http, WNEWS_LOCATION_SEARCH_URL, params={"query": query})
        if not isinstance(payload, list) or not payload:
            raise RuntimeError(f"Weathernews location search returned no results for {prefecture}: {query}")
        first = payload[0]
        if not isinstance(first, dict) or not first.get("url"):
            raise RuntimeError(f"Invalid Weathernews location search payload for {prefecture}: {first!r}")
        output[prefecture] = urljoin(WNEWS_BASE_URL, str(first["url"]))
    return output


def fetch_wnews_prefecture_probabilities(
    prefectures: Sequence[str],
    *,
    week_start: date,
    week_days: int,
    session: requests.Session | None = None,
) -> dict[str, dict[date, int]]:
    http = session or requests.Session()
    urls = fetch_wnews_prefecture_location_urls(prefectures, session=http)
    today = datetime.now(JST).date()
    return {
        prefecture: extract_wnews_weekly_probabilities(
            request_text(http, url),
            week_start=week_start,
            week_days=week_days,
            base_date=today,
        )
        for prefecture, url in urls.items()
    }


def build_wnews_prefecture_store_results(
    stores: Sequence[Store],
    *,
    week_start: date,
    week_days: int,
    session: requests.Session | None = None,
) -> list[DailyRainResult]:
    by_prefecture = fetch_wnews_prefecture_probabilities(
        [store.prefecture for store in stores],
        week_start=week_start,
        week_days=week_days,
        session=session,
    )
    results: list[DailyRainResult] = []
    for store in stores:
        probabilities = by_prefecture.get(store.prefecture, {})
        for index in range(week_days):
            target_date = week_start + timedelta(days=index)
            results.append(
                DailyRainResult(
                    store=store,
                    target_date=target_date,
                    probability=probabilities.get(target_date, 0),
                    source=WNEWS_SOURCE_LABEL,
                )
            )
    return results


def group_prefecture_probabilities_by_date(
    results: Sequence[DailyRainResult],
) -> dict[date, list[DailyRainResult]]:
    grouped: dict[date, list[DailyRainResult]] = defaultdict(list)
    for result in results:
        grouped[result.target_date].append(result)
    return dict(sorted(grouped.items()))
