import math
import unittest
from datetime import date, timedelta

from scripts import highlights


DAY_ONE = date(2026, 3, 1)


def done(player, place, seconds=None):
    return (player, place, 0, seconds)


def dnf(player):
    return (player, None, 1, None)


def dq(player):
    return (player, None, 2, None)


def record(entries, *, mode=0, day=0, hour=12, n=None, preset=None):
    return {
        "r": "alttpr-ladder/test-race",
        "m": mode,
        "mode": f"mode{mode}",
        "d": (DAY_ONE + timedelta(days=day)).isoformat(),
        "h": hour,
        "n": len(entries) if n is None else n,
        "p": preset,
        "e": list(entries),
    }


def duel(first, second, *, first_seconds=3600.0, margin=60.0, **kwargs):
    return record(
        [done(first, 1, first_seconds), done(second, 2, first_seconds + margin)],
        **kwargs,
    )


class FakeRatings:
    """Constant per-player means before and after every race."""

    def __init__(self, records, means=None):
        means = means or {}
        self.pre = [[(means.get(e[0], 0.0), 1.0) for e in r["e"]] for r in records]
        self.post = self.pre

    def win_chance(self, a, b):
        return 1 / (1 + math.exp(b[0] - a[0]))


def run(records, *, means=None, mode_count=2, modes=None, presets=(), names=None):
    names = names or {}
    return highlights.compute(
        records,
        players=[{"id": str(i), "name": names.get(i, f"P{i}")} for i in range(40)],
        modes=modes or [f"mode{i}" for i in range(mode_count)],
        presets=list(presets),
        ratings=FakeRatings(records, means),
    )


def tags_of(result, race_index, kind):
    return [tag for tag in result["race_tags"][race_index] if tag[0] == kind]


GLITCHED_MODES = ["mode0", highlights.GLITCHED_MODE]
ENTRANCE_MODES = ["mode0", highlights.ENTRANCE_MODE]
MYSTERY_MODES = ["mode0", highlights.MYSTERY_MODE]
CASUAL_BOOTS = ({"m": 0, "n": highlights.CASUAL_BOOTS_PRESET}, {"m": 0, "n": "Open"})


def warmup(players, count, **kwargs):
    """Races in which the players finish in the order given."""
    return [
        record(
            [done(p, place + 1, 3000.0 + 100 * place) for place, p in enumerate(players)],
            day=day,
            **kwargs,
        )
        for day in range(count)
    ]


class PersonalBestTests(unittest.TestCase):
    def test_first_timed_finish_is_baseline_and_untagged(self):
        result = run([duel(0, 1)])
        self.assertEqual(tags_of(result, 0, "pb"), [])
        self.assertEqual(result["players"][0]["pbs"], {"0": [[0, 3600]]})

    def test_strict_improvement_is_tagged_with_previous_best(self):
        records = [duel(0, 1, first_seconds=3600.0), duel(0, 1, first_seconds=3500.4)]
        result = run(records)
        self.assertEqual(tags_of(result, 1, "pb"), [["pb", 0, 3500, 3600], ["pb", 1, 3560, 3660]])
        self.assertEqual(result["players"][0]["pbs"]["0"], [[0, 3600], [1, 3500]])

    def test_equal_or_slower_time_is_not_a_pb(self):
        records = [duel(0, 1, first_seconds=3600.0), duel(0, 1, first_seconds=3600.0)]
        self.assertEqual(tags_of(run(records), 1, "pb"), [])

    def test_sub_second_improvement_is_not_a_pb(self):
        records = [duel(0, 1, first_seconds=3600.4), duel(0, 1, first_seconds=3600.1)]
        result = run(records)
        self.assertEqual(tags_of(result, 1, "pb"), [])
        self.assertEqual(tags_of(result, 1, "record"), [])

    def test_baseline_is_per_mode(self):
        records = [duel(0, 1, first_seconds=3600.0), duel(0, 1, first_seconds=4000.0, mode=1)]
        result = run(records, mode_count=2)
        self.assertEqual(tags_of(result, 1, "pb"), [])
        self.assertEqual(set(result["players"][0]["pbs"]), {"0", "1"})

    def test_dnf_and_dq_have_no_pb(self):
        result = run([record([dnf(0), dq(1), done(2, 1, 3000.0)])])
        self.assertEqual(result["players"][0]["pbs"], {})
        self.assertEqual(result["players"][1]["pbs"], {})


class ModeRecordTests(unittest.TestCase):
    def test_first_race_sets_baseline_without_tag(self):
        result = run([duel(0, 1)])
        self.assertEqual(tags_of(result, 0, "record"), [])
        self.assertEqual(result["records"]["modes"][0]["steps"], [[0, 0, 3600]])

    def test_improvement_is_tagged_and_recorded_as_a_step(self):
        records = [duel(0, 1, first_seconds=3600.0), duel(2, 1, first_seconds=3500.0)]
        result = run(records)
        self.assertEqual(tags_of(result, 1, "record"), [["record", 2, 3500, 3600]])
        self.assertEqual(result["records"]["modes"][0]["steps"], [[0, 0, 3600], [1, 2, 3500]])

    def test_slower_or_equal_race_adds_no_step(self):
        records = [duel(0, 1, first_seconds=3600.0), duel(2, 1, first_seconds=3600.0)]
        result = run(records)
        self.assertEqual(tags_of(result, 1, "record"), [])
        self.assertEqual(len(result["records"]["modes"][0]["steps"]), 1)

    def test_race_without_times_does_not_set_baseline(self):
        records = [record([dnf(0), dnf(1)]), duel(0, 1)]
        result = run(records)
        self.assertEqual(result["records"]["modes"][0]["steps"], [[1, 0, 3600]])

    def test_modes_are_independent(self):
        records = [duel(0, 1, first_seconds=3000.0), duel(0, 1, first_seconds=4000.0, mode=1)]
        result = run(records, mode_count=2)
        self.assertEqual(tags_of(result, 1, "record"), [])
        self.assertEqual(len(result["records"]["modes"]), 2)


class PhotoFinishTests(unittest.TestCase):
    def test_margin_under_five_seconds_is_a_photo(self):
        result = run([duel(0, 1, margin=1.234)])
        self.assertEqual(tags_of(result, 0, "photo"), [["photo", 0, 1, 1.23]])

    def test_margin_of_exactly_five_seconds_is_not_a_photo(self):
        self.assertEqual(tags_of(run([duel(0, 1, margin=5.0)]), 0, "photo"), [])

    def test_margin_uses_exact_seconds(self):
        result = run([duel(0, 1, first_seconds=3600.4, margin=4.9)])
        self.assertEqual(len(tags_of(result, 0, "photo")), 1)

    def test_runner_up_must_be_timed_and_identified(self):
        untimed = record([done(0, 1, 3600.0), done(1, 2, None)])
        anonymous_second = record([done(0, 1, 3600.0), done(1, 3, 3601.0)], n=3)
        result = run([untimed, anonymous_second])
        self.assertEqual(tags_of(result, 0, "photo"), [])
        self.assertEqual(tags_of(result, 1, "photo"), [])

    def test_photo_unlocks_sniper_for_first_and_sniped_for_second(self):
        result = run([duel(0, 1, margin=2.0), duel(2, 3, margin=30.0)])
        self.assertEqual(result["players"][0]["badges"]["sniper"], 0)
        self.assertEqual(result["players"][1]["badges"]["sniped"], 0)
        self.assertNotIn("sniped", result["players"][0]["badges"])
        self.assertNotIn("sniper", result["players"][1]["badges"])
        self.assertNotIn("sniper", result["players"][2]["badges"])
        self.assertEqual(tags_of(result, 0, "badge"), [["badge", 0, "sniper"], ["badge", 1, "sniped"]])


class WinnerTests(unittest.TestCase):
    def test_first_win_is_tagged_once_across_modes(self):
        records = [duel(0, 1), duel(0, 1, mode=1), duel(1, 0)]
        result = run(records, mode_count=2)
        self.assertEqual(tags_of(result, 0, "first_win"), [["first_win", 0]])
        self.assertEqual(tags_of(result, 1, "first_win"), [])
        self.assertEqual(tags_of(result, 2, "first_win"), [["first_win", 1]])

    def test_first_blood_badge_has_no_badge_tag(self):
        result = run([duel(0, 1)])
        self.assertEqual(result["players"][0]["badges"]["first_blood"], 0)
        self.assertEqual(tags_of(result, 0, "badge"), [])

    def test_tied_first_place_uses_first_entry_as_winner(self):
        tied = record([done(3, 1, 3000.0), done(2, 1, 3000.0), done(1, 3, 3100.0)])
        result = run([tied])
        self.assertEqual(result["records"]["blowouts"], [])
        self.assertEqual(result["records"]["modes"][0]["steps"], [[0, 3, 3000]])
        self.assertEqual(
            tags_of(result, 0, "first_win"), [["first_win", 3], ["first_win", 2]]
        )

    def test_anonymous_winner_skips_winner_tags(self):
        race = record([done(0, 2, 3000.0), done(1, 3, 3001.0), done(2, 4, 3002.0)], n=5)
        result = run([race], means={0: 5.0})
        for kind in ("photo", "first_win", "upset"):
            self.assertEqual(tags_of(result, 0, kind), [])
        self.assertEqual(result["records"]["photo"], [])
        self.assertEqual(result["records"]["big_field"], [])
        self.assertEqual(result["players"][0]["badges"], {})

    def test_dq_has_no_place_and_is_not_a_finish(self):
        result = run([record([dq(0), done(1, 1, 3000.0)])])
        self.assertEqual(result["players"][0]["streaks"]["finish"]["best"], None)
        self.assertEqual(result["players"][1]["streaks"]["win"]["best"], [1, 0, 0])


class MilestoneTests(unittest.TestCase):
    def test_entry_count_milestone_counts_any_status(self):
        records = [record([dnf(0), done(1, 1, 3000.0)], day=i) for i in range(25)]
        result = run(records)
        self.assertEqual(tags_of(result, 24, "milestone"), [["milestone", 0, 25], ["milestone", 1, 25]])
        self.assertEqual(sum(len(tags_of(result, i, "milestone")) for i in range(24)), 0)


class StreakTests(unittest.TestCase):
    def test_win_streak_best_and_current(self):
        records = [duel(0, 1), duel(0, 1), duel(1, 0), duel(0, 1), duel(0, 1), duel(0, 1)]
        streaks = run(records)["players"][0]["streaks"]["win"]
        self.assertEqual(streaks["best"], [3, 3, 5])
        self.assertEqual(streaks["current"], [3, 3, 5])

    def test_best_streak_tie_keeps_earliest_end(self):
        records = [duel(0, 1), duel(0, 1), duel(1, 0), duel(0, 1), duel(0, 1)]
        streaks = run(records)["players"][0]["streaks"]["win"]
        self.assertEqual(streaks["best"], [2, 0, 1])
        self.assertEqual(streaks["current"], [2, 3, 4])

    def test_current_is_none_when_latest_race_broke_the_streak(self):
        records = [duel(0, 1), duel(0, 1), duel(1, 0)]
        streaks = run(records)["players"][0]["streaks"]["win"]
        self.assertEqual(streaks["best"], [2, 0, 1])
        self.assertIsNone(streaks["current"])

    def test_racer_without_wins_has_no_win_streak(self):
        streaks = run([duel(0, 1)])["players"][1]["streaks"]["win"]
        self.assertEqual(streaks, {"best": None, "current": None})

    def test_podium_streak_includes_places_one_to_three(self):
        records = [
            record([done(0, 1, 3000.0), done(1, 2, 3001.0), done(2, 3, 3002.0), done(3, 4, 3003.0)]),
            record([done(1, 1, 3000.0), done(2, 2, 3001.0), done(3, 3, 3002.0), done(0, 4, 3003.0)]),
        ]
        result = run(records)
        self.assertEqual(result["players"][1]["streaks"]["podium"]["best"], [2, 0, 1])
        self.assertEqual(result["players"][0]["streaks"]["podium"]["current"], None)
        self.assertEqual(result["players"][3]["streaks"]["podium"]["best"], [1, 1, 1])

    def test_dnf_breaks_finish_streak_but_not_daily_streak(self):
        records = [
            duel(0, 1, day=0),
            record([dnf(0), done(1, 1, 3000.0)], day=1),
            duel(0, 1, day=2),
        ]
        streaks = run(records)["players"][0]["streaks"]
        self.assertEqual(streaks["finish"]["best"], [1, 0, 0])
        self.assertEqual(streaks["finish"]["current"], [1, 2, 2])
        self.assertEqual(streaks["daily"]["best"], [3, "2026-03-01", "2026-03-03"])
        self.assertEqual(streaks["daily"]["current"], [3, "2026-03-01", "2026-03-03"])

    def test_dq_breaks_finish_streak(self):
        records = [duel(0, 1), record([dq(0), done(1, 1, 3000.0)]), duel(0, 1)]
        self.assertEqual(run(records)["players"][0]["streaks"]["finish"]["best"], [1, 0, 0])

    def test_midnight_boundary_counts_as_consecutive_days(self):
        records = [duel(0, 1, day=0, hour=23), duel(0, 1, day=1, hour=0)]
        self.assertEqual(
            run(records)["players"][0]["streaks"]["daily"]["best"],
            [2, "2026-03-01", "2026-03-02"],
        )

    def test_gap_breaks_daily_streak_and_current_is_last_run(self):
        records = [duel(0, 1, day=day) for day in (0, 1, 2, 4, 5)]
        daily = run(records)["players"][0]["streaks"]["daily"]
        self.assertEqual(daily["best"], [3, "2026-03-01", "2026-03-03"])
        self.assertEqual(daily["current"], [2, "2026-03-05", "2026-03-06"])

    def test_several_races_on_one_day_count_once(self):
        records = [duel(0, 1, day=0), duel(0, 1, day=0), duel(0, 1, day=1)]
        self.assertEqual(run(records)["players"][0]["streaks"]["daily"]["best"][0], 2)

    def test_streak_boards_rank_by_length_then_earliest_end(self):
        records = [duel(0, 1), duel(0, 1), duel(2, 3), duel(2, 3)]
        boards = run(records)["records"]["streaks"]["win"]
        self.assertEqual(boards, {"best": [[0, 2, 0, 1], [2, 2, 2, 3]]})
        self.assertEqual(run(records)["records"]["streaks"]["daily"]["best"][0][1], 1)


def solo_finishes(count, **kwargs):
    """Races in which racer 0 finishes alone, one per day."""
    return [record([done(0, 1, 3000.0)], day=day, **kwargs) for day in range(count)]


def non_wins(count):
    return [duel(1, 0, day=day) for day in range(count)]


class BadgeTests(unittest.TestCase):
    def badges(self, records, player=0, **kwargs):
        return run(records, **kwargs)["players"][player]["badges"]

    def test_hat_trick(self):
        result = run([duel(0, 1), duel(0, 1), duel(0, 1)])
        self.assertEqual(result["players"][0]["badges"]["hat_trick"], 2)
        self.assertEqual(tags_of(result, 2, "badge"), [["badge", 0, "hat_trick"]])

    def test_hat_trick_needs_consecutive_wins(self):
        result = run([duel(0, 1), duel(0, 1), duel(1, 0), duel(0, 1)])
        self.assertNotIn("hat_trick", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["hat_trick"], 2)

    def test_ok_dude_lucksack_needs_nine_wins_in_a_row(self):
        self.assertNotIn("ok_dude_lucksack", self.badges([duel(0, 1)] * 8))
        self.assertEqual(self.badges([duel(0, 1)] * 9)["ok_dude_lucksack"], 8)

    def test_no_dodge_tiers_need_consecutive_finishes(self):
        self.assertNotIn("no_dodge", self.badges(solo_finishes(9)))
        self.assertEqual(self.badges(solo_finishes(10))["no_dodge"], 9)
        self.assertNotIn("more_no_dodge", self.badges(solo_finishes(39)))
        self.assertEqual(self.badges(solo_finishes(40))["more_no_dodge"], 39)
        self.assertNotIn("nice", self.badges(solo_finishes(68)))
        self.assertEqual(self.badges(solo_finishes(69))["nice"], 68)

    def test_a_dnf_breaks_the_no_dodge_run(self):
        records = solo_finishes(9) + [record([dnf(0), done(1, 1, 3000.0)])] + solo_finishes(9)
        result = run(records)
        self.assertNotIn("no_dodge", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["no_dodge"], 9)
        self.assertEqual(
            run(records + solo_finishes(1))["players"][0]["badges"]["no_dodge"], 19
        )

    def test_centurian_v_counts_finishes_but_not_dnfs(self):
        dnfs = [record([dnf(0), done(1, 1, 3000.0)]) for _ in range(5)]
        self.assertNotIn("centurian_v", self.badges(solo_finishes(104) + dnfs))
        result = run(solo_finishes(104) + dnfs + solo_finishes(1))
        self.assertEqual(result["players"][0]["badges"]["centurian_v"], 109)

    def test_centurian_v_progress_while_locked(self):
        self.assertEqual(run(solo_finishes(7))["players"][0]["progress"]["centurian_v"], 7)

    def test_blaze_it_needs_420_finishes(self):
        self.assertNotIn("blaze_it", self.badges(solo_finishes(419)))
        self.assertEqual(self.badges(solo_finishes(420))["blaze_it"], 419)

    def test_over_174_counts_every_race_not_won_dnfs_included(self):
        records = non_wins(173) + [record([dnf(0), done(1, 1, 3000.0)])]
        result = run(records)
        self.assertNotIn("over_174", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["over_174"], 174)
        self.assertEqual(self.badges(records + non_wins(1))["over_174"], 174)

    def test_over_174_does_not_count_wins(self):
        records = non_wins(173) + [duel(0, 1)] + non_wins(1)
        self.assertNotIn("over_174", self.badges(records))

    def test_omnivore_needs_a_finish_in_seven_modes(self):
        modes = [f"mode{i}" for i in range(8)]
        records = [record([done(0, 1, 3000.0)], mode=i) for i in range(7)]
        records.insert(3, record([dnf(0), done(1, 1, 3000.0)], mode=7))
        result = run(records[:7], modes=modes)
        self.assertNotIn("omnivore", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["omnivore"], 6)
        self.assertEqual(self.badges(records, modes=modes)["omnivore"], 7)

    def test_omnivore_target_is_capped_by_the_mode_count(self):
        records = [duel(0, 1, mode=0), record([dnf(0), done(1, 1, 3000.0)], mode=1)]
        result = run(records, mode_count=2)
        definition = next(b for b in result["badges"] if b["id"] == "omnivore")
        self.assertEqual(definition["target"], 2)
        self.assertNotIn("omnivore", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["omnivore"], 1)
        self.assertEqual(result["players"][1]["badges"]["omnivore"], 1)

    def test_sniper_wins_by_under_five_seconds(self):
        self.assertEqual(self.badges([duel(0, 1, margin=4.99)])["sniper"], 0)
        self.assertNotIn("sniper", self.badges([duel(0, 1, margin=5.0)]))

    def test_sniped_is_second_place_under_five_seconds_behind(self):
        self.assertEqual(self.badges([duel(0, 1, margin=4.99)], player=1)["sniped"], 0)
        self.assertNotIn("sniped", self.badges([duel(0, 1, margin=5.0)], player=1))

    def test_retime_needs_a_finish_under_a_second_from_another(self):
        race = record([done(0, 1, 3600.0), done(1, 2, 3600.99), done(2, 3, 3700.0)])
        self.assertEqual(self.badges([race], player=0)["retime"], 0)
        self.assertEqual(self.badges([race], player=1)["retime"], 0)
        self.assertNotIn("retime", self.badges([race], player=2))

    def test_retime_gap_of_exactly_one_second_is_not_enough(self):
        race = record([done(0, 1, 3600.0), done(1, 2, 3601.0)])
        self.assertNotIn("retime", self.badges([race], player=0))
        self.assertNotIn("retime", self.badges([race], player=1))

    def test_retime_ignores_a_dnf_neighbour(self):
        race = record([done(0, 1, 3600.0), dnf(1)])
        self.assertNotIn("retime", self.badges([race], player=0))
        self.assertNotIn("retime", self.badges([race], player=1))

    def test_retime_chains_through_the_middle_finisher(self):
        race = record([done(0, 1, 3600.0), done(1, 2, 3600.6), done(2, 3, 3601.2)])
        for player in range(3):
            self.assertEqual(self.badges([race], player=player)["retime"], 0)

    def test_palindrome_times(self):
        self.assertTrue(highlights.is_palindrome_time(5001))  # 1:23:21
        self.assertFalse(highlights.is_palindrome_time(741))  # 0:12:21 -> "01221"
        self.assertTrue(highlights.is_palindrome_time(5000.6))
        self.assertFalse(highlights.is_palindrome_time(5000.4))

    def test_palindrome_badge_uses_finish_time(self):
        records = [duel(0, 1, first_seconds=741.0, margin=100.0), duel(2, 3, first_seconds=5001.0, margin=100.0)]
        result = run(records)
        self.assertNotIn("palindrome", result["players"][0]["badges"])
        self.assertEqual(result["players"][2]["badges"]["palindrome"], 1)
        self.assertEqual(tags_of(result, 1, "badge"), [["badge", 2, "palindrome"]])

    def test_big_game_hunter_wins_a_field_of_sixteen(self):
        entries = [done(0, 1, 3000.0)] + [done(i, i + 1, 3000.0 + i) for i in range(1, 16)]
        result = run([record(entries), record(entries[:15])])
        self.assertEqual(result["players"][0]["badges"]["big_game_hunter"], 0)
        self.assertNotIn("big_game_hunter", result["players"][1]["badges"])
        self.assertEqual(result["records"]["big_field"], [[0, 0, 16]])

    def test_big_game_hunter_counts_anonymous_entrants(self):
        race = record([done(0, 1, 3000.0), done(1, 2, 3100.0)], n=16)
        self.assertEqual(run([race])["players"][0]["badges"]["big_game_hunter"], 0)

    def test_god_route_beats_the_top_rated_from_outside_the_top_ten(self):
        records = warmup([0, 2], 5) + [duel(1, 0, day=5)]
        result = run(records, means={0: 3.0})
        self.assertEqual(result["players"][1]["badges"]["god_route"], 5)
        self.assertEqual(tags_of(result, 5, "badge"), [["badge", 1, "god_route"]])

    def test_god_route_not_awarded_from_inside_the_top_ten(self):
        records = warmup([0, 1], 5) + [duel(1, 0, day=5)]
        result = run(records, means={0: 3.0, 1: 2.0})
        self.assertNotIn("god_route", result["players"][1]["badges"])

    def test_god_route_needs_a_finish_ahead_of_number_one(self):
        records = warmup([0, 2], 5) + [duel(0, 1, day=5)]
        result = run(records, means={0: 3.0})
        self.assertNotIn("god_route", result["players"][1]["badges"])

    def test_god_route_any_finisher_beats_a_non_finisher(self):
        records = warmup([0, 2], 5) + [record([dnf(0), done(1, 1, 3000.0)], day=5)]
        result = run(records, means={0: 3.0})
        self.assertEqual(result["players"][1]["badges"]["god_route"], 5)

    def test_god_route_ignores_inactive_number_one(self):
        late = 4 + highlights.RANKING_WINDOW_DAYS
        active = warmup([0, 2], 5) + [duel(1, 0, day=late)]
        inactive = warmup([0, 2], 5) + [duel(1, 0, day=late + 1)]
        self.assertIn("god_route", run(active, means={0: 3.0})["players"][1]["badges"])
        self.assertNotIn("god_route", run(inactive, means={0: 3.0})["players"][1]["badges"])

    def test_god_route_ignores_provisional_number_one(self):
        records = warmup([0, 2], 4) + [duel(1, 0, day=4)]
        result = run(records, means={0: 3.0})
        self.assertNotIn("god_route", result["players"][1]["badges"])

    def test_glitched_tiers_count_finishes_in_the_glitched_mode(self):
        glitched = [record([done(0, 1, 3000.0)], mode=1, day=day) for day in range(100)]
        records = [record([dnf(0), done(1, 1, 3000.0)], mode=1)] + solo_finishes(5) + glitched
        badges = self.badges(records, modes=GLITCHED_MODES)
        self.assertEqual(
            {id: ri for id, ri in badges.items() if id.startswith("glitched")},
            {"glitched": 6, "glitched_10": 15, "glitched_30": 35, "glitched_100": 105},
        )

    def test_glitched_ignores_other_modes(self):
        self.assertNotIn("glitched", self.badges(solo_finishes(3), modes=GLITCHED_MODES))

    def test_jet_seed_needs_a_casual_boots_finish_under_4200_seconds(self):
        def jet_seed(seconds, preset=0):
            records = [duel(0, 1, first_seconds=seconds, margin=100.0, preset=preset)]
            return "jet_seed" in self.badges(records, presets=CASUAL_BOOTS)

        self.assertTrue(jet_seed(4199.0))
        self.assertFalse(jet_seed(4200.0))
        self.assertFalse(jet_seed(4199.0, preset=1))
        self.assertFalse(jet_seed(4199.0, preset=None))

    def test_jet_lag_needs_a_slow_casual_boots_win(self):
        def jet_lag(seconds, player=0, preset=0):
            records = [duel(0, 1, first_seconds=seconds, margin=1000.0, preset=preset)]
            return "jet_lag" in self.badges(records, player=player, presets=CASUAL_BOOTS)

        self.assertTrue(jet_lag(6000.0))
        self.assertFalse(jet_lag(5999.0))
        self.assertFalse(jet_lag(6000.0, player=1))
        self.assertFalse(jet_lag(6000.0, preset=1))

    def test_entranced_counts_days_in_a_row_with_a_gap_resetting_the_run(self):
        def entranced(days):
            records = [record([done(0, 1, 3000.0)], mode=1, day=day) for day in days]
            return run(records, modes=ENTRANCE_MODES)["players"][0]

        player = entranced([0, 1, 2, 3, 5, 6])
        self.assertNotIn("entranced", player["badges"])
        self.assertEqual(player["progress"]["entranced"], 4)
        self.assertEqual(entranced([0, 1, 2, 3, 4])["badges"]["entranced"], 4)

    def test_entranced_counts_a_day_once_and_counts_a_dnf(self):
        records = [
            record([done(0, 1, 3000.0)], mode=1, day=0),
            record([done(0, 1, 3000.0)], mode=1, day=0),
            record([dnf(0), done(1, 1, 3000.0)], mode=1, day=1),
            record([done(0, 1, 3000.0)], mode=1, day=2),
            record([done(0, 1, 3000.0)], mode=1, day=3),
        ]
        result = run(records, modes=ENTRANCE_MODES)
        self.assertEqual(result["players"][0]["progress"]["entranced"], 4)
        records.append(record([dnf(0), done(1, 1, 3000.0)], mode=1, day=4))
        self.assertEqual(self.badges(records, modes=ENTRANCE_MODES)["entranced"], 5)

    def test_entranced_ignores_other_modes(self):
        self.assertNotIn("entranced", self.badges(solo_finishes(5), modes=ENTRANCE_MODES))

    def test_entranced_weeks_run_across_the_sunday_to_monday_boundary(self):
        def weeks(days):
            records = [record([done(0, 1, 3000.0)], mode=1, day=day) for day in days]
            return run(records, modes=ENTRANCE_MODES)["players"][0]["progress"]["entranced_weeks"]

        # DAY_ONE is a Sunday, so day 1 is the Monday that starts the next week.
        self.assertEqual(weeks([0, 1]), 2)
        self.assertEqual(weeks([1, 7]), 1)
        self.assertEqual(weeks([1, 8]), 2)
        self.assertEqual(weeks([1, 15]), 1)

    def test_entranced_weeks_needs_twenty_weeks_in_a_row(self):
        def badges(count):
            records = [
                record([done(0, 1, 3000.0)], mode=1, day=1 + 7 * week) for week in range(count)
            ]
            return self.badges(records, modes=ENTRANCE_MODES)

        self.assertNotIn("entranced_weeks", badges(19))
        self.assertEqual(badges(20)["entranced_weeks"], 19)

    def test_both_ends_unlocks_on_the_race_that_completes_the_pair(self):
        for first, second in ((1799.0, 7801.0), (7801.0, 1799.0)):
            records = [
                record([done(0, 1, first)], mode=1, day=0),
                record([done(0, 1, second)], mode=1, day=1),
            ]
            with self.subTest(first=first):
                self.assertEqual(self.badges(records, modes=MYSTERY_MODES)["both_ends"], 1)
                self.assertNotIn("both_ends", self.badges(records[:1], modes=MYSTERY_MODES))

    def test_both_ends_needs_times_beyond_the_limits(self):
        for fast, slow in ((1800.0, 7801.0), (1799.0, 7800.0)):
            records = [
                record([done(0, 1, fast)], mode=1, day=0),
                record([done(0, 1, slow)], mode=1, day=1),
            ]
            with self.subTest(fast=fast, slow=slow):
                self.assertNotIn("both_ends", self.badges(records, modes=MYSTERY_MODES))

    def test_both_ends_ignores_other_modes(self):
        records = [record([done(0, 1, 1799.0)], mode=1), record([done(0, 1, 7801.0)], mode=0)]
        self.assertNotIn("both_ends", self.badges(records, modes=MYSTERY_MODES))

    def test_grocery_stick_is_the_exact_middle_of_an_odd_field(self):
        five = record([done(p, p + 1, 3000.0 + p) for p in range(5)])
        result = run([five])
        self.assertEqual(result["players"][2]["badges"]["grocery_stick"], 0)
        for player in (0, 1, 3, 4):
            self.assertNotIn("grocery_stick", result["players"][player]["badges"])

    def test_grocery_stick_needs_an_odd_field_of_five_or_more(self):
        six = record([done(p, p + 1, 3000.0 + p) for p in range(6)])
        three = record([done(p, p + 1, 3000.0 + p) for p in range(3)])
        for race in (six, three):
            result = run([race])
            for player in range(6):
                self.assertNotIn("grocery_stick", result["players"][player]["badges"])

    def test_grocery_stick_counts_a_dnf_in_the_field(self):
        race = record([done(p, p + 1, 3000.0 + p) for p in range(4)] + [dnf(4)])
        self.assertEqual(run([race])["players"][2]["badges"]["grocery_stick"], 0)

    def test_grocery_stick_counts_anonymous_entrants(self):
        race = record([done(0, 1, 3000.0), done(1, 4, 3100.0)], n=7)
        self.assertEqual(run([race])["players"][1]["badges"]["grocery_stick"], 0)

    def test_rando_sicko_counts_five_finishes_on_one_day(self):
        def day(finishes):
            return [record([done(0, 1, 3000.0)], hour=h) for h in range(finishes)]

        self.assertNotIn("rando_sicko", self.badges(day(4)))
        self.assertEqual(self.badges(day(5))["rando_sicko"], 4)
        self.assertEqual(run(day(4))["players"][0]["progress"]["rando_sicko"], 4)

    def test_rando_sicko_does_not_count_a_dnf_or_another_day(self):
        records = [record([done(0, 1, 3000.0)], hour=h) for h in range(4)]
        records.append(record([dnf(0), done(1, 1, 3000.0)], hour=4))
        records.append(record([done(0, 1, 3000.0)], day=1))
        result = run(records)
        self.assertNotIn("rando_sicko", result["players"][0]["badges"])
        self.assertEqual(result["players"][0]["progress"]["rando_sicko"], 4)

    def test_step_letter_is_alphabetical_in_place_order_ignoring_case(self):
        names = {0: "alice", 1: "Bob", 2: "carol", 3: "Dave", 4: "eve"}
        race = record([done(p, p + 1, 3000.0 + p) for p in range(5)])
        result = run([race], names=names)
        for player in range(5):
            self.assertEqual(result["players"][player]["badges"]["step_letter"], 0)
        shuffled = run([race], names={**names, 0: "zed"})
        self.assertNotIn("step_letter", shuffled["players"][0]["badges"])

    def test_step_letter_needs_every_entrant_to_finish_and_be_identified(self):
        names = {p: "abcde"[p] for p in range(5)}
        with_dnf = record([done(p, p + 1, 3000.0 + p) for p in range(4)] + [dnf(4)])
        anonymous = record([done(p, p + 1, 3000.0 + p) for p in range(5)], n=6)
        for race in (with_dnf, anonymous):
            result = run([race], names=names)
            for player in range(5):
                self.assertNotIn("step_letter", result["players"][player]["badges"])

    def test_step_letter_needs_a_field_of_five(self):
        names = {p: "abcde"[p] for p in range(5)}
        race = record([done(p, p + 1, 3000.0 + p) for p in range(4)])
        self.assertNotIn("step_letter", run([race], names=names)["players"][0]["badges"])

    def test_badge_table_order(self):
        ids = [badge["id"] for badge in run([])["badges"]]
        self.assertEqual(
            ids,
            [
                "first_blood",
                "hat_trick",
                "ok_dude_lucksack",
                "no_dodge",
                "more_no_dodge",
                "nice",
                "centurian_v",
                "blaze_it",
                "over_174",
                "omnivore",
                "sniper",
                "sniped",
                "retime",
                "palindrome",
                "god_route",
                "big_game_hunter",
                "glitched",
                "glitched_10",
                "glitched_30",
                "glitched_100",
                "jet_seed",
                "jet_lag",
                "entranced",
                "entranced_weeks",
                "both_ends",
                "grocery_stick",
                "rando_sicko",
                "step_letter",
            ],
        )

    def test_badge_ids_are_unique_snake_case(self):
        ids = [badge.id for badge in highlights.BADGES]
        self.assertEqual(len(ids), len(set(ids)))
        for badge_id in ids:
            self.assertRegex(badge_id, r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

    def test_each_badge_has_exactly_one_rule(self):
        for badge in highlights.BADGES:
            with self.subTest(badge=badge.id):
                self.assertNotEqual(badge.progress is None, badge.earned is None)

    def test_badge_definitions_carry_image_paths(self):
        for definition in run([])["badges"]:
            image, still = highlights.badge_image_paths(definition["id"])
            self.assertEqual(definition["image"], image)
            self.assertEqual(definition["still"], still)
        self.assertEqual(
            highlights.badge_image_paths("hat_trick"),
            ("assets/badges/hat_trick.webp", "assets/badges/hat_trick-still.webp"),
        )

    def test_badge_unlocked_once(self):
        result = run([duel(0, 1, margin=1.0), duel(0, 1, margin=1.0)])
        self.assertEqual(result["players"][0]["badges"]["sniper"], 0)
        self.assertEqual(tags_of(result, 1, "badge"), [])


class UpsetTests(unittest.TestCase):
    def upset_race(self, finishers=(1, 0, 2), day=5):
        return record(
            [done(p, place + 1, 3000.0 + 100 * place) for place, p in enumerate(finishers)],
            day=day,
        )

    def test_low_chance_win_over_the_favourite_is_an_upset(self):
        records = warmup([0, 1, 2], 5) + [self.upset_race()]
        result = run(records, means={0: 2.0})
        self.assertEqual(tags_of(result, 5, "upset"), [["upset", 1, 0, 11.9]])
        self.assertEqual(result["diagnostics"]["upset_share"], round(1 / 6, 4))

    def test_chance_above_threshold_is_not_an_upset(self):
        records = warmup([0, 1, 2], 5) + [self.upset_race()]
        result = run(records, means={0: 1.0})
        self.assertEqual(tags_of(result, 5, "upset"), [])

    def test_favourite_winning_is_not_an_upset(self):
        records = warmup([0, 1, 2], 5) + [self.upset_race((0, 1, 2))]
        self.assertEqual(tags_of(run(records, means={0: 2.0}), 5, "upset"), [])

    def test_provisional_favourite_suppresses_the_upset(self):
        records = warmup([0, 1, 2], 4) + warmup([1, 2], 1) + [self.upset_race(day=6)]
        # Racer 0 has 4 finishes (provisional); racers 1 and 2 are the candidates.
        result = run(records, means={0: 2.0})
        self.assertEqual(tags_of(result, 5, "upset"), [])

    def test_provisional_favourite_is_skipped_for_the_next_best(self):
        records = warmup([0, 1, 2], 4) + warmup([1, 2], 1) + [self.upset_race((2, 0, 1), day=6)]
        result = run(records, means={0: 3.0, 1: 2.0})
        self.assertEqual(tags_of(result, 5, "upset"), [["upset", 2, 1, 11.9]])

    def test_needs_three_identified_entrants(self):
        records = warmup([0, 1], 5) + [duel(1, 0, day=5)]
        self.assertEqual(tags_of(run(records, means={0: 2.0}), 5, "upset"), [])

    def test_tag_order_within_a_race(self):
        records = warmup([0, 1, 2], 5)
        records.append(
            record(
                [done(1, 1, 2000.0), done(0, 2, 2001.0), done(2, 3, 3000.0)],
                day=5,
            )
        )
        kinds = [tag[0] for tag in run(records, means={0: 2.0})["race_tags"][5]]
        self.assertEqual(kinds, ["record", "first_win", "upset", "photo", "pb", "pb", "pb", "badge", "badge"])


class PresetTests(unittest.TestCase):
    presets = ({"m": 0, "n": "Easy"}, {"m": 0, "n": "Hard"})

    def races(self):
        records = []
        for i in range(8):
            records.append(
                record(
                    [done(0, 1, 1000.0 + i), done(1, 2, 2000.0 + i), dnf(2)],
                    preset=0,
                    day=i,
                )
            )
            records.append(
                record(
                    [done(0, 1, 3000.0 + i), done(1, 2, 3500.0 + i), dnf(2), dnf(3)],
                    preset=1,
                    day=i,
                    n=5,
                )
            )
        return records

    def test_preset_stats(self):
        stats = run(self.races(), presets=self.presets)["preset_stats"]
        self.assertEqual(stats[0]["races"], 8)
        self.assertEqual(stats[0]["entries"], 24)
        self.assertEqual(stats[0]["dnf_rate"], round(8 / 24, 4))
        self.assertEqual(stats[0]["median_win_seconds"], 1004)
        self.assertEqual(stats[0]["mean_field"], 3.0)
        self.assertEqual(stats[0]["best"], [0, 1000, 0])
        self.assertEqual(stats[1]["dnf_rate"], 0.5)
        self.assertEqual(stats[1]["mean_field"], 5.0)

    def test_median_seconds_is_over_timed_finishes(self):
        stats = run(self.races(), presets=self.presets)["preset_stats"]
        self.assertEqual(stats[0]["median_seconds"], 1504)

    def test_hardest_preset_has_the_highest_dnf_rate(self):
        self.assertEqual(run(self.races(), presets=self.presets)["hardest_preset"], 1)

    def test_hardest_preset_needs_eight_races(self):
        records = [r for r in self.races() if r["p"] == 0] + [r for r in self.races() if r["p"] == 1][:7]
        self.assertEqual(run(records, presets=self.presets)["hardest_preset"], 0)

    def test_no_hardest_preset_without_enough_races(self):
        self.assertIsNone(run(self.races()[:4], presets=self.presets)["hardest_preset"])

    def test_best_preset_is_lowest_ratio_to_the_median(self):
        players = run(self.races(), presets=self.presets)["players"]
        self.assertEqual(players[0]["best_preset"][0], 0)
        self.assertLess(players[0]["best_preset"][1], 1)
        self.assertEqual(players[1]["best_preset"][0], 1)
        self.assertEqual(players[2]["best_preset"], None)

    def test_best_preset_needs_three_timed_finishes(self):
        sparse = [
            record([done(0, 1, 1000.0), done(1, 2, 1100.0)], preset=0, day=day)
            if day < 2
            else record([done(1, 1, 1000.0), dnf(0)], preset=0, day=day)
            for day in range(8)
        ]
        players = run(sparse, presets=self.presets)["players"]
        self.assertIsNone(players[0]["best_preset"])
        self.assertIsNotNone(players[1]["best_preset"])

    def test_races_without_preset_are_ignored(self):
        stats = run([duel(0, 1)], presets=self.presets)["preset_stats"]
        self.assertEqual(stats[0]["races"], 0)
        self.assertIsNone(stats[0]["median_seconds"])
        self.assertIsNone(stats[0]["best"])


class RecordBoardTests(unittest.TestCase):
    def test_photo_board_is_smallest_margin_first(self):
        records = [duel(0, 1, margin=3.0), duel(2, 3, margin=0.5), duel(4, 5, margin=9.0)]
        self.assertEqual(
            run(records)["records"]["photo"], [[1, 2, 3, 0.5], [0, 0, 1, 3.0]]
        )

    def test_blowout_board_is_largest_margin_first(self):
        records = [duel(0, 1, margin=3.0), duel(2, 3, margin=900.0), duel(4, 5, margin=90.0)]
        self.assertEqual(
            run(records)["records"]["blowouts"],
            [[1, 2, 3, 900], [2, 4, 5, 90], [0, 0, 1, 3]],
        )

    def test_boards_are_capped_at_ten(self):
        records = [duel(0, 1, margin=1.0 + i / 10) for i in range(15)]
        result = run(records)["records"]
        self.assertEqual(len(result["photo"]), 10)
        self.assertEqual(len(result["blowouts"]), 10)

    def test_most_races_board(self):
        records = [duel(0, 1), duel(0, 2), duel(0, 1)]
        self.assertEqual(run(records)["records"]["most_races"], [[0, 3], [1, 2], [2, 1]])


class OutputTests(unittest.TestCase):
    def test_empty_input(self):
        result = run([])
        self.assertEqual(result["race_tags"], [])
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["diagnostics"]["racer_count"], 0)
        self.assertEqual(result["diagnostics"]["upset_share"], 0.0)

    def test_race_tags_align_with_records_and_players_with_player_list(self):
        result = run([duel(0, 1), duel(2, 3)])
        self.assertEqual(len(result["race_tags"]), 2)
        self.assertEqual(len(result["players"]), 40)
        self.assertEqual(result["players"][39]["streaks"]["daily"]["best"], None)

    def test_diagnostics_count_tags_and_badge_holders(self):
        result = run([duel(0, 1, margin=1.0)])
        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics["tag_counts"]["photo"], 1)
        self.assertEqual(diagnostics["tag_counts"]["first_win"], 1)
        self.assertEqual(diagnostics["badge_holders"]["sniper"], 1)
        self.assertEqual(diagnostics["badge_holders"]["sniped"], 1)
        self.assertEqual(diagnostics["badge_holders"]["first_blood"], 1)
        self.assertEqual(diagnostics["racer_count"], 2)

    def test_facts_are_deterministic_sentences_with_numbers(self):
        records = [duel(0, 1, margin=0.5, day=i, first_seconds=5001.0) for i in range(3)]
        first = run(records)["facts"]
        self.assertEqual(first, run(records)["facts"])
        self.assertGreaterEqual(len(first), 8)
        for fact in first:
            self.assertTrue(fact.endswith("."), fact)
            self.assertTrue(any(char.isdigit() for char in fact), fact)
        self.assertIn("The closest photo finish was 0.50 seconds, with P0 edging out P1.", first)
        self.assertIn("The mode0 record is 1:23:21, held by P0.", first)
        self.assertIn("P0 raced on 3 days in a row, from 2026-03-01 to 2026-03-03.", first)
        self.assertIn("3 finish times read the same both ways, like 1:23:21.", first)

    def test_format_duration(self):
        self.assertEqual(highlights.format_duration(5001), "1:23:21")
        self.assertEqual(highlights.format_duration(59.6), "0:01:00")


if __name__ == "__main__":
    unittest.main()
