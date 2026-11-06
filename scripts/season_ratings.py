#!/usr/bin/env python3
"""Season membership, and the ledgers that lock a finished season's ratings for good.

A season's ratings are the ladder rating run on that season's races alone (see
`ladder_rating.rate_by_season`). While a season is open, every build recomputes them.
Once it has ended and settled (see `lock_reason`), `build_stats.py --lock-seasons`
writes them to a ledger, `season-ratings/season-N.json`, keyed by race slug, user ID
and mode name. From then on every build takes that season's races and points from its
ledger, so a later change to the rating never rewrites a finished season.

A ledger is written once and never overwritten. Deleting one is the only way to lock
its season again, with the rating as it is then.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/build_stats.py` and `from scripts import season_ratings`.
try:
    from scripts import race_archive, schedule_archive
except ImportError:
    import race_archive
    import schedule_archive


LEDGER_DIRECTORY = "season-ratings"
LEDGER_SCHEMA_VERSION = 1
LEDGER_FILE = re.compile(r"season-(\d+)\.json")
# A season locks only once this many slots follow the next season's start and none of
# them is the season's own. A misfiled slot that put the next season's start too early
# would be followed by the season's own slots, so it can never fix a wrong boundary.
LOCK_CONFIRM_SLOTS = 10

JsonObject = dict[str, Any]
# An entry's season points and season-mode points after a race.
Points = tuple[int, int]


class SeasonLedgerError(RuntimeError):
    """Raised when a season ledger is malformed, or would be overwritten."""


@dataclass(frozen=True)
class LedgerRace:
    mode: str
    points: dict[str, Points]  # by user ID, in entry order


@dataclass(frozen=True)
class Ledger:
    season: int
    start: datetime
    end: datetime
    races: dict[str, LedgerRace]  # by race slug, in race order


@dataclass(frozen=True)
class Race:
    """What season membership reads from an included race."""

    name: str
    started_at: datetime
    mode: str
    user_ids: tuple[str, ...]


@dataclass(frozen=True)
class Assignment:
    """Each race's season, or None, and why races in locked seasons got none."""

    seasons: list[int | None]
    # Races the season spans place in a locked season that its ledger does not hold.
    left_out: int
    # Races a ledger holds whose mode or entrants no longer match it.
    mismatched: int


def ledger_dir_for(archive_dir: Path) -> Path:
    """The ledger directory beside an archive directory."""
    return archive_dir.parent / LEDGER_DIRECTORY


# Membership -------------------------------------------------------------------


def assign(
    races: list[Race],
    starts: list[tuple[int, datetime]],
    ledgers: Mapping[int, Ledger],
) -> Assignment:
    """Return each race's season.

    A race in a ledger belongs to that ledger's season, as long as its mode and
    entrants still match the ledger. Any other race belongs to the season its start
    falls in (`schedule_archive.season_of`), unless that season is locked: a ledger
    holds the whole season, so such a race gets no season.
    """
    locked = {name: ledger.season for ledger in ledgers.values() for name in ledger.races}
    seasons: list[int | None] = []
    left_out = 0
    mismatched = 0
    for race in races:
        season = locked.get(race.name)
        if season is not None:
            entry = ledgers[season].races[race.name]
            if entry.mode != race.mode or set(entry.points) != set(race.user_ids):
                mismatched += 1
                season = None
        else:
            season = schedule_archive.season_of(race.started_at, starts)
            if season in ledgers:
                left_out += 1
                season = None
        seasons.append(season)
    return Assignment(seasons, left_out, mismatched)


def overlay(
    races: list[Race],
    seasons: list[int | None],
    computed: list[list[Points]],
    ledgers: Mapping[int, Ledger],
) -> list[list[Points] | None]:
    """Return each race's season points per entry, or None for a race without a season.

    A race in an open season keeps its `computed` points. A race in a locked season
    takes the points its ledger holds, whatever the rating computes today.
    """
    result: list[list[Points] | None] = []
    for race, season, points in zip(races, seasons, computed, strict=True):
        if season is None:
            result.append(None)
        elif season in ledgers:
            locked = ledgers[season].races[race.name].points
            result.append([locked[user_id] for user_id in race.user_ids])
        else:
            result.append(points)
    return result


def published_seasons(
    seasons: list[int | None],
    dates: list[str],
    starts: list[tuple[int, datetime]],
    ledgers: Mapping[int, Ledger],
) -> list[JsonObject]:
    """Return every season that holds a race, oldest first.

    Each gives its number, the Eastern dates of its first and latest race, and
    whether it has ended: it is locked, or a later season has started.
    """
    newest_start = max((number for number, _start in starts), default=0)
    found: dict[int, list[str]] = {}
    for season, day in zip(seasons, dates, strict=True):
        if season is not None:
            found.setdefault(season, []).append(day)
    return [
        {
            "n": season,
            "first": min(found[season]),
            "last": max(found[season]),
            "ended": season in ledgers or newest_start > season,
        }
        for season in sorted(found)
    ]


# Locking ----------------------------------------------------------------------


def lock_reason(
    season: int,
    starts: list[tuple[int, datetime]],
    rows: list[JsonObject],
    races: list[JsonObject],
    newest_started_at: datetime,
    now: datetime,
) -> str | None:
    """Return why `season` cannot be locked yet, or None once it can.

    `races` are the season's included races as archived. A season locks once:
    - the next season started at least `schedule_archive.SETTLE` ago;
    - LOCK_CONFIRM_SLOTS slots follow the next season's start, none of them this season's;
    - the archive holds a race that started after the next season's start, so the race
      sync has covered the season's end;
    - every race of the season is settled (`race_archive.is_settled`): recorded,
      unrecordable, or old enough that the archive no longer refreshes it. None of
      them still awaits recording.
    """
    later = [(number, start) for number, start in starts if number > season]
    if not later:
        return "the next season has not started"
    next_season, next_start = later[0]
    if now < next_start + schedule_archive.SETTLE:
        return f"season {next_season} started less than {schedule_archive.SETTLE} ago"
    following = sorted(
        (row for row in rows if schedule_archive.slot_time(row) >= next_start),
        key=lambda row: (int(row["time"]), row["id"]),
    )[:LOCK_CONFIRM_SLOTS]
    if len(following) < LOCK_CONFIRM_SLOTS:
        return f"fewer than {LOCK_CONFIRM_SLOTS} slots follow the start of season {next_season}"
    if any(row["season"] == season for row in following):
        return f"season {season} still has slots among the first {LOCK_CONFIRM_SLOTS} of season {next_season}"
    if newest_started_at <= next_start:
        return f"the archive has no race from season {next_season} yet"
    waiting = sum(1 for race in races if not race_archive.is_settled(race, now))
    if waiting:
        return f"{waiting} of its races still await recording"
    return None


def lock_seasons(
    archive_dir: Path,
    ledger_dir: Path,
    participants: JsonObject,
    now: datetime,
) -> list[tuple[int, str | None]]:
    """Lock every season without a ledger that is ready, from the payload just built.

    Returns `(season, reason)` for every season in the payload without a ledger
    before this call: the reason it is not locked yet, or None if it was locked now.
    """
    rows = schedule_archive.load_schedule(archive_dir)
    starts = schedule_archive.season_starts(rows)
    ledgers = load_ledgers(ledger_dir)
    archived = {
        envelope["race"]["name"]: envelope["race"]
        for envelope in race_archive.load_races(archive_dir)
    }
    races = participants["races"]
    if not races:
        return []
    newest_started_at = max(
        race_archive.parse_timestamp(archived[race["r"]]["started_at"], "started_at")
        for race in races
    )
    span_starts = dict(starts)
    outcomes: list[tuple[int, str | None]] = []
    for season in sorted({race["s"] for race in races if "s" in race} - set(ledgers)):
        season_races = [race for race in races if race.get("s") == season]
        reason = lock_reason(
            season,
            starts,
            rows,
            [archived[race["r"]] for race in season_races],
            newest_started_at,
            now,
        )
        if reason is None:
            end = next(start for number, start in starts if number > season)
            write_ledger(
                ledger_dir,
                ledger_payload(season, span_starts[season], end, participants, season_races),
            )
        outcomes.append((season, reason))
    return outcomes


def ledger_payload(
    season: int,
    start: datetime,
    end: datetime,
    participants: JsonObject,
    races: list[JsonObject],
) -> JsonObject:
    """Return a season's ledger from the published payload's races of that season."""
    players = participants["players"]
    modes = participants["modes"]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "season": season,
        "start": race_archive._format_timestamp(start),
        "end": race_archive._format_timestamp(end),
        "rating": participants["rating"],
        "races": [
            {
                "r": race["r"],
                "mode": modes[race["m"]],
                "e": [[players[entry[0]]["id"], entry[6], entry[7]] for entry in race["e"]],
            }
            for race in races
        ],
    }


# Ledger files -----------------------------------------------------------------


def write_ledger(directory: Path, payload: JsonObject) -> Path:
    """Write a new ledger. Refuses to replace one: a locked season never changes."""
    label = f"Season {payload.get('season')!r} ledger"
    ledger = parse_ledger(payload, label)
    path = directory / f"season-{ledger.season}.json"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as output:
        output.write(json.dumps(payload, separators=(",", ":")))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError as error:
        raise SeasonLedgerError(
            f"{path} already exists; a locked season is never rewritten"
        ) from error
    finally:
        temporary.unlink()
    return path


def load_ledgers(directory: Path) -> dict[int, Ledger]:
    """Return every ledger in `directory` by season; a missing directory holds none."""
    ledgers: dict[int, Ledger] = {}
    owner: dict[str, int] = {}
    if not directory.is_dir():
        return ledgers
    for path in sorted(directory.glob("season-*.json")):
        match = LEDGER_FILE.fullmatch(path.name)
        if not match:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SeasonLedgerError(f"{path} is not valid JSON") from error
        ledger = parse_ledger(payload, str(path))
        if ledger.season != int(match.group(1)):
            raise SeasonLedgerError(f"{path} holds season {ledger.season}")
        for name in ledger.races:
            if name in owner:
                raise SeasonLedgerError(
                    f"Race {name} is in the ledgers of seasons {owner[name]} and {ledger.season}"
                )
            owner[name] = ledger.season
        ledgers[ledger.season] = ledger
    return ledgers


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_ledger(payload: Any, label: str) -> Ledger:
    """Validate a ledger's JSON and return it, or raise SeasonLedgerError."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "season", "start", "end", "rating", "races"}
        or payload["schema_version"] != LEDGER_SCHEMA_VERSION
        or not _is_int(payload["season"])
        or payload["season"] < 1
        or not isinstance(payload["rating"], dict)
        or not isinstance(payload["races"], list)
    ):
        raise SeasonLedgerError(f"{label} is not a season ledger")
    try:
        start = race_archive.parse_timestamp(payload["start"], "start")
        end = race_archive.parse_timestamp(payload["end"], "end")
    except ValueError as error:
        raise SeasonLedgerError(f"{label} has an invalid start or end") from error
    if end <= start:
        raise SeasonLedgerError(f"{label} ends before it starts")

    races: dict[str, LedgerRace] = {}
    for index, race in enumerate(payload["races"]):
        if (
            not isinstance(race, dict)
            or set(race) != {"r", "mode", "e"}
            or not isinstance(race["r"], str)
            or not isinstance(race["mode"], str)
            or not isinstance(race["e"], list)
            or not race["e"]
        ):
            raise SeasonLedgerError(f"{label} race {index} is malformed")
        points: dict[str, Points] = {}
        for entry in race["e"]:
            if (
                not isinstance(entry, list)
                or len(entry) != 3
                or not isinstance(entry[0], str)
                or not _is_int(entry[1])
                or not _is_int(entry[2])
                or entry[0] in points
            ):
                raise SeasonLedgerError(f"{label} race {race['r']} has a malformed entry")
            points[entry[0]] = (entry[1], entry[2])
        if race["r"] in races:
            raise SeasonLedgerError(f"{label} lists race {race['r']} twice")
        races[race["r"]] = LedgerRace(race["mode"], points)
    if not races:
        raise SeasonLedgerError(f"{label} holds no races")
    return Ledger(payload["season"], start, end, races)
