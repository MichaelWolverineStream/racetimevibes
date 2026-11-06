import itertools
import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest import mock

from schedule_fixtures import (
    BASE_COMPOSITION,
    EASTERN,
    EVENTS,
    LAYOUT,
    MODES,
    NEWEST,
    PAIRS,
    compose,
    fitted,
    flat_model,
    history,
    between_result,
    main_result,
    mixed_category,
    mixed_result,
    limit_result,
    mode_names,
    newest_race,
    plain_category,
    race,
    slot,
    world_history,
)
import schedule_fixtures as fx
from scripts import race_archive, schedule_archive, schedule_planner as sp


class JoinTests(unittest.TestCase):
    def join(self, slots, races):
        return sp.history_slots(slots, MODES, [newest_race(), *races])[0]

    def test_seed_id_join_uses_the_races_own_mode_and_entrants(self):
        at = NEWEST - timedelta(days=5)
        slots = self.join([slot(1, 42, at, 11)], [race(42, "entrance", at, 7)])
        self.assertEqual(len(slots), 1)
        self.assertTrue(slots[0].fired)
        self.assertEqual((slots[0].category, slots[0].entrants, slots[0].mode_id), ("entrance", 7, 11))

    def test_time_fallback_window_is_minus_five_to_plus_thirty_minutes(self):
        at = NEWEST - timedelta(days=5)
        for minutes, fired in ((-5, True), (-6, False), (30, True), (31, False)):
            with self.subTest(minutes=minutes):
                other = race(1, "simple", at + timedelta(minutes=minutes), 4, seeded=False)
                (joined,) = self.join([slot(1, 500, at, 11)], [other])
                self.assertEqual(joined.fired, fired)
                self.assertEqual(joined.entrants, 4 if fired else 0)

    def test_time_fallback_needs_the_same_category(self):
        at = NEWEST - timedelta(days=5)
        other = race(1, "entrance", at, 4, seeded=False)
        (joined,) = self.join([slot(1, 500, at, 11)], [other])
        self.assertFalse(joined.fired)

    def test_a_race_matches_only_one_slot(self):
        at = NEWEST - timedelta(days=5)
        other = race(1, "simple", at + timedelta(minutes=5), 4, seeded=False)
        slots = self.join(
            [slot(1, 501, at, 11), slot(2, 502, at + timedelta(minutes=10), 11)], [other]
        )
        self.assertEqual(sorted(s.fired for s in slots), [False, True])

    def test_cancelled_slot_counts_zero_in_its_archetype_category(self):
        at = NEWEST - timedelta(days=5)
        (joined,) = self.join([slot(1, 999, at, 41)], [])
        self.assertEqual((joined.fired, joined.entrants, joined.category), (False, 0, "keys"))

    def test_pulled_slot_is_dropped(self):
        at = NEWEST - timedelta(days=5)
        control = slot(2, 999, at, 11)
        self.assertEqual(self.join([slot(1, None, at, 11), control], []), self.join([control], []))
        self.assertEqual(len(self.join([control], [])), 1)

    def test_ladder_test_slot_is_dropped(self):
        at = NEWEST - timedelta(days=5)
        control = slot(2, 999, at, 11)
        joined = self.join([slot(1, 77, at, 11), control], [race(77, "ladder_test", at)])
        self.assertEqual(len(joined), 1)
        self.assertFalse(joined[0].fired)

    def test_unknown_mode_id_raises(self):
        at = NEWEST - timedelta(days=5)
        with self.assertRaises(sp.ScheduleError):
            self.join([slot(1, 999, at, 12345)], [])

    def test_cancelled_slot_of_unmapped_archetype_raises_but_a_fired_one_does_not(self):
        at = NEWEST - timedelta(days=5)
        with self.assertRaises(sp.ScheduleError):
            self.join([slot(1, 999, at, 80)], [])
        (joined,) = self.join([slot(1, 42, at, 80)], [race(42, "fun", at, 6)])
        self.assertEqual(joined.category, "fun")

    def test_window_is_365_days_ending_a_day_before_the_newest_race(self):
        end = NEWEST - timedelta(days=1)
        start = end - timedelta(days=365)
        rows = [
            slot(1, 901, end - timedelta(hours=1), 11),
            slot(2, 902, NEWEST - timedelta(hours=12), 11),
            slot(3, 903, start - timedelta(hours=1), 11),
            slot(4, 904, start + timedelta(hours=1), 11),
        ]
        slots, window_start, window_end = sp.history_slots(rows, MODES, [newest_race()])
        self.assertEqual((window_start, window_end), (start, end))
        self.assertEqual(
            sorted(s.started_at for s in slots),
            [start + timedelta(hours=1), end - timedelta(hours=1)],
        )



def monday_slots(entrants, count, *, mode_id=1, category="simple", hour=14, skip_holidays=True):
    """`count` fired slots on ordinary Mondays of 2026 at an Eastern hour."""
    slots, day = [], date(2026, 1, 5)
    while len(slots) < count:
        if not (skip_holidays and sp.is_holiday(day)):
            local = datetime(day.year, day.month, day.day, hour, tzinfo=EASTERN)
            slots.append((category, mode_id, local, entrants, True))
        day += timedelta(days=7)
    return slots


MEMORIAL_DAY = datetime(2026, 5, 25, 14, tzinfo=EASTERN)


class ModelTests(unittest.TestCase):
    def test_a_mode_with_more_entrants_gets_the_higher_factor_and_shrinks_toward_one(self):
        slots = []
        for week in range(40):
            local = datetime(2026, 5, 4, 14, tzinfo=EASTERN) + timedelta(weeks=week)
            if week < 10:
                slots.append(("simple", 1, local, 6, True))
            else:
                slots.append(("simple", 2, local, 2, True))
        model = sp.fit_model(history(slots))
        self.assertGreater(model.mode[1], 1.0)
        self.assertLess(model.mode[2], 1.0)
        # Shrinkage (n * r + 20) / (n + 20) keeps a factor between its prior and its ratio.
        self.assertLess(model.mode[1], 6 / 2.0 + 1)
        self.assertGreater(model.mode[1], model.mode[2])
        self.assertEqual(model.mode_slots[1], 10)
        self.assertEqual(model.mode_slots[2], 30)

    def test_a_mode_with_slots_everywhere_equal_to_the_average_stays_at_one(self):
        slots = monday_slots(4, 40)
        model = sp.fit_model(history(slots))
        self.assertAlmostEqual(model.mode[1], 1.0, places=9)

    def test_fill_is_the_fired_share_per_block_with_the_overall_share_as_fallback(self):
        slots = [
            ("simple", 1, datetime(2026, 5, 4 + 7 * k, 14, tzinfo=EASTERN), 4, k != 0)
            for k in range(4)
        ]
        slots += [
            ("simple", 1, datetime(2026, 5, 4 + 7 * k, 21, tzinfo=EASTERN), 4, k != 0)
            for k in range(2)
        ]
        model = sp.fit_model(history(slots))
        self.assertAlmostEqual(model.fill["simple"][3], 3 / 4)
        self.assertAlmostEqual(model.fill["simple"][5], 1 / 2)
        for empty in (0, 1, 2, 4):
            self.assertAlmostEqual(model.fill["simple"][empty], 4 / 6)

    def test_hour_is_a_dict_of_24_factors_per_group(self):
        model = fitted()
        self.assertEqual(set(model.hour), set(sp.DAY_GROUPS))
        for group in sp.DAY_GROUPS:
            self.assertEqual(len(model.hour[group]), 24)

    def test_each_blocks_twelve_group_hours_average_one(self):
        for model in (
            fitted(),
            sp.fit_model(history(monday_slots(9, 30) + [("simple", 1, MEMORIAL_DAY, 1, True)] * 30)),
        ):
            for block in range(6):
                hours = range(block * 4, block * 4 + 4)
                mean = sum(model.hour[g][h] for g in sp.DAY_GROUPS for h in hours) / 12
                self.assertAlmostEqual(mean, 1.0, places=9)

    def test_a_holiday_slot_counts_in_the_prime_group(self):
        slots = monday_slots(2, 30) + [("simple", 1, MEMORIAL_DAY, 10, True)] * 30
        with_holidays = sp.fit_model(history(slots))
        self.assertTrue(with_holidays.holidays)
        # The thirty holiday slots drew five times the ordinary Mondays, and sit in prime.
        self.assertGreater(with_holidays.hour["prime"][14], 1.2 * with_holidays.hour["work"][14])
        self.assertLess(with_holidays.hour["work"][14], 1.0)

    def test_without_holidays_a_holiday_slot_counts_in_its_weekday_group(self):
        slots = monday_slots(2, 30) + [("simple", 1, MEMORIAL_DAY, 10, True)] * 30
        plain = sp.fit_model(history(slots), holidays=False)
        self.assertFalse(plain.holidays)
        # Everything is in the work group, so the single category's block explains it all.
        for group in sp.DAY_GROUPS:
            self.assertEqual(plain.hour[group][14], plain.hour["work"][14])
        self.assertAlmostEqual(plain.hour["work"][14], 1.0, places=9)

    def test_unseen_group_hours_and_modes_are_one(self):
        slots = monday_slots(2, 30) + [("simple", 1, MEMORIAL_DAY, 10, True)] * 30
        model = sp.fit_model(history(slots))
        for hour in range(12):
            self.assertEqual(model.hour["casual"][hour], 1.0)
        for hour in range(16, 24):
            self.assertEqual(model.hour["casual"][hour], 1.0)
        self.assertNotIn(99, model.mode)
        self.assertEqual(model.mode_slots[99], 0)

    def test_group_hours_are_smoothed_around_the_clock(self):
        # Hour 23 draws more than average and hour 21 less: hour 0 borrows from hour 23 across
        # midnight, while hour 1 sees only unseen neighbours.
        slots = monday_slots(10, 30, hour=23) + monday_slots(2, 30, hour=21)
        model = sp.fit_model(history(slots))
        self.assertGreater(model.hour["work"][0], model.hour["work"][1])

    def test_group_follows_the_models_holiday_switch(self):
        labor_day = date(2026, 9, 7)
        self.assertEqual(flat_model(["a"]).group(labor_day), "prime")
        self.assertEqual(flat_model(["a"], holidays=False).group(labor_day), "work")
        self.assertEqual(flat_model(["a"]).group(date(2026, 9, 8)), "work")


class CategoryTests(unittest.TestCase):
    def test_sources_follow_the_modes_in_list_order_without_repeats(self):
        category = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50), ("keys", 2.0, 50))
        self.assertEqual(category.sources, ("keys", "mystery"))
        self.assertTrue(category.mixed)
        single = mixed_category("s", ("keys", 1.0, 50), ("keys", 2.0, 50))
        self.assertEqual(single.sources, ("keys",))
        self.assertFalse(single.mixed)

    def test_expected_entrants_are_the_mean_over_the_modes_at_their_own_sources(self):
        model = flat_model(["keys", "mystery"], {"keys": 2.0, "mystery": 6.0})
        category = mixed_category("t", ("keys", 1.5, 50), ("mystery", 0.5, 50))
        self.assertAlmostEqual(category.at(model, "work", 13), (2.0 * 1.5 + 6.0 * 0.5) / 2)
        local = datetime(2026, 9, 2, 13, tzinfo=EASTERN)
        self.assertAlmostEqual(category.expected(model, local), category.at(model, "work", 13))
        one = plain_category("k", 1.5, source="keys")
        self.assertAlmostEqual(one.at(model, "prime", 0), 3.0)

    def test_expected_entrants_use_the_block_the_groups_hour_factor_and_the_mode(self):
        model = sp.Model(
            block={"keys": [1.0, 1.0, 1.0, 2.0, 1.0, 1.0]},
            hour={
                "work": [1.0] * 24,
                "casual": [1.0] * 24,
                "prime": [1.0] * 13 + [1.5] + [1.0] * 10,
            },
            mode={}, mode_slots=Counter(), fill={},
        )
        category = plain_category("k", 2.0, source="keys")
        # Block 3 (hours 12 to 15) is 2 and the mode is 2; hour 13 is 1.5 in the prime group.
        self.assertAlmostEqual(category.at(model, "prime", 13), 2.0 * 1.5 * 2.0)
        self.assertAlmostEqual(category.at(model, "work", 13), 2.0 * 1.0 * 2.0)
        self.assertAlmostEqual(category.at(model, "prime", 20), 1.0 * 1.0 * 2.0)

    def test_expected_entrants_use_the_dates_own_group_and_its_holiday_switch(self):
        hours = {"work": [1.0] * 24, "casual": [2.0] * 24, "prime": [3.0] * 24}
        model = replace(flat_model(["keys"]), hour=hours)
        category = plain_category("k", source="keys")
        at = lambda day: datetime(2026, 9, day, 13, tzinfo=EASTERN)  # noqa: E731
        self.assertEqual(category.expected(model, at(2)), 1.0)   # Wednesday
        self.assertEqual(category.expected(model, at(3)), 2.0)   # Thursday
        self.assertEqual(category.expected(model, at(5)), 3.0)   # Saturday
        self.assertEqual(category.expected(model, at(7)), 3.0)   # Labor Day, a Monday
        plain = replace(model, holidays=False)
        self.assertEqual(category.expected(plain, at(7)), 1.0)
        self.assertEqual(model.expected("keys", 1.0, at(7)), 3.0)

    def test_the_favourite_mode_needs_enough_slots_then_the_best_factor_then_plan_order(self):
        # E Hype has the best factor but too few slots to count.
        category = mixed_category("e", ("entrance", 1.0, 50), ("entrance", 4.0, 50),
                                  ("entrance", 5.0, sp.MIN_MODE_SLOTS - 1),
                                  ("entrance", 3.0, sp.MIN_MODE_SLOTS))
        self.assertEqual(category.favourite_mode.id, 1)
        # With every mode under the limit, the factor still ranks them.
        thin = mixed_category("e", ("entrance", 1.0, 3), ("entrance", 5.0, 2))
        self.assertEqual(thin.favourite_mode.id, 1)
        # Ties on both go to the earlier mode.
        tied = mixed_category("e", ("entrance", 2.0, 50), ("entrance", 2.0, 50))
        self.assertEqual(tied.favourite_mode.id, 0)

    def test_exactly_the_minimum_of_slots_counts(self):
        category = mixed_category("e", ("entrance", 9.0, sp.MIN_MODE_SLOTS - 1),
                                  ("entrance", 1.0, sp.MIN_MODE_SLOTS))
        self.assertEqual(category.favourite_mode.id, 1)

    def test_a_mixed_categorys_favourite_mode_still_ranks_among_all_its_modes(self):
        category = mixed_category("t", ("keys", 1.0, 50), ("mystery", 2.0, 50))
        self.assertEqual(category.favourite_mode.source, "mystery")


class ConfigTests(unittest.TestCase):
    def one(self, entry, name="entrance"):
        (category,), left_out = compose({name: entry})
        self.assertEqual(left_out, [])
        return category

    def rejected(self, composition, *fragments):
        with self.assertRaises(sp.ScheduleError) as caught:
            compose(composition)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_categories_come_out_in_plan_order_with_their_modes_in_list_order(self):
        chosen, left_out = compose({
            "keys": {"modes": ["K One"]},
            "entrance": {"modes": ["Crosskeys 2024", "Crosskeys"]},
        })
        self.assertEqual([c.name for c in chosen], ["keys", "entrance"])
        self.assertEqual(mode_names(chosen[1]), ["Crosskeys 2024", "Crosskeys"])
        self.assertEqual(left_out, [])

    def test_a_mode_carries_its_factor_slots_source_and_retired_flag(self):
        category = self.one({"modes": ["Crosskeys", "Old Entrance", "Crosskeys 2024"]})
        first, old, newer = category.modes
        self.assertEqual((first.id, first.factor, first.slots, first.source, first.retired),
                         (21, 1.5, 50, "entrance", False))
        # A mode the model never saw draws like its category: factor 1, no slots.
        self.assertEqual((old.factor, old.slots, old.retired), (1.0, 40, True))
        self.assertEqual((newer.factor, newer.slots), (2.0, 7))
        self.assertEqual(self.one({"modes": ["Old Entrance"]}).modes[0].factor, 1.0)

    def test_the_source_is_the_category_of_the_modes_archetype(self):
        sources = {"simple": "S One", "entrance": "Crosskeys", "keys": "K One",
                   "glitched": "HMG [VT]", "mystery": "M One"}
        for source, name in sources.items():
            with self.subTest(source):
                self.assertEqual(self.one({"modes": [name]}, name="x").modes[0].source, source)

    def test_the_allowed_keys_are_modes_the_limits_and_the_two_flags(self):
        category = self.one({"modes": ["Crosskeys"], "dailyMax": 2, "weeklyMax": 9,
                             "onlyPrimeTime": True})
        self.assertEqual((category.daily_max, category.weekly_max, category.only_prime, category.between),
                         (2, 9, True, False))
        plain = self.one({"modes": ["Crosskeys"]})
        self.assertEqual((plain.daily_max, plain.weekly_max, plain.only_prime, plain.between),
                         (None, None, False, False))
        self.assertEqual(set(sp.CATEGORY_KEYS), {"modes", "dailyMax", "weeklyMax", "onlyPrimeTime", "between"})
        self.rejected({"entrance": {"modes": ["Crosskeys"], "optional": True}}, "entrance")
        self.rejected({"entrance": {"modes": ["Crosskeys"], "like": "simple"}}, "entrance")

    def test_modes_are_required_and_must_be_a_list_of_names(self):
        self.rejected({"entrance": {}}, "entrance", "modes")
        self.rejected({"entrance": {"dailyMax": 2}}, "modes")
        for bad in (2, "Crosskeys", None, {"a": 1}):
            with self.subTest(bad=bad):
                self.rejected({"entrance": {"modes": bad}}, "modes must be a list")
        for bad in (["Crosskeys", 5], [None], [""]):
            with self.subTest(bad=bad):
                self.rejected({"entrance": {"modes": bad}}, "modes must be a list")
        self.rejected({"entrance": ["Crosskeys"]}, "entrance")

    def test_limits_are_whole_numbers_of_at_least_one_and_booleans_are_not_numbers(self):
        for key in ("dailyMax", "weeklyMax"):
            for bad in (0, -1, 1.5, True, False, "2", None):
                with self.subTest(key=key, bad=bad):
                    self.rejected({"entrance": {"modes": ["Crosskeys"], key: bad}}, key)
            for good in (1, 3, 40):
                with self.subTest(key=key, good=good):
                    category = self.one({"modes": ["Crosskeys"], key: good})
                    self.assertEqual(getattr(category, "daily_max" if key == "dailyMax" else "weekly_max"), good)

    def test_only_prime_time_must_be_a_boolean(self):
        for bad in ("yes", 1, 0, None):
            with self.subTest(bad=bad):
                self.rejected({"entrance": {"modes": ["Crosskeys"], "onlyPrimeTime": bad}},
                              "onlyPrimeTime")
        self.assertFalse(self.one({"modes": ["Crosskeys"], "onlyPrimeTime": False}).only_prime)

    def test_between_must_be_a_boolean(self):
        for bad in ("yes", 1, 0, None, "true"):
            with self.subTest(bad=bad):
                self.rejected({"entrance": {"modes": ["Crosskeys"], "between": bad},
                               "keys": {"modes": ["K One"]}}, "between")
        for good in (True, False):
            chosen, _left = compose({"entrance": {"modes": ["Crosskeys"], "between": good},
                                     "keys": {"modes": ["K One"]}})
            self.assertEqual(chosen[0].between, good)
            self.assertFalse(chosen[1].between)

    def test_every_category_being_a_between_category_is_an_error(self):
        self.rejected({"entrance": {"modes": ["Crosskeys"], "between": True}}, "between")
        self.rejected({"entrance": {"modes": ["Crosskeys"], "between": True},
                       "keys": {"modes": ["K One"], "between": True}}, "between")
        # A category left out for lack of modes does not count as a main category.
        self.rejected({"entrance": {"modes": ["Crosskeys"], "between": True},
                       "keys": {"modes": []}}, "between")
        chosen, _left = compose({"entrance": {"modes": ["Crosskeys"], "between": True},
                                 "keys": {"modes": ["K One"]}})
        self.assertEqual([c.between for c in chosen], [True, False])

    def test_a_category_with_no_modes_is_left_out_and_listed(self):
        chosen, left_out = compose({"entrance": {"modes": ["Crosskeys"]}, "fun": {"modes": []},
                                    "keys": {"modes": []}})
        self.assertEqual([c.name for c in chosen], ["entrance"])
        self.assertEqual(left_out, ["fun", "keys"])
        # Even a left-out category is checked for bad keys and limits.
        self.rejected({"entrance": {"modes": ["Crosskeys"]}, "fun": {"modes": [], "dailyMax": 0}},
                      "dailyMax")

    def test_a_composition_of_only_empty_categories_is_an_error(self):
        self.rejected({"fun": {"modes": []}}, "no modes")

    def test_names_match_exactly_then_ignoring_case_and_bracket_style(self):
        for label, expected in (("Crosskeys", 21), ("Crosskeys 2024", 22),
                                ("crosskeys 2024", 22), ("CROSSKEYS", 21),
                                ("  Crosskeys   2024 ", 22)):
            with self.subTest(label):
                self.assertEqual(self.one({"modes": [label]}).modes[0].id, expected)
        self.assertEqual(self.one({"modes": ["No Logic Double-Rods (DR)"]}, "g").modes[0].id, 52)
        self.assertEqual(self.one({"modes": ["no logic double-rods [dr]"]}, "g").modes[0].id, 52)
        # Square brackets match the exact name 51 without any ambiguity with 56.
        self.assertEqual(self.one({"modes": ["HMG [VT]"]}, "g").modes[0].id, 51)
        self.assertEqual(self.one({"modes": ["hmg (vt)"]}, "g").modes[0].id, 56)

    def test_a_name_that_matches_several_modes_lists_their_ids(self):
        self.rejected({"g": {"modes": ["HMG (VT)"]}}, "several modes", "51, 56")
        self.rejected({"g": {"modes": ["Hmg [vt]"]}}, "several modes", "51, 56")
        # The test modes of the world have two modes called Simple Open.
        with self.assertRaises(sp.ScheduleError) as caught:
            sp.resolve_composition({"composition": {"s": {"modes": ["Simple Open"]}}},
                                   MODES, fitted())
        self.assertIn("11, 13", str(caught.exception))

    def test_an_unknown_name_is_an_error_that_suggests_close_names(self):
        self.rejected({"entrance": {"modes": ["Crosskey 2024"]}}, "no mode is called",
                      "did you mean", "Crosskeys 2024")
        self.rejected({"entrance": {"modes": ["zzzzzz"]}}, "no mode is called")
        with self.assertRaises(sp.ScheduleError) as caught:
            compose({"entrance": {"modes": ["zzzzzz"]}})
        self.assertNotIn("did you mean", str(caught.exception))

    def test_a_name_listed_twice_is_an_error_whatever_its_spelling(self):
        self.rejected({"entrance": {"modes": ["Crosskeys", "Crosskeys"]}}, "listed twice")
        self.rejected({"entrance": {"modes": ["Crosskeys", "crosskeys"]}}, "listed twice")

    def test_a_mode_in_two_categories_is_allowed(self):
        chosen, _left = compose({"a": {"modes": ["Crosskeys"]}, "b": {"modes": ["Crosskeys"]}})
        self.assertEqual([c.name for c in chosen], ["a", "b"])

    def test_an_archetype_without_a_category_is_an_error(self):
        self.rejected({"odd": {"modes": ["Odd One"]}}, "archetype 8")

    def test_a_category_with_no_slots_in_the_history_window_is_an_error(self):
        self.rejected({"fun": {"modes": ["F One"]}}, "fun", "no slots")


class MixedCategoryTests(unittest.TestCase):
    def test_a_pair_of_plan_categories_with_a_shared_source_has_no_score(self):
        pairs = {frozenset(("keys", "mystery")): sp.PairScore(0.1, False)}
        a = plain_category("a", source="keys")
        b = plain_category("b", source="keys")
        self.assertIsNone(sp.category_pairs([a, b], pairs)[frozenset(("a", "b"))].score)
        # A mixed category shares a source with a plain one that has any of its sources.
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        self.assertIsNone(sp.category_pairs([a, t], pairs)[frozenset(("a", "t"))].score)

    def test_the_pair_score_is_the_highest_of_the_sources_pair_scores(self):
        pairs = {
            frozenset(("simple", "keys")): sp.PairScore(0.10, False),
            frozenset(("simple", "mystery")): sp.PairScore(0.24, False),
            frozenset(("entrance", "keys")): sp.PairScore(0.2, False),
            frozenset(("entrance", "mystery")): sp.PairScore(0.05, False),
        }
        simple, entrance = plain_category("s", source="simple"), plain_category("e", source="entrance")
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        scored = sp.category_pairs([simple, entrance, t], pairs)
        self.assertAlmostEqual(scored[frozenset(("s", "t"))].score, 0.24)
        self.assertAlmostEqual(scored[frozenset(("e", "t"))].score, 0.2)
        self.assertEqual(len(scored), 3)

    def test_weak_evidence_in_any_source_pair_propagates(self):
        pairs = {
            frozenset(("simple", "keys")): sp.PairScore(0.10, False),
            frozenset(("simple", "mystery")): sp.PairScore(0.05, True),
        }
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        scored = sp.category_pairs([plain_category("s", source="simple"), t], pairs)[frozenset(("s", "t"))]
        self.assertTrue(scored.weak)
        self.assertAlmostEqual(scored.score, 0.10)

    def test_an_unmeasured_source_pair_has_no_score(self):
        pairs = {frozenset(("simple", "keys")): sp.PairScore(0.10, False)}
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        scored = sp.category_pairs([plain_category("s", source="simple"), t], pairs)[frozenset(("s", "t"))]
        self.assertIsNone(scored.score)

    def test_plain_categories_keep_their_source_pair_scores(self):
        pairs = {frozenset(("simple", "keys")): sp.PairScore(0.12, False)}
        scored = sp.category_pairs([plain_category("s", source="simple"),
                                    plain_category("k", source="keys")], pairs)
        self.assertEqual(scored[frozenset(("s", "k"))], sp.PairScore(0.12, False))

    def test_an_avoid_event_on_a_source_also_avoids_the_mixed_category(self):
        window = ((datetime(2026, 9, 7, 15, tzinfo=UTC), datetime(2026, 9, 7, 17, tzinfo=UTC)),)
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        plain = plain_category("s", source="simple")
        avoid = sp.Event("e", "avoid", frozenset({"keys"}), window)
        (expanded,) = sp.expand_events([avoid], [t, plain])
        self.assertEqual(expanded.categories, frozenset({"keys", "t"}))
        self.assertEqual(expanded.windows, avoid.windows)
        # A plain category of that source counts too, and one of another source does not.
        keys_plain = plain_category("k", source="keys")
        (again,) = sp.expand_events([avoid], [keys_plain, plain])
        self.assertEqual(again.categories, frozenset({"keys", "k"}))

    def test_events_that_do_not_avoid_are_left_alone(self):
        window = ((datetime(2026, 9, 7, 15, tzinfo=UTC), datetime(2026, 9, 7, 17, tzinfo=UTC)),)
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        events = [sp.Event("b", "block", frozenset(), window),
                  sp.Event("s", "single", frozenset(), window),
                  sp.Event("a", "avoid", frozenset({"simple"}), window)]
        self.assertEqual(sp.expand_events(events, [t]), events)



class HolidayTests(unittest.TestCase):
    def test_2026_holds_the_nine_holidays_and_only_those(self):
        self.assertEqual(
            sp.holiday_dates(2026),
            {
                date(2026, 1, 1), date(2026, 5, 25), date(2026, 7, 4), date(2026, 9, 7),
                date(2026, 11, 26), date(2026, 11, 27),
                date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 31),
            },
        )

    def test_the_moving_holidays_of_other_years(self):
        moving = {
            # year: (Memorial Day, Labor Day, Thanksgiving)
            2024: (date(2024, 5, 27), date(2024, 9, 2), date(2024, 11, 28)),
            2025: (date(2025, 5, 26), date(2025, 9, 1), date(2025, 11, 27)),
            2027: (date(2027, 5, 31), date(2027, 9, 6), date(2027, 11, 25)),
        }
        for year, (memorial, labor, thanksgiving) in moving.items():
            with self.subTest(year):
                holidays = sp.holiday_dates(year)
                self.assertEqual(len(holidays), 9)
                self.assertTrue({memorial, labor, thanksgiving} <= holidays)
                self.assertIn(thanksgiving + timedelta(days=1), holidays)
                self.assertEqual(memorial.weekday(), 0)
                self.assertEqual(labor.weekday(), 0)
                self.assertEqual(thanksgiving.weekday(), 3)
                self.assertTrue(all(day.year == year for day in holidays))

    def test_a_holiday_on_a_weekend_moves_no_day_off_to_a_weekday(self):
        # 2027-12-25 is a Saturday: Christmas Eve is a holiday in its own right.
        holidays = sp.holiday_dates(2027)
        self.assertEqual(date(2027, 12, 25).weekday(), 5)
        self.assertIn(date(2027, 12, 24), holidays)
        self.assertNotIn(date(2027, 12, 27), holidays)
        self.assertNotIn(date(2027, 12, 23), holidays)
        # 2027-07-04 is a Sunday: neither the Friday nor the Monday is a day off.
        self.assertEqual(date(2027, 7, 4).weekday(), 6)
        self.assertNotIn(date(2027, 7, 5), holidays)
        self.assertNotIn(date(2027, 7, 2), holidays)
        # 2028-01-01 is a Saturday, and 2028-01-03 is not a holiday.
        self.assertNotIn(date(2028, 1, 3), sp.holiday_dates(2028))

    def test_is_holiday_follows_holiday_dates(self):
        self.assertTrue(sp.is_holiday(date(2026, 11, 26)))
        self.assertFalse(sp.is_holiday(date(2026, 11, 25)))


class DayGroupTests(unittest.TestCase):
    def test_monday_to_wednesday_work_thursday_friday_casual_weekend_prime(self):
        week = [date(2026, 11, 2) + timedelta(days=offset) for offset in range(7)]
        self.assertEqual(
            [sp.day_group(day) for day in week],
            ["work", "work", "work", "casual", "casual", "prime", "prime"],
        )
        self.assertEqual(sp.WEEKDAY_GROUPS, tuple(sp.day_group(day) for day in week))

    def test_a_holiday_is_prime_whatever_its_weekday(self):
        for day, plain in (
            (date(2026, 11, 26), "casual"),   # Thanksgiving, a Thursday
            (date(2026, 11, 27), "casual"),   # Black Friday
            (date(2026, 5, 25), "work"),      # Memorial Day, a Monday
            (date(2026, 9, 7), "work"),       # Labor Day
            (date(2027, 12, 24), "casual"),   # Christmas Eve, a Friday
        ):
            with self.subTest(day=day):
                self.assertEqual(sp.day_group(day), "prime")
                self.assertEqual(sp.day_group(day, holidays=True), "prime")
                self.assertEqual(sp.day_group(day, holidays=False), plain)

    def test_the_day_after_a_weekend_holiday_stays_a_work_day(self):
        self.assertEqual(sp.day_group(date(2027, 12, 27)), "work")



class ParseBandsTests(unittest.TestCase):
    SPEC = {
        "work": {"prime": "12-15", "quiet": "1-7"},
        "casual": {"prime": "12-17", "quiet": "2-7"},
        "prime": {"prime": "11-18", "quiet": "2-4"},
    }

    @staticmethod
    def hours(layout, group, band):
        return {hour for hour in range(24) if layout[group, hour] == band}

    def bands(self, **changes):
        raw = {group: dict(entry) for group, entry in self.SPEC.items()}
        for group, entry in changes.items():
            raw[group].update(entry)
        return raw

    def rejected(self, raw):
        with self.assertRaises(sp.ScheduleError):
            sp.parse_bands(raw)

    def test_a_valid_plan_gives_every_group_hour_prime_quiet_or_normal(self):
        layout = sp.parse_bands(self.SPEC)
        self.assertEqual(len(layout), 72)
        self.assertEqual(self.hours(layout, "work", "prime"), {12, 13, 14, 15})
        self.assertEqual(self.hours(layout, "work", "quiet"), set(range(1, 8)))
        self.assertEqual(self.hours(layout, "casual", "prime"), set(range(12, 18)))
        self.assertEqual(self.hours(layout, "casual", "quiet"), set(range(2, 8)))
        self.assertEqual(self.hours(layout, "prime", "prime"), set(range(11, 19)))
        self.assertEqual(self.hours(layout, "prime", "quiet"), {2, 3, 4})
        self.assertEqual(
            {group: len(self.hours(layout, group, "normal")) for group in sp.DAY_GROUPS},
            {"work": 13, "casual": 12, "prime": 13},
        )
        self.assertEqual(set(layout.values()), set(sp.BANDS))

    def test_the_bands_hold_40_39_and_89_weekday_hours_of_the_week(self):
        layout = sp.parse_bands(self.SPEC)
        counts = Counter(
            layout[sp.WEEKDAY_GROUPS[weekday], hour] for weekday in range(7) for hour in range(24)
        )
        self.assertEqual(counts, {"prime": 40, "quiet": 39, "normal": 89})

    def test_a_range_whose_first_hour_is_after_the_last_wraps_past_midnight(self):
        layout = sp.parse_bands(self.bands(work={"quiet": "22-2"}))
        self.assertEqual(self.hours(layout, "work", "quiet"), {22, 23, 0, 1, 2})
        layout = sp.parse_bands(self.bands(work={"prime": "23-0", "quiet": "5-6"}))
        self.assertEqual(self.hours(layout, "work", "prime"), {23, 0})

    def test_a_single_hour_and_the_ends_of_the_day_are_ranges(self):
        layout = sp.parse_bands(self.bands(work={"prime": "5-5", "quiet": "0-0"}))
        self.assertEqual(self.hours(layout, "work", "prime"), {5})
        self.assertEqual(self.hours(layout, "work", "quiet"), {0})
        layout = sp.parse_bands(self.bands(work={"prime": "23-23", "quiet": "0-3"}))
        self.assertEqual(self.hours(layout, "work", "prime"), {23})

    def test_both_ends_are_included_and_adjacent_ranges_do_not_overlap(self):
        layout = sp.parse_bands(self.bands(work={"prime": "10-12", "quiet": "13-15"}))
        self.assertEqual(self.hours(layout, "work", "prime"), {10, 11, 12})
        self.assertEqual(self.hours(layout, "work", "quiet"), {13, 14, 15})

    def test_groups_and_keys_must_be_exactly_the_three_and_two(self):
        self.rejected(None)
        self.rejected([])
        self.rejected("work")
        self.rejected({})
        self.rejected({k: v for k, v in self.SPEC.items() if k != "casual"})
        self.rejected({**self.SPEC, "holiday": {"prime": "1-2", "quiet": "3-4"}})
        self.rejected({**self.SPEC, "work": "12-15"})
        self.rejected({**self.SPEC, "work": {"prime": "12-15"}})
        self.rejected({**self.SPEC, "work": {"quiet": "1-7"}})
        self.rejected({**self.SPEC, "work": {"prime": "12-15", "quiet": "1-7", "normal": "8-9"}})

    def test_a_value_must_be_a_first_last_range_of_hours(self):
        for bad in (12, None, ["12-15"], "", "12", "12-", "-15", "a-b", "12–15", "12-15-16",
                    " 12-15", "12-15 ", "12 - 15", "1.5-3", "-1-3"):
            with self.subTest(bad=bad):
                self.rejected(self.bands(work={"prime": bad}))
                self.rejected(self.bands(work={"quiet": bad}))

    def test_an_hour_outside_0_to_23_is_an_error(self):
        for bad in ("24-3", "3-24", "25-26", "99-1"):
            with self.subTest(bad=bad):
                self.rejected(self.bands(work={"quiet": bad}))

    def test_prime_and_quiet_hours_may_not_overlap(self):
        self.rejected(self.bands(work={"prime": "12-15", "quiet": "15-17"}))
        self.rejected(self.bands(work={"prime": "12-15", "quiet": "14-14"}))
        self.rejected(self.bands(work={"prime": "22-2", "quiet": "1-3"}))
        self.rejected(self.bands(work={"prime": "22-2", "quiet": "23-23"}))
        self.rejected(self.bands(work={"prime": "3-2", "quiet": "3-3"}))
        # Another group's overlap is its own.
        self.rejected(self.bands(prime={"prime": "2-4", "quiet": "4-5"}))
        # The same hours in two groups are fine.
        sp.parse_bands(self.bands(work={"prime": "5-6", "quiet": "7-8"}, casual={"prime": "7-8", "quiet": "5-6"}))

    def test_every_error_is_a_schedule_error_naming_the_group(self):
        with self.assertRaises(sp.ScheduleError) as caught:
            sp.parse_bands(self.bands(casual={"prime": "x"}))
        self.assertIn("casual", str(caught.exception))

    def test_a_fixture_layout_matches_its_plan(self):
        for (group, hour), band in fx.WORLD_LAYOUT.items():
            plan = fx.WORLD_BANDS[group]
            self.assertEqual(band, "prime" if hour in sp._range_hours(group, "prime", plan["prime"])
                             else "quiet" if hour in sp._range_hours(group, "quiet", plan["quiet"])
                             else "normal")


class BandTests(unittest.TestCase):
    @staticmethod
    def model(blocks, hour_factor=None):
        names = list(blocks)
        model = flat_model(names)
        hours = {group: [1.0] * 24 for group in sp.DAY_GROUPS}
        hours.update(hour_factor or {})
        return replace(model, block={name: list(values) for name, values in blocks.items()}, hour=hours)

    LAYOUT = sp.parse_bands(ParseBandsTests.SPEC)

    def test_every_group_hour_gets_its_plan_file_band_and_a_value(self):
        for model, chosen in ((fitted(), fx.categories()), (fitted(), fx.categories(fx.MIXED_COMPOSITION))):
            bands = sp.group_bands(fx.WORLD_LAYOUT, model, chosen)
            self.assertEqual(len(bands), 72)
            for key, band in fx.WORLD_LAYOUT.items():
                self.assertEqual(bands[key][0], band)

    def test_bands_come_from_the_plan_not_from_the_values(self):
        # Work-day hours 0 to 5 draw twice as much, yet they stay quiet as the plan says.
        loud_night = [2.0] * 6 + [1.0] * 18
        model = self.model({"a": [1.0] * 6}, {"work": loud_night})
        bands = sp.group_bands(self.LAYOUT, model, [plain_category("a")])
        self.assertEqual(bands["work", 3], ("quiet", 2.0))
        self.assertEqual(bands["work", 1], ("quiet", 2.0))
        self.assertEqual(bands["work", 0], ("normal", 2.0))
        self.assertEqual(bands["work", 13][0], "prime")
        self.assertEqual(bands["casual", 3], ("quiet", 1.0))
        self.assertEqual(bands["prime", 11][0], "prime")
        self.assertEqual(bands["work", 11][0], "normal")

    def test_value_is_the_mean_over_the_plan_categories_between_ones_included(self):
        model = self.model({"a": [1, 2, 3, 4, 5, 6], "b": [1.0] * 6})
        both = [plain_category("a"), plain_category("b", between=True)]
        bands = sp.group_bands(self.LAYOUT, model, both)
        for group in sp.DAY_GROUPS:
            for hour in range(24):
                self.assertAlmostEqual(bands[group, hour][1], ((hour // 4 + 1) + 1) / 2)

    def test_value_follows_each_groups_own_hour_factors_and_the_modes(self):
        hours = {"work": [1.0] * 24, "casual": [2.0] * 24, "prime": [3.0] * 24}
        model = self.model({"a": [1.0] * 6, "b": [3.0] * 6}, hours)
        b = plain_category("b", 2.0)
        bands = sp.group_bands(self.LAYOUT, model, [plain_category("a"), b])
        self.assertAlmostEqual(bands["work", 9][1], (1.0 + 3.0 * 2.0) / 2)
        self.assertAlmostEqual(bands["casual", 9][1], 2 * (1.0 + 6.0) / 2)
        self.assertAlmostEqual(bands["prime", 9][1], 3 * (1.0 + 6.0) / 2)

    def test_the_fixture_world_has_prime_time_in_the_second_half_of_the_day(self):
        for group in sp.DAY_GROUPS:
            prime = sorted(h for h in range(24) if fx.WORLD_LAYOUT[group, h] == "prime")
            self.assertTrue(all(h >= 12 for h in prime), (group, prime))


class BandRateTests(unittest.TestCase):
    @staticmethod
    def bands():
        # Hour 10 is prime time on prime days and normal on work days; every hour is otherwise normal.
        return fx.bands_by(lambda group, hour: ("prime" if (group, hour) == ("prime", 10) else "normal", 1.0))

    @staticmethod
    def slots(*entries):
        return history([
            ("k", 1, datetime(2026, 9, day, 10, tzinfo=EASTERN), 3, fired) for day, fired in entries
        ])

    def test_slots_count_by_their_own_dates_group_and_hour(self):
        # 2026-09-12 is a Saturday, 2026-09-14 a Monday, 2026-09-07 Labor Day.
        slots = self.slots((12, True), (12, False), (14, True), (7, True))
        rates = sp.band_rates(slots, flat_model(["k"]), self.bands())
        self.assertEqual(rates["k"]["prime"], (2, 3))
        self.assertEqual(rates["k"]["normal"], (1, 1))
        self.assertEqual(rates["k"]["quiet"], (0, 0))

    def test_without_holidays_a_holiday_slot_counts_in_its_weekday_group(self):
        slots = self.slots((12, True), (12, False), (14, True), (7, True))
        rates = sp.band_rates(slots, flat_model(["k"], holidays=False), self.bands())
        self.assertEqual(rates["k"]["prime"], (1, 2))
        self.assertEqual(rates["k"]["normal"], (2, 2))

    def test_each_category_is_counted_on_its_own(self):
        slots = history([
            ("a", 1, datetime(2026, 9, 14, 10, tzinfo=EASTERN), 3, True),
            ("b", 1, datetime(2026, 9, 14, 10, tzinfo=EASTERN), 0, False),
        ])
        rates = sp.band_rates(slots, flat_model(["a", "b"]), self.bands())
        self.assertEqual(rates["a"]["normal"], (1, 1))
        self.assertEqual(rates["b"]["normal"], (0, 1))

    def test_a_fire_rate_is_fired_over_scheduled_and_zero_without_slots(self):
        self.assertEqual(sp.fire_rate((1, 4)), 0.25)
        self.assertEqual(sp.fire_rate((4, 4)), 1.0)
        self.assertEqual(sp.fire_rate((0, 0)), 0.0)


class OpenBandTests(unittest.TestCase):
    @staticmethod
    def rates(**counts):
        return {
            name: dict(zip(sp.BANDS, bands)) for name, bands in counts.items()
        }

    def test_a_band_is_open_at_a_fire_rate_of_exactly_ten_percent(self):
        rates = self.rates(keys=((1, 10), (9, 100), (1, 11)))
        category = plain_category("k", source="keys")
        # 10% is open, 9% and 1/11 are closed.
        self.assertEqual(sp.open_bands(category, rates), ("prime",))

    def test_every_source_of_a_mixed_category_must_be_open(self):
        rates = self.rates(keys=((5, 10), (5, 10), (0, 10)), mystery=((5, 10), (0, 10), (5, 10)))
        mixed = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        self.assertEqual(sp.open_bands(mixed, rates), ("prime",))
        self.assertEqual(sp.open_bands(plain_category("k", source="keys"), rates), ("prime", "normal"))

    def test_a_band_with_no_slots_never_fired_so_it_is_closed(self):
        rates = self.rates(keys=((3, 10), (0, 0), (0, 0)))
        self.assertEqual(sp.open_bands(plain_category("k", source="keys"), rates), ("prime",))

    def test_only_prime_time_opens_prime_time_alone(self):
        rates = self.rates(keys=((5, 10), (5, 10), (5, 10)))
        only = plain_category("k", source="keys", only_prime=True)
        self.assertEqual(sp.open_bands(only, rates), ("prime",))
        self.assertEqual(sp.open_bands(plain_category("k", source="keys"), rates), sp.BANDS)

    def test_only_prime_time_with_a_closed_prime_time_may_race_nowhere(self):
        rates = self.rates(keys=((0, 10), (5, 10), (5, 10)))
        with self.assertRaises(sp.ScheduleError) as caught:
            sp.open_bands(plain_category("kk", source="keys", only_prime=True), rates)
        self.assertIn("kk", str(caught.exception))

    def test_without_the_band_rule_every_band_is_open_but_prime_time_still_rules(self):
        rates = self.rates(keys=((0, 10), (0, 10), (0, 10)))
        free = plain_category("k", source="keys")
        self.assertEqual(sp.open_bands(free, rates, band_rule=False), sp.BANDS)
        only = plain_category("k", source="keys", only_prime=True)
        self.assertEqual(sp.open_bands(only, rates, band_rule=False), ("prime",))

    def test_a_category_with_no_open_band_is_an_error_naming_it(self):
        rates = self.rates(keys=((0, 10), (0, 10), (0, 10)), mystery=((9, 10), (9, 10), (9, 10)))
        with self.assertRaises(sp.ScheduleError) as caught:
            sp.open_bands(plain_category("kk", source="keys"), rates)
        self.assertIn("kk", str(caught.exception))
        # One closed source is enough to close a band of a mixed category.
        mixed = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        with self.assertRaises(sp.ScheduleError):
            sp.open_bands(mixed, rates)


class ShareWeightTests(unittest.TestCase):
    # The groups' hour factors differ, so each weekday must count in its own group:
    # work 1, casual 2, prime 3 (three work days, two casual days, two prime days).
    WEEK_MEAN = (3 * 1 + 2 * 2 + 2 * 3) / 7

    def setUp(self):
        hours = {"work": [1.0] * 24, "casual": [2.0] * 24, "prime": [3.0] * 24}
        self.model = replace(flat_model(["keys", "mystery"], {"keys": 2.0, "mystery": 4.0}), hour=hours)
        self.rates = {
            "keys": {"prime": (8, 10), "normal": (6, 10), "quiet": (2, 10)},
            "mystery": {"prime": (4, 10), "normal": (6, 10), "quiet": (1, 10)},
        }

    def weights(self, categories, shares, open_bands=None):
        names = [c.name for c in categories]
        open_by_name = open_bands or {name: sp.BANDS for name in names}
        return sp.share_weights(categories, self.model, LAYOUT, self.rates, open_by_name, shares)

    def test_the_bands_hold_42_84_and_42_weekday_hours(self):
        counts = Counter(
            LAYOUT[sp.WEEKDAY_GROUPS[weekday], hour][0] for weekday in range(7) for hour in range(24)
        )
        self.assertEqual(counts, {"prime": 42, "normal": 84, "quiet": 42})

    def test_entrants_weight_is_the_mean_expected_entrants_with_each_weekday_in_its_group(self):
        keys = plain_category("k", 1.5, source="keys")
        weights = self.weights([keys], "entrants")["k"]
        for band in sp.BANDS:
            self.assertAlmostEqual(weights[band], 2.0 * 1.5 * self.WEEK_MEAN)

    def test_entrants_weight_follows_the_hours_of_each_band(self):
        hours = {group: [float(hour + 1) for hour in range(24)] for group in sp.DAY_GROUPS}
        model = replace(self.model, hour=hours)
        keys = plain_category("k", source="keys")
        weights = sp.share_weights([keys], model, LAYOUT, self.rates, {"k": sp.BANDS}, "entrants")["k"]
        # Block factor 2 times the mean hour factor of the band (the same in every group).
        self.assertAlmostEqual(weights["prime"], 2.0 * sum(range(13, 19)) / 6)
        self.assertAlmostEqual(weights["quiet"], 2.0 * sum(range(1, 7)) / 6)
        self.assertAlmostEqual(weights["normal"], 2.0 * (sum(range(7, 13)) + sum(range(19, 25))) / 12)

    def test_a_mixed_categorys_entrants_weight_is_the_mean_over_its_modes(self):
        mixed = mixed_category("t", ("keys", 1.0, 50), ("mystery", 0.5, 50))
        weights = self.weights([mixed], "entrants")["t"]
        self.assertAlmostEqual(weights["prime"], (2.0 * 1.0 + 4.0 * 0.5) / 2 * self.WEEK_MEAN)

    def test_fill_weight_is_the_mean_over_the_modes_of_their_sources_fire_rate(self):
        keys = plain_category("k", source="keys")
        mixed = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        twice = mixed_category("u", ("keys", 1.0, 50), ("keys", 1.0, 50), ("mystery", 1.0, 50))
        weights = self.weights([keys, mixed, twice], "fill")
        self.assertAlmostEqual(weights["k"]["prime"], 0.8)
        self.assertAlmostEqual(weights["k"]["quiet"], 0.2)
        self.assertAlmostEqual(weights["t"]["prime"], (0.8 + 0.4) / 2)
        self.assertAlmostEqual(weights["t"]["normal"], 0.6)
        self.assertAlmostEqual(weights["u"]["prime"], (0.8 + 0.8 + 0.4) / 3)

    def test_equal_weight_is_one(self):
        weights = self.weights([plain_category("k", source="keys")], "equal")["k"]
        self.assertEqual(weights, {"prime": 1.0, "normal": 1.0, "quiet": 1.0})

    def test_a_band_with_no_hours_weighs_nothing_under_every_rule(self):
        no_normal = fx.bands_by(lambda group, hour: ("prime" if hour >= 12 else "quiet", 1.0))
        keys = plain_category("k", source="keys")
        for shares in sp.SHARES:
            with self.subTest(shares):
                weights = sp.share_weights(
                    [keys], self.model, no_normal, self.rates, {"k": sp.BANDS}, shares
                )["k"]
                self.assertEqual(weights["normal"], 0.0)
                self.assertGreater(weights["prime"], 0.0)

    def test_a_plan_with_no_normal_hours_runs_with_every_band_open(self):
        inputs = fx.rules_inputs()
        inputs["layout"] = sp.parse_bands(
            {group: {"prime": "8-19", "quiet": "20-7"} for group in sp.DAY_GROUPS}
        )
        plan = sp.plan_proposal(fx.race_set("light"), rules=sp.Rules(band_rule=False), **inputs)
        self.assertGreater(plan["races"], 0)
        self.assertFalse(any(race["band"] == "normal" for race in plan["schedule"]))

    def test_a_closed_band_weighs_nothing_under_every_rule(self):
        keys = plain_category("k", source="keys")
        for shares in sp.SHARES:
            with self.subTest(shares):
                weights = self.weights([keys], shares, {"k": ("prime", "normal")})["k"]
                self.assertEqual(weights["quiet"], 0.0)
                self.assertGreater(weights["prime"], 0.0)
                self.assertGreater(weights["normal"], 0.0)


# Featured races -------------------------------------------------------------------

SEPTEMBER = sp.period_dates(*fx.PERIOD)


def source_slots(category, weekday, hour, entrants, count=5):
    """`count` slots of a category at a weekday and Eastern hour, on consecutive weeks."""
    monday = datetime(2026, 6, 1, hour, tzinfo=EASTERN)
    return [
        (category, 1, monday + timedelta(days=weekday, weeks=week), entrants // count, True)
        for week in range(count)
    ]


def local_at(day, hour):
    return datetime(day.year, day.month, day.day, hour, tzinfo=EASTERN)


def weekly_event(effect, weekday, hour, hours, *, categories=(), dates=SEPTEMBER, name="weekly"):
    """An event every week on a weekday, with a window on each such date of `dates`."""
    windows = tuple(
        (local_at(day, hour).astimezone(UTC), local_at(day, hour).astimezone(UTC) + timedelta(hours=hours))
        for day in dates
        if day.weekday() == weekday
    )
    return sp.Event(name, effect, frozenset(categories), windows, weekly=True)


def featured(categories, entries=(), *, events=(), bands=LAYOUT, model=None, open_bands=None,
             dates=SEPTEMBER):
    names = [c.name for c in categories]
    model = model or flat_model(names)
    open_bands = open_bands or {name: sp.BANDS for name in names}
    return sp.featured_slots(categories, model, history(list(entries)), bands, open_bands,
                             list(events), dates)


class FeaturedSlotTests(unittest.TestCase):
    def test_candidates_are_the_prime_hours_of_the_weekdays_own_group(self):
        def own_prime(group, hour):
            first = {"work": 10, "casual": 12, "prime": 14}[group]
            return ("prime" if first <= hour < first + 6 else "normal"), 1.0

        bands = fx.bands_by(own_prime)
        a = plain_category("a")
        base = flat_model(["a"])

        def peaked(hour):
            factors = [1.0] * 24
            factors[hour] = 5.0
            return replace(base, hour={group: list(factors) for group in sp.DAY_GROUPS})

        # Hour 11 is prime only on work days, hour 17 on casual and prime days, hour 19 only on
        # prime days: the first such weekday from Monday takes the race.
        self.assertEqual(featured([a], bands=bands, model=peaked(11)), {"a": (0, 11)})
        self.assertEqual(featured([a], bands=bands, model=peaked(17)), {"a": (3, 17)})
        self.assertEqual(featured([a], bands=bands, model=peaked(19)), {"a": (5, 19)})
        # Hour 3 is prime nowhere, so it is never a candidate however good it looks.
        self.assertEqual(featured([a], bands=bands, model=peaked(3)), {"a": (0, 10)})

    def test_a_between_categorys_candidates_run_from_one_to_twenty_two(self):
        late = fx.bands_by(lambda group, hour: ("prime" if hour in (0, 22, 23) else "normal", 1.0))
        factors = [1.0] * 24
        factors[23] = 5.0
        model = replace(flat_model(["a"]), hour={group: list(factors) for group in sp.DAY_GROUPS})
        # A main category takes its best prime hour, 23:00. A between race there could have
        # no bracket on its own date, so a between category takes 22:00 instead.
        self.assertEqual(featured([plain_category("a")], bands=late, model=model), {"a": (0, 23)})
        self.assertEqual(
            featured([plain_category("a", between=True)], bands=late, model=model), {"a": (0, 22)}
        )
        edges = fx.bands_by(lambda group, hour: ("prime" if hour in (0, 23) else "normal", 1.0))
        with self.assertRaises(sp.ScheduleError):
            featured([plain_category("a", between=True)], bands=edges, model=model)

    def test_a_category_with_a_tie_between_hours_takes_the_earlier_hour(self):
        self.assertEqual(featured([plain_category("a")]), {"a": (0, 12)})

    def test_the_score_blends_the_past_slots_with_five_times_the_expected_entrants(self):
        a = plain_category("a")
        # Tuesday at 13: two slots with 21 entrants; (21 + 5 * 1) / (2 + 5) beats the 1 elsewhere.
        entries = source_slots("a", 1, 13, 20, 2)
        entries[0] = (*entries[0][:3], 11, True)
        self.assertEqual(featured([a], entries), {"a": (1, 13)})
        # A bad record scores below the expected entrants of 1 and is avoided.
        bad = source_slots("a", 0, 12, 0, 5)
        self.assertEqual(featured([a], bad), {"a": (0, 13)})

    def test_a_mixed_category_counts_the_slots_of_every_source(self):
        t = mixed_category("t", ("keys", 1.0, 50), ("mystery", 1.0, 50))
        model = flat_model(["keys", "mystery"], {"keys": 2.0, "mystery": 4.0})
        entries = (
            source_slots("keys", 2, 13, 20, 1) + source_slots("mystery", 2, 13, 20, 1)
            + source_slots("keys", 3, 13, 30, 1)
        )
        # Wednesday: (40 + 5 * 3) / 7 = 7.86; Thursday: (30 + 5 * 3) / 6 = 7.5.
        self.assertEqual(featured([t], entries, model=model), {"t": (2, 13)})
        # Counting one source only would have picked Thursday.
        keys_only = mixed_category("t", ("keys", 1.0, 50))
        self.assertEqual(featured([keys_only], entries, model=model), {"t": (3, 13)})

    def test_the_assignment_maximises_the_summed_score_not_the_strongest_first(self):
        a, b = plain_category("a"), plain_category("b")
        # Scores are (entrants + 5) / (5 + 5): a has 10 on Monday and 9 on Tuesday, b has 9.5 on
        # Monday and 1 elsewhere. Strongest first would give Monday to a and leave b 1: 11 in all.
        entries = (
            source_slots("a", 0, 12, 95) + source_slots("a", 1, 12, 85)
            + source_slots("b", 0, 12, 90)
        )
        self.assertEqual(featured([a, b], entries), {"a": (1, 12), "b": (0, 12)})

    def test_a_tie_goes_to_the_first_assignment_in_plan_order_from_monday(self):
        a, b = plain_category("a"), plain_category("b")
        self.assertEqual(featured([a, b]), {"a": (0, 12), "b": (1, 12)})
        self.assertEqual(featured([b, a]), {"b": (0, 12), "a": (1, 12)})

    def test_every_category_gets_a_weekday_of_its_own(self):
        cats = [plain_category(name) for name in "abcdefg"]
        result = featured(cats)
        self.assertEqual(sorted(weekday for weekday, _hour in result.values()), list(range(7)))

    def test_more_than_seven_categories_is_an_error(self):
        cats = [plain_category(name) for name in "abcdefgh"]
        with self.assertRaises(sp.ScheduleError):
            featured(cats)

    def test_a_category_without_prime_time_has_no_candidates_and_the_plan_fails(self):
        a = plain_category("a")
        with self.assertRaises(sp.ScheduleError):
            featured([a], open_bands={"a": ("normal", "quiet")})
        b = plain_category("b")
        with self.assertRaises(sp.ScheduleError):
            featured([a, b], open_bands={"a": sp.BANDS, "b": ("normal",)})

    def test_a_weekly_block_event_skips_the_hours_it_covers_in_any_week(self):
        a = plain_category("a")
        entries = source_slots("a", 0, 12, 95)
        self.assertEqual(featured([a], entries), {"a": (0, 12)})
        # Monday 11:00 to 14:00 touches the races at 12 and 13 but not 14.
        block = weekly_event("block", 0, 11, 3)
        self.assertEqual(featured([a], entries, events=[block]), {"a": (0, 14)})
        # An event of one week only still rules the hour out, since a weekly event repeats.
        third = weekly_event("block", 0, 11, 3, dates=[d for d in SEPTEMBER if d.day == 21])
        self.assertEqual(len(third.windows), 1)
        self.assertEqual(featured([a], entries, events=[third]), {"a": (0, 14)})

    def test_a_weekly_avoid_event_skips_only_the_categories_it_lists(self):
        a, b = plain_category("a"), plain_category("b")
        entries = source_slots("a", 0, 12, 95) + source_slots("b", 0, 12, 95)
        avoid_a = weekly_event("avoid", 0, 11, 3, categories=["a"])
        result = featured([a, b], entries, events=[avoid_a])
        self.assertEqual(result["a"][0], 1)
        self.assertEqual(result["b"], (0, 12))

    def test_a_dated_event_and_a_weekly_single_event_never_skip_a_candidate(self):
        a = plain_category("a")
        entries = source_slots("a", 0, 12, 95)
        dated = replace(weekly_event("block", 0, 11, 3), weekly=False)
        single = weekly_event("single", 0, 11, 3)
        self.assertEqual(featured([a], entries, events=[dated, single]), {"a": (0, 12)})

    def test_a_weekly_event_outside_the_period_dates_skips_nothing(self):
        a = plain_category("a")
        entries = source_slots("a", 0, 12, 95)
        october = [date(2026, 10, 5)]
        away = weekly_event("block", 0, 11, 3, dates=october)
        self.assertEqual(featured([a], entries, events=[away]), {"a": (0, 12)})


class EventTests(unittest.TestCase):
    period = sp.season_bounds(date(2027, 3, 1), 9, sp.WEEKDAYS.index("thursday"))
    known = {"entrance", "simple"}

    def valid(self, **changes):
        event = {"name": "e", "weekly": "monday", "start": "01:00", "timezone": "UTC",
                 "hours": 3, "effect": "avoid", "categories": ["entrance"]}
        event.update(changes)
        return {key: value for key, value in event.items() if value is not None}

    def parse(self, event):
        return sp.parse_events([event], self.known, *self.period)

    def test_weekly_utc_event_keeps_its_utc_time_across_the_dst_change(self):
        (event,) = self.parse(self.valid())
        hours = set()
        for start, end in event.windows:
            self.assertEqual((start.hour, end - start), (1, timedelta(hours=3)))
            self.assertEqual(start.weekday(), 0)
            local = start.astimezone(EASTERN)
            self.assertEqual(local.hour, 20 if start < datetime(2027, 3, 14, 7, tzinfo=UTC) else 21)
            hours.add(local.hour)
        self.assertEqual(hours, {20, 21})

    def test_weekly_event_in_eastern_time_keeps_its_wall_clock_time(self):
        (event,) = self.parse(self.valid(timezone="America/New_York", start="20:00"))
        self.assertEqual({s.astimezone(EASTERN).hour for s, _e in event.windows}, {20})
        self.assertEqual({s.hour for s, _e in event.windows}, {0, 1})

    def test_date_event_and_period_reach(self):
        (event,) = self.parse(self.valid(weekly=None, date="2027-03-20", start="14:00",
                                         timezone="America/New_York", hours=5,
                                         effect="block", categories=None))
        self.assertEqual(event.windows, ((datetime(2027, 3, 20, 18, tzinfo=UTC),
                                          datetime(2027, 3, 20, 23, tzinfo=UTC)),))
        (outside,) = self.parse(self.valid(weekly=None, date="2028-01-01", effect="block", categories=None))
        self.assertEqual(outside.windows, ())
        (reaching,) = self.parse(self.valid(weekly=None, date="2027-02-28", start="20:00",
                                            hours=24, effect="block", categories=None))
        self.assertEqual(len(reaching.windows), 1)

    def test_a_race_is_inside_when_the_two_hour_span_overlaps(self):
        begin = datetime(2027, 3, 20, 18, tzinfo=UTC)
        event = sp.Event("e", "block", frozenset(), ((begin, begin + timedelta(hours=5)),))
        self.assertFalse(event.touches(begin - timedelta(hours=2)))
        self.assertTrue(event.touches(begin - timedelta(minutes=119)))
        self.assertTrue(event.touches(begin + timedelta(hours=4, minutes=59)))
        self.assertFalse(event.touches(begin + timedelta(hours=5)))

    def test_events_know_whether_they_repeat_every_week(self):
        (weekly,) = self.parse(self.valid())
        (dated,) = self.parse(self.valid(weekly=None, date="2027-03-20", effect="block", categories=None))
        self.assertTrue(weekly.weekly)
        self.assertFalse(dated.weekly)
        self.assertFalse(sp.Event("e", "block", frozenset(), ()).weekly)

    def test_validation_errors(self):
        cases = {
            "unknown key": self.valid(bogus=1),
            "weekly and date": self.valid(date="2027-03-20"),
            "neither weekly nor date": self.valid(weekly=None),
            "bad effect": self.valid(effect="ban"),
            "categories on single": self.valid(effect="single"),
            "avoid without categories": self.valid(categories=None),
            "unknown category": self.valid(categories=["nope"]),
            "bad timezone": self.valid(timezone="Mars/Base"),
            "bad start": self.valid(start="25:99"),
            "missing start": self.valid(start=None),
            "zero hours": self.valid(hours=0),
            "negative hours": self.valid(hours=-2),
            "bad weekday": self.valid(weekly="Monday"),
            "bad date": self.valid(weekly=None, date="2027-13-01"),
            "no name": self.valid(name=None),
        }
        for label, event in cases.items():
            with self.subTest(label), self.assertRaises(sp.ScheduleError):
                self.parse(event)




class CalendarTests(unittest.TestCase):
    THURSDAY = sp.WEEKDAYS.index("thursday")

    def test_a_season_races_until_midnight_after_its_last_thursday(self):
        begin, end = sp.season_bounds(date(2026, 11, 9), 9, self.THURSDAY)
        self.assertEqual(begin, datetime(2026, 11, 9, 5, tzinfo=UTC))
        self.assertEqual(end, datetime(2027, 1, 8, 5, tzinfo=UTC))
        dates = sp.period_dates(begin, end)
        self.assertEqual((len(dates), dates[0], dates[-1]), (60, date(2026, 11, 9), date(2027, 1, 7)))
        # Friday to Sunday are the pause, and the next season starts on Monday 11 January.
        self.assertEqual(date(2026, 11, 9) + timedelta(weeks=9), date(2027, 1, 11))

    def test_the_last_race_day_is_the_last_such_weekday_in_the_seasons_weeks(self):
        for start, weeks, last_day, expected in (
            (date(2026, 11, 9), 9, self.THURSDAY, date(2027, 1, 7)),  # Monday: 3 days off
            (date(2026, 11, 12), 1, self.THURSDAY, date(2026, 11, 12)),  # Thursday: 6 days off
            (date(2026, 11, 13), 1, self.THURSDAY, date(2026, 11, 19)),  # Friday: no days off
            (date(2026, 11, 9), 2, sp.WEEKDAYS.index("sunday"), date(2026, 11, 22)),
        ):
            with self.subTest(start=start, weeks=weeks, last_day=last_day):
                self.assertEqual(sp.last_race_day(start, weeks, last_day), expected)

    def test_period_dates_count_eastern_dates_across_daylight_saving(self):
        dates = sp.period_dates(*sp.season_bounds(date(2027, 2, 20), 9, self.THURSDAY))
        self.assertEqual(len(dates), 62)
        self.assertEqual((dates[0], dates[-1]), (date(2027, 2, 20), date(2027, 4, 22)))
        dates = sp.period_dates(*sp.season_bounds(date(2026, 10, 26), 2, self.THURSDAY))
        self.assertEqual((len(dates), dates[-1]), (11, date(2026, 11, 5)))

    def test_default_start_is_the_first_date_whose_midnight_is_two_hours_after_the_last_slot(self):
        def rows(local):
            return [slot(1, 1, local, 11)]

        for local, expected in (
            (datetime(2026, 11, 5, 22, tzinfo=EASTERN), date(2026, 11, 6)),
            (datetime(2026, 11, 5, 22, 1, tzinfo=EASTERN), date(2026, 11, 7)),
            (datetime(2026, 11, 5, 23, tzinfo=EASTERN), date(2026, 11, 7)),
            (datetime(2026, 11, 5, 3, tzinfo=EASTERN), date(2026, 11, 6)),
        ):
            with self.subTest(local=local):
                self.assertEqual(sp.default_start(rows(local)), expected)
        with self.assertRaises(sp.ScheduleError):
            sp.default_start([])




# Placement ------------------------------------------------------------------------


def start_at(local, band="normal", value=1.0):
    return sp.Start(local.astimezone(UTC), local, sp.day_group(local.date()), band, value)


def hours_of(starts):
    return [start.local.hour for start in starts]


SEP1, SEP2, SEP3, SEP4, SEP5, SEP6, SEP7, SEP8 = (date(2026, 9, day) for day in range(1, 9))


def st(hour, band="normal", value=1.0, day=SEP2):
    """A start at an Eastern hour with the given band and value."""
    return start_at(local_at(day, hour), band, value)


def day_starts(day, rule=fx.layout):
    """Every hourly instant of a date, so a 23 or 25 hour day has that many starts."""
    begin = datetime.combine(day, time(0), tzinfo=EASTERN).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=EASTERN).astimezone(UTC)
    starts, at = [], begin
    while at < end:
        local = at.astimezone(EASTERN)
        group = sp.day_group(local.date())
        band, value = rule(group, local.hour)
        starts.append(sp.Start(at, local, group, band, value))
        at += timedelta(hours=1)
    return starts


def make_filler(names="abc", *, between=(), events=(), rules=sp.SHIPPED, limits=None,
                open_bands=None, weights=None, holidays=True, bands=LAYOUT):
    limits = limits or {}
    categories = [
        plain_category(name, between=name in between, **limits.get(name, {})) for name in names
    ]
    return sp.Filler(
        categories,
        flat_model(list(names), holidays=holidays),
        bands,
        open_bands or {name: sp.BANDS for name in names},
        weights or {name: {band: 1.0 for band in sp.BANDS} for name in names},
        list(events),
        rules,
    )


def put(filler, name, day, hour, *, featured=False):
    filler.place(filler.start(day, hour), name, featured=featured)


def timeline(filler):
    return [(race.start.local.date().day, race.start.local.hour, race.category)
            for race in filler.races()]


def block(day, first, last, *, name="block", effect="block", categories=()):
    """An event from the first hour to the last hour of a date (the end is exclusive)."""
    begin = local_at(day, first)
    return sp.Event(name, effect, frozenset(categories),
                    ((begin.astimezone(UTC), (begin + timedelta(hours=last - first)).astimezone(UTC)),))


def clock(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN).astimezone(UTC)


class ChooseHoursTests(unittest.TestCase):
    """The objective is, in order: hours, the next date's hours, prime, fewer short gaps, normal,
    value, then the earlier hours. Each test lets a criterion decide only among ties above it."""

    @staticmethod
    def pick(options, **kwargs):
        kwargs.setdefault("need", len(options))
        result = sp.choose_hours(options, **kwargs)
        return None if result is None else (result[0], hours_of(result[1]))

    def test_every_pick_is_two_hours_from_the_next_and_the_most_that_fit_come_out_in_time_order(self):
        _score, picked = self.pick([st(h) for h in range(10, 20)], wide_gaps=False)
        self.assertEqual(picked, [10, 12, 14, 16, 18])
        _score, picked = self.pick([st(h) for h in (13, 11, 12, 10)], wide_gaps=False)
        self.assertEqual(picked, [10, 12])

    def test_two_hours_apart_exactly_is_allowed_and_one_is_not(self):
        self.assertEqual(self.pick([st(10), st(12)])[1], [10, 12])
        self.assertEqual(len(self.pick([st(10), st(11)])[1]), 1)

    def test_the_score_is_hours_next_hours_prime_minus_short_gaps_normal_value(self):
        score, picked = self.pick(
            [st(10, "normal", 1.5), st(13, "prime", 2.5), st(16, "quiet", 4.0), st(19, "prime", 0.5)]
        )
        self.assertEqual(picked, [10, 13, 16, 19])
        self.assertEqual(score[:5], (4, 0, 2, 0, 1))
        self.assertAlmostEqual(score[5], 8.5)

    def test_it_picks_at_most_the_number_needed_and_none_for_zero(self):
        options = [st(h) for h in range(0, 24)]
        for need in (0, 1, 2, 5, 12):
            with self.subTest(need=need):
                score, picked = self.pick(options, need=need, wide_gaps=False)
                self.assertEqual(len(picked), need)
                self.assertEqual(score[0], need)
        self.assertEqual(self.pick(options, need=-3)[1], [])
        # Fewer options than needed return the most that fit.
        self.assertEqual(self.pick([st(10), st(11), st(20)], need=9)[1], [10, 20])
        self.assertEqual(self.pick([], need=3), ((0, 0, 0, 0, 0, 0.0), []))

    def test_the_most_hours_beat_the_most_prime_hours(self):
        # 11 and 13 are two normal races; 12 alone is one prime race.
        self.assertEqual(self.pick([st(11), st(12, "prime"), st(13)])[1], [11, 13])

    def test_the_next_dates_hours_beat_prime_and_the_rest(self):
        options = [st(20, "prime", 9.0), st(22, "normal", 1.0)]
        # Only a last race at 22 leaves the next date its hours.
        following = lambda last: 5 if last is not None and last.local.hour == 22 else 1  # noqa: E731
        score, picked = self.pick(options, need=1, following=following)
        self.assertEqual(picked, [22])
        self.assertEqual(score[:3], (1, 5, 0))
        # With equal lookahead the prime hour wins as usual.
        self.assertEqual(self.pick(options, need=1, following=lambda last: 3)[1], [20])
        self.assertEqual(self.pick(options, need=1)[1], [20])

    def test_the_lookahead_is_asked_from_the_last_pick_or_from_the_previous_race(self):
        def following(last):
            return -1 if last is None else last.local.hour

        # The score carries what the lookahead says of the last race taken.
        self.assertEqual(self.pick([st(10), st(12)], need=2, following=following)[0][1], 12)
        self.assertEqual(self.pick([st(10)], need=0, previous=st(7, day=SEP1), following=following)[0][1], 7)
        self.assertEqual(self.pick([st(10)], need=0, following=following)[0][1], -1)
        # Without a lookahead the term is 0.
        self.assertEqual(self.pick([st(10), st(12)], need=2)[0][1], 0)

    def test_the_most_prime_hours_break_a_tie_in_hours(self):
        self.assertEqual(self.pick([st(10), st(11, "prime", 0.1)], need=1)[1], [11])
        score, _ = self.pick([st(10), st(11, "prime")], need=1)
        self.assertEqual(score[2], 1)

    def test_prime_beats_short_gaps(self):
        # (10, 12) and (12, 14) hold a short gap and a prime hour; (10, 14) none and no prime.
        score, picked = self.pick([st(10), st(12, "prime"), st(14)], need=2)
        self.assertEqual(picked, [10, 12])
        self.assertEqual(score[2:5], (1, -1, 1))

    def test_fewer_short_gaps_beat_more_normal_hours(self):
        # A quiet 13 keeps the gap at 3 hours, where the normal 12 would shrink it to 2.
        score, picked = self.pick([st(10), st(12), st(13, "quiet")], need=2)
        self.assertEqual(picked, [10, 13])
        self.assertEqual(score[3:5], (0, 1))
        # Without the short-gap term the normal pair wins.
        score, picked = self.pick([st(10), st(12), st(13, "quiet")], need=2, wide_gaps=False)
        self.assertEqual(picked, [10, 12])
        self.assertEqual(score[3], 0)

    def test_a_gap_is_short_below_three_hours_unless_both_races_are_prime(self):
        cases = (
            ([st(12, "prime"), st(14, "prime")], 0),
            ([st(12, "prime"), st(14)], -1),
            ([st(12), st(14, "prime")], -1),
            ([st(12), st(14)], -1),
            ([st(2, "quiet"), st(4, "quiet")], -1),
            ([st(10), st(13)], 0),
            ([st(10), st(16)], 0),
        )
        for options, gaps in cases:
            with self.subTest(hours=hours_of(options), gaps=gaps):
                score, picked = self.pick(options, need=2)
                self.assertEqual(len(picked), 2)
                self.assertEqual(score[3], gaps)

    def test_the_previous_main_race_counts_for_the_first_gap(self):
        previous = start_at(local_at(SEP1, 23))
        # 01:00 would be 2 hours after a race at 23:00 the day before; 02:00 is 3 hours after.
        options = [st(1), st(2)]
        score, picked = self.pick(options, need=1, previous=previous)
        self.assertEqual((picked, score[3]), ([2], 0))
        score, picked = self.pick(options, need=1, previous=previous, wide_gaps=False)
        self.assertEqual(picked, [1])
        # Two prime races are never a short gap, and no previous race is no gap.
        prime = start_at(local_at(SEP1, 23), "prime")
        score, picked = self.pick([st(1, "prime"), st(2, "prime")], need=1, previous=prime)
        self.assertEqual((picked, score[3]), ([1], 0))
        self.assertEqual(self.pick(options, need=1)[1], [1])

    def test_the_most_normal_hours_break_a_tie_in_short_gaps(self):
        # No gap is short: (8, 11) holds two normal hours, (8, 14) one, however dear the quiet one.
        options = [st(8), st(11, "normal", 1.0), st(14, "quiet", 9.0)]
        score, picked = self.pick(options, need=2)
        self.assertEqual(picked, [8, 11])
        self.assertEqual(score[4], 2)

    def test_the_highest_total_value_breaks_a_tie_in_bands(self):
        self.assertEqual(self.pick([st(10, value=1.0), st(11, value=2.0)], need=1)[1], [11])
        # Two normal pairs: the dearer one wins even though it starts later.
        options = [st(8, value=1), st(10, value=1), st(9, value=3), st(11, value=3)]
        self.assertEqual(self.pick(options, need=2, wide_gaps=False)[1], [9, 11])

    def test_the_earliest_hours_break_a_tie_in_everything(self):
        self.assertEqual(self.pick([st(10), st(11)], need=1)[1], [10])
        self.assertEqual(self.pick([st(h) for h in (10, 11, 12, 13)], need=2, wide_gaps=False)[1], [10, 12])
        self.assertEqual(self.pick([st(h) for h in range(24)], need=12, wide_gaps=False)[1],
                         list(range(0, 24, 2)))

    def test_fixed_starts_are_kept_apart_but_never_returned_or_counted(self):
        options = [st(10), st(12), st(16), st(17)]
        score, picked = self.pick(options, need=2, fixed=[st(14)])
        # 10 and 17 leave 4 and 3 hours either side of the fixed race: no short gap.
        self.assertEqual(picked, [10, 17])
        self.assertEqual(score[:4], (2, 0, 0, 0))
        # A pick one hour from the fixed race is not allowed.
        self.assertEqual(self.pick([st(13), st(15)], need=2, fixed=[st(14)])[1], [])
        # Fixed races do not count against the number needed.
        self.assertEqual(len(self.pick(options, need=1, fixed=[st(14)])[1]), 1)

    def test_a_fixed_race_counts_for_short_gaps_with_the_races_around_it(self):
        score, picked = self.pick([st(12), st(16)], need=1, fixed=[st(14)], wide_gaps=True)
        self.assertEqual(score[3], -1)
        score, _ = self.pick([st(11, "prime"), st(16)], need=1, fixed=[st(13, "prime")])
        self.assertEqual(score[3], 0)

    def test_forced_starts_are_always_picked_and_count_against_need(self):
        options = [st(10), st(13), st(14), st(15)]
        score, picked = self.pick(options, need=2, forced=[st(12, "prime")])
        self.assertEqual(picked, [12, 15])
        self.assertEqual(score[0], 2)
        self.assertEqual(self.pick(options, need=1, forced=[st(12, "prime")])[1], [12])
        # Without the forced start the best picks are three hours.
        self.assertEqual(self.pick([st(11), st(13), st(15)], need=3, wide_gaps=False)[1], [11, 13, 15])
        self.assertEqual(self.pick([st(11), st(13), st(15)], need=3, forced=[st(12)], wide_gaps=False)[1],
                         [12, 15])

    def test_it_is_none_when_the_forced_and_fixed_starts_cannot_all_be_taken(self):
        self.assertIsNone(self.pick([st(20)], forced=[st(12), st(13)]))
        self.assertIsNone(self.pick([st(20)], forced=[st(13)], fixed=[st(12)]))
        self.assertIsNone(self.pick([st(20)], need=0, forced=[st(13)]))
        self.assertIsNone(self.pick([st(20)], need=1, forced=[st(12), st(14)]))
        self.assertIsNotNone(self.pick([st(20)], need=2, forced=[st(12), st(14)]))
        self.assertIsNone(self.pick([], fixed=[st(12), st(13)]))

    @staticmethod
    def brute(options, need, fixed, forced, previous, wide_gaps, following):
        """The spec's objective by trying every subset of the free starts."""
        best = None
        for size in range(len(options) + 1):
            for chosen in itertools.combinations(options, size):
                picked = sorted([*forced, *chosen], key=lambda start: start.utc)
                taken = sorted([*fixed, *picked], key=lambda start: start.utc)
                if len(picked) > max(need, 0):
                    continue
                if any(b.utc - a.utc < sp.RACE_LENGTH for a, b in zip(taken, taken[1:])):
                    continue
                before = None if previous is None else previous
                short = 0
                for start in taken:
                    if (wide_gaps and before is not None and start.utc - before.utc < sp.WIDE_GAP
                            and not (before.band == "prime" and start.band == "prime")):
                        short += 1
                    before = start
                last = taken[-1] if taken else previous
                score = (
                    len(picked), following(last) if following else 0,
                    sum(s.band == "prime" for s in picked), -short,
                    sum(s.band == "normal" for s in picked), sum(s.value for s in picked),
                )
                key = (score, [-start.utc.timestamp() for start in picked])
                if best is None or key > best[0]:
                    best = (key, score, picked)
        return None if best is None else (best[1], best[2])

    def test_the_search_matches_a_brute_force_of_the_objective_on_random_inputs(self):
        import random
        rng = random.Random(20261104)
        bands = ("prime", "normal", "quiet")
        for case in range(250):
            hours = rng.sample(range(0, 14), rng.randint(0, 9))
            starts = [st(h, rng.choice(bands), rng.choice((1.0, 1.5, 2.0, 2.5))) for h in hours]
            kinds = [rng.choice(("free", "free", "free", "fixed", "forced")) for _ in starts]
            options = [s for s, k in zip(starts, kinds) if k == "free"]
            fixed = [s for s, k in zip(starts, kinds) if k == "fixed"]
            forced = [s for s, k in zip(starts, kinds) if k == "forced"]
            previous = rng.choice((None, st(rng.randint(0, 22), rng.choice(bands), 1.0, SEP1)))
            lookahead = rng.choice((None, "late"))
            following = None if lookahead is None else (
                lambda last: 0 if last is None else (last.local.hour * 7) % 5)
            need = rng.randint(0, 7)
            wide = rng.choice((True, False))
            expected = self.brute(options, need, fixed, forced, previous, wide, following)
            actual = sp.choose_hours(options, need=need, fixed=fixed, forced=forced,
                                     previous=previous, wide_gaps=wide, following=following)
            with self.subTest(case=case, hours=hours, kinds=kinds, need=need, wide=wide):
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertIsNotNone(actual)
                    self.assertEqual(actual[0], expected[0])
                    self.assertEqual(hours_of(actual[1]), hours_of(expected[1]))

    def test_the_spring_day_has_23_hours_and_still_reaches_12_races(self):
        starts = day_starts(date(2026, 3, 8))
        self.assertEqual(len(starts), 23)
        _score, picked = sp.choose_hours(starts, need=12)
        self.assertEqual(len(picked), 12)
        for first, second in zip(picked, picked[1:]):
            self.assertGreaterEqual(second.utc - first.utc, sp.RACE_LENGTH)
        self.assertEqual(len(sp.choose_hours(starts, need=13)[1]), 12)

    def test_the_autumn_day_has_25_hours_and_reaches_12_and_13_races(self):
        starts = day_starts(date(2026, 11, 1))
        self.assertEqual(len(starts), 25)
        for need in (12, 13):
            _score, picked = sp.choose_hours(starts, need=need)
            self.assertEqual(len(picked), need)
            for first, second in zip(picked, picked[1:]):
                self.assertGreaterEqual(second.utc - first.utc, sp.RACE_LENGTH)
        self.assertEqual(len(sp.choose_hours(starts, need=14)[1]), 13)


class StartTests(unittest.TestCase):
    def test_a_wall_clock_hour_that_daylight_saving_skips_has_no_start(self):
        filler = make_filler()
        self.assertIsNone(filler.start(date(2026, 3, 8), 2))
        self.assertIsNotNone(filler.start(date(2026, 3, 8), 3))
        free = filler.free(date(2026, 3, 8))
        self.assertEqual(len(free), 23)
        self.assertNotIn(2, hours_of(free))

    def test_a_repeated_hour_takes_its_first_occurrence(self):
        filler = make_filler()
        first = filler.start(date(2026, 11, 1), 1)
        self.assertEqual(first.utc, datetime(2026, 11, 1, 5, tzinfo=UTC))
        free = filler.free(date(2026, 11, 1))
        self.assertEqual(len(free), 24)
        self.assertEqual(hours_of(free), list(range(24)))

    def test_a_start_carries_the_dates_group_and_the_hours_band_and_value(self):
        filler = make_filler()
        start = filler.start(date(2026, 9, 7), 13)  # Labor Day
        self.assertEqual((start.group, start.band, start.value), ("prime", "prime", LAYOUT["prime", 13][1]))
        plain = make_filler(holidays=False).start(date(2026, 9, 7), 13)
        self.assertEqual(plain.group, "work")

    def test_free_hours_leave_out_placed_races_and_block_events(self):
        filler = make_filler(events=[block(SEP2, 10, 12)])
        put(filler, "a", SEP2, 20)
        free = hours_of(filler.free(SEP2))
        self.assertNotIn(20, free)
        # A race at 9 would run until 11, inside the event; 12 starts as it ends.
        for hour in (9, 10, 11):
            self.assertNotIn(hour, free)
        self.assertIn(12, free)
        self.assertIn(8, free)

    def test_open_hours_keep_two_hours_from_every_main_race_on_any_date(self):
        filler = make_filler()
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP1, 23)
        open_hours = hours_of(filler.open_hours(SEP2))
        for hour in (0, 11, 12, 13):
            self.assertNotIn(hour, open_hours)
        for hour in (1, 10, 14):
            self.assertIn(hour, open_hours)
        self.assertNotIn(23, hours_of(filler.open_hours(SEP1)))
        self.assertNotIn(22, hours_of(filler.open_hours(SEP1)))
        self.assertIn(21, hours_of(filler.open_hours(SEP1)))

    def test_a_between_race_does_not_close_the_hours_next_to_it(self):
        filler = make_filler(between="c")
        put(filler, "c", SEP2, 13)
        open_hours = hours_of(filler.open_hours(SEP2))
        self.assertIn(12, open_hours)
        self.assertIn(14, open_hours)
        self.assertNotIn(13, open_hours)

    def test_a_single_event_closes_the_hours_less_than_two_hours_from_a_between_race_it_touches(self):
        # A between race at 12 closes no hour next to it, unless a single event touches either.
        # The event on 14 and 15 touches a start at 13, and the one on 13 touches the race itself.
        inside = make_filler("abm", between="m", events=[block(SEP2, 14, 16, effect="single", name="s")])
        put(inside, "m", SEP2, 12)
        open_hours = hours_of(inside.open_hours(SEP2))
        self.assertNotIn(13, open_hours)
        self.assertIn(11, open_hours)
        touching = make_filler("abm", between="m", events=[block(SEP2, 13, 14, effect="single", name="s")])
        put(touching, "m", SEP2, 12)
        open_hours = hours_of(touching.open_hours(SEP2))
        self.assertNotIn(11, open_hours)
        self.assertNotIn(13, open_hours)
        self.assertIn(14, open_hours)
        self.assertIn(10, open_hours)
        far = make_filler("abm", between="m", events=[block(SEP2, 20, 22, effect="single", name="s")])
        put(far, "m", SEP2, 12)
        self.assertIn(13, hours_of(far.open_hours(SEP2)))
        self.assertIn(11, hours_of(far.open_hours(SEP2)))


class FitsTests(unittest.TestCase):
    def test_a_closed_band_does_not_fit(self):
        filler = make_filler(open_bands={"a": ("prime",), "b": sp.BANDS, "c": sp.BANDS})
        self.assertTrue(filler.fits(filler.start(SEP2, 13), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 8), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 2), "a"))
        self.assertTrue(filler.fits(filler.start(SEP2, 8), "b"))

    def test_an_avoid_event_that_lists_the_category_does_not_fit(self):
        avoid = block(SEP2, 12, 13, effect="avoid", name="av", categories="a")
        filler = make_filler(events=[avoid])
        # A race at 11 runs 11 to 13 and overlaps; one at 13 does not; others are unaffected.
        self.assertFalse(filler.fits(filler.start(SEP2, 11), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 12), "a"))
        self.assertTrue(filler.fits(filler.start(SEP2, 13), "a"))
        self.assertTrue(filler.fits(filler.start(SEP2, 11), "b"))

    def test_daily_max_stops_a_category_on_that_date_only(self):
        filler = make_filler(limits={"a": {"daily_max": 1}})
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 14)
        self.assertFalse(filler.fits(filler.start(SEP2, 16), "a"))
        self.assertTrue(filler.fits(filler.start(SEP3, 12), "a"))
        loose = make_filler(limits={"a": {"daily_max": 2}})
        put(loose, "a", SEP2, 12)
        put(loose, "b", SEP2, 14)
        self.assertTrue(loose.fits(loose.start(SEP2, 16), "a"))
        put(loose, "a", SEP2, 16)
        put(loose, "b", SEP2, 18)
        self.assertFalse(loose.fits(loose.start(SEP2, 20), "a"))

    def test_weekly_max_counts_a_monday_to_sunday_week(self):
        filler = make_filler(limits={"a": {"weekly_max": 1}})
        # 2026-09-06 is a Sunday, the last day of the week that began on Monday 2026-08-31.
        put(filler, "a", SEP6, 12)
        put(filler, "b", SEP6, 14)
        self.assertFalse(filler.fits(filler.start(SEP6, 16), "a"))
        self.assertFalse(filler.fits(filler.start(SEP5, 12), "a"))
        self.assertFalse(filler.fits(filler.start(date(2026, 8, 31), 12), "a"))
        # Monday 2026-09-07 starts a new week.
        self.assertTrue(filler.fits(filler.start(SEP7, 12), "a"))
        put(filler, "a", SEP7, 12)
        put(filler, "b", SEP7, 14)
        self.assertFalse(filler.fits(filler.start(SEP8, 12), "a"))

    def test_the_race_just_before_or_after_must_be_of_another_category(self):
        filler = make_filler()
        put(filler, "a", SEP2, 12)
        self.assertFalse(filler.fits(filler.start(SEP2, 14), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 10), "a"))
        self.assertTrue(filler.fits(filler.start(SEP2, 14), "b"))
        self.assertTrue(filler.fits(filler.start(SEP2, 10), "b"))
        # Just before in time, whatever the distance: nothing lies between them.
        self.assertFalse(filler.fits(filler.start(SEP2, 20), "a"))
        self.assertFalse(filler.fits(filler.start(SEP3, 12), "a"))
        put(filler, "b", SEP2, 16)
        self.assertTrue(filler.fits(filler.start(SEP2, 20), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 14), "b"))

    def test_a_main_category_looks_past_between_races_to_the_main_races_next_to_it(self):
        filler = make_filler("abdc", between="c")
        put(filler, "a", SEP2, 13)
        put(filler, "c", SEP2, 14)
        put(filler, "b", SEP2, 18)
        start = filler.start(SEP2, 15)
        # The race just before 15 is the between race c, but the main race before is a: a would
        # run twice in a row among the main races, and so would b after it.
        self.assertFalse(filler.fits(start, "a"))
        self.assertFalse(filler.fits(start, "b"))
        self.assertTrue(filler.fits(start, "d"))
        # A between category goes by the races next to it in time.
        self.assertFalse(filler.fits(start, "c"))
        self.assertTrue(filler.fits(filler.start(SEP2, 11), "c"))
        # Before the first and after the last main race the same holds.
        self.assertFalse(filler.fits(filler.start(SEP2, 11), "a"))
        self.assertFalse(filler.fits(filler.start(SEP2, 20), "b"))
        self.assertTrue(filler.fits(filler.start(SEP2, 20), "a"))

    def test_a_between_category_still_looks_at_the_races_just_before_and_after_in_time(self):
        filler = make_filler("abmn", between="mn")
        put(filler, "a", SEP2, 12)
        put(filler, "m", SEP2, 13)
        put(filler, "b", SEP2, 14)
        start = filler.start(SEP2, 15)
        self.assertTrue(filler.fits(start, "m"))
        self.assertTrue(filler.fits(start, "n"))
        put(filler, "n", SEP2, 15)
        # Another between race is not allowed next to n, whichever category it is of its own.
        self.assertFalse(filler.fits(filler.start(SEP2, 16), "n"))
        self.assertTrue(filler.fits(filler.start(SEP2, 16), "m"))
        # A between race may follow a main race of any category.
        self.assertTrue(filler.fits(filler.start(SEP2, 20), "m"))
        put(filler, "m", SEP2, 20)
        self.assertFalse(filler.fits(filler.start(SEP2, 21), "m"))

    def test_neighbours_are_the_races_or_the_main_races_just_before_and_after(self):
        filler = make_filler(between="c")
        put(filler, "a", SEP2, 12)
        put(filler, "c", SEP2, 13)
        put(filler, "b", SEP2, 14)
        at = filler.start(SEP2, 15).utc
        before, after = filler.neighbours(at)
        self.assertEqual((before.category, after), ("b", None))
        before, after = filler.neighbours(filler.start(SEP2, 14).utc + timedelta(minutes=1))
        self.assertEqual(before.category, "b")
        before, after = filler.neighbours(filler.start(SEP2, 13, ).utc + timedelta(minutes=30))
        self.assertEqual((before.category, after.category), ("c", "b"))
        before, after = filler.neighbours(filler.start(SEP2, 13).utc + timedelta(minutes=30), main=True)
        self.assertEqual((before.category, after.category), ("a", "b"))
        self.assertEqual(filler.neighbours(filler.start(SEP2, 9).utc, main=True)[0], None)

    def test_a_single_event_forbids_a_close_race_if_it_touches_either_race(self):
        def single(begin, end):
            return block(SEP2, begin, end, effect="single", name="s")

        # a runs 12 to 14 and b 13 to 15 in every case.
        for begin, end, fits in ((11, 13, False),   # touches a only
                                 (14, 15, False),   # touches b only
                                 (12, 15, False),   # touches both
                                 (16, 18, True),    # touches neither
                                 (9, 12, True)):    # ends as a starts
            with self.subTest(begin=begin, end=end):
                filler = make_filler(events=[single(begin, end)])
                put(filler, "a", SEP2, 12)
                self.assertEqual(filler.fits(filler.start(SEP2, 13), "b"), fits)

    def test_races_less_than_two_hours_apart_fit_without_a_single_event(self):
        filler = make_filler()
        put(filler, "a", SEP2, 12)
        self.assertTrue(filler.fits(filler.start(SEP2, 13), "b"))

    def test_a_single_event_does_not_touch_races_two_hours_or_more_apart(self):
        everything = sp.Event("s", "single", frozenset(),
                              ((local_at(SEP2, 0).astimezone(UTC), local_at(SEP3, 0).astimezone(UTC)),))
        filler = make_filler(events=[everything])
        put(filler, "a", SEP2, 12)
        self.assertTrue(filler.fits(filler.start(SEP2, 14), "b"))
        self.assertFalse(filler.fits(filler.start(SEP2, 13), "b"))


class ChooseTests(unittest.TestCase):
    def test_the_lowest_share_ratio_wins_with_featured_races_counted(self):
        weights = {"a": {band: 2.0 for band in sp.BANDS}, "b": {band: 1.0 for band in sp.BANDS}}
        filler = make_filler("ab", weights=weights)
        start = filler.start(SEP3, 12)
        # Nothing taken: both ratios are 0, so plan order breaks the tie.
        self.assertEqual(filler.choose(start, ["b", "a"]), "a")
        put(filler, "a", SEP1, 12, featured=True)
        self.assertEqual(filler.choose(start, ["a", "b"]), "b")
        put(filler, "b", SEP2, 12)
        # a has 1 of weight 2 = 0.5 and b has 1 of weight 1 = 1.
        self.assertEqual(filler.choose(start, ["a", "b"]), "a")

    def test_the_ratio_counts_the_races_of_that_band_only(self):
        filler = make_filler("ab")
        put(filler, "a", SEP1, 12)   # prime
        self.assertEqual(filler.choose(filler.start(SEP3, 8), ["a", "b"]), "a")   # normal
        self.assertEqual(filler.choose(filler.start(SEP3, 14), ["a", "b"]), "b")  # prime

    def test_a_category_of_weight_zero_comes_last(self):
        weights = {"a": {band: 0.0 for band in sp.BANDS}, "b": {band: 1.0 for band in sp.BANDS}}
        filler = make_filler("ab", weights=weights)
        put(filler, "b", SEP1, 12)
        put(filler, "b", SEP2, 12)
        self.assertEqual(filler.choose(filler.start(SEP3, 12), ["a", "b"]), "b")

    def test_last_weeks_category_at_the_wall_clock_time_comes_before_the_ratio(self):
        weights = {"a": {band: 100.0 for band in sp.BANDS}, "b": {band: 1.0 for band in sp.BANDS}}

        def build(**rules):
            filler = make_filler("ab", weights=weights, rules=sp.Rules(**rules))
            put(filler, "a", SEP1, 12)
            put(filler, "b", SEP3, 12)
            put(filler, "b", SEP3, 14)
            put(filler, "a", SEP4, 12)
            return filler

        filler = build()
        start = filler.start(date(2026, 9, 8), 12)   # a ran here a week ago
        # a's ratio is 2/100 and b's is 2: only the weekly rule steers away from a.
        self.assertEqual(filler.choose(start, ["a", "b"]), "b")
        self.assertEqual(build(rotation=False).choose(start, ["a", "b"]), "a")

    def test_yesterdays_category_comes_after_the_ratio_and_before_plan_order(self):
        filler = make_filler("ab")
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP5, 12)
        start = filler.start(SEP3, 12)   # a ran here yesterday, ratios are 1 and 1
        self.assertEqual(filler.choose(start, ["a", "b"]), "b")
        weights = {"a": {band: 2.0 for band in sp.BANDS}, "b": {band: 1.0 for band in sp.BANDS}}
        weighted = make_filler("ab", weights=weights)
        put(weighted, "a", SEP2, 12)
        put(weighted, "b", SEP5, 12)
        # a's ratio 0.5 is below b's 1, so the ratio beats the daily rule.
        self.assertEqual(weighted.choose(start, ["a", "b"]), "a")
        loose = make_filler("ab", rules=sp.Rules(rotation=False))
        put(loose, "a", SEP2, 12)
        put(loose, "b", SEP5, 12)
        self.assertEqual(loose.choose(start, ["a", "b"]), "a")

    def test_the_weekly_rule_outranks_the_daily_rule(self):
        filler = make_filler("abc")
        put(filler, "a", SEP1, 12)    # a week before SEP8
        put(filler, "b", SEP7, 12)    # a day before SEP8
        put(filler, "c", SEP5, 12)
        put(filler, "c", SEP6, 12)
        start = filler.start(SEP8, 12)
        # Ratios: a 1, b 1, c 2. Of the two tied, a ran last week, b yesterday: b is chosen.
        self.assertEqual(filler.choose(start, ["a", "b"]), "b")
        self.assertEqual(filler.choose(start, ["a", "b", "c"]), "b")

    def test_plan_order_not_the_order_of_the_list_breaks_the_last_tie(self):
        filler = make_filler("abc")
        start = filler.start(SEP3, 12)
        self.assertEqual(filler.choose(start, ["c", "b"]), "b")
        self.assertEqual(filler.choose(start, ["c", "a", "b"]), "a")


class PlaceFeaturedTests(unittest.TestCase):
    WANTED = {day: 7 for day in SEPTEMBER}

    def place(self, filler, featured, wanted=None):
        return filler.place_featured(featured, SEPTEMBER, wanted or self.WANTED)

    def test_a_featured_race_keeps_its_weekday_and_hour_all_period_and_on_holidays(self):
        filler = make_filler()
        skipped = self.place(filler, {"a": (0, 13)})
        races = filler.races()
        self.assertEqual(skipped, {})
        self.assertEqual([race.start.local.date().day for race in races], [7, 14, 21, 28])
        self.assertTrue(all(race.featured and race.start.local.hour == 13 for race in races))
        self.assertFalse(any(race.between for race in races))
        # Labor Day, 2026-09-07, is a Monday and a holiday, and the race still runs.
        self.assertIn(SEP7, [race.start.local.date() for race in races])
        self.assertEqual(races[0].start.group, "prime")
        self.assertEqual(filler.day_races[SEP7], 1)

    def test_a_block_event_skips_that_dates_race_and_lists_it(self):
        filler = make_filler(events=[block(date(2026, 9, 14), 12, 14)])
        skipped = self.place(filler, {"a": (0, 13), "b": (1, 13)})
        self.assertEqual(skipped, {"a": ["2026-09-14"]})
        self.assertEqual(Counter(race.category for race in filler.races()), {"a": 3, "b": 5})

    def test_an_avoid_event_skips_only_its_listed_categories_races(self):
        avoid = block(date(2026, 9, 14), 12, 14, effect="avoid", name="av", categories="a")
        filler = make_filler(events=[avoid])
        skipped = self.place(filler, {"a": (0, 13), "b": (0, 15)})
        self.assertEqual(skipped, {"a": ["2026-09-14"]})
        self.assertEqual(Counter(race.category for race in filler.races()), {"a": 3, "b": 4})

    def test_a_skipped_race_leaves_its_hour_open_to_other_races(self):
        filler = make_filler(events=[block(date(2026, 9, 14), 12, 14)])
        self.place(filler, {"a": (0, 13)})
        self.assertNotIn(local_at(date(2026, 9, 14), 13).astimezone(UTC), filler.placed)

    def test_the_hour_daylight_saving_skips_is_skipped_that_date(self):
        filler = make_filler()
        dates = [date(2026, 3, 1) + timedelta(days=offset) for offset in range(15)]
        skipped = filler.place_featured({"a": (6, 2)}, dates, {day: 7 for day in dates})
        self.assertEqual(skipped, {"a": ["2026-03-08"]})
        self.assertEqual([race.start.local.day for race in filler.races()], [1, 15])

    def test_two_featured_races_less_than_two_hours_apart_are_an_error(self):
        filler = make_filler("ab")
        with self.assertRaises(sp.ScheduleError):
            self.place(filler, {"a": (0, 12), "b": (0, 13)})
        far = make_filler("ab")
        self.place(far, {"a": (0, 12), "b": (0, 14)})
        self.assertEqual(len(far.races()), 8)

    def test_a_featured_between_race_is_a_between_race_that_counts_no_main_race(self):
        filler = make_filler(between="c")
        self.assertEqual(self.place(filler, {"c": (0, 14)}), {})
        races = filler.races()
        self.assertEqual(len(races), 4)
        self.assertTrue(all(race.between and race.featured for race in races))
        self.assertEqual(filler.day_races[SEP7], 0)

    def test_a_featured_between_race_is_skipped_on_a_date_that_wants_fewer_than_two_main_races(self):
        filler = make_filler(between="c")
        wanted = {**self.WANTED, date(2026, 9, 14): 1, date(2026, 9, 21): 2}
        self.assertEqual(self.place(filler, {"c": (0, 14)}, wanted), {"c": ["2026-09-14"]})
        self.assertEqual([race.start.local.day for race in filler.races()], [7, 21, 28])
        # A featured main race is not skipped for it.
        main = make_filler(between="c")
        self.assertEqual(self.place(main, {"a": (0, 14)}, wanted), {})

    def test_a_featured_between_race_is_skipped_on_a_date_without_an_open_bracket(self):
        day = date(2026, 9, 14)
        # The event touches the starts at 12, 13 and 14, so nothing before 15 is left.
        before = make_filler("abm", between="m", events=[block(day, 13, 15)])
        self.assertEqual(self.place(before, {"m": (0, 15)}), {"m": ["2026-09-14"]})
        self.assertEqual(len(before.races()), 3)
        # The event touches the starts at 16 and 17 and the hours after.
        after = make_filler("abm", between="m", events=[block(day, 17, 19)])
        self.assertEqual(self.place(after, {"m": (0, 15)}), {"m": ["2026-09-14"]})
        # With 14 left outside the event a start one hour before is enough.
        some = make_filler("abm", between="m", events=[block(day, 12, 14)])
        self.assertEqual(self.place(some, {"m": (0, 15)}), {})
        self.assertEqual(len(some.races()), 4)
        # A date with the bracket taken by earlier races has none open either.
        taken = make_filler("abm", between="m")
        for hour in (13, 14):
            put(taken, "a", day, hour)
        self.assertEqual(self.place(taken, {"m": (0, 15)}), {"m": ["2026-09-14"]})

    def test_featured_main_races_two_hours_from_a_featured_between_race_leave_its_bracket_open(self):
        filler = make_filler("abm", between="m")
        self.assertEqual(self.place(filler, {"a": (0, 13), "m": (0, 15), "b": (0, 17)}), {})
        self.assertEqual(len(filler.races()), 12)
        other = make_filler("abm", between="m")
        self.assertEqual(self.place(other, {"m": (0, 15), "b": (0, 17), "a": (0, 13)}), {})

    def test_a_featured_race_less_than_two_hours_from_a_between_one_is_an_error_too(self):
        filler = make_filler("abc", between="c")
        with self.assertRaises(sp.ScheduleError):
            self.place(filler, {"c": (0, 14), "a": (0, 15)})


class BracketTests(unittest.TestCase):
    def test_a_start_is_bracketed_one_to_two_hours_after_a_main_race_and_before_the_next(self):
        filler = make_filler()
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 15)
        flags = {hour: filler.bracketed(filler.start(SEP2, hour).utc) for hour in range(10, 18)}
        self.assertEqual(flags, {10: False, 11: False, 12: False, 13: True, 14: True,
                                 15: False, 16: False, 17: False})
        # Four hours apart: only the middle hour has both sides in range.
        wide = make_filler()
        put(wide, "a", SEP2, 12)
        put(wide, "b", SEP2, 16)
        self.assertEqual([hour for hour in range(11, 17)
                          if wide.bracketed(wide.start(SEP2, hour).utc)], [14])
        far = make_filler()
        put(far, "a", SEP2, 12)
        put(far, "b", SEP2, 17)
        self.assertFalse(any(far.bracketed(far.start(SEP2, hour).utc) for hour in range(24)))

    def test_the_main_races_must_be_consecutive_and_a_gap_holds_one_race(self):
        filler = make_filler(between="c")
        put(filler, "a", SEP2, 10)
        put(filler, "b", SEP2, 14)
        # 11 is 3 hours before the next main race; 13 is 3 after the last.
        self.assertFalse(filler.bracketed(filler.start(SEP2, 11).utc))
        self.assertTrue(filler.bracketed(filler.start(SEP2, 12).utc))
        put(filler, "c", SEP2, 12)
        self.assertTrue(filler.bracketed(filler.start(SEP2, 12).utc))
        # A second race could no longer start in that gap.
        two = make_filler(between="c")
        put(two, "a", SEP2, 12)
        put(two, "b", SEP2, 15)
        put(two, "c", SEP2, 13)
        self.assertFalse(two.bracketed(two.start(SEP2, 14).utc))
        self.assertTrue(two.bracketed(two.start(SEP2, 13).utc))

    def test_a_gap_across_midnight_is_bracketed(self):
        filler = make_filler()
        put(filler, "a", SEP2, 23)
        put(filler, "b", SEP3, 1)
        self.assertTrue(filler.bracketed(filler.start(SEP3, 0).utc))

    def test_bracket_open_needs_a_free_start_on_each_side(self):
        filler = make_filler()
        start = filler.start(SEP2, 15)
        self.assertTrue(filler.bracket_open(SEP2, start))
        put(filler, "a", SEP2, 13)
        put(filler, "a", SEP2, 14)
        self.assertFalse(filler.bracket_open(SEP2, start))
        edge = make_filler()
        self.assertFalse(edge.bracket_open(SEP2, edge.start(SEP2, 0)))
        self.assertFalse(edge.bracket_open(SEP2, edge.start(SEP2, 23)))
        self.assertTrue(edge.bracket_open(SEP2, edge.start(SEP2, 1)))

    def test_short_gaps_count_consecutive_main_races_below_three_hours_unless_both_are_prime(self):
        filler = make_filler("abm", between="m")
        for hour, name in ((8, "a"), (10, "b"), (13, "a"), (15, "b"), (18, "a"), (20, "b"), (2, "a")):
            put(filler, name, SEP2, hour)
        put(filler, "m", SEP2, 9)
        # 2-8 6h, 8-10 short, 10-13 3h, 13-15 both prime, 15-18 3h, 18-20 short.
        self.assertEqual(filler.short_gaps(), 2)
        self.assertEqual(make_filler().short_gaps(), 0)

    def test_unbracketed_counts_the_between_races_without_a_main_race_on_each_side(self):
        filler = make_filler("abm", between="m")
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 14)
        put(filler, "m", SEP2, 13)
        self.assertEqual(filler.unbracketed(), 0)
        put(filler, "m", SEP2, 20)
        self.assertEqual(filler.unbracketed(), 1)
        put(filler, "m", SEP2, 9)
        self.assertEqual(filler.unbracketed(), 2)


class FillMainsTests(unittest.TestCase):
    def test_a_day_gets_its_races_two_hours_apart_with_the_most_prime_hours_first(self):
        filler = make_filler()
        filler.fill_mains(SEP2, 7)
        placed = filler.races()
        self.assertEqual(len(placed), 7)
        self.assertEqual(filler.empty, 0)
        for first, second in zip(placed, placed[1:]):
            self.assertGreaterEqual(second.start.utc - first.start.utc, sp.RACE_LENGTH)
        # Six prime hours hold three races, and the date takes as many prime hours as it can.
        self.assertEqual(Counter(race.start.band for race in placed)["prime"], 3)
        self.assertFalse(any(race.featured or race.between for race in placed))
        self.assertEqual(filler.day_races[SEP2], 7)

    def test_the_picked_hours_are_the_ones_with_most_prime_then_fewest_short_gaps(self):
        filler = make_filler()
        filler.fill_mains(SEP2, 7)
        hours = hours_of([race.start for race in filler.races()])
        # Prime time 12 to 17 holds three races two hours apart; the others keep three hours.
        self.assertEqual([hour for hour in hours if 12 <= hour <= 17], [13, 15, 17])
        self.assertEqual(hours, [7, 10, 13, 15, 17, 20, 23])
        self.assertEqual(filler.short_gaps(), 0)

    def test_categories_take_prime_time_first_then_normal_each_by_share_and_without_repeats(self):
        filler = make_filler()
        filler.fill_mains(SEP2, 7)
        races = filler.races()
        names = [race.category for race in races]
        for first, second in zip(names, names[1:]):
            self.assertNotEqual(first, second)
        # Prime races came first, so each of the three categories has one prime race.
        prime = [race.category for race in races if race.start.band == "prime"]
        self.assertEqual(sorted(prime), ["a", "b", "c"])

    def test_the_races_a_date_already_has_count_towards_what_it_wants(self):
        filler = make_filler()
        put(filler, "a", SEP2, 14, featured=True)
        filler.fill_mains(SEP2, 5)
        self.assertEqual(filler.day_races[SEP2], 5)
        self.assertEqual(sum(1 for race in filler.races() if race.start.local.date() == SEP2), 5)
        # Open hours are at least two hours from the placed race.
        hours = [race.start.local.hour for race in filler.races()]
        self.assertNotIn(13, hours)
        self.assertNotIn(15, hours)

    def test_races_stay_two_hours_from_races_placed_on_the_neighbouring_dates(self):
        filler = make_filler()
        put(filler, "a", SEP1, 23, featured=True)
        filler.fill_mains(SEP2, 12)
        hours = [race.start.local.hour for race in filler.races() if race.start.local.date() == SEP2]
        self.assertNotIn(0, hours)
        filler = make_filler()
        put(filler, "a", SEP3, 0, featured=True)
        filler.fill_mains(SEP2, 11)
        late = [race.start.local.hour for race in filler.races() if race.start.local.date() == SEP2]
        self.assertNotIn(23, late)
        self.assertIn(22, late)

    def test_an_hour_no_category_fits_stays_empty_and_is_counted(self):
        filler = make_filler("a")
        filler.fill_mains(SEP2, 3)
        # The first prime hour takes a; the other picks sit next to its race in time.
        self.assertEqual(len(filler.races()), 1)
        self.assertEqual(filler.empty, 2)
        self.assertEqual(filler.day_races[SEP2], 1)

    def test_a_picked_hour_no_category_fits_stays_empty_without_a_make_up(self):
        # Only 10 to 15 are open; c may not race in prime time, and a and b hold 10 and 14.
        blocks = [block(SEP2, 0, 10), block(SEP2, 16, 24)]
        filler = make_filler(events=blocks, open_bands={"a": sp.BANDS, "b": sp.BANDS, "c": ("normal",)})
        put(filler, "a", SEP2, 10, featured=True)
        put(filler, "b", SEP2, 14, featured=True)
        filler.fill_mains(SEP2, 5)
        # The one hour left is 12, in prime time; a and b would run back to back with their
        # neighbours and c may not race there, so it stays empty and nothing makes it up.
        self.assertEqual(timeline(filler), [(2, 10, "a"), (2, 14, "b")])
        self.assertEqual(filler.empty, 1)
        self.assertEqual(filler.day_races[SEP2], 2)

    def test_block_events_keep_races_out_of_the_hours_they_cover(self):
        filler = make_filler(events=[block(SEP2, 0, 12)])
        filler.fill_mains(SEP2, 12)
        hours = [race.start.local.hour for race in filler.races()]
        self.assertTrue(hours and min(hours) >= 12)
        self.assertEqual(len(hours), 6)

    def test_a_date_short_of_open_hours_stays_short_with_no_make_up(self):
        # Hours 11 to 15 are open: only 11, 13 and 15 are two hours apart.
        filler = make_filler(events=[block(SEP2, 0, 11), block(SEP2, 17, 24)])
        self.assertEqual(hours_of(filler.free(SEP2)), [11, 12, 13, 14, 15])
        filler.fill_mains(SEP2, 4)
        self.assertEqual([race.start.local.hour for race in filler.races()], [11, 13, 15])
        self.assertEqual(filler.day_races[SEP2], 3)

    def test_main_races_are_never_less_than_two_hours_apart(self):
        filler = make_filler()
        filler.fill_mains(SEP2, 12, 12)
        filler.fill_mains(SEP3, 12, 12)
        times = [race.start.utc for race in filler.races()]
        for first, second in zip(times, times[1:]):
            self.assertGreaterEqual(second - first, sp.RACE_LENGTH)

    def test_the_most_hours_left_for_the_next_date_beat_the_picks_that_end_at_23(self):
        # Tomorrow has a featured race at 12:00, so it needs the even hours, and a race today
        # at 23:00 would take its 00:00 and leave it 11 races instead of 12.
        def run(next_wanted):
            filler = make_filler()
            put(filler, "a", SEP3, 12, featured=True)
            filler.fill_mains(SEP2, 12, next_wanted)
            return hours_of([race.start for race in filler.races() if race.start.local.date() == SEP2])

        self.assertEqual(run(12), list(range(0, 24, 2)))
        # Without a lookahead the date ends at 23:00, which keeps its gaps three hours wide.
        self.assertEqual(run(None), [0, *range(3, 24, 2)])
        # A next date that wants few races does not need its midnight.
        self.assertEqual(run(3), [0, *range(3, 24, 2)])

    def test_twelve_races_a_day_two_dates_in_a_row_reach_twelve_each(self):
        filler = make_filler("abcd")
        filler.fill_mains(SEP2, 12, 12)
        filler.fill_mains(SEP3, 12)
        self.assertEqual((filler.day_races[SEP2], filler.day_races[SEP3]), (12, 12))
        self.assertEqual(filler.empty, 0)

    def test_a_featured_between_race_makes_the_date_pick_a_main_race_on_each_side(self):
        filler = make_filler("abcm", between="m")
        put(filler, "m", SEP2, 15, featured=True)
        filler.fill_mains(SEP2, 7)
        hours = {race.start.local.hour for race in filler.races() if not race.between}
        self.assertTrue(hours & {13, 14}, hours)
        self.assertTrue(hours & {16, 17}, hours)
        self.assertEqual(filler.day_races[SEP2], 7)
        self.assertEqual(filler.unbracketed(), 0)

    def test_a_main_race_already_placed_on_one_side_counts_as_that_side_of_the_bracket(self):
        filler = make_filler("abcm", between="m", events=[block(SEP2, 20, 24)])
        put(filler, "m", SEP2, 15, featured=True)
        put(filler, "a", SEP2, 13, featured=True)
        filler.fill_mains(SEP2, 4)
        hours = {race.start.local.hour for race in filler.races() if not race.between}
        self.assertIn(13, hours)
        self.assertTrue(hours & {16, 17}, hours)
        self.assertEqual(filler.unbracketed(), 0)

    def test_without_a_bracket_the_date_picks_without_it_and_the_race_is_unbracketed(self):
        filler = make_filler("abcm", between="m", events=[block(SEP2, 11, 15)])
        put(filler, "m", SEP2, 15, featured=True)
        filler.fill_mains(SEP2, 4)
        self.assertEqual(filler.day_races[SEP2], 4)
        self.assertEqual(filler.unbracketed(), 1)

    def test_short_gaps_can_be_switched_off(self):
        def picks(rules):
            filler = make_filler(rules=rules)
            filler.fill_mains(SEP2, 12)
            return hours_of([race.start for race in filler.races()]), filler.short_gaps()

        wide_hours, wide_short = picks(sp.SHIPPED)
        loose_hours, loose_short = picks(sp.Rules(wide_gaps=False))
        # Twelve races a day are two hours apart: the term cannot be kept, and the dearer odd
        # hours win when it is off.
        self.assertEqual(loose_hours, list(range(1, 24, 2)))
        self.assertEqual(wide_hours, [0, *range(3, 24, 2)])
        self.assertGreater(loose_short, wide_short)


class DueTests(unittest.TestCase):
    def test_a_between_category_is_due_at_or_below_the_pace_of_the_band(self):
        filler = make_filler("abm", between="m")
        self.assertTrue(filler.due("m", "prime"))
        for name, hour in (("a", 12), ("b", 14), ("a", 16)):
            put(filler, name, SEP2, hour)
        # Three prime races over a summed weight of 3 is a pace of 1: m at 0 is due.
        self.assertTrue(filler.due("m", "prime"))
        put(filler, "m", SEP2, 13)
        # Four races now, a pace of 4/3; m is at 1, due.
        self.assertTrue(filler.due("m", "prime"))
        put(filler, "m", SEP3, 13)
        # Five races, a pace of 5/3, and m is at 2.
        self.assertFalse(filler.due("m", "prime"))
        # Other bands have their own count.
        self.assertTrue(filler.due("m", "normal"))
        self.assertTrue(filler.due("m", "quiet"))

    def test_a_ratio_equal_to_the_pace_is_due(self):
        filler = make_filler("abm", between="m")
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 14)
        put(filler, "m", SEP2, 13)
        # Three races over weight 3: pace 1, and m's ratio 1.
        self.assertTrue(filler.due("m", "prime"))

    def test_the_pace_counts_every_category_over_the_summed_weight_of_all_of_them(self):
        weights = {"a": {band: 2.0 for band in sp.BANDS}, "b": {band: 2.0 for band in sp.BANDS},
                   "m": {band: 1.0 for band in sp.BANDS}}
        filler = make_filler("abm", between="m", weights=weights)
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 14)
        put(filler, "a", SEP2, 16)
        # Pace 3/5 = 0.6 against m's 0: due; after one race m's ratio is 1 over weight 1.
        self.assertTrue(filler.due("m", "prime"))
        put(filler, "m", SEP3, 13)
        self.assertFalse(filler.due("m", "prime"))
        put(filler, "b", SEP3, 15)
        put(filler, "a", SEP3, 17)
        put(filler, "b", SEP4, 12)
        self.assertTrue(filler.due("m", "prime"))   # 6 races over 5 = 1.2 against 1

    def test_a_category_of_weight_zero_in_the_band_is_never_due(self):
        weights = {"a": {band: 1.0 for band in sp.BANDS},
                   "m": {"prime": 0.0, "normal": 1.0, "quiet": 1.0}}
        filler = make_filler("am", between="m", weights=weights)
        self.assertFalse(filler.due("m", "prime"))
        put(filler, "a", SEP2, 12)
        self.assertFalse(filler.due("m", "prime"))
        self.assertTrue(filler.due("m", "normal"))


class BetweenRaceTests(unittest.TestCase):
    """fill_between with a main category pair a, b and a between category m."""

    @staticmethod
    def mains(*hours, day=SEP2, **kwargs):
        filler = make_filler("abm", between="m", **kwargs)
        for index, hour in enumerate(hours):
            put(filler, "ab"[index % 2], day, hour)
        return filler

    @staticmethod
    def between_hours(filler):
        return [race.start.local.hour for race in filler.races() if race.between]

    def test_a_between_race_goes_one_or_two_hours_after_a_main_race_and_before_the_next(self):
        for first, second, expected in ((12, 14, {13}), (12, 15, {13, 14}), (12, 16, {14})):
            with self.subTest(first=first, second=second):
                filler = self.mains(first, second)
                filler.fill_between(SEP2)
                (hour,) = self.between_hours(filler)
                self.assertIn(hour, expected)

    def test_no_gap_hour_means_no_between_race(self):
        for hours in ((12, 17), (12,), ()):
            with self.subTest(hours=hours):
                filler = self.mains(*hours)
                filler.fill_between(SEP2)
                self.assertEqual(self.between_hours(filler), [])

    def test_a_gap_holds_one_between_race_whatever_the_limits(self):
        filler = make_filler("abmn", between="mn")
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 15)
        filler.fill_between(SEP2)
        self.assertEqual(len([race for race in filler.races() if race.between]), 1)

    def test_every_gap_between_consecutive_main_races_gets_at_most_one(self):
        filler = self.mains(10, 12, 14, 16, 19)
        filler.fill_between(SEP2)
        hours = self.between_hours(filler)
        # Five gaps, but m is due only while it holds at most its share of each band: the two
        # dearest prime gaps, then a normal one once prime time has its share.
        self.assertEqual(sorted(hours), [11, 15, 17])
        self.assertEqual(filler.unbracketed(), 0)
        for hour in hours:
            self.assertTrue(filler.bracketed(filler.start(SEP2, hour).utc))

    def test_with_weights_that_let_it_race_every_gap_is_used(self):
        weights = {"a": {band: 1.0 for band in sp.BANDS}, "b": {band: 1.0 for band in sp.BANDS},
                   "m": {band: 100.0 for band in sp.BANDS}}
        filler = self.mains(10, 12, 14, 16, 19, weights=weights)
        filler.fill_between(SEP2)
        hours = self.between_hours(filler)
        self.assertEqual(sorted(hours), [11, 13, 15, 17])

    def test_a_gap_is_between_consecutive_main_races_only(self):
        # 11 is one hour after 10 but three before 14.
        filler = self.mains(10, 14)
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [12])

    def test_gap_hours_go_by_band_then_value_then_time(self):
        quiet_first = fx.bands_by(lambda group, hour: (
            "quiet" if hour < 6 else "prime" if 12 <= hour < 18 else "normal",
            {13: 1.0, 20: 9.0, 4: 20.0}.get(hour, 1.0)))
        # A prime gap at 13 beats the dearer normal gap at 20 and the dearest quiet one at 4.
        filler = self.mains(3, 5, 12, 14, 19, 21, bands=quiet_first, limits={"m": {"daily_max": 1}})
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [13])
        # Normal before quiet, whatever the value.
        filler = self.mains(3, 5, 19, 21, bands=quiet_first, limits={"m": {"daily_max": 1}})
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [20])
        # Within a band, the higher value first.
        filler = self.mains(12, 14, 15, 17, limits={"m": {"daily_max": 1}})
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [16])
        # Equal values take the earlier time.
        flat = fx.bands_by(lambda group, hour: ("normal", 1.0))
        filler = self.mains(8, 10, 14, 16, bands=flat, limits={"m": {"daily_max": 1}})
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [9])

    def test_a_closed_band_is_never_used(self):
        filler = self.mains(3, 5, 9, 11, open_bands={"a": sp.BANDS, "b": sp.BANDS, "m": ("prime", "normal")})
        filler.fill_between(SEP2)
        hours = self.between_hours(filler)
        # Quiet hour 4 is closed to m; hour 7 (2 and 2 hours away) and 10 are not.
        self.assertNotIn(4, hours)
        self.assertEqual(sorted(hours), [7, 10])
        only_prime = self.mains(3, 5, 9, 11, open_bands={"a": sp.BANDS, "b": sp.BANDS, "m": ("prime",)})
        only_prime.fill_between(SEP2)
        self.assertEqual(self.between_hours(only_prime), [])

    def test_only_a_due_category_races(self):
        filler = self.mains(12, 14, 16)
        filler.fill_between(SEP2)
        # Both gaps, 13 and 15, are filled while m is due.
        self.assertEqual(sorted(self.between_hours(filler)), [13, 15])
        zero = make_filler("abm", between="m", weights={"a": {b: 1.0 for b in sp.BANDS},
                                                      "b": {b: 1.0 for b in sp.BANDS},
                                                      "m": {b: 0.0 for b in sp.BANDS}})
        put(zero, "a", SEP2, 12)
        put(zero, "b", SEP2, 14)
        zero.fill_between(SEP2)
        self.assertEqual(self.between_hours(zero), [])

    def test_a_category_over_its_share_stops_racing(self):
        filler = self.mains(12, 14, 16, 18, 20, 22)
        filler.fill_between(SEP2)
        hours = self.between_hours(filler)
        # Prime time and normal hours each hold three main races, so m is due for two races
        # in each band and no more: the dearest gaps of each.
        self.assertEqual(sorted(hours), [15, 17, 19, 21])
        self.assertTrue(all(filler.bracketed(filler.start(SEP2, hour).utc) for hour in hours))

    def test_daily_and_weekly_limits_hold(self):
        daily = self.mains(10, 12, 14, 16, 19, limits={"m": {"daily_max": 2}})
        daily.fill_between(SEP2)
        self.assertEqual(len(self.between_hours(daily)), 2)
        weekly = make_filler("abm", between="m", limits={"m": {"weekly_max": 1}})
        for day in (SEP2, SEP3):
            put(weekly, "a", day, 12)
            put(weekly, "b", day, 14)
        weekly.fill_between(SEP2)
        weekly.fill_between(SEP3)
        self.assertEqual(len(self.between_hours(weekly)), 1)
        # The next Monday starts a new week.
        put(weekly, "a", SEP7, 12)
        put(weekly, "b", SEP7, 14)
        weekly.fill_between(SEP7)
        self.assertEqual(len(self.between_hours(weekly)), 2)

    def test_an_avoid_event_keeps_a_between_category_out_of_a_gap(self):
        avoid = block(SEP2, 12, 15, effect="avoid", name="av", categories="m")
        filler = self.mains(12, 14, 16, events=[avoid])
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [15])

    def test_a_block_event_keeps_a_gap_hour_closed(self):
        filler = self.mains(12, 14, events=[block(SEP2, 13, 14, name="b")])
        # The event touches the start at 13 (and 12, where a main race was put by hand).
        self.assertNotIn(13, hours_of(filler.free(SEP2)))
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [])

    def test_a_single_event_closes_gaps_with_a_race_less_than_two_hours_away(self):
        everything = block(SEP2, 0, 24, effect="single", name="s")
        close = self.mains(12, 14, events=[everything])
        close.fill_between(SEP2)
        self.assertEqual(self.between_hours(close), [])
        # A gap two hours from both main races has no race that close.
        wide = self.mains(12, 16, events=[everything])
        wide.fill_between(SEP2)
        self.assertEqual(self.between_hours(wide), [14])
        # An event elsewhere does nothing.
        far = self.mains(12, 14, events=[block(SEP2, 20, 22, effect="single", name="s")])
        far.fill_between(SEP2)
        self.assertEqual(self.between_hours(far), [13])

    def test_the_gap_across_midnight_is_filled_with_the_next_dates_hours(self):
        filler = make_filler("abm", between="m")
        put(filler, "a", SEP2, 23)
        put(filler, "b", SEP3, 1)
        # Midnight is the next date's hour, so the earlier date's pass leaves it.
        filler.fill_between(SEP2)
        self.assertEqual(self.between_hours(filler), [])
        filler.fill_between(SEP3)
        self.assertEqual(self.between_hours(filler), [0])
        self.assertEqual(filler.unbracketed(), 0)

    def test_a_category_without_between_categories_places_nothing(self):
        filler = make_filler("abc")
        put(filler, "a", SEP2, 12)
        put(filler, "b", SEP2, 14)
        filler.fill_between(SEP2)
        self.assertEqual(len(filler.races()), 2)

    def test_between_categories_take_turns_by_the_categories_rules(self):
        filler = make_filler("abmn", between="mn")
        for day in (SEP2, SEP3):
            for hour, name in ((12, "a"), (14, "b"), (16, "a"), (18, "b")):
                put(filler, name, day, hour)
        filler.fill_between(SEP2)
        names = [race.category for race in filler.races() if race.between]
        self.assertTrue(names)
        for first, second in zip(names, names[1:]):
            self.assertNotEqual(first, second)

    def test_a_between_race_never_sits_next_to_a_between_race_of_its_own_category(self):
        filler = self.mains(12, 14)
        put(filler, "m", SEP2, 20)
        filler.fill_between(SEP2)
        self.assertEqual(sorted(self.between_hours(filler)), [13, 20])
        # No gap holds a second race.
        self.assertEqual(filler.unbracketed(), 1)


class RulesBetweenTests(unittest.TestCase):
    def test_between_categories_are_main_categories_with_the_between_lever_off(self):
        on = make_filler("abm", between="m")
        self.assertEqual((on.main_names, on.between_names), (["a", "b"], ["m"]))
        off = make_filler("abm", between="m", rules=sp.Rules(between=False))
        self.assertEqual((off.main_names, off.between_names), (["a", "b", "m"], []))
        put(off, "m", SEP2, 12)
        self.assertFalse(off.races()[0].between)
        self.assertEqual(off.day_races[SEP2], 1)
        off.fill_between(SEP2)
        self.assertEqual(len(off.races()), 1)
        put(on, "m", SEP2, 12)
        self.assertTrue(on.races()[0].between)
        self.assertEqual(on.day_races[SEP2], 0)

    def test_a_featured_race_of_a_between_category_is_a_main_race_with_the_lever_off(self):
        filler = make_filler("abm", between="m", rules=sp.Rules(between=False))
        wanted = {day: 1 for day in SEPTEMBER}
        self.assertEqual(filler.place_featured({"m": (0, 14)}, SEPTEMBER, wanted), {})
        self.assertEqual(len(filler.races()), 4)
        self.assertFalse(any(race.between for race in filler.races()))

    def test_main_categories_fill_mains_with_the_lever_off(self):
        filler = make_filler("abm", between="m", rules=sp.Rules(between=False))
        filler.fill_mains(SEP2, 6)
        self.assertEqual(filler.day_races[SEP2], 6)
        self.assertIn("m", {race.category for race in filler.races()})
        on = make_filler("abm", between="m")
        on.fill_mains(SEP2, 6)
        self.assertNotIn("m", {race.category for race in on.races()})


class ClashTests(unittest.TestCase):
    PAIRS = {frozenset(("a", "b")): sp.PairScore(0.5, False),
             frozenset(("a", "c")): sp.PairScore(0.8, False)}

    def test_two_races_less_than_two_hours_apart_cost_the_score_times_the_smaller_expected(self):
        lost = sp.clash([clock(SEP2, 12), clock(SEP2, 13)], ["a", "b"], [10.0, 4.0], self.PAIRS)
        self.assertAlmostEqual(lost, 0.5 * 4.0)
        lost = sp.clash([clock(SEP2, 12), clock(SEP2, 13)], ["a", "b"], [4.0, 10.0], self.PAIRS)
        self.assertAlmostEqual(lost, 0.5 * 4.0)

    def test_races_two_hours_or_more_apart_cost_nothing(self):
        self.assertEqual(sp.clash([clock(SEP2, 12), clock(SEP2, 14)], ["a", "b"], [5.0, 5.0], self.PAIRS), 0.0)
        self.assertEqual(sp.clash([clock(SEP2, 12), clock(SEP2, 20)], ["a", "b"], [5.0, 5.0], self.PAIRS), 0.0)
        self.assertGreater(
            sp.clash([clock(SEP2, 12), clock(SEP2, 13, 59)], ["a", "b"], [5.0, 5.0], self.PAIRS), 0.0)
        self.assertEqual(sp.clash([], [], [], self.PAIRS), 0.0)
        self.assertEqual(sp.clash([clock(SEP2, 12)], ["a"], [5.0], self.PAIRS), 0.0)

    def test_a_missing_pair_or_score_counts_as_one(self):
        lost = sp.clash([clock(SEP2, 12), clock(SEP2, 13)], ["b", "c"], [10.0, 4.0], self.PAIRS)
        self.assertAlmostEqual(lost, 4.0)
        pairs = {frozenset(("a", "b")): sp.PairScore(None, True)}
        lost = sp.clash([clock(SEP2, 12), clock(SEP2, 13)], ["a", "b"], [10.0, 4.0], pairs)
        self.assertAlmostEqual(lost, 4.0)
        self.assertAlmostEqual(
            sp.clash([clock(SEP2, 12), clock(SEP2, 13)], ["a", "b"], [10.0, 4.0], {}), 4.0)

    def test_each_races_charge_is_at_most_its_own_expected_entrants(self):
        # x at 12:30 is near two bigger races, each of which would charge it all of 2.
        pairs = {frozenset(("x", "y")): sp.PairScore(1.0, False)}
        starts = [clock(SEP2, 11, 30), clock(SEP2, 12, 30), clock(SEP2, 13, 30)]
        lost = sp.clash(starts, ["y", "x", "y"], [10.0, 2.0, 10.0], pairs)
        self.assertAlmostEqual(lost, 2.0)
        # Three mutual neighbours of equal size: each charge is capped at 5.
        starts = [clock(SEP2, 12), clock(SEP2, 12, 30), clock(SEP2, 13)]
        lost = sp.clash(starts, ["a", "b", "c"], [5.0, 5.0, 5.0],
                        {frozenset(p): sp.PairScore(1.0, False) for p in (("a", "b"), ("a", "c"), ("b", "c"))})
        self.assertAlmostEqual(lost, 10.0)

    def test_a_tie_is_charged_to_the_later_race(self):
        # z, a and b: z (the largest) is near a only, a and b tie. Charged to the later b, a
        # keeps only z's charge of 4; charged to the earlier a, it would be capped at 5.
        pairs = {frozenset(p): sp.PairScore(0.8, False) for p in (("z", "a"), ("a", "b"))}
        starts = [clock(SEP2, 10, 30), clock(SEP2, 12), clock(SEP2, 12, 30)]
        lost = sp.clash(starts, ["z", "a", "b"], [9.0, 5.0, 5.0], pairs)
        self.assertAlmostEqual(lost, 0.8 * 5 + 0.8 * 5)

    def test_the_order_of_the_inputs_does_not_matter(self):
        pairs = {frozenset(p): sp.PairScore(0.8, False) for p in (("z", "a"), ("a", "b"))}
        starts = [clock(SEP2, 12, 30), clock(SEP2, 10, 30), clock(SEP2, 12)]
        lost = sp.clash(starts, ["b", "z", "a"], [5.0, 9.0, 5.0], pairs)
        self.assertAlmostEqual(lost, 8.0)


class BetweenProposalTests(unittest.TestCase):
    """plan_proposal over hand-built inputs, without the fixture world."""

    @staticmethod
    def plan(names="abcdm", between="m", *, per_day=12, events=(), rules=sp.SHIPPED, layout=None,
             start=SEP2, limits=None):
        categories = [plain_category(n, between=n in between, **(limits or {}).get(n, {})) for n in names]
        bands = layout or fx.LAYOUT
        # Five weeks from Wednesday 2 September, racing until the last Thursday: 1 October.
        period_start, period_end = sp.season_bounds(start, 5, sp.WEEKDAYS.index("thursday"))
        return sp.plan_proposal(
            sp.RaceSet("t", {"work": per_day, "casual": per_day, "prime": per_day}),
            history=BetweenProposalTests.history(names), model=flat_model(list(names)),
            categories=categories,
            layout={key: band for key, (band, _value) in bands.items()},
            pairs=sp.category_pairs(categories, {}), events=list(events),
            period_start=period_start, period_end=period_end, rules=rules,
        )

    @staticmethod
    def history(names):
        """Every category fired in every band, so none is closed."""
        entries = []
        for name in names:
            for day in range(1, 8):
                for hour in (3, 9, 14):
                    entries.append((name, 1, datetime(2026, 8, day, hour, tzinfo=EASTERN), 5, True))
        return history(entries)

    def test_between_races_come_on_top_of_the_main_races_of_every_date(self):
        plan = self.plan(per_day=7)
        per_date = Counter(r["date"] for r in plan["schedule"] if not r["between"])
        self.assertEqual(set(per_date.values()), {7})
        self.assertEqual(plan["shortfall"], [])
        self.assertGreater(plan["between"], 0)
        self.assertEqual(plan["races"], len(plan["schedule"]))
        self.assertEqual(plan["races"], sum(per_date.values()) + plan["between"])
        self.assertEqual(plan["between"], sum(r["between"] for r in plan["schedule"]))
        self.assertEqual(plan["between_categories"], ["m"])
        self.assertEqual({r["category"] for r in plan["schedule"] if r["between"]}, {"m"})
        self.assertEqual(plan["day_races"], {"fewest": 7, "most": 7})

    def test_every_between_race_is_bracketed_and_the_main_races_stay_two_hours_apart(self):
        plan = self.plan(per_day=7)
        self.assertEqual(plan["unbracketed"], 0)
        mains = [race_time(r) for r in plan["schedule"] if not r["between"]]
        for first, second in zip(mains, mains[1:]):
            self.assertGreaterEqual(second - first, sp.RACE_LENGTH)
        for r in plan["schedule"]:
            if not r["between"]:
                continue
            at = race_time(r)
            before = max(t for t in mains if t < at)
            after = min(t for t in mains if t > at)
            self.assertTrue(timedelta(hours=1) <= at - before <= sp.RACE_LENGTH, r["utc"])
            self.assertTrue(timedelta(hours=1) <= after - at <= sp.RACE_LENGTH, r["utc"])
        between = sorted(race_time(r) for r in plan["schedule"] if r["between"])
        for first, second in zip(between, between[1:]):
            gap = [t for t in mains if first < t < second]
            self.assertTrue(gap)

    def test_with_twelve_races_a_day_the_gap_across_midnight_is_filled(self):
        plan = self.plan(per_day=12, rules=sp.Rules(featured=False))
        self.assertEqual(plan["shortfall"], [])
        self.assertEqual(plan["unbracketed"], 0)
        # 23:00 and 01:00 on either side of midnight leave 00:00 to a between race.
        late = [r for r in plan["schedule"] if r["between"] and r["hour"] == 0 and r["date"] > "2026-09-02"]
        self.assertTrue(late)
        # Every date's twelve main races still fit.
        per_date = Counter(r["date"] for r in plan["schedule"] if not r["between"])
        self.assertEqual(set(per_date.values()), {12})

    def test_a_date_never_ends_so_late_that_the_next_loses_races(self):
        plan = self.plan(names="abcd", between="", per_day=12, rules=sp.Rules(featured=False))
        self.assertEqual(plan["shortfall"], [])
        self.assertEqual(plan["empty_hours"], 0)
        self.assertEqual(plan["day_races"], {"fewest": 12, "most": 12})

    def test_the_description_says_main_races_and_mentions_between_races_only_with_them(self):
        with_between = self.plan(per_day=7)
        without = self.plan(names="abc", between="", per_day=7)
        self.assertIn("main races a day", with_between["description"])
        self.assertTrue(with_between["description"].endswith(" Between races come on top."))
        self.assertIn("main races a day", without["description"])
        self.assertNotIn("Between races", without["description"])
        self.assertEqual(without["between"], 0)
        self.assertEqual(without["between_categories"], [])

    def test_the_between_flag_marks_the_composition_the_featured_and_the_split(self):
        plan = self.plan(per_day=7)
        flags = {c["category"]: c["between"] for c in plan["composition"]}
        self.assertEqual(flags, {"a": False, "b": False, "c": False, "d": False, "m": True})
        self.assertEqual({f["category"]: f["between"] for f in plan["featured"]},
                         {"a": False, "b": False, "c": False, "d": False, "m": True})
        counts = {c["category"]: c for c in plan["categories"]}
        self.assertEqual(counts["m"]["between"], plan["between"])
        self.assertEqual(counts["a"]["between"], 0)
        for key in ("races", "between", "between_categories", "short_gaps", "unbracketed"):
            self.assertIn(key, plan)

    def test_a_featured_between_race_has_a_main_race_on_each_side(self):
        plan = self.plan(per_day=7)
        featured = [r for r in plan["schedule"] if r["featured"] and r["category"] == "m"]
        self.assertTrue(featured)
        self.assertTrue(all(r["between"] for r in featured))
        self.assertEqual(plan["unbracketed"], 0)

    def test_a_plan_without_trouble_has_no_notice(self):
        plan = self.plan(per_day=7)
        self.assertEqual((plan["shortfall"], plan["empty_hours"], plan["unbracketed"]), ([], 0, 0))
        self.assertIsNone(plan["notice"])

    def test_the_notice_counts_a_shortfall_date(self):
        closed = block(SEP3, 0, 24)
        plan = self.plan(per_day=7, events=[closed])
        self.assertEqual([s["date"] for s in plan["shortfall"]], ["2026-09-03"])
        self.assertEqual(plan["shortfall"][0], {"date": "2026-09-03", "races": 0, "wanted": 7})
        self.assertIn("On 1 of 30 dates fewer main races fit", plan["notice"])
        self.assertEqual(plan["day_races"]["fewest"], 0)

    def test_the_between_lever_makes_every_category_main(self):
        plan = self.plan(per_day=7, rules=sp.Rules(between=False))
        self.assertEqual(plan["between"], 0)
        self.assertEqual(plan["between_categories"], [])
        self.assertEqual(plan["races"], len(plan["schedule"]))
        per_date = Counter(r["date"] for r in plan["schedule"])
        self.assertEqual(set(per_date.values()), {7})
        self.assertIn("m", {r["category"] for r in plan["schedule"]})
        self.assertNotIn("Between", plan["description"])
        self.assertTrue(all(not c["between"] for c in plan["composition"]))

    def test_short_gaps_are_counted_as_the_lever_says(self):
        shipped = self.plan(per_day=7)
        loose = self.plan(per_day=7, rules=sp.Rules(wide_gaps=False))
        mains = sorted((race_time(r), r["band"]) for r in shipped["schedule"] if not r["between"])
        short = sum(
            later - earlier < sp.WIDE_GAP and not (a == "prime" and b == "prime")
            for (earlier, a), (later, b) in zip(mains, mains[1:])
        )
        self.assertEqual(shipped["short_gaps"], short)
        self.assertGreaterEqual(loose["short_gaps"], shipped["short_gaps"])


class WorldCase(unittest.TestCase):
    """Whole-world checks on the fixture world's four sets."""

    results = staticmethod(lambda: {"base": main_result(), "mixed": mixed_result(), "limits": limit_result()})

    def every_proposal(self):
        for key, result in self.results().items():
            for p in result["proposals"]:
                yield f"{key}/{p['set']}", result, p


def week_start(day):
    return day - timedelta(days=day.weekday())


def race_day(race):
    return date.fromisoformat(race["date"])


def race_time(race):
    return datetime.fromisoformat(race["utc"].replace("Z", "+00:00"))


class DaylightSavingProposalTests(unittest.TestCase):
    """Whole proposals over a daylight-saving change keep every placement rule."""

    def check(self, start, changed, missing_hour=None):
        result = fx.cached(f"dst-{start}", fx.BETWEEN_COMPOSITION, events=fx.EVENTS, start=start)
        for p in result["proposals"]:
            with self.subTest(start=start, set=p["set"]):
                rows = p["schedule"]
                starts = [race_time(r) for r in rows]
                self.assertEqual(starts, sorted(starts))
                self.assertEqual(len(set(starts)), len(starts))
                mains = [t for t, r in zip(starts, rows) if not r["between"]]
                for first, second in zip(mains, mains[1:]):
                    self.assertGreaterEqual(second - first, sp.RACE_LENGTH)
                per_date = Counter(r["date"] for r in rows if not r["between"])
                short = {s["date"] for s in p["shortfall"]}
                for day in sp.period_dates(*fx.period(start)):
                    wanted = p["per_day"][sp.day_group(day)]
                    if day.isoformat() not in short:
                        self.assertEqual(per_date[day.isoformat()], wanted, day)
                featured = {f["category"]: (f["weekday"], f["hour"]) for f in p["featured"]}
                for r in rows:
                    if r["featured"]:
                        self.assertEqual((r["weekday"], r["hour"]), featured[r["category"]])
                    if r["between"] and not r["featured"]:
                        at = race_time(r)
                        before = max((t for t in mains if t < at), default=None)
                        after = min((t for t in mains if t > at), default=None)
                        self.assertTrue(before and timedelta(hours=1) <= at - before <= sp.RACE_LENGTH, r["utc"])
                        self.assertTrue(after and timedelta(hours=1) <= after - at <= sp.RACE_LENGTH, r["utc"])
                on_change = [r for r in rows if r["date"] == changed.isoformat()]
                self.assertTrue(on_change)
                hours = [r["hour"] for r in on_change]
                self.assertEqual(len(hours), len(set(hours)), "a repeated hour runs once")
                if missing_hour is not None:
                    self.assertNotIn(missing_hour, hours)

    def test_a_season_over_the_autumn_change_keeps_every_rule(self):
        self.check(date(2026, 10, 20), date(2026, 11, 1))

    def test_a_season_over_the_spring_change_keeps_every_rule(self):
        self.check(date(2027, 3, 1), date(2027, 3, 14), missing_hour=2)


class SetProposalTests(WorldCase):
    def test_the_four_sets_run_light_medium_full_bonanza(self):
        expected = {
            "light": ({"work": 7, "casual": 8, "prime": 9}, 55),
            "medium": ({"work": 8, "casual": 9, "prime": 10}, 62),
            "full": ({"work": 9, "casual": 10, "prime": 11}, 69),
            "bonanza": ({"work": 10, "casual": 11, "prime": 12}, 76),
        }
        for key, result in self.results().items():
            with self.subTest(key):
                self.assertEqual([p["set"] for p in result["proposals"]], list(expected))
                for p in result["proposals"]:
                    per_day, weekly = expected[p["set"]]
                    self.assertEqual(p["per_day"], per_day)
                    self.assertEqual(p["weekly_races"], weekly)
                    self.assertEqual(sp.SETS[list(expected).index(p["set"])].weekly, weekly)

    def test_every_date_has_its_groups_main_races_unless_it_is_listed_in_the_shortfall(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                per_date = Counter(r["date"] for r in p["schedule"] if not r["between"])
                short = {s["date"]: s for s in p["shortfall"]}
                self.assertEqual(p["days"], 30)
                for day in sp.period_dates(*fx.PERIOD):
                    wanted = p["per_day"][sp.day_group(day)]
                    count = per_date[day.isoformat()]
                    if day.isoformat() in short:
                        self.assertEqual(short[day.isoformat()],
                                         {"date": day.isoformat(), "races": count, "wanted": wanted})
                        self.assertLess(count, wanted)
                    else:
                        self.assertEqual(count, wanted, day)
                dates = sp.period_dates(*fx.PERIOD)
                self.assertEqual(p["day_races"]["fewest"], min(per_date[d.isoformat()] for d in dates))
                self.assertEqual(p["day_races"]["most"], max(per_date[d.isoformat()] for d in dates))

    def test_a_date_without_main_races_counts_as_the_fewest(self):
        closed = {"name": "Closed", "date": "2026-09-15", "start": "00:00",
                  "timezone": "America/New_York", "hours": 24, "effect": "block"}
        result = fx.run(fx.BASE_COMPOSITION, events=[*fx.EVENTS, closed])
        for p in result["proposals"]:
            with self.subTest(p["set"]):
                self.assertEqual(p["day_races"]["fewest"], 0)
                short = {s["date"]: s["races"] for s in p["shortfall"]}
                self.assertEqual(short["2026-09-15"], 0)
                self.assertFalse(any(r["date"] == "2026-09-15" for r in p["schedule"]))
                self.assertIn(f"{len(p['shortfall'])} of 30 dates", p["notice"])

    def test_labor_day_takes_the_prime_groups_races(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                self.assertEqual(p["holidays"], ["2026-09-07"])
                labor = [r for r in p["schedule"] if r["date"] == "2026-09-07"]
                short = {s["date"]: s for s in p["shortfall"]}
                self.assertTrue(all(r["group"] == "prime" for r in labor))
                mains = [r for r in labor if not r["between"]]
                wanted = short["2026-09-07"]["wanted"] if "2026-09-07" in short else len(mains)
                self.assertEqual(wanted, p["per_day"]["prime"])

    def test_the_tournament_date_may_run_short_but_never_inside_the_block(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                self.assertEqual(p["event_conflicts"], 0)
                for r in p["schedule"]:
                    if r["date"] == fx.TOURNAMENT:
                        self.assertTrue(r["hour"] < 13 or r["hour"] >= 19, r["hour"])

    def test_races_come_in_time_order_inside_the_period(self):
        for label, result, p in self.every_proposal():
            with self.subTest(label):
                times = [race_time(r) for r in p["schedule"]]
                self.assertEqual(times, sorted(times))
                self.assertEqual(p["races"], len(p["schedule"]))
                self.assertLess(p["races"], result["period"]["today_races"])
                for r in p["schedule"]:
                    self.assertTrue("2026-09-01" <= r["date"] <= "2026-09-30", r["date"])

    def test_bigger_sets_hold_more_races(self):
        for result in self.results().values():
            counts = [p["races"] for p in result["proposals"]]
            self.assertEqual(counts, sorted(counts))
            self.assertEqual(len(set(counts)), 4)

    def test_the_bands_are_the_plan_files_bands(self):
        for _label, _result, p in self.every_proposal():
            self.assertEqual(len(p["bands"]), 72)
            for b in p["bands"]:
                self.assertEqual(b["band"], fx.WORLD_LAYOUT[b["group"], b["hour"]])
                self.assertGreater(b["value"], 0)

    def test_each_race_carries_its_dates_group_and_that_groups_band(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                bands = {(b["group"], b["hour"]): b["band"] for b in p["bands"]}
                for r in p["schedule"]:
                    self.assertEqual(r["group"], sp.day_group(race_day(r)))
                    self.assertEqual(r["band"], bands[r["group"], r["hour"]])
                    self.assertEqual(r["time"], f"{r['hour']:02d}:00")
                    self.assertEqual(r["weekday"], race_day(r).weekday())

    def test_main_races_start_at_least_two_hours_apart(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                mains = [race_time(r) for r in p["schedule"] if not r["between"]]
                self.assertEqual(mains, sorted(mains))
                for first, second in zip(mains, mains[1:]):
                    self.assertGreaterEqual(second - first, sp.RACE_LENGTH)

    def test_the_composition_without_between_categories_has_no_between_races(self):
        for key in ("main", "mixed", "limits"):
            result = {"main": main_result, "mixed": mixed_result, "limits": limit_result}[key]()
            for p in result["proposals"]:
                self.assertEqual(p["between"], 0)
                self.assertEqual(p["between_categories"], [])
                self.assertEqual(p["unbracketed"], 0)
                self.assertFalse(any(r["between"] for r in p["schedule"]))
                self.assertEqual(p["races"], len(p["schedule"]))
                self.assertNotIn("Between", p["description"])
                self.assertIn("main races a day", p["description"])

    def test_no_category_races_back_to_back(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                names = [r["category"] for r in p["schedule"]]
                for first, second in zip(names, names[1:]):
                    self.assertNotEqual(first, second)

    def test_no_main_category_races_twice_in_a_row_among_the_main_races(self):
        for p in between_result()["proposals"]:
            with self.subTest(p["set"]):
                names = [r["category"] for r in p["schedule"] if not r["between"]]
                for first, second in zip(names, names[1:]):
                    self.assertNotEqual(first, second)

    def test_the_daily_and_weekly_caps_hold(self):
        p_limits = {"simple": (3, None), "entrance": (4, None), "keys": (None, 5), "mystery": (3, None)}
        for p in limit_result()["proposals"]:
            with self.subTest(p["set"]):
                daily, weekly = Counter(), Counter()
                for r in p["schedule"]:
                    daily[r["date"], r["category"]] += 1
                    weekly[week_start(race_day(r)), r["category"]] += 1
                for (day, name), count in daily.items():
                    cap = p_limits[name][0]
                    self.assertTrue(cap is None or count <= cap, (day, name, count))
                for (week, name), count in weekly.items():
                    cap = p_limits[name][1]
                    self.assertTrue(cap is None or count <= cap, (week, name, count))
                self.assertIn(5, {count for (_w, n), count in weekly.items() if n == "keys"})

    def test_the_week_cap_counts_monday_to_sunday_weeks_including_the_partial_ones(self):
        for p in limit_result()["proposals"]:
            keys = Counter(week_start(race_day(r)) for r in p["schedule"] if r["category"] == "keys")
            self.assertIn(date(2026, 8, 31), keys)
            self.assertLessEqual(max(keys.values()), 5)


class RaceModeTests(WorldCase):
    def test_prime_races_and_featured_races_run_the_favourite_mode_of_a_plain_category(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                favourite = {c["category"]: c["favourite_mode"] for c in p["composition"]}
                for r in p["schedule"]:
                    if favourite[r["category"]] is not None and (r["featured"] or r["band"] == "prime"):
                        self.assertEqual(r["mode"], favourite[r["category"]])

    def test_every_other_race_takes_the_modes_in_list_order(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                for c in p["composition"]:
                    turn = 0
                    for r in p["schedule"]:
                        if r["category"] != c["category"]:
                            continue
                        if not c["mixed"] and (r["featured"] or r["band"] == "prime"):
                            continue
                        self.assertEqual(r["mode"], c["modes"][turn % len(c["modes"])]["name"], label)
                        turn += 1

    def test_the_favourite_modes_are_the_most_played_with_the_best_factor(self):
        for p in main_result()["proposals"]:
            by_name = {c["category"]: c for c in p["composition"]}
            self.assertEqual(by_name["entrance"]["favourite_mode"], "Ent B")
            self.assertEqual(by_name["simple"]["favourite_mode"], "Simple Old")
            self.assertEqual(by_name["keys"]["favourite_mode"], "Keys A")

    def test_only_the_planned_modes_ever_race_under_their_own_source(self):
        plan = {"simple": ["Simple Old"], "entrance": ["Ent A", "Ent B"], "keys": ["Keys A"],
                "mystery": ["Myst A"], "throwback": ["Keys A", "Myst A"]}
        modes = {m["name"]: m for m in MODES}
        for _label, _result, p in self.every_proposal():
            for r in p["schedule"]:
                self.assertIn(r["mode"], plan[r["category"]])
                self.assertEqual(r["mode_id"], modes[r["mode"]]["id"])
                self.assertEqual(r["source"], schedule_archive.ARCHETYPE_CATEGORIES[modes[r["mode"]]["archetype"]])

    def test_a_mixed_categorys_races_count_under_both_sources_and_rotate_even_when_featured(self):
        for p in mixed_result()["proposals"]:
            throwback = next(c for c in p["composition"] if c["category"] == "throwback")
            self.assertTrue(throwback["mixed"])
            self.assertEqual(throwback["sources"], ["keys", "mystery"])
            self.assertIsNone(throwback["favourite_mode"])
            entry = next(e for e in p["featured"] if e["category"] == "throwback")
            self.assertEqual((entry["mode"], entry["mode_id"]), (None, None))
            self.assertEqual(entry["rotates"], ["Keys A", "Myst A"])
            races = [r for r in p["schedule"] if r["category"] == "throwback"]
            self.assertEqual({r["source"] for r in races}, {"keys", "mystery"})
            featured = [r for r in races if r["featured"]]
            self.assertTrue(featured)
            # Its races, featured ones included, rotate Keys A and Myst A in time order.
            for turn, r in enumerate(races):
                self.assertEqual(r["mode"], ["Keys A", "Myst A"][turn % 2])
            plain = next(e for e in p["featured"] if e["category"] == "simple")
            self.assertEqual(plain["rotates"], [])

    def test_a_race_expects_what_its_own_mode_and_source_draw(self):
        model = fitted()
        for p in (mixed_result()["proposals"][0], main_result()["proposals"][0]):
            factors = {m["name"]: m["factor"] for c in p["composition"] for m in c["modes"]}
            for r in p["schedule"][:60]:
                local = r_local(r)
                self.assertAlmostEqual(r["expected"], model.expected(r["source"], factors[r["mode"]], local))
                self.assertAlmostEqual(r["fill"], model.fill[r["source"]][local.hour // 4])


def r_local(race):
    return race_time(race).astimezone(EASTERN)


class FeaturedRaceTests(WorldCase):
    def test_every_category_has_a_weekday_of_its_own_and_races_there_all_season(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                names = [c["category"] for c in p["composition"]]
                self.assertEqual(sorted(e["category"] for e in p["featured"]), sorted(names))
                self.assertEqual(len({e["weekday"] for e in p["featured"]}), len(names))
                for entry in p["featured"]:
                    races = [r for r in p["schedule"] if r["featured"] and r["category"] == entry["category"]]
                    self.assertEqual(len(races), entry["races"])
                    self.assertTrue(races)
                    for r in races:
                        self.assertEqual((r["weekday"], r["hour"]), (entry["weekday"], entry["hour"]))
                    skipped = {r["date"] for r in races}
                    self.assertFalse(skipped & set(entry["skipped"]))

    def test_featured_hours_are_prime_hours_of_the_weekdays_own_group(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                bands = {(b["group"], b["hour"]): b["band"] for b in p["bands"]}
                for entry in p["featured"]:
                    group = sp.WEEKDAY_GROUPS[entry["weekday"]]
                    self.assertEqual(bands[group, entry["hour"]], "prime")

    def test_a_featured_race_on_a_holiday_keeps_its_weekday_hour(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                monday = [e for e in p["featured"] if e["weekday"] == 0]
                for entry in monday:
                    labor = [r for r in p["schedule"] if r["date"] == "2026-09-07"
                             and r["featured"] and r["category"] == entry["category"]]
                    self.assertTrue(labor or "2026-09-07" in entry["skipped"])

    def test_the_fixture_assignment_is_the_best_by_a_brute_force_search_of_the_spec(self):
        model, slots, chosen = fitted(), world_history(), fx.categories()
        bands = sp.group_bands(fx.WORLD_LAYOUT, model, chosen)
        record = Counter()
        count = Counter()
        for s in slots:
            local = s.started_at.astimezone(EASTERN)
            record[s.category, local.weekday(), local.hour] += s.entrants
            count[s.category, local.weekday(), local.hour] += 1
        score = {}
        for c in chosen:
            for weekday in range(7):
                group = sp.WEEKDAY_GROUPS[weekday]
                options = []
                for hour in range(24):
                    if bands[group, hour][0] != "prime":
                        continue
                    expected = model.at(c.sources[0], c.favourite_mode.factor, group, hour)
                    n = count[c.sources[0], weekday, hour]
                    value = (record[c.sources[0], weekday, hour] + 5 * expected) / (n + 5)
                    options.append((value, -hour))
                score[c.name, weekday] = max(options)
        best = max(
            sum(score[c.name, weekday][0] for c, weekday in zip(chosen, weekdays))
            for weekdays in itertools.permutations(range(7), len(chosen))
        )
        open_bands = {c.name: sp.BANDS for c in chosen}
        dates = sp.period_dates(*fx.PERIOD)
        result = sp.featured_slots(chosen, model, slots, bands, open_bands, [], dates)
        self.assertAlmostEqual(
            sum(score[name, weekday][0] for name, (weekday, _hour) in result.items()), best)
        for name, (weekday, hour) in result.items():
            self.assertEqual(hour, -score[name, weekday][1])

    def test_a_featured_race_in_a_weekly_avoid_event_never_gets_that_candidate(self):
        light = main_result()["proposals"][0]
        entrance = next(e for e in light["featured"] if e["category"] == "entrance")
        # The fixture's weekly Entrance event runs Monday 01:00 UTC, Sunday evening in Eastern time.
        self.assertNotEqual((entrance["weekday"], entrance["hour"]), (6, 20))
        self.assertEqual(entrance["skipped"], [])

    def test_a_featured_race_in_a_block_event_is_skipped_and_reported(self):
        light = main_result()["proposals"][0]
        entry = light["featured"][0]
        first = next(r for r in light["schedule"] if r["featured"] and r["category"] == entry["category"])
        block = {"name": "Block", "date": first["date"], "start": f"{entry['hour'] - 1:02d}:30",
                 "timezone": "America/New_York", "hours": 1, "effect": "block"}
        result = fx.cached("blocked", fx.BASE_COMPOSITION, events=[block])
        blocked = fx.proposal(result, "light")
        again = next(e for e in blocked["featured"] if e["category"] == entry["category"])
        self.assertEqual((again["weekday"], again["hour"]), (entry["weekday"], entry["hour"]))
        self.assertEqual(again["skipped"], [first["date"]])
        self.assertEqual(again["races"], entry["races"] - 1)
        self.assertEqual(blocked["event_conflicts"], 0)


class ResultTests(WorldCase):
    def clash(self, p):
        pairs = {frozenset((x["a"], x["b"])): x["score"] for x in p["pairs"]}
        schedule = p["schedule"]
        total = 0.0
        for index, first in enumerate(schedule):
            for second in schedule[index + 1:]:
                if race_time(second) - race_time(first) >= sp.RACE_LENGTH:
                    break
                total += pairs[frozenset((first["category"], second["category"]))] * min(
                    first["expected"], second["expected"])
        return total

    def test_expected_entrants_are_the_sum_of_the_races_less_the_clash(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                total = sum(r["expected"] for r in p["schedule"])
                self.assertAlmostEqual(p["expected_entrants"], total - self.clash(p))

    def test_expected_entrants_of_a_proposal_with_between_races_less_the_capped_clash(self):
        for p in between_result()["proposals"]:
            with self.subTest(p["set"]):
                pairs = {frozenset((x["a"], x["b"])): x["score"] for x in p["pairs"]}
                rows = p["schedule"]
                charged = [0.0] * len(rows)
                for i, first in enumerate(rows):
                    for j in range(i + 1, len(rows)):
                        second = rows[j]
                        if race_time(second) - race_time(first) >= sp.RACE_LENGTH:
                            break
                        score = pairs.get(frozenset((first["category"], second["category"])))
                        score = 1.0 if score is None else score
                        smaller = i if first["expected"] < second["expected"] else j
                        charged[smaller] += score * rows[smaller]["expected"]
                lost = sum(min(c, r["expected"]) for c, r in zip(charged, rows))
                self.assertGreater(lost, 0)
                self.assertAlmostEqual(
                    p["expected_entrants"], sum(r["expected"] for r in rows) - lost)

    def test_expected_cancelled_rooms_are_one_less_the_fill_of_each_race(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                self.assertAlmostEqual(
                    p["expected_cancelled"], sum(1 - r["fill"] for r in p["schedule"]))

    def test_weekly_repeats_count_races_whose_category_ran_at_that_time_a_week_before(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                by_time = {(r["date"], r["hour"]): r for r in p["schedule"]}
                repeats = 0
                for r in p["schedule"]:
                    before = by_time.get(((race_day(r) - timedelta(days=7)).isoformat(), r["hour"]))
                    if (before and before["category"] == r["category"]
                            and not r["featured"] and not before["featured"]):
                        repeats += 1
                self.assertEqual(p["repeats"], repeats)

    def test_the_categories_split_counts_every_race_once(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                self.assertEqual(sum(c["races"] for c in p["categories"]), p["races"])
                self.assertEqual(sum(c["between"] for c in p["categories"]), p["between"])
                for c in p["categories"]:
                    self.assertEqual(c["races"], c["prime"] + c["normal"] + c["quiet"])
                    mine = [r for r in p["schedule"] if r["category"] == c["category"]]
                    self.assertEqual(c["featured"], sum(r["featured"] for r in mine))
                    self.assertEqual(c["between"], sum(r["between"] for r in mine))
                    self.assertNotIn("paired", c)

    def test_the_notice_counts_shortfall_dates_empty_hours_and_unbracketed_races(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                notice = p["notice"] or ""
                if p["shortfall"]:
                    self.assertIn(f"{len(p['shortfall'])} of {p['days']} dates", notice)
                else:
                    self.assertNotIn("dates fewer", notice)
                if p["empty_hours"]:
                    self.assertIn(f"{p['empty_hours']} picked hours", notice)
                else:
                    self.assertNotIn("picked hours", notice)
                if p["unbracketed"]:
                    self.assertIn(f"{p['unbracketed']} between races", notice)
                else:
                    self.assertNotIn("between races lack", notice)
                if not p["shortfall"] and not p["empty_hours"] and not p["unbracketed"]:
                    self.assertIsNone(p["notice"])

    def test_the_empty_hours_and_the_shortfall_of_the_fixture_world_add_up(self):
        for label, _result, p in self.every_proposal():
            with self.subTest(label):
                lacking = sum(s["wanted"] - s["races"] for s in p["shortfall"])
                self.assertGreaterEqual(lacking, 0)
                # Every hour that stayed empty left its date a race short.
                self.assertLessEqual(p["empty_hours"], lacking)

    def test_a_run_is_deterministic(self):
        again = fx.run(fx.BASE_COMPOSITION, events=fx.EVENTS)
        self.assertEqual(again, main_result())

    def test_the_composition_lists_the_plan_in_order_with_limits_open_bands_and_weights(self):
        for p in limit_result()["proposals"]:
            limits = {c["category"]: (c["daily_max"], c["weekly_max"], c["only_prime"])
                      for c in p["composition"]}
            self.assertEqual(limits, {"simple": (3, None, False), "entrance": (4, None, False),
                                      "keys": (None, 5, False), "mystery": (3, None, False)})
            for c in p["composition"]:
                self.assertEqual(set(c["weights"]), set(sp.BANDS))
                for band in sp.BANDS:
                    self.assertEqual(c["weights"][band] > 0, band in c["open_bands"])
        for p in main_result()["proposals"]:
            self.assertEqual([c["category"] for c in p["composition"]],
                             ["simple", "entrance", "keys", "mystery"])

    def test_a_category_with_no_modes_is_left_out_and_reported(self):
        result = limit_result()
        self.assertEqual(result["left_out"], ["<b>fun</b>"])
        for p in result["proposals"]:
            self.assertNotIn("<b>fun</b>", [c["category"] for c in p["composition"]])
            self.assertNotIn("<b>fun</b>", {r["category"] for r in p["schedule"]})
        self.assertEqual(main_result()["left_out"], [])

    def test_the_period_starts_and_ends_at_midnight_eastern(self):
        period = main_result()["period"]
        self.assertEqual((period["days"], period["holidays"]), (30, ["2026-09-07"]))
        self.assertEqual(period["today_races"], 30 * sp.TODAY_PER_DAY)
        self.assertTrue(period["start"].startswith("2026-09-01T00:00"))
        self.assertTrue(period["end"].startswith("2026-10-01T00:00"))
        self.assertEqual(
            (period["weeks"], period["last_day"], period["next_season"]),
            (5, "2026-09-30", "2026-10-06"),
        )


class OnlyPrimeTimeTests(unittest.TestCase):
    def test_a_category_that_races_only_in_prime_time_never_races_elsewhere(self):
        inputs = fx.rules_inputs()
        chosen = [replace(c, only_prime=True) if c.name == "mystery" else c for c in inputs["categories"]]
        inputs.update(categories=chosen, pairs=sp.category_pairs(chosen, PAIRS))
        plan = sp.plan_proposal(fx.race_set("light"), rules=sp.SHIPPED, **inputs)
        mystery = [r for r in plan["schedule"] if r["category"] == "mystery"]
        self.assertTrue(mystery)
        self.assertEqual({r["band"] for r in mystery}, {"prime"})
        entry = next(c for c in plan["composition"] if c["category"] == "mystery")
        self.assertEqual(entry["open_bands"], ["prime"])
        self.assertEqual(entry["weights"]["normal"], 0.0)


class SeasonTests(unittest.TestCase):
    def test_a_missing_season_or_key_takes_the_defaults(self):
        for raw in (None, {}):
            with self.subTest(raw):
                self.assertEqual(sp.parse_season(raw), sp.Season(start=None, weeks=9, last_day=3))
        self.assertEqual(
            sp.parse_season({"start": "2026-11-09", "weeks": 4, "lastDay": "sunday"}),
            sp.Season(start=date(2026, 11, 9), weeks=4, last_day=6),
        )

    def test_a_bad_season_is_an_error(self):
        for raw in ([], {"length": 9}, {"start": "2026-13-01"}, {"start": 20261109},
                    {"weeks": 0}, {"weeks": 2.5}, {"weeks": True}, {"weeks": "9"},
                    {"lastDay": "Thursday"}, {"lastDay": 3}):
            with self.subTest(raw), self.assertRaises(sp.ScheduleError):
                sp.parse_season(raw)

    def test_the_options_override_the_plan_files_season(self):
        schedule = [slot(1, 1, datetime(2026, 11, 5, 21, tzinfo=EASTERN), 11)]
        config = {"season": {"start": "2026-11-09", "weeks": 9}}
        self.assertEqual(sp.plan_season(config, schedule), (date(2026, 11, 9), 9, 3))
        self.assertEqual(sp.plan_season(config, schedule, date(2026, 11, 16), 4),
                         (date(2026, 11, 16), 4, 3))
        # Without a start anywhere, the season follows the mirrored schedule.
        self.assertEqual(sp.plan_season({}, schedule), (date(2026, 11, 6), 9, 3))
        with self.assertRaises(sp.ScheduleError):
            sp.plan_season(config, schedule, weeks=0)

    def test_the_committed_plan_starts_a_nine_week_season_on_monday_9_november(self):
        if not sp.CONFIG_PATH.exists():
            self.skipTest("schedule-plan.json is missing")
        config = sp.load_config(sp.CONFIG_PATH)
        first_day, weeks, last_day = sp.plan_season(config, [])
        self.assertEqual((first_day, weeks), (date(2026, 11, 9), 9))
        self.assertEqual(sp.last_race_day(first_day, weeks, last_day), date(2027, 1, 7))


class AnalyseTests(unittest.TestCase):
    def test_weeks_must_be_at_least_one(self):
        with self.assertRaises(sp.ScheduleError):
            schedule, modes, races = fx.world()
            sp.analyse(schedule, modes, races, {"composition": fx.BASE_COMPOSITION},
                       start=fx.START, weeks=0, pairs=PAIRS)

    def test_every_set_names_the_plan_categories_in_its_composition_and_pairs(self):
        for p in main_result()["proposals"]:
            self.assertEqual(len(p["pairs"]), 6)
            for row in p["pairs"]:
                self.assertEqual(set(row), {"a", "b", "score", "weak"})

    def test_the_plan_file_must_hold_bands(self):
        schedule, modes, races = fx.world()
        with self.assertRaisesRegex(sp.ScheduleError, "bands"):
            sp.analyse(schedule, modes, races, {"composition": fx.BASE_COMPOSITION, "events": []},
                       start=fx.START, pairs=PAIRS)
        bad = {"bands": {"work": {"prime": "12-15"}}, "composition": fx.BASE_COMPOSITION, "events": []}
        with self.assertRaises(sp.ScheduleError):
            sp.analyse(schedule, modes, races, bad, start=fx.START, pairs=PAIRS)

    def test_the_result_hands_the_plan_files_bands_back(self):
        result = main_result()
        self.assertEqual(result["parameters"]["bands"], fx.WORLD_BANDS)
        for p in result["proposals"]:
            self.assertEqual(p["bands"][0]["group"], "work")

    def test_every_between_race_of_the_fixture_world_has_a_main_race_on_each_side(self):
        for p in between_result()["proposals"]:
            with self.subTest(p["set"]):
                mains = [race_time(r) for r in p["schedule"] if not r["between"]]
                between = [r for r in p["schedule"] if r["between"]]
                self.assertTrue(between)
                self.assertEqual(p["between"], len(between))
                self.assertEqual(p["between_categories"], ["mystery"])
                self.assertEqual({r["category"] for r in between}, {"mystery"})
                self.assertEqual(p["races"], len(p["schedule"]))
                unbracketed = 0
                for r in between:
                    at = race_time(r)
                    before = max((t for t in mains if t < at), default=None)
                    after = min((t for t in mains if t > at), default=None)
                    bracketed = (before is not None and after is not None
                                 and timedelta(hours=1) <= at - before <= sp.RACE_LENGTH
                                 and timedelta(hours=1) <= after - at <= sp.RACE_LENGTH)
                    if not bracketed:
                        self.assertTrue(r["featured"], r["utc"])
                        unbracketed += 1
                self.assertEqual(p["unbracketed"], unbracketed)

    def test_between_races_come_on_top_of_the_main_races_in_the_fixture_world(self):
        for p in between_result()["proposals"]:
            with self.subTest(p["set"]):
                per_date = Counter(r["date"] for r in p["schedule"] if not r["between"])
                short = {s["date"] for s in p["shortfall"]}
                for day in sp.period_dates(*fx.PERIOD):
                    wanted = p["per_day"][sp.day_group(day)]
                    if day.isoformat() in short:
                        self.assertLess(per_date[day.isoformat()], wanted)
                    else:
                        self.assertEqual(per_date[day.isoformat()], wanted)
                self.assertEqual(p["races"], sum(per_date.values()) + p["between"])
                self.assertTrue(p["description"].endswith(" Between races come on top."))
                self.assertEqual(p["day_races"]["most"], max(per_date.values()))

    def test_a_featured_between_race_runs_every_week_or_is_skipped(self):
        for p in between_result()["proposals"]:
            entry = next(e for e in p["featured"] if e["category"] == "mystery")
            self.assertTrue(entry["between"])
            races = [r for r in p["schedule"] if r["featured"] and r["category"] == "mystery"]
            self.assertEqual(len(races), entry["races"])
            self.assertTrue(all(r["between"] for r in races))
            self.assertGreater(entry["races"], 0)

    def test_the_between_caps_hold(self):
        composition = {**fx.BETWEEN_COMPOSITION, "mystery": {"modes": ["Myst A"], "between": True,
                                                              "dailyMax": 1, "weeklyMax": 3}}
        result = fx.cached("between-limits", composition, events=fx.EVENTS)
        for p in result["proposals"]:
            with self.subTest(p["set"]):
                daily, weekly = Counter(), Counter()
                for r in p["schedule"]:
                    if r["category"] == "mystery":
                        daily[r["date"]] += 1
                        weekly[week_start(race_day(r))] += 1
                self.assertTrue(daily)
                self.assertLessEqual(max(daily.values()), 1)
                self.assertLessEqual(max(weekly.values()), 3)


class RulesTests(unittest.TestCase):
    def test_the_shipped_rules_are_the_defaults(self):
        self.assertEqual(sp.Rules(), sp.SHIPPED)
        self.assertEqual(
            (sp.SHIPPED.featured, sp.SHIPPED.shares, sp.SHIPPED.band_rule, sp.SHIPPED.between,
             sp.SHIPPED.wide_gaps, sp.SHIPPED.favourite, sp.SHIPPED.rotation),
            (True, "entrants", True, True, True, True, True),
        )
        self.assertEqual(sp.SHARES, ("entrants", "fill", "equal"))

    def test_rules_are_frozen(self):
        with self.assertRaises(Exception):
            sp.SHIPPED.featured = False

    def test_an_unknown_share_rule_is_an_error(self):
        for bad in ("quota", "", None, "Entrants"):
            with self.subTest(shares=bad), self.assertRaises(sp.ScheduleError):
                sp.Rules(shares=bad)
        for good in sp.SHARES:
            self.assertEqual(sp.Rules(shares=good).shares, good)

    def test_analyse_hands_the_shipped_rules_to_every_set(self):
        with mock.patch.object(sp, "plan_proposal", wraps=sp.plan_proposal) as spy:
            fx.run(fx.BASE_COMPOSITION)
        self.assertEqual(len(spy.call_args_list), len(sp.SETS))
        for call in spy.call_args_list:
            self.assertIs(call.kwargs["rules"], sp.SHIPPED)

    def plan(self, **rules):
        return fx.planned_with(sp.Rules(**rules))

    @staticmethod
    def signature(p):
        return [(r["utc"], r["category"], r["mode_id"]) for r in p["schedule"]]

    def test_without_featured_races_no_race_is_featured(self):
        plan = self.plan(featured=False)
        self.assertEqual(plan["featured"], [])
        self.assertFalse(any(r["featured"] for r in plan["schedule"]))
        self.assertNotEqual(self.signature(plan), self.signature(fx.planned_with(sp.SHIPPED)))
        for day, count in Counter(r["date"] for r in plan["schedule"]).items():
            self.assertLessEqual(count, plan["per_day"]["prime"], day)

    def test_the_share_rule_changes_who_races(self):
        shipped = fx.planned_with(sp.SHIPPED)
        splits = {}
        for shares in sp.SHARES:
            plan = self.plan(shares=shares)
            splits[shares] = tuple(c["races"] for c in plan["categories"])
        self.assertEqual(self.plan(shares="entrants")["schedule"], shipped["schedule"])
        self.assertGreater(len(set(splits.values())), 1)
        equal = self.plan(shares="equal")
        counts = [c["races"] for c in equal["categories"]]
        self.assertLessEqual(max(counts) - min(counts), 0.2 * sum(counts))

    def test_equal_shares_weigh_every_open_band_one(self):
        equal = self.plan(shares="equal")
        for c in equal["composition"]:
            self.assertEqual(set(c["weights"].values()), {1.0})

    def test_without_the_band_rule_every_band_is_open(self):
        plan = self.plan(band_rule=False)
        for c in plan["composition"]:
            self.assertEqual(c["open_bands"], list(sp.BANDS))

    def test_the_world_never_closes_a_band_so_the_band_rule_changes_nothing_there(self):
        self.assertEqual(self.signature(self.plan(band_rule=False)),
                         self.signature(fx.planned_with(sp.SHIPPED)))

    def test_without_the_between_lever_the_between_categories_are_main_categories(self):
        plan = fx.planned_with(sp.Rules(between=False), composition=fx.BETWEEN_COMPOSITION)
        self.assertEqual(plan["between"], 0)
        self.assertEqual(plan["between_categories"], [])
        self.assertFalse(any(r["between"] for r in plan["schedule"]))
        self.assertEqual(plan["races"], len(plan["schedule"]))
        self.assertNotIn("Between races", plan["description"])
        # Mystery now counts toward the date's races like the others.
        per_date = Counter(r["date"] for r in plan["schedule"])
        short = {s["date"] for s in plan["shortfall"]}
        for day in sp.period_dates(*fx.PERIOD):
            if day.isoformat() not in short:
                self.assertEqual(per_date[day.isoformat()], plan["per_day"][sp.day_group(day)])
        self.assertGreater(sum(r["category"] == "mystery" for r in plan["schedule"]), 0)
        shipped = fx.planned_with(sp.SHIPPED, composition=fx.BETWEEN_COMPOSITION)
        self.assertGreater(shipped["between"], 0)
        self.assertGreater(shipped["races"], plan["races"] - 1)

    def test_without_the_wide_gap_lever_main_races_may_start_closer(self):
        shipped = fx.planned_with(sp.SHIPPED)
        loose = fx.planned_with(sp.Rules(wide_gaps=False))
        self.assertGreaterEqual(loose["short_gaps"], shipped["short_gaps"])
        self.assertNotEqual(self.signature(loose), self.signature(shipped))

    def test_without_the_favourite_rule_prime_races_rotate_the_modes(self):
        shipped = fx.planned_with(sp.SHIPPED)
        free = self.plan(favourite=False)
        def prime_entrance(plan):
            return {r["mode"] for r in plan["schedule"]
                    if r["category"] == "entrance" and r["band"] == "prime" and not r["featured"]}
        self.assertEqual(prime_entrance(shipped), {"Ent B"})
        self.assertEqual(prime_entrance(free), {"Ent A", "Ent B"})
        # Featured races still run the favourite.
        featured = {r["mode"] for r in free["schedule"] if r["featured"] and r["category"] == "entrance"}
        self.assertEqual(featured, {"Ent B"})

    def test_without_rotation_a_category_may_repeat_last_weeks_hour(self):
        shipped = fx.planned_with(sp.SHIPPED)
        free = self.plan(rotation=False)
        self.assertGreaterEqual(free["repeats"], shipped["repeats"])
        self.assertNotEqual(self.signature(free), self.signature(shipped))


class LoadConfigTests(unittest.TestCase):
    BANDS = {group: dict(entry) for group, entry in fx.WORLD_BANDS.items()}

    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(text, encoding="utf-8")
            return sp.load_config(path)

    def plan(self, **changes):
        return json.dumps({"bands": self.BANDS, "composition": {"a": {"modes": ["x"]}}, "events": [], **changes})

    def test_a_valid_plan_loads(self):
        config = self.load(self.plan())
        self.assertEqual(config["composition"], {"a": {"modes": ["x"]}})
        self.assertEqual(config["bands"], self.BANDS)
        text = json.dumps({"bands": self.BANDS, "composition": {"a": {"modes": []}}})
        self.assertEqual(self.load(text)["composition"]["a"]["modes"], [])
        season = {"start": "2026-11-09", "weeks": 9, "lastDay": "thursday"}
        self.assertEqual(self.load(self.plan(season=season))["season"], season)

    def test_a_plan_needs_bands(self):
        for text in (json.dumps({"composition": {"a": {"modes": ["x"]}}, "events": []}),
                     json.dumps({"composition": {"a": {"modes": ["x"]}}})):
            with self.subTest(text), self.assertRaises(sp.ScheduleError):
                self.load(text)

    def test_a_plan_with_other_keys_or_without_a_composition_is_an_error(self):
        bands = json.dumps(self.BANDS)
        for text in ('{"bands": %s, "composition": {"a": {"modes": []}}, "recipes": []}' % bands,
                     '{"bands": %s, "events": []}' % bands,
                     '{"bands": %s, "composition": {}}' % bands,
                     '{"bands": %s, "composition": []}' % bands, '[]', 'not json',
                     '{"bands": %s, "composition": {"a": {"modes": []}}, "events": {}}' % bands):
            with self.subTest(text), self.assertRaises(sp.ScheduleError):
                self.load(text)

    def test_a_missing_file_is_an_error(self):
        with self.assertRaises(sp.ScheduleError):
            sp.load_config(Path(tempfile.gettempdir()) / "no-such-plan-file.json")

    def test_the_committed_plan_loads_and_uses_only_the_allowed_keys(self):
        if not sp.CONFIG_PATH.exists():
            self.skipTest("schedule-plan.json is missing")
        config = sp.load_config(sp.CONFIG_PATH)
        for name, entry in config["composition"].items():
            self.assertTrue(set(entry) <= sp.CATEGORY_KEYS, name)
            self.assertIn("modes", entry)
            self.assertIsInstance(entry["modes"], list)
            self.assertIsInstance(entry.get("between", False), bool)

    def test_the_committed_bands_parse_and_leave_a_main_category(self):
        if not sp.CONFIG_PATH.exists():
            self.skipTest("schedule-plan.json is missing")
        config = sp.load_config(sp.CONFIG_PATH)
        layout = sp.parse_bands(config["bands"])
        self.assertEqual(len(layout), 72)
        listed = [entry for entry in config["composition"].values() if entry["modes"]]
        self.assertTrue(any(not entry.get("between", False) for entry in listed))


class CommittedDataTests(unittest.TestCase):
    def test_fired_slots_map_archetypes_to_the_races_mode(self):
        directory = race_archive.ARCHIVE_DIR / "ladder-schedule"
        if not directory.exists():
            self.skipTest("archive/ladder-schedule/ is missing")
        schedule = schedule_archive.load_schedule()
        modes = schedule_archive.load_modes()
        races = [envelope["race"] for envelope in race_archive.load_races()]
        if not (schedule and modes and races):
            self.skipTest("the archive is empty")
        slots, _begin, _end = sp.history_slots(schedule, modes, races)
        archetypes = {mode["id"]: mode["archetype"] for mode in modes}
        fired = [s for s in slots if s.fired]
        self.assertTrue(fired)
        wrong = [
            (s.started_at.isoformat(), s.mode_id, s.category)
            for s in fired
            if schedule_archive.ARCHETYPE_CATEGORIES.get(archetypes[s.mode_id]) != s.category
        ]
        self.assertEqual(wrong[:5], [])



if __name__ == "__main__":
    unittest.main()

