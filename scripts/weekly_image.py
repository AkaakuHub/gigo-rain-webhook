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
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
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


def _probability_color(probability: int) -> str:
    if probability >= 70:
        return _HIGH
    if probability >= 40:
        return _MIDDLE
    return _LOW


def _result_color(result: DailyRainResult) -> str:
    if result.probability >= 70 or (result.precipitation_mm is not None and result.precipitation_mm >= 20):
        return _HIGH
    if result.probability >= 40 or (result.precipitation_mm is not None and result.precipitation_mm >= 1):
        return _MIDDLE
    return _LOW


def _weather_mark(probability: int, precipitation_mm: float | None) -> str:
    if probability >= 70 or (precipitation_mm is not None and precipitation_mm >= 20):
        return "☔"
    if probability >= 40 or (precipitation_mm is not None and precipitation_mm >= 1):
        return "🌧"
    return "☁️"


def _draw_resized_emoji(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    *,
    size: int,
    emoji: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    canvas_size = max(size * 6, 192)
    emoji_image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    emoji_draw = ImageDraw.Draw(emoji_image)
    try:
        emoji_draw.text((canvas_size // 3, canvas_size // 3), emoji, font=font, embedded_color=True)
    except TypeError:
        emoji_draw.text((canvas_size // 3, canvas_size // 3), emoji, font=font)
    bbox = emoji_image.getbbox()
    if bbox is None:
        return
    cropped = emoji_image.crop(bbox)
    width, height = cropped.size
    ratio = min(size / width, size / height)
    resized_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    resized = cropped.resize(resized_size, Image.Resampling.LANCZOS)
    target = draw._image
    target.paste(resized, (x + (size - resized_size[0]) // 2, y + (size - resized_size[1]) // 2), resized)


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


def _draw_right_aligned(
    draw: ImageDraw.ImageDraw,
    right_x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
) -> None:
    draw.text((right_x - _measure_width(draw, text, font), y), text, font=font, fill=fill)


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
    if result.source.startswith("Open-Meteo") and result.probability == 0:
        return f"{result.precipitation_mm or 0:g}mm"
    if result.precipitation_mm is not None and result.probability == 0:
        return f"{result.precipitation_mm:g}mm"
    if result.precipitation_mm is not None:
        return f"{result.probability}% {result.precipitation_mm:g}mm"
    return f"{result.probability}%"


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
    title_font = _load_font(36, bold=True)
    date_font = _load_font(28, bold=True)
    source_font = _load_font(22, bold=True)
    row_font = _load_font(18)
    prefecture_font = _load_font(16, bold=True)
    value_font = _load_font(21, bold=True)
    emoji_font = _load_emoji_font(64)
    large_emoji_font = _load_emoji_font(96)

    width = 1800
    margin = 28
    card_gap = 14
    card_padding = 18
    row_height = 36
    source_header_height = 38
    card_width = width - margin * 2
    column_gap = 12
    columns = max(1, len(sources))
    column_width = int((card_width - card_padding * 2 - column_gap * (columns - 1)) / columns)
    rows_per_source = min(top_n_per_day or 10, 10)
    card_height = 70 + source_header_height + rows_per_source * row_height + card_padding
    content_height = 78 + len(target_dates) * (card_height + card_gap)
    height = max(360, content_height + margin)

    image = Image.new("RGB", (width, height), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    display_title = _fit_text(draw, title.strip("【】"), title_font, width - margin * 2)
    draw.text((margin, 22), display_title, font=title_font, fill=_TEXT)
    y = 78
    if not target_dates:
        draw.rounded_rectangle((margin, y, width - margin, y + 120), radius=12, fill=_CARD, outline=_GRID)
        draw.text((margin + card_padding, y + card_padding), "対象店舗なし", font=date_font, fill=_TEXT)
    for target_date in target_dates:
        daily_groups = [grouped.get(target_date, []) for grouped in grouped_by_source]
        max_probability = max((result.probability for daily in daily_groups for result in daily), default=0)
        accent = _probability_color(max_probability)
        draw.rounded_rectangle((margin, y, width - margin, y + card_height), radius=12, fill=_CARD, outline=_GRID)
        draw.rounded_rectangle((margin, y, margin + 14, y + card_height), radius=6, fill=accent)
        _draw_resized_emoji(
            draw,
            margin + card_padding,
            y + 9,
            size=40,
            emoji=_weather_mark(max_probability, None),
            font=large_emoji_font,
        )
        draw.text((margin + card_padding + 52, y + 16), format_date_heading(target_date), font=date_font, fill=_TEXT)
        draw.text(
            (width - margin - 142, y + 16),
            f"最大{max_probability}%",
            font=value_font,
            fill=accent,
        )

        column_y = y + 62
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
            draw.text((column_x + 18, column_y + 7), source.label, font=source_font, fill=_TEXT)
            row_y = column_y + source_header_height + 8
            daily_results = daily_groups[source_index]
            if not daily_results:
                draw.text((column_x + 18, row_y), "対象店舗なし", font=row_font, fill=_MUTED)
            for result in daily_results[:rows_per_source]:
                color = _result_color(result)
                value = _format_result_value(result)
                _draw_resized_emoji(
                    draw,
                    column_x + 14,
                    row_y - 5,
                    size=28,
                    emoji=_weather_mark(result.probability, result.precipitation_mm),
                    font=emoji_font,
                )
                prefecture_x = column_x + 52
                draw.rounded_rectangle(
                    (prefecture_x, row_y + 1, prefecture_x + 88, row_y + 27),
                    radius=6,
                    fill="#edf2f7",
                    outline="#dbe3f0",
                )
                prefecture = _fit_text(draw, result.store.prefecture, prefecture_font, 80)
                draw.text((prefecture_x + 7, row_y + 4), prefecture, font=prefecture_font, fill=_MUTED)
                name_x = column_x + 152
                value_right_x = column_x + column_width - 16
                value_width = max(96, _measure_width(draw, value, value_font))
                name = _fit_text(draw, result.store.name, row_font, value_right_x - value_width - name_x - 12)
                draw.text((name_x, row_y + 2), name, font=row_font, fill=_TEXT)
                _draw_right_aligned(draw, value_right_x, row_y, value, value_font, color)
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
