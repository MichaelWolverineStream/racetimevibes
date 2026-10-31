#!/usr/bin/env python3
"""Fetch new ALTTPR ladder races into the local raw race archive."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CATEGORY = "alttpr-ladder"
SOURCE_URL = f"https://racetime.gg/{CATEGORY}/races/data"
LEADERBOARDS_URL = f"https://racetime.gg/{CATEGORY}/leaderboards/data"
PER_PAGE = 100
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.2
NETWORK_ATTEMPTS = 3
USER_AGENT = "racetimevibes/1.0 (+static infographic data builder)"
ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "archive"
RACES_DIRECTORY = "races"
RACER_NAMES_FILE = "racer-names.json"
ARCHIVE_SCHEMA_VERSION = 1

# Moderators record finished races in batches, usually within two days. Until then
# score changes, the recorded flag, and comments can still change, so unsettled races
# are refreshed. A race that waits longer than this is frozen as last seen.
SETTLE_LIMIT = timedelta(days=14)
# Re-read this far behind the newest archived race, in case a race reaches the cached
# listing slightly after a race that completed later.
OVERLAP = timedelta(days=1)
RACE_NAME_PATTERN = re.compile(re.escape(CATEGORY) + r"/[a-z0-9]+(?:-[a-z0-9]+)*")

JsonObject = dict[str, Any]
PageLoader = Callable[[int], JsonObject]
LeaderboardsLoader = Callable[[], JsonObject]
Clock = Callable[[], datetime]


class ArchiveError(RuntimeError):
    """Raised when race data cannot be archived or the archive is invalid."""


def _get_json(url: str, label: str) -> JsonObject:
    """Fetch one JSON object with bounded retries for transient failures."""
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )

    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ArchiveError(f"{label} returned a non-object JSON response")
            return payload
        except HTTPError as error:
            is_transient = error.code == 429 or 500 <= error.code < 600
            if not is_transient or attempt == NETWORK_ATTEMPTS:
                raise ArchiveError(f"{label} failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError, HTTPException) as error:
            if attempt == NETWORK_ATTEMPTS:
                raise ArchiveError(f"{label} could not be fetched: {error}") from error
        except json.JSONDecodeError as error:
            raise ArchiveError(f"{label} returned invalid JSON") from error

        time.sleep(2 ** (attempt - 1))

    raise AssertionError("network retry loop exited unexpectedly")


def fetch_page(page: int) -> JsonObject:
    """Fetch one page of completed races, newest completion first, with entrants."""
    query = urlencode(
        {"page": page, "per_page": PER_PAGE, "show_entrants": "true"}
    )
    return _get_json(f"{SOURCE_URL}?{query}", f"Page {page}")


def fetch_leaderboards() -> JsonObject:
    """Fetch the category leaderboards, whose rankings carry current user profiles."""
    return _get_json(LEADERBOARDS_URL, "Leaderboards")


def _page_metadata(payload: JsonObject, page: int) -> tuple[int, int, list[JsonObject]]:
    count = payload.get("count")
    num_pages = payload.get("num_pages")
    races = payload.get("races")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(num_pages, int)
        or isinstance(num_pages, bool)
        or num_pages < 1
        or not isinstance(races, list)
        or any(not isinstance(race, dict) for race in races)
    ):
        raise ArchiveError(f"Page {page} has invalid pagination data")
    return count, num_pages, races


def parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} has no UTC offset")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _completion_time(race: JsonObject) -> datetime:
    value = race.get("ended_at") or race.get("cancelled_at")
    try:
        return parse_timestamp(value, "ended_at/cancelled_at")
    except (TypeError, ValueError) as error:
        name = race.get("name", "<unnamed>")
        raise ArchiveError(f"Race {name} has no valid completion timestamp") from error


def _checked_race_name(name: Any) -> str:
    if not isinstance(name, str) or not RACE_NAME_PATTERN.fullmatch(name):
        raise ArchiveError(f"Race name {name!r} is not a race slug in {CATEGORY}")
    return name


def race_path(archive_dir: Path, name: Any) -> Path:
    """Return the file that stores a race, rejecting anything but a plain race slug."""
    return archive_dir / RACES_DIRECTORY / f"{_checked_race_name(name)}.json"


def is_settled(race: JsonObject, now: datetime) -> bool:
    """Return whether a race can no longer change: recorded, unrecordable, or too old."""
    return (
        race.get("recorded") is True
        or race.get("recordable") is False
        or _completion_time(race) < now - SETTLE_LIMIT
    )


def write_json_atomic(payload: JsonObject, output_path: Path) -> None:
    """Write the payload without whitespace between tokens, replacing any old file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, separators=(",", ":"), sort_keys=False)
        output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, output_path)


def load_races(archive_dir: Path = ARCHIVE_DIR) -> list[JsonObject]:
    """Return every archived race envelope, newest completion first.

    A missing archive directory is an empty archive.
    """
    envelopes = []
    for path in (archive_dir / RACES_DIRECTORY).glob("*/*.json"):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ArchiveError(f"{path} is not valid JSON") from error
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema_version") != ARCHIVE_SCHEMA_VERSION
            or not isinstance(envelope.get("race"), dict)
        ):
            raise ArchiveError(f"{path} is not a race archive file")
        try:
            parse_timestamp(envelope.get("fetched_at"), "fetched_at")
        except ValueError as error:
            raise ArchiveError(f"{path} has no valid fetched_at timestamp") from error
        name = envelope["race"].get("name")
        if name != f"{path.parent.name}/{path.stem}" or not RACE_NAME_PATTERN.fullmatch(name):
            raise ArchiveError(f"{path} holds race {name!r}, which does not match its path")
        envelopes.append(envelope)

    envelopes.sort(
        key=lambda envelope: (
            _completion_time(envelope["race"]),
            envelope["race"]["name"],
        ),
        reverse=True,
    )
    return envelopes


def load_racer_names(archive_dir: Path = ARCHIVE_DIR) -> dict[str, str]:
    """Return the current display name of every racer seen on the leaderboards."""
    path = archive_dir / RACER_NAMES_FILE
    if not path.exists():
        return {}
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ArchiveError(f"{path} is not valid JSON") from error
    if not isinstance(names, dict) or any(
        not isinstance(name, str) or not name for name in names.values()
    ):
        raise ArchiveError(f"{path} is not a map of user IDs to names")
    return names


def _envelope(race: JsonObject, fetched_at: datetime) -> JsonObject:
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "fetched_at": _format_timestamp(fetched_at),
        "race": race,
    }


def _horizon(
    archived: dict[str, JsonObject], pending: dict[str, JsonObject]
) -> datetime | None:
    """Return how far back the listing must be re-read; None means every page."""
    if not archived:
        return None
    newest = max(_completion_time(envelope["race"]) for envelope in archived.values())
    return min(
        [newest - OVERLAP]
        + [_completion_time(envelope["race"]) for envelope in pending.values()]
    )


def _read_listing(
    page_loader: PageLoader, horizon: datetime | None, clock: Clock
) -> tuple[dict[str, tuple[JsonObject, datetime]], int, int]:
    """Read listing pages from the newest race back past the horizon.

    Returns every race seen with the time its page arrived, the API's race count from
    page one, and the number of pages read. Pages that shift while they are read only
    repeat races, so a repeat keeps its first copy.
    """
    seen: dict[str, tuple[JsonObject, datetime]] = {}
    api_count: int | None = None
    previous_completion: datetime | None = None
    page = 0
    while True:
        page += 1
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)
        payload = page_loader(page)
        fetched_at = clock()
        count, num_pages, races = _page_metadata(payload, page)
        if api_count is None:
            api_count = count

        for race in races:
            name = _checked_race_name(race.get("name"))
            completion = _completion_time(race)
            if previous_completion is not None and completion > previous_completion:
                raise ArchiveError(
                    "Races were not returned in non-increasing completion order"
                )
            previous_completion = completion
            seen.setdefault(name, (race, fetched_at))

        if not races or page >= num_pages:
            break
        if horizon is not None and previous_completion < horizon:
            break

    return seen, api_count, page


def _leaderboard_names(payload: JsonObject) -> dict[str, str]:
    leaderboards = payload.get("leaderboards")
    if not isinstance(leaderboards, list):
        raise ArchiveError("Leaderboards response has no leaderboards array")

    names: dict[str, str] = {}
    for board in leaderboards:
        rankings = board.get("rankings") if isinstance(board, dict) else None
        if not isinstance(rankings, list):
            raise ArchiveError("Leaderboards response has a board without rankings")
        for ranking in rankings:
            user = ranking.get("user") if isinstance(ranking, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            name = user.get("name") if isinstance(user, dict) else None
            if not isinstance(user_id, str) or not user_id or not isinstance(name, str):
                raise ArchiveError(
                    "Leaderboards response has a ranking without a user ID and name"
                )
            if name.strip():
                names[user_id] = name.strip()
    return names


def sync_racer_names(archive_dir: Path, leaderboards: JsonObject) -> tuple[int, int]:
    """Merge leaderboard names into the racer name map.

    The leaderboard name wins and entries are never removed. Returns how many racers
    were added and how many were renamed.
    """
    current = _leaderboard_names(leaderboards)
    names = load_racer_names(archive_dir)
    added = sum(user_id not in names for user_id in current)
    renamed = sum(
        user_id in names and names[user_id] != name for user_id, name in current.items()
    )
    if added or renamed:
        write_json_atomic(
            dict(sorted((names | current).items())), archive_dir / RACER_NAMES_FILE
        )
    return added, renamed


def sync(
    archive_dir: Path = ARCHIVE_DIR,
    *,
    page_loader: PageLoader | None = None,
    leaderboards_loader: LeaderboardsLoader | None = None,
    clock: Clock | None = None,
) -> JsonObject:
    """Archive new races, refresh unsettled ones, and update racer names.

    A race file is written when the race is first seen, rewritten while the race is
    unsettled and its data changes, and never touched once it is settled.
    """
    load_page = page_loader or fetch_page
    load_leaderboards = leaderboards_loader or fetch_leaderboards
    read_clock = clock or (lambda: datetime.now(UTC))
    now = read_clock()

    archived = {envelope["race"]["name"]: envelope for envelope in load_races(archive_dir)}
    pending = {
        name: envelope
        for name, envelope in archived.items()
        if not is_settled(envelope["race"], now)
    }
    seen, api_count, page_count = _read_listing(
        load_page, _horizon(archived, pending), read_clock
    )

    full_scan = len(archived.keys() | seen.keys()) < api_count
    if full_scan:
        time.sleep(REQUEST_DELAY_SECONDS)
        full_seen, api_count, full_page_count = _read_listing(load_page, None, read_clock)
        seen |= full_seen
        page_count += full_page_count
        archive_count = len(archived.keys() | seen.keys())
        if archive_count < api_count:
            raise ArchiveError(
                f"The API lists {api_count} races but only {archive_count} were found "
                "after reading every page"
            )

    new_races = []
    refreshed_races = []
    for name, (race, fetched_at) in seen.items():
        if name not in archived:
            new_races.append((race, fetched_at))
        elif name in pending and archived[name]["race"] != race:
            refreshed_races.append((race, fetched_at))

    # Oldest first, so an interrupted run never leaves a gap behind the newest file.
    for race, fetched_at in sorted(
        new_races + refreshed_races,
        key=lambda item: (_completion_time(item[0]), item[0]["name"]),
    ):
        write_json_atomic(_envelope(race, fetched_at), race_path(archive_dir, race["name"]))

    time.sleep(REQUEST_DELAY_SECONDS)
    added_names, renamed_names = sync_racer_names(archive_dir, load_leaderboards())

    return {
        "page_count": page_count,
        "full_scan": full_scan,
        "new_count": len(new_races),
        "refreshed_count": len(refreshed_races),
        "vanished": sorted(pending.keys() - seen.keys()),
        "archive_count": len(archived) + len(new_races),
        "api_count": api_count,
        "added_name_count": added_names,
        "renamed_name_count": renamed_names,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=ARCHIVE_DIR,
        help=f"raw race archive directory (default: {ARCHIVE_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = sync(args.archive)
    except ArchiveError as error:
        print(f"Archive sync failed: {error}", file=sys.stderr)
        return 1

    if summary["full_scan"]:
        print("The archive was missing races, so every listing page was read.")
    print(
        f"Read {summary['page_count']} listing page(s): "
        f"{summary['new_count']} new and {summary['refreshed_count']} refreshed races. "
        f"The archive holds {summary['archive_count']} races; "
        f"the API lists {summary['api_count']}."
    )
    print(
        f"Racer names: {summary['added_name_count']} added, "
        f"{summary['renamed_name_count']} renamed."
    )
    for name in summary["vanished"]:
        print(
            f"Warning: unrecorded race {name} is no longer listed; "
            "its last archived copy is kept.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
