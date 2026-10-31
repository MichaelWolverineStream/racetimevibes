import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts import build_stats, race_archive


DEFAULT_ENTRANTS = object()


def race(
    name,
    *,
    started_at="2026-09-17T20:00:00.000Z",
    ended_at="2026-09-17T22:00:00.000Z",
    cancelled_at=None,
    entrants_count=4,
    entrants=DEFAULT_ENTRANTS,
    info="Step Ladder Series - [simple] - Open",
    status="finished",
):
    if entrants is DEFAULT_ENTRANTS:
        count = (
            entrants_count
            if isinstance(entrants_count, int)
            and not isinstance(entrants_count, bool)
            and entrants_count >= 0
            else 0
        )
        entrants = [
            {
                "user": {
                    "id": f"{name}-player-{index}",
                    "name": f"Player {index}",
                }
            }
            for index in range(count)
        ]
    return {
        "name": name,
        "status": {"value": status},
        "started_at": started_at,
        "ended_at": ended_at,
        "cancelled_at": cancelled_at,
        "entrants_count": entrants_count,
        "entrants": entrants,
        "info": info,
    }


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.generated_at = datetime(2026, 9, 17, 12, tzinfo=UTC)

    def test_rejects_dataset_with_no_included_races(self):
        with self.assertRaisesRegex(build_stats.DataError, "No races remained"):
            build_stats.aggregate_races(
                [race("cancelled", status="cancelled")],
                generated_at=self.generated_at,
            )

    def test_extracts_only_leading_normalized_mode(self):
        self.assertEqual(
            build_stats.extract_mode(
                "Step Ladder Series - [ Keys ] - HMG [VT]\nother details"
            ),
            "keys",
        )
        self.assertEqual(build_stats.extract_mode("Different prefix [keys]"), "unknown")

    def test_filters_and_aggregates_finished_races_in_eastern_time(self):
        races = [
            race(
                "keys-late",
                started_at="2026-09-17T02:30:00Z",
                entrants_count=4,
                info="Step Ladder Series - [ Keys ] - HMG [VT]",
            ),
            race(
                "keys-early",
                started_at="2026-09-17T02:45:00+00:00",
                entrants_count=8,
                info="Step Ladder Series - [keys] - Cabookey",
            ),
            race(
                "entrance",
                started_at="2026-09-17T04:00:00Z",
                entrants_count=3,
                info="Step Ladder Series - [entrance] - Crosskeys",
            ),
            race(
                "unknown",
                started_at="2026-09-17T05:00:00Z",
                entrants_count=6,
                info="No bracketed mode here",
            ),
            race("cancelled", status="cancelled"),
            race("missing-start", started_at=None),
            race("bad-count", entrants_count=True),
            race("bad-info", info=None),
        ]

        stats = build_stats.aggregate_races(
            races,
            generated_at=self.generated_at,
        )

        self.assertEqual(len(stats["hourly"]), 24)
        self.assertEqual([item["hour"] for item in stats["hourly"]], list(range(24)))
        by_hour = {item["hour"]: item for item in stats["hourly"]}
        self.assertEqual(by_hour[22]["race_count"], 2)
        self.assertEqual(by_hour[22]["entrant_total"], 12)
        self.assertEqual(by_hour[22]["average_racers"], 6)
        self.assertEqual(by_hour[22]["median_racers"], 6)
        self.assertEqual(by_hour[22]["q1_racers"], 4)
        self.assertEqual(by_hour[22]["q3_racers"], 8)
        self.assertAlmostEqual(by_hour[22]["standard_deviation"], 2.8284271247461903)
        self.assertEqual(by_hour[0]["average_racers"], 3)
        self.assertEqual(by_hour[1]["average_racers"], 6)
        self.assertIsNone(by_hour[2]["average_racers"])

        by_weekday = {item["label"]: item for item in stats["weekdays"]}
        self.assertEqual(by_weekday["Wednesday"]["race_count"], 2)
        self.assertEqual(by_weekday["Thursday"]["race_count"], 2)
        wednesday_22 = next(
            item
            for item in stats["weekday_hourly"]
            if item["weekday_label"] == "Wednesday" and item["hour"] == 22
        )
        self.assertEqual(wednesday_22["average_racers"], 6)
        self.assertEqual(wednesday_22["adjusted_average"], 0)

        by_mode = {item["mode"]: item for item in stats["modes"]}
        self.assertEqual(by_mode["keys"]["race_count"], 2)
        self.assertEqual(by_mode["keys"]["average_racers"], 6)
        self.assertEqual(by_mode["unknown"]["race_count"], 1)
        self.assertEqual(stats["schema_version"], 8)
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["excluded_archived_race_count"], 4)
        self.assertEqual(stats["diagnostics"]["excluded_by_status"], {"cancelled": 1})
        self.assertEqual(
            stats["diagnostics"]["invalid_records"],
            {
                "invalid_entrants_count": 1,
                "invalid_info": 1,
                "invalid_started_at": 1,
            },
        )
        self.assertEqual(stats["diagnostics"]["unknown_mode_count"], 1)
        self.assertEqual(stats["generated_at"], "2026-09-17T12:00:00Z")
        self.assertEqual(stats["timezone"]["name"], "America/New_York")

    def test_uses_eastern_standard_time_in_winter(self):
        stats = build_stats.aggregate_races(
            [race("winter", started_at="2026-01-15T05:00:00Z")],
            generated_at=self.generated_at,
        )

        by_hour = {item["hour"]: item for item in stats["hourly"]}
        self.assertEqual(by_hour[0]["race_count"], 1)
        self.assertEqual(by_hour[1]["race_count"], 0)

    def test_adjusts_weekday_hours_for_mode_and_period_mix(self):
        stats = build_stats.aggregate_races(
            [
                race("simple-13", started_at="2026-01-05T18:00:00Z", entrants_count=10),
                race("simple-14", started_at="2026-01-05T19:00:00Z", entrants_count=2),
                race(
                    "entrance-13",
                    started_at="2026-01-12T18:00:00Z",
                    entrants_count=20,
                    info="Step Ladder Series - [entrance]",
                ),
                race(
                    "entrance-14",
                    started_at="2026-01-12T19:00:00Z",
                    entrants_count=12,
                    info="Step Ladder Series - [entrance]",
                ),
            ],
            generated_at=self.generated_at,
        )

        monday = {
            item["hour"]: item
            for item in stats["weekday_hourly"]
            if item["weekday_label"] == "Monday"
        }
        self.assertEqual(monday[13]["average_racers"], 15)
        self.assertEqual(monday[13]["adjusted_average"], 4)
        self.assertEqual(monday[14]["average_racers"], 7)
        self.assertEqual(monday[14]["adjusted_average"], -4)

    def test_sorts_modes_by_average_then_name(self):
        stats = build_stats.aggregate_races(
            [
                race("simple", entrants_count=3),
                race("keys", entrants_count=8, info="Step Ladder Series - [keys]"),
                race("entrance", entrants_count=8, info="Step Ladder Series - [entrance]"),
            ],
            generated_at=self.generated_at,
        )

        self.assertEqual(
            [item["mode"] for item in stats["modes"]],
            ["entrance", "keys", "simple"],
        )

    def test_includes_2025_races_excludes_ladder_tests_and_groups_quarters(self):
        stats = build_stats.aggregate_races(
            [
                race(
                    "archive-start",
                    started_at="2025-04-20T16:00:00Z",
                    entrants_count=4,
                ),
                race(
                    "late-2025",
                    started_at="2025-12-31T20:00:00Z",
                    entrants_count=5,
                ),
                race(
                    "early-2026",
                    started_at="2026-01-01T05:00:00Z",
                    entrants_count=6,
                    info="Step Ladder Series - [keys]",
                ),
                race(
                    "mid-2026",
                    started_at="2026-07-01T04:00:00Z",
                    entrants_count=8,
                    info="Step Ladder Series - [entrance]",
                ),
                race(
                    "underscore-test",
                    entrants_count=100,
                    info="Step Ladder Series - [ladder_test]",
                ),
                race(
                    "space-test",
                    entrants_count=100,
                    info="Step Ladder Series - [ladder test]",
                ),
            ],
            generated_at=self.generated_at,
        )

        self.assertEqual(stats["schema_version"], 8)
        self.assertEqual(stats["source"]["data_start_local"], "2025-04-20")
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["included_entrant_count"], 23)
        self.assertEqual(stats["source"]["excluded_archived_race_count"], 2)
        self.assertNotIn("ladder_test", {item["mode"] for item in stats["modes"]})
        self.assertNotIn("excluded_before_data_start", stats["diagnostics"])
        self.assertEqual(
            stats["diagnostics"]["excluded_by_mode"],
            {"ladder test": 1, "ladder_test": 1},
        )

        periods = {item["id"]: item for item in stats["periods"]}
        self.assertEqual(
            list(periods), ["2025-Q2", "2025-Q4", "2026-Q1", "2026-Q3"]
        )
        self.assertEqual(periods["2025-Q2"]["label"], "Q2 2025")
        self.assertEqual(
            {period_id: item["completeness"] for period_id, item in periods.items()},
            {
                "2025-Q2": "partial",
                "2025-Q4": "complete",
                "2026-Q1": "complete",
                "2026-Q3": "partial",
            },
        )
        self.assertEqual(periods["2025-Q4"]["entrant_total"], 5)
        self.assertEqual(periods["2026-Q1"]["entrant_total"], 6)
        self.assertEqual(
            sum(item["race_count"] for item in stats["periods"]),
            stats["source"]["included_race_count"],
        )
        self.assertEqual(
            sum(item["entrant_total"] for item in stats["periods"]),
            stats["source"]["included_entrant_count"],
        )

    def test_quarter_period_spans_calendar_quarters(self):
        expected = {
            2: ("2026-Q1", "Q1 2026", "2026-01-01", "2026-03-31"),
            4: ("2026-Q2", "Q2 2026", "2026-04-01", "2026-06-30"),
            9: ("2026-Q3", "Q3 2026", "2026-07-01", "2026-09-30"),
            12: ("2026-Q4", "Q4 2026", "2026-10-01", "2026-12-31"),
        }
        for month, (period_id, label, start, end) in expected.items():
            with self.subTest(month=month):
                period = build_stats.quarter_period(
                    datetime(2026, month, 15, tzinfo=build_stats.SCHEDULE_TIMEZONE)
                )
                self.assertEqual(
                    period,
                    {"id": period_id, "label": label, "start": start, "end": end},
                )

    def test_month_period_spans_calendar_months(self):
        expected = {
            (2024, 2): ("2024-02", "Feb 2024", "2024-02-01", "2024-02-29", "2024-Q1"),
            (2026, 4): ("2026-04", "Apr 2026", "2026-04-01", "2026-04-30", "2026-Q2"),
            (2026, 12): ("2026-12", "Dec 2026", "2026-12-01", "2026-12-31", "2026-Q4"),
        }
        for (year, month), (month_id, label, start, end, quarter_id) in expected.items():
            with self.subTest(year=year, month=month):
                period = build_stats.month_period(
                    datetime(year, month, 15, tzinfo=build_stats.SCHEDULE_TIMEZONE)
                )
                self.assertEqual(
                    period,
                    {
                        "id": month_id,
                        "label": label,
                        "start": start,
                        "end": end,
                        "quarter_id": quarter_id,
                    },
                )

    def test_monthly_turnout_covers_every_archive_month(self):
        stats = build_stats.aggregate_races(
            [
                race("april", started_at="2025-04-20T16:00:00Z", entrants_count=4),
                race("december", started_at="2025-12-31T20:00:00Z", entrants_count=5),
                race("july", started_at="2026-07-01T04:00:00Z", entrants_count=8),
            ],
            generated_at=self.generated_at,
        )

        monthly = {item["id"]: item for item in stats["monthly"]}
        expected_ids = [f"2025-{month:02d}" for month in range(4, 13)] + [
            f"2026-{month:02d}" for month in range(1, 8)
        ]
        self.assertEqual(list(monthly), expected_ids)

        self.assertEqual(monthly["2025-04"]["covered_day_count"], 11)
        self.assertFalse(monthly["2025-04"]["is_complete"])
        self.assertAlmostEqual(monthly["2025-04"]["entries_per_day"], 4 / 11)

        self.assertEqual(monthly["2025-05"]["race_count"], 0)
        self.assertIsNone(monthly["2025-05"]["average_racers"])
        self.assertEqual(monthly["2025-05"]["entries_per_day"], 0)

        self.assertTrue(monthly["2025-12"]["is_complete"])
        self.assertEqual(monthly["2025-12"]["covered_day_count"], 31)
        self.assertAlmostEqual(monthly["2025-12"]["entries_per_day"], 5 / 31)

        self.assertEqual(monthly["2026-07"]["covered_day_count"], 1)
        self.assertFalse(monthly["2026-07"]["is_complete"])
        self.assertEqual(monthly["2026-07"]["entries_per_day"], 8)

        self.assertEqual(
            sum(item["race_count"] for item in stats["monthly"]),
            stats["source"]["included_race_count"],
        )
        self.assertEqual(
            sum(item["entrant_total"] for item in stats["monthly"]),
            stats["source"]["included_entrant_count"],
        )

    def test_aggregates_unique_players_without_serializing_identities(self):
        private_id = "private-player-id"
        private_name = "Private Player"
        stats = build_stats.aggregate_races(
            [
                race(
                    "first",
                    entrants_count=3,
                    entrants=[
                        {"user": {"id": private_id, "name": private_name}},
                        {"user": {"id": "one-race", "name": "Once"}},
                        {"user": None},
                    ],
                ),
                race(
                    "second",
                    entrants_count=2,
                    entrants=[
                        {"user": {"id": private_id, "name": private_name}},
                        {"user": {"id": "other", "name": "Other"}},
                    ],
                ),
            ],
            generated_at=self.generated_at,
        )

        players = stats["unique_players"]
        self.assertEqual(players["identified_player_count"], 3)
        self.assertEqual(players["identified_entry_count"], 4)
        self.assertEqual(players["unidentified_entry_count"], 1)
        self.assertEqual(players["identity_coverage"], 0.8)
        self.assertEqual(players["repeat_player_count"], 1)
        self.assertEqual(
            sum(item["player_count"] for item in players["cohorts"]), 3
        )
        self.assertEqual(
            sum(item["entry_count"] for item in players["cohorts"]), 4
        )
        serialized = json.dumps(stats)
        self.assertNotIn(private_id, serialized)
        self.assertNotIn(private_name, serialized)
        self.assertNotIn('"entrants"', serialized)

    def test_unique_player_summary_covers_all_cohorts(self):
        records = []
        for race_number in range(21):
            entrant_ids = ["core"]
            if race_number < 6:
                entrant_ids.append("regular")
            if race_number < 2:
                entrant_ids.append("occasional")
            if race_number == 0:
                entrant_ids.append("one-race")
            records.append(
                {
                    "entrant_ids": tuple(entrant_ids),
                    "unidentified_entry_count": 0,
                    "entrants_count": len(entrant_ids),
                }
            )

        summary = build_stats._unique_player_summary(records)
        cohorts = {item["id"]: item for item in summary["cohorts"]}
        self.assertEqual(
            {cohort_id: item["player_count"] for cohort_id, item in cohorts.items()},
            {"one_race": 1, "occasional": 1, "regular": 1, "core": 1},
        )
        self.assertEqual(
            {cohort_id: item["entry_count"] for cohort_id, item in cohorts.items()},
            {"one_race": 1, "occasional": 2, "regular": 6, "core": 21},
        )

    def test_rejects_incomplete_or_duplicate_entrant_data(self):
        with self.assertRaisesRegex(build_stats.DataError, "reports 2 entrants"):
            build_stats.aggregate_races(
                [race("partial", entrants_count=2, entrants=[])],
                generated_at=self.generated_at,
            )

        duplicate = {"user": {"id": "same-player"}}
        with self.assertRaisesRegex(build_stats.DataError, "duplicate entrant"):
            build_stats.aggregate_races(
                [race("duplicate", entrants_count=2, entrants=[duplicate, duplicate])],
                generated_at=self.generated_at,
            )


class ExtractPresetTests(unittest.TestCase):
    def test_extracts_and_normalizes_presets(self):
        cases = {
            "Step Ladder Series - [entrance] - Crosskeys 2024\nladder/crosskeys2024": "Crosskeys 2024",
            "Step Ladder Series - [keys] - HMG [DR]": "HMG (DR)",
            "Step Ladder Series - [keys] - HMG (DR)": "HMG (DR)",
            "Step Ladder Series - [keys] -   HMG   [VT]  ": "HMG (VT)",
            "Step Ladder Series - [simple] - Grab Bag (Open 7/7 Enemizer (Starting Maps & Compasses))": "Grab Bag",
            "Step Ladder Series - [simple] - Grab Bag (Open 7/7) https://alttpr.com/h/abc": "Grab Bag",
            "Step Ladder Series - [simple] - Open https://alttpr.com/h/abc": "Open",
            "Step Ladder Series - [entrance] - Crosskeys - https://avianart.games/perm/qsX (Pendant/Cape)": "Crosskeys",
            "Step Ladder Series - [entrance] - XD Hunt  jem041/xkeydrophunt - https://avianart.games/perm/q - (Rod / Lamp)": "XD Hunt",
            "Step Ladder Series - [fun] - Open 7/7 Swordless": "Open 7/7 Swordless",
            "Step Ladder Series - [simple] - Open 7/7 - https://alttpr.com/h/abc - (Bow/Lamp)": "Open 7/7",
        }
        for info, expected in cases.items():
            with self.subTest(info=info):
                self.assertEqual(build_stats.extract_preset(info), expected)

    def test_returns_none_without_a_preset(self):
        for info in (
            "Casual race with friends",
            "Step Ladder Series - [simple]",
            "Step Ladder Series - [simple] -   \nnext line",
            "Step Ladder Series - [simple] - https://alttpr.com/h/abc",
            "",
        ):
            with self.subTest(info=info):
                self.assertIsNone(build_stats.extract_preset(info))

    def test_classify_race_includes_the_preset(self):
        context, _ = build_stats.classify_race(
            race("preset", info="Step Ladder Series - [keys] - HMG [DR]")
        )
        self.assertEqual(context["preset"], "HMG (DR)")


class BuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.archive_dir = self.directory / "archive"
        self.output_paths = (
            self.directory / "race-stats.json",
            self.directory / "participants.json",
            self.directory / "comments.json",
        )

    def archive(self, race_record):
        race_archive.write_json_atomic(
            {
                "schema_version": 1,
                "fetched_at": "2026-09-18T00:00:00Z",
                "race": race_record,
            },
            race_archive.race_path(self.archive_dir, race_record["name"]),
        )

    def test_builds_all_datasets_from_the_archive(self):
        entrants = [
            {"user": {"id": "player-a", "name": "A"}, "comment": "  gg  "},
            {"user": {"id": "player-b", "name": "B"}, "comment": "   "},
        ]
        self.archive(
            race(
                "alttpr-ladder/kept-race-0001",
                entrants_count=2,
                entrants=entrants,
            )
        )
        self.archive(
            race(
                "alttpr-ladder/test-race-0002",
                info="Step Ladder Series - [ladder_test]",
            )
        )
        race_archive.write_json_atomic(
            {"player-a": "Renamed"},
            self.archive_dir / race_archive.RACER_NAMES_FILE,
        )

        stats, participants, comments = build_stats.build(
            self.archive_dir, *self.output_paths
        )

        source = stats["source"]
        self.assertEqual(source["archived_race_count"], 2)
        self.assertEqual(source["included_race_count"], 1)
        self.assertEqual(source["excluded_archived_race_count"], 1)
        self.assertEqual(stats["diagnostics"]["excluded_by_mode"], {"ladder_test": 1})
        self.assertEqual(
            comments["races"], {"alttpr-ladder/kept-race-0001": [["player-a", "gg", None]]}
        )
        self.assertEqual(
            json.loads(self.output_paths[2].read_text(encoding="utf-8")), comments
        )
        self.assertEqual(
            json.loads(self.output_paths[0].read_text(encoding="utf-8")), stats
        )
        self.assertEqual(
            json.loads(self.output_paths[1].read_text(encoding="utf-8")), participants
        )
        self.assertEqual(
            sorted(player["name"] for player in participants["players"]),
            ["B", "Renamed"],
        )

    def test_empty_archive_fails_with_instructions(self):
        with self.assertRaisesRegex(
            build_stats.DataError, "run scripts/race_archive.py first"
        ):
            build_stats.build(self.archive_dir, *self.output_paths)
        self.assertFalse(self.output_paths[0].exists())


class RoomCancellationEstimateTests(unittest.TestCase):
    @staticmethod
    def hourly(counts):
        return [
            {"hour": hour, "race_count": count}
            for hour, count in enumerate(counts)
        ]

    def test_uses_top_six_median_and_clamped_deficits(self):
        estimate = build_stats._room_cancellation_estimate(
            self.hourly([10, 9, 8, 7, 6, 5] + [0] * 18)
        )

        self.assertEqual(estimate["baseline_rooms_per_hour"], 7.5)
        self.assertEqual(estimate["estimated_cancelled_rooms"], 140)
        self.assertEqual(estimate["observed_finished_rooms"], 45)
        self.assertEqual(estimate["estimated_opened_rooms"], 185)
        self.assertEqual(estimate["estimated_cancelled_share"], 140 / 185)
        self.assertLessEqual(
            estimate["sensitivity"]["lower_estimated_cancelled_rooms"],
            estimate["estimated_cancelled_rooms"],
        )
        self.assertLessEqual(
            estimate["estimated_cancelled_rooms"],
            estimate["sensitivity"]["upper_estimated_cancelled_rooms"],
        )

    def test_equal_hours_infer_no_cancelled_rooms(self):
        estimate = build_stats._room_cancellation_estimate(self.hourly([4] * 24))

        self.assertEqual(estimate["estimated_cancelled_rooms"], 0)
        self.assertEqual(estimate["estimated_opened_rooms"], 96)
        self.assertEqual(estimate["estimated_cancelled_share"], 0)

    def test_sparse_hours_remain_nonnegative(self):
        estimate = build_stats._room_cancellation_estimate(
            self.hourly([3, 2, 1] + [0] * 21)
        )

        self.assertEqual(estimate["baseline_rooms_per_hour"], 0.5)
        self.assertEqual(estimate["estimated_cancelled_rooms"], 11)
        self.assertGreaterEqual(
            estimate["sensitivity"]["lower_estimated_cancelled_rooms"], 0
        )


class GeneratedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_path = (
            Path(__file__).resolve().parents[1]
            / "site"
            / "data"
            / "race-stats.json"
        )
        cls.serialized = cls.data_path.read_text(encoding="utf-8")
        cls.data = json.loads(cls.serialized)

    def test_generated_schema_reconciles(self):
        data = self.data
        source = data["source"]
        self.assertEqual(data["schema_version"], 8)
        self.assertRegex(source["data_start_local"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(
            source["archived_race_count"],
            source["included_race_count"] + source["excluded_archived_race_count"],
        )
        self.assertTrue(
            all(period["end"] >= source["data_start_local"] for period in data["periods"])
        )
        self.assertEqual(
            sum(period["race_count"] for period in data["periods"]),
            source["included_race_count"],
        )
        self.assertEqual(
            sum(period["entrant_total"] for period in data["periods"]),
            source["included_entrant_count"],
        )
        self.assertEqual(
            sum(month["race_count"] for month in data["monthly"]),
            source["included_race_count"],
        )
        self.assertEqual(
            sum(month["entrant_total"] for month in data["monthly"]),
            source["included_entrant_count"],
        )
        month_numbers = [
            int(month["id"][:4]) * 12 + int(month["id"][5:]) - 1
            for month in data["monthly"]
        ]
        self.assertEqual(
            month_numbers, list(range(month_numbers[0], month_numbers[-1] + 1))
        )

        for aggregate in [data, *data["periods"]]:
            players = aggregate["unique_players"]
            estimate = aggregate["room_cancellation_estimate"]
            self.assertEqual(
                players["identified_entry_count"]
                + players["unidentified_entry_count"],
                aggregate.get("entrant_total", source["included_entrant_count"]),
            )
            self.assertEqual(
                sum(cohort["player_count"] for cohort in players["cohorts"]),
                players["identified_player_count"],
            )
            self.assertEqual(
                sum(cohort["entry_count"] for cohort in players["cohorts"]),
                players["identified_entry_count"],
            )
            self.assertEqual(
                estimate["observed_finished_rooms"],
                sum(item["race_count"] for item in aggregate["hourly"]),
            )
            self.assertEqual(
                estimate["estimated_opened_rooms"],
                estimate["observed_finished_rooms"]
                + estimate["estimated_cancelled_rooms"],
            )

    def test_generated_schema_contains_no_participant_details(self):
        forbidden_keys = {
            "entrants",
            "user",
            "full_name",
            "twitch_name",
            "twitch_channel",
            "profile",
            "avatar",
            "pronouns",
        }

        def visit(value):
            if isinstance(value, dict):
                self.assertTrue(forbidden_keys.isdisjoint(value))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.data)

    def test_matches_a_rebuild_from_the_archive(self):
        committed_paths = (
            build_stats.OUTPUT_PATH,
            build_stats.PARTICIPANTS_OUTPUT_PATH,
            build_stats.COMMENTS_OUTPUT_PATH,
        )
        with tempfile.TemporaryDirectory() as directory:
            rebuilt_paths = tuple(Path(directory) / path.name for path in committed_paths)
            build_stats.build(race_archive.ARCHIVE_DIR, *rebuilt_paths)
            for committed_path, rebuilt_path in zip(committed_paths, rebuilt_paths):
                committed = json.loads(committed_path.read_text(encoding="utf-8"))
                rebuilt = json.loads(rebuilt_path.read_text(encoding="utf-8"))
                committed.pop("generated_at")
                rebuilt.pop("generated_at")
                with self.subTest(dataset=committed_path.name):
                    self.assertTrue(
                        rebuilt == committed,
                        f"{committed_path} is stale; run python3 scripts/build_stats.py",
                    )


if __name__ == "__main__":
    unittest.main()