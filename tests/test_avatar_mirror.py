import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts import avatar_mirror, race_archive


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://racetime.gg/media/"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


def envelope(slug, fetched_at, avatars, *, started_at="2026-10-01T12:00:00.000Z", mode="simple"):
    """A finished race file whose entrants carry the given {user_id: avatar} map."""
    return {
        "schema_version": race_archive.ARCHIVE_SCHEMA_VERSION,
        "fetched_at": fetched_at,
        "race": {
            "name": f"alttpr-ladder/{slug}",
            "status": {"value": "finished"},
            "info": f"Step Ladder Series - [{mode}] Test preset",
            "entrants_count": len(avatars),
            "started_at": started_at,
            "ended_at": "2026-10-01T14:00:00.000Z",
            "entrants": [
                {"user": {"id": user_id, "name": user_id, "avatar": avatar}}
                for user_id, avatar in avatars.items()
            ],
        },
    }


class MirrorNameTests(unittest.TestCase):
    def test_accepts_a_percent_encoded_raster_url(self):
        name = avatar_mirror.mirror_name(f"{ORIGIN}Petit_chat_pott%C3%A9.jpg")
        self.assertRegex(name, r"^[0-9a-f]{16}\.jpg$")

    def test_accepts_every_raster_extension_in_any_case(self):
        for extension in ("png", "jpg", "jpeg", "gif", "webp", "PNG", "Jpg"):
            with self.subTest(extension=extension):
                name = avatar_mirror.mirror_name(f"{ORIGIN}pic.{extension}")
                self.assertTrue(name.endswith(f".{extension.lower()}"))

    def test_rejects_urls_that_cannot_be_mirrored(self):
        for url in (
            None,
            "",
            "https://evil.example/media/a.png",
            "http://racetime.gg/media/a.png",
            f"{ORIGIN}sub/a.png",
            f"{ORIGIN}../a.png",
            f"{ORIGIN}a.png?size=big",
            f"{ORIGIN}a.png#top",
            f"{ORIGIN}a.svg",
            f"{ORIGIN}a.png.exe",
            f"{ORIGIN}png",
            f"{ORIGIN}",
            f"{ORIGIN}\u00e9.png",
            f"{ORIGIN}a b.png",
        ):
            with self.subTest(url=url):
                self.assertIsNone(avatar_mirror.mirror_name(url))

    def test_name_is_deterministic_and_depends_on_the_whole_url(self):
        first = avatar_mirror.mirror_name(f"{ORIGIN}a.png")
        self.assertEqual(first, avatar_mirror.mirror_name(f"{ORIGIN}a.png"))
        self.assertNotEqual(first, avatar_mirror.mirror_name(f"{ORIGIN}b.png"))
        self.assertNotEqual(first, avatar_mirror.mirror_name(f"{ORIGIN}A.png"))


class CurrentAvatarUrlsTests(unittest.TestCase):
    A = f"{ORIGIN}a.png"
    B = f"{ORIGIN}b.png"

    def test_a_newer_race_beats_an_older_observation(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-02T00:00:00Z", {"u1": self.B})],
            {"u1": [self.A, "2026-10-01T00:00:00Z"]},
        )
        self.assertEqual(urls, {"u1": self.B})

    def test_a_newer_observation_beats_an_older_race(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.B})],
            {"u1": [self.A, "2026-10-02T00:00:00Z"]},
        )
        self.assertEqual(urls, {"u1": self.A})

    def test_a_tie_goes_to_the_leaderboard(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.B})],
            {"u1": [self.A, "2026-10-01T00:00:00Z"]},
        )
        self.assertEqual(urls, {"u1": self.A})

    def test_times_are_compared_as_instants_not_text(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-01T02:00:00+02:00", {"u1": self.B})],
            {"u1": [self.A, "2026-09-30T23:30:00Z"]},
        )
        self.assertEqual(urls, {"u1": self.B})

    def test_a_null_observation_removes_the_avatar(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A})],
            {"u1": [None, "2026-10-02T00:00:00Z"]},
        )
        self.assertEqual(urls, {})

    def test_a_newer_race_without_an_avatar_removes_it(self):
        urls = avatar_mirror.current_avatar_urls(
            [envelope("r1", "2026-10-02T00:00:00Z", {"u1": None})],
            {"u1": [self.A, "2026-10-01T00:00:00Z"]},
        )
        self.assertEqual(urls, {})

    def test_unmirrorable_winners_have_no_entry_even_over_an_older_good_one(self):
        urls = avatar_mirror.current_avatar_urls(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A, "u2": self.A}),
                envelope(
                    "r2",
                    "2026-10-02T00:00:00Z",
                    {"u1": "https://evil.example/x.png", "u2": f"{ORIGIN}x.svg"},
                ),
            ],
            {},
        )
        self.assertEqual(urls, {})

    def test_the_newest_race_file_wins_among_race_files(self):
        urls = avatar_mirror.current_avatar_urls(
            [
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.B}),
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
            ],
            {},
        )
        self.assertEqual(urls, {"u1": self.B})

    def test_race_files_that_tie_are_ordered_by_start_time_then_name(self):
        fetched = "2026-10-01T00:00:00Z"
        early = envelope("zz", fetched, {"u1": self.A}, started_at="2026-09-30T12:00:00.000Z")
        late = envelope("aa", fetched, {"u1": self.B}, started_at="2026-10-01T12:00:00.000Z")
        for envelopes in ([early, late], [late, early]):
            self.assertEqual(
                avatar_mirror.current_avatar_urls(envelopes, {}), {"u1": self.B}
            )

        same_a = envelope("aa", fetched, {"u1": self.A})
        same_z = envelope("zz", fetched, {"u1": self.B})
        for envelopes in ([same_a, same_z], [same_z, same_a]):
            self.assertEqual(
                avatar_mirror.current_avatar_urls(envelopes, {}), {"u1": self.B}
            )

    def test_racers_without_an_identity_are_skipped(self):
        race_file = envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A})
        race_file["race"]["entrants"].append({"user": None})
        race_file["race"]["entrants"].append({"user": {"name": "No id", "avatar": self.A}})
        self.assertEqual(
            avatar_mirror.current_avatar_urls([race_file], {}), {"u1": self.A}
        )


class IsRasterTests(unittest.TestCase):
    def test_accepts_matching_magic_bytes(self):
        for data, extension in (
            (PNG, "png"),
            (JPEG, "jpg"),
            (JPEG, "jpeg"),
            (GIF, "gif"),
            (b"GIF87a" + b"\x00", "gif"),
            (WEBP, "webp"),
        ):
            with self.subTest(extension=extension):
                self.assertTrue(avatar_mirror.is_raster(data, extension))

    def test_rejects_wrong_or_missing_magic_bytes(self):
        for data, extension in (
            (b"<!doctype html>", "png"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "jpg"),
            (PNG, "jpg"),
            (JPEG, "png"),
            (b"RIFF\x00\x00\x00\x00WAVEfmt ", "webp"),
            (b"", "png"),
            (PNG, "svg"),
        ):
            with self.subTest(data=data[:8], extension=extension):
                self.assertFalse(avatar_mirror.is_raster(data, extension))

    def test_rejects_oversize_files(self):
        limit = avatar_mirror.MAX_AVATAR_BYTES
        self.assertTrue(avatar_mirror.is_raster(PNG + b"\x00" * (limit - len(PNG)), "png"))
        self.assertFalse(avatar_mirror.is_raster(PNG + b"\x00" * (limit - len(PNG) + 1), "png"))


class MirrorTests(unittest.TestCase):
    URL_A = f"{ORIGIN}a.png"
    URL_B = f"{ORIGIN}b.jpg"
    URL_C = f"{ORIGIN}c.gif"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive_dir = self.root / "archive"
        self.output_dir = self.root / "avatars"
        self.participants_path = self.root / "participants.json"
        sleeper = patch.object(avatar_mirror.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.requested = []
        self.bodies = {self.URL_A: PNG, self.URL_B: JPEG, self.URL_C: GIF}

    def archive(self, avatars):
        race_archive.write_json_atomic(
            envelope("r1", "2026-10-01T00:00:00Z", avatars),
            race_archive.race_path(self.archive_dir, "alttpr-ladder/r1"),
        )

    def publish(self, published):
        """Write a participants.json; published maps user ID to a URL or a raw name."""
        players = []
        for user_id, value in published.items():
            player = {"id": user_id, "name": user_id}
            if value is not None:
                player["a"] = avatar_mirror.mirror_name(value) or value
            players.append(player)
        self.participants_path.write_text(json.dumps({"players": players}), encoding="utf-8")

    def loader(self, url):
        self.requested.append(url)
        result = self.bodies[url]
        if isinstance(result, Exception):
            raise result
        return result

    def run_mirror(self):
        with redirect_stderr(io.StringIO()) as stderr:
            summary = avatar_mirror.mirror(
                self.participants_path,
                self.archive_dir,
                self.output_dir,
                loader=self.loader,
            )
        return summary, stderr.getvalue()

    def files(self):
        return sorted(path.name for path in self.output_dir.iterdir())

    def test_downloads_only_missing_files(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B})
        self.publish({"u1": self.URL_A, "u2": self.URL_B})
        self.output_dir.mkdir()
        existing = avatar_mirror.mirror_name(self.URL_A)
        (self.output_dir / existing).write_bytes(PNG + b"kept")

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "removed": 0, "failed": 0})
        self.assertEqual(self.requested, [self.URL_B])
        self.assertEqual((self.output_dir / existing).read_bytes(), PNG + b"kept")
        self.assertEqual(
            self.files(),
            sorted([existing, avatar_mirror.mirror_name(self.URL_B)]),
        )

    def test_a_second_run_makes_no_requests(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()
        self.requested.clear()

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "removed": 0, "failed": 0})
        self.assertEqual(self.requested, [])

    def test_paces_downloads(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})
        self.publish({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})

        self.run_mirror()

        self.assertEqual(self.sleep.call_count, 2)
        self.sleep.assert_called_with(race_archive.REQUEST_DELAY_SECONDS)

    def test_ignores_ladder_test_races(self):
        self.archive({"u1": self.URL_A})
        race_archive.write_json_atomic(
            envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.URL_B}, mode="ladder_test"),
            race_archive.race_path(self.archive_dir, "alttpr-ladder/r2"),
        )
        self.publish({"u1": self.URL_A})

        summary, _ = self.run_mirror()

        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(self.requested, [self.URL_A])

    def test_prunes_files_that_are_not_published_and_leaves_the_outside_alone(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.output_dir.mkdir()
        (self.output_dir / "stale.png").write_bytes(PNG)
        (self.output_dir / "notes.txt").write_text("x", encoding="utf-8")
        outside = self.root / "outside.png"
        outside.write_bytes(PNG)
        (self.output_dir / "link.png").symlink_to(outside)
        (self.output_dir / "folder").mkdir()
        (self.output_dir / "folder" / "inner.png").write_bytes(PNG)

        summary, _ = self.run_mirror()

        self.assertEqual(summary["removed"], 3)
        self.assertEqual(
            self.files(), sorted(["folder", avatar_mirror.mirror_name(self.URL_A)])
        )
        self.assertTrue((self.output_dir / "folder" / "inner.png").exists())
        self.assertEqual(outside.read_bytes(), PNG)

    def test_a_changed_avatar_replaces_the_old_file(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()

        self.archive({"u1": self.URL_B})
        self.publish({"u1": self.URL_B})
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "removed": 1, "failed": 0})
        self.assertEqual(self.files(), [avatar_mirror.mirror_name(self.URL_B)])

    def test_a_removed_avatar_is_pruned(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()

        self.publish({"u1": None})
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "removed": 1, "failed": 0})
        self.assertEqual(self.files(), [])

    def test_rejects_files_that_are_not_the_claimed_image(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})
        self.publish({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})
        self.bodies[self.URL_A] = b"<html>not a png</html>"
        self.bodies[self.URL_B] = JPEG + b"\x00" * avatar_mirror.MAX_AVATAR_BYTES

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "removed": 0, "failed": 2})
        self.assertEqual(self.files(), [avatar_mirror.mirror_name(self.URL_C)])
        self.assertEqual(warnings.count("Warning:"), 2)

    def test_warns_and_continues_when_a_download_fails(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B})
        self.publish({"u1": self.URL_A, "u2": self.URL_B})
        self.bodies[self.URL_A] = race_archive.ArchiveError("Avatar failed with HTTP 404")

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "removed": 0, "failed": 1})
        self.assertIn("Warning: Avatar failed with HTTP 404", warnings)
        self.assertEqual(self.files(), [avatar_mirror.mirror_name(self.URL_B)])

    def test_a_failed_download_is_retried_on_the_next_run(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.bodies[self.URL_A] = race_archive.ArchiveError("Avatar failed with HTTP 500")
        self.run_mirror()

        self.bodies[self.URL_A] = PNG
        summary, _ = self.run_mirror()

        self.assertEqual(summary["downloaded"], 1)

    def test_raises_when_participants_names_a_file_the_archive_does_not_give(self):
        self.archive({"u1": self.URL_B})
        for published in (
            {"u1": self.URL_A},
            {"u1": "0123456789abcdef.png"},
            {"nobody": self.URL_A},
        ):
            with self.subTest(published=published):
                self.publish(published)
                with self.assertRaisesRegex(
                    avatar_mirror.AvatarMirrorError, "participants.json is stale"
                ):
                    self.run_mirror()
        self.assertEqual(self.requested, [])

    def test_stale_input_removes_nothing(self):
        self.archive({"u1": self.URL_B})
        self.publish({"u1": self.URL_A})
        self.output_dir.mkdir()
        (self.output_dir / "keep.png").write_bytes(PNG)

        with self.assertRaises(avatar_mirror.AvatarMirrorError):
            self.run_mirror()

        self.assertEqual(self.files(), ["keep.png"])

    def test_unreadable_participants_raise(self):
        self.archive({"u1": self.URL_A})
        with self.assertRaises(avatar_mirror.AvatarMirrorError):
            self.run_mirror()
        self.participants_path.write_text("{", encoding="utf-8")
        with self.assertRaises(avatar_mirror.AvatarMirrorError):
            self.run_mirror()

    def test_main_exits_one_on_stale_input_and_zero_otherwise(self):
        self.archive({"u1": self.URL_B})
        self.publish({"u1": self.URL_A})
        arguments = [
            "--participants", str(self.participants_path),
            "--archive", str(self.archive_dir),
            "--output", str(self.output_dir),
        ]
        with patch("sys.argv", ["avatar_mirror.py", *arguments]):
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(avatar_mirror.main(), 1)
        self.assertIn("participants.json is stale", stderr.getvalue())

        self.publish({"u1": None})
        with patch("sys.argv", ["avatar_mirror.py", *arguments]):
            with patch("builtins.print") as mocked_print:
                self.assertEqual(avatar_mirror.main(), 0)
        mocked_print.assert_called_with("Avatars: 0 downloaded, 0 removed, 0 failed.")


class CommittedAvatarTests(unittest.TestCase):
    def test_every_mirrored_file_is_published_and_a_raster_image(self):
        avatar_dir = REPO_ROOT / "site" / "avatars"
        if not avatar_dir.exists():
            self.skipTest("no avatars are mirrored yet")
        participants = json.loads(
            (REPO_ROOT / "site" / "data" / "participants.json").read_text(encoding="utf-8")
        )
        published = {player["a"] for player in participants["players"] if "a" in player}

        for path in avatar_dir.iterdir():
            with self.subTest(file=path.name):
                self.assertIn(path.name, published)
                self.assertTrue(path.is_file() and not path.is_symlink())
                self.assertTrue(
                    avatar_mirror.is_raster(path.read_bytes(), path.suffix.removeprefix("."))
                )


if __name__ == "__main__":
    unittest.main()
