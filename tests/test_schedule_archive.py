import io
import json
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime
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


if __name__ == "__main__":
    unittest.main()
