import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import race_archive, racer_bios


PROFILE_HEAD = """<main>
    <h3>View user profile</h3>
    <div class="user-profile">
    <span class="user-pop">
        <span class="avatar" style="background-image: url(https://racetime.gg/media/x.png)"></span>
        <span class="name">Testracer</span>
        <span class="scrim">#0001</span>
    </span>
        <div class="role-badge team-badge">
            <span><a href="/team/test-team">Test Team</a></span>
        </div>
"""
PROFILE_TAIL = """    </div>
    <h3>Race results</h3>
    <div class="user-races race-list"><p>No races</p></div>
</main>"""
NO_BIO_PAGE = PROFILE_HEAD + PROFILE_TAIL
BIO_PAGE = (
    PROFILE_HEAD
    + '        <p class="bio">I don&#x27;t stream &amp; race.\n'
    + 'My favourite pokemon is: Mew, see <a href="https://x.example" rel="nofollow">'
    + "https://x.example</a></p>\n"
    + PROFILE_TAIL
)
CHALLENGE_PAGE = "<html><body><h1>Just a moment...</h1><p class=\"bio\">nope</p></body></html>"
LATER = "2026-10-05T00:00:00Z"
EARLIER = "2026-09-01T00:00:00Z"


def envelope(slug, users, *, ended_at="2026-10-01T14:00:00.000Z", mode="simple"):
    """A finished race file whose entrants are the given user IDs (None is unidentified)."""
    return {
        "schema_version": race_archive.ARCHIVE_SCHEMA_VERSION,
        "fetched_at": "2026-10-02T00:00:00Z",
        "race": {
            "name": f"alttpr-ladder/{slug}",
            "status": {"value": "finished"},
            "info": f"Step Ladder Series - [{mode}] Test preset",
            "entrants_count": len(users),
            "started_at": "2026-10-01T12:00:00.000Z",
            "ended_at": ended_at,
            "entrants": [
                {"user": None if user is None else {"id": user, "name": user}}
                for user in users
            ],
        },
    }


def write_race(archive, slug, users, **kwargs):
    race = envelope(slug, users, **kwargs)
    race_archive.write_json_atomic(race, race_archive.race_path(archive, race["race"]["name"]))
    return race


class FakeLoader:
    """Serves pages by profile URL; an Exception value is raised, None means a 404."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        page = self.pages.get(url.rsplit("/", 1)[1], NO_BIO_PAGE)
        if isinstance(page, Exception):
            raise page
        return page.encode() if isinstance(page, str) else page


class FixedClock:
    def __init__(self, *moments):
        self.moments = list(moments)

    def __call__(self):
        return self.moments.pop(0) if len(self.moments) > 1 else self.moments[0]


class ExtractBioTests(unittest.TestCase):
    def test_returns_the_bio_text_with_links_escapes_and_newlines(self):
        self.assertEqual(
            racer_bios.extract_bio(BIO_PAGE),
            "I don't stream & race.\nMy favourite pokemon is: Mew, see https://x.example",
        )

    def test_a_profile_without_a_bio_gives_none(self):
        self.assertIsNone(racer_bios.extract_bio(NO_BIO_PAGE))

    def test_a_blank_bio_gives_none(self):
        page = PROFILE_HEAD + '<p class="bio">  \n </p>' + PROFILE_TAIL
        self.assertIsNone(racer_bios.extract_bio(page))

    def test_only_the_first_bio_paragraph_counts_and_it_ends_at_its_own_end_tag(self):
        page = PROFILE_HEAD + '<p class="bio">one</p><p class="bio">two</p>' + PROFILE_TAIL
        self.assertEqual(racer_bios.extract_bio(page), "one")

    def test_a_page_that_is_not_a_profile_is_rejected(self):
        with self.assertRaises(racer_bios.ProfileError):
            racer_bios.extract_bio(CHALLENGE_PAGE)

    def test_a_long_bio_is_truncated(self):
        page = PROFILE_HEAD + '<p class="bio">' + "a" * 2500 + "</p>" + PROFILE_TAIL
        self.assertEqual(racer_bios.extract_bio(page), "a" * racer_bios.MAX_BIO_CHARS)


class LoadRacerBiosTests(unittest.TestCase):
    def load(self, text=None):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            if text is not None:
                (archive / racer_bios.RACER_BIOS_FILE).write_text(text, encoding="utf-8")
            return racer_bios.load_racer_bios(archive)

    def test_a_missing_file_gives_an_empty_map(self):
        self.assertEqual(self.load(), {})

    def test_a_valid_file_is_returned(self):
        data = {"a": ["hello", EARLIER, None, []], "b": [None, LATER, EARLIER, []]}
        self.assertEqual(self.load(json.dumps(data)), data)

    def test_a_legacy_entry_loads_in_the_four_element_form(self):
        data = {"a": ["hello", EARLIER], "b": [None, LATER]}
        self.assertEqual(
            self.load(json.dumps(data)),
            {"a": ["hello", EARLIER, None, []], "b": [None, LATER, None, []]},
        )

    def test_the_history_form_round_trips(self):
        data = {
            "a": ["new", LATER, LATER, [["first", None], ["second", EARLIER], [None, EARLIER]]],
            "b": ["now", LATER, EARLIER, [[None, None]]],
        }
        self.assertEqual(self.load(json.dumps(data)), data)

    def test_malformed_history_is_rejected(self):
        for entry in (
            # A None time after earlier[0].
            ["x", LATER, LATER, [["a", EARLIER], ["b", None]]],
            # A None since with a non-empty history.
            ["x", LATER, None, [["a", EARLIER]]],
            # Bad timestamps anywhere.
            ["x", LATER, "yesterday", []],
            ["x", LATER, LATER, [["a", "yesterday"]]],
            ["x", "yesterday", LATER, []],
            # Bad shapes.
            ["x", LATER, LATER, "none"],
            ["x", LATER, LATER, [["a"]]],
            ["x", LATER, LATER, [[1, EARLIER]]],
            ["x", LATER, LATER, ["a"]],
        ):
            with self.subTest(entry=entry), self.assertRaises(race_archive.ArchiveError):
                self.load(json.dumps({"a": entry}))

    def test_malformed_files_are_rejected(self):
        for text in (
            "not json",
            "[]",
            '{"a": "x"}',
            f'{{"a": ["x"]}}',
            f'{{"a": [1, "{EARLIER}"]}}',
            f'{{"a": ["x", "{EARLIER}", 1]}}',
        ):
            with self.subTest(text=text), self.assertRaises(race_archive.ArchiveError):
                self.load(text)

    def test_a_bad_time_is_rejected(self):
        for stamp in ("yesterday", None, "2026-10-01T00:00:00"):
            with self.subTest(stamp=stamp), self.assertRaises(race_archive.ArchiveError):
                self.load(json.dumps({"a": ["x", stamp]}))


class RacersToCheckTests(unittest.TestCase):
    def test_a_racer_without_an_entry_is_due(self):
        races = [envelope("a-1", ["u2", "u1"])]
        self.assertEqual(racer_bios.racers_to_check(races, {}), ["u1", "u2"])

    def test_a_racer_who_raced_after_the_check_is_due(self):
        races = [envelope("a-1", ["u1"])]
        bios = {"u1": [None, "2026-10-01T13:00:00Z"]}
        self.assertEqual(racer_bios.racers_to_check(races, bios), ["u1"])

    def test_a_racer_who_raced_before_the_check_is_not_due(self):
        races = [envelope("a-1", ["u1"])]
        self.assertEqual(racer_bios.racers_to_check(races, {"u1": ["x", LATER]}), [])

    def test_the_latest_race_decides(self):
        races = [
            envelope("a-1", ["u1"], ended_at="2026-09-01T00:00:00Z"),
            envelope("a-2", ["u1"], ended_at="2026-10-09T00:00:00Z"),
        ]
        self.assertEqual(racer_bios.racers_to_check(races, {"u1": ["x", LATER]}), ["u1"])

    def test_unidentified_entrants_are_skipped(self):
        races = [envelope("a-1", [None, "u1"])]
        races[0]["race"]["entrants"].append({"user": {"id": ""}})
        races[0]["race"]["entrants"].append({})
        self.assertEqual(racer_bios.racers_to_check(races, {}), ["u1"])

    def test_excluded_races_are_ignored(self):
        races = [envelope("a-1", ["u1"], mode="ladder_test")]
        self.assertEqual(racer_bios.racers_to_check(races, {}), [])


class FetchProfileTests(unittest.TestCase):
    def test_a_404_gives_none(self):
        error = race_archive.ArchiveError("gone")
        error.__cause__ = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(race_archive, "_get_bytes", side_effect=error):
            self.assertIsNone(racer_bios.fetch_profile("https://racetime.gg/user/x"))

    def test_other_failures_are_raised(self):
        error = race_archive.ArchiveError("busy")
        error.__cause__ = urllib.error.HTTPError("u", 503, "Busy", {}, None)
        with patch.object(race_archive, "_get_bytes", side_effect=error):
            with self.assertRaises(race_archive.ArchiveError):
                racer_bios.fetch_profile("https://racetime.gg/user/x")

    def test_the_body_is_returned(self):
        with patch.object(race_archive, "_get_bytes", return_value=b"hi") as get:
            self.assertEqual(racer_bios.fetch_profile("https://racetime.gg/user/x"), b"hi")
        self.assertEqual(get.call_args.kwargs, {"limit": racer_bios.MAX_PROFILE_BYTES})


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Path(self.directory.name)
        self.path = self.archive / racer_bios.RACER_BIOS_FILE
        sleep = patch.object(racer_bios.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def run_sync(self, loader, clock=None):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            summary = racer_bios.sync(self.archive, loader=loader, clock=clock)
        self.warnings = stderr.getvalue()
        return summary

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_stores_bios_and_timestamps_sorted_and_minified(self):
        write_race(self.archive, "a-1", ["u2", "u1"])
        loader = FakeLoader({"u1": BIO_PAGE})
        clock = FixedClock(
            datetime(2026, 11, 2, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 2, 10, 0, 5, tzinfo=UTC),
        )
        summary = self.run_sync(loader, clock)
        self.assertEqual(summary, {"due": 2, "checked": 2, "failed": 0, "stopped": False})
        self.assertEqual(
            loader.urls,
            ["https://racetime.gg/user/u1", "https://racetime.gg/user/u2"],
        )
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            '{"u1":["I don\'t stream & race.\\nMy favourite pokemon is: Mew, see '
            'https://x.example","2026-11-02T10:00:00Z","2026-11-02T10:00:00Z",[]],'
            '"u2":[null,"2026-11-02T10:00:05Z","2026-11-02T10:00:05Z",[]]}\n',
        )

    def test_a_404_stores_null(self):
        write_race(self.archive, "a-1", ["u1"])
        summary = self.run_sync(lambda url: None, FixedClock(datetime(2026, 11, 2, tzinfo=UTC)))
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(
            self.stored(), {"u1": [None, "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z", []]}
        )

    def test_a_failed_fetch_keeps_the_old_entry_and_stays_due(self):
        write_race(self.archive, "a-1", ["u1", "u2"])
        race_archive.write_json_atomic({"u1": ["old", EARLIER]}, self.path)
        loader = FakeLoader({"u1": race_archive.ArchiveError("HTTP 500"), "u2": BIO_PAGE})
        summary = self.run_sync(loader)
        self.assertEqual(summary, {"due": 2, "checked": 1, "failed": 1, "stopped": False})
        self.assertIn("Warning: HTTP 500; skipping u1.", self.warnings)
        self.assertEqual(self.stored()["u1"], ["old", EARLIER, None, []])
        self.assertIn("u2", self.stored())
        due = racer_bios.racers_to_check(
            race_archive.load_races(self.archive), racer_bios.load_racer_bios(self.archive)
        )
        self.assertEqual(due, ["u1"])

    def test_an_unchanged_bio_keeps_since_and_earlier_but_updates_checked_at(self):
        write_race(self.archive, "a-1", ["u1"])
        history = [["older", None]]
        race_archive.write_json_atomic({"u1": [None, EARLIER, "2026-09-02T00:00:00Z", history]}, self.path)
        self.run_sync(lambda url: None, FixedClock(datetime(2026, 11, 2, tzinfo=UTC)))
        self.assertEqual(
            self.stored()["u1"], [None, "2026-11-02T00:00:00Z", "2026-09-02T00:00:00Z", history]
        )

    def test_a_changed_bio_appends_the_old_one_from_a_legacy_entry(self):
        write_race(self.archive, "a-1", ["u1"])
        race_archive.write_json_atomic({"u1": ["old", EARLIER]}, self.path)
        self.run_sync(FakeLoader({"u1": BIO_PAGE}), FixedClock(datetime(2026, 11, 2, tzinfo=UTC)))
        entry = self.stored()["u1"]
        self.assertEqual(entry[1:], ["2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z", [["old", None]]])
        self.assertIn("Mew", entry[0])

    def test_a_changed_bio_appends_the_old_one_to_a_history(self):
        write_race(self.archive, "a-1", ["u1"])
        race_archive.write_json_atomic(
            {"u1": ["two", EARLIER, "2026-08-01T00:00:00Z", [["one", None]]]}, self.path
        )
        self.run_sync(FakeLoader({"u1": BIO_PAGE}), FixedClock(datetime(2026, 11, 2, tzinfo=UTC)))
        entry = self.stored()["u1"]
        self.assertEqual(
            entry[1:],
            [
                "2026-11-02T00:00:00Z",
                "2026-11-02T00:00:00Z",
                [["one", None], ["two", "2026-08-01T00:00:00Z"]],
            ],
        )

    def test_a_bio_that_disappears_is_recorded_like_any_change(self):
        write_race(self.archive, "a-1", ["u1"])
        race_archive.write_json_atomic({"u1": ["old", EARLIER, EARLIER, []]}, self.path)
        self.run_sync(lambda url: None, FixedClock(datetime(2026, 11, 2, tzinfo=UTC)))
        self.assertEqual(
            self.stored()["u1"],
            [None, "2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z", [["old", EARLIER]]],
        )

    def test_a_failed_fetch_leaves_a_history_entry_untouched(self):
        write_race(self.archive, "a-1", ["u1", "u2"])
        entry = ["old", EARLIER, EARLIER, [["older", None]]]
        race_archive.write_json_atomic({"u1": entry}, self.path)
        self.run_sync(FakeLoader({"u1": race_archive.ArchiveError("HTTP 500")}))
        self.assertEqual(self.stored()["u1"], entry)

    def test_an_os_error_counts_as_a_failure(self):
        write_race(self.archive, "a-1", ["u1"])
        summary = self.run_sync(FakeLoader({"u1": OSError("reset")}))
        self.assertEqual(summary["failed"], 1)

    def test_a_page_that_is_not_a_profile_is_a_failure(self):
        write_race(self.archive, "a-1", ["u1"])
        summary = self.run_sync(FakeLoader({"u1": CHALLENGE_PAGE}))
        self.assertEqual(summary, {"due": 1, "checked": 0, "failed": 1, "stopped": False})
        self.assertIn("skipping u1", self.warnings)

    def test_an_oversized_page_is_a_failure(self):
        write_race(self.archive, "a-1", ["u1"])
        page = b"x" * (racer_bios.MAX_PROFILE_BYTES + 1)
        summary = self.run_sync(FakeLoader({"u1": page}))
        self.assertEqual(summary["failed"], 1)

    def test_stops_after_consecutive_failures(self):
        users = [f"u{number}" for number in range(8)]
        write_race(self.archive, "a-1", users)
        loader = FakeLoader({user: race_archive.ArchiveError("blocked") for user in users})
        summary = self.run_sync(loader)
        self.assertEqual(summary, {"due": 8, "checked": 0, "failed": 5, "stopped": True})
        self.assertEqual(len(loader.urls), 5)
        self.assertIn("refusing profile pages", self.warnings)

    def test_a_success_resets_the_failure_streak(self):
        users = [f"u{number}" for number in range(10)]
        write_race(self.archive, "a-1", users)
        pages = {user: race_archive.ArchiveError("blocked") for user in users}
        pages["u3"] = BIO_PAGE
        loader = FakeLoader(pages)
        summary = self.run_sync(loader)
        # u0-u2 fail, u3 succeeds, then u4-u8 fail five in a row and u9 is never asked.
        self.assertEqual(summary, {"due": 10, "checked": 1, "failed": 8, "stopped": True})
        self.assertEqual(len(loader.urls), 9)

    def test_sleeps_between_requests_only(self):
        write_race(self.archive, "a-1", ["u1", "u2", "u3"])
        self.run_sync(FakeLoader({}))
        self.assertEqual(self.sleep.call_count, 2)
        self.sleep.assert_called_with(racer_bios.BIO_REQUEST_DELAY_SECONDS)

    def test_a_single_request_does_not_sleep(self):
        write_race(self.archive, "a-1", ["u1"])
        self.run_sync(FakeLoader({}))
        self.sleep.assert_not_called()

    def test_the_file_is_untouched_when_nothing_was_written(self):
        write_race(self.archive, "a-1", ["u1"])
        self.run_sync(FakeLoader({"u1": race_archive.ArchiveError("blocked")}))
        self.assertFalse(self.path.exists())

    def test_nothing_due_makes_no_requests(self):
        write_race(self.archive, "a-1", ["u1"])
        race_archive.write_json_atomic({"u1": ["x", LATER]}, self.path)
        before = self.path.read_text(encoding="utf-8")
        loader = FakeLoader({})
        summary = self.run_sync(loader)
        self.assertEqual(summary, {"due": 0, "checked": 0, "failed": 0, "stopped": False})
        self.assertEqual(loader.urls, [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_an_invalid_id_is_never_requested(self):
        write_race(self.archive, "a-1", ["u1", "../evil", "u 2"])
        loader = FakeLoader({})
        summary = self.run_sync(loader)
        self.assertEqual(summary, {"due": 3, "checked": 1, "failed": 2, "stopped": False})
        self.assertEqual(loader.urls, ["https://racetime.gg/user/u1"])
        self.assertEqual(list(self.stored()), ["u1"])
        self.assertEqual(self.sleep.call_count, 0)

    def test_a_corrupt_bios_file_raises(self):
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(race_archive.ArchiveError):
            racer_bios.sync(self.archive, loader=FakeLoader({}))


class MainTests(unittest.TestCase):
    def run_main(self, archive):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", ["racer_bios.py", "--archive", str(archive)]),
            patch.object(racer_bios.time, "sleep"),
            patch.object(racer_bios, "fetch_profile", side_effect=FakeLoader({"u1": BIO_PAGE})),
            patch.object(racer_bios.pokemon_avatars, "load_catalogue", return_value=object()),
            patch.object(
                racer_bios.pokemon_avatars,
                "favourite_pokemon",
                side_effect=lambda bio, catalogue: 151 if bio else None,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = racer_bios.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_prints_the_summary_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            write_race(archive, "a-1", ["u1", "u2"])
            code, out, _err = self.run_main(archive)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Bios: 2 checked, 0 failed; 1 name a favourite Pokémon.\n")

    def test_a_corrupt_bios_file_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            write_race(archive, "a-1", ["u1"])
            (archive / racer_bios.RACER_BIOS_FILE).write_text("not json", encoding="utf-8")
            code, out, err = self.run_main(archive)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Bio sync failed:", err)

    def test_a_pokedex_error_only_skips_the_count(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            write_race(archive, "a-1", ["u1"])
            with patch.object(
                racer_bios.pokemon_avatars,
                "load_catalogue",
                side_effect=racer_bios.pokemon_avatars.PokedexError("missing"),
            ):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    patch("sys.argv", ["racer_bios.py", "--archive", str(archive)]),
                    patch.object(racer_bios.time, "sleep"),
                    patch.object(racer_bios, "fetch_profile", FakeLoader({})),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    code = racer_bios.main()
            stored = racer_bios.load_racer_bios(archive)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "Bios: 1 checked, 0 failed.\n")
        self.assertIn("Warning: missing; favourites were not counted.", stderr.getvalue())
        self.assertEqual(list(stored), ["u1"])

    def test_failed_fetches_do_not_change_the_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            write_race(archive, "a-1", ["u1"])
            with patch.object(
                racer_bios, "fetch_profile", side_effect=race_archive.ArchiveError("x")
            ):
                stdout = io.StringIO()
                with (
                    patch("sys.argv", ["racer_bios.py", "--archive", str(archive)]),
                    patch.object(racer_bios.pokemon_avatars, "load_catalogue", return_value=1),
                    patch.object(racer_bios.pokemon_avatars, "favourite_pokemon"),
                    redirect_stdout(stdout),
                    redirect_stderr(io.StringIO()),
                ):
                    code = racer_bios.main()
        self.assertEqual(code, 0)
        self.assertIn("0 checked, 1 failed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
