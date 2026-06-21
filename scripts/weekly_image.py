from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from scripts.discord_message import format_date_heading, weekly_result_sort_key
from scripts.gigo_models import DailyRainResult


@dataclass(frozen=True)
class WeeklyImage:
    filename: str
    content: bytes


@dataclass(frozen=True)
class SourceForecast:
    label: str
    results: Sequence[DailyRainResult]


_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_EMOJI_FONT_CANDIDATES = [
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
]

_BACKGROUND = "#f6f8fb"
_TEXT = "#172033"
_MUTED = "#59657a"
_CARD = "#ffffff"
_GRID = "#d8dee9"
_HIGH = "#e5484d"
_MIDDLE = "#f59e0b"
_LOW = "#2563eb"
_SOURCE_COLORS = ["#e5484d", "#2563eb", "#16a34a"]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _FONT_CANDIDATES
    if bold:
        candidates = [
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            *candidates,
        ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _load_emoji_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _EMOJI_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        for candidate_size in (size, 20, 32, 40, 48, 64, 96, 109, 128, 160):
            try:
                return ImageFont.truetype(path, size=candidate_size)
            except OSError:
                continue
    return _load_font(size)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
    *,
    embedded_color: bool = False,
) -> None:
    try:
        draw.text(xy, text, font=font, fill=fill, embedded_color=embedded_color)
    except TypeError:
        draw.text(xy, text, font=font, fill=fill)


def _probability_color(probability: int) -> str:
    if probability >= 70:
        return _HIGH
    if probability >= 40:
        return _MIDDLE
    return _LOW


def _weather_mark(probability: int, precipitation_mm: float | None) -> str:
    if probability >= 70:
        return "☔"
    if probability >= 40 or (precipitation_mm is not None and precipitation_mm >= 1):
        return "🌧"
    return "🌂"


def _measure_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
    if _measure_width(draw, text, font) <= max_width:
        return text
    suffix = "..."
    for length in range(len(text), 0, -1):
        candidate = text[:length].rstrip() + suffix
        if _measure_width(draw, candidate, font) <= max_width:
            return candidate
    return suffix


def _group_weekly_results(
    results: Sequence[DailyRainResult],
    *,
    min_probability: int,
    top_n_per_day: int | None,
) -> dict[date, list[DailyRainResult]]:
    grouped: dict[date, list[DailyRainResult]] = defaultdict(list)
    for result in results:
        if result.probability >= min_probability:
            grouped[result.target_date].append(result)
    output: dict[date, list[DailyRainResult]] = {}
    for target_date, daily_results in grouped.items():
        sorted_results = sorted(daily_results, key=weekly_result_sort_key)
        if top_n_per_day is not None:
            sorted_results = sorted_results[:top_n_per_day]
        output[target_date] = sorted_results
    return dict(sorted(output.items()))


def _all_target_dates(sources: Sequence[SourceForecast]) -> list[date]:
    return sorted({result.target_date for source in sources for result in source.results})


def _format_result_value(result: DailyRainResult) -> str:
    if result.precipitation_mm is None:
        return f"{result.probability}%"
    return f"{result.probability}%/{result.precipitation_mm:g}mm"


def build_weekly_sources_image(
    sources: Sequence[SourceForecast],
    *,
    title: str,
    min_probability: int = 0,
    top_n_per_day: int | None = 10,
) -> WeeklyImage:
    if not (0 <= min_probability <= 100):
        raise ValueError("MIN_POP_PERCENT must be between 0 and 100")
    if top_n_per_day is not None and top_n_per_day <= 0:
        raise ValueError("TOP_N_PER_DAY must be positive when set")

    grouped_by_source = [
        _group_weekly_results(source.results, min_probability=min_probability, top_n_per_day=top_n_per_day)
        for source in sources
    ]
    target_dates = _all_target_dates(sources)
    title_font = _load_font(34, bold=True)
    date_font = _load_font(26, bold=True)
    source_font = _load_font(22, bold=True)
    row_font = _load_font(19)
    probability_font = _load_font(20, bold=True)
    emoji_font = _load_emoji_font(20)
    large_emoji_font = _load_emoji_font(32)

    width = 1500
    margin = 40
    card_gap = 20
    card_padding = 24
    row_height = 34
    source_header_height = 44
    card_width = width - margin * 2
    column_gap = 18
    columns = max(1, len(sources))
    column_width = int((card_width - card_padding * 2 - column_gap * (columns - 1)) / columns)
    rows_per_source = min(top_n_per_day or 5, 5)
    card_height = 94 + source_header_height + rows_per_source * row_height + card_padding
    content_height = 88 + len(target_dates) * (card_height + card_gap)
    height = max(360, content_height + margin)

    image = Image.new("RGB", (width, height), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    display_title = _fit_text(draw, title.strip("【】"), title_font, width - margin * 2)
    draw.text((margin, 28), display_title, font=title_font, fill=_TEXT)
    y = 92
    if not target_dates:
        draw.rounded_rectangle((margin, y, width - margin, y + 120), radius=12, fill=_CARD, outline=_GRID)
        draw.text((margin + card_padding, y + card_padding), "対象店舗なし", font=date_font, fill=_TEXT)
    for target_date in target_dates:
        daily_groups = [grouped.get(target_date, []) for grouped in grouped_by_source]
        max_probability = max((result.probability for daily in daily_groups for result in daily), default=0)
        accent = _probability_color(max_probability)
        draw.rounded_rectangle((margin, y, width - margin, y + card_height), radius=12, fill=_CARD, outline=_GRID)
        draw.rounded_rectangle((margin, y, margin + 14, y + card_height), radius=6, fill=accent)
        _draw_text(
            draw,
            (margin + card_padding, y + 14),
            _weather_mark(max_probability, None),
            large_emoji_font,
            _TEXT,
            embedded_color=True,
        )
        draw.text((margin + card_padding + 44, y + 20), format_date_heading(target_date), font=date_font, fill=_TEXT)
        draw.text(
            (width - margin - 136, y + 18),
            f"最大{max_probability}%",
            font=probability_font,
            fill=accent,
        )

        column_y = y + 76
        for source_index, source in enumerate(sources):
            column_x = margin + card_padding + source_index * (column_width + column_gap)
            source_color = _SOURCE_COLORS[source_index % len(_SOURCE_COLORS)]
            draw.rounded_rectangle(
                (column_x, column_y, column_x + column_width, column_y + source_header_height),
                radius=8,
                fill="#f1f5fb",
                outline="#dbe3f0",
            )
            draw.rounded_rectangle(
                (column_x, column_y, column_x + 8, column_y + source_header_height),
                radius=4,
                fill=source_color,
            )
            draw.text((column_x + 18, column_y + 10), source.label, font=source_font, fill=_TEXT)
            row_y = column_y + source_header_height + 12
            daily_results = daily_groups[source_index]
            if not daily_results:
                draw.text((column_x + 18, row_y), "対象店舗なし", font=row_font, fill=_MUTED)
            for result in daily_results[:rows_per_source]:
                color = _probability_color(result.probability)
                mark = _weather_mark(result.probability, result.precipitation_mm)
                name = _fit_text(draw, result.store.name, row_font, column_width - 210)
                value = _format_result_value(result)
                _draw_text(draw, (column_x + 18, row_y - 2), mark, emoji_font, color, embedded_color=True)
                draw.text((column_x + 48, row_y), name, font=row_font, fill=_TEXT)
                draw.text((column_x + column_width - 118, row_y - 1), value, font=probability_font, fill=color)
                row_y += row_height
        y += card_height + card_gap

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return WeeklyImage(filename="weekly_forecast.png", content=buffer.getvalue())


def build_weekly_forecast_image(
    results: Sequence[DailyRainResult],
    *,
    title: str,
    min_probability: int = 0,
    top_n_per_day: int | None = 10,
) -> WeeklyImage:
    return build_weekly_sources_image(
        [SourceForecast(label="予報", results=results)],
        title=title,
        min_probability=min_probability,
        top_n_per_day=top_n_per_day,
    )
