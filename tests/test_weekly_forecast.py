from __future__ import annotations

import unittest
from datetime import date

from scripts.discord_message import build_weekly_message_lines, split_discord_messages
from scripts.gigo_models import DailyRainResult, Store
from scripts.jma_weekly import extract_jma_weekly_probabilities
from scripts.weather import default_previous_sunday_run, resolve_forecast_start_date


class WeeklyForecastTests(unittest.TestCase):
    def test_resolve_forecast_start_date_accepts_any_date(self) -> None:
        self.assertEqual(resolve_forecast_start_date("2026-06-08"), date(2026, 6, 8))
        self.assertEqual(resolve_forecast_start_date("2026-06-09"), date(2026, 6, 9))
        self.assertEqual(resolve_forecast_start_date("", now=date(2026, 6, 9)), date(2026, 6, 9))

    def test_default_previous_sunday_run(self) -> None:
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 8), run_hour_utc=0), "2026-06-07T00:00")
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 9), run_hour_utc=0), "2026-06-07T00:00")
        self.assertEqual(default_previous_sunday_run(date(2026, 6, 8), run_hour_utc=6), "2026-06-07T06:00")

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


if __name__ == "__main__":
    unittest.main()
