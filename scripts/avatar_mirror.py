#!/usr/bin/env python3
"""Mirror the racer avatars that participants.json names into site/avatars/.

racetime.gg avatars are downloaded; Pokémon avatars are copied from assets/pokemon/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/avatar_mirror.py` and `from scripts import avatar_mirror`.
try:
    from scripts import pokemon_avatars, race_archive
except ImportError:
    import pokemon_avatars
    import race_archive


AVATAR_ORIGIN = "https://racetime.gg/media/"
RASTER_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
# Chosen generously above the sizes observed on racetime.gg, to stop only runaway files.
MAX_AVATAR_BYTES = 2_000_000
IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp"
# racetime.gg percent-encodes avatar URLs; anything else could not be requested as is.
URL_SAFE_NAME = re.compile(r"[A-Za-z0-9._~%-]+")
SITE_DIR = Path(__file__).resolve().parents[1] / "site"
PARTICIPANTS_PATH = SITE_DIR / "data" / "participants.json"
OUTPUT_DIR = SITE_DIR / "avatars"

JsonObject = dict[str, Any]
Loader = Callable[[str], bytes]


class AvatarMirrorError(RuntimeError):
    """Raised when the mirror cannot tell which avatars to hold."""


def mirror_name(url: Any) -> str | None:
    """Return the file name that mirrors an avatar URL, or None if it can't be mirrored.

    Only a raster image directly under racetime.gg/media/, named in URL-safe ASCII, is
    mirrored. The name is a
    hash of the whole URL, so it is plain ASCII and a new picture always has a new name.
    """
    if not isinstance(url, str) or not url.startswith(AVATAR_ORIGIN):
        return None
    remainder = url.removeprefix(AVATAR_ORIGIN)
    if not URL_SAFE_NAME.fullmatch(remainder):
        return None
    extension = remainder.rpartition(".")[2].lower() if "." in remainder else ""
    if extension not in RASTER_EXTENSIONS:
        return None
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"{digest}.{extension}"


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
        values = [mirror_name(url) for _key, url in newest_first]
        for index, value in enumerate(values):
            if value != values[0]:
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


def _write_atomic(data: bytes, path: Path) -> None:
    temporary_path = path.with_name(f"{path.name}.tmp")
    with temporary_path.open("wb") as output_file:
        output_file.write(data)
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, path)


def mirror(
    participants_path: Path = PARTICIPANTS_PATH,
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    output_dir: Path = OUTPUT_DIR,
    *,
    loader: Loader | None = None,
    pokemon_dir: Path = pokemon_avatars.POKEMON_DIR,
) -> dict[str, int]:
    """Make output_dir hold exactly the avatar files participants.json names.

    A Pokémon avatar is copied from its sprite in pokemon_dir when the copy is missing
    or differs; any other avatar is downloaded only when missing. A file that fails to
    download or validate is skipped with a warning and retried on the next run. Returns
    the counts of files downloaded, copied, removed and failed.
    """
    # build_stats imports this module via participant_stats, so import it at call time.
    try:
        from scripts import build_stats
    except ImportError:
        import build_stats

    load = loader or _fetch_avatar
    published = _published_avatars(participants_path)
    envelopes = build_stats.included_envelopes(race_archive.load_races(archive_dir))
    urls = current_avatar_urls(envelopes, race_archive.load_racer_avatars(archive_dir))

    wanted: dict[str, str] = {}
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
        wanted[name] = url

    output_dir.mkdir(parents=True, exist_ok=True)
    present = {
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    }

    downloaded = failed = 0
    for name, url in sorted(wanted.items()):
        if name in present:
            continue
        if downloaded or failed:
            time.sleep(race_archive.REQUEST_DELAY_SECONDS)
        try:
            data = load(url)
        except (race_archive.ArchiveError, OSError) as error:
            print(f"Warning: {error}; skipping {name}.", file=sys.stderr)
            failed += 1
            continue
        if not is_raster(data, name.rpartition(".")[2]):
            print(
                f"Warning: {url} is not a raster image within {MAX_AVATAR_BYTES} bytes; "
                f"skipping {name}.",
                file=sys.stderr,
            )
            failed += 1
            continue
        _write_atomic(data, output_dir / name)
        downloaded += 1

    copied = 0
    for name, sprite in sorted(sprites.items()):
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
        _write_atomic(data, output_dir / name)
        copied += 1

    removed = 0
    for path in output_dir.iterdir():
        if path.name not in wanted and path.name not in sprites and (
            path.is_symlink() or path.is_file()
        ):
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
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
