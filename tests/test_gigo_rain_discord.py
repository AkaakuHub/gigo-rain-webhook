from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from scripts.discord_message import build_message_lines, split_discord_messages
from scripts.geocoding import attach_coordinates, write_geocode_cache
from scripts.gigo_models import RainResult, Store, StoreRecord


def store(name: str, prefecture: str = "東京都") -> Store:
    return Store(
        store_id=name,
        name=name,
        prefecture=prefecture,
        address="東京都調布市小島町1-1-1",
        latitude=35.651,
        longitude=139.544,
        source_url="https://www.gigo.co.jp/shops/example",
    )


class NotifyMessageTest(unittest.TestCase):
    def test_message_lines_are_sorted_by_probability_descending(self) -> None:
        results = [
            RainResult(store("GiGO町田"), 40),
            RainResult(store("GiGO調布"), 75),
            RainResult(store("GiGO府中"), 75),
        ]
        self.assertEqual(
            build_message_lines(results),
            [
                "GiGO府中/東京都 75%",
                "GiGO調布/東京都 75%",
                "GiGO町田/東京都 40%",
            ],
        )

    def test_message_lines_apply_min_probability_and_top_n(self) -> None:
        results = [
            RainResult(store("GiGO A"), 10),
            RainResult(store("GiGO B"), 30),
            RainResult(store("GiGO C"), 60),
        ]
        self.assertEqual(build_message_lines(results, min_probability=20, top_n=1), ["GiGO C/東京都 60%"])

    def test_split_discord_messages_keeps_plain_line_format(self) -> None:
        lines = ["GiGO調布/東京都 75%", "GiGO町田/東京都 40%"]
        self.assertEqual(split_discord_messages(lines, limit=2000), ["GiGO調布/東京都 75%\nGiGO町田/東京都 40%"])

    def test_split_discord_messages_chunks_when_limit_is_exceeded(self) -> None:
        lines = ["GiGO調布/東京都 75%", "GiGO町田/東京都 40%", "GiGO府中/東京都 20%"]
        self.assertEqual(
            split_discord_messages(lines, limit=25),
            [
                "GiGO調布/東京都 75%",
                "GiGO町田/東京都 40%",
                "GiGO府中/東京都 20%",
            ],
        )


class StoreCoordinateTest(unittest.TestCase):
    def test_attach_coordinates_reuses_geocode_cache_by_address(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "geocodes.csv"
            current_csv = Path(temp_dir) / "stores.csv"
            write_geocode_cache(cache_path, {"東京都調布市小島町1-1-1": (35.651, 139.544)})
            record = StoreRecord(
                store_id="chofu",
                name="GiGO調布",
                prefecture="東京都",
                address="東京都調布市小島町1-1-1",
                source_url="https://www.gigo.co.jp/shops/chofu",
            )

            with patch("scripts.geocoding.geocode_address") as geocode_address, redirect_stdout(StringIO()):
                records = attach_coordinates(
                    requests.Session(),
                    [record],
                    current_csv=current_csv,
                    geocode_cache_path=cache_path,
                    force_geocode=False,
                    allow_missing_coordinates=False,
                    sleep_seconds=0,
                )

            geocode_address.assert_not_called()
            self.assertEqual(records[0].latitude, 35.651)
            self.assertEqual(records[0].longitude, 139.544)


if __name__ == "__main__":
    unittest.main()
