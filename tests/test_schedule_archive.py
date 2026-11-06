import io
import json
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import race_archive, schedule_archive


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
MODES = [
    {"id": 1, "archetype": 2, "name": "Open", "slug": "open", "active": True, "extra": "kept"},
]


def slot(slot_id, season, mode=1, race_id=None, time="1700000000"):
    return {"id": slot_id, "raceId": race_id, "season": season, "time": time, "mode": mode}


def make_fetcher(seasons, requested=None):
    def fetch(season):
        if requested is not None:
            requested.append(season)
        return seasons.get(season)

    return fetch


def no_sleep(_seconds):
    return None


class ScheduleTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.archive = Path(temp.name)
        self.directory = self.archive / schedule_archive.SCHEDULE_DIRECTORY

    def sync(self, seasons, modes=MODES, clock=NOW, requested=None):
        return schedule_archive.sync(
            self.archive,
            fetch_modes=lambda: modes,
            fetch_season=make_fetcher(seasons, requested),
            clock=lambda: clock,
            sleep=no_sleep,
        )

    def read(self, name):
        return json.loads((self.directory / name).read_text(encoding="utf-8"))

    def write_season(self, season, rows):
        race_archive.write_json_atomic(
            {"schema_version": 1, "fetched_at": "2026-11-03T12:00:00Z", "schedule": rows},
            self.directory / f"season-{season}.json",
        )

    def files(self):
        return sorted(path.name for path in self.directory.glob("*")) if self.directory.exists() else []


class SyncTests(ScheduleTestCase):
    def test_stops_at_first_404_and_never_requests_season_zero(self):
        requested = []
        result = self.sync({1: [slot(1, 1)], 2: [slot(2, 2), slot(3, 2)]}, requested=requested)
        self.assertEqual(requested, [1, 2, 3])
        self.assertEqual(result, schedule_archive.SyncResult(2, 3, 3))
        self.assertEqual(self.files(), ["modes.json", "season-1.json", "season-2.json"])

    def test_writes_envelopes_with_verbatim_bodies(self):
        rows = [dict(slot(1, 1, race_id=5), note="extra")]
        self.sync({1: rows})
        self.assertEqual(
            self.read("modes.json"),
            {"schema_version": 1, "fetched_at": "2026-11-03T12:00:00Z", "modes": MODES},
        )
        self.assertEqual(
            self.read("season-1.json"),
            {"schema_version": 1, "fetched_at": "2026-11-03T12:00:00Z", "schedule": rows},
        )
        text = (self.directory / "season-1.json").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertNotIn(" ", text.replace("2026-11-03T12:00:00Z", ""))

    def test_unchanged_data_touches_nothing(self):
        seasons = {1: [slot(1, 1)]}
        self.sync(seasons)
        result = self.sync(seasons, clock=LATER)
        self.assertEqual(result.files_changed, 0)
        self.assertEqual(self.read("season-1.json")["fetched_at"], "2026-11-03T12:00:00Z")
        self.assertEqual(self.read("modes.json")["fetched_at"], "2026-11-03T12:00:00Z")

    def test_changed_season_rewrites_only_that_file(self):
        self.sync({1: [slot(1, 1)], 2: [slot(2, 2)]})
        result = self.sync({1: [slot(1, 1)], 2: [slot(2, 2, race_id=9)]}, clock=LATER)
        self.assertEqual(result.files_changed, 1)
        self.assertEqual(self.read("season-1.json")["fetched_at"], "2026-11-03T12:00:00Z")
        self.assertEqual(self.read("season-2.json")["fetched_at"], "2026-11-04T12:00:00Z")
        self.assertEqual(self.read("season-2.json")["schedule"][0]["raceId"], 9)

    def test_never_deletes_files(self):
        self.sync({1: [slot(1, 1)], 2: [slot(2, 2)]})
        self.sync({1: [slot(1, 1)]})
        self.assertIn("season-2.json", self.files())

    def test_invalid_responses_raise_and_write_nothing(self):
        bad_rows = {
            "bool mode": [slot(2, 2, mode=True)],
            "non-digit time": [slot(2, 2, time="12ab")],
            "empty time": [slot(2, 2, time="")],
            "wrong season": [slot(2, 3)],
            "bad race id": [dict(slot(2, 2), raceId="7")],
            "not an object": ["row"],
            "non-list body": {"id": 2},
        }
        for label, body in bad_rows.items():
            with self.subTest(label), self.assertRaises(race_archive.ArchiveError):
                self.sync({1: [slot(1, 1)], 2: body})
            self.assertEqual(self.files(), [], label)

    def test_invalid_modes_raise_and_write_nothing(self):
        for modes in ({"id": 1}, [{"id": 1, "archetype": 1, "name": "x", "active": 1}]):
            with self.subTest(modes), self.assertRaises(race_archive.ArchiveError):
                self.sync({1: [slot(1, 1)]}, modes=modes)
            self.assertEqual(self.files(), [])

    def test_invalid_json_raises_and_writes_nothing(self):
        def get_bytes(url, label, accept, limit=None):
            return b"<html>not json</html>"

        with patch.object(race_archive, "_get_bytes", get_bytes):
            with self.assertRaises(race_archive.ArchiveError):
                schedule_archive.fetch_season(1)
            with self.assertRaises(race_archive.ArchiveError):
                schedule_archive.sync(self.archive, fetch_modes=lambda: MODES, sleep=no_sleep)
        self.assertEqual(self.files(), [])


class FetchSeasonTests(unittest.TestCase):
    def http_error(self, code):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO())

    def failing_get(self, code):
        def get_bytes(url, label, accept, limit=None):
            raise race_archive.ArchiveError(f"{label} failed") from self.http_error(code)

        return get_bytes

    def test_404_maps_to_none(self):
        with patch.object(race_archive, "_get_bytes", self.failing_get(404)):
            self.assertIsNone(schedule_archive.fetch_season(11))

    def test_other_errors_are_raised(self):
        with patch.object(race_archive, "_get_bytes", self.failing_get(500)):
            with self.assertRaises(race_archive.ArchiveError):
                schedule_archive.fetch_season(1)

    def test_parses_body_and_uses_season_url(self):
        urls = []

        def get_bytes(url, label, accept, limit=None):
            urls.append(url)
            return json.dumps([slot(1, 1)]).encode()

        with patch.object(race_archive, "_get_bytes", get_bytes):
            self.assertEqual(schedule_archive.fetch_season(1), [slot(1, 1)])
        self.assertEqual(urls, ["https://alttpr.racing/api/v1/schedule/1"])


class LoaderTests(ScheduleTestCase):
    def test_missing_directory_gives_empty_lists(self):
        self.assertEqual(schedule_archive.load_schedule(self.archive), [])
        self.assertEqual(schedule_archive.load_modes(self.archive), [])

    def test_modes_round_trip(self):
        self.sync({1: [slot(1, 1)]})
        self.assertEqual(schedule_archive.load_modes(self.archive), MODES)

    def test_schedule_round_trip_in_numeric_season_order(self):
        seasons = {n: [slot(n * 10, n), slot(n * 10 + 1, n)] for n in range(1, 11)}
        self.sync(seasons)
        loaded = schedule_archive.load_schedule(self.archive)
        self.assertEqual([row["season"] for row in loaded], [n for n in range(1, 11) for _ in (0, 1)])
        self.assertEqual(loaded[2:4], seasons[2])

    def test_orders_season_2_before_season_10(self):
        self.write_season(10, [slot(100, 10)])
        self.write_season(2, [slot(20, 2)])
        ids = [row["id"] for row in schedule_archive.load_schedule(self.archive)]
        self.assertEqual(ids, [20, 100])

    def test_identical_duplicate_ids_are_kept_once(self):
        self.write_season(1, [slot(1, 1), slot(1, 1)])
        self.assertEqual(schedule_archive.load_schedule(self.archive), [slot(1, 1)])

    def test_same_time_different_ids_are_both_kept(self):
        self.write_season(1, [slot(1, 1), slot(2, 1)])
        self.assertEqual(len(schedule_archive.load_schedule(self.archive)), 2)

    def test_conflicting_duplicate_ids_raise(self):
        self.write_season(1, [slot(1, 1, mode=1), slot(1, 1, mode=2)])
        with self.assertRaises(race_archive.ArchiveError):
            schedule_archive.load_schedule(self.archive)

    def test_malformed_files_raise(self):
        self.directory.mkdir(parents=True)
        path = self.directory / "season-1.json"
        for text in ("not json", "[]", '{"schema_version":2,"fetched_at":"2026-11-03T12:00:00Z","schedule":[]}',
                     '{"schema_version":1,"fetched_at":"bad","schedule":[]}'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text), self.assertRaises(race_archive.ArchiveError):
                schedule_archive.load_schedule(self.archive)
        (self.directory / schedule_archive.MODES_FILE).write_text("[]", encoding="utf-8")
        with self.assertRaises(race_archive.ArchiveError):
            schedule_archive.load_modes(self.archive)


JOIN_AT = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
JOIN_MODES = [
    {"id": 11, "archetype": 1, "name": "Simple", "active": True},
    {"id": 41, "archetype": 4, "name": "Keys", "active": True},
    {"id": 80, "archetype": 8, "name": "Odd", "active": True},
]


def timed_slot(slot_id, race_id, started_at, mode=11):
    return {
        "id": slot_id,
        "raceId": race_id,
        "season": 1,
        "time": str(int(started_at.timestamp())),
        "mode": mode,
    }


def context(name, mode, started_at, seed=None):
    info = f"Step Ladder Series - [{mode}] - Open"
    if seed is not None:
        info += f"\nopen - https://alttpr.racing/getseed.php?race={seed} - (x)"
    return {
        "race": {"name": name, "info": info},
        "started_at": started_at,
        "mode": mode,
        "entrants_count": 5,
    }


class JoinTests(unittest.TestCase):
    def join(self, rows, contexts, excluded=frozenset()):
        return schedule_archive.join_races(rows, JOIN_MODES, contexts, set(excluded))

    def test_reads_the_seed_number_from_a_race_info(self):
        self.assertEqual(schedule_archive.seed_race_id(context("a", "keys", JOIN_AT, 42)["race"]["info"]), 42)
        self.assertIsNone(schedule_archive.seed_race_id("Step Ladder Series - [keys] - Open"))
        self.assertIsNone(schedule_archive.seed_race_id(None))

    def test_seed_join_ignores_category_and_time(self):
        race = context("seeded", "entrance", JOIN_AT + timedelta(hours=3), seed=42)
        (joined,) = self.join([timed_slot(1, 42, JOIN_AT)], [race])
        self.assertIs(joined.race, race)
        self.assertEqual((joined.category, joined.pulled), ("simple", False))
        self.assertEqual(joined.started_at, JOIN_AT)

    def test_time_fallback_is_five_minutes_early_to_thirty_late(self):
        for minutes, joins in ((-5, True), (-6, False), (30, True), (31, False)):
            with self.subTest(minutes=minutes):
                race = context("loose", "simple", JOIN_AT + timedelta(minutes=minutes))
                (joined,) = self.join([timed_slot(1, 500, JOIN_AT)], [race])
                self.assertEqual(joined.race is race, joins)

    def test_time_fallback_needs_the_slot_category_and_no_seed(self):
        other_category = context("entrance", "entrance", JOIN_AT)
        seeded = context("seeded", "simple", JOIN_AT, seed=7)
        (joined,) = self.join([timed_slot(1, 500, JOIN_AT)], [other_category, seeded])
        self.assertIsNone(joined.race)

    def test_a_race_without_a_seed_joins_only_one_slot(self):
        race = context("loose", "simple", JOIN_AT + timedelta(minutes=5))
        joined = self.join(
            [timed_slot(2, 502, JOIN_AT + timedelta(minutes=10)), timed_slot(1, 501, JOIN_AT)],
            [race],
        )
        self.assertEqual([slot.row["id"] for slot in joined], [1, 2])
        self.assertEqual([slot.race is race for slot in joined], [True, False])

    def test_orders_by_time_then_id(self):
        rows = [
            timed_slot(3, 503, JOIN_AT + timedelta(hours=1)),
            timed_slot(2, 502, JOIN_AT),
            timed_slot(1, 501, JOIN_AT),
        ]
        self.assertEqual([slot.row["id"] for slot in self.join(rows, [])], [1, 2, 3])

    def test_drops_slots_of_excluded_races(self):
        joined = self.join(
            [timed_slot(1, 77, JOIN_AT), timed_slot(2, 78, JOIN_AT)], [], excluded={77}
        )
        self.assertEqual([slot.row["id"] for slot in joined], [2])

    def test_a_slot_without_race_id_or_race_is_pulled(self):
        (pulled,) = self.join([timed_slot(1, None, JOIN_AT)], [])
        self.assertTrue(pulled.pulled)
        race = context("loose", "simple", JOIN_AT)
        (fired,) = self.join([timed_slot(1, None, JOIN_AT)], [race])
        self.assertIs(fired.race, race)
        self.assertFalse(fired.pulled)
        (never_started,) = self.join([timed_slot(1, 999, JOIN_AT)], [])
        self.assertFalse(never_started.pulled)
        self.assertIsNone(never_started.race)

    def test_unlisted_mode_and_unmapped_archetype_have_no_category(self):
        seeded = context("odd", "fun", JOIN_AT, seed=42)
        unlisted, odd = self.join(
            [timed_slot(1, 500, JOIN_AT, mode=12345), timed_slot(2, 42, JOIN_AT, mode=80)],
            [seeded],
        )
        self.assertIsNone(unlisted.mode)
        self.assertIsNone(unlisted.category)
        self.assertEqual(odd.mode["archetype"], 8)
        self.assertIsNone(odd.category)
        self.assertIs(odd.race, seeded)


if __name__ == "__main__":
    unittest.main()
