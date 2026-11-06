import math
import statistics
import unittest
from pathlib import Path

from scripts import ladder_rating


REPO_ROOT = Path(__file__).resolve().parents[1]
DONE, DNF = 0, 1

# One update from these (mu, sigma) pre-states, listed in finishing order, by openskill 6.2.0's
# BradleyTerryFull with beta 12, kappa 1e-4 and tau 0: (mu, sigma^2) after the race. Generated
# outside the repository; the same values come from tests/test_rating_lab.py's golden races.
GOLDEN_RACES = [
    [(25.0, 8.0), (25.0, 8.0)],
    [(31.5, 5.2), (40.0, 4.5), (18.0, 7.9)],
    [(22.0, 6.0), (35.0, 4.2), (28.0, 7.0), (10.0, 5.5), (45.0, 4.8)],
    [(25.0, 4.5), (22.0, 4.9), (31.0, 5.3), (16.0, 5.7), (37.0, 6.1), (10.0, 6.5), (43.0, 6.9), (4.0, 7.300000000000001), (49.0, 7.7)],
    [(3.5, 5.0), (6.600000000000001, 5.199999999999999), (9.699999999999996, 5.4), (12.799999999999997, 5.6), (15.899999999999999, 5.8), (19.0, 6.0), (22.099999999999998, 6.2), (25.2, 6.4), (28.3, 6.6), (31.4, 6.8), (34.5, 7.0), (37.6, 7.2), (40.7, 7.4), (43.8, 7.6), (46.9, 7.8), (50.0, 8.0)],
    [(0.0, 6.5), (60.0, 4.1), (30.0, 5.0), (-10.0, 7.5), (75.0, 4.3)],
]
GOLDEN_BRADLEY_TERRY_FULL = [
    [(26.568929081105473, 63.03450518085817), (23.431070918894527, 63.03450518085817)],
    [(32.87011908791643, 26.778079777992346), (39.57531961092996, 20.13135695141036), (16.146534492525067, 60.68749731194608)],
    [(26.53683064940736, 34.99002672790589), (35.557211676994946, 17.461610453157537), (28.026414530616762, 46.83371773696584), (10.087966515476616, 29.711485510180843), (41.28922153315085, 22.749325210667013)],
    [(29.48007962877167, 19.756743829930496), (26.377571359269332, 23.270511892870694), (33.15316272543612, 27.01542427182095), (19.425134141052055, 31.028998708172963), (34.70551410692683, 35.21268721227322), (11.234179994991344, 39.74434270325757), (33.84670010679288, 44.3392160636341), (1.511742425967891, 49.457886881296744), (30.369041216728547, 54.46110292978387)],
    [(18.643722044409138, 23.826130915295877), (20.78669302659439, 25.5225184430325), (22.611753720557154, 27.23451374374872), (24.101526701458678, 28.960017293678373), (25.24126285971751, 30.699333209610987), (26.01906800806335, 32.45549868155635), (26.426025626452457, 34.23448412671785), (26.45621203851286, 36.04524078971757), (26.106605401734008, 37.8995775320407), (25.376893887492898, 39.81185482553355), (24.26919193542492, 41.798493670781305), (22.787677107346326, 43.87731177897722), (20.938164124317126, 46.06671854982526), (18.727636918727864, 48.38482149377), (16.163763261537294, 50.848515199926126), (13.254418765960303, 53.47263450059052)],
    [(7.0726490708841805, 41.55114236097477), (59.954741338588484, 16.731167253532114), (30.08699404209417, 24.78338024094367), (-8.551824473284668, 55.06647698371106), (71.41418406179584, 18.412802533339665)],
]


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

    def test_without_damping_the_update_matches_the_openskill_reference(self):
        for race, expected in zip(GOLDEN_RACES, GOLDEN_BRADLEY_TERRY_FULL):
            states = [[mu, sigma * sigma] for mu, sigma in race]
            entries = [(player, place, DONE, None) for place, player in enumerate(range(len(race)), start=1)]
            ladder_rating._update_race(
                states, entries, beta=12.0, sigma_floor=0.0, kappa=ladder_rating.KAPPA, alpha=0.0
            )
            for (mean, variance), (golden_mean, golden_variance) in zip(states, expected):
                self.assertAlmostEqual(mean, golden_mean, places=9)
                self.assertAlmostEqual(variance, golden_variance, places=9)

    def test_damping_scales_a_race_by_its_field_size(self):
        for size in (2, 5, 17):
            entries = finished([3000 + 60 * place for place in range(size)])
            start = [[25.0, 64.0] for _ in range(size)]
            plain = [list(state) for state in start]
            damped = [list(state) for state in start]
            common = {"beta": ladder_rating.BETA, "sigma_floor": 0.0, "kappa": ladder_rating.KAPPA}
            ladder_rating._update_race(plain, entries, alpha=0.0, **common)
            ladder_rating._update_race(damped, entries, alpha=ladder_rating.ALPHA, **common)
            scale = (size - 1) ** -ladder_rating.ALPHA
            for before, full, scaled in zip(start, plain, damped):
                self.assertAlmostEqual(scaled[0] - before[0], scale * (full[0] - before[0]), places=12)
                self.assertAlmostEqual(
                    1 - scaled[1] / before[1], scale * (1 - full[1] / before[1]), places=12
                )

    def test_the_published_rating_is_damped(self):
        entries = finished([3000, 3300, 3600, 3900, 4200])
        damped = ladder_rating.rate([record(entries)], 5)
        undamped = ladder_rating.rate([record(entries)], 5, alpha=0.0)
        self.assertAlmostEqual(move(damped, 0, 0), move(undamped, 0, 0) / 2, places=12)


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
            {"model", "base", "scale", "k", "mu0", "beta", "sigma0", "sigma_floor", "kappa", "alpha",
             "provisional_finishes"},
        )
        self.assertEqual(params["model"], "bradley-terry")
        self.assertEqual(params["alpha"], 0.5)
        self.assertAlmostEqual(params["k"], 25 / 12)
        self.assertAlmostEqual(params["scale"], 13.7336, places=3)

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


class SeasonRatingTests(unittest.TestCase):
    def two_seasons(self):
        """Season 1: players 0-2 race mode 0 six times. A race outside every season.
        Season 2: the same three plus player 3, twice in mode 0 and twice in mode 1."""
        races = [
            record(finished([3000, 3300, 3600]), f"alttpr-ladder/one-{index}")
            for index in range(6)
        ]
        races.append(record(finished([3600, 3300, 3000]), "alttpr-ladder/break"))
        races += [
            record(
                finished([3600, 3300, 3000, 3900]),
                f"alttpr-ladder/two-{index}",
                mode=index % 2,
                mode_name=("open", "entrance")[index % 2],
            )
            for index in range(4)
        ]
        seasons = [1] * 6 + [None] + [2] * 4
        return races, seasons

    def test_each_season_matches_rating_its_races_alone(self):
        races, seasons = self.two_seasons()
        result = ladder_rating.rate_by_season(races, 4, seasons)
        for season in (1, 2):
            indexes = [index for index, number in enumerate(seasons) if number == season]
            chosen = [races[index] for index in indexes]
            alone = ladder_rating.rate(chosen, 4)
            by_mode = ladder_rating.rate_by_mode(chosen, 4)
            self.assertEqual([result.pre[index] for index in indexes], alone.pre)
            self.assertEqual([result.post[index] for index in indexes], alone.post)
            self.assertEqual([result.mode_pre[index] for index in indexes], by_mode.pre)
            self.assertEqual([result.mode_post[index] for index in indexes], by_mode.post)
        self.assertEqual(result.params, ladder_rating.rate(races, 4).params)

    def test_a_race_outside_every_season_has_no_season_state(self):
        races, seasons = self.two_seasons()
        result = ladder_rating.rate_by_season(races, 4, seasons)
        for states in (result.pre, result.post, result.mode_pre, result.mode_post):
            self.assertEqual(states[6], [])

    def test_a_race_in_one_season_leaves_another_untouched(self):
        races, seasons = self.two_seasons()
        before = ladder_rating.rate_by_season(races[:7], 4, seasons[:7])
        after = ladder_rating.rate_by_season(races, 4, seasons)
        self.assertEqual(before.post[:7], after.post[:7])
        self.assertEqual(before.mode_post[:7], after.mode_post[:7])

    def test_everyone_debuts_at_1000_every_season_and_in_every_mode_of_it(self):
        races, seasons = self.two_seasons()
        result = ladder_rating.rate_by_season(races, 4, seasons)
        self.assertNotEqual(result.display(result.post[5][0]), 1000)
        for position in range(4):
            self.assertEqual(result.display(result.pre[7][position]), 1000)
            self.assertEqual(result.display(result.mode_pre[7][position]), 1000)
            self.assertEqual(result.display(result.mode_pre[8][position]), 1000)
        self.assertNotEqual(result.display(result.pre[8][0]), 1000)

    def test_the_seasons_must_line_up_with_the_records(self):
        races, seasons = self.two_seasons()
        with self.assertRaisesRegex(ValueError, "season"):
            ladder_rating.rate_by_season(races, 4, seasons[:-1])


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
