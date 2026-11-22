#!/usr/bin/env python3
"""Pick the Featured daily racer: one racer a day, from the published race rows.

Every day from the first race on is replayed in order, so each day knows the picks
before it and nobody is featured twice within COOLDOWN_DAYS, with nothing stored.
A day's pick reads only races dated LAG_DAYS or more before it, so it never changes
once the day has begun. The rules are in docs/highlights.md (Featured racer).
"""

from __future__ import annotations

import hashlib
import heapq
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# Supports both `python3 scripts/build_stats.py` and `from scripts import featured`.
try:
    from scripts import highlights
except ImportError:
    import highlights


# Candidates are the racers named by a tag in races of the WINDOW_DAYS days that end
# LAG_DAYS before the pick's day: D-15 to D-2.
WINDOW_DAYS = 14
LAG_DAYS = 2
TOP_RANKS = 3
COOLDOWN_DAYS = 28
SEED = "featured 1"
BIO_MAX_LENGTH = 160
# Must match BADGE_RARITIES in site/ui.js.
LEGENDARY_SHARE = 0.02
RARE_SHARE = 0.1

# A racer's best highlight is their tag whose kind comes first here; it gives them
# that many tickets in the draw. Badge tags are split by rarity at the cutoff.
HIGHLIGHT_ORDER = (
    ("record", 8),
    ("preset_record", 8),
    ("legendary_badge", 8),
    ("upset", 4),
    ("first_win", 4),
    ("photo", 4),
    ("rare_badge", 4),
    ("milestone", 4),
    ("pb", 1),
    ("preset_pb", 1),
    ("badge", 1),
)
HIGHLIGHT_RANKS = {kind: rank for rank, (kind, _tickets) in enumerate(HIGHLIGHT_ORDER)}
# A tag kind added later ranks last, with one ticket.
UNKNOWN_KIND_RANK = len(HIGHLIGHT_ORDER)
UNKNOWN_KIND_TICKETS = 1

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Pick:
    """One day's featured racer and the tag that is their best highlight."""

    day: date
    player: int
    race: int
    tag: int


def bio_text(raw: str | None) -> str | None:
    """Return a bio as the card shows it: whitespace collapsed, capped, or None if blank.

    A bio longer than BIO_MAX_LENGTH is cut at its last word break, as long as that
    keeps at least half of it, and ends with an ellipsis; the result never exceeds
    BIO_MAX_LENGTH characters.
    """
    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) <= BIO_MAX_LENGTH:
        return text
    cut = text[: BIO_MAX_LENGTH - 1]
    space = cut.rfind(" ")
    if space >= BIO_MAX_LENGTH // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _top(ratings: Mapping[int, int]) -> frozenset[int]:
    """Racers with competition rank TOP_RANKS or better: at or above the 3rd-highest rating."""
    if len(ratings) <= TOP_RANKS:
        return frozenset(ratings)
    threshold = heapq.nlargest(TOP_RANKS, ratings.values())[-1]
    return frozenset(player for player, rating in ratings.items() if rating >= threshold)


def _score(day: date, user_id: str, tickets: int) -> int:
    """The racer's best ticket: the highest hash among `tickets` of them."""
    return max(
        int.from_bytes(
            hashlib.sha256(f"{SEED} {day.isoformat()} {user_id} {ticket}".encode()).digest()[:8],
            "big",
        )
        for ticket in range(tickets)
    )


class _Ladder:
    """Rating maps as they stood at a cutoff, with each map's top set kept up to date.

    A map is keyed by (season, mode): season None is all time and mode None is overall.
    Each holds a racer's rating after their last race in scope by the cutoff, read from
    the race rows' entry indexes 4 to 7, as ratingRanksThrough reads them in leaderboard.js.
    """

    def __init__(self, modes: Sequence[str]) -> None:
        self.categories: list[int | None] = [None] + [
            index for index, mode in enumerate(modes) if mode != highlights.UNKNOWN_MODE
        ]
        self.maps: dict[tuple[int | None, int | None], dict[int, int]] = {}
        self.tops: dict[tuple[int | None, int | None], frozenset[int]] = {}
        self.newest_season: int | None = None

    def add(self, race: Mapping[str, Any]) -> None:
        season = race.get("s")
        if season is not None:
            self.newest_season = season if self.newest_season is None else max(self.newest_season, season)
        scopes = [(None, None, 4), (None, race["m"], 5)]
        if season is not None:
            scopes += [(season, None, 6), (season, race["m"], 7)]
        for scope_season, mode, index in scopes:
            key = (scope_season, mode)
            ratings = self.maps.setdefault(key, {})
            for entry in race["e"]:
                if isinstance(entry[index], int):
                    ratings[entry[0]] = entry[index]
            self.tops.pop(key, None)

    def leaders(self) -> set[int]:
        """Everyone in a top TOP_RANKS of any category, all time or in the newest season."""
        scopes = [None] if self.newest_season is None else [None, self.newest_season]
        found: set[int] = set()
        for season in scopes:
            for mode in self.categories:
                key = (season, mode)
                if key not in self.maps:
                    continue
                if key not in self.tops:
                    self.tops[key] = _top(self.maps[key])
                found |= self.tops[key]
        return found


def _highlight_kind(tag: Sequence[Any], holders: Mapping[str, set[int]], racers: int) -> str:
    """The tag's kind in HIGHLIGHT_ORDER, a badge split by its holder share at the cutoff."""
    if tag[0] != "badge":
        return tag[0]
    share = len(holders.get(tag[2], ())) / racers
    if share <= LEGENDARY_SHARE:
        return "legendary_badge"
    if share <= RARE_SHARE:
        return "rare_badge"
    return "badge"


def _rank(kind: str) -> int:
    return HIGHLIGHT_RANKS.get(kind, UNKNOWN_KIND_RANK)


def replay(
    races: Sequence[Mapping[str, Any]],
    player_ids: Sequence[str],
    modes: Sequence[str],
) -> list[Pick]:
    """Return every day's pick, from the first day with a candidate through the last.

    `races` are the published race rows in start order, so their Eastern dates `d`
    never fall. A day with nobody left after the exclusions has no pick.
    """
    if not races:
        return []
    first = date.fromisoformat(races[0]["d"])
    newest = date.fromisoformat(races[-1]["d"])
    ladder = _Ladder(modes)
    # Holders per badge, from the badge tags. They are every unlock but First Blood's, which
    # is the first_win tag; no badge tag names First Blood, so its count is never needed.
    holders: dict[str, set[int]] = {}
    seen: set[int] = set()
    window: deque[int] = deque()
    # Per race and tag, the rank of a tag whose kind never changes, or None for a badge
    # tag, whose kind moves with the holder counts and so is ranked afresh every day.
    static_ranks: list[list[int | None]] = []
    last_featured: dict[str, date] = {}
    picks: list[Pick] = []
    added = 0

    day = first + timedelta(days=LAG_DAYS)
    last_day = newest + timedelta(days=WINDOW_DAYS + LAG_DAYS - 1)
    while day <= last_day:
        cutoff = (day - timedelta(days=LAG_DAYS)).isoformat()
        opens = (day - timedelta(days=WINDOW_DAYS + LAG_DAYS - 1)).isoformat()
        while added < len(races) and races[added]["d"] <= cutoff:
            race = races[added]
            ladder.add(race)
            seen.update(entry[0] for entry in race["e"])
            for tag in race.get("t", ()):
                if tag[0] == "badge":
                    holders.setdefault(tag[2], set()).add(tag[1])
            static_ranks.append(
                [None if tag[0] == "badge" else _rank(tag[0]) for tag in race.get("t", ())]
            )
            window.append(added)
            added += 1
        while window and races[window[0]]["d"] < opens:
            window.popleft()

        # Each racer's best highlight in the window; between two of a kind, the later one.
        best: dict[int, tuple[int, int, int]] = {}
        for race_index in window:
            for tag_index, tag in enumerate(races[race_index].get("t", ())):
                rank = static_ranks[race_index][tag_index]
                if rank is None:
                    rank = _rank(_highlight_kind(tag, holders, len(seen)))
                current = best.get(tag[1])
                if current is None or rank <= current[0]:
                    best[tag[1]] = (rank, race_index, tag_index)

        excluded = ladder.leaders()
        draw = []
        for player, (rank, race_index, tag_index) in best.items():
            user_id = player_ids[player]
            if player in excluded:
                continue
            featured_on = last_featured.get(user_id)
            if featured_on is not None and (day - featured_on).days < COOLDOWN_DAYS:
                continue
            tickets = HIGHLIGHT_ORDER[rank][1] if rank < UNKNOWN_KIND_RANK else UNKNOWN_KIND_TICKETS
            draw.append((-_score(day, user_id, tickets), user_id, player, race_index, tag_index))
        if draw:
            _score_key, user_id, player, race_index, tag_index = min(draw)
            last_featured[user_id] = day
            picks.append(Pick(day, player, race_index, tag_index))
        day += timedelta(days=1)
    return picks


def schedule(
    races: Sequence[Mapping[str, Any]],
    player_ids: Sequence[str],
    modes: Sequence[str],
) -> list[Pick]:
    """The picks a viewer can still see: from the newest race's date on.

    A viewer's today is never before the newest race's date, and no day after the
    last pick of the replay has a candidate.
    """
    if not races:
        return []
    newest = date.fromisoformat(races[-1]["d"])
    return [pick for pick in replay(races, player_ids, modes) if pick.day >= newest]


def published(
    races: Sequence[Mapping[str, Any]],
    player_ids: Sequence[str],
    modes: Sequence[str],
    bios: Mapping[str, Sequence[Any]],
) -> list[list[Any]]:
    """The `featured` field: [date, player, race, tag, bio] for each coming pick.

    `bios` maps user IDs to their racer-bios.json entry; only the current bio, entry 0,
    of a featured racer is published, through bio_text.
    """
    return [
        [
            pick.day.isoformat(),
            pick.player,
            pick.race,
            pick.tag,
            bio_text(bios.get(player_ids[pick.player], [None])[0]),
        ]
        for pick in schedule(races, player_ids, modes)
    ]
