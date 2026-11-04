import copy
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import mock

import schedule_fixtures as fx
from scripts import schedule_lab as lab
from scripts import schedule_planner as sp

EASTERN = fx.EASTERN
SET_NAMES = [race_set.name for race_set in sp.SETS]


def with_rules(**changes):
    return replace(sp.SHIPPED, **changes)


def history_slot(local, category="simple", entrants=4, fired=True, mode_id=1):
    return sp.HistorySlot(local.astimezone(UTC), 1, category, mode_id, entrants, fired)


def utc_text(local):
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def race_row(local, category, source=None, mode_id=1, band="normal"):
    return {
        "utc": utc_text(local),
        "date": local.date().isoformat(),
        "category": category,
        "source": source or category,
        "mode_id": mode_id,
        "band": band,
    }


MONDAY = datetime(2026, 9, 7, tzinfo=EASTERN)


class VariantTests(unittest.TestCase):
    def test_shipped_changes_nothing(self):
        self.assertEqual(lab.changed_levers(lab.SHIPPED), {})

    def test_every_variant_changes_a_lever_of_its_own(self):
        keys = [variant.key for variant in lab.VARIANTS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn(lab.SHIPPED.key, keys)
        seen = {}
        for variant in lab.VARIANTS:
            levers = lab.changed_levers(variant)
            with self.subTest(variant.key):
                self.assertTrue(levers)
                for name, value in levers.items():
                    # Two variants may set one field, but to different values.
                    self.assertNotIn((name, value), seen, f"{variant.key} and {seen.get((name, value))}")
                    seen[name, value] = variant.key

    def test_variants_change_exactly_their_levers(self):
        levers = {variant.key: lab.changed_levers(variant) for variant in lab.VARIANTS}
        self.assertEqual(
            levers,
            {
                "open-bands": {"rules.band_rule": False},
                "no-featured": {"rules.featured": False},
                "fill-shares": {"rules.shares": "fill"},
                "equal-shares": {"rules.shares": "equal"},
                "no-between": {"rules.between": False},
                "two-hour-gaps": {"rules.wide_gaps": False},
                "no-favourite": {"rules.favourite": False},
                "no-rotation": {"rules.rotation": False},
                "no-plan-caps": {"plan_caps": False},
                "no-holidays": {"holidays": False},
            },
        )

    def test_every_rules_field_is_broken_by_a_variant(self):
        named = {name for variant in lab.VARIANTS for name in lab.changed_levers(variant)}
        for rule in lab.fields(sp.Rules):
            self.assertIn(f"rules.{rule.name}", named)

    def test_the_new_rules_are_broken_by_their_own_variants(self):
        self.assertEqual(by_key("no-between").rules, with_rules(between=False))
        self.assertEqual(by_key("two-hour-gaps").rules, with_rules(wide_gaps=False))
        for key in ("no-between", "two-hour-gaps"):
            self.assertTrue(by_key(key).plan_caps)
            self.assertTrue(by_key(key).holidays)

    def test_no_variant_mentions_the_removed_pair_rules(self):
        self.assertNotIn("pairs", {rule.name for rule in lab.fields(sp.Rules)})
        for variant in lab.VARIANTS:
            text = f"{variant.title} {variant.breaks} {variant.model_blind}".lower()
            for gone in ("paired", "pair "):
                self.assertNotIn(gone, text, variant.key)

    def test_the_model_blind_variants_say_why(self):
        blind = {variant.key: variant.model_blind for variant in lab.VARIANTS if variant.model_blind}
        self.assertEqual(set(blind), {"open-bands", "no-rotation", "no-between", "two-hour-gaps"})
        self.assertIn("3 hours apart", blind["two-hour-gaps"])
        self.assertIn("overlap", blind["no-between"])


class CombineTests(unittest.TestCase):
    def test_a_combination_carries_every_parts_changes(self):
        first = lab.Variant("a", "A", "x", holidays=False, model_blind="a is blind")
        second = lab.Variant("b", "B", "y", rules=with_rules(wide_gaps=False))
        third = lab.Variant("c", "C", "z", rules=with_rules(shares="fill"), plan_caps=False)
        combined = lab.combine([first, second, third])
        self.assertEqual(
            lab.changed_levers(combined),
            {**lab.changed_levers(first), **lab.changed_levers(second), **lab.changed_levers(third)},
        )
        self.assertEqual(combined.key, "combined")
        self.assertEqual(combined.model_blind, "a is blind")

    def test_the_combination_is_model_blind_if_any_part_is_and_says_why(self):
        blind = lab.Variant("a", "A", "x", holidays=False, model_blind="first reason")
        also = lab.Variant("b", "B", "y", rules=with_rules(rotation=False), model_blind="second reason")
        clear = lab.Variant("c", "C", "z", plan_caps=False)
        self.assertEqual(lab.combine([blind, also]).model_blind, "first reason; second reason")
        self.assertEqual(lab.combine([clear, also]).model_blind, "second reason")
        self.assertIsNone(lab.combine([clear, lab.Variant("d", "D", "w", holidays=False)]).model_blind)

    def test_two_parts_may_not_set_a_lever_to_different_values(self):
        fill = lab.Variant("a", "A", "x", rules=with_rules(shares="fill"))
        equal = lab.Variant("b", "B", "y", rules=with_rules(shares="equal"))
        with self.assertRaises(lab.LabError):
            lab.combine([fill, equal])
        # The same value twice is no conflict.
        again = lab.Variant("c", "C", "z", rules=with_rules(shares="fill"))
        self.assertEqual(lab.changed_levers(lab.combine([fill, again])), {"rules.shares": "fill"})

    def test_every_single_variant_combines_unless_two_change_one_lever(self):
        # fill-shares and equal-shares both set the share rule, so they cannot merge.
        rest = [v for v in lab.VARIANTS if v.key != "equal-shares"]
        combined = lab.combine(rest)
        merged = {}
        for variant in rest:
            merged.update(lab.changed_levers(variant))
        self.assertEqual(lab.changed_levers(combined), merged)
        with self.assertRaises(lab.LabError):
            lab.combine(list(lab.VARIANTS))


class VariantConfigTests(unittest.TestCase):
    CONFIG = {
        "bands": fx.WORLD_BANDS,
        "composition": {
            "a": {"modes": ["One"], "dailyMax": 2, "weeklyMax": 5, "onlyPrimeTime": True},
            "b": {"modes": ["Two"], "dailyMax": 3},
            "c": {"modes": []},
            "d": {"modes": ["Four"], "between": True, "dailyMax": 1},
        },
        "events": [{"name": "e"}],
    }

    def test_plan_caps_are_stripped_only_when_lifted(self):
        kept = lab.variant_config(self.CONFIG, lab.SHIPPED)
        self.assertEqual(kept, self.CONFIG)
        lifted = lab.variant_config(self.CONFIG, lab.Variant("x", "X", "y", plan_caps=False))
        self.assertEqual(
            lifted["composition"],
            {
                "a": {"modes": ["One"]},
                "b": {"modes": ["Two"]},
                "c": {"modes": []},
                "d": {"modes": ["Four"], "between": True},
            },
        )
        self.assertEqual(lifted["events"], self.CONFIG["events"])

    def test_the_input_is_never_changed(self):
        before = copy.deepcopy(self.CONFIG)
        lab.variant_config(self.CONFIG, lab.Variant("x", "X", "y", plan_caps=False))
        lab.variant_config(self.CONFIG, lab.SHIPPED)
        self.assertEqual(self.CONFIG, before)

    def test_other_levers_leave_the_caps_alone(self):
        variant = lab.Variant("x", "X", "y", holidays=False, rules=with_rules(between=False))
        self.assertEqual(lab.variant_config(self.CONFIG, variant), self.CONFIG)

    def test_lifting_the_caps_keeps_between_and_the_bands(self):
        lifted = lab.variant_config(self.CONFIG, lab.Variant("x", "X", "y", plan_caps=False))
        self.assertEqual(lifted["bands"], self.CONFIG["bands"])
        self.assertIs(lifted["composition"]["d"]["between"], True)
        self.assertEqual(lifted["composition"]["d"], {"modes": ["Four"], "between": True})


class HalvesTests(unittest.TestCase):
    def half_of(self, local):
        parts = lab.halves([history_slot(local)])
        (name,) = [name for name, part in parts.items() if part]
        return name

    def test_the_halves_partition_the_slots(self):
        slots = [history_slot(MONDAY + timedelta(hours=7 * k)) for k in range(100)]
        parts = lab.halves(slots)
        self.assertEqual(set(parts), set(lab.HALVES))
        self.assertEqual(len(parts["A"]) + len(parts["B"]), len(slots))
        self.assertFalse(set(parts["A"]) & set(parts["B"]))
        self.assertEqual(set(parts["A"]) | set(parts["B"]), set(slots))
        self.assertTrue(parts["A"] and parts["B"])

    def test_weeks_alternate_from_the_week_zero_monday(self):
        zero = datetime.combine(lab.WEEK_ZERO, datetime.min.time(), tzinfo=EASTERN)
        self.assertEqual(lab.WEEK_ZERO.weekday(), 0)
        self.assertEqual(self.half_of(zero), "A")
        self.assertEqual(self.half_of(zero + timedelta(weeks=1)), "B")
        self.assertEqual(self.half_of(zero + timedelta(weeks=2)), "A")
        self.assertNotEqual(self.half_of(MONDAY + timedelta(weeks=1)), self.half_of(MONDAY))

    def test_a_week_runs_monday_to_sunday_in_eastern_time(self):
        week = [MONDAY + timedelta(days=d, hours=h) for d in range(7) for h in (0, 12, 23)]
        self.assertEqual({self.half_of(local) for local in week}, {self.half_of(MONDAY)})
        sunday_night = MONDAY - timedelta(hours=1)
        self.assertEqual(sunday_night.weekday(), 6)
        self.assertNotEqual(self.half_of(sunday_night), self.half_of(MONDAY))
        # Sunday 23:00 ET is already Monday in UTC, and still the old week.
        self.assertEqual(sunday_night.astimezone(UTC).weekday(), 0)
        next_monday = MONDAY + timedelta(weeks=1)
        self.assertEqual(self.half_of(next_monday - timedelta(hours=1)), self.half_of(MONDAY))
        self.assertNotEqual(self.half_of(next_monday), self.half_of(MONDAY))


class SaturationTests(unittest.TestCase):
    @staticmethod
    def shrunk(count, ratio):
        return (count * ratio + sp.SHRINK_SLOTS * 1.0) / (count + sp.SHRINK_SLOTS)

    def test_ratios_are_shrunk_per_count_of_a_categorys_slots_on_a_date(self):
        model = fx.flat_model(["simple", "entrance"])
        d1, d2, d3, d4, d5 = (MONDAY + timedelta(days=k) for k in range(5))
        at = lambda day, hour: day.replace(hour=hour)
        slots = [
            # One slot a date: simple on d1 and d2, entrance on d1 (its own count).
            history_slot(at(d1, 9), "simple", 4),
            history_slot(at(d2, 9), "simple", 2),
            history_slot(at(d1, 10), "entrance", 0),
            # Two slots on d2, late enough to cross midnight in UTC; one never fired.
            history_slot(at(d2, 22), "entrance", 1),
            history_slot(at(d2, 23), "entrance", 0, fired=False),
            # Three slots.
            *(history_slot(at(d3, 9 + k), "simple", 3) for k in range(3)),
            # Four, and five, both count as 4 and more.
            *(history_slot(at(d4, 9 + k), "simple", 2) for k in range(4)),
            *(history_slot(at(d5, 9 + k), "simple", 2) for k in range(5)),
        ]
        table = lab.saturation(slots, model)
        self.assertEqual(sorted(table), [1, 2, 3, 4])
        # Flat model: every slot expects 1 entrant and fills half the time.
        # Bucket 1: simple 4, simple 2 and entrance 0 on d1.
        self.assertAlmostEqual(table[1][0], self.shrunk(3, 6 / 3))
        self.assertAlmostEqual(table[1][1], self.shrunk(3, 3 / 1.5))
        # Bucket 2: entrance 1 and 0 on d2, one fired.
        self.assertAlmostEqual(table[2][0], self.shrunk(2, 1 / 2))
        self.assertAlmostEqual(table[2][1], self.shrunk(2, 1 / 1.0))
        self.assertAlmostEqual(table[3][0], self.shrunk(3, 3.0))
        self.assertAlmostEqual(table[3][1], self.shrunk(3, 3 / 1.5))
        self.assertAlmostEqual(table[4][0], self.shrunk(9, 2.0))
        self.assertAlmostEqual(table[4][1], self.shrunk(9, 9 / 4.5))

    def test_an_empty_bucket_is_one(self):
        model = fx.flat_model(["simple"])
        table = lab.saturation([history_slot(MONDAY.replace(hour=9))], model)
        for bucket in (2, 3, 4):
            self.assertEqual(table[bucket], (1.0, 1.0))
        self.assertNotEqual(table[1], (1.0, 1.0))


def category_row(*names):
    return [{"category": name} for name in names]


class ScoreTests(unittest.TestCase):
    pairs = {
        frozenset(("a", "b")): sp.PairScore(0.2, False),
        frozenset(("a", "c")): sp.PairScore(0.2, False),
        frozenset(("b", "c")): sp.PairScore(0.2, False),
    }
    model = fx.flat_model(["a", "b", "c"], {"a": 2.0, "b": 3.0, "c": 1.0})

    def at(self, day, hour):
        return (MONDAY + timedelta(days=day)).replace(hour=hour)

    def test_an_identity_table_gives_the_models_own_numbers(self):
        proposal = {
            "schedule": [
                race_row(self.at(0, 14), "a"),
                race_row(self.at(0, 15), "b"),
                race_row(self.at(1, 14), "a"),
            ],
            "composition": category_row("a", "b", "c"),
        }
        scored = lab.score(proposal, self.model, lab.IDENTITY, self.pairs)
        self.assertEqual(scored["races"], 3)
        # Entrants 2 + 3 + 2, less 0.2 * min(2, 3) for the two races an hour apart.
        self.assertAlmostEqual(scored["clash"], 0.4)
        self.assertAlmostEqual(scored["total"], 7 - 0.4)
        self.assertAlmostEqual(scored["per_race"], (7 - 0.4) / 3)
        # c has no races, so it weighs 0 and is the weakest.
        self.assertEqual(scored["weakest"], 0.0)
        self.assertEqual(scored["weakest_category"], "c")
        self.assertEqual(scored["categories"]["c"], {"races": 0, "entrants": 0.0})
        self.assertEqual(scored["categories"]["a"], {"races": 2, "entrants": 4.0})
        # Half the rooms fill: 3 races cancel 1.5.
        self.assertAlmostEqual(scored["cancelled"], 1.5)
        self.assertEqual(scored["crowded"], 0)

    def test_the_corrected_fill_is_capped_at_one(self):
        proposal = {"schedule": [race_row(self.at(0, 14), "a")], "composition": category_row("a")}
        # The model's fill is 0.5; three times that would be 1.5, so the room surely fires.
        table = {bucket: (1.0, 3.0) for bucket in range(1, lab.SATURATION_BUCKETS + 1)}
        scored = lab.score(proposal, self.model, table, self.pairs)
        self.assertAlmostEqual(scored["cancelled"], 0.0)

    def test_two_races_two_hours_apart_do_not_clash(self):
        proposal = {
            "schedule": [race_row(self.at(0, 14), "a"), race_row(self.at(0, 16), "b")],
            "composition": category_row("a", "b"),
        }
        scored = lab.score(proposal, self.model, lab.IDENTITY, self.pairs)
        self.assertEqual(scored["clash"], 0.0)
        self.assertAlmostEqual(scored["total"], 5.0)
        self.assertEqual(scored["weakest_category"], "a")
        self.assertAlmostEqual(scored["weakest"], 2.0)

    def test_the_table_ratios_apply_by_source_category_count_on_the_date(self):
        table = {1: (2.0, 3.0), 2: (0.5, 0.5), 3: (1.0, 1.0), 4: (1.0, 1.0)}
        proposal = {
            "schedule": [
                race_row(self.at(0, 14), "a"),
                race_row(self.at(0, 15), "b"),
                race_row(self.at(0, 20), "a"),
            ],
            "composition": category_row("a", "b"),
        }
        scored = lab.score(proposal, self.model, table, self.pairs)
        # a races twice on the date (0.5), b once (2.0): entrants 1, 6, 1.
        self.assertAlmostEqual(scored["categories"]["a"]["entrants"], 2.0)
        self.assertAlmostEqual(scored["categories"]["b"]["entrants"], 6.0)
        self.assertAlmostEqual(scored["clash"], 0.2 * min(1.0, 6.0))
        self.assertAlmostEqual(scored["total"], 8.0 - 0.2)
        self.assertAlmostEqual(scored["weakest"], 1.0)
        self.assertEqual(scored["weakest_category"], "a")
        # a: fill 0.5 * 0.5 = 0.25 each, so 0.75 cancelled twice; b: 0.5 * 3 is capped at 1.
        self.assertAlmostEqual(scored["cancelled"], 1.5)

    def test_a_race_counts_under_its_source_not_its_plan_category(self):
        # Plan category "m" mixes sources a and b; each source is counted on its own.
        table = {1: (1.0, 1.0), 2: (0.5, 1.0), 3: (1.0, 1.0), 4: (1.0, 1.0)}
        proposal = {
            "schedule": [
                race_row(self.at(0, 10), "m", source="a"),
                race_row(self.at(0, 14), "m", source="b"),
                race_row(self.at(0, 18), "m", source="a"),
            ],
            "composition": category_row("m"),
        }
        scored = lab.score(proposal, self.model, table, {})
        self.assertAlmostEqual(scored["total"], 2 * 0.5 + 3.0 + 2 * 0.5)

    def test_many_races_on_one_date_count_as_crowded(self):
        rows = [race_row(self.at(0, 8 + 2 * k), "a") for k in range(lab.SATURATION_BUCKETS)]
        scored = lab.score(
            {"schedule": rows, "composition": category_row("a")}, self.model, lab.IDENTITY, {}
        )
        self.assertEqual(scored["crowded"], 1)

    def test_no_races_scores_zero(self):
        scored = lab.score(
            {"schedule": [], "composition": category_row("a")}, self.model, lab.IDENTITY, {}
        )
        self.assertEqual((scored["races"], scored["per_race"], scored["total"]), (0, 0.0, 0.0))
        self.assertEqual(scored["weakest"], 0.0)

    def test_an_identity_table_reproduces_the_planners_own_score(self):
        inputs = fx.rules_inputs()
        for set_name in ("light", "bonanza"):
            with self.subTest(set_name):
                proposal = sp.plan_proposal(fx.race_set(set_name), rules=sp.SHIPPED, **inputs)
                scored = lab.score(proposal, inputs["model"], lab.IDENTITY, inputs["pairs"])
                self.assertEqual(scored["races"], proposal["races"])
                self.assertAlmostEqual(scored["total"], proposal["expected_entrants"])
                self.assertAlmostEqual(scored["cancelled"], proposal["expected_cancelled"])
                self.assertAlmostEqual(
                    scored["per_race"], proposal["expected_entrants"] / proposal["races"]
                )


class ClashTests(unittest.TestCase):
    model = fx.flat_model(["a", "b", "c"], {"a": 2.0, "b": 3.0, "c": 1.0})

    def at(self, hour):
        return MONDAY.replace(hour=hour)

    def test_the_charge_to_a_race_is_capped_at_its_own_entrants(self):
        # c (1 entrant) is an hour from a and from b. Without pair scores each pair costs
        # 1 * 1, so c would be charged 2, but it only has 1 to lose.
        proposal = {
            "schedule": [race_row(self.at(14), "a"), race_row(self.at(15), "c"),
                         race_row(self.at(16), "b")],
            "composition": category_row("a", "b", "c"),
        }
        scored = lab.score(proposal, self.model, lab.IDENTITY, {})
        self.assertAlmostEqual(scored["clash"], 1.0)
        self.assertAlmostEqual(scored["total"], 2.0 + 1.0 + 3.0 - 1.0)

    def test_the_clash_is_the_planners(self):
        pairs = {frozenset(("a", "b")): sp.PairScore(0.5, False),
                 frozenset(("a", "c")): sp.PairScore(None, True)}
        rows = [race_row(self.at(14), "a"), race_row(self.at(15), "b"),
                race_row(self.at(16), "c"), race_row(self.at(17), "a")]
        proposal = {"schedule": rows, "composition": category_row("a", "b", "c")}
        scored = lab.score(proposal, self.model, lab.IDENTITY, pairs)
        starts = [datetime.fromisoformat(row["utc"].replace("Z", "+00:00")) for row in rows]
        expected = [2.0, 3.0, 1.0, 2.0]
        self.assertAlmostEqual(
            scored["clash"], sp.clash(starts, ["a", "b", "c", "a"], expected, pairs)
        )
        self.assertGreater(scored["clash"], 0.0)

    def test_a_missing_pair_score_counts_as_one(self):
        proposal = {
            "schedule": [race_row(self.at(14), "a"), race_row(self.at(15), "b")],
            "composition": category_row("a", "b"),
        }
        self.assertAlmostEqual(lab.score(proposal, self.model, lab.IDENTITY, {})["clash"], 2.0)

    def test_a_between_race_an_hour_from_a_main_race_is_charged(self):
        # The score does not know which races are between races: it charges any two races
        # less than 2 hours apart.
        pairs = {frozenset(("a", "b")): sp.PairScore(0.25, False)}
        proposal = {
            "schedule": [race_row(self.at(14), "a"), race_row(self.at(16), "b"),
                         race_row(self.at(15), "b")],
            "composition": category_row("a", "b"),
        }
        scored = lab.score(proposal, self.model, lab.IDENTITY, pairs)
        # a at 14 and b at 15: 0.25 * 2, charged to a. The two b races, an hour apart, share
        # a category and so have no score, which counts 1 * 3, charged to the later one.
        self.assertAlmostEqual(scored["clash"], 0.5 + 3.0)


class ClosedBandTests(unittest.TestCase):
    def history(self):
        """Category a fires in normal and prime hours but never in the quiet small hours."""
        slots = []
        for day in range(10):
            for hour, fired in ((2, False), (10, True), (20, True)):
                slots.append(history_slot(MONDAY + timedelta(days=day, hours=hour), "a", fired=fired))
        return slots

    def test_races_in_a_band_the_rule_closes_are_counted(self):
        model = fx.flat_model(["a"])
        categories = [fx.plain_category("a")]
        proposal = {
            "schedule": [
                race_row(MONDAY, "a", band="quiet"),
                race_row(MONDAY, "a", band="quiet"),
                race_row(MONDAY, "a", band="normal"),
                race_row(MONDAY, "a", band="prime"),
            ]
        }
        self.assertEqual(lab.closed_band_races(proposal, categories, self.history(), model, fx.WORLD_LAYOUT), 2)

    def test_a_category_with_no_open_band_has_every_race_counted(self):
        model = fx.flat_model(["a"])
        categories = [fx.plain_category("a")]
        never = [history_slot(MONDAY + timedelta(days=d, hours=h), "a", fired=False)
                 for d in range(10) for h in (2, 10, 20)]
        proposal = {"schedule": [race_row(MONDAY, "a", band="prime")]}
        self.assertEqual(lab.closed_band_races(proposal, categories, never, model, fx.WORLD_LAYOUT), 1)


def result_row(variant, set_name, plan, scored, per_race=10.0, **extra):
    row = {
        "variant": variant, "set": set_name, "plan": plan, "scored": scored,
        "races": 100, "per_race": per_race, "weakest": 5.0, "total": per_race * 100,
        "cancelled": 20.0, "categories": {"a": {"races": 60, "entrants": 600.0},
                                          "b": {"races": 40, "entrants": 200.0}},
        "closed": 0, "crowded": 2, "between": 3, "short_gaps": 4, "shortfall": 1, "empty_hours": 0,
        "weekly_races": 49, "notice": None,
    }
    row.update(extra)
    return row


class FoldMeanTests(unittest.TestCase):
    def test_the_two_out_of_sample_folds_are_averaged_and_the_in_sample_one_is_kept_apart(self):
        results = [
            result_row("v", "light", "all", "all", per_race=99.0),
            result_row("v", "light", "B", "A", per_race=14.0, notice="short"),
            result_row("v", "light", "A", "B", per_race=10.0),
        ]
        (key, entry), = lab.fold_means(results).items()
        self.assertEqual(key, ("v", "light"))
        self.assertAlmostEqual(entry["per_race"], 12.0)
        # The folds keep the order of FOLDS whatever order they arrived in.
        self.assertEqual([fold["plan"] for fold in entry["folds"]], ["A", "B"])
        self.assertEqual([fold["per_race"] for fold in entry["folds"]], [10.0, 14.0])
        self.assertEqual(entry["in_sample"], 99.0)
        self.assertIsNone(entry["in_sample_error"])
        self.assertEqual(entry["notices"], ["short"])
        self.assertAlmostEqual(entry["categories"]["a"]["races"], 60)
        for field in lab.DIAGNOSTICS:
            self.assertIn(field, entry)
        self.assertEqual(entry["between"], 3)
        self.assertEqual(entry["short_gaps"], 4)

    def test_each_variant_and_set_is_its_own_entry(self):
        results = [
            result_row(variant, set_name, plan, scored)
            for variant in ("v", "w") for set_name in ("light", "full") for plan, scored in lab.FOLDS
        ]
        self.assertEqual(
            set(lab.fold_means(results)),
            {(v, s) for v in ("v", "w") for s in ("light", "full")},
        )

    def test_a_failed_out_of_sample_fold_makes_the_entry_its_error(self):
        results = [
            result_row("v", "light", "A", "B"),
            {"variant": "v", "set": "light", "plan": "B", "scored": "A", "error": "boom"},
            result_row("v", "light", "all", "all"),
        ]
        self.assertEqual(lab.fold_means(results)["v", "light"], {"error": "boom"})

    def test_a_failed_in_sample_fold_is_noted_but_does_not_decide(self):
        results = [
            result_row("v", "light", "A", "B"),
            result_row("v", "light", "B", "A"),
            {"variant": "v", "set": "light", "plan": "all", "scored": "all", "error": "late"},
        ]
        entry = lab.fold_means(results)["v", "light"]
        self.assertNotIn("error", entry)
        self.assertIsNone(entry["in_sample"])
        self.assertEqual(entry["in_sample_error"], "late")


class JobTests(unittest.TestCase):
    def test_a_job_per_variant_set_and_fold(self):
        jobs = lab.jobs_for([lab.SHIPPED, lab.VARIANTS[0]])
        self.assertEqual(len(jobs), 2 * len(sp.SETS) * len(lab.FOLDS))
        self.assertEqual({(job.plan, job.scored) for job in jobs}, set(lab.FOLDS))
        self.assertEqual({job.set_name for job in jobs}, set(SET_NAMES))
        self.assertEqual(len(SET_NAMES), 4)


_CONTEXTS = {}


def context_for(key, composition):
    if key not in _CONTEXTS:
        schedule, modes, races = fx.world()
        config = {"bands": fx.WORLD_BANDS, "composition": composition, "events": []}
        _CONTEXTS[key] = lab.build_context(
            schedule, modes, races, config, start=fx.START, months=1, pairs=fx.PAIRS
        )
    return _CONTEXTS[key]


# Every kind of cap, and mystery as a between category.
LIMIT_BETWEEN_COMPOSITION = {
    **fx.LIMIT_COMPOSITION,
    "mystery": {"modes": ["Myst A"], "dailyMax": 3, "between": True},
}


def limited_context():
    """A context whose plan file carries every kind of cap and a between category."""
    return context_for("limits", LIMIT_BETWEEN_COMPOSITION)


def planner_call(variant, set_name="light", plan="A", scored="B", context=None):
    """Run one job and return the arguments `plan_proposal` received and what it returned."""
    job = lab.Job(variant, set_name, plan, scored)
    seen = []
    real = sp.plan_proposal

    def spy(*args, **kwargs):
        proposal = real(*args, **kwargs)
        seen.append((mock.call(*args, **kwargs), proposal))
        return proposal

    with mock.patch.object(lab.planner, "plan_proposal", side_effect=spy):
        result = lab.run_job(context or limited_context(), job)
    ((call, proposal),) = seen
    return call, proposal, result


def by_key(key):
    return next(variant for variant in lab.VARIANTS if variant.key == key)


class LeverThreadingTests(unittest.TestCase):
    def test_every_variants_rules_reach_the_planner(self):
        for variant in (lab.SHIPPED, *lab.VARIANTS):
            with self.subTest(variant.key):
                call, _proposal, _result = planner_call(variant)
                self.assertEqual(call.kwargs["rules"], variant.rules)

    def test_the_planner_gets_the_planning_halfs_history_and_no_seed(self):
        context = limited_context()
        for plan, scored in lab.FOLDS:
            with self.subTest(plan=plan):
                call, _proposal, _result = planner_call(lab.SHIPPED, plan=plan, scored=scored)
                self.assertIs(call.kwargs["history"], context.slots[plan])
                self.assertIs(call.kwargs["model"], context.models[plan])
                self.assertEqual(len(call.args), 1)
                for gone in ("fired", "iterations", "seed"):
                    self.assertNotIn(gone, call.kwargs)

    def test_every_set_reaches_the_planner(self):
        for race_set in sp.SETS:
            call, proposal, _result = planner_call(lab.SHIPPED, race_set.name)
            self.assertIs(call.args[0], race_set)
            self.assertEqual(proposal["set"], race_set.name)

    def test_lifting_the_plan_caps_reaches_the_categories(self):
        call, _proposal, _result = planner_call(by_key("no-plan-caps"))
        for category in call.kwargs["categories"]:
            self.assertEqual(
                (category.daily_max, category.weekly_max, category.only_prime),
                (None, None, False), category.name,
            )
        shipped, _p, _r = planner_call(lab.SHIPPED)
        self.assertTrue(any(c.daily_max or c.weekly_max for c in shipped.kwargs["categories"]))
        # `between` is not a cap: mystery stays a between category.
        between = {c.name: c.between for c in call.kwargs["categories"]}
        self.assertTrue(between["mystery"])
        self.assertFalse(any(flag for name, flag in between.items() if name != "mystery"))

    def test_the_planner_gets_the_plan_files_bands(self):
        context = limited_context()
        for variant in (lab.SHIPPED, by_key("no-plan-caps"), by_key("no-between")):
            with self.subTest(variant.key):
                call, _proposal, _result = planner_call(variant)
                self.assertIs(call.kwargs["layout"], context.layout)

    def test_no_between_makes_the_between_categories_main_categories(self):
        _call, shipped, shipped_row = planner_call(lab.SHIPPED)
        _call, plain, plain_row = planner_call(by_key("no-between"))
        self.assertGreater(shipped["between"], 0)
        self.assertEqual(shipped_row["between"], shipped["between"])
        self.assertEqual(plain["between"], 0)
        self.assertEqual(plain_row["between"], 0)

    def test_two_hour_gaps_reach_the_planner_and_change_the_gaps(self):
        call, proposal, row = planner_call(by_key("two-hour-gaps"), "bonanza")
        self.assertFalse(call.kwargs["rules"].wide_gaps)
        _call, shipped, shipped_row = planner_call(lab.SHIPPED, "bonanza")
        self.assertEqual(row["short_gaps"], proposal["short_gaps"])
        self.assertEqual(shipped_row["short_gaps"], shipped["short_gaps"])
        self.assertGreaterEqual(row["short_gaps"], shipped_row["short_gaps"])

    def test_no_holidays_plans_with_a_holiday_free_model(self):
        context = limited_context()
        call, _proposal, _result = planner_call(by_key("no-holidays"))
        self.assertFalse(call.kwargs["model"].holidays)
        self.assertIs(call.kwargs["model"], context.plain_models["A"])
        shipped, _p, _r = planner_call(lab.SHIPPED)
        self.assertTrue(shipped.kwargs["model"].holidays)
        self.assertTrue(all(model.holidays for model in context.models.values()))
        self.assertFalse(any(model.holidays for model in context.plain_models.values()))

    def test_no_holidays_scores_with_holidays(self):
        context = limited_context()
        call, proposal, result = planner_call(by_key("no-holidays"))
        pairs = sp.category_pairs(call.kwargs["categories"], context.pairs)
        with_holidays = lab.score(proposal, context.models["B"], context.saturation["B"], pairs)
        without = lab.score(proposal, context.plain_models["B"], context.saturation["B"], pairs)
        self.assertAlmostEqual(result["total"], with_holidays["total"])
        self.assertAlmostEqual(result["cancelled"], with_holidays["cancelled"])
        # Labor Day is in the period, so the two models disagree about it.
        self.assertNotAlmostEqual(with_holidays["total"], without["total"])

    def test_every_lever_changes_something_on_the_fixture_world(self):
        context = limited_context()
        keys = ("races", "per_race", "weakest", "total", "cancelled", "closed", "crowded",
                "between", "short_gaps", "shortfall", "empty_hours")

        def numbers(variant):
            *_rest, result = planner_call(variant, "bonanza", context=context)
            return {key: round(result[key], 6) for key in keys}, result["categories"]

        shipped = numbers(lab.SHIPPED)
        # Every fixture history fires in every band, so the band rule has its own test.
        for variant in lab.VARIANTS:
            if variant.key != "open-bands":
                with self.subTest(variant.key):
                    self.assertNotEqual(numbers(variant), shipped)

    def test_the_band_rule_reaches_the_planner_where_a_band_is_closed(self):
        context = limited_context()
        # Mystery only fires in the evening in the planning half, so only prime time stays open.
        closed = [
            replace(slot, fired=False) if slot.category == "mystery"
            and slot.started_at.astimezone(EASTERN).hour < 18 else slot
            for slot in context.slots["A"]
        ]
        context = replace(context, slots={**context.slots, "A": closed})
        _call, shipped, shipped_row = planner_call(lab.SHIPPED, context=context)
        _call, opened, opened_row = planner_call(by_key("open-bands"), context=context)
        self.assertNotEqual(
            [row["utc"] + row["category"] for row in shipped["schedule"]],
            [row["utc"] + row["category"] for row in opened["schedule"]],
        )
        # The diagnostic judges every proposal by the shipped rule.
        self.assertEqual(shipped_row["closed"], 0)
        self.assertGreater(opened_row["closed"], 0)
        mystery = next(c for c in shipped["composition"] if c["category"] == "mystery")
        self.assertEqual(mystery["open_bands"], ["prime"])
        opened_mystery = next(c for c in opened["composition"] if c["category"] == "mystery")
        self.assertEqual(opened_mystery["open_bands"], ["prime", "normal", "quiet"])


class HalfCoverageTests(unittest.TestCase):
    def test_a_source_missing_from_one_half_is_refused(self):
        schedule, modes, races = fx.world()
        mystery = {mode["id"] for mode in modes if mode["archetype"] == 7}

        def in_half_b(row):
            local = datetime.fromtimestamp(int(row["time"]), EASTERN)
            return ((local.date() - lab.WEEK_ZERO).days // 7) % 2 == 1

        kept = [row for row in schedule if not (row["mode"] in mystery and in_half_b(row))]
        self.assertLess(len(kept), len(schedule))
        config = {"bands": fx.WORLD_BANDS, "composition": fx.BASE_COMPOSITION, "events": []}
        with self.assertRaises(lab.LabError) as caught:
            lab.build_context(kept, modes, races, config, start=fx.START,
                              months=1, pairs=fx.PAIRS)
        message = str(caught.exception)
        self.assertIn("Half B of the history has no slots for mystery", message)
        # The unfiltered world builds.
        lab.build_context(schedule, modes, races, config, start=fx.START,
                          months=1, pairs=fx.PAIRS)


class VerdictTests(unittest.TestCase):
    def test_a_change_is_judged_by_its_mean_and_its_sign_in_both_folds(self):
        tol = lab.TOLERANCE
        table = [
            ([0.02, 0.03], "better"),
            ([tol, tol], "better"),
            # Above tolerance on average, but one fold is worse or no better.
            ([0.03, -0.001], "same"),
            ([0.02, 0.0], "same"),
            ([-0.01, 0.03], "same"),
            ([0.004, 0.004], "same"),
            ([0.0, 0.0], "same"),
            ([-0.004, -0.004], "same"),
            ([-0.01, -0.02], "worse"),
            ([-tol, -tol], "worse"),
            # Worse on average, whatever the other fold says.
            ([-0.03, 0.01], "worse"),
            ([0.01, -0.03], "worse"),
        ]
        for changes, expected in table:
            with self.subTest(changes=changes):
                self.assertEqual(lab.verdict(changes), expected)


BASE = {"per_race": 10.0, "weakest": 5.0, "total": 1000.0, "cancelled": 20.0}


def fold(per_race=1.0, weakest=1.0, total=1.0, cancelled=1.0, races=None):
    """One fold: the shipped numbers times these factors, and each category's races."""
    scaled = {
        "per_race": BASE["per_race"] * per_race,
        "weakest": BASE["weakest"] * weakest,
        "total": BASE["total"] * total,
        "cancelled": BASE["cancelled"] * cancelled,
    }
    races = races or {"a": 50, "b": 50}
    return {**scaled, "categories": {name: {"races": count, "entrants": 1.0}
                                     for name, count in races.items()}}


def entry(**factors):
    """A fold-means entry whose two folds are alike."""
    return {"folds": [fold(**factors), fold(**factors)]}


def make_means(**variants):
    """means for shipped and each variant: a function of the set name giving its entry."""
    means = {}
    for set_name in SET_NAMES:
        means["shipped", set_name] = entry()
        for key, make in variants.items():
            means[key, set_name] = make(set_name)
    return means


def judged(means, key):
    return lab.judge(means, key, SET_NAMES)


class JudgeTests(unittest.TestCase):
    def test_a_variant_that_gains_a_measure_and_loses_none_wins(self):
        means = make_means(win=lambda s: entry(per_race=1.05))
        judgement = judged(means, "win")
        self.assertEqual(set(judgement), set(SET_NAMES))
        for set_name in SET_NAMES:
            measures = judgement[set_name]["measures"]
            self.assertEqual(measures["per_race"]["verdict"], "better")
            self.assertAlmostEqual(measures["per_race"]["change"], 0.05)
            self.assertEqual(measures["total"]["verdict"], "same")
            self.assertTrue(judgement[set_name]["guard"])
            self.assertEqual(judgement[set_name]["short"], [])
        self.assertTrue(lab.wins(judgement))
        self.assertTrue(lab.harmless(judgement))

    def test_cancelled_rooms_are_better_when_lower(self):
        means = make_means(fewer=lambda s: entry(cancelled=0.9), more=lambda s: entry(cancelled=1.1))
        fewer, more = judged(means, "fewer"), judged(means, "more")
        self.assertEqual(fewer["light"]["measures"]["cancelled"]["verdict"], "better")
        # `change` is the plain relative change, so fewer rooms is -10% and still better.
        self.assertAlmostEqual(fewer["light"]["measures"]["cancelled"]["change"], -0.1)
        self.assertEqual(more["light"]["measures"]["cancelled"]["verdict"], "worse")
        self.assertAlmostEqual(more["light"]["measures"]["cancelled"]["change"], 0.1)
        self.assertTrue(lab.wins(fewer))
        self.assertFalse(lab.wins(more))
        self.assertFalse(lab.harmless(more))

    def test_one_worse_measure_in_any_of_the_four_sets_stops_a_win(self):
        for worst in SET_NAMES:
            with self.subTest(worst):
                def mixed(set_name, worst=worst):
                    return entry(per_race=1.05, weakest=0.9 if set_name == worst else 1.0)

                judgement = judged(make_means(mixed=mixed), "mixed")
                self.assertEqual(judgement[worst]["measures"]["weakest"]["verdict"], "worse")
                self.assertFalse(lab.wins(judgement))
                self.assertFalse(lab.harmless(judgement))

    def test_a_gain_in_one_fold_only_is_the_same(self):
        means = make_means(
            shaky=lambda s: {"folds": [fold(per_race=1.10), fold(per_race=0.99)]}
        )
        judgement = judged(means, "shaky")
        self.assertEqual(judgement["light"]["measures"]["per_race"]["verdict"], "same")
        self.assertFalse(lab.wins(judgement))
        self.assertTrue(lab.harmless(judgement))

    def test_a_loss_in_one_fold_that_outweighs_the_other_is_worse(self):
        means = make_means(
            lopsided=lambda s: {"folds": [fold(per_race=1.01), fold(per_race=0.90)]}
        )
        judgement = judged(means, "lopsided")
        self.assertEqual(judgement["light"]["measures"]["per_race"]["verdict"], "worse")

    def test_a_gain_in_both_folds_counts_even_when_unequal(self):
        means = make_means(
            both=lambda s: {"folds": [fold(per_race=1.002), fold(per_race=1.02)]}
        )
        judgement = judged(means, "both")
        self.assertEqual(judgement["light"]["measures"]["per_race"]["verdict"], "better")
        self.assertAlmostEqual(judgement["light"]["measures"]["per_race"]["change"], 0.011)

    def test_a_variant_short_of_a_categorys_races_in_either_fold_is_disqualified(self):
        floor = lab.VOLUME_FLOOR * 50
        means = make_means(
            short=lambda s: entry(per_race=1.2, races={"a": 50, "b": floor - 1}),
            one=lambda s: {"folds": [fold(per_race=1.2), fold(per_race=1.2, races={"a": 50, "b": 10})]},
            edge=lambda s: entry(per_race=1.2, races={"a": 50, "b": floor}),
            gone=lambda s: entry(per_race=1.2, races={"a": 50}),
        )
        for key in ("short", "gone", "one"):
            with self.subTest(key):
                judgement = judged(means, key)
                self.assertFalse(judgement["light"]["guard"])
                self.assertEqual(judgement["light"]["short"], ["b"])
                self.assertEqual(judgement["light"]["measures"]["per_race"]["verdict"], "better")
                self.assertFalse(lab.wins(judgement))
                self.assertFalse(lab.harmless(judgement))
        # Exactly the floor is enough.
        edge = judged(means, "edge")
        self.assertTrue(edge["light"]["guard"])
        self.assertTrue(lab.wins(edge))

    def test_a_variant_the_planner_could_not_plan_is_an_error_and_neither_wins_nor_is_harmless(self):
        means = make_means(
            broken=lambda s: {"error": "no timetable"} if s == "full" else entry(per_race=1.1)
        )
        judgement = judged(means, "broken")
        self.assertEqual(judgement["full"], {"error": "no timetable"})
        self.assertIn("measures", judgement["light"])
        self.assertFalse(lab.wins(judgement))
        self.assertFalse(lab.harmless(judgement))

    def test_a_failed_shipped_run_is_an_error(self):
        means = make_means(win=lambda s: entry(per_race=1.05))
        means["shipped", "light"] = {"error": "boom"}
        with self.assertRaises(lab.LabError):
            judged(means, "win")

    def test_a_zero_shipped_measure_counts_as_no_change(self):
        means = make_means(v=lambda s: entry(cancelled=0.0))
        for key in means:
            for one in means[key]["folds"]:
                one["cancelled"] = 0.0 if key[0] == "shipped" else 3.0
        self.assertEqual(judged(means, "v")["light"]["measures"]["cancelled"]["verdict"], "same")


class CombinationTests(unittest.TestCase):
    def variants(self, *keys):
        return [lab.Variant(key, key, "x") for key in keys]

    def pick(self, **makers):
        means = make_means(**makers)
        variants = self.variants(*makers)
        judgements = {variant.key: judged(means, variant.key) for variant in variants}
        return [variant.key for variant in lab.combination(variants, judgements)]

    def test_two_winners_are_combined_without_the_harmless_ones(self):
        chosen = self.pick(
            w1=lambda s: entry(per_race=1.05),
            flat=lambda s: entry(),
            w2=lambda s: entry(cancelled=0.9),
            bad=lambda s: entry(per_race=0.9),
        )
        self.assertEqual(chosen, ["w1", "w2"])

    def test_with_one_winner_the_harmless_variants_are_combined(self):
        chosen = self.pick(
            w1=lambda s: entry(per_race=1.05),
            flat=lambda s: entry(),
            bad=lambda s: entry(per_race=0.9),
            short=lambda s: entry(per_race=1.2, races={"a": 50, "b": 10}),
        )
        # The winner is harmless too.
        self.assertEqual(chosen, ["w1", "flat"])

    def test_with_fewer_than_two_of_either_nothing_is_combined(self):
        self.assertEqual(
            self.pick(
                w1=lambda s: entry(per_race=1.05),
                bad=lambda s: entry(per_race=0.9),
                short=lambda s: entry(per_race=1.2, races={"a": 50, "b": 10}),
            ),
            [],
        )
        self.assertEqual(self.pick(bad=lambda s: entry(per_race=0.9)), [])
        self.assertEqual(self.pick(), [])

    def test_a_variant_with_an_error_is_never_combined(self):
        chosen = self.pick(
            err=lambda s: {"error": "x"},
            flat=lambda s: entry(),
        )
        self.assertEqual(chosen, [])

    def test_variants_keep_their_given_order(self):
        chosen = self.pick(
            b=lambda s: entry(per_race=1.05),
            a=lambda s: entry(per_race=1.05),
        )
        self.assertEqual(chosen, ["b", "a"])


_SMOKE = None


def smoke_result():
    """Compare every variant on the synthetic world once, in this process."""
    global _SMOKE
    if _SMOKE is None:
        context = limited_context()
        _SMOKE = context, lab.compare(context, workers=1)
    return _SMOKE


class ContextTests(unittest.TestCase):
    def test_the_context_holds_the_whole_year_and_its_two_halves(self):
        context, _result = smoke_result()
        self.assertEqual(set(context.slots), {lab.WHOLE, *lab.HALVES})
        self.assertEqual(
            len(context.slots["A"]) + len(context.slots["B"]), len(context.slots[lab.WHOLE])
        )
        self.assertEqual(set(context.models), set(context.slots))
        self.assertEqual(set(context.plain_models), set(context.slots))
        self.assertEqual(set(context.saturation), set(context.slots))
        for table in context.saturation.values():
            self.assertEqual(sorted(table), list(range(1, lab.SATURATION_BUCKETS + 1)))
        for part in context.slots:
            self.assertTrue(context.models[part].holidays)
            self.assertFalse(context.plain_models[part].holidays)
        self.assertLess(context.period_start, context.period_end)

    def test_the_context_carries_the_plan_files_bands(self):
        context, _result = smoke_result()
        self.assertEqual(context.layout, sp.parse_bands(fx.WORLD_BANDS))
        self.assertEqual(context.layout, fx.WORLD_LAYOUT)
        self.assertEqual(context.config["bands"], fx.WORLD_BANDS)


class SmokeTests(unittest.TestCase):
    def test_every_single_variant_is_compared(self):
        _context, result = smoke_result()
        keys = [variant["key"] for variant in result["variants"]]
        self.assertEqual(keys[0], "shipped")
        for variant in lab.VARIANTS:
            self.assertIn(variant.key, keys)
        self.assertEqual(len(keys), len(set(keys)))
        for gone in ("seeds", "iterations"):
            self.assertNotIn(gone, result)
        for row in result["variants"]:
            self.assertIn("changes", row)
        self.assertEqual(result["variants"][0]["changes"], {})

    def test_each_mean_is_numeric_or_an_error(self):
        _context, result = smoke_result()
        seen = {(row["variant"], row["set"]) for row in result["means"]}
        for variant in result["variants"]:
            for set_name in SET_NAMES:
                self.assertIn((variant["key"], set_name), seen)
        for row in result["means"]:
            with self.subTest(row["variant"], set=row["set"]):
                if "error" in row:
                    self.assertIsInstance(row["error"], str)
                else:
                    for measure in lab.MEASURES:
                        self.assertIsInstance(row[measure.key], float)
                    for field in lab.DIAGNOSTICS:
                        self.assertIn(field, row)
                    self.assertEqual(len(row["folds"]), 2)
                    self.assertGreater(row["races"], 0)
        # The shipped rules must plan the synthetic world.
        for row in result["means"]:
            if row["variant"] == "shipped":
                self.assertNotIn("error", row)

    def test_the_judgements_and_the_recommendations_name_known_variants(self):
        _context, result = smoke_result()
        keys = {variant["key"] for variant in result["variants"]}
        self.assertEqual(set(result["judgements"]), keys - {"shipped"})
        for name in (*result["recommended"], *result["trial"], *result["combined_parts"]):
            self.assertIn(name, keys | {"combined"})
        self.assertEqual(bool(result["combined_parts"]), "combined" in keys)
        # Model-blind winners are for a trial, the rest recommended.
        blind = {variant.key for variant in lab.VARIANTS if variant.model_blind}
        self.assertFalse(set(result["recommended"]) & blind)

    def test_the_result_is_json(self):
        _context, result = smoke_result()
        json.dumps(result)

    def test_the_report_names_every_variant_and_every_set(self):
        _context, result = smoke_result()
        text = lab.format_report(result)
        for variant in result["variants"]:
            self.assertIn(variant["key"], text)
        for set_name in SET_NAMES:
            self.assertIn(f"{set_name.capitalize()}:", text)
        self.assertIn("Diagnostics, Bonanza", text)
        self.assertIn("Recommended:", text)

    def test_the_run_is_deterministic(self):
        context, result = smoke_result()
        again = lab.compare(context, workers=1)
        self.assertEqual(again["means"], result["means"])
        self.assertEqual(again["judgements"], result["judgements"])

    def test_one_variant_can_be_run_alone_and_an_unknown_one_is_refused(self):
        context, _result = smoke_result()
        with self.assertRaises(lab.LabError):
            lab.compare(context, workers=1, only="nonsense")
        alone = lab.compare(context, workers=1, only="no-featured")
        self.assertEqual([v["key"] for v in alone["variants"]], ["shipped", "no-featured"])
        self.assertEqual(alone["combined_parts"], [])
        self.assertEqual(
            {(row["variant"], row["set"]) for row in alone["means"]},
            {(key, name) for key in ("shipped", "no-featured") for name in SET_NAMES},
        )


if __name__ == "__main__":
    unittest.main()
