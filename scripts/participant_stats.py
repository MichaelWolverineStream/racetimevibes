#!/usr/bin/env python3
"""Build the per-racer leaderboard dataset from fetched ladder races."""

from __future__ import annotations

import json
import os
import re
import statistics
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/build_stats.py` and `from scripts import participant_stats`.
try:
    from scripts import highlights, ladder_rating
except ImportError:
    import highlights
    import ladder_rating


PARTICIPANT_SCHEMA_VERSION = 5
AVATAR_ORIGIN = "https://racetime.gg/media/"
AVATAR_FILENAME = re.compile(r"^[\w.-]+$")
STATUS_CODES = {"done": 0, "dnf": 1, "dq": 2}
OTHER_STATUS_CODE = 3
DONE_STATUS_CODE = STATUS_CODES["done"]
DQ_STATUS_CODE = STATUS_CODES["dq"]
ALL_MODES = "all"
DURATION_PATTERN = re.compile(
    r"^P(?!$)(?:(\d+(?:\.\d+)?)D)?"
    r"(?:T(?!$)(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)

JsonObject = dict[str, Any]


class ParticipantDataError(RuntimeError):
    """Raised when entrant data cannot produce a trustworthy leaderboard."""


def parse_finish_seconds(value: str | None) -> float | None:
    """Convert an ISO 8601 duration such as PT1H23M45.6S into exact seconds."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParticipantDataError(f"Finish time {value!r} is not a string")
    match = DURATION_PATTERN.match(value)
    if not match:
        raise ParticipantDataError(f"Finish time {value!r} is not an ISO 8601 duration")
    days, hours, minutes, seconds = (
        float(part) if part else 0.0 for part in match.groups()
    )
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_finish_time(value: str | None) -> int | None:
    """Convert an ISO 8601 duration into whole seconds."""
    seconds = parse_finish_seconds(value)
    return None if seconds is None else round(seconds)


def _avatar_filename(entrant: JsonObject) -> str | None:
    """Return the avatar's filename when it is hosted on racetime.gg/media, else None."""
    user = entrant.get("user")
    url = user.get("avatar") if isinstance(user, dict) else None
    if not isinstance(url, str) or not url.startswith(AVATAR_ORIGIN):
        return None
    filename = url.removeprefix(AVATAR_ORIGIN)
    return filename if AVATAR_FILENAME.match(filename) else None


def _status_value(entrant: JsonObject) -> str:
    status = entrant.get("status")
    value = status.get("value") if isinstance(status, dict) else None
    return value if isinstance(value, str) and value else "unknown"


def _entrant_identity(entrant: JsonObject) -> tuple[str, str] | None:
    user = entrant.get("user")
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    if not isinstance(user_id, str) or not user_id:
        return None
    name = user.get("name")
    if not isinstance(name, str) or not name.strip():
        return user_id, user_id
    return user_id, name.strip()


def _has_comment(entrant: JsonObject) -> bool:
    """True for a comment that isn't blank, the ones the comments dataset keeps."""
    text = entrant.get("comment")
    return isinstance(text, str) and bool(text.strip())


def _entrant_place(entrant: JsonObject, race_name: str) -> int | None:
    place = entrant.get("place")
    if place is None:
        return None
    if not isinstance(place, int) or isinstance(place, bool) or place < 1:
        raise ParticipantDataError(
            f"Race {race_name} has entrant place {place!r} that is not a positive integer"
        )
    return place


def _race_entries(race: JsonObject, diagnostics: Counter[str]) -> list[list[Any]]:
    race_name = race.get("name", "<unnamed>")
    entrants = race.get("entrants")
    if not isinstance(entrants, list):
        raise ParticipantDataError(f"Race {race_name} is missing its entrants array")

    entries: list[list[Any]] = []
    seen: set[str] = set()
    for entrant in entrants:
        if not isinstance(entrant, dict):
            raise ParticipantDataError(
                f"Race {race_name} contains a malformed entrant record"
            )
        status_value = _status_value(entrant)
        diagnostics[f"status:{status_value}"] += 1
        identity = _entrant_identity(entrant)
        if identity is None:
            diagnostics["unidentified_entry_count"] += 1
            continue
        user_id, name = identity
        if user_id in seen:
            raise ParticipantDataError(
                f"Race {race_name} contains a duplicate entrant user ID"
            )
        seen.add(user_id)
        status_code = STATUS_CODES.get(status_value, OTHER_STATUS_CODE)
        place = _entrant_place(entrant, race_name)
        seconds = parse_finish_seconds(entrant.get("finish_time"))
        if status_code == DONE_STATUS_CODE:
            if place is None:
                diagnostics["finishes_without_place"] += 1
            if seconds is None:
                diagnostics["finishes_without_time"] += 1
        entries.append([user_id, name, place, status_code, seconds])
    return entries


def _is_finisher(place: int | None, status_code: int) -> bool:
    return status_code == DONE_STATUS_CODE and place is not None


def _blank_row(player_index: int) -> JsonObject:
    return {
        "p": player_index,
        "races": 0,
        "finishes": 0,
        "dnf": 0,
        "dq": 0,
        "first": 0,
        "second": 0,
        "third": 0,
        "places": [],
        "times": [],
    }


def _finalize_row(row: JsonObject) -> JsonObject:
    places = row.pop("places")
    times = row.pop("times")
    return row | {
        "podiums": row["first"] + row["second"] + row["third"],
        "best_place": min(places) if places else None,
        "median_place": statistics.median(places) if places else None,
        "average_seconds": sum(times) / len(times) if times else None,
        "min_seconds": min(times) if times else None,
        "max_seconds": max(times) if times else None,
    }


def _leaderboard(
    races: list[JsonObject], players: list[JsonObject], mode_index: int | None
) -> list[JsonObject]:
    totals: dict[int, JsonObject] = {}
    for race in races:
        if mode_index is not None and race["m"] != mode_index:
            continue
        for player_index, place, status_code, seconds, *_rating in race["e"]:
            row = totals.get(player_index)
            if row is None:
                row = totals[player_index] = _blank_row(player_index)
            row["races"] += 1
            if _is_finisher(place, status_code):
                row["finishes"] += 1
                row["places"].append(place)
                if seconds is not None:
                    row["times"].append(seconds)
                if place == 1:
                    row["first"] += 1
                elif place == 2:
                    row["second"] += 1
                elif place == 3:
                    row["third"] += 1
            elif status_code == DQ_STATUS_CODE:
                row["dq"] += 1
            else:
                row["dnf"] += 1

    rows = [_finalize_row(row) for row in totals.values()]
    rows.sort(
        key=lambda row: (
            -row["first"],
            -row["second"],
            -row["third"],
            -row["races"],
            players[row["p"]]["name"].casefold(),
        )
    )
    return rows


def build_participant_payload(
    contexts: list[JsonObject],
    *,
    source_url: str,
    timezone_label: str,
    timezone_name: str,
    generated_at: datetime | None = None,
    current_names: Mapping[str, str] | None = None,
) -> JsonObject:
    """Build the racer leaderboard payload from included race contexts.

    A racer is shown under their name in `current_names` when present, otherwise
    under the name on their most recent race.
    """
    if not contexts:
        raise ParticipantDataError("No races were supplied for the racer leaderboard")

    diagnostics: Counter[str] = Counter()
    ordered = sorted(
        contexts,
        key=lambda context: (context["started_at"], context["race"].get("name") or ""),
    )

    names: dict[str, str] = {}
    avatars: dict[str, str] = {}
    staged: list[JsonObject] = []
    for context in ordered:
        race = context["race"]
        entries = _race_entries(race, diagnostics)
        for user_id, name, *_rest in entries:
            names[user_id] = name
        commenters: set[str] = set()
        for entrant in race["entrants"]:
            identity = _entrant_identity(entrant)
            if identity is None:
                continue
            avatar = _avatar_filename(entrant)
            if avatar:
                avatars[identity[0]] = avatar
            else:
                avatars.pop(identity[0], None)
            if _has_comment(entrant):
                commenters.add(identity[0])
        staged.append(
            {
                "r": race.get("name"),
                "mode": context["mode"],
                "preset": context.get("preset"),
                "d": context["local_started_at"].date().isoformat(),
                "h": context["local_started_at"].hour,
                "n": len(race["entrants"]),
                "entries": entries,
                "commenters": commenters,
            }
        )

    if not names:
        raise ParticipantDataError("No identified racers remained after validation")
    for user_id, name in (current_names or {}).items():
        if user_id in names:
            names[user_id] = name

    ordered_players = sorted(names.items(), key=lambda item: (item[1].casefold(), item[0]))
    players = [{"id": user_id, "name": name} for user_id, name in ordered_players]
    player_index = {user_id: index for index, (user_id, _name) in enumerate(ordered_players)}

    modes = sorted({row["mode"] for row in staged})
    mode_index = {mode: index for index, mode in enumerate(modes)}

    preset_keys = sorted(
        {(mode_index[row["mode"]], row["preset"]) for row in staged if row["preset"]},
        key=lambda key: (key[0], key[1].casefold(), key[1]),
    )
    preset_index = {key: index for index, key in enumerate(preset_keys)}
    presets = [{"m": mode, "n": name} for mode, name in preset_keys]

    records = [
        {
            "r": row["r"],
            "m": mode_index[row["mode"]],
            "mode": row["mode"],
            "d": row["d"],
            "h": row["h"],
            "n": row["n"],
            "p": preset_index.get((mode_index[row["mode"]], row["preset"])),
            "e": [
                (player_index[user_id], place, status_code, seconds)
                for user_id, _name, place, status_code, seconds in row["entries"]
            ],
            # Who left a comment, for the Yap badge; never published.
            "c": {player_index[user_id] for user_id in row["commenters"]},
        }
        for row in staged
    ]
    diagnostics["races_without_preset"] = sum(1 for record in records if record["p"] is None)

    ratings = ladder_rating.rate(records, len(players))
    derived = highlights.compute(
        records, players=players, modes=modes, presets=presets, ratings=ratings
    )

    for index, (player, (user_id, _name)) in enumerate(zip(players, ordered_players)):
        rating = ratings.players[index]
        if user_id in avatars:
            player["a"] = avatars[user_id]
        player["rating"] = rating.rating if rating else None
        player["mu"] = rating.mu if rating else None
        player["sigma"] = rating.sigma if rating else None
        player["finishes"] = rating.finishes if rating else 0
        player.update(derived["players"][index])

    races = []
    for index, record in enumerate(records):
        race_row = {
            "r": record["r"],
            "m": record["m"],
            "d": record["d"],
            "h": record["h"],
            "n": record["n"],
            "p": record["p"],
            "e": [
                [player, place, status_code, None if seconds is None else round(seconds), after]
                for (player, place, status_code, seconds), after in zip(
                    record["e"],
                    (ratings.display(state) for state in ratings.post[index]),
                )
            ],
        }
        if derived["race_tags"][index]:
            race_row["t"] = derived["race_tags"][index]
        races.append(race_row)

    leaderboards = [{"mode": ALL_MODES, "rows": _leaderboard(races, players, None)}]
    for mode in modes:
        leaderboards.append(
            {"mode": mode, "rows": _leaderboard(races, players, mode_index[mode])}
        )

    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    status_counts = {
        key.removeprefix("status:"): value
        for key, value in sorted(diagnostics.items())
        if key.startswith("status:")
    }

    return {
        "schema_version": PARTICIPANT_SCHEMA_VERSION,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "timezone": {"label": timezone_label, "name": timezone_name},
        "modes": modes,
        "presets": [
            preset | stats for preset, stats in zip(presets, derived["preset_stats"])
        ],
        "hardest_preset": derived["hardest_preset"],
        "preset_min_races": highlights.PRESET_MIN_RACES,
        "players": players,
        "races": races,
        "leaderboards": leaderboards,
        "badges": derived["badges"],
        "records": derived["records"],
        "facts": derived["facts"],
        "rating": ratings.params,
        "diagnostics": {
            "race_count": len(races),
            "player_count": len(players),
            "entry_count": sum(len(race["e"]) for race in races),
            "unidentified_entry_count": diagnostics["unidentified_entry_count"],
            "finishes_without_place": diagnostics["finishes_without_place"],
            "finishes_without_time": diagnostics["finishes_without_time"],
            "races_without_preset": diagnostics["races_without_preset"],
            "status_counts": status_counts,
            "highlights": derived["diagnostics"],
        },
    }


def write_compact_json(payload: JsonObject, output_path: Path) -> None:
    """Write the payload without indentation so the browser download stays small."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, separators=(",", ":"), sort_keys=False)
        output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, output_path)
