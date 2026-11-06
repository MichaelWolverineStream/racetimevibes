import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts import build_stats, ladder_rating, race_archive, schedule_archive, season_ratings


MODES = [{"id": 1, "archetype": 1, "name": "Open", "active": True}]
SEASON_ONE = datetime(2026, 9, 1, 20, tzinfo=UTC)
SEASON_TWO = datetime(2026, 9, 15, 20, tzinfo=UTC)
# Long after every fixture race, so the 14-day settle limit has passed for all of them.
LATE = datetime(2026, 12, 1, tzinfo=UTC)


def stamp(moment):
    return moment.isoformat().replace("+00:00", "Z")


def slot(slot_id, season, moment):
    return {
        "id": slot_id,
        "raceId": None,
        "season": season,
        "time": str(int(moment.timestamp())),
        "mode": 1,
    }


def schedule(first=SEASON_ONE, second=SEASON_TWO, second_slots=12):
    """Daily slots: 10 of season 1 from `first`, then `second_slots` of season 2 from `second`."""
    return [slot(day, 1, first + timedelta(days=day)) for day in range(10)] + [
        slot(100 + day, 2, second + timedelta(days=day)) for day in range(second_slots)
    ]


def race(name, started_at, order, *, recorded=True, recordable=True):
    return {
        "name": f"alttpr-ladder/{name}",
        "status": {"value": "finished"},
        "started_at": stamp(started_at),
        "ended_at": stamp(started_at + timedelta(hours=2)),
        "cancelled_at": None,
        "recorded": recorded,
        "recordable": recordable,
        "entrants_count": len(order),
        "entrants": [
            {
                "user": {"id": user_id, "name": user_id},
                "status": {"value": "done"},
                "place": index + 1,
                "finish_time": f"PT1H{index + 10}M",
            }
            for index, user_id in enumerate(order)
        ],
        "info": "Step Ladder Series - [simple] - Open",
    }


def season_races(recorded=True):
    """Two races in each season, all settled, unless season 1's last is still unrecorded."""
    return [
        race("one-alpha-1001", SEASON_ONE, ["ana", "ben", "cid"]),
        # The day before season 2, so it is still unsettled for 14 days into season 2.
        race("one-bravo-1002", SEASON_TWO - timedelta(days=1), ["ben", "ana"], recorded=recorded),
        race("two-alpha-2001", SEASON_TWO, ["cid", "ana", "ben"]),
        race("two-bravo-2002", SEASON_TWO + timedelta(days=1), ["ana", "cid"]),
    ]


class LockReasonTests(unittest.TestCase):
    def reason(self, *, rows=None, races=None, newest=None, now=LATE):
        rows = schedule() if rows is None else rows
        return season_ratings.lock_reason(
            1,
            schedule_archive.season_starts(rows),
            rows,
            season_races()[:2] if races is None else races,
            SEASON_TWO + timedelta(days=1) if newest is None else newest,
            now,
        )

    def test_a_finished_settled_season_locks(self):
        self.assertIsNone(self.reason())

    def test_not_before_the_next_season_starts(self):
        rows = [row for row in schedule() if row["season"] == 1]
        self.assertEqual(self.reason(rows=rows), "the next season has not started")

    def test_not_within_a_day_of_the_next_season_starting(self):
        self.assertRegex(
            self.reason(now=SEASON_TWO + timedelta(hours=23)), "season 2 started less than"
        )
        self.assertIsNone(self.reason(now=SEASON_TWO + schedule_archive.SETTLE))

    def test_not_while_the_next_season_has_fewer_slots_than_the_check(self):
        rows = schedule(second_slots=season_ratings.LOCK_CONFIRM_SLOTS - 1)
        self.assertRegex(self.reason(rows=rows), "fewer than 10 slots follow the start of season 2")

    def test_not_while_the_seasons_own_slots_follow_the_next_seasons_start(self):
        # A misfiled season-2 slot early would put season 2's start before season 1 ends.
        rows = schedule() + [slot(900, 1, SEASON_TWO + timedelta(hours=1))]
        self.assertRegex(self.reason(rows=rows), "season 1 still has slots among the first 10")

    def test_a_misfiled_slot_of_a_third_season_does_not_block(self):
        # Like season 6's slot right after season 5 began, which must not block season 4.
        rows = schedule() + [slot(900, 3, SEASON_TWO + timedelta(hours=1))]
        self.assertIsNone(self.reason(rows=rows))

    def test_not_before_the_archive_holds_a_race_of_the_next_season(self):
        self.assertRegex(
            self.reason(newest=SEASON_TWO - timedelta(hours=1)),
            "the archive has no race from season 2 yet",
        )

    def test_not_while_a_race_awaits_recording(self):
        unrecorded = season_races(recorded=False)[:2]
        now = SEASON_TWO + timedelta(days=2)
        self.assertEqual(
            self.reason(races=unrecorded, now=now), "1 of its races still await recording"
        )

    def test_an_unrecordable_race_does_not_block(self):
        races = [race("one-alpha-1001", SEASON_ONE, ["ana", "ben"], recorded=False, recordable=False)]
        self.assertIsNone(self.reason(races=races, now=SEASON_TWO + timedelta(days=2)))

    def test_a_race_past_the_settle_limit_no_longer_blocks(self):
        unrecorded = season_races(recorded=False)[:2]
        self.assertIsNone(self.reason(races=unrecorded, now=LATE))


class LedgerFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name) / season_ratings.LEDGER_DIRECTORY

    def ledger(self, season=1, races=None):
        return {
            "schema_version": 1,
            "season": season,
            "start": "2026-09-01T20:00:00Z",
            "end": "2026-09-15T20:00:00Z",
            "rating": {"model": "bradley-terry"},
            "races": races
            if races is not None
            else [{"r": f"alttpr-ladder/race-{season}", "mode": "simple", "e": [["ana", 1100, 1090]]}],
        }

    def test_a_ledger_round_trips(self):
        path = season_ratings.write_ledger(self.directory, self.ledger())
        self.assertEqual(path.name, "season-1.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.ledger())
        ledgers = season_ratings.load_ledgers(self.directory)
        self.assertEqual(list(ledgers), [1])
        locked = ledgers[1]
        self.assertEqual((locked.start, locked.end), (SEASON_ONE, SEASON_TWO))
        self.assertEqual(
            locked.races,
            {"alttpr-ladder/race-1": season_ratings.LedgerRace("simple", {"ana": (1100, 1090)})},
        )
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_a_ledger_is_never_overwritten(self):
        season_ratings.write_ledger(self.directory, self.ledger())
        changed = self.ledger(races=[{"r": "alttpr-ladder/race-1", "mode": "simple", "e": [["ana", 1, 1]]}])
        with self.assertRaisesRegex(season_ratings.SeasonLedgerError, "never rewritten"):
            season_ratings.write_ledger(self.directory, changed)
        self.assertEqual(season_ratings.load_ledgers(self.directory)[1].races["alttpr-ladder/race-1"].points, {"ana": (1100, 1090)})
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_a_missing_directory_holds_no_ledgers(self):
        self.assertEqual(season_ratings.load_ledgers(self.directory), {})

    def test_malformed_ledgers_are_refused(self):
        cases = {
            "extra key": self.ledger() | {"locked_at": "now"},
            "schema": self.ledger() | {"schema_version": 2},
            "season": self.ledger() | {"season": 0},
            "backwards": self.ledger() | {"end": "2026-08-01T00:00:00Z"},
            "no races": self.ledger(races=[]),
            "empty race": self.ledger(races=[{"r": "alttpr-ladder/x", "mode": "simple", "e": []}]),
            "null points": self.ledger(
                races=[{"r": "alttpr-ladder/x", "mode": "simple", "e": [["ana", None, 1000]]}]
            ),
            "twice": self.ledger(
                races=[{"r": "alttpr-ladder/x", "mode": "simple", "e": [["ana", 1, 1], ["ana", 2, 2]]}]
            ),
        }
        for label, payload in cases.items():
            with self.subTest(label):
                with self.assertRaises(season_ratings.SeasonLedgerError):
                    season_ratings.parse_ledger(payload, label)

    def test_a_file_must_hold_its_own_season(self):
        self.directory.mkdir(parents=True)
        (self.directory / "season-2.json").write_text(json.dumps(self.ledger(season=1)), encoding="utf-8")
        with self.assertRaisesRegex(season_ratings.SeasonLedgerError, "holds season 1"):
            season_ratings.load_ledgers(self.directory)

    def test_a_race_can_be_in_one_ledger_only(self):
        season_ratings.write_ledger(self.directory, self.ledger(season=1))
        twin = self.ledger(season=2)
        twin["races"][0]["r"] = "alttpr-ladder/race-1"
        season_ratings.write_ledger(self.directory, twin)
        with self.assertRaisesRegex(season_ratings.SeasonLedgerError, "seasons 1 and 2"):
            season_ratings.load_ledgers(self.directory)


class LockSeasonsTests(unittest.TestCase):
    """Build a fixture archive, lock its seasons and rebuild, as `--lock-seasons` does."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive_dir = self.root / "archive"
        self.ledger_dir = season_ratings.ledger_dir_for(self.archive_dir)
        self.outputs = tuple(self.root / name for name in ("race-stats.json", "participants.json", "comments.json"))

    def archive(self, races, rows):
        for item in races:
            race_archive.write_json_atomic(
                {"schema_version": 1, "fetched_at": "2026-09-30T00:00:00Z", "race": item},
                race_archive.race_path(self.archive_dir, item["name"]),
            )
        directory = self.archive_dir / schedule_archive.SCHEDULE_DIRECTORY
        race_archive.write_json_atomic(
            {"schema_version": 1, "fetched_at": "2026-09-30T00:00:00Z", "modes": MODES},
            directory / schedule_archive.MODES_FILE,
        )
        for season in sorted({row["season"] for row in rows}):
            race_archive.write_json_atomic(
                {
                    "schema_version": 1,
                    "fetched_at": "2026-09-30T00:00:00Z",
                    "schedule": [row for row in rows if row["season"] == season],
                },
                directory / f"season-{season}.json",
            )

    def build(self):
        return build_stats.build(self.archive_dir, *self.outputs)[1]

    def test_the_ledger_sits_beside_the_archive(self):
        self.assertEqual(self.ledger_dir, self.root / "season-ratings")
        self.assertEqual(
            season_ratings.ledger_dir_for(race_archive.ARCHIVE_DIR),
            Path(build_stats.__file__).resolve().parents[1] / "season-ratings",
        )

    def test_locking_writes_the_finished_season_and_changes_no_published_byte(self):
        self.archive(season_races(), schedule())
        participants = self.build()
        before = self.outputs[1].read_bytes()

        outcomes = season_ratings.lock_seasons(self.archive_dir, self.ledger_dir, participants, LATE)

        self.assertEqual(outcomes, [(1, None), (2, "the next season has not started")])
        self.assertEqual([path.name for path in self.ledger_dir.iterdir()], ["season-1.json"])
        ledger = json.loads((self.ledger_dir / "season-1.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["start"], "2026-09-01T20:00:00Z")
        self.assertEqual(ledger["end"], "2026-09-15T20:00:00Z")
        self.assertEqual(
            [race_row["r"] for race_row in ledger["races"]],
            ["alttpr-ladder/one-alpha-1001", "alttpr-ladder/one-bravo-1002"],
        )
        self.assertEqual(ledger["rating"], participants["rating"])

        rebuilt = self.build()
        self.assertEqual(
            {key: value for key, value in rebuilt.items() if key != "generated_at"},
            {key: value for key, value in participants.items() if key != "generated_at"},
        )
        self.assertNotEqual(before, b"")
        # A locked season is not offered again.
        self.assertEqual(
            season_ratings.lock_seasons(self.archive_dir, self.ledger_dir, rebuilt, LATE),
            [(2, "the next season has not started")],
        )

    def test_a_season_awaiting_recording_stays_open(self):
        self.archive(season_races(recorded=False), schedule())
        participants = self.build()
        now = SEASON_TWO + timedelta(days=3)
        outcomes = season_ratings.lock_seasons(self.archive_dir, self.ledger_dir, participants, now)
        self.assertEqual(outcomes[0], (1, "1 of its races still await recording"))
        self.assertFalse(self.ledger_dir.exists())

    def test_two_locks_of_the_same_archive_are_byte_identical(self):
        self.archive(season_races(), schedule())
        participants = self.build()
        other = self.root / "elsewhere"
        season_ratings.lock_seasons(self.archive_dir, self.ledger_dir, participants, LATE)
        season_ratings.lock_seasons(self.archive_dir, other, participants, LATE + timedelta(days=9))
        self.assertEqual(
            (self.ledger_dir / "season-1.json").read_bytes(), (other / "season-1.json").read_bytes()
        )

    def test_a_locked_season_survives_a_change_to_the_rating(self):
        self.archive(season_races(), schedule())
        participants = self.build()
        season_ratings.lock_seasons(self.archive_dir, self.ledger_dir, participants, LATE)

        update = ladder_rating._update_race

        def livelier(states, entries, **params):
            update(states, entries, **(params | {"beta": params["beta"] / 4}))

        with mock.patch.object(ladder_rating, "_update_race", livelier):
            changed = self.build()

        def points(payload, season):
            return [
                [entry[4:] for entry in race_row["e"]]
                for race_row in payload["races"]
                if race_row.get("s") == season
            ]

        for (locked, now) in zip(points(participants, 1), points(changed, 1)):
            for before, after in zip(locked, now):
                self.assertNotEqual(before[0], after[0])  # the all-time rating moved
                self.assertEqual(before[2:], after[2:])  # season 1 kept its ledger points
        self.assertNotEqual(
            [[entry[2:] for entry in race] for race in points(participants, 2)],
            [[entry[2:] for entry in race] for race in points(changed, 2)],
        )  # the open season 2 follows the new rating


if __name__ == "__main__":
    unittest.main()
