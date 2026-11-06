import csv
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import schedule_fixtures as fx
from scripts import schedule_planner as sp

EASTERN = ZoneInfo("America/New_York")
# 2026-03-02 is a Monday. Wall-clock arithmetic keeps the Eastern hour across the March 8 shift.
MONDAY = datetime(2026, 3, 2)
# Quiet hours 0 to 5, prime hours 12 to 17 and normal otherwise, in every day group.
BANDS = fx.LAYOUT


def slot(category, day_offset, hour, fired):
    """A slot on Monday 2026-03-02 plus whole days (day_offset), Eastern hour."""
    local = (MONDAY + timedelta(days=day_offset, hours=hour)).replace(tzinfo=EASTERN)
    return sp.HistorySlot(local.astimezone(UTC), 1, category, 1, 5 if fired else 0, fired)


def history():
    return [
        # Alpha, Monday 10:00 (normal): 2 of 5 fired = 0.4.
        *[slot("alpha", 7 * week, 10, week < 2) for week in range(5)],
        # Alpha, Monday 20:00 (normal): 1 of 5 fired = exactly 0.2, not low.
        *[slot("alpha", 7 * week, 20, week == 0) for week in range(5)],
        # Alpha, Monday 14:00 and 15:00 (prime): 0 of 3 and 1 of 1.
        *[slot("alpha", 7 * week, 14, False) for week in range(3)],
        slot("alpha", 0, 15, True),
        # Alpha, Sunday 23:00 (normal in the prime group) fired, Monday 00:00 (quiet) did not.
        slot("alpha", 6, 23, True),
        slot("alpha", 0, 0, False),
        # Alpha, Monday 5:00 (quiet): 1 of 6 is below 20%.
        *[slot("alpha", 7 * week, 5, week == 0) for week in range(6)],
        # Beta fires nowhere, in one normal slot: every band of it is closed.
        slot("beta", 1, 9, False),
    ]


def rows_for(slots, rates=None):
    if rates is None:
        rates = sp.band_rates(slots, fx.flat_model(sorted({s.category for s in slots})), BANDS)
    return sp.success_rates(slots, BANDS, rates)


def cell(rows, category, weekday, hour):
    (row,) = [
        r
        for r in rows
        if (r["category"], r["weekday"], r["hour"]) == (category, weekday, hour)
    ]
    return row


class SuccessRateTests(unittest.TestCase):
    def setUp(self):
        self.rows = rows_for(history())

    def test_one_row_per_category_and_weekday_hour(self):
        self.assertEqual(len(self.rows), 2 * 168)
        self.assertEqual([r["category"] for r in self.rows[:168]], ["alpha"] * 168)
        self.assertEqual(self.rows[1]["hour"], 1)
        self.assertEqual(self.rows[24]["weekday"], 1)

    def test_counts_and_rate(self):
        row = cell(self.rows, "alpha", 0, 10)
        self.assertEqual((row["scheduled"], row["fired"], row["rate"]), (5, 2, 0.4))
        self.assertFalse(row["low"])
        self.assertFalse(row["closed"])

    def test_untried_cell_has_no_rate_and_is_not_low(self):
        row = cell(self.rows, "alpha", 3, 12)
        self.assertEqual((row["scheduled"], row["fired"], row["rate"]), (0, 0, None))
        self.assertFalse(row["low"])

    def test_low_is_strictly_below_the_threshold(self):
        self.assertFalse(cell(self.rows, "alpha", 0, 20)["low"])
        self.assertEqual(cell(self.rows, "alpha", 0, 20)["rate"], 0.2)
        self.assertTrue(cell(self.rows, "alpha", 0, 5)["low"])

    def test_a_zero_rate_cell_is_low_but_closed_follows_its_band(self):
        row = cell(self.rows, "alpha", 0, 14)
        self.assertEqual(row["rate"], 0.0)
        self.assertTrue(row["low"])
        # Alpha fired 1 of 4 slots in prime time, so the band is open.
        self.assertFalse(row["closed"])

    def test_each_row_carries_its_weekdays_band(self):
        self.assertEqual(cell(self.rows, "alpha", 0, 3)["band"], "quiet")
        self.assertEqual(cell(self.rows, "alpha", 4, 14)["band"], "prime")
        self.assertEqual(cell(self.rows, "alpha", 6, 20)["band"], "normal")

    def test_a_category_that_never_fired_has_every_band_closed(self):
        beta = [r for r in self.rows if r["category"] == "beta"]
        self.assertTrue(all(r["closed"] for r in beta))
        self.assertEqual(cell(self.rows, "beta", 1, 9)["rate"], 0.0)
        self.assertTrue(cell(self.rows, "beta", 1, 9)["closed"])
        self.assertTrue(cell(self.rows, "beta", 5, 3)["closed"])

    def test_closed_is_the_near_never_rule_on_the_band_rate(self):
        rates = {"alpha": {"prime": (1, 10), "normal": (9, 100), "quiet": (0, 0)}}
        rows = rows_for([slot("alpha", 0, 10, True)], rates)
        # Exactly 10% is open, 9% is closed, and a band with no slots never fired.
        self.assertFalse(cell(rows, "alpha", 0, 14)["closed"])
        self.assertTrue(cell(rows, "alpha", 0, 10)["closed"])
        self.assertTrue(cell(rows, "alpha", 0, 2)["closed"])


def result_for(rows, slots):
    model = fx.flat_model(sorted({s.category for s in slots}))
    rates = sp.band_rates(slots, model, BANDS)
    return {
        "proposals": [],
        "success_rates": rows,
        "band_rates": [
            {
                "category": category,
                "band": band,
                "fired": rates[category][band][0],
                "scheduled": rates[category][band][1],
                "rate": sp.fire_rate(rates[category][band]),
                "closed": sp.fire_rate(rates[category][band]) < sp.NEAR_NEVER_RATE,
            }
            for category in sorted(rates)
            for band in sp.BANDS
        ],
        "parameters": {"low_rate": sp.LOW_RATE, "near_never_rate": sp.NEAR_NEVER_RATE},
        "history": {"start": "2025-01-01", "end": "2025-12-31"},
        "generated_at": "2026-01-01T00:00:00Z",
    }


class CsvTests(unittest.TestCase):
    def test_header_order_and_formatting(self):
        slots = history()
        with tempfile.TemporaryDirectory() as directory:
            paths = sp.write_outputs(result_for(rows_for(slots), slots), Path(directory))
            self.assertEqual([p.name for p in paths], ["success-rates.csv"])
            with paths[0].open(encoding="utf-8", newline="") as source:
                lines = list(csv.reader(source))
        self.assertEqual(
            lines[0],
            ["category", "weekday", "hour", "band", "scheduled", "fired", "rate", "low", "closed"],
        )
        self.assertEqual(len(lines), 1 + 2 * 168)
        self.assertEqual(lines[1], ["alpha", "Mon", "0", "quiet", "1", "0", "0.000", "true", "false"])
        self.assertEqual(lines[2], ["alpha", "Mon", "1", "quiet", "0", "0", "", "false", "false"])
        self.assertEqual(lines[1 + 10], ["alpha", "Mon", "10", "normal", "5", "2", "0.400", "false", "false"])
        self.assertEqual(lines[1 + 168][:2], ["beta", "Mon"])
        self.assertEqual(lines[1 + 168 + 24][1], "Tue")
        self.assertEqual(lines[1 + 168 + 24 + 9], ["beta", "Tue", "9", "normal", "1", "0", "0.000", "true", "true"])


class PageTests(unittest.TestCase):
    def setUp(self):
        slots = history() + [slot("a<b", 0, 9, True), slot("a<b", 1, 9, True)]
        self.page = sp.render_rates_html(result_for(rows_for(slots), slots))

    def test_follows_the_page_contract(self):
        self.assertTrue(self.page.startswith("<title>"))
        self.assertNotIn("<script", self.page)
        self.assertNotIn("http", self.page)
        self.assertNotIn("<html", self.page)

    def test_makes_no_claim_about_how_the_bands_are_chosen(self):
        for phrase in ("most expected turnout", "ranked", "least"):
            self.assertNotIn(phrase, self.page)
        self.assertNotIn("<script", self.page)

    def test_escapes_category_names(self):
        self.assertIn("a&lt;b", self.page)
        self.assertNotIn("a<b", self.page)

    def test_one_grid_per_category_ordered_by_fired_share(self):
        self.assertEqual(self.page.count('<table class="rates">'), 3)
        # a<b fired 2 of 2, alpha about a third, beta none.
        order = [self.page.index(f"<h2>{name}</h2>") for name in ("a&lt;b", "alpha", "beta")]
        self.assertEqual(order, sorted(order))

    def test_cells_and_legend(self):
        self.assertIn('title="2 fired of 5 scheduled"', self.page)
        self.assertIn("Legend", self.page)
        self.assertIn("below 20%", self.page)
        self.assertIn("&times;", self.page)
        self.assertIn("2025-01-01", self.page)
        self.assertIn("throwback", self.page)
        self.assertNotIn("blocked", self.page)

    def test_closed_cells_are_marked(self):
        # Beta's one slot is in a closed band; a<b never tried prime or quiet, so those are closed.
        self.assertIn('<td class="r-low r-closed" title="0 fired of 1 scheduled">0<span', self.page)
        self.assertIn('<td class="r-closed"></td>', self.page)
        # An open band's cell carries no mark.
        self.assertIn('<td title="1 fired of 1 scheduled">100</td>', self.page)

    def test_band_rates_are_listed_per_category(self):
        self.assertEqual(self.page.count("<th>Band</th>"), 3)
        for band in ("prime", "normal", "quiet"):
            self.assertEqual(self.page.count(f"<td>{band}</td>"), 3)
        # Alpha: 4 of 11 normal slots fired, and beta's bands are closed.
        self.assertIn("<td>normal</td><td", self.page)
        self.assertIn('<td class="num">4</td><td class="num">11</td><td class="num">36%</td>', self.page)
        self.assertIn('<span class="badge">closed</span>', self.page)
        self.assertIn("n/a", self.page)


if __name__ == "__main__":
    unittest.main()
