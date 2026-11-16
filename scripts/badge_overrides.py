#!/usr/bin/env python3
"""Give racers badges by hand.

`badge-overrides.json` at the repository root is hand-maintained, like
`season-names.json`. Each override gives one racer one existing badge, unlocked in
their last included race on an Eastern date, with an optional note for the badge's
tooltip. `load` validates the file on its own, and `resolve` checks it against the
racers and races of a build and gives what `highlights.compute` and
`participants.json` need.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

# Supports both `python3 scripts/build_stats.py` and `from scripts import badge_overrides`.
try:
    from scripts import highlights
except ImportError:
    import highlights

JsonObject = dict[str, Any]

OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "badge-overrides.json"
SCHEMA_VERSION = 1
MAX_NOTE_LENGTH = 160
# First Blood is the badge of the first-win tag (see highlights.compute), so only a win unlocks it.
NOT_BY_HAND = frozenset({"first_blood"})
REQUIRED_KEYS = frozenset({"racer", "badge", "date"})
OPTIONAL_KEYS = frozenset({"note"})
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class BadgeOverridesError(RuntimeError):
    """The badge overrides file is missing or invalid, or doesn't match the races."""


class Override(NamedTuple):
    """One badge given by hand."""

    racer: str  # racetime.gg user ID
    badge: str  # an id in highlights.BADGES
    date: str  # Eastern date, YYYY-MM-DD
    note: str | None


class Resolved(NamedTuple):
    """The overrides matched to a build's racers and races."""

    # (player index, badge id) -> index of the race that unlocks it.
    granted: dict[tuple[int, str], int]
    # Player index -> {badge id: note}, for the overrides that have a note.
    notes: dict[int, dict[str, str]]


def load(path: Path = OVERRIDES_PATH) -> list[Override]:
    """Return the overrides in file order, or raise BadgeOverridesError naming the problem."""

    def fail(problem: str) -> BadgeOverridesError:
        return BadgeOverridesError(f"{path}: {problem}")

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise fail("the file is missing") from error
    try:
        body = json.loads(text)
    except json.JSONDecodeError as error:
        raise fail(f"is not valid JSON ({error})") from error
    if not isinstance(body, dict):
        raise fail("must hold a JSON object")
    if set(body) != {"schema_version", "overrides"}:
        raise fail(
            f"must have exactly the keys schema_version and overrides, not {sorted(body)}"
        )
    if body["schema_version"] != SCHEMA_VERSION or isinstance(body["schema_version"], bool):
        raise fail(f"schema_version must be {SCHEMA_VERSION}, not {body['schema_version']!r}")
    entries = body["overrides"]
    if not isinstance(entries, list):
        raise fail("overrides must be a list")

    badge_ids = {badge.id for badge in highlights.BADGES}
    overrides: list[Override] = []
    seen: dict[tuple[str, str], int] = {}
    for number, entry in enumerate(entries, start=1):
        where = f"override {number}"
        if not isinstance(entry, dict):
            raise fail(f"{where} must be an object")
        keys = set(entry)
        if not REQUIRED_KEYS <= keys <= REQUIRED_KEYS | OPTIONAL_KEYS:
            raise fail(
                f"{where} must have the keys racer, badge and date, and may have note, "
                f"not {sorted(keys)}"
            )
        racer = entry["racer"]
        if not isinstance(racer, str) or not racer or racer != racer.strip():
            raise fail(f"{where} racer must be a user ID without surrounding whitespace")
        badge = entry["badge"]
        if not isinstance(badge, str) or badge not in badge_ids:
            raise fail(f"{where} badge {badge!r} is not a badge id in highlights.BADGES")
        if badge in NOT_BY_HAND:
            raise fail(f"{where} badge {badge} can't be given by hand: only a win unlocks it")
        day = entry["date"]
        if not isinstance(day, str) or not DATE_PATTERN.fullmatch(day):
            raise fail(f"{where} date {day!r} must be written YYYY-MM-DD")
        try:
            date.fromisoformat(day)
        except ValueError as error:
            raise fail(f"{where} date {day} is not a real date") from error
        note = entry.get("note")
        if "note" in entry:
            if not isinstance(note, str) or not note:
                raise fail(f"{where} note must be a non-empty string; leave the key out for no note")
            if note != note.strip():
                raise fail(f"{where} note has leading or trailing whitespace")
            if not note.isprintable():
                raise fail(f"{where} note must be one line of printable text")
            if len(note) > MAX_NOTE_LENGTH:
                raise fail(f"{where} note is longer than {MAX_NOTE_LENGTH} characters")
        pair = (racer, badge)
        if pair in seen:
            raise fail(f"overrides {seen[pair]} and {number} both give {racer} the badge {badge}")
        seen[pair] = number
        overrides.append(Override(racer, badge, day, note))
    return overrides


def resolve(
    overrides: Sequence[Override],
    records: Sequence[JsonObject],
    players: Sequence[JsonObject],
) -> Resolved:
    """Match each override to its racer's last race on its date.

    `records` are the included races in start order, each with its Eastern date `d` and
    entries `e` whose first item is a player index; `players` are the published racers,
    each with a user ID `id` and a `name`. Raises BadgeOverridesError when a racer is
    not among `players` or entered no race on the override's date.
    """
    player_index = {player["id"]: index for index, player in enumerate(players)}
    # Each racer's last race on each day: races come in start order, so later ones win.
    last_race: dict[tuple[int, str], int] = {}
    for race_index, record in enumerate(records):
        for entry in record["e"]:
            last_race[(entry[0], record["d"])] = race_index

    granted: dict[tuple[int, str], int] = {}
    notes: dict[int, dict[str, str]] = {}
    for override in overrides:
        where = f"badge override {override.badge} for {override.racer} on {override.date}"
        index = player_index.get(override.racer)
        if index is None:
            raise BadgeOverridesError(f"{where}: no included race has this racer")
        race_index = last_race.get((index, override.date))
        if race_index is None:
            raise BadgeOverridesError(
                f"{where}: {players[index]['name']} entered no included race that day"
            )
        granted[(index, override.badge)] = race_index
        if override.note is not None:
            notes.setdefault(index, {})[override.badge] = override.note
    return Resolved(granted, notes)
