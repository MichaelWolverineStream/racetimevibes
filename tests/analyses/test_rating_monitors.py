import math
import statistics
import unittest
from pathlib import Path

from scripts import ladder_rating


REPO_ROOT = Path(__file__).resolve().parents[2]


class RatingMonitorTests(unittest.TestCase):
    """Monitors on growing data: they alert weekly and never gate a deploy."""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.player_count, cls.scores = ladder_rating._backtest_records(
            REPO_ROOT / "archive"
        )
        cls.result = ladder_rating.rate(cls.records, cls.player_count)
        cls.report = ladder_rating.backtest(REPO_ROOT / "archive")

    def test_the_median_veteran_ends_above_their_first_race(self):
        first = {}
        count = {}
        for race, post in zip(self.records, self.result.post):
            for (player, *_rest), state in zip(race["e"], post):
                first.setdefault(player, self.result.display(state))
                count[player] = count.get(player, 0) + 1
        gains = [
            player_rating.rating - first[player]
            for player, player_rating in enumerate(self.result.players)
            if count.get(player, 0) >= 50
        ]
        self.assertGreater(len(gains), 10)
        self.assertGreater(statistics.median(gains), 0)

    def test_veterans_still_move(self):
        self.assertIsNotNone(self.report.veteran_move)
        self.assertGreater(self.report.veteran_move, 3)

    def test_beats_racetime_on_2026_pairs(self):
        ours, racetime = self.report.evaluation
        self.assertEqual(ours.pairs, racetime.pairs)
        self.assertGreater(ours.pairs, 40_000)
        self.assertGreater(ours.accuracy, racetime.accuracy)
        self.assertLess(ours.log_loss, racetime.log_loss)
        self.assertTrue(math.isfinite(ours.log_loss))


if __name__ == "__main__":
    unittest.main()
