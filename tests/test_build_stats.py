import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts import build_stats, race_archive, schedule_archive


DEFAULT_ENTRANTS = object()
MODES = [
    {"id": 1, "archetype": 1, "name": "Open", "active": True},
    {"id": 4, "archetype": 4, "name": "Keys", "active": True},
    {"id": 8, "archetype": 8, "name": "Odd", "active": True},
]


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


def seeded(seed, mode="simple"):
    """A race info line with an alttpr.racing seed link for race id `seed`."""
    return (
        f"Step Ladder Series - [{mode}] - Open\n"
        f"open - https://alttpr.racing/getseed.php?race={seed} - (x)"
    )


def slot(slot_id, race_id, started_at, mode=1, season=1):
    moment = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    return {
        "id": slot_id,
        "raceId": race_id,
        "season": season,
        "time": str(int(moment.timestamp())),
        "mode": mode,
    }


def aggregate(races, schedule=(), modes=MODES):
    return build_stats.aggregate_races(
        list(races),
        schedule=list(schedule),
        modes=modes,
        generated_at=datetime(2026, 9, 17, 12, tzinfo=UTC),
    )


class AggregationTests(unittest.TestCase):
    def test_rejects_dataset_with_no_included_races(self):
        with self.assertRaisesRegex(build_stats.DataError, "No races remained"):
            aggregate([race("cancelled", status="cancelled")])

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

        stats = aggregate(races)

        self.assertEqual(stats["schema_version"], 10)
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["included_entrant_count"], 21)
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

    def test_buckets_slots_by_eastern_hour_in_winter_and_summer(self):
        stats = aggregate(
            [
                race("winter", started_at="2026-01-15T05:00:00Z", info=seeded(1)),
                race("summer", started_at="2026-07-15T05:00:00Z", info=seeded(2)),
                race("newest", started_at="2026-07-20T05:00:00Z"),
            ],
            [
                slot(1, 1, "2026-01-15T05:00:00Z"),
                slot(2, 2, "2026-07-15T05:00:00Z"),
            ],
        )

        self.assertNotIn("hourly", stats)
        self.assertEqual(sum(item["scheduled_count"] for item in stats["weekday_hourly"]), 2)
        cells = {
            (item["weekday_label"], item["hour"]): item["scheduled_count"]
            for item in stats["weekday_hourly"]
            if item["scheduled_count"]
        }
        self.assertEqual(cells, {("Thursday", 0): 1, ("Wednesday", 1): 1})

    def test_includes_2025_races_excludes_ladder_tests_and_groups_quarters(self):
        stats = aggregate(
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
            ]
        )

        self.assertEqual(stats["schema_version"], 10)
        self.assertEqual(stats["source"]["data_start_local"], "2025-04-20")
        self.assertEqual(stats["source"]["included_race_count"], 4)
        self.assertEqual(stats["source"]["included_entrant_count"], 23)
        self.assertEqual(stats["source"]["excluded_archived_race_count"], 2)
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
        stats = aggregate(
            [
                race("april", started_at="2025-04-20T16:00:00Z", entrants_count=4, info=seeded(1)),
                race("december", started_at="2025-12-31T20:00:00Z", entrants_count=5, info=seeded(2)),
                race("july", started_at="2026-07-01T04:00:00Z", entrants_count=8, info=seeded(3)),
            ],
            [
                slot(1, 1, "2025-04-20T16:00:00Z"),
                slot(2, 2, "2025-12-31T20:00:00Z"),
                slot(3, 3, "2026-07-01T04:00:00Z"),
            ],
        )

        monthly = {item["id"]: item for item in stats["monthly"]}
        expected_ids = [f"2025-{month:02d}" for month in range(4, 13)] + [
            f"2026-{month:02d}" for month in range(1, 8)
        ]
        self.assertEqual(list(monthly), expected_ids)

        self.assertEqual(monthly["2025-04"]["scheduled_day_count"], 1)
        self.assertFalse(monthly["2025-04"]["is_complete"])
        self.assertEqual(monthly["2025-04"]["entries_per_day"], 4)

        self.assertEqual(monthly["2025-05"]["race_count"], 0)
        self.assertIsNone(monthly["2025-05"]["average_racers"])
        self.assertEqual(monthly["2025-05"]["scheduled_day_count"], 0)
        self.assertIsNone(monthly["2025-05"]["entries_per_day"])

        self.assertTrue(monthly["2025-12"]["is_complete"])
        self.assertEqual(monthly["2025-12"]["scheduled_day_entrant_total"], 5)
        self.assertEqual(monthly["2025-12"]["entries_per_day"], 5)

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

    def test_never_serializes_identities(self):
        private_id = "private-player-id"
        private_name = "Private Player"
        stats = aggregate(
            [
                race(
                    "first",
                    entrants_count=2,
                    entrants=[
                        {"user": {"id": private_id, "name": private_name}},
                        {"user": None},
                    ],
                    info=seeded(1),
                ),
                race("newest", started_at="2026-09-20T20:00:00Z"),
            ],
            [slot(1, 1, "2026-09-17T20:00:00Z")],
        )

        serialized = json.dumps(stats)
        self.assertNotIn(private_id, serialized)
        self.assertNotIn(private_name, serialized)
        self.assertNotIn('"entrants"', serialized)

    def test_rejects_an_entrant_count_mismatch(self):
        with self.assertRaisesRegex(build_stats.DataError, "reports 2 entrants"):
            aggregate([race("partial", entrants_count=2, entrants=[])])


# Mondays at 13:00 ET (17:00 UTC) in June 2026, and a race long after them, so the day
# before the newest race never cuts the schedule short.
MONDAYS = ("2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29")
NEWEST = race("newest", started_at="2026-08-31T16:00:00Z")


class ScheduleAggregationTests(unittest.TestCase):
    def test_counts_started_never_started_pulled_and_excluded_slots(self):
        stats = aggregate(
            [
                race("a", started_at="2026-06-01T17:00:00Z", entrants_count=6, info=seeded(101)),
                race("test", started_at="2026-06-01T23:00:00Z", info=seeded(104, "ladder_test")),
                race("b", started_at="2026-06-08T17:00:00Z", entrants_count=4, info=seeded(105)),
                NEWEST,
            ],
            [
                slot(1, 101, "2026-06-01T17:00:00Z"),
                slot(2, 102, "2026-06-01T19:00:00Z"),
                slot(3, None, "2026-06-01T21:00:00Z"),
                slot(4, 104, "2026-06-01T23:00:00Z"),
                slot(5, 105, "2026-06-08T17:00:00Z"),
            ],
        )

        self.assertEqual(
            stats["diagnostics"]["schedule"],
            {
                "scheduled_count": 3,
                "started_count": 2,
                "pulled_count": 1,
                "unscheduled_race_count": 0,
            },
        )
        self.assertEqual(stats["source"]["schedule_counted_through"], "2026-06-08T17:00:00Z")
        by_hour = {
            hour: {
                key: sum(
                    item[key] for item in stats["weekday_hourly"] if item["hour"] == hour
                )
                for key in ("scheduled_count", "started_count", "entrant_total")
            }
            for hour in (13, 15, 17)
        }
        self.assertEqual(
            by_hour[13], {"scheduled_count": 2, "started_count": 2, "entrant_total": 10}
        )
        self.assertEqual(
            by_hour[15], {"scheduled_count": 1, "started_count": 0, "entrant_total": 0}
        )
        self.assertEqual(
            by_hour[17], {"scheduled_count": 0, "started_count": 0, "entrant_total": 0}
        )
        monday_13 = next(
            item
            for item in stats["weekday_hourly"]
            if item["weekday_label"] == "Monday" and item["hour"] == 13
        )
        self.assertEqual(
            (monday_13["entrant_total"], monday_13["average_racers"], monday_13["band"]),
            (10, 5, None),
        )

    def test_stops_a_day_before_the_newest_race(self):
        stats = aggregate(
            [
                race("first", started_at="2026-06-01T17:00:00Z"),
                race("newest", started_at="2026-06-10T12:00:00Z"),
            ],
            [
                slot(1, 1, "2026-06-09T11:00:00Z"),
                slot(2, 2, "2026-06-09T13:00:00Z"),
            ],
        )

        self.assertEqual(stats["source"]["schedule_counted_through"], "2026-06-09T12:00:00Z")
        self.assertEqual(stats["diagnostics"]["schedule"]["scheduled_count"], 1)

    def test_stops_at_the_latest_slot_with_a_race_id(self):
        # A stale mirror: the later slots have no race id yet, so they are neither
        # counted as never started nor as pulled.
        stats = aggregate(
            [
                race("a", started_at="2026-06-01T17:00:00Z", info=seeded(1)),
                race("later", started_at="2026-06-03T17:00:00Z", info=seeded(3)),
                NEWEST,
            ],
            [
                slot(1, 1, "2026-06-01T17:00:00Z"),
                slot(2, 2, "2026-06-02T17:00:00Z"),
                slot(3, None, "2026-06-03T17:00:00Z"),
                slot(4, None, "2026-06-04T17:00:00Z"),
            ],
        )

        self.assertEqual(stats["source"]["schedule_counted_through"], "2026-06-02T17:00:00Z")
        self.assertEqual(
            stats["diagnostics"]["schedule"],
            {
                "scheduled_count": 2,
                "started_count": 1,
                "pulled_count": 0,
                "unscheduled_race_count": 0,
            },
        )

    def test_counts_nothing_without_a_slot_that_has_a_race_id(self):
        stats = aggregate(
            [race("a", started_at="2026-06-01T17:00:00Z"), NEWEST],
            [slot(1, None, "2026-06-01T17:00:00Z")],
        )

        self.assertIsNone(stats["source"]["schedule_counted_through"])
        self.assertEqual(
            stats["diagnostics"]["schedule"],
            {
                "scheduled_count": 0,
                "started_count": 0,
                "pulled_count": 0,
                "unscheduled_race_count": 0,
            },
        )

    def test_an_empty_schedule_gives_empty_schedule_panels(self):
        stats = aggregate([race("a", started_at="2026-06-01T17:00:00Z")], [])

        self.assertIsNone(stats["source"]["schedule_counted_through"])
        self.assertTrue(all(item["scheduled_count"] == 0 for item in stats["weekday_hourly"]))
        self.assertTrue(all(item["band"] is None for item in stats["weekday_hourly"]))
        (month,) = stats["monthly"]
        self.assertEqual(month["scheduled_day_count"], 0)
        self.assertIsNone(month["entries_per_day"])
        self.assertIsNone(month["fired_per_day"])
        self.assertEqual(stats["seasons"], [])

    def test_leaves_out_slots_before_the_archive_starts(self):
        stats = aggregate(
            [race("a", started_at="2026-06-01T17:00:00Z", info=seeded(1)), NEWEST],
            [
                slot(1, 900, "2026-05-31T17:00:00Z"),
                slot(2, 1, "2026-06-01T17:00:00Z"),
            ],
        )

        self.assertEqual(stats["diagnostics"]["schedule"]["scheduled_count"], 1)
        self.assertEqual(stats["monthly"][0]["id"], "2026-06")

    def test_an_unmapped_archetype_joins_by_seed(self):
        stats = aggregate(
            [race("odd", started_at="2026-06-01T17:00:00Z", info=seeded(1, "fun")), NEWEST],
            [
                slot(1, 1, "2026-06-01T17:00:00Z", mode=8),
                slot(2, 2, "2026-06-08T17:00:00Z", mode=8),
            ],
        )

        schedule = stats["diagnostics"]["schedule"]
        self.assertEqual((schedule["scheduled_count"], schedule["started_count"]), (2, 1))

    def test_rejects_a_counted_slot_with_an_unlisted_mode(self):
        with self.assertRaisesRegex(build_stats.DataError, "names mode 77, which is not listed"):
            aggregate(
                [race("a", started_at="2026-06-01T17:00:00Z"), NEWEST],
                [slot(1, 1, "2026-06-01T17:00:00Z", mode=77)],
            )

    def test_rejects_a_counted_slot_in_a_quarter_without_races(self):
        with self.assertRaisesRegex(build_stats.DataError, "2026-Q2, which has no finished race"):
            aggregate(
                [
                    race("q1", started_at="2026-03-02T17:00:00Z"),
                    race("q3", started_at="2026-07-13T17:00:00Z"),
                ],
                [slot(1, 1, "2026-05-04T17:00:00Z")],
            )

    def test_bands_weekday_hours_by_quartile(self):
        # Seven Monday hours whose racers per scheduled race are 1 to 7, over five slots
        # each: one race of 5 * value racers and four that never started.
        races, schedule = [NEWEST], []
        for value in range(1, 8):
            hour = 10 + value
            for week, day in enumerate(MONDAYS):
                race_id = value * 10 + week
                started_at = f"{day}T{hour + 4:02d}:00:00Z"
                schedule.append(slot(race_id, race_id, started_at))
                if week == 0:
                    races.append(
                        race(
                            f"r{race_id}",
                            started_at=started_at,
                            entrants_count=5 * value,
                            info=seeded(race_id),
                        )
                    )
        # Four slots are one too few for a band.
        for week, day in enumerate(MONDAYS[:4]):
            schedule.append(slot(900 + week, 900 + week, f"{day}T13:00:00Z"))

        stats = aggregate(races, schedule)

        monday = {
            item["hour"]: item for item in stats["weekday_hourly"] if item["weekday"] == 0
        }
        # The quartiles of 1..7 are 2 and 6; a tie with a quartile takes the outer band.
        self.assertEqual(
            {hour: monday[hour]["band"] for hour in range(9, 18)},
            {
                9: None,
                10: None,
                11: "quiet",
                12: "quiet",
                13: "normal",
                14: "normal",
                15: "normal",
                16: "prime",
                17: "prime",
            },
        )
        self.assertEqual(monday[17]["average_racers"], 7)
        self.assertTrue(all(item["band"] is None for item in stats["weekday_hourly"] if item["weekday"] != 0))

    def test_per_day_entries_count_only_days_with_scheduled_races(self):
        stats = aggregate(
            [
                race("may", started_at="2026-05-20T17:00:00Z", entrants_count=3),
                race("a", started_at="2026-06-01T17:00:00Z", entrants_count=4, info=seeded(1)),
                race("gap", started_at="2026-06-03T17:00:00Z", entrants_count=5),
                # On a scheduled day, but too late to join slot 3: its entries count, but it never fired.
                race("extra", started_at="2026-06-04T20:00:00Z", entrants_count=2),
                race("test", started_at="2026-06-10T17:00:00Z", info=seeded(4, "ladder_test")),
                race("c", started_at="2026-07-02T17:00:00Z", entrants_count=6, info=seeded(5)),
            ],
            [
                slot(1, 1, "2026-06-01T17:00:00Z"),
                slot(2, None, "2026-06-02T17:00:00Z"),
                slot(3, 3, "2026-06-04T17:00:00Z"),
                slot(4, 4, "2026-06-10T17:00:00Z"),
                slot(5, 5, "2026-07-02T17:00:00Z"),
                slot(6, 6, "2026-07-15T17:00:00Z"),
            ],
        )

        monthly = {item["id"]: item for item in stats["monthly"]}
        self.assertEqual(
            {
                month_id: (
                    item["scheduled_day_count"],
                    item["scheduled_day_entrant_total"],
                    item["entrant_total"],
                    item["entries_per_day"],
                    item["fired_race_count"],
                    item["fired_per_day"],
                )
                for month_id, item in monthly.items()
            },
            {
                "2026-05": (0, 0, 3, None, 0, None),
                "2026-06": (3, 6, 11, 2, 1, 1 / 3),
                "2026-07": (1, 6, 6, 6, 1, 1),
            },
        )
        schedule = stats["diagnostics"]["schedule"]
        self.assertEqual(stats["source"]["schedule_counted_through"], "2026-07-01T17:00:00Z")
        self.assertEqual(schedule["unscheduled_race_count"], 3)
        self.assertEqual(schedule["pulled_count"], 1)

    def test_periods_take_slots_by_their_own_quarter(self):
        stats = aggregate(
            [
                race("q2", started_at="2026-06-29T17:00:00Z", entrants_count=4, info=seeded(1)),
                race("q3", started_at="2026-07-06T17:00:00Z", entrants_count=6, info=seeded(2)),
                NEWEST,
            ],
            [
                slot(1, 1, "2026-06-29T17:00:00Z"),
                slot(2, 2, "2026-07-06T17:00:00Z"),
                slot(3, 3, "2026-07-13T17:00:00Z"),
            ],
        )

        periods = {item["id"]: item for item in stats["periods"]}
        self.assertEqual(
            {
                period_id: sum(item["scheduled_count"] for item in period["weekday_hourly"])
                for period_id, period in periods.items()
            },
            {"2026-Q2": 1, "2026-Q3": 2},
        )
        self.assertEqual(sum(item["scheduled_count"] for item in stats["weekday_hourly"]), 3)

    def test_seasons_bucket_slots_and_races_by_their_start(self):
        stats = aggregate(
            [
                race("early", started_at="2026-05-10T17:00:00Z", entrants_count=2),
                race("a", started_at="2026-06-01T17:00:00Z", entrants_count=6, info=seeded(1)),
                race("b", started_at="2026-06-08T17:00:00Z", entrants_count=4, info=seeded(2)),
                race("e", started_at="2026-06-29T17:00:00Z", entrants_count=5, info=seeded(5)),
                race("newest", started_at="2026-07-03T12:00:00Z", entrants_count=3),
            ],
            [
                slot(1, 1, "2026-06-01T17:00:00Z"),
                slot(2, 2, "2026-06-08T17:00:00Z"),
                slot(3, 103, "2026-06-15T17:00:00Z"),
                slot(4, 104, "2026-06-22T17:00:00Z", season=2),
                slot(5, 5, "2026-06-29T17:00:00Z", season=2),
                slot(6, 106, "2026-05-16T17:00:00Z", season=2),
            ],
        )

        self.assertEqual(stats["source"]["schedule_counted_through"], "2026-06-29T17:00:00Z")
        self.assertEqual(
            stats["seasons"],
            [
                {
                    "season": 1,
                    "is_complete": True,
                    "scheduled_count": 3,
                    "started_count": 2,
                    "race_count": 2,
                    "entrant_total": 10,
                },
                {
                    "season": 2,
                    "is_complete": False,
                    "scheduled_count": 2,
                    "started_count": 1,
                    "race_count": 2,
                    "entrant_total": 8,
                },
            ],
        )
        self.assertEqual(
            stats["diagnostics"]["seasons"],
            {"races_without_season": 1, "slots_without_season": 1},
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

    def schedule(self, rows, modes=MODES):
        directory = self.archive_dir / schedule_archive.SCHEDULE_DIRECTORY
        race_archive.write_json_atomic(
            {"schema_version": 1, "fetched_at": "2026-09-18T00:00:00Z", "modes": modes},
            directory / schedule_archive.MODES_FILE,
        )
        race_archive.write_json_atomic(
            {"schema_version": 1, "fetched_at": "2026-09-18T00:00:00Z", "schedule": rows},
            directory / "season-1.json",
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
                info=seeded(1),
            )
        )
        self.archive(
            race(
                "alttpr-ladder/test-race-0002",
                info="Step Ladder Series - [ladder_test]",
            )
        )
        self.archive(race("alttpr-ladder/newest-race-0003", started_at="2026-09-20T20:00:00Z"))
        self.schedule([slot(1, 1, "2026-09-17T20:00:00Z"), slot(2, 2, "2026-09-18T20:00:00Z")])
        race_archive.write_json_atomic(
            {"player-a": "Renamed"},
            self.archive_dir / race_archive.RACER_NAMES_FILE,
        )

        stats, participants, comments = build_stats.build(
            self.archive_dir, *self.output_paths
        )

        source = stats["source"]
        self.assertEqual(source["archived_race_count"], 3)
        self.assertEqual(source["included_race_count"], 2)
        self.assertEqual(source["excluded_archived_race_count"], 1)
        self.assertEqual(stats["diagnostics"]["excluded_by_mode"], {"ladder_test": 1})
        self.assertEqual(
            (stats["diagnostics"]["schedule"]["scheduled_count"], stats["diagnostics"]["schedule"]["started_count"]),
            (2, 1),
        )
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
            ["B", "Player 0", "Player 1", "Player 2", "Player 3", "Renamed"],
        )

    def test_empty_archive_fails_with_instructions(self):
        with self.assertRaisesRegex(
            build_stats.DataError, "run scripts/race_archive.py first"
        ):
            build_stats.build(self.archive_dir, *self.output_paths)
        self.assertFalse(self.output_paths[0].exists())

    def test_refuses_an_archive_without_a_schedule_mirror(self):
        self.archive(race("alttpr-ladder/kept-race-0001"))
        with self.assertRaisesRegex(
            build_stats.DataError, "run scripts/schedule_archive.py first"
        ):
            build_stats.build(self.archive_dir, *self.output_paths)
        self.assertFalse(self.output_paths[0].exists())


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
        self.assertEqual(data["schema_version"], 10)
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

        schedule = data["diagnostics"]["schedule"]
        self.assertNotIn("hourly", data)
        for selection in [data, *data["periods"]]:
            cells = selection["weekday_hourly"]
            self.assertNotIn("hourly", selection)
            self.assertEqual(len(cells), 168)
            self.assertTrue(
                all(item["started_count"] <= item["scheduled_count"] for item in cells)
            )
            self.assertTrue(
                {item["band"] for item in cells} <= {"prime", "normal", "quiet", None}
            )
        for key in ("scheduled_count", "started_count"):
            self.assertEqual(
                sum(item[key] for item in data["weekday_hourly"]), schedule[key]
            )
            self.assertEqual(
                sum(
                    item[key]
                    for period in data["periods"]
                    for item in period["weekday_hourly"]
                ),
                schedule[key],
            )
        seasons = data["seasons"]
        missing = data["diagnostics"]["seasons"]
        self.assertEqual(
            sum(row["race_count"] for row in seasons) + missing["races_without_season"],
            source["included_race_count"],
        )
        self.assertLessEqual(
            sum(row["entrant_total"] for row in seasons), source["included_entrant_count"]
        )
        self.assertEqual(
            sum(row["scheduled_count"] for row in seasons) + missing["slots_without_season"],
            schedule["scheduled_count"],
        )
        self.assertTrue(all(row["started_count"] <= row["scheduled_count"] for row in seasons))
        numbers = [row["season"] for row in seasons]
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertTrue(all(row["is_complete"] for row in seasons[:-1]))
        for month in data["monthly"]:
            self.assertLessEqual(
                month["scheduled_day_entrant_total"], month["entrant_total"]
            )
            self.assertLessEqual(month["fired_race_count"], month["race_count"])
            self.assertEqual(
                month["fired_per_day"] is None, month["scheduled_day_count"] == 0
            )
            if month["scheduled_day_count"] == 0:
                self.assertIsNone(month["entries_per_day"])

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