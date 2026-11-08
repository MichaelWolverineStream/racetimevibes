#!/usr/bin/env python3
"""Name the ladder seasons.

Seasons 1 to 10 carry fixed retro names: the short and full name of an ALTTP
dungeon, in order. From season 11 on, a season is named after an animal that the
previous season's champion chose. The animals are the one hand-maintained input,
`season-names.json` at the repository root; `load` validates it and
`published_names` gives the names `participants.json` publishes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

FIRST_CHAMPION_SEASON = 11
RETRO_NAMES = (
    ("EP", "Eastern Palace"),
    ("DP", "Desert Palace"),
    ("ToH", "Tower of Hera"),
    ("PoD", "Palace of Darkness"),
    ("SP", "Swamp Palace"),
    ("SW", "Skull Woods"),
    ("TT", "Thieves' Town"),
    ("IP", "Ice Palace"),
    ("MM", "Misery Mire"),
    ("TR", "Turtle Rock"),
)
assert len(RETRO_NAMES) == FIRST_CHAMPION_SEASON - 1

NAMES_PATH = Path(__file__).resolve().parents[1] / "season-names.json"
SCHEMA_VERSION = 1
MAX_ANIMAL_LENGTH = 40
SEASON_KEY_PATTERN = re.compile(r"[1-9][0-9]*")


class SeasonNamesError(RuntimeError):
    """The season names file is missing or invalid."""


def _retro_words() -> set[str]:
    return {word.casefold() for pair in RETRO_NAMES for word in pair}


def load(path: Path = NAMES_PATH) -> dict[int, str]:
    """Return {season: animal} from the names file, or raise SeasonNamesError naming the problem."""

    def fail(problem: str) -> SeasonNamesError:
        return SeasonNamesError(f"{path}: {problem}")

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
    if set(body) != {"schema_version", "seasons"}:
        raise fail(
            f"must have exactly the keys schema_version and seasons, not {sorted(body)}"
        )
    if body["schema_version"] != SCHEMA_VERSION or isinstance(body["schema_version"], bool):
        raise fail(f"schema_version must be {SCHEMA_VERSION}, not {body['schema_version']!r}")
    seasons = body["seasons"]
    if not isinstance(seasons, dict):
        raise fail("seasons must be an object")

    retro = _retro_words()
    animals: dict[int, str] = {}
    seen: dict[str, int] = {}
    for key, entry in seasons.items():
        if not SEASON_KEY_PATTERN.fullmatch(key) or not key.isascii():
            raise fail(f"season key {key!r} is not a plain whole number")
        season = int(key)
        if season < FIRST_CHAMPION_SEASON:
            raise fail(
                f"season {season} is named by champions only from season {FIRST_CHAMPION_SEASON}"
            )
        if not isinstance(entry, dict) or set(entry) != {"animal"}:
            raise fail(f"season {season} must be an object with exactly the key animal")
        animal = entry["animal"]
        if not isinstance(animal, str) or not animal:
            raise fail(f"season {season} animal must be a non-empty string")
        if animal != animal.strip():
            raise fail(f"season {season} animal {animal!r} has leading or trailing whitespace")
        if len(animal) > MAX_ANIMAL_LENGTH:
            raise fail(f"season {season} animal is longer than {MAX_ANIMAL_LENGTH} characters")
        folded = animal.casefold()
        if folded in seen:
            raise fail(f"seasons {seen[folded]} and {season} share the animal {animal!r}")
        if folded in retro:
            raise fail(f"season {season} animal {animal!r} is a retro season name")
        seen[folded] = season
        animals[season] = animal
    return animals


def published_names(animals: Mapping[int, str]) -> dict[str, JsonObject]:
    """Return the published name of every season, keyed by season number in numeric order."""
    names: dict[str, JsonObject] = {
        str(season): {"kind": "retro", "text": short, "full": full}
        for season, (short, full) in enumerate(RETRO_NAMES, start=1)
    }
    for season in sorted(animals):
        names[str(season)] = {"kind": "champion", "text": animals[season]}
    return names
