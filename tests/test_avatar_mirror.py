import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts import avatar_mirror, pokemon_avatars, race_archive


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://racetime.gg/media/"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
MIRRORED_NAME = re.compile(r"^[0-9a-f]{16}\.webp$")


def riff_chunk(tag, body):
    return tag + len(body).to_bytes(4, "little") + body + b"\x00" * (len(body) % 2)


def webp_file(*chunks):
    payload = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(payload).to_bytes(4, "little") + payload


def lossy_webp(width, height):
    """A simple lossy WebP header: the VP8 frame tag, start code and 14-bit sizes."""
    body = b"\x00\x00\x00\x9d\x01\x2a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    return webp_file(riff_chunk(b"VP8 ", body))


def lossless_webp(width, height):
    """A simple lossless WebP header: the VP8L signature and the sizes less one."""
    bits = (width - 1) | (height - 1) << 14
    return webp_file(riff_chunk(b"VP8L", b"\x2f" + bits.to_bytes(4, "little")))


def animated_webp(width, height, durations):
    """An extended WebP with the animation flag and one ANMF chunk per frame duration."""
    vp8x = b"\x02\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    frames = [
        riff_chunk(b"ANMF", bytes(12) + duration.to_bytes(3, "little") + b"\x00")
        for duration in durations
    ]
    return webp_file(riff_chunk(b"VP8X", vp8x), riff_chunk(b"ANIM", bytes(6)), *frames)


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
        self.assertRegex(name, MIRRORED_NAME)

    def test_every_raster_extension_in_any_case_is_named_as_webp(self):
        for extension in ("png", "jpg", "jpeg", "gif", "webp", "PNG", "Jpg"):
            with self.subTest(extension=extension):
                name = avatar_mirror.mirror_name(f"{ORIGIN}pic.{extension}")
                self.assertRegex(name, MIRRORED_NAME)

    def test_the_faceoff_copy_adds_the_suffix_before_the_extension(self):
        name = avatar_mirror.mirror_name(f"{ORIGIN}a.png")
        stem = name.removesuffix(".webp")
        self.assertEqual(
            avatar_mirror.faceoff_name(name), f"{stem}{avatar_mirror.FACEOFF_SUFFIX}.webp"
        )

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

    def test_the_newer_of_race_and_leaderboard_observation_wins(self):
        a, b, first, second = self.A, self.B, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
        for case, race_at, race_url, seen, expected in (
            ("a newer race", second, b, [a, first], b),
            ("a newer observation", first, b, [a, second], a),
            ("a tie goes to the leaderboard", first, b, [a, first], a),
            ("instants, not text", "2026-10-01T02:00:00+02:00", b, [a, "2026-09-30T23:30:00Z"], b),
            ("a null observation removes it", first, a, [None, second], None),
            ("a newer race without one removes it", second, None, [a, first], None),
        ):
            with self.subTest(case):
                urls = avatar_mirror.current_avatar_urls(
                    [envelope("r1", race_at, {"u1": race_url})], {"u1": seen}
                )
                self.assertEqual(urls, {"u1": expected} if expected else {})

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


class AvatarChangeTimesTests(unittest.TestCase):
    A = f"{ORIGIN}a.png"
    B = f"{ORIGIN}b.png"

    @staticmethod
    def instant(text):
        return race_archive.parse_timestamp(text, "test time")

    def test_a_change_is_dated_by_the_first_sighting_after_a_different_value(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.A}),
                envelope("r3", "2026-10-03T00:00:00Z", {"u1": self.B}),
                envelope("r4", "2026-10-04T00:00:00Z", {"u1": self.B}),
            ],
            {},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-10-03T00:00:00Z")})

    def test_a_single_value_is_no_change(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.A}),
            ],
            {"u1": [self.A, "2026-10-03T00:00:00Z"]},
        )
        self.assertEqual(changes, {})

    def test_none_to_a_url_is_a_change(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": None}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.B}),
            ],
            {},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-10-02T00:00:00Z")})

    def test_a_removed_avatar_is_dated_at_the_first_sighting_without_it(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": None}),
                envelope("r3", "2026-10-03T00:00:00Z", {"u1": None}),
            ],
            {},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-10-02T00:00:00Z")})

    def test_a_switch_to_an_unmirrorable_url_is_a_change(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": "https://evil.example/x.png"}),
            ],
            {},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-10-02T00:00:00Z")})

    def test_two_different_unmirrorable_urls_are_no_change(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": "https://evil.example/x.png"}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": "https://evil.example/y.png"}),
                envelope("r3", "2026-10-03T00:00:00Z", {"u1": None}),
            ],
            {},
        )
        self.assertEqual(changes, {})

    def test_a_leaderboard_sighting_later_than_the_races_dates_the_change(self):
        changes = avatar_mirror.avatar_change_times(
            [envelope("r1", "2026-10-27T00:00:00Z", {"u1": self.A})],
            {"u1": [self.B, "2026-11-04T08:00:00Z"]},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-11-04T08:00:00Z")})

    def test_a_change_back_is_dated_at_the_return(self):
        changes = avatar_mirror.avatar_change_times(
            [
                envelope("r1", "2026-10-01T00:00:00Z", {"u1": self.A}),
                envelope("r2", "2026-10-02T00:00:00Z", {"u1": self.B}),
                envelope("r3", "2026-10-03T00:00:00Z", {"u1": self.A}),
            ],
            {},
        )
        self.assertEqual(changes, {"u1": self.instant("2026-10-03T00:00:00Z")})


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


class WebpInfoTests(unittest.TestCase):
    def test_reads_the_size_of_lossy_lossless_and_animated_files(self):
        for data, expected in (
            (lossy_webp(128, 96), avatar_mirror.WebpInfo(128, 96, False, 0)),
            (lossless_webp(16383, 1), avatar_mirror.WebpInfo(16383, 1, False, 0)),
            (
                animated_webp(256, 256, [200, 100, 35]),
                avatar_mirror.WebpInfo(256, 256, True, 335),
            ),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(avatar_mirror.webp_info(data), expected)

    def test_an_extended_file_without_the_animation_flag_is_still(self):
        vp8x = bytes(4) + (99).to_bytes(3, "little") + (49).to_bytes(3, "little")
        data = webp_file(riff_chunk(b"VP8X", vp8x), riff_chunk(b"ICCP", b"icc"))
        self.assertEqual(avatar_mirror.webp_info(data), avatar_mirror.WebpInfo(100, 50, False, 0))

    def test_rejects_anything_but_a_well_formed_webp(self):
        whole = lossy_webp(64, 64)
        for data in (
            b"",
            PNG,
            b"RIFF\x04\x00\x00\x00WAVE",
            whole[:-1],
            webp_file(),
            webp_file(riff_chunk(b"VP8 ", b"\x00" * 10)),
            webp_file(riff_chunk(b"VP8L", b"\x00" * 5)),
            webp_file(riff_chunk(b"ALPH", b"\x00" * 10)),
            animated_webp(64, 64, []),
        ):
            with self.subTest(data=data[:24]):
                with self.assertRaises(ValueError):
                    avatar_mirror.webp_info(data)


class MirrorTests(unittest.TestCase):
    URL_A = f"{ORIGIN}a.png"
    URL_B = f"{ORIGIN}b.jpg"
    URL_C = f"{ORIGIN}c.gif"
    # What the stub converter writes, so a test can tell the two files apart.
    CONVERTED = {
        avatar_mirror.AVATAR_SIDE: lossless_webp(avatar_mirror.AVATAR_SIDE, avatar_mirror.AVATAR_SIDE),
        avatar_mirror.FACEOFF_SIDE: lossless_webp(avatar_mirror.FACEOFF_SIDE, avatar_mirror.FACEOFF_SIDE),
    }

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive_dir = self.root / "archive"
        self.output_dir = self.root / "avatars"
        self.participants_path = self.root / "participants.json"
        self.pokemon_dir = self.root / "pokemon"
        self.pokemon_dir.mkdir()
        (self.pokemon_dir / "25.png").write_bytes(PNG + b"pikachu")
        (self.pokemon_dir / "201-b.png").write_bytes(PNG + b"unown")
        sleeper = patch.object(avatar_mirror.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.requested = []
        self.bodies = {self.URL_A: PNG, self.URL_B: JPEG, self.URL_C: GIF}
        self.converted = []
        self.conversion_error = None

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

    def converter(self, data, extension):
        self.converted.append((data, extension))
        if self.conversion_error is not None:
            raise self.conversion_error
        return self.CONVERTED

    def run_mirror(self, converter=None):
        with redirect_stderr(io.StringIO()) as stderr:
            summary = avatar_mirror.mirror(
                self.participants_path,
                self.archive_dir,
                self.output_dir,
                loader=self.loader,
                converter=converter or self.converter,
                pokemon_dir=self.pokemon_dir,
            )
        return summary, stderr.getvalue()

    def pending(self):
        return avatar_mirror.pending(
            self.participants_path,
            self.archive_dir,
            self.output_dir,
            pokemon_dir=self.pokemon_dir,
        )

    def files(self):
        return sorted(path.name for path in self.output_dir.iterdir())

    @staticmethod
    def both(url):
        """The two files a racetime.gg avatar is mirrored as."""
        name = avatar_mirror.mirror_name(url)
        return sorted([name, avatar_mirror.faceoff_name(name)])

    def test_downloads_only_missing_files(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B})
        self.publish({"u1": self.URL_A, "u2": self.URL_B})
        self.output_dir.mkdir()
        for existing in self.both(self.URL_A):
            (self.output_dir / existing).write_bytes(PNG + b"kept")

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 0, "failed": 0})
        self.assertEqual(self.requested, [self.URL_B])
        for existing in self.both(self.URL_A):
            self.assertEqual((self.output_dir / existing).read_bytes(), PNG + b"kept")
        self.assertEqual(self.files(), sorted(self.both(self.URL_A) + self.both(self.URL_B)))

    def test_converts_each_download_into_its_avatar_and_faceoff_files(self):
        self.archive({"u1": self.URL_B})
        self.publish({"u1": self.URL_B})

        self.run_mirror()

        self.assertEqual(self.converted, [(JPEG, "jpg")])
        name = avatar_mirror.mirror_name(self.URL_B)
        faceoff = avatar_mirror.faceoff_name(name)
        self.assertEqual(
            (self.output_dir / name).read_bytes(), self.CONVERTED[avatar_mirror.AVATAR_SIDE]
        )
        self.assertEqual(
            (self.output_dir / faceoff).read_bytes(),
            self.CONVERTED[avatar_mirror.FACEOFF_SIDE],
        )

    def test_a_missing_faceoff_copy_downloads_the_avatar_again(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()
        name = avatar_mirror.mirror_name(self.URL_A)
        faceoff = avatar_mirror.faceoff_name(name)
        (self.output_dir / faceoff).unlink()
        self.requested.clear()

        self.assertEqual(self.pending(), 1)
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 0, "failed": 0})
        self.assertEqual(self.requested, [self.URL_A])
        self.assertEqual(self.files(), self.both(self.URL_A))

    def test_a_failed_conversion_warns_writes_nothing_and_is_retried_on_the_next_run(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.conversion_error = avatar_mirror.AvatarConvertError("ImageMagick failed: corrupt")

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 0, "removed": 0, "failed": 1})
        self.assertIn("could not be converted: ImageMagick failed: corrupt", warnings)
        self.assertEqual(self.files(), [])

        self.conversion_error = None
        summary, _ = self.run_mirror()

        self.assertEqual(summary["downloaded"], 1)
        self.assertEqual(self.files(), self.both(self.URL_A))

    def test_without_imagemagick_downloads_are_skipped_with_one_warning(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C, "u4": None})
        self.publish({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C, "u4": "pokemon-25.png"})
        self.output_dir.mkdir()
        for existing in self.both(self.URL_B):
            (self.output_dir / existing).write_bytes(PNG)

        with patch.object(avatar_mirror, "imagemagick", return_value=None):
            with redirect_stderr(io.StringIO()) as stderr:
                summary = avatar_mirror.mirror(
                    self.participants_path,
                    self.archive_dir,
                    self.output_dir,
                    loader=self.loader,
                    pokemon_dir=self.pokemon_dir,
                )

        self.assertEqual(summary, {"downloaded": 0, "copied": 1, "removed": 0, "failed": 2})
        self.assertEqual(self.requested, [])
        self.assertEqual(stderr.getvalue().count("Warning:"), 1)
        self.assertIn("ImageMagick", stderr.getvalue())
        self.assertEqual(self.files(), sorted(self.both(self.URL_B) + ["pokemon-25.png"]))

    def test_pending_counts_the_racetime_avatars_a_run_would_download(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B, "u3": None})
        self.publish({"u1": self.URL_A, "u2": self.URL_B, "u3": "pokemon-25.png"})

        self.assertEqual(self.pending(), 2)
        self.assertFalse(self.output_dir.exists())
        self.run_mirror()
        self.assertEqual(self.pending(), 0)

        self.publish({"u1": self.URL_C})
        with self.assertRaisesRegex(avatar_mirror.AvatarMirrorError, "participants.json is stale"):
            self.pending()

    def test_a_second_run_makes_no_requests(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()
        self.requested.clear()

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 0, "removed": 0, "failed": 0})
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
        self.assertEqual(self.files(), sorted(["folder", *self.both(self.URL_A)]))
        self.assertTrue((self.output_dir / "folder" / "inner.png").exists())
        self.assertEqual(outside.read_bytes(), PNG)

        self.publish({"u1": None})
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 0, "removed": 2, "failed": 0})
        self.assertEqual(self.files(), ["folder"])

    def test_a_changed_avatar_replaces_the_old_file(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": self.URL_A})
        self.run_mirror()

        self.archive({"u1": self.URL_B})
        self.publish({"u1": self.URL_B})
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 2, "failed": 0})
        self.assertEqual(self.files(), self.both(self.URL_B))

    def test_rejects_files_that_are_not_the_claimed_image(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})
        self.publish({"u1": self.URL_A, "u2": self.URL_B, "u3": self.URL_C})
        self.bodies[self.URL_A] = b"<html>not a png</html>"
        self.bodies[self.URL_B] = JPEG + b"\x00" * avatar_mirror.MAX_AVATAR_BYTES

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 0, "failed": 2})
        self.assertEqual(self.files(), self.both(self.URL_C))
        self.assertEqual(warnings.count("Warning:"), 2)

    def test_warns_and_continues_when_a_download_fails(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B})
        self.publish({"u1": self.URL_A, "u2": self.URL_B})
        self.bodies[self.URL_A] = race_archive.ArchiveError("Avatar failed with HTTP 404")

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 0, "failed": 1})
        self.assertIn("Warning: Avatar failed with HTTP 404", warnings)
        self.assertEqual(self.files(), self.both(self.URL_B))

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
            {"u1": "0123456789abcdef.webp"},
            {"nobody": self.URL_A},
        ):
            with self.subTest(published=published):
                self.publish(published)
                with self.assertRaisesRegex(
                    avatar_mirror.AvatarMirrorError, "participants.json is stale"
                ):
                    self.run_mirror()
        self.assertEqual(self.requested, [])

    def test_copies_pokemon_sprites_without_a_request_or_a_pause(self):
        self.archive({"u1": self.URL_A, "u2": None})
        self.publish({"u1": "pokemon-25.png", "u2": "pokemon-201-b.png"})

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 2, "removed": 0, "failed": 0})
        self.assertEqual(self.requested, [])
        self.sleep.assert_not_called()
        self.assertEqual(self.files(), ["pokemon-201-b.png", "pokemon-25.png"])
        self.assertEqual(
            (self.output_dir / "pokemon-25.png").read_bytes(),
            (self.pokemon_dir / "25.png").read_bytes(),
        )

    def test_downloads_and_copies_side_by_side(self):
        self.archive({"u1": self.URL_A, "u2": None})
        self.publish({"u1": self.URL_A, "u2": "pokemon-25.png"})
        self.run_mirror()

        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 0, "removed": 0, "failed": 0})
        self.assertEqual(self.files(), sorted([*self.both(self.URL_A), "pokemon-25.png"]))

    def test_a_changed_sprite_replaces_its_copy(self):
        self.archive({"u1": None})
        self.publish({"u1": "pokemon-25.png"})
        self.run_mirror()

        (self.pokemon_dir / "25.png").write_bytes(PNG + b"better pikachu")
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 1, "removed": 0, "failed": 0})
        self.assertEqual(
            (self.output_dir / "pokemon-25.png").read_bytes(), PNG + b"better pikachu"
        )

    def test_a_pokemon_replaced_by_a_racetime_avatar_is_pruned(self):
        self.archive({"u1": self.URL_A})
        self.publish({"u1": "pokemon-25.png"})
        self.run_mirror()

        self.publish({"u1": self.URL_A})
        summary, _ = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 1, "copied": 0, "removed": 1, "failed": 0})
        self.assertEqual(self.files(), self.both(self.URL_A))

    def test_a_sprite_that_is_not_a_png_is_skipped_with_a_warning(self):
        self.archive({"u1": None})
        (self.pokemon_dir / "25.png").write_bytes(b"<html>not a png</html>")
        self.publish({"u1": "pokemon-25.png"})

        summary, warnings = self.run_mirror()

        self.assertEqual(summary, {"downloaded": 0, "copied": 0, "removed": 0, "failed": 1})
        self.assertIn("Warning:", warnings)
        self.assertEqual(self.files(), [])

    def test_raises_when_participants_names_a_sprite_that_does_not_exist(self):
        self.archive({"u1": None})
        for name in ("pokemon-26.png", "pokemon-../25.png", "pokemon-25.PNG"):
            with self.subTest(name=name):
                self.publish({"u1": name})
                with self.assertRaisesRegex(
                    avatar_mirror.AvatarMirrorError, "participants.json is stale"
                ):
                    self.run_mirror()

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
            "--pokemon", str(self.pokemon_dir),
        ]
        with patch("sys.argv", ["avatar_mirror.py", *arguments]):
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(avatar_mirror.main(), 1)
        self.assertIn("participants.json is stale", stderr.getvalue())

        self.publish({"u1": None})
        with patch("sys.argv", ["avatar_mirror.py", *arguments]):
            with patch("builtins.print") as mocked_print:
                self.assertEqual(avatar_mirror.main(), 0)
        mocked_print.assert_called_with(
            "Avatars: 0 downloaded, 0 copied, 0 removed, 0 failed."
        )

    def test_main_pending_prints_the_count_and_downloads_nothing(self):
        self.archive({"u1": self.URL_A, "u2": self.URL_B})
        self.publish({"u1": self.URL_A, "u2": self.URL_B})
        arguments = [
            "--pending",
            "--participants", str(self.participants_path),
            "--archive", str(self.archive_dir),
            "--output", str(self.output_dir),
            "--pokemon", str(self.pokemon_dir),
        ]
        with patch("sys.argv", ["avatar_mirror.py", *arguments]):
            with patch("builtins.print") as mocked_print:
                self.assertEqual(avatar_mirror.main(), 0)
        mocked_print.assert_called_once_with(2)
        self.assertFalse(self.output_dir.exists())


def jpeg_with_orientation(jpeg, orientation):
    """Insert an EXIF segment holding only the orientation tag right after the JPEG's SOI."""
    tiff = (
        b"MM\x00\x2a" + (8).to_bytes(4, "big")  # Big-endian TIFF header, first IFD at 8.
        + (1).to_bytes(2, "big")  # One entry: Orientation (0x0112), SHORT, count 1.
        + (0x0112).to_bytes(2, "big") + (3).to_bytes(2, "big") + (1).to_bytes(4, "big")
        + orientation.to_bytes(2, "big") + b"\x00\x00"
        + (0).to_bytes(4, "big")  # No next IFD.
    )
    payload = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + jpeg[2:]


IMAGEMAGICK = avatar_mirror.imagemagick()


@unittest.skipUnless(IMAGEMAGICK, "ImageMagick (magick or convert) is not installed")
class ConvertAvatarTests(unittest.TestCase):
    """The converter on the real ImageMagick. The bot runs these on ImageMagick 6 before a download."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def draw(self, name, *arguments):
        """Make a fixture with drawing options that ImageMagick 6 and 7 share."""
        path = self.directory / name
        subprocess.run([IMAGEMAGICK, *arguments, str(path)], check=True, capture_output=True)
        return path.read_bytes()

    def convert(self, data, extension):
        return avatar_mirror.convert_avatar(data, extension, IMAGEMAGICK)

    def pixel_signature(self, data, coder):
        """Return ImageMagick's hash of the opaque RGB pixels, whatever the file format."""
        result = subprocess.run(
            [IMAGEMAGICK, f"{coder}:-", "-alpha", "off", "-format", "%#", "info:"],
            input=data, check=True, capture_output=True,
        )
        return result.stdout

    def colour_at(self, webp, x, y):
        """Return the (red, blue) of a pixel of the last frame, as fractions of full intensity."""
        result = subprocess.run(
            [IMAGEMAGICK, "webp:-", "-coalesce",
             "-format", f"%[fx:p{{{x},{y}}}.r] %[fx:p{{{x},{y}}}.b]\n", "info:"],
            input=webp, check=True, capture_output=True,
        )
        red, blue = result.stdout.splitlines()[-1].split()
        return float(red), float(blue)

    def test_a_large_image_is_cropped_square_and_shrunk_to_each_side(self):
        photo = self.draw("photo.png", "-seed", "1", "-size", "400x300", "xc:", "+noise", "Random")

        files = self.convert(photo, "png")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                self.assertEqual(
                    avatar_mirror.webp_info(files[side]), avatar_mirror.WebpInfo(side, side, False, 0)
                )

    def test_a_small_image_is_cropped_square_but_never_enlarged(self):
        small = self.draw("small.jpg", "-size", "100x80", "xc:red")

        files = self.convert(small, "jpg")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                self.assertEqual(
                    avatar_mirror.webp_info(files[side]), avatar_mirror.WebpInfo(80, 80, False, 0)
                )

    def test_an_image_within_the_side_keeps_its_exact_pixels(self):
        # Flat art compresses best losslessly. Mid-tones, unlike pure red and blue, show any
        # resampling or sharpening, which saturated colours would clip away.
        flat = self.draw(
            "flat.png", "-size", "64x64", "xc:#c04040",
            "-fill", "#4040c0", "-draw", "rectangle 0,0 31,63",
        )

        files = self.convert(flat, "png")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                self.assertEqual(
                    self.pixel_signature(files[side], "webp"), self.pixel_signature(flat, "png")
                )

    def test_flat_art_is_kept_lossless_and_a_photo_lossy_whichever_is_smaller(self):
        flat = self.draw(
            "flat.png", "-size", "64x64", "xc:red", "-fill", "blue", "-draw", "rectangle 0,0 31,63"
        )
        photo = self.draw("photo.png", "-seed", "1", "-size", "200x200", "xc:", "+noise", "Random")

        flat_file = self.convert(flat, "png")[avatar_mirror.AVATAR_SIDE]
        photo_file = self.convert(photo, "png")[avatar_mirror.AVATAR_SIDE]

        self.assertIn(b"VP8L", flat_file)
        self.assertNotIn(b"VP8 ", flat_file)
        self.assertIn(b"VP8 ", photo_file)
        self.assertNotIn(b"VP8L", photo_file)

    def test_an_optimised_animation_stays_animated_for_the_same_total_duration_and_frames(self):
        # Three 40 x 30 frames of 10 hundredths, the last with a blue square at 15,10 to 24,19.
        # Optimising stores the later frames as small patches, as real GIFs do, and libwebp
        # may merge the two identical frames.
        animation = self.draw(
            "animation.gif", "-delay", "10", "-size", "40x30", "xc:red", "xc:red",
            "(", "-size", "40x30", "xc:red", "-fill", "blue", "-draw", "rectangle 15,10 24,19", ")",
            "-layers", "optimize",
        )

        files = self.convert(animation, "gif")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                self.assertEqual(
                    avatar_mirror.webp_info(files[side]), avatar_mirror.WebpInfo(30, 30, True, 300)
                )
                # The centre crop starts 5 px in, so the square sits at 10,10 to 19,19.
                corner_red, corner_blue = self.colour_at(files[side], 2, 2)
                square_red, square_blue = self.colour_at(files[side], 15, 15)
                self.assertGreater(corner_red, 0.5)
                self.assertLess(corner_blue, 0.5)
                self.assertLess(square_red, 0.5)
                self.assertGreater(square_blue, 0.5)

    def test_an_animation_with_zero_delays_stays_animated(self):
        # Browsers show a delay of 0 as about 100 ms, and the WebP encoder writes it so.
        animation = self.draw("zero.gif", "-delay", "0", "-size", "20x20", "xc:red", "xc:blue")

        files = self.convert(animation, "gif")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                info = avatar_mirror.webp_info(files[side])
                self.assertEqual((info.width, info.height, info.animated), (20, 20, True))

    def test_an_animation_of_identical_frames_converts_and_may_come_out_still(self):
        animation = self.draw("same.gif", "-delay", "10", "-size", "20x30", "xc:red", "xc:red")

        files = self.convert(animation, "gif")

        for side in (avatar_mirror.AVATAR_SIDE, avatar_mirror.FACEOFF_SIDE):
            with self.subTest(side=side):
                info = avatar_mirror.webp_info(files[side])
                self.assertEqual((info.width, info.height), (20, 20))

    def test_a_cmyk_photo_comes_out_in_its_srgb_colours(self):
        cmyk = self.draw("cmyk.jpg", "-size", "40x40", "xc:#c04040", "-colorspace", "CMYK")

        converted = self.convert(cmyk, "jpg")[avatar_mirror.AVATAR_SIDE]

        red, blue = self.colour_at(converted, 20, 20)
        self.assertAlmostEqual(red, 0xC0 / 255, delta=0.05)
        self.assertAlmostEqual(blue, 0x40 / 255, delta=0.05)
        self.assertNotIn(b"ICCP", converted)

    def test_an_exif_rotated_photo_is_turned_upright_and_loses_its_exif(self):
        # Stored red on the left and blue on the right; orientation 6 shows the left on top.
        stored = self.draw(
            "sideways.jpg", "-size", "400x200", "xc:red",
            "-fill", "blue", "-draw", "rectangle 200,0 399,199",
        )
        sideways = jpeg_with_orientation(stored, 6)

        upright = self.convert(sideways, "jpg")[avatar_mirror.AVATAR_SIDE]

        last = avatar_mirror.AVATAR_SIDE - 1
        top_red, top_blue = self.colour_at(upright, 0, 0)
        bottom_red, bottom_blue = self.colour_at(upright, 0, last)
        self.assertGreater(top_red, 0.5)
        self.assertLess(top_blue, 0.5)
        self.assertLess(bottom_red, 0.5)
        self.assertGreater(bottom_blue, 0.5)
        self.assertNotIn(b"EXIF", upright)

    def test_an_image_imagemagick_cannot_read_raises(self):
        with self.assertRaises(avatar_mirror.AvatarConvertError):
            self.convert(PNG + b"broken", "png")


class ConvertAvatarFailureTests(unittest.TestCase):
    """Failures of ImageMagick itself, faked, so these run without it."""

    def test_imagemagick_that_cannot_run_hangs_or_writes_nothing_raises_a_convert_error(self):
        def writes_nothing(arguments, **kwargs):
            # Every call "succeeds"; the probe reads one 20 x 20 sRGB frame.
            return subprocess.CompletedProcess(arguments, 0, stdout="20 20 sRGB abc\n", stderr="")

        def hangs(arguments, **kwargs):
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

        def is_missing(arguments, **kwargs):
            raise FileNotFoundError(arguments[0])

        for fake in (writes_nothing, hangs, is_missing):
            with self.subTest(fake=fake.__name__):
                with patch.object(avatar_mirror.subprocess, "run", side_effect=fake):
                    with self.assertRaises(avatar_mirror.AvatarConvertError):
                        avatar_mirror.convert_avatar(PNG, "png", "magick")


class SiteMirrorTests(unittest.TestCase):
    def test_ui_js_mirrors_the_faceoff_suffix_and_the_pokemon_prefix(self):
        source = (REPO_ROOT / "site" / "ui.js").read_text(encoding="utf-8")
        for constant, value in (
            ("FACEOFF_AVATAR_SUFFIX", avatar_mirror.FACEOFF_SUFFIX),
            ("POKEMON_AVATAR_PREFIX", pokemon_avatars.AVATAR_PREFIX),
        ):
            with self.subTest(constant=constant):
                match = re.search(rf'^const {constant} = "([^"]*)";', source, re.MULTILINE)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), value)


class CommittedAvatarTests(unittest.TestCase):
    def test_every_mirrored_file_is_published_and_a_valid_image_within_its_side(self):
        avatar_dir = REPO_ROOT / "site" / "avatars"
        self.assertTrue(avatar_dir.is_dir(), "site/avatars/ is missing")
        participants = json.loads(
            (REPO_ROOT / "site" / "data" / "participants.json").read_text(encoding="utf-8")
        )
        # Each file the mirror may hold, and the largest side it may have (None: a sprite copy).
        sides = {}
        for player in participants["players"]:
            name = player.get("a")
            if name is None:
                continue
            if name.startswith(pokemon_avatars.AVATAR_PREFIX):
                sides[name] = None
            else:
                sides[name] = avatar_mirror.AVATAR_SIDE
                sides[avatar_mirror.faceoff_name(name)] = avatar_mirror.FACEOFF_SIDE

        for path in avatar_dir.iterdir():
            with self.subTest(file=path.name):
                self.assertIn(path.name, sides)
                self.assertTrue(path.is_file() and not path.is_symlink())
                data = path.read_bytes()
                side = sides[path.name]
                if side is None:
                    self.assertTrue(avatar_mirror.is_raster(data, "png"))
                    continue
                info = avatar_mirror.webp_info(data)
                self.assertEqual(info.width, info.height)
                self.assertLessEqual(info.width, side)

    def test_every_mirrored_pokemon_is_a_copy_of_its_sprite(self):
        avatar_dir = REPO_ROOT / "site" / "avatars"
        self.assertTrue(avatar_dir.is_dir(), "site/avatars/ is missing")
        for path in avatar_dir.glob(f"{pokemon_avatars.AVATAR_PREFIX}*"):
            with self.subTest(file=path.name):
                sprite = pokemon_avatars.sprite_path(path.name)
                self.assertIsNotNone(sprite)
                self.assertEqual(path.read_bytes(), sprite.read_bytes())


if __name__ == "__main__":
    unittest.main()
