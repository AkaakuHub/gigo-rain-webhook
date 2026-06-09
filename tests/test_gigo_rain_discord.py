from __future__ import annotations

import unittest

from scripts.gigo_rain_discord import RainResult, Store, build_message_lines, split_discord_messages


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


if __name__ == "__main__":
    unittest.main()
