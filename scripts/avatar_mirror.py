#!/usr/bin/env python3
"""Mirror the racer avatars that participants.json names into site/avatars/.

racetime.gg avatars are downloaded and converted into two square WebP files; Pokémon
avatars are copied from assets/pokemon/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/avatar_mirror.py` and `from scripts import avatar_mirror`.
try:
    from scripts import pokemon_avatars, race_archive, schedule_archive
except ImportError:
    import pokemon_avatars
    import race_archive
    import schedule_archive


AVATAR_ORIGIN = "https://racetime.gg/media/"
# The ImageMagick coder that reads each extension, named so ImageMagick never guesses.
CODERS = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}
RASTER_EXTENSIONS = frozenset(CODERS)
# Chosen generously above the sizes observed on racetime.gg, to stop only runaway files.
MAX_AVATAR_BYTES = 2_000_000
IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp"
# racetime.gg percent-encodes avatar URLs; anything else could not be requested as is.
URL_SAFE_NAME = re.compile(r"[A-Za-z0-9._~%-]+")
SITE_DIR = Path(__file__).resolve().parents[1] / "site"
PARTICIPANTS_PATH = SITE_DIR / "data" / "participants.json"
OUTPUT_DIR = SITE_DIR / "avatars"

# Twice the largest normal display, the 64 px racer-page avatar, for 2x screens.
AVATAR_SIDE = 128
# Twice the face-off's 128 px avatar (--faceoff-avatar in faceoff.css). Only it uses this file.
FACEOFF_SIDE = 256
# `<hash>.webp` has its face-off copy at `<hash>-256.webp`. Must match FACEOFF_AVATAR_SUFFIX in ui.js.
FACEOFF_SUFFIX = f"-{FACEOFF_SIDE}"
WEBP_QUALITY = 90
# Applied after shrinking only. Resizing alone came out 13-22% softer than a browser's own
# downscale of the upload (edge contrast on 12 large uploads against Chrome); this light
# unsharp mask restores it to within about 5%. RGB only, so ImageMagick 6 and 7 agree.
SHARPEN = ["-channel", "RGB", "-unsharp", "0x0.5+0.5+0", "+channel"]
# A conversion takes well under a second; this only stops a pathological image hanging the run.
IMAGEMAGICK_TIMEOUT_SECONDS = 60

JsonObject = dict[str, Any]
Loader = Callable[[str], bytes]
# (image bytes, source extension) -> {AVATAR_SIDE: WebP bytes, FACEOFF_SIDE: WebP bytes}
Converter = Callable[[bytes, str], Mapping[int, bytes]]


class AvatarMirrorError(RuntimeError):
    """Raised when the mirror cannot tell which avatars to hold."""


class AvatarConvertError(RuntimeError):
    """Raised when an avatar cannot be turned into valid WebP files."""


def _url_extension(url: str) -> str:
    remainder = url.removeprefix(AVATAR_ORIGIN)
    return remainder.rpartition(".")[2].lower() if "." in remainder else ""


def mirror_name(url: Any) -> str | None:
    """Return the file name that mirrors an avatar URL, or None if it can't be mirrored.

    Only a raster image directly under racetime.gg/media/, named in URL-safe ASCII, is
    mirrored. The name is a hash of the whole URL plus `.webp`, the format every mirrored
    upload is converted to, so it is plain ASCII and a new picture always has a new name.
    """
    if not isinstance(url, str) or not url.startswith(AVATAR_ORIGIN):
        return None
    if not URL_SAFE_NAME.fullmatch(url.removeprefix(AVATAR_ORIGIN)):
        return None
    if _url_extension(url) not in RASTER_EXTENSIONS:
        return None
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"{digest}.webp"


def faceoff_name(name: str) -> str:
    """Return the file name of the face-off's larger copy of a mirrored avatar."""
    stem, _, extension = name.rpartition(".")
    return f"{stem}{FACEOFF_SUFFIX}.{extension}"


def _race_start(race: JsonObject) -> datetime:
    try:
        return race_archive.parse_timestamp(race.get("started_at"), "started_at")
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


def _observations(
    envelopes: list[JsonObject],
    observations: Mapping[str, list[str | None]],
) -> dict[str, list[tuple[tuple, Any]]]:
    """Return every avatar observation per racer as `(sort key, URL or None)` pairs."""
    seen: dict[str, list[tuple[tuple, Any]]] = {}

    for envelope in envelopes:
        race = envelope["race"]
        fetched_at = race_archive.parse_timestamp(envelope["fetched_at"], "fetched_at")
        key = (fetched_at, 0, _race_start(race), race.get("name") or "")
        for entrant in race.get("entrants") or []:
            user = entrant.get("user") if isinstance(entrant, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            if isinstance(user_id, str) and user_id:
                seen.setdefault(user_id, []).append((key, user.get("avatar")))

    for user_id, (url, first_seen) in observations.items():
        key = (
            race_archive.parse_timestamp(first_seen, "first-seen time"),
            1,
            datetime.min.replace(tzinfo=UTC),
            "",
        )
        seen.setdefault(user_id, []).append((key, url))
    return seen


def current_avatar_urls(
    envelopes: list[JsonObject],
    observations: Mapping[str, list[str | None]],
) -> dict[str, str]:
    """Return each racer's most recently observed avatar URL that can be mirrored.

    An observation is either a race file (each identified entrant's avatar as of the
    envelope's `fetched_at`) or a leaderboard observation (`user_id -> [URL or None,
    first-seen time]`). The newest wins and a leaderboard observation wins a tie. Race
    files that tie are ordered by race start time, then name. A racer whose winning
    observation is null, off racetime.gg/media or not a raster image has no entry.
    """
    current: dict[str, str] = {}
    for user_id, seen in _observations(envelopes, observations).items():
        # max() returns the first of equal items: the first-seen of equal keys wins.
        _key, url = max(seen, key=lambda item: item[0])
        if mirror_name(url) is not None:
            current[user_id] = url
    return current


def avatar_change_times(
    envelopes: list[JsonObject],
    observations: Mapping[str, list[str | None]],
) -> dict[str, datetime]:
    """Return when each racer last changed their racetime.gg avatar.

    Observations are compared by `mirror_name`, so None means no mirrorable upload. The
    change is dated at the first sighting of the current value after the latest
    observation whose value differed from it. Removing the upload, or switching to a URL
    that cannot be mirrored, is a change; two different unmirrorable URLs are not. A
    racer seen with one value only has no entry.
    """
    changes: dict[str, datetime] = {}
    for user_id, seen in _observations(envelopes, observations).items():
        # Newest first. A reverse sort keeps equal keys in their original order, so the
        # first entry is the one current_avatar_urls picks with max().
        newest_first = sorted(seen, key=lambda item: item[0], reverse=True)
        newest_url = newest_first[0][1]
        newest_name = mirror_name(newest_url)
        for index, (_key, url) in enumerate(newest_first):
            # The same URL always has the same mirror name, so hash only a different one.
            if url != newest_url and mirror_name(url) != newest_name:
                changes[user_id] = newest_first[index - 1][0][0]
                break
    return changes


def is_raster(data: bytes, extension: str) -> bool:
    """Return whether the bytes are a PNG, JPEG, GIF or WebP matching the extension."""
    if len(data) > MAX_AVATAR_BYTES:
        return False
    extension = extension.lower()
    if extension == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if extension == "gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if extension == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


@dataclass(frozen=True)
class WebpInfo:
    width: int
    height: int
    animated: bool
    # The sum of the frame durations of an animated image; 0 for a still one.
    duration_ms: int


def _riff_chunks(data: bytes, start: int, end: int):
    """Yield (tag, body) for each RIFF chunk between start and end."""
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise ValueError("truncated WebP chunk header")
        tag = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        body_end = offset + 8 + size
        if body_end > end:
            raise ValueError(f"truncated WebP {tag!r} chunk")
        yield tag, data[offset + 8 : body_end]
        offset = body_end + (size & 1)  # Chunks are padded to an even length.


def webp_info(data: bytes) -> WebpInfo:
    """Return a WebP file's canvas size, whether it is animated, and its total duration.

    Reads a simple lossy (`VP8 `), simple lossless (`VP8L`) or extended (`VP8X`) file, and
    sums the ANMF frame durations of an animated one. Raises ValueError for anything else.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a RIFF WebP file")
    end = 8 + int.from_bytes(data[4:8], "little")
    if end > len(data):
        raise ValueError("truncated WebP file")
    chunks = list(_riff_chunks(data, 12, end))
    if not chunks:
        raise ValueError("WebP file has no chunks")
    tag, body = chunks[0]
    if tag == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
        width = int.from_bytes(body[6:8], "little") & 0x3FFF
        height = int.from_bytes(body[8:10], "little") & 0x3FFF
        return WebpInfo(width, height, False, 0)
    if tag == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
        bits = int.from_bytes(body[1:5], "little")
        return WebpInfo((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, False, 0)
    if tag == b"VP8X" and len(body) >= 10:
        width = int.from_bytes(body[4:7], "little") + 1
        height = int.from_bytes(body[7:10], "little") + 1
        if not body[0] & 0x02:
            return WebpInfo(width, height, False, 0)
        frames = [frame for frame_tag, frame in chunks if frame_tag == b"ANMF"]
        if not frames or any(len(frame) < 16 for frame in frames):
            raise ValueError("animated WebP file has no well-formed frames")
        duration = sum(int.from_bytes(frame[12:15], "little") for frame in frames)
        return WebpInfo(width, height, True, duration)
    raise ValueError(f"unrecognised WebP {tag!r} chunk")


def imagemagick() -> str | None:
    """Return ImageMagick 7's `magick`, else ImageMagick 6's `convert` (as Ubuntu's apt ships)."""
    return shutil.which("magick") or shutil.which("convert")


def _run_imagemagick(tool: str, arguments: list[str], directory: Path) -> str:
    try:
        result = subprocess.run(
            [tool, *arguments],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=IMAGEMAGICK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AvatarConvertError(f"ImageMagick could not run: {error}") from error
    if result.returncode != 0:
        raise AvatarConvertError(f"ImageMagick failed: {result.stderr.strip()}")
    return result.stdout


def _check_webp(data: bytes, side: int, moving: bool) -> None:
    """Raise unless data is a square WebP of the given side, animated if the source moves.

    A source whose frames are all the same may come out still: libwebp merges identical
    frames, and a still image looks the same.
    """
    try:
        info = webp_info(data)
    except ValueError as error:
        raise AvatarConvertError(f"ImageMagick wrote no valid WebP: {error}") from error
    if (info.width, info.height) != (side, side):
        raise AvatarConvertError(
            f"expected a {side} x {side} WebP, got {info.width} x {info.height}"
        )
    if moving and not info.animated:
        raise AvatarConvertError("an animation came out still")


def convert_avatar(data: bytes, extension: str, tool: str) -> dict[int, bytes]:
    """Return the avatar as square WebP files of at most AVATAR_SIDE and FACEOFF_SIDE.

    The image is turned upright by its EXIF orientation, centre-cropped to a square,
    shrunk to each side and lightly sharpened (SHARPEN), or left at its size when it
    is no larger. An sRGB image keeps its ICC colour profile and loses every other
    profile; any other image is converted to sRGB and loses them all, so no CMYK or gray
    profile is left on RGB pixels. Each file is the smaller of a lossy encoding at
    WEBP_QUALITY and a lossless one. An animation whose frames differ stays animated.
    Raises AvatarConvertError when ImageMagick fails or writes anything else.
    """
    coder = CODERS[extension.lower()]
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        (directory / "source").write_bytes(data)
        # The explicit coder keeps ImageMagick from sniffing the bytes for another format.
        # One line per frame: size, colourspace and a hash of the pixels (%#).
        frames = [
            line.split()
            for line in _run_imagemagick(
                tool,
                [f"{coder}:source", "-coalesce", "-auto-orient",
                 "-format", "%w %h %[colorspace] %#\n", "info:"],
                directory,
            ).splitlines()
            if line.strip()
        ]
        try:
            width, height, colourspace = int(frames[0][0]), int(frames[0][1]), frames[0][2]
            moving = len({frame[3] for frame in frames}) > 1
        except (IndexError, ValueError) as error:
            raise AvatarConvertError(f"ImageMagick could not measure it: {error}") from error
        source_side = min(width, height)
        if colourspace == "sRGB":
            colours = ["+profile", "!icc,*"]
        else:
            colours = ["-colorspace", "sRGB", "+profile", "*"]
        square = [
            f"{coder}:source", "-coalesce", "-auto-orient",
            "-gravity", "center", "-crop", f"{source_side}x{source_side}+0+0", "+repage",
        ]
        encodings = {
            "lossy.webp": ["-quality", str(WEBP_QUALITY), "-define", "webp:use-sharp-yuv=true"],
            "lossless.webp": ["-define", "webp:lossless=true"],
        }

        files: dict[int, bytes] = {}
        for side in (AVATAR_SIDE, FACEOFF_SIDE):
            # A square no larger than the side is kept as it is, pixel for pixel.
            shrink = ["-resize", f"{side}x{side}", *SHARPEN] if source_side > side else []
            resized = [*square, *shrink, *colours]
            encoded = []
            for output, encoding in encodings.items():
                _run_imagemagick(tool, [*resized, *encoding, f"webp:{output}"], directory)
                try:
                    encoded.append((directory / output).read_bytes())
                except OSError as error:
                    raise AvatarConvertError(f"ImageMagick wrote no file: {error}") from error
            smallest = min(encoded, key=len)
            _check_webp(smallest, min(source_side, side), moving)
            files[side] = smallest
        return files


def _fetch_avatar(url: str) -> bytes:
    return race_archive._get_bytes(
        url, f"Avatar {url}", IMAGE_ACCEPT, limit=MAX_AVATAR_BYTES
    )


def _published_avatars(participants_path: Path) -> dict[str, str]:
    """Return {user ID: file name} for every racer participants.json gives an avatar."""
    try:
        payload = json.loads(participants_path.read_text(encoding="utf-8"))
        players = payload["players"]
        return {player["id"]: player["a"] for player in players if "a" in player}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AvatarMirrorError(f"{participants_path} could not be read: {error}") from error


@dataclass(frozen=True)
class MirrorPlan:
    # Each published racetime.gg avatar name and the URL it mirrors.
    downloads: dict[str, str]
    # Each published Pokémon avatar name and the sprite it copies.
    sprites: dict[str, Path]


def mirror_plan(
    participants_path: Path = PARTICIPANTS_PATH,
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    pokemon_dir: Path = pokemon_avatars.POKEMON_DIR,
) -> MirrorPlan:
    """Work out what each avatar name in participants.json mirrors.

    Raises AvatarMirrorError if participants.json is unreadable, or names a file the
    archive does not explain or a sprite that does not exist.
    """
    # build_stats imports this module via participant_stats, so import it at call time.
    try:
        from scripts import build_stats
    except ImportError:
        import build_stats

    published = _published_avatars(participants_path)
    spans = schedule_archive.season_spans(schedule_archive.load_schedule(archive_dir))
    envelopes = build_stats.included_envelopes(race_archive.load_races(archive_dir), spans)
    urls = current_avatar_urls(envelopes, race_archive.load_racer_avatars(archive_dir))

    downloads: dict[str, str] = {}
    sprites: dict[str, Path] = {}
    for user_id, name in published.items():
        # A Pokémon avatar's name says which sprite it is; the archive need not explain it.
        sprite = pokemon_avatars.sprite_path(name, pokemon_dir)
        if sprite is not None:
            sprites[name] = sprite
            continue
        url = urls.get(user_id)
        if url is None or mirror_name(url) != name:
            raise AvatarMirrorError(
                "participants.json is stale; run python3 scripts/build_stats.py"
            )
        downloads[name] = url
    return MirrorPlan(downloads, sprites)


def _present_files(output_dir: Path) -> set[str]:
    if not output_dir.is_dir():
        return set()
    return {
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    }


def _missing_downloads(plan: MirrorPlan, present: set[str]) -> list[str]:
    """Return the racetime.gg avatar names missing either of their two files."""
    return [
        name
        for name in sorted(plan.downloads)
        if name not in present or faceoff_name(name) not in present
    ]


def pending(
    participants_path: Path = PARTICIPANTS_PATH,
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    output_dir: Path = OUTPUT_DIR,
    *,
    pokemon_dir: Path = pokemon_avatars.POKEMON_DIR,
) -> int:
    """Return how many racetime.gg avatars the next mirror run would download."""
    plan = mirror_plan(participants_path, archive_dir, pokemon_dir)
    return len(_missing_downloads(plan, _present_files(output_dir)))


def mirror(
    participants_path: Path = PARTICIPANTS_PATH,
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    output_dir: Path = OUTPUT_DIR,
    *,
    loader: Loader | None = None,
    converter: Converter | None = None,
    pokemon_dir: Path = pokemon_avatars.POKEMON_DIR,
) -> dict[str, int]:
    """Make output_dir hold exactly the avatar files participants.json names.

    A racetime.gg avatar `<hash>.webp` is downloaded when it or its face-off copy
    (`faceoff_name`) is missing, and converted into both (see `convert_avatar`). A
    Pokémon avatar is copied from its sprite in pokemon_dir when the copy is missing or
    differs. An avatar that fails to download, validate or convert is skipped with a
    warning and retried on the next run; with no ImageMagick, every download is. Returns
    the counts of avatars downloaded, copied, removed and failed.
    """
    load = loader or _fetch_avatar
    plan = mirror_plan(participants_path, archive_dir, pokemon_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    present = _present_files(output_dir)
    missing = _missing_downloads(plan, present)

    downloaded = failed = requests = 0
    convert = converter
    if missing and convert is None:
        tool = imagemagick()
        if tool is None:
            print(
                "Warning: ImageMagick (magick or convert) is not on PATH; skipping "
                f"{len(missing)} racetime.gg avatar(s).",
                file=sys.stderr,
            )
            failed, missing = len(missing), []
        else:
            def convert(data: bytes, extension: str) -> Mapping[int, bytes]:
                return convert_avatar(data, extension, tool)

    for name in missing:
        url = plan.downloads[name]
        if requests:
            time.sleep(race_archive.REQUEST_DELAY_SECONDS)
        requests += 1
        try:
            data = load(url)
        except (race_archive.ArchiveError, OSError) as error:
            print(f"Warning: {error}; skipping {name}.", file=sys.stderr)
            failed += 1
            continue
        extension = _url_extension(url)
        if not is_raster(data, extension):
            print(
                f"Warning: {url} is not a raster image within {MAX_AVATAR_BYTES} bytes; "
                f"skipping {name}.",
                file=sys.stderr,
            )
            failed += 1
            continue
        try:
            files = convert(data, extension)
        except AvatarConvertError as error:
            print(
                f"Warning: {url} could not be converted: {error}; skipping {name}.",
                file=sys.stderr,
            )
            failed += 1
            continue
        race_archive.write_bytes_atomic(files[AVATAR_SIDE], output_dir / name)
        race_archive.write_bytes_atomic(files[FACEOFF_SIDE], output_dir / faceoff_name(name))
        downloaded += 1

    copied = 0
    for name, sprite in sorted(plan.sprites.items()):
        try:
            data = sprite.read_bytes()
            # A local read is cheap, so a copy is replaced whenever its sprite changed.
            if name in present and (output_dir / name).read_bytes() == data:
                continue
        except OSError as error:
            print(
                f"Warning: {sprite} could not be read: {error}; skipping {name}.",
                file=sys.stderr,
            )
            failed += 1
            continue
        if not is_raster(data, "png"):
            print(
                f"Warning: {sprite} is not a PNG image; skipping {name}.", file=sys.stderr
            )
            failed += 1
            continue
        race_archive.write_bytes_atomic(data, output_dir / name)
        copied += 1

    kept = {*plan.downloads, *map(faceoff_name, plan.downloads), *plan.sprites}
    removed = 0
    for path in output_dir.iterdir():
        if path.name not in kept and (path.is_symlink() or path.is_file()):
            path.unlink()
            removed += 1

    return {
        "downloaded": downloaded,
        "copied": copied,
        "removed": removed,
        "failed": failed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--participants",
        type=Path,
        default=PARTICIPANTS_PATH,
        help=f"published racer dataset (default: {PARTICIPANTS_PATH})",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=race_archive.ARCHIVE_DIR,
        help=f"raw race archive directory (default: {race_archive.ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"avatar directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--pokemon",
        type=Path,
        default=pokemon_avatars.POKEMON_DIR,
        help=f"Pokémon sprite directory (default: {pokemon_avatars.POKEMON_DIR})",
    )
    parser.add_argument(
        "--pending",
        action="store_true",
        help="only print how many racetime.gg avatars a run would download, which "
        "tells whether it needs ImageMagick",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.pending:
            print(pending(args.participants, args.archive, args.output, pokemon_dir=args.pokemon))
            return 0
        summary = mirror(
            args.participants, args.archive, args.output, pokemon_dir=args.pokemon
        )
    except (AvatarMirrorError, race_archive.ArchiveError) as error:
        print(f"Avatar mirror failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Avatars: {summary['downloaded']} downloaded, {summary['copied']} copied, "
        f"{summary['removed']} removed, {summary['failed']} failed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
