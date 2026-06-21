from __future__ import annotations

import unittest
from datetime import date

from scripts.discord_message import build_weekly_message_lines, split_discord_messages
from scripts.gigo_rain_discord import _source_values, _weekly_output_format
from scripts.gigo_models import DailyRainResult, Store
from scripts.jma_weekly import extract_jma_weekly_probabilities
from scripts.weather import _open_meteo_daily_results_from_payload, default_previous_sunday_run, resolve_forecast_start_date
from scripts.weekly_image import build_weekly_forecast_image
from scripts.wn_prefecture import extract_wnews_weekly_probabilities


class WeeklyForecastTests(unittest.TestCase):
    def test_resolve_forecast_start_date_accepts_any_date(self) -> None:
        self.assertEqual(resolve_forecast_start_date("2026-06-08"), date(2026, 6, 8))
        self.assertEqual(resolve_forecast_start_date("2026-06-09"), date(2026, 6, 9))
        self.assertEqual(resolve_forecast_start_date("", now=date(2026, 6, 9)), date(2026, 6, 9))

    def test_default_previous_sunday_run(self) -> None:
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 8), run_hour_utc=0), "2026-06-07T00:00")
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 9), run_hour_utc=0), "2026-06-07T00:00")
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 8), run_hour_utc=6), "2026-06-07T06:00")

    def test_weekly_output_format_defaults_to_image(self) -> None:
        self.assertEqual(_weekly_output_format(""), "image")
        self.assertEqual(_weekly_output_format("png"), "image")
        self.assertEqual(_weekly_output_format("text"), "text")

    def test_all_source_uses_open_meteo_forecast(self) -> None:
        self.assertEqual(_source_values("all"), ["jma_weekly", "open_meteo_forecast", "weathernews_prefecture"])
        self.assertEqual(_source_values("open_meteo"), ["open_meteo_forecast"])
        self.assertEqual(_source_values("open_meteo_single_run"), ["open_meteo_single_run"])

    def test_open_meteo_daily_results_from_payload(self) -> None:
        store = Store("chofu", "GiGO調布", "東京都", "東京都調布市", 35.65, 139.54, "https://example.test/chofu")
        payload = {
            "daily": {
                "time": ["2026-06-21", "2026-06-22"],
                "precipitation_probability_max": [84, None],
                "precipitation_sum": [6.3, 0.0],
            }
        }
        actual = _open_meteo_daily_results_from_payload(
            store,
            payload,
            days=[date(2026, 6, 21), date(2026, 6, 22)],
            source="Open-Meteo Forecast",
        )
        self.assertEqual(actual[0].probability, 84)
        self.assertEqual(actual[0].precipitation_mm, 6.3)
        self.assertEqual(actual[1].probability, 0)
        self.assertEqual(actual[1].precipitation_mm, 0.0)

    def test_extract_jma_weekly_probabilities(self) -> None:
        payload = [
            {
                "timeSeries": [
                    {
                        "timeDefines": [
                            "2026-06-08T00:00:00+09:00",
                            "2026-06-09T00:00:00+09:00",
                            "2026-06-10T00:00:00+09:00",
                        ],
                        "areas": [
                            {
                                "area": {"name": "東京地方", "code": "130010"},
                                "pops": ["70", "40", ""],
                            }
                        ],
                    }
                ]
            }
        ]
        actual = extract_jma_weekly_probabilities(payload, week_start=date(2026, 6, 8), week_days=3)
        self.assertEqual(actual[date(2026, 6, 8)], 70)
        self.assertEqual(actual[date(2026, 6, 9)], 40)
        self.assertEqual(actual[date(2026, 6, 10)], 0)

    def test_extract_wnews_current_week_probabilities(self) -> None:
        html = """
        <ul id="wx__week0" class="wxweek_content">
          <li class="date sun"><p class="day">21</p><p class="dow">(日)</p></li>
          <li class="rain"><p>80<span>%</span></p></li>
        </ul>
        <ul id="wx__week1" class="wxweek_content">
          <li class="date"><p class="day">22</p><p class="dow">(月)</p></li>
          <li class="rain"><p>40<span>%</span></p></li>
        </ul>
        <ul id="wx__week2" class="wxweek_content">
          <li class="date"><p class="day">23</p><p class="dow">(火)</p></li>
          <li class="rain"><p>60<span>%</span></p></li>
        </ul>
        """
        actual = extract_wnews_weekly_probabilities(
            html,
            week_start=date(2026, 6, 21),
            week_days=3,
            base_date=date(2026, 6, 21),
        )
        self.assertEqual(actual[date(2026, 6, 21)], 80)
        self.assertEqual(actual[date(2026, 6, 22)], 40)
        self.assertEqual(actual[date(2026, 6, 23)], 60)

    def test_extract_wnews_current_week_probabilities_handles_month_boundary(self) -> None:
        html = """
        <ul id="wx__week0" class="wxweek_content">
          <li class="date"><p class="day">30</p></li>
          <li class="rain"><p>20<span>%</span></p></li>
        </ul>
        <ul id="wx__week1" class="wxweek_content">
          <li class="date"><p class="day">1</p></li>
          <li class="rain"><p>70<span>%</span></p></li>
        </ul>
        """
        actual = extract_wnews_weekly_probabilities(
            html,
            week_start=date(2026, 6, 30),
            week_days=2,
            base_date=date(2026, 6, 30),
        )
        self.assertEqual(actual[date(2026, 6, 30)], 20)
        self.assertEqual(actual[date(2026, 7, 1)], 70)

    def test_weekly_message_lines(self) -> None:
        store1 = Store("chofu", "GiGO調布", "東京都", "東京都調布市", 35.65, 139.54, "https://example.test/chofu")
        store2 = Store("machida", "GiGO町田", "東京都", "東京都町田市", 35.54, 139.45, "https://example.test/machida")
        lines = build_weekly_message_lines(
            [
                DailyRainResult(store1, date(2026, 6, 8), 75, "test"),
                DailyRainResult(store2, date(2026, 6, 8), 40, "test", precipitation_mm=1.5),
                DailyRainResult(store1, date(2026, 6, 9), 20, "test"),
            ],
            title="title",
            top_n_per_day=2,
        )
        text = "\n".join(lines)
        self.assertIn("2026-06-08(月)", text)
        self.assertIn("GiGO調布/東京都 75%", text)
        self.assertIn("GiGO町田/東京都 40% / 1.5mm", text)
        self.assertIn("2026-06-09(火)", text)
        self.assertEqual(len(split_discord_messages(lines, limit=1900)), 1)

    def test_weekly_forecast_image_is_png(self) -> None:
        store = Store("chofu", "GiGO調布", "東京都", "東京都調布市", 35.65, 139.54, "https://example.test/chofu")
        image = build_weekly_forecast_image(
            [DailyRainResult(store, date(2026, 6, 8), 75, "test")],
            title="title",
            top_n_per_day=1,
        )
        self.assertEqual(image.filename, "weekly_forecast.png")
        self.assertTrue(image.content.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
