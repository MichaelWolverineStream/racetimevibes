import math
import statistics
import unittest
from pathlib import Path

from scripts import ladder_rating


REPO_ROOT = Path(__file__).resolve().parents[1]
DONE, DNF = 0, 1


def record(entries, name="alttpr-ladder/race", mode=0, mode_name="open"):
    return {
        "r": name,
        "m": mode,
        "mode": mode_name,
        "d": "2026-09-17",
        "h": 20,
        "n": len(entries),
        "p": None,
        "e": entries,
    }


def finished(times):
    """Return entries for players 0, 1, ... finishing in the given seconds."""
    order = sorted(range(len(times)), key=lambda player: times[player])
    places = {player: place for place, player in enumerate(order, start=1)}
    return [(player, places[player], DONE, float(seconds)) for player, seconds in enumerate(times)]


def dnf(player):
    return (player, None, DNF, None)


def move(result, race_index, entry_index):
    return result.post[race_index][entry_index][0] - result.pre[race_index][entry_index][0]


def league(race_count=10, times=(3000, 3300, 3600, 3900, 4200)):
    """Five racers who all enter `race_count` races with the same order."""
    return [record(finished(times), f"alttpr-ladder/race-{index}") for index in range(race_count)]


class RatingUpdateTests(unittest.TestCase):
    def test_the_winner_gains_points_and_last_place_loses_skill(self):
        result = ladder_rating.rate([record(finished([3000, 3600, 4200]))], 3)
        self.assertGreater(move(result, 0, 0), 0)
        self.assertLess(move(result, 0, 2), 0)
        self.assertGreater(
            result.display(result.post[0][0]), result.display(result.pre[0][0])
        )

    def test_a_dnf_loses_to_every_finisher(self):
        result = ladder_rating.rate([record([*finished([3600, 3700, 3800]), dnf(3)])], 4)
        self.assertLess(move(result, 0, 3), 0)
        # Even the last finisher beat the DNF, so it does better than the DNF.
        self.assertGreater(move(result, 0, 2), move(result, 0, 3))

    def test_two_dnfs_leave_each_other_unchanged(self):
        both = ladder_rating.rate([record([dnf(0), dnf(1)])], 2)
        self.assertEqual(both.post[0], both.pre[0])
        with_finisher = ladder_rating.rate([record([(0, 1, DONE, 3000.0), dnf(1), dnf(2)])], 3)
        self.assertEqual(move(with_finisher, 0, 1), move(with_finisher, 0, 2))

    def test_equal_places_leave_each_other_unchanged(self):
        tied = [(0, 1, DONE, 3000.0), (1, 1, DONE, 3000.0)]
        result = ladder_rating.rate([record(tied)], 2)
        self.assertEqual(result.post[0], result.pre[0])

    def test_a_shared_win_never_costs_the_stronger_co_winner(self):
        # Player 0 first builds a big lead over player 1, then they tie for first ahead of player 2.
        history = [record([(0, 1, DONE, 3000.0), (1, 2, DONE, 3100.0)]) for _ in range(10)]
        tied = record([(0, 1, DONE, 3000.0), (1, 1, DONE, 3000.0), (2, 3, DONE, 3200.0)])
        result = ladder_rating.rate([*history, tied], 3)
        before, after = result.pre[-1][0], result.post[-1][0]
        self.assertGreaterEqual(result.display(after), result.display(before))

    def test_finish_seconds_have_no_effect(self):
        order = [(0, 1), (1, 2), (2, 3), (3, 4)]
        fast = [(p, place, DONE, 100.0 + place) for p, place in order]
        slow = [(p, place, DONE, 100.0 + 10_000 * place) for p, place in order]
        untimed = [(p, place, DONE, None) for p, place in order]
        results = [
            ladder_rating.rate([record(entries, "a"), record(entries, "b")], 4)
            for entries in (fast, slow, untimed)
        ]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_a_newcomer_shows_exactly_1000(self):
        result = ladder_rating.rate([record(finished([3000, 3600, 4200]))], 3)
        for state in result.pre[0]:
            self.assertEqual(result.display(state), 1000)
        self.assertEqual(result.params["base"], 1000)

    def test_a_newcomer_climbs_by_winning(self):
        races = [record(finished([3000, 3600]), f"race-{index}") for index in range(8)]
        result = ladder_rating.rate(races, 2)
        points = [result.display(post[0]) for post in result.post]
        self.assertEqual(points, sorted(points))
        self.assertGreater(points[-1], 1000)

    def test_sigma_never_rises_and_never_goes_below_the_floor(self):
        races = [*league(40), record([(0, 1, DONE, 1.0), dnf(1), dnf(2), (7, 2, DONE, 5.0)])]
        result = ladder_rating.rate(races, 8, sigma_floor=7.5)
        for pre, post in zip(result.pre, result.post):
            for (_m, before), (_m2, after) in zip(pre, post):
                self.assertLessEqual(after, before)
                self.assertGreaterEqual(after, 7.5**2 - 1e-12)
        self.assertEqual(result.players[0].sigma, 7.5)

    def test_recency_a_floored_racer_still_moves(self):
        result = ladder_rating.rate([*league(60), record(finished([5000, 3300, 3600, 3900, 4200]))], 5)
        self.assertLess(move(result, 60, 0), -1)

    def test_a_win_is_worth_more_to_a_newcomer_than_to_a_veteran(self):
        veteran = ladder_rating.rate([*league(40), record(finished([4300, 3300, 3600, 3900, 4200]))], 5)
        newcomer = ladder_rating.rate([record(finished([4300, 3300, 3600, 3900, 4200]))], 5)
        self.assertGreater(move(newcomer, 0, 1), move(veteran, 40, 1))

    def test_a_race_with_one_finisher_among_non_finishers_is_decisive(self):
        result = ladder_rating.rate([record([(0, 1, DONE, 3000.0), dnf(1)])], 2)
        self.assertGreater(move(result, 0, 0), 0)


class RatingResultTests(unittest.TestCase):
    def test_win_chances_are_complementary_and_favour_the_higher_mean(self):
        result = ladder_rating.rate(league(10), 5)
        a, b = (30.0, 20.0), (22.0, 40.0)
        self.assertAlmostEqual(result.win_chance(a, b) + result.win_chance(b, a), 1.0, places=12)
        self.assertGreater(result.win_chance(a, b), 0.5)
        self.assertEqual(result.win_chance(a, a), 0.5)

    def test_params_carry_the_schema_keys(self):
        params = ladder_rating.rate(league(1), 5).params
        self.assertEqual(
            set(params),
            {"model", "base", "scale", "k", "mu0", "beta", "sigma0", "sigma_floor", "kappa",
             "provisional_finishes"},
        )
        self.assertEqual(params["model"], "bradley-terry")
        self.assertAlmostEqual(params["k"], 3.125)
        self.assertAlmostEqual(params["scale"], 9.7111, places=3)

    def test_a_rating_is_provisional_below_five_finishes(self):
        result = ladder_rating.rate(league(4), 5)
        self.assertTrue(all(player.provisional and player.finishes == 4 for player in result.players))
        result = ladder_rating.rate(league(5), 5)
        self.assertTrue(all(not player.provisional for player in result.players))

    def test_players_who_never_raced_have_no_rating(self):
        result = ladder_rating.rate(league(2), 6)
        self.assertIsNone(result.players[5])

    def test_published_mu_and_sigma_are_rounded_to_four_places(self):
        player = ladder_rating.rate(league(7), 5).players[0]
        self.assertEqual(player.mu, round(player.mu, 4))
        self.assertEqual(player.sigma, round(player.sigma, 4))

    def test_output_is_deterministic(self):
        races = [*league(6), record([*finished([3000, 3100]), dnf(2)])]
        self.assertEqual(ladder_rating.rate(races, 5), ladder_rating.rate(races, 5))


class ModeRatingTests(unittest.TestCase):
    def two_modes(self):
        """Players 0-2 race mode 0 six times, then mode 1 twice; player 3 only races mode 1."""
        races = [
            record(finished([3000, 3300, 3600]), f"alttpr-ladder/open-{index}")
            for index in range(6)
        ]
        races += [
            record(
                finished([3600, 3300, 3000, 3900]),
                f"alttpr-ladder/entrance-{index}",
                mode=1,
                mode_name="entrance",
            )
            for index in range(2)
        ]
        return races

    def test_each_mode_matches_rating_its_races_alone(self):
        races = self.two_modes()
        by_mode = ladder_rating.rate_by_mode(races, 4)
        for mode in (0, 1):
            indexes = [index for index, race in enumerate(races) if race["m"] == mode]
            alone = ladder_rating.rate([races[index] for index in indexes], 4)
            self.assertEqual(by_mode.players[mode], alone.players)
            self.assertEqual([by_mode.post[index] for index in indexes], alone.post)
            self.assertEqual([by_mode.pre[index] for index in indexes], alone.pre)
        self.assertEqual(by_mode.params, ladder_rating.rate(races, 4).params)

    def test_a_race_in_one_mode_leaves_another_mode_untouched(self):
        races = self.two_modes()
        before = ladder_rating.rate_by_mode(races[:6], 4)
        after = ladder_rating.rate_by_mode(races, 4)
        self.assertEqual(list(before.players), [0])
        self.assertEqual(before.players[0], after.players[0])

    def test_a_veteran_of_one_mode_debuts_at_1000_in_another(self):
        races = self.two_modes()
        by_mode = ladder_rating.rate_by_mode(races, 4)
        self.assertNotEqual(by_mode.display(by_mode.post[5][0]), 1000)
        for position in range(3):
            self.assertEqual(by_mode.display(by_mode.pre[6][position]), 1000)

    def test_provisional_is_per_mode(self):
        by_mode = ladder_rating.rate_by_mode(self.two_modes(), 4)
        self.assertFalse(by_mode.players[0][0].provisional)
        self.assertEqual(by_mode.players[0][0].finishes, 6)
        self.assertTrue(by_mode.players[1][0].provisional)
        self.assertEqual(by_mode.players[1][0].finishes, 2)

    def test_a_racer_has_no_rating_in_a_mode_they_never_raced(self):
        by_mode = ladder_rating.rate_by_mode(self.two_modes(), 4)
        self.assertIsNone(by_mode.players[0][3])
        self.assertIsNotNone(by_mode.players[1][3])


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.player_count, cls.scores = ladder_rating._backtest_records(
            REPO_ROOT / "archive"
        )
        cls.result = ladder_rating.rate(cls.records, cls.player_count)
        cls.mode_result = ladder_rating.rate_by_mode(cls.records, cls.player_count)
        cls.report = ladder_rating.backtest(REPO_ROOT / "archive")

    def test_no_winner_ever_loses_points(self):
        self.assertGreater(self.report.winners, 3000)
        self.assertEqual(self.report.winners_lost_points, 0)
        for race, pre, post in zip(self.records, self.result.pre, self.result.post):
            for (_player, place, status, _seconds), before, after in zip(race["e"], pre, post):
                if place == 1 and status == DONE:
                    self.assertGreaterEqual(
                        self.result.display(after), self.result.display(before), race["r"]
                    )

    def test_no_winner_ever_loses_mode_points(self):
        self.assertEqual(self.report.mode_winners, self.report.winners)
        self.assertEqual(self.report.mode_winners_lost_points, 0)
        result = self.mode_result
        for race, pre, post in zip(self.records, result.pre, result.post):
            for (_player, place, status, _seconds), before, after in zip(race["e"], pre, post):
                if place == 1 and status == DONE:
                    self.assertGreaterEqual(
                        result.display(after), result.display(before), race["r"]
                    )

    def test_every_mode_debut_is_1000(self):
        seen = set()
        for race, pre in zip(self.records, self.mode_result.pre):
            for (player, *_rest), state in zip(race["e"], pre):
                if (player, race["m"]) not in seen:
                    seen.add((player, race["m"]))
                    self.assertEqual(self.mode_result.display(state), 1000)

    def test_every_newcomer_debuts_at_1000(self):
        seen = set()
        for race, pre in zip(self.records, self.result.pre):
            for (player, *_rest), state in zip(race["e"], pre):
                if player not in seen:
                    seen.add(player)
                    self.assertEqual(self.result.display(state), 1000)

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
