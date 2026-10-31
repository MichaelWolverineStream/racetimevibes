import json
import math
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import race_archive


NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
OLD_FETCH = "2026-10-01T00:00:00Z"


def days_ago(days):
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def race(slug, *, ended_days_ago, recorded=True, recordable=True, comment=None):
    return {
        "name": f"alttpr-ladder/{slug}",
        "status": {"value": "finished"},
        "started_at": days_ago(ended_days_ago + 0.1),
        "ended_at": days_ago(ended_days_ago),
        "cancelled_at": None,
        "recordable": recordable,
        "recorded": recorded,
        "entrants": [{"user": {"id": f"{slug}-racer"}, "comment": comment}],
    }


def page(races, *, count=None, num_pages=1):
    return {
        "count": len(races) if count is None else count,
        "num_pages": num_pages,
        "races": races,
    }


def leaderboards(*users):
    return {
        "leaderboards": [
            {
                "goal": "Beat the game (Simple)",
                "rankings": [{"user": {"id": user_id, "name": name}} for user_id, name in users],
            }
        ]
    }


class Listing:
    """Serve races newest first in fixed-size pages and record each requested page."""

    def __init__(self, races, *, per_page=2):
        self.races = list(races)
        self.per_page = per_page
        self.calls = []

    def __call__(self, page_number):
        self.calls.append(page_number)
        start = (page_number - 1) * self.per_page
        return page(
            self.races[start : start + self.per_page],
            count=len(self.races),
            num_pages=max(1, math.ceil(len(self.races) / self.per_page)),
        )


def archive_race(archive_dir, race_record, *, fetched_at=OLD_FETCH):
    path = race_archive.race_path(archive_dir, race_record["name"])
    race_archive.write_json_atomic(
        {"schema_version": 1, "fetched_at": fetched_at, "race": race_record}, path
    )
    return path


def run_sync(archive_dir, page_loader, names=None):
    with patch.object(race_archive.time, "sleep"):
        return race_archive.sync(
            archive_dir,
            page_loader=page_loader,
            leaderboards_loader=lambda: names or leaderboards(),
            clock=lambda: NOW,
        )


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive_dir = Path(directory.name) / "archive"

    def archived_names(self):
        return sorted(
            envelope["race"]["name"]
            for envelope in race_archive.load_races(self.archive_dir)
        )


class FetchTests(unittest.TestCase):
    @patch.object(race_archive.json, "load", return_value={"races": []})
    @patch.object(race_archive, "urlopen")
    def test_fetch_requests_entrant_details(self, mocked_urlopen, _json_load):
        race_archive.fetch_page(3)

        request = mocked_urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["page"], ["3"])
        self.assertEqual(query["per_page"], ["100"])
        self.assertEqual(query["show_entrants"], ["true"])

    @patch.object(race_archive.time, "sleep")
    @patch.object(race_archive.json, "load", return_value={"races": []})
    @patch.object(race_archive, "urlopen")
    def test_fetch_retries_connection_resets(
        self, mocked_urlopen, _json_load, mocked_sleep
    ):
        mocked_urlopen.side_effect = [ConnectionResetError(), mocked_urlopen.return_value]

        self.assertEqual(race_archive.fetch_page(1), {"races": []})
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(1)

    @patch.object(race_archive.time, "sleep")
    @patch.object(race_archive, "urlopen")
    def test_fetch_retries_truncated_responses(self, mocked_urlopen, _sleep):
        with patch.object(
            race_archive.json,
            "load",
            side_effect=[IncompleteRead(b"{", 10), {"leaderboards": []}],
        ):
            self.assertEqual(race_archive.fetch_leaderboards(), {"leaderboards": []})

        self.assertEqual(
            mocked_urlopen.call_args.args[0].full_url, race_archive.LEADERBOARDS_URL
        )
        self.assertEqual(mocked_urlopen.call_count, 2)


class ListingValidationTests(ArchiveTestCase):
    def test_rejects_invalid_pagination_metadata(self):
        with self.assertRaisesRegex(race_archive.ArchiveError, "pagination"):
            run_sync(
                self.archive_dir,
                lambda _page: {"count": "1", "num_pages": 1, "races": []},
            )

    def test_rejects_missing_completion_timestamp(self):
        missing = race("missing-terminal", ended_days_ago=1) | {"ended_at": None}

        with self.assertRaisesRegex(race_archive.ArchiveError, "completion timestamp"):
            run_sync(self.archive_dir, Listing([missing]))

    def test_rejects_completion_order_violation(self):
        unordered = [race("older", ended_days_ago=2), race("newer", ended_days_ago=1)]

        with self.assertRaisesRegex(race_archive.ArchiveError, "order"):
            run_sync(self.archive_dir, Listing(unordered))
        self.assertEqual(self.archived_names(), [])

    def test_rejects_unsafe_race_names_without_writing(self):
        for name in (
            "alttpr-ladder/../../escape",
            "other-category/quiet-race-0001",
            "alttpr-ladder/Upper-Case-0001",
            "alttpr-ladder/trailing-newline-0001\n",
            None,
        ):
            with self.subTest(name=name):
                unsafe = race("placeholder", ended_days_ago=1) | {"name": name}
                with self.assertRaisesRegex(race_archive.ArchiveError, "race slug"):
                    run_sync(self.archive_dir, Listing([unsafe]))
                self.assertFalse((self.archive_dir / "races").exists())


class SyncTests(ArchiveTestCase):
    def test_backfills_every_page_into_a_missing_archive(self):
        listing = Listing(
            [race(f"race-{index}", ended_days_ago=index) for index in range(5)]
        )

        summary = run_sync(self.archive_dir, listing, leaderboards(("u1", "Ana")))

        self.assertEqual(listing.calls, [1, 2, 3])
        self.assertEqual(summary["new_count"], 5)
        self.assertEqual((summary["archive_count"], summary["api_count"]), (5, 5))
        self.assertFalse(summary["full_scan"])
        path = self.archive_dir / "races" / "alttpr-ladder" / "race-0.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            envelope,
            {
                "schema_version": 1,
                "fetched_at": "2026-10-27T12:00:00Z",
                "race": race("race-0", ended_days_ago=0),
            },
        )
        self.assertEqual(race_archive.load_racer_names(self.archive_dir), {"u1": "Ana"})

    def test_reads_to_the_new_last_page_when_the_listing_grows(self):
        races = [race(f"race-{index}", ended_days_ago=index) for index in range(5)]
        listing = Listing(races[1:])

        def load(page_number):
            if page_number == 2:
                listing.races.insert(0, races[0])
            return listing(page_number)

        summary = run_sync(self.archive_dir, load)

        self.assertEqual(listing.calls, [1, 2, 3])
        self.assertEqual(self.archived_names(), [item["name"] for item in races[1:]])
        self.assertFalse(summary["full_scan"])

    def test_empty_page_ends_the_pass(self):
        calls = []

        def load(page_number):
            calls.append(page_number)
            if page_number == 1:
                return page([race("only", ended_days_ago=1)], count=1, num_pages=3)
            return page([], count=1, num_pages=3)

        run_sync(self.archive_dir, load)

        self.assertEqual(calls, [1, 2])

    def test_incremental_sync_stops_at_the_horizon_and_keeps_settled_files(self):
        settled = [
            race("settled-a", ended_days_ago=3),
            race("settled-b", ended_days_ago=4.5),
            race("settled-c", ended_days_ago=5),
            race("settled-d", ended_days_ago=6),
        ]
        paths = [archive_race(self.archive_dir, item) for item in settled]
        before = [path.read_bytes() for path in paths]
        listing = Listing(
            [
                race("new-1", ended_days_ago=0.1),
                race("new-2", ended_days_ago=0.2),
                race("settled-a", ended_days_ago=3, comment="edited later"),
                *settled[1:],
            ],
            per_page=4,
        )

        summary = run_sync(self.archive_dir, listing)

        self.assertEqual(listing.calls, [1])
        self.assertEqual((summary["new_count"], summary["refreshed_count"]), (2, 0))
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertIn("alttpr-ladder/new-1", self.archived_names())

    def test_keeps_reading_until_a_page_passes_the_horizon(self):
        settled = [
            race("settled-a", ended_days_ago=3),
            race("settled-b", ended_days_ago=4.5),
            race("settled-c", ended_days_ago=5),
            race("settled-d", ended_days_ago=6),
            race("settled-e", ended_days_ago=7),
        ]
        for item in settled:
            archive_race(self.archive_dir, item)
        listing = Listing(
            [
                race("new-1", ended_days_ago=0.1),
                race("new-2", ended_days_ago=0.2),
                race("new-3", ended_days_ago=0.3),
                *settled,
            ]
        )

        summary = run_sync(self.archive_dir, listing)

        self.assertEqual(listing.calls, [1, 2, 3])
        self.assertEqual(summary["new_count"], 3)

    def test_refreshes_unsettled_race_until_it_is_recorded(self):
        path = archive_race(
            self.archive_dir, race("pending", ended_days_ago=1, recorded=False)
        )
        archive_race(self.archive_dir, race("settled", ended_days_ago=2))
        recorded = race("pending", ended_days_ago=1, comment="gg")

        summary = run_sync(
            self.archive_dir,
            Listing([recorded, race("settled", ended_days_ago=2)]),
        )

        self.assertEqual(summary["refreshed_count"], 1)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(envelope["race"], recorded)
        self.assertEqual(envelope["fetched_at"], "2026-10-27T12:00:00Z")

        frozen = path.read_bytes()
        summary = run_sync(
            self.archive_dir,
            Listing(
                [
                    race("pending", ended_days_ago=1, comment="edited"),
                    race("settled", ended_days_ago=2),
                ]
            ),
        )

        self.assertEqual(summary["refreshed_count"], 0)
        self.assertEqual(path.read_bytes(), frozen)

    def test_unchanged_unsettled_race_is_not_rewritten(self):
        pending = race("pending", ended_days_ago=1, recorded=False)
        path = archive_race(self.archive_dir, pending)
        before = path.read_bytes()

        summary = run_sync(self.archive_dir, Listing([pending]))

        self.assertEqual(summary["refreshed_count"], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_unrecorded_race_past_the_settle_limit_is_frozen(self):
        stale = race("stale", ended_days_ago=20, recorded=False)
        stale_path = archive_race(self.archive_dir, stale)
        before = stale_path.read_bytes()
        for item in (
            race("recent", ended_days_ago=1),
            race("middle", ended_days_ago=10),
        ):
            archive_race(self.archive_dir, item)
        listing = Listing(
            [
                race("recent", ended_days_ago=1),
                race("middle", ended_days_ago=10),
                race("stale", ended_days_ago=20, comment="late"),
            ],
            per_page=1,
        )

        summary = run_sync(self.archive_dir, listing)

        self.assertEqual(listing.calls, [1, 2])
        self.assertEqual(summary["refreshed_count"], 0)
        self.assertEqual(stale_path.read_bytes(), before)

    def test_reports_unsettled_race_that_is_no_longer_listed(self):
        path = archive_race(
            self.archive_dir, race("gone", ended_days_ago=1, recorded=False)
        )
        archive_race(self.archive_dir, race("settled", ended_days_ago=3))

        summary = run_sync(
            self.archive_dir, Listing([race("settled", ended_days_ago=3)])
        )

        self.assertEqual(summary["vanished"], ["alttpr-ladder/gone"])
        self.assertTrue(path.exists())

    def test_full_scan_repairs_a_gap(self):
        for item in (
            race("newest", ended_days_ago=1),
            race("overlap", ended_days_ago=2.5),
            race("oldest", ended_days_ago=5),
        ):
            archive_race(self.archive_dir, item)
        listing = Listing(
            [
                race("newest", ended_days_ago=1),
                race("overlap", ended_days_ago=2.5),
                race("missing", ended_days_ago=3),
                race("oldest", ended_days_ago=5),
            ],
            per_page=1,
        )

        summary = run_sync(self.archive_dir, listing)

        self.assertEqual(listing.calls, [1, 2, 1, 2, 3, 4])
        self.assertTrue(summary["full_scan"])
        self.assertEqual(summary["new_count"], 1)
        self.assertIn("alttpr-ladder/missing", self.archived_names())

    def test_persistent_gap_fails_without_writing(self):
        def load(_page_number):
            return page([race("listed", ended_days_ago=1)], count=2)

        with self.assertRaisesRegex(race_archive.ArchiveError, "API lists 2 races"):
            run_sync(self.archive_dir, load)
        self.assertEqual(self.archived_names(), [])

    def test_writes_oldest_race_first(self):
        listing = Listing(
            [race(f"race-{index}", ended_days_ago=index) for index in range(3)]
        )
        with patch.object(
            race_archive, "write_json_atomic", wraps=race_archive.write_json_atomic
        ) as mocked_write:
            run_sync(self.archive_dir, listing)

        written = [
            call.args[1].stem
            for call in mocked_write.call_args_list
            if call.args[1].parent.name == "alttpr-ladder"
        ]
        self.assertEqual(written, ["race-2", "race-1", "race-0"])


class LoadTests(ArchiveTestCase):
    def test_missing_archive_is_empty(self):
        self.assertEqual(race_archive.load_races(self.archive_dir), [])
        self.assertEqual(race_archive.load_racer_names(self.archive_dir), {})

    def test_returns_newest_first_and_ignores_temporary_files(self):
        archive_race(self.archive_dir, race("older", ended_days_ago=2))
        newer_path = archive_race(self.archive_dir, race("newer", ended_days_ago=1))
        newer_path.with_suffix(".json.tmp").write_text("{", encoding="utf-8")

        envelopes = race_archive.load_races(self.archive_dir)

        self.assertEqual(
            [envelope["race"]["name"] for envelope in envelopes],
            ["alttpr-ladder/newer", "alttpr-ladder/older"],
        )

    def test_rejects_race_stored_under_another_name(self):
        path = race_archive.race_path(self.archive_dir, "alttpr-ladder/other")
        race_archive.write_json_atomic(
            {
                "schema_version": 1,
                "fetched_at": OLD_FETCH,
                "race": race("actual", ended_days_ago=1),
            },
            path,
        )

        with self.assertRaisesRegex(race_archive.ArchiveError, "does not match its path"):
            race_archive.load_races(self.archive_dir)

    def test_writes_compact_json_atomically(self):
        output_path = self.archive_dir / "nested" / "stats.json"
        race_archive.write_json_atomic({"hourly": [1, 2], "label": "a b"}, output_path)

        self.assertEqual(
            output_path.read_text(encoding="utf-8"), '{"hourly":[1,2],"label":"a b"}\n'
        )
        self.assertFalse(output_path.with_suffix(".json.tmp").exists())


class RacerNameTests(ArchiveTestCase):
    def test_merges_leaderboard_names_without_dropping_racers(self):
        race_archive.write_json_atomic(
            {"u1": "Old name", "u2": "Retired"},
            self.archive_dir / race_archive.RACER_NAMES_FILE,
        )

        counts = race_archive.sync_racer_names(
            self.archive_dir, leaderboards(("u3", " Fresh "), ("u1", "New name"))
        )

        self.assertEqual(counts, (1, 1))
        self.assertEqual(
            race_archive.load_racer_names(self.archive_dir),
            {"u1": "New name", "u2": "Retired", "u3": "Fresh"},
        )

    def test_unchanged_names_are_not_rewritten(self):
        race_archive.sync_racer_names(self.archive_dir, leaderboards(("u1", "Ana")))

        with patch.object(race_archive, "write_json_atomic") as mocked_write:
            counts = race_archive.sync_racer_names(
                self.archive_dir, leaderboards(("u1", "Ana"))
            )

        self.assertEqual(counts, (0, 0))
        mocked_write.assert_not_called()

    def test_rejects_malformed_leaderboards(self):
        for payload in (
            {},
            {"leaderboards": [{"goal": "no rankings"}]},
            {"leaderboards": [{"rankings": [{"user": None}]}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(race_archive.ArchiveError):
                    race_archive.sync_racer_names(self.archive_dir, payload)


class CommittedArchiveTests(unittest.TestCase):
    def test_committed_archive_is_valid(self):
        envelopes = race_archive.load_races()
        self.assertTrue(envelopes)
        names = race_archive.load_racer_names()
        self.assertTrue(names)

    def test_committed_archive_is_compact(self):
        archive_dir = race_archive.ARCHIVE_DIR
        files = [
            (race_archive.race_path(archive_dir, envelope["race"]["name"]), envelope)
            for envelope in race_archive.load_races()
        ]
        files.append(
            (
                archive_dir / race_archive.RACER_NAMES_FILE,
                race_archive.load_racer_names(),
            )
        )
        stale = [
            path.name
            for path, payload in files
            if path.read_text(encoding="utf-8")
            != json.dumps(payload, separators=(",", ":")) + "\n"
        ]
        self.assertEqual(
            stale, [], "rewrite these files with race_archive.write_json_atomic"
        )


if __name__ == "__main__":
    unittest.main()
