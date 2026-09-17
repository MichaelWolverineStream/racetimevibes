import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import build_stats


def race(
    name,
    *,
    started_at="2026-09-17T20:00:00.000Z",
    entrants_count=4,
    info="Step Ladder Series - [simple] - Open",
    status="finished",
):
    return {
        "name": name,
        "status": {"value": status},
        "started_at": started_at,
        "entrants_count": entrants_count,
        "info": info,
    }


def page(count, num_pages, races):
    return {"count": count, "num_pages": num_pages, "races": races}


class PaginationTests(unittest.TestCase):
    @patch.object(build_stats.time, "sleep")
    def test_collects_dynamic_pages_and_partial_final_page(self, _sleep):
        first = race("race-1")
        second = race("race-2")
        third = race("race-3")
        calls = []

        def load(page_number):
            calls.append(page_number)
            if page_number == 2:
                return page(3, 2, [third])
            return page(3, 2, [first, second])

        races, count, num_pages = build_stats.collect_races(
            load, collection_attempts=1
        )

        self.assertEqual(calls, [1, 2, 1])
        self.assertEqual([item["name"] for item in races], ["race-1", "race-2", "race-3"])
        self.assertEqual(count, 3)
        self.assertEqual(num_pages, 2)

    @patch.object(build_stats.time, "sleep")
    def test_retries_complete_collection_when_snapshot_changes(self, _sleep):
        stable = page(1, 1, [race("stable")])
        changed = page(2, 1, [race("newest"), race("stable")])
        responses = iter([stable, changed, stable, stable])

        races, count, num_pages = build_stats.collect_races(
            lambda _page_number: next(responses), collection_attempts=2
        )

        self.assertEqual([item["name"] for item in races], ["stable"])
        self.assertEqual((count, num_pages), (1, 1))

    @patch.object(build_stats.time, "sleep")
    def test_rejects_duplicate_races(self, _sleep):
        duplicate = race("duplicate")

        def load(page_number):
            return page(2, 2, [duplicate])

        with self.assertRaisesRegex(build_stats.ConsistencyError, "Duplicate"):
            build_stats.collect_races(load, collection_attempts=1)

    def test_rejects_invalid_pagination_metadata(self):
        with self.assertRaisesRegex(build_stats.DataError, "pagination"):
            build_stats.collect_races(
                lambda _page_number: {"count": "1", "num_pages": 1, "races": []},
                collection_attempts=1,
            )


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.generated_at = datetime(2026, 9, 17, 12, tzinfo=UTC)

    def test_rejects_dataset_with_no_included_races(self):
        with self.assertRaisesRegex(build_stats.DataError, "No races remained"):
            build_stats.aggregate_races(
                [race("cancelled", status="cancelled")],
                api_record_count=1,
                num_pages=1,
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
            api_record_count=8,
            num_pages=1,
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
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["excluded_race_count"], 4)
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
            api_record_count=1,
            num_pages=1,
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
            api_record_count=4,
            num_pages=1,
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
            api_record_count=3,
            num_pages=1,
            generated_at=self.generated_at,
        )

        self.assertEqual(
            [item["mode"] for item in stats["modes"]],
            ["entrance", "keys", "simple"],
        )

    def test_excludes_ladder_tests_and_groups_calendar_half_years(self):
        stats = build_stats.aggregate_races(
            [
                race(
                    "h1-boundary",
                    started_at="2025-07-01T03:30:00Z",
                    entrants_count=4,
                ),
                race(
                    "h2-boundary",
                    started_at="2025-07-01T04:00:00Z",
                    entrants_count=6,
                    info="Step Ladder Series - [keys]",
                ),
                race(
                    "next-year",
                    started_at="2026-01-01T05:00:00Z",
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
            api_record_count=5,
            num_pages=1,
            generated_at=self.generated_at,
        )

        self.assertEqual(stats["schema_version"], 3)
        self.assertEqual(stats["source"]["included_race_count"], 3)
        self.assertEqual(stats["source"]["included_entrant_count"], 18)
        self.assertEqual(stats["source"]["excluded_race_count"], 2)
        self.assertNotIn("ladder_test", {item["mode"] for item in stats["modes"]})
        self.assertEqual(
            stats["diagnostics"]["excluded_by_mode"],
            {"ladder test": 1, "ladder_test": 1},
        )

        periods = {item["id"]: item for item in stats["periods"]}
        self.assertEqual(list(periods), ["2025-H1", "2025-H2", "2026-H1"])
        self.assertEqual(periods["2025-H1"]["label"], "Jan-Jun 2025")
        self.assertEqual(periods["2025-H1"]["completeness"], "partial")
        self.assertEqual(periods["2025-H1"]["race_count"], 1)
        self.assertEqual(periods["2025-H1"]["entrant_total"], 4)
        self.assertEqual(periods["2025-H2"]["completeness"], "complete")
        self.assertEqual(periods["2025-H2"]["race_count"], 1)
        self.assertEqual(periods["2025-H2"]["entrant_total"], 6)
        self.assertEqual(periods["2026-H1"]["completeness"], "partial")
        self.assertEqual(periods["2026-H1"]["race_count"], 1)
        self.assertEqual(periods["2026-H1"]["entrant_total"], 8)
        self.assertEqual(
            sum(item["race_count"] for item in stats["periods"]),
            stats["source"]["included_race_count"],
        )
        self.assertEqual(
            sum(item["entrant_total"] for item in stats["periods"]),
            stats["source"]["included_entrant_count"],
        )

    def test_writes_valid_json_atomically(self):
        payload = {"hourly": [], "modes": []}
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "nested" / "stats.json"
            build_stats.write_json_atomic(payload, output_path)

            self.assertEqual(json.loads(output_path.read_text()), payload)
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()