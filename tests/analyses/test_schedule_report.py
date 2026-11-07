import csv
import json
import re
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import schedule_fixtures as fx
from scripts import schedule_planner as sp


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fx.main_result()

    def test_csv_and_json_agree_for_every_set(self):
        with tempfile.TemporaryDirectory() as folder:
            written = sp.write_outputs(self.result, Path(folder))
            names = {path.name for path in written}
            expected = {f"schedule-{s.name}.{ext}" for s in sp.SETS for ext in ("csv", "json")}
            self.assertEqual(names, expected | {"success-rates.csv"})
            self.assertEqual(len(sp.SETS), 4)
            for path in written:
                self.assertEqual(path.parent, Path(folder))
                self.assertTrue(path.exists())
            for proposal in self.result["proposals"]:
                stem = Path(folder) / f"schedule-{proposal['set']}"
                with stem.with_suffix(".csv").open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.reader(handle))
                self.assertEqual(tuple(rows[0]), sp.CSV_COLUMNS)
                self.assertEqual(len(rows) - 1, proposal["races"])
                api = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertEqual(len(api), len(rows) - 1)
                for row, entry, race in zip(rows[1:], api, proposal["schedule"]):
                    at = datetime.fromisoformat(row[5].replace("Z", "+00:00"))
                    self.assertEqual(entry, {"time": str(int(at.timestamp())), "mode": int(row[4])})
                    local = at.astimezone(fx.EASTERN)
                    self.assertEqual((row[0], row[1]), (local.date().isoformat(), local.strftime("%H:00")))
                    self.assertEqual(
                        (row[2], row[3], int(row[4])),
                        (race["category"], race["mode"], race["mode_id"]),
                    )

    def test_success_rates_csv_has_the_spec_columns_and_formatting(self):
        with tempfile.TemporaryDirectory() as folder:
            sp.write_outputs(self.result, Path(folder))
            with (Path(folder) / "success-rates.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(tuple(rows[0]), sp.RATE_COLUMNS)
        self.assertEqual(len(rows) - 1, len(self.result["success_rates"]))
        for row, source in zip(rows[1:], self.result["success_rates"]):
            self.assertEqual(row[:4], [
                source["category"], sp.WEEKDAY_SHORT[source["weekday"]],
                str(source["hour"]), source["band"],
            ])
            self.assertEqual(row[4:6], [str(source["scheduled"]), str(source["fired"])])
            self.assertEqual(row[6], "" if source["rate"] is None else f"{source['rate']:.3f}")
            self.assertEqual(row[7:], [str(source["low"]).lower(), str(source["closed"]).lower()])

    def test_every_mode_has_an_id_so_the_json_is_always_written(self):
        for result in (fx.main_result(), fx.mixed_result(), fx.limit_result()):
            for proposal in result["proposals"]:
                self.assertTrue(all(isinstance(r["mode_id"], int) for r in proposal["schedule"]))

    def test_the_directory_is_created_and_files_are_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "nested" / "out"
            sp.write_outputs(self.result, target)
            first = (target / "schedule-light.csv").read_text(encoding="utf-8")
            sp.write_outputs(self.result, target)
            self.assertEqual((target / "schedule-light.csv").read_text(encoding="utf-8"), first)

    def test_the_mixed_categorys_csv_rows_name_the_mode_that_ran(self):
        with tempfile.TemporaryDirectory() as folder:
            sp.write_outputs(fx.mixed_result(), Path(folder))
            with (Path(folder) / "schedule-full.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))[1:]
        throwback = {(row[3], row[4]) for row in rows if row[2] == "throwback"}
        self.assertEqual(throwback, {("Keys A", "41"), ("Myst A", "71")})


class RenderHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fx.between_result()
        cls.page = sp.render_html(cls.result)
        cls.markup = fx.cached("markup", fx.MARKUP_COMPOSITION, events=fx.MARKUP_EVENTS)
        cls.left_out = fx.limit_result()

    def test_the_lede_counts_the_between_races_on_top(self):
        self.assertIn("races a week plus the races that start between them", self.page)

    def test_is_a_titled_artifact_page(self):
        self.assertTrue(self.page.startswith("<title>"))
        for tag in ("<!doctype", "<html", "<head>", "<body"):
            self.assertNotIn(tag, self.page.lower())

    def test_is_self_contained(self):
        self.assertNotIn("<script", self.page.lower())
        self.assertNotIn("http://", self.page)
        self.assertNotIn("https://", self.page)

    def test_names_are_escaped(self):
        page = sp.render_html(self.markup)
        self.assertIn("&lt;i&gt;Chaos&amp;Co", page)
        self.assertNotIn("<i>Chaos", page)
        self.assertNotIn("<img src=x>", page)
        self.assertIn("&lt;img src=x&gt;", page)
        left_out = sp.render_html(self.left_out)
        self.assertIn("&lt;b&gt;fun&lt;/b&gt;", left_out)
        self.assertNotIn("<b>fun", left_out)

    def test_the_summary_lists_the_four_sets_and_marks_one_best(self):
        self.assertIn("<h2>Summary</h2>", self.page)
        for race_set in sp.SETS:
            self.assertIn(f"<strong>{race_set.name.capitalize()}</strong>", self.page)
            self.assertIn(f"<summary>{race_set.name.capitalize()}:", self.page)
            self.assertIn(escape(race_set.description), self.page)
        self.assertIn("The four proposals", self.page)
        self.assertEqual(self.page.count("best per race"), 1)
        for heading in ("Between races a week", "Short gaps", "Shortfall dates", "Weekly repeats",
                        "Event conflicts", "Cancelled rooms", "Expected entrants"):
            self.assertIn(heading, self.page)
        self.assertIn("Today", self.page)

    def test_the_summary_rows_carry_between_races_a_week_and_short_gaps(self):
        table = sp._summary_table(self.result)
        self.assertEqual(table.count('<div class="summary">'), 1)
        rows = re.findall(r"<tr>(.*?)</tr>", table)
        self.assertEqual(len(rows), 1 + 2 + 4)
        head = re.findall(r"<th[^>]*>(.*?)</th>", rows[0])
        self.assertEqual(len(head), 12)
        for row in rows[1:]:
            self.assertEqual(len(re.findall(r"<td", row)), 12)
        for proposal in self.result["proposals"]:
            row = next(r for r in rows if f"<strong>{proposal['set'].capitalize()}</strong>" in r)
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
            week = proposal["between"] / proposal["days"] * 7
            self.assertEqual(cells[head.index("Between races a week")], f"{week:.1f}")
            self.assertEqual(cells[head.index("Short gaps")], str(proposal["short_gaps"]))
            self.assertEqual(cells[head.index("Shortfall dates")], str(len(proposal["shortfall"])))

    def test_has_the_band_layout_grid_and_a_full_schedule_per_set(self):
        self.assertIn("<h2>Bands</h2>", self.page)
        self.assertEqual(self.page.count('aria-label="Band by hour for each day group"'), 1)
        self.assertEqual(self.page.count("<h3>Full schedule</h3>"), 4)
        self.assertEqual(self.page.count('<rect class="cell b-'), 72)
        for band in ("prime", "normal", "quiet"):
            self.assertIn(f'class="cell b-{band}"', self.page)
            self.assertIn(f'class="run b-{band}"', self.page)

    def test_the_band_layout_shows_the_plan_file_bands_for_each_group(self):
        section = sp._band_section(self.result)
        self.assertIn("set by hand in the plan file", section)
        self.assertNotIn("most expected turnout", section)
        for group, ranges in fx.WORLD_BANDS.items():
            self.assertIn(
                f"Plan file: prime {ranges['prime']}, quiet {ranges['quiet']}.", section
            )
        self.assertIn("prime 18:00 to 23:00 (6 hours); quiet 01:00 to 06:00 (6 hours); "
                      "the other 12 hours normal", section)
        self.assertIn("prime 16:00 to 23:00 (8 hours); quiet 00:00 to 05:00 (6 hours); "
                      "the other 10 hours normal", section)
        self.assertIn("Hover it for the model", section)
        row = self.result["proposals"][0]["bands"][0]
        self.assertIn(f"value {row['value']:.2f} expected entrants", sp._band_layout(self.result))

    def test_the_full_schedule_holds_every_date_and_every_race(self):
        period = self.result["period"]
        first = date.fromisoformat(period["start"][:10])
        every = {(first + timedelta(days=n)).isoformat() for n in range(period["days"])}
        weeks = sp._weeks(self.result)
        self.assertEqual({d.isoformat() for week in weeks for d in week}, every)
        self.assertTrue(all(week[0].weekday() == 0 for week in weeks[1:]))
        self.assertTrue(all(week[-1].weekday() == 6 for week in weeks[:-1]))
        for proposal in self.result["proposals"]:
            grid = sp._full_schedule(self.result, proposal)
            self.assertEqual(grid.count("<svg"), len(weeks))
            self.assertEqual(grid.count("<h4>Week "), len(weeks))
            labels = re.findall(r'class="axis" x="94" [^>]*>([A-Z][a-z]{2} \d\d-\d\d)', grid)
            self.assertEqual(len(labels), period["days"])
            titles = re.findall(r"<g><title>(.*?)</title>", grid)
            self.assertEqual(len(titles), proposal["races"])
            self.assertEqual(sum("between race" in t for t in titles), proposal["between"])
            featured_between = sum(
                r["featured"] and r["between"] for r in proposal["schedule"]
            )
            self.assertEqual(grid.count('class="btw"'), proposal["between"] - featured_between)
            self.assertEqual(grid.count('class="btw-feat"'), featured_between)
            moved_suffix = re.compile(r", moved an hour (earlier|later)$")
            self.assertEqual(
                sum(moved_suffix.sub("", t).endswith("featured") for t in titles)
                + sum(", featured, between race" in t for t in titles),
                sum(r["featured"] for r in proposal["schedule"]),
            )

    def test_the_full_schedule_hover_names_date_hour_band_category_and_mode(self):
        proposal = fx.proposal(self.result, "light")
        race = next(r for r in proposal["schedule"] if r["between"])
        grid = sp._full_schedule(self.result, proposal)
        self.assertIn(
            f"{race['date']} {race['hour']:02d}:00 ET, {race['band']} time: "
            f"{race['category']}, {race['mode']}", grid
        )
        self.assertIn(", between race", grid)

    def test_the_full_schedule_hover_says_which_way_a_race_moved(self):
        for proposal in self.result["proposals"]:
            with self.subTest(set=proposal["set"]):
                grid = sp._full_schedule(self.result, proposal)
                titles = re.findall(r"<g><title>(.*?)</title>", grid)
                earlier = [t for t in titles if t.endswith(", moved an hour earlier")]
                later = [t for t in titles if t.endswith(", moved an hour later")]
                rows = proposal["schedule"]
                self.assertEqual(len(earlier), sum(r["moved"] < 0 for r in rows))
                self.assertEqual(len(later), sum(r["moved"] > 0 for r in rows))
                self.assertEqual(len(earlier) + len(later), proposal["moved"])
                # Every title is one row, in order, and a race that stayed has no suffix.
                for row in rows:
                    head = (f"{row['date']} {row['hour']:02d}:00 ET, ")
                    mine = [t for t in titles if t.startswith(head) or f"{row['date']} (holiday) {row['hour']:02d}:00 ET, " in t]
                    suffix = ", moved an hour later" if row["moved"] > 0 else (
                        ", moved an hour earlier" if row["moved"] < 0 else None)
                    self.assertEqual(len(mine), 1)
                    if suffix:
                        self.assertTrue(mine[0].endswith(suffix), mine[0])
                    else:
                        self.assertNotIn("moved an hour", mine[0])
        self.assertTrue(any(p["moved"] for p in self.result["proposals"]))

    def test_the_hover_suffix_follows_featured_and_between_ones(self):
        proposal = fx.proposal(self.result, "light")
        row = next(r for r in proposal["schedule"] if r["featured"] and not r["between"])
        for moved, suffix in ((1, ", moved an hour later"), (-1, ", moved an hour earlier"), (0, "")):
            with self.subTest(moved=moved):
                rows = [dict(r, moved=moved) if r is row else r for r in proposal["schedule"]]
                grid = sp._full_schedule(self.result, {**proposal, "schedule": rows})
                self.assertIn(f"{row['category']}, {row['mode']}, featured{suffix}</title>", grid)

    def test_the_full_schedule_marks_holidays_with_the_prime_groups_bands(self):
        self.assertEqual(self.result["period"]["holidays"], ["2026-09-07"])
        grid = sp._full_schedule(self.result, fx.proposal(self.result, "light"))
        self.assertIn("Mon 09-07 ★", grid)
        self.assertIn("(holiday)", grid)
        # A holiday Monday starts its prime time at 16, not at the Work group's 18.
        row = re.search(r'>Mon 09-07 ★</text>(.*?)(?=<text class="axis"|</svg>)', grid, re.S).group(1)
        prime = re.search(r'class="run b-prime" x="(\d+)"', row)
        self.assertEqual((int(prime.group(1)) - 100) // sp._CELL, 16)

    def test_the_full_schedule_stays_a_small_page(self):
        self.assertLess(len(self.page), 2_000_000)

    def test_the_legend_names_between_races_and_featured_races(self):
        self.assertIn("between race", sp._SCHEDULE_KEY)
        self.assertIn("featured race", sp._SCHEDULE_KEY)
        self.assertIn('class="swatch btw"', sp._SCHEDULE_KEY)
        self.assertIn('class="swatch btw-feat"', sp._SCHEDULE_KEY)
        self.assertIn("featured between race", sp._SCHEDULE_KEY)
        self.assertNotIn("pair", sp._SCHEDULE_KEY)
        self.assertIn("&#9733; holiday", sp._SCHEDULE_KEY)

    def test_the_composition_section_lists_modes_favourites_bands_and_weights(self):
        self.assertIn("<h2>Composition</h2>", self.page)
        for name in ("simple", "entrance", "keys", "mystery"):
            self.assertIn(f"<td>{name}", self.page)
        self.assertIn("Ent B", self.page)
        self.assertIn("favourite", self.page)
        self.assertIn("retired", self.page)
        self.assertIn("Weight by band", self.page)

    def test_the_composition_marks_between_categories(self):
        composition = sp._composition_section(self.result)
        self.assertEqual(composition.count("between category"), 1)
        self.assertIn("<td>mystery<span", composition)
        self.assertNotIn("between category", sp._composition_section(fx.main_result()))

    def test_it_marks_mixed_categories_and_lists_what_was_left_out(self):
        page = sp.render_html(fx.mixed_result())
        self.assertIn("mixed", page)
        self.assertIn("modes take turns", page)
        left_out = sp.render_html(self.left_out)
        self.assertIn("Left out, with no modes listed:", left_out)
        self.assertNotIn("Left out, with no modes listed:", page)

    def test_the_composition_section_shows_limits(self):
        page = sp.render_html(self.left_out)
        for phrase in ("at most 3 a day", "at most 4 a day", "at most 5 a week"):
            self.assertIn(phrase, page)

    def test_the_between_section_lists_races_and_pair_scores(self):
        self.assertIn("<h2>Between categories</h2>", self.page)
        section = sp._between_section(self.result)
        for proposal in self.result["proposals"]:
            races = next(r["between"] for r in proposal["categories"] if r["category"] == "mystery")
            self.assertIn(f'<td class="num">{races}</td>', section)
        for main, score in (("simple", "0.24"), ("entrance", "0.20 (weak evidence)"),
                            ("keys", "0.10")):
            self.assertIn(f"{main} {score}", section)
        self.assertIn("expected entrants, charged to the smaller race", section)
        self.assertIn("A missing score counts as 1.", section)
        self.assertNotIn("Allowed pair", self.page)

    def test_a_missing_pair_score_reads_n_a(self):
        result = fx.cached("between-nopairs", fx.BETWEEN_COMPOSITION, pairs={}, events=fx.EVENTS)
        section = sp._between_section(result)
        self.assertIn("simple n/a", section)
        self.assertIn("n/a", sp.format_report(result))

    def test_without_between_categories_the_section_says_so(self):
        main = fx.main_result()
        self.assertIn("No plan category is a between category: every race is a main race.",
                      sp.render_html(main))
        self.assertIn("No between categories: every race is a main race.", sp.format_report(main))
        self.assertTrue(all(p["between"] == 0 for p in main["proposals"]))
        self.assertNotIn('class="btw"', sp.render_html(main).replace(".btw", ""))

    def test_the_featured_table_marks_between_races(self):
        proposal = fx.proposal(self.result, "light")
        table = sp._featured_table(proposal)
        self.assertEqual(table.count("between race"), 1)
        self.assertIn('mystery<span class="badge">between race</span>', table)
        self.assertNotIn("between race", sp._featured_table(fx.proposal(fx.main_result(), "light")))

    def test_the_featured_table_has_both_hours_between_races_and_skipped_dates(self):
        proposal = fx.proposal(self.result, "light")
        table = sp._featured_table(proposal)
        heads = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", table.split("<tbody>")[0])
        self.assertEqual(heads, [
            "Category", "Weekday", "Weeks 1, 3, 5&hellip; (ET)", "Weeks 2, 4, 6&hellip; (ET)",
            "Mode", "Races", "Skipped dates",
        ])
        self.assertNotIn("Moved", table)
        rows = re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>")[1], re.S)
        self.assertEqual(len(rows), len(proposal["featured"]))
        for row, item in zip(rows, proposal["featured"]):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            self.assertEqual(len(cells), 7)
            self.assertEqual(cells[2], f"{item['hours'][0]:02d}:00")
            self.assertEqual(cells[3], f"{item['hours'][1]:02d}:00")
            self.assertEqual(abs(item["hours"][0] - item["hours"][1]), 1)
            self.assertEqual(cells[5], str(item["races"]))
            self.assertIn('<td class="num">' + str(item["races"]) + "</td>", row)
            self.assertEqual(len(re.findall(r'<td class="num"', row)), 1)

    def test_the_proposal_note_states_how_many_races_moved(self):
        proposal = fx.proposal(self.result, "light")
        section = sp._proposal_section(self.result, proposal, True)
        self.assertGreater(proposal["moved"], 0)
        self.assertIn(
            f"In weeks 2, 4, 6 and so on, {proposal['moved']:,} races moved an hour into the "
            "two-hour sections the week before left empty.", section)
        self.assertIn("and whether it moved.", section)
        bigger = dict(proposal, moved=1234)
        self.assertIn("1,234 races moved an hour", sp._proposal_section(self.result, bigger, True))
        self.assertIn("0 races moved an hour", sp._proposal_section(self.result, dict(proposal, moved=0), True))
        self.assertIn("races moved an hour", self.page)

    def test_the_categories_table_has_a_between_column(self):
        proposal = fx.proposal(self.result, "light")
        table = sp._categories_table(proposal)
        self.assertIn("<th class=\"num\">Between</th>", table)
        self.assertNotIn("Paired", table)
        self.assertIn(f'<td class="num">{proposal["between"]}</td>', table)

    def test_the_events_section_says_weekly_or_dated(self):
        self.assertIn("<h2>Events</h2>", self.page)
        self.assertIn("every week", self.page)
        self.assertIn("one date", self.page)
        for event in fx.EVENTS:
            self.assertIn(escape(event["name"]), self.page)
        quiet = sp.render_html(fx.cached("noevents"))
        self.assertIn("No external events in the plan.", quiet)

    def test_the_holiday_section_lists_the_holidays_in_the_period(self):
        self.assertIn("<h2>Holidays</h2>", self.page)
        self.assertIn("Mon 2026-09-07 count as Prime days.", self.page)
        self.assertIn("Labor Day", self.page)
        january = sp.render_html(fx.cached("no-holiday", start=fx.date(2026, 9, 8)))
        self.assertIn("No holidays fall in the period.", january)

    def test_a_notice_and_the_shortfall_are_shown(self):
        result = fx.cached("notice", fx.LIMIT_COMPOSITION, events=fx.EVENTS)
        for proposal in result["proposals"]:
            page = sp.render_html(result)
            if proposal["notice"]:
                self.assertIn(escape(proposal["notice"]), page)
            if proposal["shortfall"]:
                self.assertIn("Short of races on:", page)

    def test_the_method_note_describes_the_method(self):
        for phrase in ("Work (Monday to Wednesday)", "featured race", "favourite", "between category",
                       "short gap", "lookahead", "next date&rsquo;s best hours", "share",
                       "fewer than two", "less than 10%",
                       "1 or 2 hours after one main race", "bands are set by hand, not learned",
                       "Spoiler&rsquo;s overlap scores are weak evidence and high"):
            self.assertIn(phrase, self.page)

    def test_the_method_note_names_the_bands_of_each_group(self):
        note = sp._method_note(self.result)
        for group in sp.DAY_GROUPS:
            self.assertIn(sp._band_sentence(self.result, group), note)


class ReportTests(unittest.TestCase):
    def test_the_sets_are_ranked_by_expected_entrants_per_race_and_the_best_marked(self):
        result = fx.main_result()
        lines = sp.format_report(result).splitlines()
        start = lines.index("Ranked by expected entrants per race:")
        rows = lines[start + 2 : start + 2 + len(result["proposals"])]
        self.assertEqual(len(rows), 4)
        per_race = {p["set"]: p["expected_entrants"] / p["races"] for p in result["proposals"]}
        ranked = sorted(per_race, key=lambda name: -per_race[name])
        self.assertEqual([row.split()[0] for row in rows], ranked)
        self.assertTrue(rows[0].endswith("best"))
        self.assertFalse(any(row.endswith("best") for row in rows[1:]))
        for row in rows:
            proposal = fx.proposal(result, row.split()[0])
            a_day = "/".join(str(proposal["per_day"][g]) for g in sp.DAY_GROUPS)
            self.assertIn(a_day, row.split())
            self.assertIn(str(proposal["weekly_races"]), row.split())

    def test_the_report_lists_the_composition_and_what_was_left_out(self):
        text = sp.format_report(fx.limit_result())
        self.assertIn("Composition, the same in all 4 sets:", text)
        self.assertIn("keys [at most 5 a week]", text)
        self.assertIn("simple [at most 3 a day]", text)
        self.assertIn("favourite Ent B", text)
        self.assertIn("open in prime, normal, quiet", text)
        self.assertIn("weights prime", text)
        self.assertIn("Left out, with no modes listed: <b>fun</b>.", text)
        mixed = sp.format_report(fx.mixed_result())
        self.assertIn("its modes take turns", mixed)
        self.assertNotIn("Left out", mixed)

    def test_the_report_describes_each_set_featured_races_and_categories(self):
        text = sp.format_report(fx.main_result())
        for race_set in sp.SETS:
            self.assertIn(f"{race_set.name.capitalize()}: {race_set.description}", text)
        self.assertIn("Featured: ", text)
        self.assertIn("a week", text)
        self.assertIn("featured  between", text)
        self.assertIn("main races a week", text)
        self.assertIn("short gaps", text)
        self.assertIn("Bands by day group, set by hand in the plan file", text)
        self.assertIn("Work (Monday to Wednesday): prime 18:00 to 23:00 (6 hours)", text)
        self.assertNotIn("Full schedule", text)

    def test_the_report_lists_between_categories_and_their_pair_scores(self):
        text = sp.format_report(fx.between_result())
        self.assertIn("Between categories, whose races start only between two main races", text)
        self.assertIn("mystery + simple: 0.24", text)
        self.assertIn("mystery + entrance: 0.20 (weak evidence)", text)
        self.assertIn("charged to the smaller race", text)
        self.assertIn("[between category]", text)
        for each in fx.between_result()["proposals"]:
            entry = next(e for e in each["featured"] if e["between"])
            self.assertEqual(entry["category"], "mystery")
            self.assertIn(sp._featured_text(entry), text)
            self.assertRegex(sp._featured_text(entry), r"^mystery \w{3} \d\d:00/\d\d:00 .*between race")
        proposal = fx.proposal(fx.between_result(), "light")
        # The between races are counted among the races, not among the cancelled rooms.
        week = proposal["between"] / proposal["days"] * 7
        self.assertIn(
            f"{proposal['races']} races, {proposal['between']} of them between races "
            f"({week:.1f} a week; {proposal['weekly_races']} main races a week",
            text,
        )
        self.assertNotIn("cancelled rooms, " + str(proposal["between"]) + " of them", text)
        self.assertIn(f"{proposal['short_gaps']} short gaps", text)

    def test_the_report_counts_the_races_moved_an_hour_in_alternate_weeks(self):
        for result in (fx.main_result(), fx.between_result()):
            text = sp.format_report(result)
            for proposal in result["proposals"]:
                with self.subTest(set=proposal["set"]):
                    self.assertGreater(proposal["moved"], 0)
                    self.assertIn(
                        f"{proposal['event_conflicts']} event conflicts, "
                        f"{proposal['moved']} races moved an hour in alternate weeks.\n", text + "\n")
        text = sp.format_report(fx.main_result())
        self.assertEqual(text.count("races moved an hour in alternate weeks."), len(sp.SETS))

    def test_a_featured_entry_names_both_hours_and_no_moved_clause(self):
        item = {"category": "simple", "weekday": 1, "hours": [13, 14], "mode": "Simple Old",
                "rotates": [], "races": 5, "between": False, "skipped": []}
        self.assertEqual(sp._featured_text(item), "simple Tue 13:00/14:00 Simple Old (5 races)")
        self.assertEqual(
            sp._featured_text(dict(item, between=True)),
            "simple Tue 13:00/14:00 Simple Old (5 races, between race)")
        self.assertEqual(
            sp._featured_text(dict(item, skipped=["2026-09-14"])),
            "simple Tue 13:00/14:00 Simple Old (5 races, skipped 2026-09-14)")
        both = dict(item, between=True, skipped=["2026-09-08"])
        self.assertEqual(
            sp._featured_text(both),
            "simple Tue 13:00/14:00 Simple Old (5 races, between race, skipped 2026-09-08)")
        for proposal in fx.main_result()["proposals"]:
            for entry in proposal["featured"]:
                self.assertNotIn("moved", entry)
                text = sp._featured_text(entry)
                self.assertIn(f"{entry['hours'][0]:02d}:00/{entry['hours'][1]:02d}:00", text)
                self.assertNotIn("moved", text)

    def test_the_report_names_the_events_and_the_holidays(self):
        text = sp.format_report(fx.main_result())
        self.assertIn("Event Weekly Entrance: avoid entrance, weekly, 4 in the period.", text)
        self.assertIn("Event Tournament: block, dated, 1 in the period.", text)
        self.assertIn("2026-09-07", text)

    def test_the_helpers(self):
        self.assertEqual(sp._hour_ranges([5, 0, 1, 2, 23]), "00:00 to 02:00, 05:00, 23:00")
        many = [f"2026-09-{day:02d}" for day in range(1, 13)]
        self.assertEqual(sp._dates(many).count(","), sp._LISTED_DATES - 1)
        self.assertTrue(sp._dates(many).endswith("and 4 more"))


class HoursTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fx.main_result()

    @staticmethod
    def rows(table):
        out = {}
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.split("<tbody>")[1], re.S):
            cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
            out[cells[0][1]] = cells[1:]
        return out

    def test_each_category_row_counts_the_schedule_by_hour(self):
        for proposal in self.result["proposals"]:
            with self.subTest(set=proposal["set"]):
                table = sp._hours_table(proposal)
                self.assertTrue(table.startswith('<div class="hours"><div class="wrap"><table>'))
                heads = re.findall(r"<th[^>]*>([^<]*)</th>", table.split("<tbody>")[0])
                self.assertEqual(heads, ["Category", *map(str, range(24)), "Total"])
                rows = self.rows(table)
                self.assertEqual(list(rows), [c["category"] for c in proposal["categories"]] + ["All"])
                every = [0] * 24
                for category in proposal["categories"]:
                    counts = [0] * 24
                    for race in proposal["schedule"]:
                        if race["category"] == category["category"]:
                            counts[race["hour"]] += 1
                    every = [a + b for a, b in zip(every, counts)]
                    cells = rows[category["category"]]
                    self.assertEqual(len(cells), 25)
                    self.assertEqual(
                        [text for _, text in cells[:24]],
                        [str(n) if n else "" for n in counts])
                    self.assertEqual(cells[24][1], str(category["races"]))
                    self.assertEqual(sum(counts), category["races"])
                    self.assertEqual(category["by_hour"], counts)
                    self.assertEqual(sum(category["by_hour"]), category["races"])
                total = rows["All"]
                self.assertEqual([t for _, t in total[:24]], [str(n) if n else "" for n in every])
                self.assertEqual(total[24][1], str(proposal["races"]))
                self.assertIn('<tr class="all">', table)

    def test_cells_are_shaded_by_count_with_the_largest_at_the_maximum(self):
        for proposal in self.result["proposals"]:
            with self.subTest(set=proposal["set"]):
                table = sp._hours_table(proposal)
                rows = self.rows(table)
                peak = max(n for c in proposal["categories"] for n in c["by_hour"])
                shade = "background:color-mix(in srgb, var(--seq) {}%, var(--surface))"
                self.assertIn(shade.format(35), table)
                for category in proposal["categories"]:
                    for hour, count in enumerate(category["by_hour"]):
                        attrs, text = rows[category["category"]][hour]
                        if not count:
                            self.assertEqual((attrs, text), (' class="num"', ""))
                            continue
                        self.assertIn(shade.format(round(35 * count / peak)), attrs)
                        self.assertIn(
                            f'title="{category["category"]}, {hour:02d}:00 ET: {count} races"',
                            attrs)
                for attrs, text in rows["All"]:
                    self.assertNotIn("style=", attrs)
                for cells in rows.values():
                    self.assertNotIn("style=", cells[24][0])

    def test_the_page_has_one_races_by_hour_section_in_each_proposal(self):
        page = sp.render_html(self.result)
        self.assertEqual(page.count("<h3>Races by hour</h3>"), len(sp.SETS))
        sections = re.findall(r"<details.*?</details>", page, re.S)
        self.assertEqual(len(sections), len(self.result["proposals"]))
        for section, proposal in zip(sections, self.result["proposals"]):
            self.assertEqual(section.count("<h3>Races by hour</h3>"), 1)
            self.assertIn(sp._hours_table(proposal), section)
            self.assertGreater(
                section.index("<h3>Races by hour</h3>"), section.index("<h3>Categories</h3>"))

    def test_each_proposal_explains_the_alternating_featured_hours(self):
        page = sp.render_html(self.result)
        sections = re.findall(r"<details.*?</details>", page, re.S)
        self.assertEqual(len(sections), len(sp.SETS))
        for section in sections:
            self.assertIn(
                "Each featured race alternates week by week between two adjacent hours: the "
                "first in weeks 1, 3, 5 and so on, the second in weeks 2, 4, 6.", section)
            self.assertIn(
                "Featured races never move: they alternate between their two hours instead.",
                section)


class EveryHourReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.marked = fx.every_hour_result()
        cls.plain = fx.main_result()

    @staticmethod
    def with_gap(proposal, category, hour):
        """Return a copy of the proposal with no race of the category at the hour, listed
        as uncovered."""
        categories = [
            {**row, "by_hour": [0 if h == hour else n for h, n in enumerate(row["by_hour"])]}
            if row["category"] == category else row
            for row in proposal["categories"]
        ]
        return {**proposal, "categories": categories, "uncovered": {category: [hour]}}

    def test_limits_name_a_category_marked_every_hour_only(self):
        for category in self.marked["proposals"][0]["composition"]:
            self.assertEqual(
                "every hour" in sp._limits(category),
                category["category"] in ("simple", "entrance"), category["category"])
        for category in self.plain["proposals"][0]["composition"]:
            self.assertFalse(category["every_hour"])
            self.assertNotIn("every hour", sp._limits(category))
        self.assertEqual(sp._limits(
            {"daily_max": 2, "weekly_max": None, "only_prime": True, "every_hour": True}),
            ["at most 2 a day", "prime time only", "every hour"])

    def test_an_uncovered_hour_shows_a_zero_with_the_gap_class_and_title(self):
        proposal = self.marked["proposals"][0]
        hour = 3
        edited = self.with_gap(proposal, "simple", hour)
        rows = HoursTableTests.rows(sp._hours_table(edited))
        plain_rows = HoursTableTests.rows(sp._hours_table(
            {**edited, "uncovered": {}}))
        attrs, text = rows["simple"][hour]
        self.assertEqual(text, "0")
        self.assertEqual(
            attrs, f' class="num gap" title="simple: no race at {hour:02d}:00 ET"')
        self.assertNotIn("style=", attrs)
        # Nothing else changes: other cells, the Total column and the All row.
        self.assertEqual(plain_rows["simple"][hour], (' class="num"', ""))
        self.assertEqual(rows["All"][hour][1], plain_rows["All"][hour][1])
        for name, cells in rows.items():
            for index, cell in enumerate(cells):
                if (name, index) != ("simple", hour):
                    self.assertEqual(cell, plain_rows[name][index], (name, index))

    def test_other_zero_cells_stay_blank(self):
        proposal = self.marked["proposals"][0]
        hour = 3
        edited = self.with_gap(proposal, "simple", hour)
        rows = HoursTableTests.rows(sp._hours_table(edited))
        for category in edited["categories"]:
            for other, count in enumerate(category["by_hour"]):
                if not count and (category["category"], other) != ("simple", hour):
                    self.assertEqual(
                        rows[category["category"]][other], (' class="num"', ""),
                        (category["category"], other))
        # The same hour of an unmarked category is not a gap.
        other_name = next(
            c["category"] for c in edited["categories"]
            if c["category"] != "simple" and not c["by_hour"][hour])
        self.assertEqual(rows[other_name][hour], (' class="num"', ""))
        self.assertNotIn("gap", rows["All"][hour][0])

    def test_a_fully_covered_proposal_has_no_gap_cell(self):
        for proposal in self.marked["proposals"]:
            self.assertEqual(proposal["uncovered"], {"simple": [], "entrance": []})
            self.assertNotIn('class="num gap"', sp._hours_table(proposal))

    def test_the_gap_style_is_bold_and_coloured(self):
        self.assertRegex(sp._CSS, r"\.hours td\.gap \{[^}]*font-weight: 700;[^}]*var\(--high\)")

    def test_the_hours_note_explains_a_zero_only_when_something_is_uncovered(self):
        sentence = "A 0 marks an hour a category marked every hour has no race at."
        result = {**self.marked, "proposals": [
            self.with_gap(self.marked["proposals"][0], "simple", 3),
            *self.marked["proposals"][1:]]}
        sections = re.findall(r"<details.*?</details>", sp.render_html(result), re.S)
        self.assertIn(sentence, sections[0])
        for section in sections[1:]:
            self.assertNotIn(sentence, section)
        for section in re.findall(r"<details.*?</details>", sp.render_html(self.plain), re.S):
            self.assertNotIn(sentence, section)

    def test_the_uncovered_notice_reaches_the_html(self):
        text = "Every-hour categories lack races at 2 hours: entrance 04:00; keys 22:00."
        proposal = {**self.marked["proposals"][0], "notice": text}
        section = sp._proposal_section(self.marked, proposal, True)
        self.assertIn(f'<p class="notice"><strong>Notice.</strong> {escape(text)}</p>', section)

    def test_the_method_note_names_the_draw_and_the_seed(self):
        result = {**self.marked, "parameters": {**self.marked["parameters"], "seed": 7}}
        note = sp._method_note(result)
        for phrase in ("seed 7", "draws one", "expected turnout", "every hour",
                       "another <code>--seed</code> draws another season",
                       "has no race yet at an hour takes it first"):
            self.assertIn(phrase, note)
        self.assertNotIn("seed 1 ", note)

    def test_the_text_report_has_the_seed_line_and_the_every_hour_limit(self):
        result = {**self.marked, "parameters": {**self.marked["parameters"], "seed": 7}}
        lines = sp.format_report(result).splitlines()
        self.assertIn("Hours drawn with seed 7; --seed draws another season.", lines)
        learned = next(i for i, line in enumerate(lines) if line.startswith("Learned from"))
        self.assertTrue(lines[learned + 1].startswith("Hours drawn with seed"))
        for name in ("simple", "entrance"):
            line = next(l for l in lines if l.startswith(f"  {name}"))
            self.assertIn("every hour", line.split(";")[0])
        keys = next(l for l in lines if l.startswith("  keys"))
        self.assertNotIn("every hour", keys)


class ArgumentTests(unittest.TestCase):
    def parse(self, *argv):
        with mock.patch("sys.argv", ["schedule_planner.py", *argv]):
            return sp.parse_args()

    def test_the_options(self):
        args = self.parse("--weeks", "3", "--start", "2026-09-01", "--out", "x")
        self.assertEqual((args.weeks, args.start.isoformat(), args.out), (3, "2026-09-01", Path("x")))
        self.assertEqual((self.parse().weeks, self.parse().start), (None, None))
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            self.parse("--weeks", "0")


if __name__ == "__main__":
    unittest.main()
