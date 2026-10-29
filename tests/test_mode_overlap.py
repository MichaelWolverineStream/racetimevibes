import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from scripts import mode_overlap

EASTERN = ZoneInfo("America/New_York")
GENERATED_AT = datetime(2026, 10, 29, tzinfo=UTC)


def context(mode, racers, *, month="2026-05", day=1, dnf=()):
    """Build a race context; a None racer is an anonymous entrant."""
    entrants = []
    for place, racer in enumerate(racers, start=1):
        finished = racer not in dnf
        entrants.append(
            {
                "user": {"id": racer, "name": racer} if racer else None,
                "status": {"value": "done" if finished else "dnf"},
                "place": place if finished else None,
                "finish_time": None,
            }
        )
    year, month_number = (int(part) for part in month.split("-"))
    return {
        "race": {"name": f"r/{mode}-{month}-{day}-{len(racers)}", "entrants": entrants},
        "local_started_at": datetime(year, month_number, day, 20, tzinfo=EASTERN),
        "mode": mode,
    }


def scenario():
    """One co-active month in which x races both modes, y only A and z only B.

    A entries: x 4, y 2. B entries: x 1, z 4. Active racers: x, y, z.
    """
    return [
        context("a", ["x", "y", None], day=1, dnf={"y"}),
        context("a", ["x", "y"], day=2),
        context("a", ["x"], day=3),
        context("a", ["x"], day=4),
        context("b", ["x", "z"], day=1),
        context("b", ["z"], day=2),
        context("b", ["z"], day=3),
        context("b", ["z"], day=4),
    ]


def pair(contexts, **kwargs):
    monthly = mode_overlap.monthly_entries(contexts)
    return mode_overlap.pair_overlap(monthly, "a", "b", **kwargs)


class DirectedShareTests(unittest.TestCase):
    def test_shares_are_directed(self):
        result = pair(scenario())
        self.assertAlmostEqual(result.a_to_b, 4 / 6)
        self.assertAlmostEqual(result.b_to_a, 1 / 5)
        self.assertAlmostEqual(result.score, 4 / 6)

    def test_anonymous_entrants_are_skipped_and_dnfs_count(self):
        monthly = mode_overlap.monthly_entries(scenario())
        self.assertEqual(set(monthly.racers), {"x", "y", "z"})
        self.assertEqual(monthly.racers["y"]["2026-05"]["a"], 2)

    def test_min_entries_requires_repeat_entries_in_the_other_mode(self):
        result = pair(scenario(), min_entries=2)
        self.assertAlmostEqual(result.a_to_b, 0)
        self.assertAlmostEqual(result.b_to_a, 1 / 5)

    def test_chance_baseline_is_the_other_modes_reach(self):
        result = pair(scenario())
        self.assertAlmostEqual(result.a_to_b_chance, 2 / 3)
        self.assertAlmostEqual(result.b_to_a_chance, 2 / 3)

    def test_chance_baseline_weights_months_by_entries(self):
        monthly = mode_overlap.monthly_entries(
            scenario()
            + [context("a", ["y"], month="2026-06", day=day) for day in range(1, 5)]
            + [context("b", ["z"], month="2026-06", day=day) for day in range(1, 5)]
        )
        months = {"2026-05", "2026-06"}
        result = mode_overlap.directed_overlap(monthly, "a", "b", months)
        # May: 6 A entries at reach 2/3. June: 4 A entries at reach 1/2.
        self.assertAlmostEqual(result.chance, (6 * 2 / 3 + 4 * 1 / 2) / 10)
        self.assertAlmostEqual(result.share, 4 / 10)

    def test_months_below_the_schedule_minimum_are_ignored(self):
        june = [context("a", ["y"], month="2026-06", day=day) for day in range(1, 4)]
        june += [context("b", ["y"], month="2026-06", day=day) for day in range(1, 5)]
        result = pair(scenario() + june)
        self.assertEqual(result.co_active_months, 1)
        self.assertAlmostEqual(result.a_to_b, 4 / 6)

    def test_pair_without_co_active_months_is_unmeasured(self):
        contexts = [context("a", ["x"], day=day) for day in range(1, 5)]
        contexts += [context("b", ["x"], month="2026-06", day=day) for day in range(1, 5)]
        result = pair(contexts)
        self.assertIsNone(result.score)
        self.assertIsNone(result.ci_low)
        self.assertEqual(result.rating, "unmeasured")
        self.assertTrue(result.weak)


class RatingTests(unittest.TestCase):
    def test_rating_boundaries(self):
        self.assertEqual(mode_overlap.rate(0.2499), "low")
        self.assertEqual(mode_overlap.rate(0.25), "moderate")
        self.assertEqual(mode_overlap.rate(0.4999), "moderate")
        self.assertEqual(mode_overlap.rate(0.5), "high")
        self.assertEqual(mode_overlap.rate(None), "unmeasured")

    def test_weak_evidence_below_six_co_active_months(self):
        def months(count):
            contexts = []
            for index in range(count):
                month = f"2026-{index + 1:02d}"
                for day in range(1, 5):
                    contexts.append(context("a", ["x"], month=month, day=day))
                    contexts.append(context("b", ["y"], month=month, day=day))
            return contexts

        self.assertTrue(pair(months(5)).weak)
        self.assertFalse(pair(months(6)).weak)
        self.assertEqual(pair(months(6)).co_active_months, 6)


class BootstrapTests(unittest.TestCase):
    def test_weights_are_deterministic_for_a_seed(self):
        first = mode_overlap.bootstrap_weights(["x", "y", "z"], rounds=20, seed=7)
        second = mode_overlap.bootstrap_weights(["z", "y", "x"], rounds=20, seed=7)
        self.assertEqual(first, second)
        self.assertTrue(all(sum(weight.values()) == 3 for weight in first))

    def test_interval_brackets_the_estimate(self):
        result = pair(scenario())
        self.assertLessEqual(result.ci_low, result.score)
        self.assertGreaterEqual(result.ci_high, result.score)


class AnalyseTests(unittest.TestCase):
    def analyse(self, contexts, **kwargs):
        return mode_overlap.analyse(
            contexts, generated_at=GENERATED_AT, bootstrap_rounds=50, **kwargs
        )

    def test_result_shape(self):
        result = self.analyse(scenario())
        self.assertEqual(result["generated_at"], "2026-10-29T00:00:00Z")
        self.assertEqual(
            result["source"],
            {
                "race_count": 8,
                "entry_count": 11,
                "racer_count": 3,
                "first_month": "2026-05",
                "last_month": "2026-05",
            },
        )
        self.assertEqual([mode["mode"] for mode in result["modes"]], ["a", "b"])
        self.assertEqual(result["modes"][0]["scheduled_months"], 1)
        self.assertEqual(result["periods"], [{"id": "2026-H1", "label": "Jan-Jun 2026"}])
        [only] = result["pairs"]
        self.assertEqual((only["a"], only["b"]), ("a", "b"))
        self.assertEqual(only["rating"], "high")
        self.assertEqual(
            only["trend"],
            [{"period": "2026-H1", "score": only["score"], "co_active_months": 1}],
        )

    def test_pairs_are_sorted_by_score(self):
        contexts = scenario()
        contexts += [context("c", ["w"], day=day) for day in range(1, 5)]
        scores = [pair["score"] for pair in self.analyse(contexts)["pairs"]]
        self.assertEqual(scores, sorted(scores))
        self.assertEqual(scores[0], 0)

    def test_rejects_empty_input_and_bad_min_entries(self):
        with self.assertRaises(mode_overlap.ModeOverlapError):
            self.analyse([])
        with self.assertRaises(mode_overlap.ModeOverlapError):
            self.analyse(scenario(), min_entries=0)

    def test_text_report(self):
        report = mode_overlap.format_report(self.analyse(scenario()))
        self.assertIn("at least 1 entry in the other mode", report)
        self.assertIn("a / b", report)
        self.assertIn("high, weak evidence", report)


class RenderHtmlTests(unittest.TestCase):
    def render(self, contexts):
        result = mode_overlap.analyse(
            contexts, generated_at=GENERATED_AT, bootstrap_rounds=50
        )
        return mode_overlap.render_html(result)

    def test_is_a_titled_artifact_page_with_every_mode(self):
        result = mode_overlap.analyse(
            scenario(), generated_at=GENERATED_AT, bootstrap_rounds=50
        )
        page = mode_overlap.render_html(result)
        # Publishing adds the document skeleton, so the page starts at its title.
        self.assertTrue(page.startswith("<title>Mode Overlap</title>"))
        for tag in ("<!doctype", "<html", "<head>", "<body"):
            self.assertNotIn(tag, page.lower())
        for mode in result["modes"]:
            self.assertIn(f"<td>{mode['mode']}</td>", page)

    def test_is_self_contained(self):
        page = self.render(scenario())
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_mode_names_are_escaped(self):
        contexts = [
            context("<b>x", ["x", "y"], day=day) for day in range(1, 5)
        ] + [context("b", ["x", "z"], day=day) for day in range(1, 5)]
        page = self.render(contexts)
        self.assertIn("&lt;b&gt;x", page)
        self.assertNotIn("<b>x", page)

    def test_unmeasured_pair_renders(self):
        contexts = [context("a", ["x"], day=day) for day in range(1, 5)]
        contexts += [context("b", ["x"], month="2026-06", day=day) for day in range(1, 5)]
        page = self.render(contexts)
        self.assertIn("unmeasured", page)
        self.assertIn("\u2013", page)


if __name__ == "__main__":
    unittest.main()
