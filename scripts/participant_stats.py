#!/usr/bin/env python3
"""Build the per-racer leaderboard dataset from fetched ladder races."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

# Supports both `python3 scripts/build_stats.py` and `from scripts import participant_stats`.
try:
    from scripts import (
        avatar_mirror,
        highlights,
        ladder_rating,
        pokemon_avatars,
        schedule_archive,
        season_ratings,
    )
except ImportError:
    import avatar_mirror
    import highlights
    import ladder_rating
    import pokemon_avatars
    import schedule_archive
    import season_ratings


PARTICIPANT_SCHEMA_VERSION = 13
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
        "median_seconds": statistics.median(times) if times else None,
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


def _rating_fields(rating: ladder_rating.PlayerRating | None) -> JsonObject:
    """Return the published rating fields; an unrated racer has no rating and 0 finishes."""
    if rating is None:
        return {"rating": None, "mu": None, "sigma": None, "finishes": 0}
    return {
        "rating": rating.rating,
        "mu": rating.mu,
        "sigma": rating.sigma,
        "finishes": rating.finishes,
    }


def build_participant_payload(
    contexts: list[JsonObject],
    *,
    source_url: str,
    timezone_label: str,
    timezone_name: str,
    generated_at: datetime | None = None,
    current_names: Mapping[str, str] | None = None,
    envelopes: list[JsonObject] | None = None,
    avatar_observations: Mapping[str, list[str | None]] | None = None,
    bios: Mapping[str, list] | None = None,
    pokemon: pokemon_avatars.Catalogue | None = None,
    schedule: list[JsonObject] | None = None,
    ledgers: Mapping[int, season_ratings.Ledger] | None = None,
) -> JsonObject:
    """Build the racer leaderboard payload from included race contexts.

    A racer is shown under their name in `current_names` when present, otherwise
    under the name on their most recent race. Their avatar is the file name that
    `avatar_mirror.mirror_name` gives their most recently observed avatar URL, taken
    from every archived race file in `envelopes` and from `avatar_observations`.
    Given a Pokémon `pokemon` catalogue, `pokemon_avatars.assign` replaces it with the
    favourite Pokémon their entry in `bios` names, or a random one if the favourite is
    `random`, and gives a racer with neither a random Pokémon; racers are ordered by
    their first included race, then user ID.

    Given the ladder `schedule`, every race also gets its season and its entrants'
    season ratings (see `season_ratings`); `ledgers` hold the seasons already locked.
    Without a schedule no race has a season.
    """
    if not contexts:
        raise ParticipantDataError("No races were supplied for the racer leaderboard")

    diagnostics: Counter[str] = Counter()
    ordered = sorted(
        contexts,
        key=lambda context: (context["started_at"], context["race"].get("name") or ""),
    )

    names: dict[str, str] = {}
    first_race: dict[str, int] = {}
    staged: list[JsonObject] = []
    for race_number, context in enumerate(ordered):
        race = context["race"]
        entries = _race_entries(race, diagnostics)
        for user_id, name, *_rest in entries:
            names[user_id] = name
            first_race.setdefault(user_id, race_number)
        commenters: set[str] = set()
        for entrant in race["entrants"]:
            identity = _entrant_identity(entrant)
            if identity is None:
                continue
            if _has_comment(entrant):
                commenters.add(identity[0])
        staged.append(
            {
                "r": race.get("name"),
                "started_at": context["started_at"],
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

    racetime_urls = avatar_mirror.current_avatar_urls(envelopes or [], avatar_observations or {})
    avatars = {user_id: avatar_mirror.mirror_name(url) for user_id, url in racetime_urls.items()}
    if pokemon is not None:
        order = sorted(names, key=lambda user_id: (first_race[user_id], user_id))
        avatars.update(pokemon_avatars.assign(order, set(avatars), bios or {}, pokemon))

    ordered_players = sorted(names.items(), key=lambda item: (item[1].casefold(), item[0]))
    players = [{"id": user_id, "name": name} for user_id, name in ordered_players]
    player_index = {user_id: index for index, (user_id, _name) in enumerate(ordered_players)}

    # Racers whose avatar changed, newest first. A favourite change always counts. An
    # upload change, including its removal, counts unless a favourite hides the upload;
    # a favourite of `random` hides it too.
    # Random Pokémon picks never count: they can reshuffle when other racers change.
    zone = ZoneInfo(timezone_name)
    upload_changes = avatar_mirror.avatar_change_times(envelopes or [], avatar_observations or {})
    favourite_changes: dict[str, datetime] = {}
    favourite_holders: set[str] = set()
    if pokemon is not None:
        favourite_changes = pokemon_avatars.favourite_change_times(bios or {}, pokemon)
        favourite_holders = {
            user_id
            for user_id, entry in (bios or {}).items()
            if pokemon_avatars.favourite_pokemon(entry[0], pokemon) is not None
        }
    changed = []
    for user_id, index in player_index.items():
        if user_id not in avatars:  # nothing published, e.g. no catalogue and no upload
            continue
        times = [favourite_changes.get(user_id)]
        if user_id not in favourite_holders:
            times.append(upload_changes.get(user_id))
        times = [changed_at for changed_at in times if changed_at is not None]
        if times:
            changed.append((max(times), index))
    changed.sort(key=lambda item: (-item[0].timestamp(), item[1]))
    avatar_changes = [
        [index, changed_at.astimezone(zone).date().isoformat()] for changed_at, index in changed
    ]

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
            # Each entrant's name as archived with this race, so a later rename never
            # changes the alphabetical badges; never published.
            "names": {player_index[user_id]: name for user_id, name, *_ in row["entries"]},
        }
        for row in staged
    ]
    diagnostics["races_without_preset"] = sum(1 for record in records if record["p"] is None)

    ratings = ladder_rating.rate(records, len(players))
    mode_ratings = ladder_rating.rate_by_mode(records, len(players))

    # Seasons: each race's season, and its entrants' points in the season and in the
    # season's mode, as computed now or as a locked season's ledger holds them.
    schedule = schedule or []
    ledgers = ledgers or {}
    starts = schedule_archive.season_starts(schedule)
    season_races = [
        season_ratings.Race(
            row["r"],
            row["started_at"],
            row["mode"],
            tuple(user_id for user_id, *_rest in row["entries"]),
        )
        for row in staged
    ]
    assignment = season_ratings.assign(season_races, starts, ledgers)
    season_points = season_ratings.overlay(
        season_races,
        assignment.seasons,
        ladder_rating.rate_by_season(records, len(players), assignment.seasons).points(),
        ledgers,
    )
    derived = highlights.compute(
        records, players=players, modes=modes, presets=presets, ratings=ratings
    )

    for index, (player, (user_id, _name)) in enumerate(zip(players, ordered_players)):
        rating = ratings.players[index]
        if user_id in avatars:
            player["a"] = avatars[user_id]
        player.update(_rating_fields(rating))
        player["mode_ratings"] = {
            str(mode): _rating_fields(by_player[index])
            for mode, by_player in mode_ratings.players.items()
            if by_player[index] is not None
        }
        player.update(derived["players"][index])

    races = []
    for index, record in enumerate(records):
        points = season_points[index] or [(None, None)] * len(record["e"])
        race_row = {
            "r": record["r"],
            "m": record["m"],
            "d": record["d"],
            "h": record["h"],
            "n": record["n"],
            "p": record["p"],
            "e": [
                [
                    player,
                    place,
                    status_code,
                    None if seconds is None else round(seconds),
                    ratings.display(after),
                    mode_ratings.display(mode_after),
                    season_after,
                    season_mode_after,
                ]
                for (player, place, status_code, seconds), after, mode_after, (
                    season_after,
                    season_mode_after,
                ) in zip(
                    record["e"], ratings.post[index], mode_ratings.post[index], points
                )
            ],
        }
        if assignment.seasons[index] is not None:
            race_row["s"] = assignment.seasons[index]
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
        "blowout_min_field": highlights.BLOWOUT_MIN_FIELD,
        "players": players,
        "avatar_changes": avatar_changes,
        "races": races,
        "leaderboards": leaderboards,
        "badges": derived["badges"],
        "records": derived["records"],
        "facts": derived["facts"],
        "rating": ratings.params,
        "seasons": season_ratings.published_seasons(
            assignment.seasons, [record["d"] for record in records], starts, ledgers
        ),
        "diagnostics": {
            "race_count": len(races),
            "player_count": len(players),
            "entry_count": sum(len(race["e"]) for race in races),
            "unidentified_entry_count": diagnostics["unidentified_entry_count"],
            "finishes_without_place": diagnostics["finishes_without_place"],
            "finishes_without_time": diagnostics["finishes_without_time"],
            "races_without_preset": diagnostics["races_without_preset"],
            "seasons": {
                "stray_slots": schedule_archive.stray_slots(schedule, starts),
                "races_without_season": assignment.seasons.count(None),
                "left_out_of_locked_seasons": assignment.left_out,
                "mismatched_locked_races": assignment.mismatched,
            },
            "status_counts": status_counts,
            "highlights": derived["diagnostics"],
        },
    }
