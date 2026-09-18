import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import build_stats


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

        races, count, num_pages, fetched_pages = build_stats.collect_races(
            load, collection_attempts=1
        )

        self.assertEqual(calls, [1, 2, 1])
        self.assertEqual([item["name"] for item in races], ["race-1", "race-2", "race-3"])
        self.assertEqual(count, 3)
        self.assertEqual(num_pages, 2)
        self.assertEqual(fetched_pages, 2)

    @patch.object(build_stats.time, "sleep")
    def test_retries_complete_collection_when_snapshot_changes(self, _sleep):
        stable = page(1, 1, [race("stable")])
        changed = page(2, 1, [race("newest"), race("stable")])
        responses = iter([stable, changed, stable, stable])

        races, count, num_pages, fetched_pages = build_stats.collect_races(
            lambda _page_number: next(responses), collection_attempts=2
        )

        self.assertEqual([item["name"] for item in races], ["stable"])
        self.assertEqual((count, num_pages), (1, 1))
        self.assertEqual(fetched_pages, 1)

    @patch.object(build_stats.time, "sleep")
    def test_stops_after_complete_boundary_page(self, _sleep):
        page_one = [
            race("new-1", ended_at="2026-02-02T00:00:00Z"),
            race("new-2", ended_at="2026-02-01T00:00:00Z"),
        ]
        page_two = [
            race("boundary-new", ended_at="2026-01-02T00:00:00Z"),
            race("boundary-old", ended_at="2025-12-31T23:00:00Z"),
        ]
        calls = []

        def load(page_number):
            calls.append(page_number)
            if page_number == 1:
                return page(8, 4, page_one)
            if page_number == 2:
                return page(8, 4, page_two)
            self.fail(f"Unexpected request for page {page_number}")

        races, count, api_pages, fetched_pages = build_stats.collect_races(
            load, collection_attempts=1
        )

        self.assertEqual(calls, [1, 2, 1])
        self.assertEqual(len(races), 4)
        self.assertEqual((count, api_pages, fetched_pages), (8, 4, 2))

    @patch.object(build_stats.time, "sleep")
    def test_rejects_completion_order_violation(self, _sleep):
        unordered = [
            race("older", ended_at="2026-08-01T00:00:00Z"),
            race("newer", ended_at="2026-08-02T00:00:00Z"),
        ]

        with self.assertRaisesRegex(build_stats.ConsistencyError, "order"):
            build_stats.collect_races(
                lambda _page_number: page(2, 1, unordered),
                collection_attempts=1,
            )

    def test_rejects_missing_completion_timestamp(self):
        missing = race("missing-terminal", ended_at=None, cancelled_at=None)

        with self.assertRaisesRegex(build_stats.DataError, "completion timestamp"):
            build_stats.collect_races(
                lambda _page_number: page(1, 1, [missing]),
                collection_attempts=1,
            )

    @patch.object(build_stats.json, "load", return_value={"races": []})
    @patch.object(build_stats, "urlopen")
    def test_fetch_requests_entrant_details(self, mocked_urlopen, _json_load):
        build_stats.fetch_page(3)

        request = mocked_urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["page"], ["3"])
        self.assertEqual(query["per_page"], ["100"])
        self.assertEqual(query["show_entrants"], ["true"])

    @patch.object(build_stats.time, "sleep")
    @patch.object(build_stats.json, "load", return_value={"races": []})
    @patch.object(build_stats, "urlopen")
    def test_fetch_retries_connection_resets(
        self, mocked_urlopen, _json_load, mocked_sleep
    ):
        mocked_urlopen.side_effect = [ConnectionResetError(), mocked_urlopen.return_value]

        self.assertEqual(build_stats.fetch_page(1), {"races": []})
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(1)

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
                api_page_count=1,
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
            api_page_count=1,
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
        self.assertEqual(stats["schema_version"], 4)
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["excluded_fetched_race_count"], 4)
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
            api_page_count=1,
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
            api_page_count=1,
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
            api_page_count=1,
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
                    "before-cutoff",
                    started_at="2026-01-01T04:59:59Z",
                    entrants_count=4,
                ),
                race(
                    "at-cutoff",
                    started_at="2026-01-01T05:00:00Z",
                    entrants_count=6,
                    info="Step Ladder Series - [keys]",
                ),
                race(
                    "second-half",
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
            api_record_count=5,
            api_page_count=1,
            generated_at=self.generated_at,
        )

        self.assertEqual(stats["schema_version"], 4)
        self.assertEqual(stats["source"]["data_start_local"], "2026-01-01")
        self.assertEqual(stats["source"]["included_race_count"], 2)
        self.assertEqual(stats["source"]["included_entrant_count"], 14)
        self.assertEqual(stats["source"]["excluded_fetched_race_count"], 3)
        self.assertNotIn("ladder_test", {item["mode"] for item in stats["modes"]})
        self.assertEqual(stats["diagnostics"]["excluded_before_data_start"], 1)
        self.assertEqual(
            stats["diagnostics"]["excluded_by_mode"],
            {"ladder test": 1, "ladder_test": 1},
        )

        periods = {item["id"]: item for item in stats["periods"]}
        self.assertEqual(list(periods), ["2026-H1", "2026-H2"])
        self.assertEqual(periods["2026-H1"]["label"], "Jan-Jun 2026")
        self.assertEqual(periods["2026-H1"]["completeness"], "complete")
        self.assertEqual(periods["2026-H1"]["race_count"], 1)
        self.assertEqual(periods["2026-H1"]["entrant_total"], 6)
        self.assertEqual(periods["2026-H2"]["completeness"], "partial")
        self.assertEqual(periods["2026-H2"]["race_count"], 1)
        self.assertEqual(periods["2026-H2"]["entrant_total"], 8)
        self.assertEqual(
            sum(item["race_count"] for item in stats["periods"]),
            stats["source"]["included_race_count"],
        )
        self.assertEqual(
            sum(item["entrant_total"] for item in stats["periods"]),
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
            api_record_count=2,
            api_page_count=1,
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
                api_record_count=1,
                api_page_count=1,
                generated_at=self.generated_at,
            )

        duplicate = {"user": {"id": "same-player"}}
        with self.assertRaisesRegex(build_stats.DataError, "duplicate entrant"):
            build_stats.aggregate_races(
                [race("duplicate", entrants_count=2, entrants=[duplicate, duplicate])],
                api_record_count=1,
                api_page_count=1,
                generated_at=self.generated_at,
            )

    def test_writes_valid_json_atomically(self):
        payload = {"hourly": [], "modes": []}
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "nested" / "stats.json"
            build_stats.write_json_atomic(payload, output_path)

            self.assertEqual(json.loads(output_path.read_text()), payload)
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())


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
        self.assertEqual(estimate["estimated_deleted_rooms"], 140)
        self.assertEqual(estimate["observed_finished_rooms"], 45)
        self.assertEqual(estimate["estimated_opened_rooms"], 185)
        self.assertEqual(estimate["estimated_deleted_share"], 140 / 185)
        self.assertLessEqual(
            estimate["sensitivity"]["lower_estimated_deleted_rooms"],
            estimate["estimated_deleted_rooms"],
        )
        self.assertLessEqual(
            estimate["estimated_deleted_rooms"],
            estimate["sensitivity"]["upper_estimated_deleted_rooms"],
        )

    def test_equal_hours_infer_no_deleted_rooms(self):
        estimate = build_stats._room_cancellation_estimate(self.hourly([4] * 24))

        self.assertEqual(estimate["estimated_deleted_rooms"], 0)
        self.assertEqual(estimate["estimated_opened_rooms"], 96)
        self.assertEqual(estimate["estimated_deleted_share"], 0)

    def test_sparse_hours_remain_nonnegative(self):
        estimate = build_stats._room_cancellation_estimate(
            self.hourly([3, 2, 1] + [0] * 21)
        )

        self.assertEqual(estimate["baseline_rooms_per_hour"], 0.5)
        self.assertEqual(estimate["estimated_deleted_rooms"], 11)
        self.assertGreaterEqual(
            estimate["sensitivity"]["lower_estimated_deleted_rooms"], 0
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
        self.assertEqual(data["schema_version"], 4)
        self.assertEqual(source["data_start_local"], "2026-01-01")
        self.assertTrue(all(period["start"] >= "2026-01-01" for period in data["periods"]))
        self.assertEqual(
            sum(period["race_count"] for period in data["periods"]),
            source["included_race_count"],
        )
        self.assertEqual(
            sum(period["entrant_total"] for period in data["periods"]),
            source["included_entrant_count"],
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
                + estimate["estimated_deleted_rooms"],
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
        self.assertNotIn("2025-", self.serialized)


if __name__ == "__main__":
    unittest.main()