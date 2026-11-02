#!/usr/bin/env python3
"""Mirror the racetime.gg profile bios of recent racers into archive/racer-bios.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/racer_bios.py` and `from scripts import racer_bios`.
try:
    from scripts import pokemon_avatars, race_archive
except ImportError:
    import pokemon_avatars
    import race_archive


RACER_BIOS_FILE = "racer-bios.json"
PROFILE_URL = "https://racetime.gg/user/{user_id}"
# racetime.gg user IDs are alphanumeric; anything else is never put into a URL.
USER_ID = re.compile(r"[A-Za-z0-9]+")
# The profile_bio model field allows 2,000 characters.
MAX_BIO_CHARS = 2000
MAX_PROFILE_BYTES = 1_000_000
BIO_REQUEST_DELAY_SECONDS = 1.0
# This many failures in a row mean racetime.gg is refusing profile pages.
MAX_CONSECUTIVE_FAILURES = 5

JsonObject = dict[str, Any]
Loader = Callable[[str], bytes | None]


class ProfileError(RuntimeError):
    """Raised when a page is not a recognisable racetime.gg profile."""


class _BioParser(HTMLParser):
    """Collect the text of the profile's `<p class="bio">` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.is_profile = False
        self.found_bio = False
        self._depth = 0
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "div" and "user-profile" in classes:
            self.is_profile = True
        if self._depth:
            if tag == "p":
                self._depth += 1
        elif tag == "p" and "bio" in classes and not self.found_bio:
            self.found_bio = True
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag == "p":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)


def extract_bio(html: str) -> str | None:
    """Return the bio text of a racetime.gg profile page, or None if it has none.

    Raises ProfileError if the page has no `user-profile` block, such as a challenge or
    error page. The text is everything inside the first `<p class="bio">`, links included.
    """
    parser = _BioParser()
    parser.feed(html)
    parser.close()
    if not parser.is_profile:
        raise ProfileError("the page is not a racetime.gg profile")
    bio = parser.text.strip()[:MAX_BIO_CHARS]
    return bio or None


def load_racer_bios(
    archive_dir: Path = race_archive.ARCHIVE_DIR,
) -> dict[str, list[str | None]]:
    """Return each racer's profile bio and when the profile was last fetched.

    Maps a user ID to [bio text or None, checked-at timestamp].
    """
    path = archive_dir / RACER_BIOS_FILE
    if not path.exists():
        return {}
    try:
        bios = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise race_archive.ArchiveError(f"{path} is not valid JSON") from error
    if not isinstance(bios, dict):
        raise race_archive.ArchiveError(f"{path} is not a map of user IDs to bios")
    for entry in bios.values():
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not (entry[0] is None or isinstance(entry[0], str))
        ):
            raise race_archive.ArchiveError(f"{path} is not a map of user IDs to bios")
        try:
            race_archive.parse_timestamp(entry[1], "checked-at time")
        except ValueError as error:
            raise race_archive.ArchiveError(
                f"{path} has a bio without a valid time"
            ) from error
    return bios


def racers_to_check(envelopes: list[JsonObject], bios: Mapping[str, list]) -> list[str]:
    """Return the sorted IDs of racers whose profile is due for a fetch.

    A racer is due if they have no stored bio, or entered an included race that finished
    after their profile was last fetched.
    """
    # build_stats imports modules that import this one, so import it at call time.
    try:
        from scripts import build_stats
    except ImportError:
        import build_stats

    latest: dict[str, datetime] = {}
    for envelope in build_stats.included_envelopes(envelopes):
        race = envelope["race"]
        completed = race_archive._completion_time(race)
        for entrant in race.get("entrants") or []:
            user = entrant.get("user") if isinstance(entrant, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            if isinstance(user_id, str) and user_id:
                if user_id not in latest or completed > latest[user_id]:
                    latest[user_id] = completed

    return sorted(
        user_id
        for user_id, completed in latest.items()
        if user_id not in bios
        or completed > race_archive.parse_timestamp(bios[user_id][1], "checked-at time")
    )


def fetch_profile(url: str) -> bytes | None:
    """Fetch a profile page, returning None if the profile is hidden or deleted (404)."""
    try:
        return race_archive._get_bytes(
            url, f"Profile {url}", "text/html", limit=MAX_PROFILE_BYTES
        )
    except race_archive.ArchiveError as error:
        cause = error.__cause__
        if isinstance(cause, urllib.error.HTTPError) and cause.code == 404:
            return None
        raise


def sync(
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    *,
    loader: Loader | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Fetch the profile of every racer who raced since their bio was last checked.

    A profile that fails to load or parse keeps its old entry, so it is due again on the
    next run. After MAX_CONSECUTIVE_FAILURES failures in a row the run stops early.
    Returns the counts of racers due, checked and failed, and whether the run stopped.
    """
    load = loader or fetch_profile
    now = clock or (lambda: datetime.now(UTC))
    bios = load_racer_bios(archive_dir)
    due = racers_to_check(race_archive.load_races(archive_dir), bios)

    checked = failed = consecutive = 0
    requested = stopped = False
    for user_id in due:
        if not USER_ID.fullmatch(user_id):
            print(f"Warning: invalid user ID {user_id!r}; skipping it.", file=sys.stderr)
            failed += 1
            continue
        if requested:
            time.sleep(BIO_REQUEST_DELAY_SECONDS)
        requested = True

        try:
            page = load(PROFILE_URL.format(user_id=user_id))
            if page is None:
                bio = None
            elif len(page) > MAX_PROFILE_BYTES:
                raise ProfileError(f"the page is longer than {MAX_PROFILE_BYTES} bytes")
            else:
                bio = extract_bio(page.decode("utf-8", errors="replace"))
        except (race_archive.ArchiveError, OSError, ProfileError) as error:
            print(f"Warning: {error}; skipping {user_id}.", file=sys.stderr)
            failed += 1
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                print(
                    "Warning: racetime.gg is refusing profile pages; "
                    "stopping with the remaining racers still due.",
                    file=sys.stderr,
                )
                stopped = True
                break
            continue

        consecutive = 0
        bios[user_id] = [bio, race_archive._format_timestamp(now())]
        checked += 1

    if checked:
        race_archive.write_json_atomic(
            dict(sorted(bios.items())), archive_dir / RACER_BIOS_FILE
        )
    return {"due": len(due), "checked": checked, "failed": failed, "stopped": stopped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=race_archive.ARCHIVE_DIR,
        help=f"raw race archive directory (default: {race_archive.ARCHIVE_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = sync(args.archive)
        bios = load_racer_bios(args.archive)
    except race_archive.ArchiveError as error:
        print(f"Bio sync failed: {error}", file=sys.stderr)
        return 1
    checked = f"Bios: {summary['checked']} checked, {summary['failed']} failed"
    try:
        catalogue = pokemon_avatars.load_catalogue()
    except pokemon_avatars.PokedexError as error:
        # The count is only a report, and the bios are saved, so the refresh goes on.
        print(f"Warning: {error}; favourites were not counted.", file=sys.stderr)
        print(f"{checked}.")
        return 0
    named = sum(
        1
        for bio, _checked_at in bios.values()
        if pokemon_avatars.favourite_pokemon(bio, catalogue) is not None
    )
    print(f"{checked}; {named} name a favourite Pokémon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
