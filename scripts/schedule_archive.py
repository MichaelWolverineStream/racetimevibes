#!/usr/bin/env python3
"""Mirror the alttpr.racing ladder schedule and modes into archive/ladder-schedule/."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/schedule_archive.py` and `from scripts import schedule_archive`.
try:
    from scripts import race_archive
except ImportError:
    import race_archive


BASE_URL = "https://alttpr.racing/api/v1"
SCHEDULE_DIRECTORY = "ladder-schedule"
MODES_FILE = "modes.json"
SEASON_FILE = re.compile(r"season-(\d+)\.json")
# Far above the real number of seasons; it only stops a server that never answers 404.
MAX_SEASONS = 1000

JsonObject = dict[str, Any]
ModesFetcher = Callable[[], Any]
SeasonFetcher = Callable[[int], Any]


@dataclass(frozen=True)
class SyncResult:
    seasons: int
    slots: int
    files_changed: int


def _get_json_list(url: str, label: str) -> Any:
    """Fetch one JSON body. The server labels JSON as text/html, so only the body is checked."""
    body = race_archive._get_bytes(url, label, "application/json")
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise race_archive.ArchiveError(f"{label} returned invalid JSON") from error


def fetch_modes() -> Any:
    return _get_json_list(f"{BASE_URL}/modes", "Modes")


def fetch_season(season: int) -> Any:
    """Fetch one season's schedule, returning None once the season does not exist (404)."""
    try:
        return _get_json_list(f"{BASE_URL}/schedule/{season}", f"Schedule season {season}")
    except race_archive.ArchiveError as error:
        cause = error.__cause__
        if isinstance(cause, urllib.error.HTTPError) and cause.code == 404:
            return None
        raise


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _list_of_objects(payload: Any, label: str) -> list[JsonObject]:
    if not isinstance(payload, list):
        raise race_archive.ArchiveError(f"{label} is not a list")
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise race_archive.ArchiveError(f"{label} row {index} is not an object")
    return payload


def validate_modes(payload: Any, label: str = "Modes") -> list[JsonObject]:
    for index, mode in enumerate(_list_of_objects(payload, label)):
        if (
            not _is_int(mode.get("id"))
            or not _is_int(mode.get("archetype"))
            or not isinstance(mode.get("name"), str)
            or not isinstance(mode.get("active"), bool)
        ):
            raise race_archive.ArchiveError(f"{label} row {index} is not a valid mode")
    return payload


def validate_season(payload: Any, season: int, label: str | None = None) -> list[JsonObject]:
    label = label or f"Schedule season {season}"
    for index, row in enumerate(_list_of_objects(payload, label)):
        time_value = row.get("time")
        if (
            not _is_int(row.get("id"))
            or not _is_int(row.get("season"))
            or not _is_int(row.get("mode"))
            or not (row.get("raceId") is None or _is_int(row.get("raceId")))
            or not isinstance(time_value, str)
            or not time_value.isascii()
            or not time_value.isdigit()
        ):
            raise race_archive.ArchiveError(f"{label} row {index} is not a valid slot")
        if row["season"] != season:
            raise race_archive.ArchiveError(
                f"{label} row {index} belongs to season {row['season']}"
            )
    return payload


def _read_envelope(path: Path, key: str) -> list[JsonObject]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise race_archive.ArchiveError(f"{path} is not valid JSON") from error
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema_version") != race_archive.ARCHIVE_SCHEMA_VERSION
        or not isinstance(envelope.get("fetched_at"), str)
        or key not in envelope
    ):
        raise race_archive.ArchiveError(f"{path} is not a schedule archive file")
    try:
        race_archive.parse_timestamp(envelope["fetched_at"], "fetched_at")
    except ValueError as error:
        raise race_archive.ArchiveError(f"{path} has no valid fetched_at") from error
    return envelope[key]


def _read_modes(path: Path) -> list[JsonObject]:
    return validate_modes(_read_envelope(path, "modes"), str(path))


def _read_season(path: Path, season: int) -> list[JsonObject]:
    return validate_season(_read_envelope(path, "schedule"), season, str(path))


def _season_paths(directory: Path) -> list[tuple[int, Path]]:
    """Return (season, path) for every season file, in numeric season order."""
    found = []
    for path in directory.glob("season-*.json"):
        match = SEASON_FILE.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def load_modes(archive_dir: Path = race_archive.ARCHIVE_DIR) -> list[JsonObject]:
    """Return the archived modes verbatim, or [] if they were never fetched."""
    path = archive_dir / SCHEDULE_DIRECTORY / MODES_FILE
    return _read_modes(path) if path.exists() else []


def load_schedule(archive_dir: Path = race_archive.ARCHIVE_DIR) -> list[JsonObject]:
    """Return every archived slot in season order, once per slot ID.

    The same ID with identical content is kept once. The same ID with different content
    raises ArchiveError.
    """
    directory = archive_dir / SCHEDULE_DIRECTORY
    slots: dict[int, JsonObject] = {}
    for season, path in _season_paths(directory):
        for row in _read_season(path, season):
            known = slots.get(row["id"])
            if known is None:
                slots[row["id"]] = row
            elif known != row:
                raise race_archive.ArchiveError(
                    f"Schedule slot {row['id']} appears twice with different content"
                )
    return list(slots.values())


def _write_if_changed(
    path: Path,
    key: str,
    body: list[JsonObject],
    read: Callable[[Path], list[JsonObject]],
    fetched_at: str,
) -> bool:
    if path.exists() and read(path) == body:
        return False
    race_archive.write_json_atomic(
        {
            "schema_version": race_archive.ARCHIVE_SCHEMA_VERSION,
            "fetched_at": fetched_at,
            key: body,
        },
        path,
    )
    return True


def sync(
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    *,
    fetch_modes: ModesFetcher = fetch_modes,
    fetch_season: SeasonFetcher = fetch_season,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SyncResult:
    """Fetch the modes and every season, then rewrite the files whose content changed.

    Everything is fetched and validated before any file is written. Seasons are fetched
    from 1 until the first one the server does not have. Files are never deleted.
    """
    now = clock or (lambda: datetime.now(UTC))
    modes = validate_modes(fetch_modes())

    seasons: list[tuple[int, list[JsonObject]]] = []
    for season in range(1, MAX_SEASONS + 1):
        sleep(race_archive.REQUEST_DELAY_SECONDS)
        payload = fetch_season(season)
        if payload is None:
            break
        seasons.append((season, validate_season(payload, season)))
    else:
        raise race_archive.ArchiveError(f"Schedule has more than {MAX_SEASONS} seasons")

    directory = archive_dir / SCHEDULE_DIRECTORY
    fetched_at = race_archive._format_timestamp(now())
    changed = _write_if_changed(
        directory / MODES_FILE, "modes", modes, _read_modes, fetched_at
    )
    files_changed = int(changed)
    for season, rows in seasons:
        files_changed += _write_if_changed(
            directory / f"season-{season}.json",
            "schedule",
            rows,
            lambda path, season=season: _read_season(path, season),
            fetched_at,
        )
    return SyncResult(
        seasons=len(seasons),
        slots=sum(len(rows) for _, rows in seasons),
        files_changed=files_changed,
    )


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
        result = sync(args.archive)
    except race_archive.ArchiveError as error:
        print(f"Schedule sync failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Schedule: {result.seasons} seasons, {result.slots} slots, "
        f"{result.files_changed} files changed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
