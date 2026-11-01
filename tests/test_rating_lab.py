import dataclasses
import math
import random
import statistics
import unittest
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from unittest import mock

from scripts import ladder_rating, rating_lab


REPO_ROOT = Path(__file__).resolve().parents[1]
DONE, DNF = 0, 1


def record(entries, name="alttpr-ladder/race", mode=0, mode_name="open", day="2026-09-17"):
    return {
        "r": name,
        "m": mode,
        "mode": mode_name,
        "d": day,
        "h": 20,
        "n": len(entries),
        "p": None,
        "e": entries,
    }


def finished(order, start=0.0):
    """Return finisher entries for the players in `order`, first place first."""
    return [(player, place, DONE, start + 600.0 * place) for place, player in enumerate(order, start=1)]


def dnf(player):
    return (player, None, DNF, None)


def mixed_league(race_count=24, players=7, seed=3):
    """Races with shuffled fields of 2 to `players`, a non-finisher now and then, and two modes."""
    rng = random.Random(seed)
    races = []
    for index in range(race_count):
        field = rng.sample(range(players), rng.randint(2, players))
        entries = finished(field[:-1]) + [dnf(field[-1])] if index % 3 == 0 else finished(field)
        races.append(
            record(entries, f"alttpr-ladder/race-{index}", mode=index % 2, mode_name=("open", "keys")[index % 2])
        )
    return races


def dated_league(start="2025-04-20", end="2026-03-31", players=6, seed=5):
    """A daily ladder of `players` racers in random order, so veterans exist by the tuning folds."""
    rng = random.Random(seed)
    races = []
    day = date.fromisoformat(start)
    while day <= date.fromisoformat(end):
        field = rng.sample(range(players), players)
        entries = finished(field[:-1]) + [dnf(field[-1])]
        races.append(record(entries, f"alttpr-ladder/{day}", day=day.isoformat()))
        day += timedelta(days=1)
    return races


def probe_settings(family):
    grid = family.grid()
    return [grid[0], grid[len(grid) // 2], grid[-1]]


def score(pairs, loss):
    return rating_lab.RaceScore(pairs, loss, 0.0, 0.0)


class ReplayTests(unittest.TestCase):
    def test_the_shipped_setting_reproduces_ladder_rating(self):
        races = mixed_league()
        shipped = ladder_rating.rate(races, 7)
        lab = rating_lab.rate_shipped(races, 7)
        self.assertEqual(lab.pre, shipped.pre)
        self.assertEqual(lab.post, shipped.post)
        for race in lab.post:
            for state in race:
                self.assertEqual(lab.display(state), shipped.display(state))
                self.assertEqual(lab.win_chance(state, race[0]), shipped.win_chance(state, race[0]))

    def test_by_mode_reproduces_ladder_rating_by_mode(self):
        races = mixed_league()
        shipped = ladder_rating.rate_by_mode(races, 7)
        lab = rating_lab.by_mode(rating_lab.rate_bradley_terry_full, races, 7, **rating_lab.SHIPPED)
        self.assertEqual((lab.pre, lab.post), (shipped.pre, shipped.post))

    def test_alpha_zero_is_the_shipped_update_operation_for_operation(self):
        rng = random.Random(11)
        for size in (2, 3, 7, 16):
            entries = finished(range(size - 1)) + [dnf(size - 1)]
            states = [[rng.uniform(-20, 70), rng.uniform(16, 64)] for _ in range(size)]
            ours = [list(state) for state in states]
            rating_lab._bradley_terry_full(ours, entries, beta=12.0, sigma_floor=4.0, alpha=0.0)
            ladder_rating._update_race(states, entries, beta=12.0, sigma_floor=4.0, kappa=ladder_rating.KAPPA)
            self.assertEqual(ours, states)

    def test_damping_scales_the_update_by_the_field_size(self):
        entries = finished(range(5))
        start = [[25.0, 64.0] for _ in range(5)]
        plain = [list(state) for state in start]
        damped = [list(state) for state in start]
        rating_lab._bradley_terry_full(plain, entries, beta=12.0, sigma_floor=0.0, alpha=0.0)
        rating_lab._bradley_terry_full(damped, entries, beta=12.0, sigma_floor=0.0, alpha=1.0)
        for before, full, scaled in zip(start, plain, damped):
            self.assertAlmostEqual(scaled[0] - before[0], (full[0] - before[0]) / 4, places=12)

    def test_the_display_scale_follows_the_setting(self):
        display = rating_lab._weng_lin_display(beta=12.0, sigma0=8.0, sigma_floor=4.0)
        self.assertEqual(display((25.0, 64.0)), 1000)
        wide = rating_lab._weng_lin_display(beta=20.0, sigma0=8.0, sigma_floor=4.0)
        self.assertLess(wide((35.0, 16.0)) - wide((25.0, 16.0)), display((35.0, 16.0)) - display((25.0, 16.0)))

    def test_400_points_are_about_10_to_1_under_every_familys_own_win_chance(self):
        # Behavioural: shift an established racer's skill until they show 400 more
        # points, then ask the family's own win chance. The liveliness bar compares
        # displayed moves across families, so this must hold for every one of them.
        for name, family in rating_lab.FAMILIES.items():
            for params in probe_settings(family):
                with self.subTest(family=name, params=params):
                    result = family.rate([], 0, **params)
                    if "sigma_floor" in params:
                        base = (25.0, params["sigma_floor"] ** 2)
                    else:
                        base = (1000.0, 100)
                    low, high = 0.0, 10_000.0
                    for _ in range(80):
                        middle = (low + high) / 2
                        shifted = (base[0] + middle, base[1])
                        if result.display(shifted) - result.display(base) >= 400:
                            high = middle
                        else:
                            low = middle
                    chance = result.win_chance((base[0] + high, base[1]), base)
                    self.assertAlmostEqual(chance / (1 - chance), 10.0, delta=0.15)


class FamilyPropertyTests(unittest.TestCase):
    """Properties every registered family must have, at several of its grid settings."""

    def each_setting(self):
        for name, family in rating_lab.FAMILIES.items():
            for params in probe_settings(family):
                with self.subTest(family=name, params=params):
                    yield family, params

    def test_a_race_only_changes_its_own_and_later_states(self):
        races = mixed_league()
        changed = list(races)
        entries = races[5]["e"]
        reordered = [(player, len(entries) - place + 1 if place else place, status, seconds)
                     for player, place, status, seconds in entries]
        changed[5] = {**races[5], "e": reordered}
        for family, params in self.each_setting():
            result = family.rate(races, 7, **params)
            other = family.rate(changed, 7, **params)
            self.assertEqual(result.pre[:6], other.pre[:6])
            self.assertNotEqual(result.post[5], other.post[5])

    def test_newcomers_show_1000_and_winners_never_lose_points(self):
        races = mixed_league()
        for family, params in self.each_setting():
            result = family.rate(races, 7, **params)
            rules = rating_lab.check_rules(races, result, lambda variant: family.rate(variant, 7, **params))
            self.assertTrue(rules.kept, rules)
            mode_result = rating_lab.by_mode(family.rate, races, 7, **params)
            mode_rules = rating_lab.check_rules(
                races,
                mode_result,
                lambda variant: rating_lab.by_mode(family.rate, variant, 7, **params),
                rating_lab._by_mode_racer,
            )
            self.assertTrue(mode_rules.kept, mode_rules)

    def test_win_chances_are_complementary_and_runs_deterministic(self):
        races = mixed_league()
        for family, params in self.each_setting():
            result = family.rate(races, 7, **params)
            again = family.rate(races, 7, **params)
            self.assertEqual((result.pre, result.post), (again.pre, again.post))
            a, b = result.post[-1][0], result.post[-1][1]
            self.assertAlmostEqual(result.win_chance(a, b) + result.win_chance(b, a), 1.0, places=12)
            self.assertEqual(result.win_chance(a, a), 0.5)

    def test_a_mode_rating_never_reads_another_mode(self):
        races = mixed_league()
        only_open = [race for race in races if race["m"] == 0]
        for family, params in self.each_setting():
            both = rating_lab.by_mode(family.rate, races, 7, **params)
            alone = family.rate(only_open, 7, **params)
            self.assertEqual([both.pre[i] for i, race in enumerate(races) if race["m"] == 0], alone.pre)


# Reference values from OpenSkill 6.2.0, one update per race from these pre-states, in finishing
# order as (mu, sigma): beta 12, kappa 1e-4, tau 0, epsilon (draw margin) 0, and partial pair
# with window 1 converted from OpenSkill's averages to the paper's sums. The same configuration
# matched ladder_rating._update_race to 1.4e-14.
GOLDEN_RACES = [
    [(25.0, 8.0), (25.0, 8.0)],
    [(31.5, 5.2), (40.0, 4.5), (18.0, 7.9)],
    [(22.0, 6.0), (35.0, 4.2), (28.0, 7.0), (10.0, 5.5), (45.0, 4.8)],
    [(25.0, 4.5), (22.0, 4.9), (31.0, 5.3), (16.0, 5.7), (37.0, 6.1), (10.0, 6.5), (43.0, 6.9), (4.0, 7.300000000000001), (49.0, 7.7)],
    [(3.5, 5.0), (6.600000000000001, 5.199999999999999), (9.699999999999996, 5.4), (12.799999999999997, 5.6), (15.899999999999999, 5.8), (19.0, 6.0), (22.099999999999998, 6.2), (25.2, 6.4), (28.3, 6.6), (31.4, 6.8), (34.5, 7.0), (37.6, 7.2), (40.7, 7.4), (43.8, 7.6), (46.9, 7.8), (50.0, 8.0)],
    [(0.0, 6.5), (60.0, 4.1), (30.0, 5.0), (-10.0, 7.5), (75.0, 4.3)],
]
GOLDEN_THURSTONE_MOSTELLER = [
    [(27.503648581617366, 61.54138763206339), (22.496351418382634, 61.54138763206339)],
    [(33.72312384305575, 26.352394588812754), (39.01700734684967, 19.947031292255744), (15.898451132734102, 59.0666391981126)],
    [(30.552868473835055, 32.758010511802944), (35.61964838807277, 17.15656939840069), (28.465759159439436, 43.12437498095259), (12.250141141116284, 28.695110623364354), (36.78399913997954, 21.74867406992918)],
    [(32.91262804472112, 18.84182737800933), (30.40212312090474, 21.790965363889793), (34.33658925820194, 25.145119264043092), (24.62306907233588, 28.03756623232394), (31.355895521108465, 31.14163808441938), (16.86255310591993, 35.151510078860504), (22.383281503831274, 35.33959537081635), (5.5599527690724395, 44.29517397780396), (5.607839841137121, 35.5657228901525)],
    [(38.93932765913894, 19.342109084563823), (39.43868157837899, 20.3496363032183), (39.303241827484555, 21.282535536159124), (38.50009239333667, 22.120364043260995), (36.996244598369564, 22.84019616478029), (34.75857574901765, 23.416302767647384), (31.753742506720535, 23.81976855988545), (27.948068111271297, 24.018039184114127), (23.30740351927432, 23.974390556120017), (17.796963329818862, 23.6473142096868), (11.381138207586794, 22.989814859452817), (4.023286506236765, 21.94862046000597), (-4.3144909618771266, 20.46331119225098), (-13.671587508743393, 18.465382455510863), (-24.08913112347816, 15.877268308394358), (-35.61021944841451, 12.611365738134678)],
    [(23.11357923537838, 36.60384351721049), (58.254752182384195, 16.449988170731416), (31.07809987093211, 23.986490319287565), (2.206855722753609, 51.015557073130914), (61.99450883788539, 17.6513598245967)],
]
GOLDEN_PARTIAL_PAIR = [
    [(26.568929081105473, 63.03450518085817), (23.431070918894527, 63.03450518085817)],
    [(32.406718543877055, 26.893230126219763), (39.57531961092997, 20.131356951410375), (17.216091739325993, 61.61942881723913)],
    [(23.302854649585612, 35.72728652749745), (34.74389832704148, 17.54689467374375), (27.65667586142856, 47.90956585964311), (10.979744296273008, 30.014797258891775), (43.91588050258943, 22.994075885307804)],
    [(25.509857322880606, 20.17434322198769), (22.20220341667877, 23.78803853290055), (30.52152630549711, 27.79557890440315), (16.75393679643257, 32.12670928539648), (35.901480641370966, 36.78799968115348), (11.404079788412492, 41.783303404113454), (41.2233296259743, 47.11365752819321), (6.079803900416431, 52.77800159625408), (46.320522357717486, 58.99839735538425)],
    [(4.234712585055799, 24.87628277073437), (6.594814551108776, 26.74166608721809), (9.694304177921335, 28.806271860135464), (12.793772252131166, 30.943651808447772), (15.893219761536454, 33.1532771754723), (18.992647751272102, 35.434618990940294), (22.09205731337835, 37.78715041991327), (25.191449576784496, 40.21034898159779), (28.290825697783095, 42.70369862232455), (31.39018685105532, 45.26669163060471), (34.48953422129331, 47.898830385625516), (37.588868995452195, 50.59962893375554), (40.68819235565175, 53.36861439055693), (43.78750547273708, 56.20532816842593), (46.88680950049726, 59.10932703229027), (48.30516695841322, 63.02906002078706)],
    [(2.1808278023742713, 42.18374391229954), (59.28120095438631, 16.777275695729553), (29.92285221645077, 24.886152010133557), (-7.40486783929833, 55.881539202764884), (74.04024340422316, 18.487602481078333)],
]
GOLDEN_PLACKETT_LUCE = [
    [(26.568929081105473, 63.03450518085817), (23.431070918894527, 63.03450518085817)],
    [(32.27461524538471, 26.972993658877286), (39.82571305760355, 20.183971283106054), (16.749287939753952, 61.547733284404906)],
    [(23.032582450998877, 35.9615256515529), (35.2916938673787, 17.620874652228487), (28.48969620443478, 48.655257705192), (10.390520442250411, 30.1506431011115), (43.43046114441786, 22.952891308668327)],
    [(25.449400563379037, 20.247475409341252), (22.475189915784767, 24.002404795066024), (31.420655192970738, 28.068509520819774), (16.48976555735807, 32.45823503620648), (37.11185936294114, 37.1186153664691), (10.417994262373751, 42.15652692124914), (42.121872154771864, 47.31207398436074), (4.112259103791501, 53.04565186857854), (44.932974790612924, 58.65247087792569)],
    [(3.938873731583712, 24.9992761060555), (7.052037868853903, 27.038103827115545), (10.159162049732712, 29.156297830085744), (13.258488701412983, 31.353610026012294), (16.347841605849037, 33.629709838583345), (19.42449824578652, 35.984157826863246), (22.485006670898507, 38.416369903213145), (25.52491289694354, 40.92556789537821), (28.53833662094872, 43.51070971247452), (31.51727366674237, 46.17038818174281), (34.45036756235266, 48.90268082479784), (37.320545293181034, 51.704924306214295), (40.099888303062535, 54.573396050674866), (42.73641914611768, 57.503090069062665), (45.10933875982869, 60.490156699380755), (46.76861425127026, 63.587521264632414)],
    [(1.3672513121294634, 42.232621800938205), (60.21054092598067, 16.790833275817235), (30.505757152647725, 24.958656710854033), (-8.401625260435521, 56.11245714414972), (73.27060149989482, 18.44941207018067)],
]


class GoldenValueTests(unittest.TestCase):
    def check(self, update, expected):
        for race, after in zip(GOLDEN_RACES, expected):
            states = [[mu, sigma * sigma] for mu, sigma in race]
            update(states, finished(range(len(race))), beta=12.0, sigma_floor=0.0, alpha=0.0)
            for state, (mean, variance) in zip(states, after):
                self.assertAlmostEqual(state[0], mean, places=9)
                self.assertAlmostEqual(state[1], variance, places=9)

    def test_thurstone_mosteller_matches_openskill(self):
        self.check(rating_lab._thurstone_mosteller, GOLDEN_THURSTONE_MOSTELLER)

    def test_partial_pair_matches_openskill(self):
        self.check(rating_lab._bradley_terry_partial, GOLDEN_PARTIAL_PAIR)

    def test_plackett_luce_matches_openskill(self):
        self.check(rating_lab._plackett_luce, GOLDEN_PLACKETT_LUCE)


class IdentityTests(unittest.TestCase):
    WENG_LIN = dict(beta=12.0, sigma_floor=3.0, alpha=0.0)

    def run_update(self, update, entries, states, **params):
        after = [list(state) for state in states]
        update(after, entries, **(params or self.WENG_LIN))
        return after

    def test_two_racer_plackett_luce_is_the_full_pair_update(self):
        states = [[31.0, 49.0], [22.0, 25.0]]
        for entries in (finished([0, 1]), finished([1, 0]), finished([0]) + [dnf(1)], [dnf(0), (1, 1, DONE, 600.0)]):
            with self.subTest(entries=entries):
                full = self.run_update(rating_lab._bradley_terry_full, entries, states)
                luce = self.run_update(rating_lab._plackett_luce, entries, states)
                for ours, theirs in zip(luce, full):
                    self.assertAlmostEqual(ours[0], theirs[0], places=12)
                    self.assertAlmostEqual(ours[1], theirs[1], places=12)

    def test_two_racer_partial_pair_is_the_full_pair_update(self):
        states = [[31.0, 49.0], [22.0, 25.0]]
        for entries in (finished([0, 1]), finished([1, 0]), finished([0]) + [dnf(1)], [dnf(0), dnf(1)]):
            with self.subTest(entries=entries):
                self.assertEqual(
                    self.run_update(rating_lab._bradley_terry_partial, entries, states),
                    self.run_update(rating_lab._bradley_terry_full, entries, states),
                )

    def test_partial_pairs_are_neighbours_and_the_worst_place_over_each_non_finisher(self):
        entries = [(0, 2, DONE, None), dnf(1), (2, 1, DONE, None), (3, 3, DONE, None), dnf(4), (5, 3, DONE, None)]
        self.assertEqual(rating_lab._partial_pairs(entries), [(2, 0), (0, 3), (0, 5), (3, 1), (3, 4), (5, 1), (5, 4)])
        self.assertEqual(rating_lab._partial_pairs([dnf(0), dnf(1)]), [])

    def test_partial_pair_treats_racers_sharing_a_place_alike_whatever_the_entry_order(self):
        states = [[25.0, 36.0] for _ in range(4)]
        for places in ([1, 2, 2, 4], [1, 1, 3, 4]):
            entries = [(player, place, DONE, None) for player, place in enumerate(places)]
            after = self.run_update(rating_lab._bradley_terry_partial, entries, states)
            shared = [i for i, place in enumerate(places) if places.count(place) > 1]
            self.assertEqual(after[shared[0]], after[shared[1]])
            self.assertNotEqual(after[shared[0]], states[shared[0]])

    def test_an_elo_update_matches_a_hand_calculation(self):
        states = [[1000.0, 3], [1200.0, 3]]
        rating_lab._elo(states, finished([0, 1]), k0=32.0, k_min=32.0, provisional=0.0, alpha=0.0)
        gain = 32 * (1 - 1 / (1 + 10**0.5))
        self.assertAlmostEqual(states[0][0], 1000 + gain, places=12)
        self.assertAlmostEqual(states[1][0], 1200 - 32 * (1 / (1 + 10**-0.5)), places=12)
        self.assertEqual([state[1] for state in states], [4, 4])

    def test_the_provisional_k_falls_in_a_line_to_k_min(self):
        k = lambda races: rating_lab._elo_k(races, k0=48.0, k_min=16.0, provisional=10.0)
        self.assertEqual(k(0), 48.0)
        self.assertEqual(k(5), 32.0)
        self.assertEqual(k(10), 16.0)
        self.assertEqual(k(500), 16.0)
        self.assertEqual(rating_lab._elo_k(0, k0=48.0, k_min=16.0, provisional=0.0), 16.0)

    def test_elo_gives_a_debut_the_provisional_k(self):
        fresh, veteran = [[1000.0, 0], [1000.0, 10]], [[1000.0, 10], [1000.0, 10]]
        params = dict(k0=48.0, k_min=16.0, provisional=10.0, alpha=0.0)
        rating_lab._elo(fresh, finished([0, 1]), **params)
        rating_lab._elo(veteran, finished([0, 1]), **params)
        self.assertAlmostEqual(fresh[0][0] - 1000.0, 24.0)
        self.assertAlmostEqual(veteran[0][0] - 1000.0, 8.0)

    def test_the_elo_grid_drops_a_k0_that_has_no_effect(self):
        grid = rating_lab.FAMILIES["elo"].grid()
        self.assertEqual(len(grid), len({tuple(params.items()) for params in grid}))
        self.assertTrue(all(params["k0"] == params["k_min"] for params in grid if params["provisional"] == 0))

    def test_a_shared_win_never_costs_the_stronger_co_winner_in_any_family(self):
        # Player 0 first builds a big lead over player 1, then they tie for first ahead of player 2.
        history = [record(finished([0, 1]), f"alttpr-ladder/history-{index}") for index in range(10)]
        tied = record([(0, 1, DONE, 3000.0), (1, 1, DONE, 3000.0), (2, 3, DONE, 3200.0)])
        for name, family in rating_lab.FAMILIES.items():
            with self.subTest(family=name):
                params = family.grid()[len(family.grid()) // 2]
                result = family.rate([*history, tied], 3, **params)
                before, after = result.pre[-1][0], result.post[-1][0]
                self.assertGreaterEqual(result.display(after), result.display(before))

    def test_plackett_luce_never_lowers_a_dominant_co_winner(self):
        # Against a weaker co-winner and a weak third, their chance of winning is above one half, which
        # would make their omega negative if the co-winner stayed in their view.
        states = [[60.0, 16.0], [20.0, 16.0], [0.0, 16.0]]
        after = self.run_update(rating_lab._plackett_luce, [(0, 1, DONE, None), (1, 1, DONE, None), (2, 3, DONE, None)], states)
        self.assertGreater(after[0][0], 60.0)
        self.assertGreater(after[1][0], 20.0)


# Every family's update, the parameters it takes, and a draw of one racer's pre-race state.
def _weng_lin_state(rng, params):
    return [rng.uniform(-40, 90), (params["sigma_floor"] if rng.random() < 0.3 else rng.uniform(params["sigma_floor"], params["sigma0"])) ** 2]


def _elo_state(rng, params):
    return [rng.uniform(400, 2200), rng.randint(0, 100)]


WENG_LIN_PARAMS = ("beta", "sigma_floor", "alpha")
FUZZ = {
    "bradley_terry_full": (rating_lab._bradley_terry_full, WENG_LIN_PARAMS, _weng_lin_state),
    "thurstone_mosteller": (rating_lab._thurstone_mosteller, WENG_LIN_PARAMS, _weng_lin_state),
    "partial_pair": (rating_lab._bradley_terry_partial, WENG_LIN_PARAMS, _weng_lin_state),
    "plackett_luce": (rating_lab._plackett_luce, WENG_LIN_PARAMS, _weng_lin_state),
    "elo": (rating_lab._elo, ("k0", "k_min", "provisional", "alpha"), _elo_state),
}
FUZZ_RACES = 2000
FUZZ_SEED = 20260101


def random_entries(rng):
    """Return a race of 2 to 32 entrants, skewed small, with DNFs and, in 10% of races, tied places."""
    size = min(32, 2 + int(rng.expovariate(0.25)))
    entrants = list(range(size))
    rng.shuffle(entrants)
    done = [player for player in entrants if rng.random() >= 0.15]
    tied = rng.random() < 0.1
    places = {}
    place = 0
    for position, player in enumerate(done):
        share = rng.random() < (0.6 if position == 1 else 0.3)
        place = place if tied and position and share else position + 1
        places[player] = place
    return [
        (player, places[player], DONE, rng.uniform(600.0, 30_000.0)) if player in places else (player, None, DNF, None)
        for player in range(size)
    ]


class FuzzTests(unittest.TestCase):
    """Whatever the pre-race states, each family keeps the ladder's promises in a single update."""

    def test_one_update_keeps_every_promise(self):
        for name, (update, names, sample) in FUZZ.items():
            family = rating_lab.FAMILIES[name]
            for params in probe_settings(family):
                with self.subTest(family=name, params=params):
                    self.fuzz(family, update, names, sample, params)

    def fuzz(self, family, update, names, sample, params):
        rng = random.Random(FUZZ_SEED)
        keep = {key: params[key] for key in names}
        display = family.rate([], 0, **params).display
        weng_lin = "sigma_floor" in params
        floor_variance = params["sigma_floor"] ** 2 if weng_lin else None
        winners = 0
        shared_wins = 0
        sizes = set()
        for _ in range(FUZZ_RACES // 3):
            entries = random_entries(rng)
            shared_wins += sum(entry[1] == 1 for entry in entries) > 1
            sizes.add(len(entries))
            before = [sample(rng, params) for _ in entries]
            after = [list(state) for state in before]
            update(after, entries, **keep)
            for (_player, place, status, _seconds), old, new in zip(entries, before, after):
                if place == 1:
                    winners += 1
                    self.assertGreaterEqual(display(new), display(old))
                if weng_lin:
                    self.assertLessEqual(new[1], old[1])
                    self.assertGreaterEqual(new[1], min(old[1], floor_variance) - 1e-9)
                else:
                    self.assertEqual(new[1], old[1] + 1)
            for variant in (
                [(player, place, status, None) for player, place, status, _seconds in entries],
                [(player, place, status, rng.uniform(1.0, 99_999.0)) for player, place, status, _seconds in entries],
            ):
                again = [list(state) for state in before]
                update(again, variant, **keep)
                self.assertEqual(again, after)
        # The races this run actually drew must include the hard cases.
        self.assertGreater(winners, 100)
        self.assertGreater(shared_wins, 10)
        self.assertIn(2, sizes)
        self.assertGreaterEqual(max(sizes), 20)

    def test_the_fuzz_covers_every_family(self):
        self.assertEqual(set(FUZZ), set(rating_lab.FAMILIES))

    def test_every_fresh_racer_shows_1000(self):
        for name, family in rating_lab.FAMILIES.items():
            for params in probe_settings(family):
                with self.subTest(family=name, params=params):
                    result = family.rate([record(finished([0, 1]))], 2, **params)
                    self.assertEqual([result.display(state) for state in result.pre[0]], [1000, 1000])


# A known-truth check. Field sizes approximate the archive: 2 racers in 19% of races, 3 in 17%,
# 4 in 13%, 5 in 10%, 6 in 8%, then a thinning tail to 16. About 14.6% of entrants do not finish.
FIELD_WEIGHTS = {2: 19, 3: 17, 4: 13, 5: 10, 6: 8, 7: 6, 8: 5, 9: 4.5, 10: 3.5, 11: 3, 12: 2.5, 13: 2.5, 14: 2, 15: 2, 16: 2}
DNF_RATE = 0.146
SIM_RACERS = 60
SIM_RACES = 1500
SIM_NOISE = 12.0  # Gaussian sd of one performance; the Gumbel scale is set to the same spread
SIM_GUMBEL_SCALE = 10.0
SIM_VETERANS = 20
SIM_ELO = dict(k0=48.0, k_min=16.0, provisional=10.0, alpha=0.0)


def gumbel(rng, scale):
    return -scale * math.log(-math.log(rng.random()))


@lru_cache(maxsize=None)
def simulate(generator, drift):
    """Return (records, skills at each race, final skills) for racers whose results follow `generator`.

    A performance is skill plus noise; the k worst performers do not finish,
    with k ~ Binomial(n, DNF_RATE), and the rest are ranked by performance.
    Skills take a Gaussian step of sd `drift` per race when `drift` is above 0.
    """
    rng = random.Random(7)
    skills = [rng.uniform(-10.0, 60.0) for _ in range(SIM_RACERS)]
    noise = (lambda: rng.gauss(0.0, SIM_NOISE)) if generator == "gaussian" else (lambda: gumbel(rng, SIM_GUMBEL_SCALE))
    sizes, weights = zip(*FIELD_WEIGHTS.items())
    records, history = [], []
    for index in range(SIM_RACES):
        field = rng.sample(range(SIM_RACERS), rng.choices(sizes, weights)[0])
        performance = {player: skills[player] + noise() for player in field}
        order = sorted(field, key=performance.get, reverse=True)
        out = sum(rng.random() < DNF_RATE for _ in field)
        done = order[: len(order) - out]
        entries = [(player, place, DONE, None) for place, player in enumerate(done, start=1)]
        records.append(record(entries + [dnf(player) for player in order[len(done):]], f"sim-{index}"))
        history.append(list(skills))
        if drift:
            skills = [skill + rng.gauss(0.0, drift) for skill in skills]
    return records, history, skills


def true_chance(generator):
    """Return P(performance of a > performance of b) from two skills, in a one-element state."""
    if generator == "gaussian":
        return lambda a, b: 0.5 * math.erfc(-(a[0] - b[0]) / (2 * SIM_NOISE))
    return lambda a, b: 1 / (1 + math.exp(-(a[0] - b[0]) / SIM_GUMBEL_SCALE))


def sim_loss(records, result):
    second_half = list(range(len(records) // 2, len(records)))
    scores = rating_lab.race_scores(rating_lab.pair_chances(records, result, second_half))
    return rating_lab.summarise(scores).log_loss


def sim_params(name):
    return SIM_ELO if name == "elo" else rating_lab.SHIPPED


class SimulationTests(unittest.TestCase):
    """On races with a known truth every family must beat a coin by a margin and rank racers sensibly."""

    LOSS_BELOW_COIN = 0.2
    LOSS_ABOVE_ORACLE = 0.1
    MIN_TAU = 0.8

    def check(self, generator, drift, *, below_coin, above_oracle, min_tau):
        records, history, final = simulate(generator, drift)
        pre = [[(history[index][player],) for player, *_rest in race["e"]] for index, race in enumerate(records)]
        oracle = rating_lab.Result(pre, [], true_chance(generator), lambda state: 0)
        best = sim_loss(records, oracle)
        veterans = [player for player in range(SIM_RACERS) if sum(player == e[0] for r in records for e in r["e"]) >= SIM_VETERANS]
        for name, family in rating_lab.FAMILIES.items():
            with self.subTest(family=name, generator=generator, drift=drift):
                result = family.rate(records, SIM_RACERS, **sim_params(name))
                loss = sim_loss(records, result)
                self.assertLess(loss, math.log(2) - below_coin, (name, loss))
                self.assertLess(loss, best + above_oracle, (name, loss, best))
                points = rating_lab._final_points(records, result)
                tau = rating_lab.kendall_tau([points[p] for p in veterans], [final[p] for p in veterans])
                self.assertGreaterEqual(tau, min_tau, (name, tau))

    def test_both_noise_shapes(self):
        for generator in ("gaussian", "gumbel"):
            self.check(generator, 0.0, below_coin=self.LOSS_BELOW_COIN, above_oracle=self.LOSS_ABOVE_ORACLE, min_tau=self.MIN_TAU)

    def test_slowly_drifting_skills(self):
        for generator in ("gaussian", "gumbel"):
            self.check(generator, 0.1, below_coin=0.18, above_oracle=0.12, min_tau=0.75)



class ScoringTests(unittest.TestCase):
    def outcome_model(self, races, believe_winner):
        """A model whose state is a racer's standing in that race, so it 'knows' every result."""
        pre = [
            [(-(place if place else 99),) for _player, place, _status, _seconds in race["e"]]
            for race in races
        ]

        def win_chance(a, b):
            if a == b:
                return 0.5
            return 1.0 if (a > b) == believe_winner else 0.0

        return rating_lab.Result(pre, pre, win_chance, lambda state: 1000)

    def test_a_coin_flip_scores_ln2_on_every_decisive_pair(self):
        races = mixed_league()
        coin = rating_lab.Result(rating_lab.rate_shipped(races, 7).pre, [], lambda a, b: 0.5, lambda s: 1000)
        indexes = list(range(len(races)))
        summary = rating_lab.summarise(rating_lab.race_scores(rating_lab.pair_chances(races, coin, indexes)))
        expected_pairs = sum(len(ladder_rating._decisive_pairs(race["e"])) for race in races)
        self.assertEqual(summary.pairs, expected_pairs)
        self.assertAlmostEqual(summary.log_loss, math.log(2), places=12)
        self.assertEqual(summary.accuracy, 0.5)

    def test_scoring_is_oriented_from_the_winner(self):
        races = mixed_league()
        indexes = list(range(len(races)))
        right = rating_lab.summarise(
            rating_lab.race_scores(rating_lab.pair_chances(races, self.outcome_model(races, True), indexes))
        )
        wrong = rating_lab.summarise(
            rating_lab.race_scores(rating_lab.pair_chances(races, self.outcome_model(races, False), indexes))
        )
        self.assertEqual(right.accuracy, 1.0)
        self.assertAlmostEqual(right.log_loss, -math.log(1 - rating_lab.PROBABILITY_CLIP), places=12)
        self.assertEqual(wrong.accuracy, 0.0)
        self.assertAlmostEqual(wrong.log_loss, -math.log(rating_lab.PROBABILITY_CLIP), places=9)

    def test_race_log_loss_counts_each_race_once(self):
        summary = rating_lab.summarise([score(1, 1.0), score(9, 0.9)])
        self.assertAlmostEqual(summary.log_loss, 0.19)
        self.assertAlmostEqual(summary.race_log_loss, 0.55)


class StatisticsTests(unittest.TestCase):
    def test_a_model_against_itself_has_a_zero_interval(self):
        scores = [score(3, 2.0), score(5, 3.0), score(1, 0.5)]
        difference = rating_lab.paired_bootstrap(scores, scores, rating_lab.race_blocks(scores))
        self.assertEqual((difference.delta, difference.low, difference.high), (0.0, 0.0, 0.0))
        self.assertEqual(difference.p_value, 1.0)

    def test_the_bootstrap_is_reproducible_and_recovers_a_known_shift(self):
        rng = random.Random(1)
        baseline = [score(5, 3.5) for _ in range(400)]
        exact = [score(5, 3.5 - 0.05) for _ in range(400)]
        noisy = [score(5, 3.5 + 5 * (-0.01 + rng.uniform(-0.02, 0.02))) for _ in range(400)]
        blocks = rating_lab.race_blocks(baseline)
        shift = rating_lab.paired_bootstrap(baseline, exact, blocks)
        self.assertAlmostEqual(shift.delta, -0.01)
        self.assertAlmostEqual(shift.low, -0.01)
        self.assertAlmostEqual(shift.high, -0.01)
        first = rating_lab.paired_bootstrap(baseline, noisy, blocks)
        self.assertEqual(first, rating_lab.paired_bootstrap(baseline, noisy, blocks))
        self.assertLess(first.low, first.delta)
        self.assertLess(first.delta, first.high)
        self.assertLess(first.high, 0)
        self.assertLess(first.p_value, 0.01)

    def test_the_clustered_se_matches_the_race_bootstrap(self):
        rng = random.Random(2)
        baseline = [score(p, p * 0.6) for p in (rng.randint(1, 30) for _ in range(500))]
        candidate = [score(b.pairs, b.loss + b.pairs * rng.gauss(-0.002, 0.03)) for b in baseline]
        blocks = rating_lab.race_blocks(baseline)
        se = rating_lab.clustered_se(baseline, candidate)
        difference = rating_lab.paired_bootstrap(baseline, candidate, blocks)
        bootstrap_se = (difference.high - difference.low) / (2 * 1.96)
        self.assertAlmostEqual(se / bootstrap_se, 1.0, delta=0.15)
        shifted = [score(b.pairs, b.loss - 0.01 * b.pairs) for b in baseline]
        self.assertAlmostEqual(rating_lab.clustered_se(baseline, shifted), 0.0, places=12)

    def test_week_blocks_follow_eastern_iso_weeks(self):
        races = [record([], day=day) for day in ("2026-03-01", "2026-03-02", "2026-03-08", "2026-03-09")]
        scores = [score(1, 0.5)] * 4
        self.assertEqual(rating_lab.week_blocks(races, [0, 1, 2, 3], scores), [[0], [1, 2], [3]])
        self.assertEqual(rating_lab.race_blocks([score(1, 0.5), score(0, 0.0), score(2, 1.0)]), [[0], [2]])

    def test_holm_adjustment(self):
        adjusted = rating_lab.holm({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertAlmostEqual(adjusted["a"], 0.03)
        self.assertAlmostEqual(adjusted["c"], 0.06)
        self.assertAlmostEqual(adjusted["b"], 0.06)
        self.assertEqual(rating_lab.holm({"a": 0.6, "b": 0.7}), {"a": 1.0, "b": 1.0})

    def test_kendall_tau_b(self):
        self.assertEqual(rating_lab.kendall_tau([1, 2, 3], [4, 5, 6]), 1.0)
        self.assertEqual(rating_lab.kendall_tau([1, 2, 3], [6, 5, 4]), -1.0)
        self.assertAlmostEqual(rating_lab.kendall_tau([1, 2, 3], [1, 1, 2]), 2 / math.sqrt(6))
        self.assertIsNone(rating_lab.kendall_tau([1, 1], [1, 2]))

    def test_calibration_error(self):
        # Chances are the winner's: 0.1 means the favourite, at 0.9, lost.
        calibrated = ([0.9] * 9 + [0.1]) * 10
        self.assertAlmostEqual(rating_lab._calibration_error(calibrated), 0.0)
        self.assertAlmostEqual(rating_lab._calibration_error([0.9, 0.1] * 50), 0.4)


class RuleCheckerTests(unittest.TestCase):
    """The checker must catch each broken promise, or a clean result would mean nothing."""

    def check(self, update, initial=(25.0, 64.0)):
        races = mixed_league()

        def rate(records):
            pre, post = rating_lab._replay(update, initial, records, 7)
            return rating_lab.Result(pre, post, lambda a, b: 0.5, rating_lab._weng_lin_display(beta=12.0, sigma0=8.0, sigma_floor=4.0))

        return rating_lab.check_rules(races, rate(races), rate)

    def test_a_clean_update_passes(self):
        self.assertTrue(self.check(lambda states, entries: None).kept)

    def test_it_catches_a_winner_losing_points(self):
        def punish_winner(states, entries):
            for state, (_player, place, status, _seconds) in zip(states, entries):
                if place == 1:
                    state[0] -= 5.0

        rules = self.check(punish_winner)
        self.assertGreater(rules.winners_lost_points, 0)
        self.assertFalse(rules.kept)

    def test_it_catches_a_debut_away_from_1000(self):
        rules = self.check(lambda states, entries: None, initial=(30.0, 64.0))
        self.assertGreater(rules.debuts_not_at_base, 0)

    def test_it_catches_finish_times_changing_the_result(self):
        def by_time(states, entries):
            for state, (_player, _place, _status, seconds) in zip(states, entries):
                state[0] += 0.0 if seconds is None else seconds / 1e6

        self.assertTrue(self.check(by_time).times_change_result)

    def test_it_catches_a_mode_rating_that_reads_other_modes(self):
        # The overall rating passed off as a mode rating: a racer's debut in a second
        # mode is not 1000, and their state in one mode moves in the other's races.
        races = mixed_league()
        leaked = rating_lab.rate_shipped(races, 7)
        rules = rating_lab.check_rules(
            races, leaked, lambda variant: rating_lab.rate_shipped(variant, 7), rating_lab._by_mode_racer
        )
        self.assertGreater(rules.debuts_not_at_base, 0)
        self.assertGreater(rules.changed_while_away, 0)
        honest = rating_lab.by_mode(rating_lab.rate_bradley_terry_full, races, 7, **rating_lab.SHIPPED)
        rerun = lambda variant: rating_lab.by_mode(rating_lab.rate_bradley_terry_full, variant, 7, **rating_lab.SHIPPED)
        self.assertTrue(rating_lab.check_rules(races, honest, rerun, rating_lab._by_mode_racer).kept)

    def test_it_catches_a_rating_moving_while_a_racer_is_away(self):
        races = mixed_league()
        result = rating_lab.rate_shipped(races, 7)
        drifted = [[(mean - 1.0, variance) for mean, variance in race] for race in result.pre]
        drifted[0] = result.pre[0]
        moved = dataclasses.replace(result, pre=drifted)
        rules = rating_lab.check_rules(races, moved, lambda variant: moved)
        self.assertGreater(rules.changed_while_away, 0)


class TuningTests(unittest.TestCase):
    # With more racers a family that compares each racer with fewer rivals, or spreads a race over its
    # whole field, is never as lively as the shipped rating on this toy league, so `tune` would refuse it.
    PLAYERS = 4

    def small(self, family):
        """Each axis's ends only, which bracket the shipped liveliness and keep the test fast."""
        axes = {name: (values[0], values[-1]) for name, values in family.axes.items()}
        return dataclasses.replace(family, axes=axes)

    def test_tuning_never_reads_a_race_from_the_evaluation_period(self):
        league = dated_league(players=self.PLAYERS)
        before = [race for race in league if race["d"] < rating_lab.OUT_OF_SAMPLE_START]
        scrambled = [
            {**race, "e": [(player, None, DNF, None) for player, *_rest in race["e"]]}
            if race["d"] >= rating_lab.OUT_OF_SAMPLE_START else race
            for race in league
        ]
        self.assertLess(len(before), len(league))
        for name, family in rating_lab.FAMILIES.items():
            with self.subTest(family=name), mock.patch.dict(rating_lab.FAMILIES, {name: self.small(family)}):
                expected = rating_lab.tune(name, before, self.PLAYERS)
                for records in (league, scrambled):
                    tuning = rating_lab.tune(name, records, self.PLAYERS)
                    self.assertEqual(tuning.chosen, expected.chosen)
                    self.assertEqual(
                        [(s.params, s.log_loss) for s in tuning.settings],
                        [(s.params, s.log_loss) for s in expected.settings],
                    )

    def test_the_chosen_setting_is_lively_and_within_one_se_of_the_best(self):
        league = dated_league(players=self.PLAYERS)
        for name, family in rating_lab.FAMILIES.items():
            with self.subTest(family=name), mock.patch.dict(rating_lab.FAMILIES, {name: self.small(family)}):
                tuning = rating_lab.tune(name, league, self.PLAYERS)
                self.assertGreaterEqual(tuning.chosen.veteran_move, tuning.liveliness)
                self.assertLessEqual(tuning.chosen.log_loss - tuning.best.log_loss, tuning.standard_error)
                self.assertEqual(len(tuning.chosen.fold_log_loss), len(rating_lab.TUNING_FOLDS))

    def test_records_out_of_date_order_are_refused(self):
        races = [record([], day="2025-12-30"), record([], day="2026-01-02"), record([], day="2025-12-31")]
        with self.assertRaises(ValueError):
            rating_lab._before(races, rating_lab.OUT_OF_SAMPLE_START)
        self.assertEqual(rating_lab._before(races[:2], rating_lab.OUT_OF_SAMPLE_START), races[:1])

    def test_a_grid_edge_is_reported_unless_it_is_a_natural_bound(self):
        family = rating_lab.FAMILIES[rating_lab.RETUNED]
        self.assertEqual(rating_lab._edges(family, rating_lab.SHIPPED), [])
        edges = rating_lab._edges(family, {**rating_lab.SHIPPED, "beta": 20.0, "sigma_floor": 2.0})
        self.assertEqual(len(edges), 2)


class DecisionTests(unittest.TestCase):
    def evidence(self, delta=-0.006, p=0.001, halves=(-0.004, -0.008), eligible=True, race_ok=True):
        difference = rating_lab.Difference(delta, delta - 0.002, delta + 0.002, p)
        return rating_lab.Evidence(eligible, difference, p, halves, race_ok)

    def test_each_requirement_is_needed_to_qualify(self):
        self.assertTrue(self.evidence().qualifies)
        for broken in (
            self.evidence(eligible=False),
            self.evidence(p=0.2),
            self.evidence(delta=-0.002),
            self.evidence(halves=(-0.01, 0.001)),
            self.evidence(race_ok=False),
        ):
            self.assertFalse(broken.qualifies)

    def test_verdicts(self):
        good, poor = self.evidence(), self.evidence(delta=0.001, p=0.9)
        retuned = rating_lab.RETUNED
        self.assertEqual(rating_lab.verdict({retuned: poor, "pl": poor}, {"pl": poor}), ("keep", None))
        self.assertEqual(rating_lab.verdict({retuned: good, "pl": poor}, {"pl": poor}), ("retune", retuned))
        self.assertEqual(rating_lab.verdict({retuned: good, "pl": good}, {"pl": good}), ("switch", "pl"))
        # Beating shipped is not enough: a new model must beat the retuned one too.
        self.assertEqual(rating_lab.verdict({retuned: good, "pl": good}, {"pl": poor}), ("retune", retuned))
        better = self.evidence(delta=-0.009)
        self.assertEqual(
            rating_lab.verdict({retuned: poor, "pl": good, "elo": better}, {"pl": good, "elo": good}),
            ("switch", "elo"),
        )


class ArchiveTests(unittest.TestCase):
    def test_the_shipped_setting_reproduces_the_backtest(self):
        records, player_count, _scores = ladder_rating._backtest_records(REPO_ROOT / "archive")
        report = ladder_rating.backtest(REPO_ROOT / "archive")
        result = rating_lab.rate_shipped(records, player_count)
        for period, (ours, _racetime) in (
            ([i for i, r in enumerate(records) if r["d"] >= rating_lab.OUT_OF_SAMPLE_START], report.evaluation),
            ([i for i, r in enumerate(records) if r["d"] < rating_lab.OUT_OF_SAMPLE_START], report.tuning),
        ):
            summary = rating_lab.summarise(rating_lab.race_scores(rating_lab.pair_chances(records, result, period)))
            self.assertEqual(summary.pairs, ours.pairs)
            self.assertEqual(summary.accuracy, ours.accuracy)
            self.assertAlmostEqual(summary.log_loss, ours.log_loss, places=12)
        # The liveliness bar is measured the way the backtest reports veteran moves.
        moves = rating_lab._veteran_moves(records, result, set(range(len(records))))
        self.assertEqual(statistics.median(moves), report.veteran_move)


if __name__ == "__main__":
    unittest.main()
