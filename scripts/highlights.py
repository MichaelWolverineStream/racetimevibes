#!/usr/bin/env python3
"""Derive race tags, streaks, badges, records and facts from normalized races."""

from __future__ import annotations

import bisect
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from typing import Any, NamedTuple

# Supports both `python3 scripts/build_stats.py` and `from scripts import highlights`.
try:
    from scripts.ladder_rating import PROVISIONAL_FINISHES
except ImportError:
    from ladder_rating import PROVISIONAL_FINISHES


UPSET_MAX_CHANCE = 0.15
UPSET_MIN_ENTRANTS = 3
PHOTO_MAX_MARGIN = 5.0
RETIME_MAX_GAP = 1.0
MILESTONES = (25, 50, 100, 250, 500, 1000)
HAT_TRICK_WINS = 3
HUMMERS_WINS = 6
LUCKSACK_WINS = 9
NO_DODGE_FINISHES = 10
MORE_NO_DODGE_FINISHES = 40
NICE_FINISHES = 69
EXCUSED_DNF_FIELD = 2
CENTURIAN_FINISHES = 105
BLAZE_IT_FINISHES = 420
OVER_174_NON_WINS = 175
OMNIVORE_MODES = 7
APEX_PREDATOR_MODES = 7
GLITCHED_MODE = "glitched"
GLITCHED_TIERS = (1, 10, 30, 100)
INVERTED_MODE = "inverted"
INVERTED_TIERS = (1, 30)
SPOILER_MODE = "spoiler"
CASUAL_BOOTS_PRESET = "Casual Boots"
JET_SEED_MAX_SECONDS = 4200
JET_LAG_MIN_SECONDS = 6000
ENTRANCE_MODE = "entrance"
ENTRANCED_DAYS = 5
ENTRANCED_WEEKS = 20
MYSTERY_MODE = "mystery"
MYSTERY_TIERS = (10, 30)
BOTH_ENDS_FAST_SECONDS = 1800
BOTH_ENDS_SLOW_SECONDS = 7800
MIDDLE_MIN_FIELD = 5
RANDO_SICKO_FINISHES = 5
STEP_LETTER_MIN_FIELD = 5
TOUCHED_GRASS_DAYS = 30
TOUCHED_GRASS_FROM = date(2026, 8, 1)
YAP_COMMENTS = 11
NIGHT_HOURS = range(0, 5)
BIG_FIELD_SIZE = 16
BLOWOUT_MIN_FIELD = 5
GOD_ROUTE_MIN_MARGIN = 600
GOD_ROUTE_MIN_FIELD = 7
UPSETTI_MIN_MARGIN = 600
UPSETTI_MIN_FIELD = 7
BIG_BRAIN_MIN_MARGIN = 900
BIG_BRAIN_MIN_FIELD = 9
RANKING_WINDOW_DAYS = 90
PRESET_MIN_RACES = 8
BEST_PRESET_MIN_FINISHES = 3
BOARD_BEST = 10
BOARD_TOP = 10
FACT_RECORD_MODES = 3
UNKNOWN_MODE = "unknown"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CLAIM_MIN_VALUE = 2
CLAIM_MAX_SHARE = 0.1

DONE_STATUS_CODE = 0
DNF_STATUS_CODE = 1
STREAK_KINDS = ("win", "podium", "finish")
# The order breaks ties between stats; a badge claim comes after every stat.
CLAIM_KINDS = (
    "wins",
    "win_streak",
    "podium_streak",
    "finish_streak",
    "races",
    "daily",
    "pbs",
    "opponents",
    "night",
    "comeback",
)
TAG_KINDS = ("record", "preset_record", "first_win", "upset", "photo", "pb", "milestone", "badge")

BADGE_IMAGE_DIR = "assets/badges"

JsonObject = dict[str, Any]


def _is_finisher(place: int | None, status_code: int) -> bool:
    return status_code == DONE_STATUS_CODE and place is not None


def is_palindrome_time(seconds: float) -> bool:
    """True when H:MM:SS, hour unpadded and colons removed, reads the same both ways."""
    whole = round(seconds)
    digits = f"{whole // 3600}{whole % 3600 // 60:02d}{whole % 60:02d}"
    return digits == digits[::-1]


def mode_label(mode: str) -> str:
    """Display text for a raw mode id. Must match `formatModeName` in site/app.js."""
    words = mode.replace("_", " ")
    return words[:1].upper() + words[1:]


def format_duration(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def badge_image_paths(badge_id: str) -> tuple[str, str]:
    """Site-relative paths of a badge's animated image and its still."""
    return f"{BADGE_IMAGE_DIR}/{badge_id}.webp", f"{BADGE_IMAGE_DIR}/{badge_id}-still.webp"


class _Run:
    """One streak kind for one racer, over the racer's own race sequence."""

    __slots__ = ("length", "start", "end", "best")

    def __init__(self) -> None:
        self.length = 0
        self.start = 0
        self.end = 0
        self.best: tuple[int, int, int] | None = None

    def step(self, race_index: int, hit: bool) -> None:
        if not hit:
            self.length = 0
            return
        if self.length == 0:
            self.start = race_index
        self.length += 1
        self.end = race_index
        if self.best is None or self.length > self.best[0]:
            self.best = (self.length, self.start, race_index)

    def best_value(self) -> list[int] | None:
        return list(self.best) if self.best else None

    def current_value(self) -> list[int] | None:
        return [self.length, self.start, self.end] if self.length else None


class _CalendarRun:
    """Consecutive calendar units (days or weeks) in which a racer showed up."""

    __slots__ = ("last", "length", "best")

    def __init__(self) -> None:
        self.last: int | None = None
        self.length = 0
        self.best = 0

    def step(self, unit: int) -> None:
        if unit == self.last:
            return
        if self.last is not None and unit == self.last + 1:
            self.length += 1
        else:
            self.length = 1
        self.last = unit
        self.best = max(self.best, self.length)


class _Player:
    __slots__ = (
        "races",
        "finishes",
        "wins",
        "glitched",
        "mystery",
        "inverted",
        "day",
        "day_finishes",
        "best_day_finishes",
        "comment_streak",
        "best_comment_streak",
        "mystery_fast",
        "mystery_slow",
        "entrance_days",
        "entrance_weeks",
        "modes_done",
        "modes_won",
        "best_by_mode",
        "pbs",
        "badges",
        "runs",
        "dates",
        "preset_times",
        "mean",
        "last_ordinal",
        "night_finishes",
    )

    def __init__(self) -> None:
        self.races = 0
        self.finishes = 0
        self.wins = 0
        self.glitched = 0
        self.mystery = 0
        self.inverted = 0
        self.day = ""
        self.day_finishes = 0
        self.best_day_finishes = 0
        self.comment_streak = 0
        self.best_comment_streak = 0
        self.mystery_fast = False
        self.mystery_slow = False
        self.entrance_days = _CalendarRun()
        self.entrance_weeks = _CalendarRun()
        self.modes_done: set[int] = set()
        self.modes_won: set[int] = set()
        self.best_by_mode: dict[int, int] = {}
        self.pbs: dict[int, list[list[int]]] = {}
        self.badges: dict[str, int] = {}
        self.runs = {kind: _Run() for kind in STREAK_KINDS}
        self.dates: list[str] = []
        self.preset_times: dict[int, list[float]] = defaultdict(list)
        self.mean: float | None = None
        self.last_ordinal = 0
        self.night_finishes = 0


class _Moment(NamedTuple):
    """One racer's entry in one race, with the race facts a one-race badge needs."""

    player: int
    state: _Player
    place: int | None
    finisher: bool
    won: bool
    seconds: float | None
    palindrome: bool
    mode: str
    preset: str | None
    n: int
    ordinal: int
    last_ordinal: int | None
    snipers: set[int]
    sniped: set[int]
    god_route: set[int]
    upsetti: set[int]
    big_brain: set[int]
    middle: set[int]
    step_letter: set[int]
    rettel_pets: set[int]
    retimed: set[int]


class Badge(NamedTuple):
    """One badge and the single rule that unlocks it.

    A counter badge has a `progress` rule, the best value so far; it unlocks when that
    reaches the target and drives the progress border. A one-race badge has an `earned`
    rule on that race alone.
    """

    id: str
    label: str
    description: str
    target: int = 1
    progress: Callable[[_Player], int] | None = None
    earned: Callable[[_Moment], bool] | None = None


def _best_run(kind: str) -> Callable[[_Player], int]:
    return lambda state: (state.runs[kind].best or (0,))[0]


def _jet_seed(moment: _Moment) -> bool:
    return (
        moment.finisher
        and moment.preset == CASUAL_BOOTS_PRESET
        and moment.seconds is not None
        and moment.seconds < JET_SEED_MAX_SECONDS
    )


def _jet_lag(moment: _Moment) -> bool:
    return (
        moment.won
        and moment.preset == CASUAL_BOOTS_PRESET
        and moment.seconds is not None
        and moment.seconds >= JET_LAG_MIN_SECONDS
    )


def _touched_grass(moment: _Moment) -> bool:
    """A return after a long break; days before TOUCHED_GRASS_FROM don't count as away."""
    if moment.last_ordinal is None:
        return False
    away_since = max(moment.last_ordinal, TOUCHED_GRASS_FROM.toordinal())
    return moment.ordinal - away_since >= TOUCHED_GRASS_DAYS


# Table order: the order of locked badges on a racer page. A new badge is one row here, plus a state field if its rule needs a new count.
BADGES = (
    Badge("first_blood", "First Blood", "Win a race.", earned=lambda m: m.won),
    Badge(
        "hat_trick",
        "Hat Trick",
        f"Win {HAT_TRICK_WINS} races in a row.",
        HAT_TRICK_WINS,
        progress=_best_run("win"),
    ),
    Badge(
        "hummers",
        "Hummers",
        f"Win {HUMMERS_WINS} races in a row.",
        HUMMERS_WINS,
        progress=_best_run("win"),
    ),
    Badge(
        "ok_dude_lucksack",
        "OkDudeLucksack",
        f"Win {LUCKSACK_WINS} races in a row.",
        LUCKSACK_WINS,
        progress=_best_run("win"),
    ),
    Badge(
        "no_dodge",
        "No Dodge",
        f"Finish {NO_DODGE_FINISHES} races in a row.",
        NO_DODGE_FINISHES,
        progress=_best_run("finish"),
    ),
    Badge(
        "more_no_dodge",
        "More No Dodge",
        f"Finish {MORE_NO_DODGE_FINISHES} races in a row.",
        MORE_NO_DODGE_FINISHES,
        progress=_best_run("finish"),
    ),
    Badge(
        "nice",
        "Nice",
        f"Finish {NICE_FINISHES} races in a row.",
        NICE_FINISHES,
        progress=_best_run("finish"),
    ),
    Badge(
        "centurian_v",
        "CenturianV",
        f"Finish {CENTURIAN_FINISHES} races.",
        CENTURIAN_FINISHES,
        progress=lambda s: s.finishes,
    ),
    Badge(
        "blaze_it",
        "BlazeIt",
        f"Finish {BLAZE_IT_FINISHES} races.",
        BLAZE_IT_FINISHES,
        progress=lambda s: s.finishes,
    ),
    Badge(
        "over_174",
        ">174",
        f"Enter {OVER_174_NON_WINS} races you don't win.",
        OVER_174_NON_WINS,
        progress=lambda s: s.races - s.wins,
    ),
    Badge(
        "omnivore",
        "Omnivore",
        f"Finish a race in {OMNIVORE_MODES} different modes.",
        OMNIVORE_MODES,
        progress=lambda s: len(s.modes_done),
    ),
    Badge(
        "apex_predator",
        "Apex Predator",
        f"Win a race in {APEX_PREDATOR_MODES} different modes.",
        APEX_PREDATOR_MODES,
        progress=lambda s: len(s.modes_won),
    ),
    Badge(
        "sniper",
        "Sniper",
        f"Win by less than {PHOTO_MAX_MARGIN:g} seconds.",
        earned=lambda m: m.player in m.snipers,
    ),
    Badge(
        "sniped",
        "Sniped!",
        f"Finish 2nd, less than {PHOTO_MAX_MARGIN:g} seconds behind the winner.",
        earned=lambda m: m.player in m.sniped,
    ),
    Badge(
        "retime",
        "RETIME",
        f"Finish less than {RETIME_MAX_GAP:g} second from another racer.",
        earned=lambda m: m.player in m.retimed,
    ),
    Badge(
        "palindrome",
        "Palindrome",
        "Finish with a time that reads the same both ways.",
        earned=lambda m: m.palindrome,
    ),
    Badge(
        "god_route",
        "God Route",
        f"Win by more than {GOD_ROUTE_MIN_MARGIN // 60} minutes in a field of "
        f"{GOD_ROUTE_MIN_FIELD} or more while not its top-rated racer.",
        earned=lambda m: m.player in m.god_route,
    ),
    Badge(
        "upsetti",
        "Upsetti",
        f"Finish 2nd, {UPSETTI_MIN_MARGIN // 60} minutes or more behind the winner, in a field of "
        f"{UPSETTI_MIN_FIELD} or more as its top-rated racer.",
        earned=lambda m: m.player in m.upsetti,
    ),
    Badge(
        "big_brain",
        "BigBrain",
        f"Win by {BIG_BRAIN_MIN_MARGIN // 60} minutes or more in a field of "
        f"{BIG_BRAIN_MIN_FIELD} or more.",
        earned=lambda m: m.player in m.big_brain,
    ),
    Badge(
        "big_game_hunter",
        "Big Game Hunter",
        f"Win a race with {BIG_FIELD_SIZE} or more entrants.",
        earned=lambda m: m.won and m.n >= BIG_FIELD_SIZE,
    ),
    Badge(
        "glitched",
        "Glitched",
        f"Finish a {GLITCHED_MODE} race.",
        GLITCHED_TIERS[0],
        progress=lambda s: s.glitched,
    ),
    Badge(
        "glitched_10",
        "Glitched!",
        f"Finish {GLITCHED_TIERS[1]} {GLITCHED_MODE} races.",
        GLITCHED_TIERS[1],
        progress=lambda s: s.glitched,
    ),
    Badge(
        "glitched_30",
        "G1!tch3d!!!",
        f"Finish {GLITCHED_TIERS[2]} {GLITCHED_MODE} races.",
        GLITCHED_TIERS[2],
        progress=lambda s: s.glitched,
    ),
    Badge(
        "glitched_100",
        "G%!#ch@d!#?",
        f"Finish {GLITCHED_TIERS[3]} {GLITCHED_MODE} races.",
        GLITCHED_TIERS[3],
        progress=lambda s: s.glitched,
    ),
    Badge(
        "the_upside_down",
        "The Upside Down",
        f"Finish an {INVERTED_MODE} race.",
        INVERTED_TIERS[0],
        progress=lambda s: s.inverted,
    ),
    Badge(
        "mirror_universe",
        "Mirror Universe",
        f"Finish {INVERTED_TIERS[1]} {INVERTED_MODE} races.",
        INVERTED_TIERS[1],
        progress=lambda s: s.inverted,
    ),
    Badge(
        "jet_seed",
        "Jet Seed",
        f"Finish a {CASUAL_BOOTS_PRESET} race in under {JET_SEED_MAX_SECONDS // 60} minutes.",
        earned=_jet_seed,
    ),
    Badge(
        "jet_lag",
        "Jet Lag",
        f"Win a {CASUAL_BOOTS_PRESET} race in {JET_LAG_MIN_SECONDS // 60} minutes or more.",
        earned=_jet_lag,
    ),
    Badge(
        "driving_lesson",
        "Driving Lesson",
        f"Finish a {SPOILER_MODE} race.",
        earned=lambda m: m.finisher and m.mode == SPOILER_MODE,
    ),
    Badge(
        "cruisin",
        "Cruisin'",
        f"Win a {SPOILER_MODE} race.",
        earned=lambda m: m.won and m.mode == SPOILER_MODE,
    ),
    Badge(
        "entrance",
        "Entrance",
        f"Finish an {ENTRANCE_MODE} race.",
        earned=lambda m: m.finisher and m.mode == ENTRANCE_MODE,
    ),
    Badge(
        "entranced",
        "Entranced",
        f"Enter an {ENTRANCE_MODE} race on {ENTRANCED_DAYS} days in a row.",
        ENTRANCED_DAYS,
        progress=lambda s: s.entrance_days.best,
    ),
    Badge(
        "entranced_weeks",
        "Entranced!",
        f"Enter an {ENTRANCE_MODE} race in {ENTRANCED_WEEKS} weeks in a row.",
        ENTRANCED_WEEKS,
        progress=lambda s: s.entrance_weeks.best,
    ),
    Badge(
        "what",
        "What?",
        f"Enter a {MYSTERY_MODE} race.",
        earned=lambda m: m.mode == MYSTERY_MODE,
    ),
    Badge(
        "i_like_it_here",
        "I Like It Here",
        f"Finish {MYSTERY_TIERS[0]} {MYSTERY_MODE} races.",
        MYSTERY_TIERS[0],
        progress=lambda s: s.mystery,
    ),
    Badge(
        "bustin",
        "BUSTIN'",
        f"Finish {MYSTERY_TIERS[1]} {MYSTERY_MODE} races.",
        MYSTERY_TIERS[1],
        progress=lambda s: s.mystery,
    ),
    Badge(
        "both_ends",
        "Both Ends",
        f"Finish a {MYSTERY_MODE} race in under {BOTH_ENDS_FAST_SECONDS // 60} minutes, "
        f"and another in over {BOTH_ENDS_SLOW_SECONDS // 60} minutes.",
        earned=lambda m: m.state.mystery_fast and m.state.mystery_slow,
    ),
    Badge(
        "grocery_stick",
        "Grocery Stick",
        f"Finish in the exact middle of an odd field of {MIDDLE_MIN_FIELD} or more entrants.",
        earned=lambda m: m.player in m.middle,
    ),
    Badge(
        "rando_sicko",
        "Rando Sicko",
        f"Finish {RANDO_SICKO_FINISHES} races in one day.",
        RANDO_SICKO_FINISHES,
        progress=lambda s: s.best_day_finishes,
    ),
    Badge(
        "touched_grass",
        "Touched Grass",
        f"Enter a race after {TOUCHED_GRASS_DAYS} or more days away, "
        f"counting from {TOUCHED_GRASS_FROM.isoformat()}.",
        earned=_touched_grass,
    ),
    Badge(
        "yap",
        "Yap",
        f"Comment on {YAP_COMMENTS} races in a row.",
        YAP_COMMENTS,
        progress=lambda s: s.best_comment_streak,
    ),
    Badge(
        "step_letter",
        "Step Letter",
        f"Be in a race of {STEP_LETTER_MIN_FIELD} or more where everyone finished "
        "in alphabetical order.",
        earned=lambda m: m.player in m.step_letter,
    ),
    Badge(
        "rettel_pets",
        "Rettel Pets",
        f"Be in a race of {STEP_LETTER_MIN_FIELD} or more where everyone finished "
        "in reverse alphabetical order.",
        earned=lambda m: m.player in m.rettel_pets,
    ),
)


def _daily_streaks(dates: list[str]) -> tuple[list[Any], list[Any]] | None:
    """Return (best, current) as [length, start_d, end_d] from sorted unique dates."""
    if not dates:
        return None
    best: list[Any] | None = None
    start = previous = dates[0]
    length = 0
    previous_ordinal = None
    for current in dates:
        ordinal = date.fromisoformat(current).toordinal()
        if previous_ordinal is not None and ordinal == previous_ordinal + 1:
            length += 1
        else:
            start = current
            length = 1
        previous_ordinal = ordinal
        previous = current
        if best is None or length > best[0]:
            best = [length, start, current]
    return best, [length, start, previous]


def _ranking_top(
    states: dict[int, _Player], race_ordinal: int
) -> list[int]:
    """Non-provisional racers active in the window, best pre-race mean first."""
    ranked = [
        (-state.mean, index)
        for index, state in states.items()
        if state.mean is not None
        and state.finishes >= PROVISIONAL_FINISHES
        and race_ordinal - state.last_ordinal <= RANKING_WINDOW_DAYS
    ]
    ranked.sort()
    return [index for _mean, index in ranked]


def _top_rated(states: dict[int, _Player], race_ordinal: int, entries: list[Any]) -> int | None:
    """The race's top-rated entrant by `_ranking_top`, if any entrant is ranked."""
    entrant_ids = {e[0] for e in entries}
    return next(
        (index for index in _ranking_top(states, race_ordinal) if index in entrant_ids),
        None,
    )


def _middle_finisher(entries: list[Any], n: int) -> set[int]:
    """The finisher in the exact middle of an odd field, if one is identified."""
    if n < MIDDLE_MIN_FIELD or n % 2 == 0:
        return set()
    return {e[0] for e in entries if _is_finisher(e[1], e[2]) and e[1] == (n + 1) // 2}


def _retimed(timed: list[Any]) -> set[int]:
    """Finishers less than RETIME_MAX_GAP seconds from another finisher's time."""
    ordered = sorted(timed, key=lambda e: e[3])
    tied: set[int] = set()
    for first, second in zip(ordered, ordered[1:]):
        if second[3] - first[3] < RETIME_MAX_GAP:
            tied |= {first[0], second[0]}
    return tied


def _alphabetical_field(
    entries: list[Any], n: int, names: dict[int, str], *, reverse: bool = False
) -> set[int]:
    """Every entrant, when all of a big enough field finished in alphabetical order (or reverse).

    `names` holds each entrant's name as archived with the race, so a later rename
    never changes the outcome.
    """
    if n < STEP_LETTER_MIN_FIELD or len(entries) != n:
        return set()
    if not all(_is_finisher(e[1], e[2]) for e in entries):
        return set()
    ordered = [names[e[0]].casefold() for e in sorted(entries, key=lambda e: e[1])]
    if ordered != sorted(ordered, reverse=reverse):
        return set()
    return {e[0] for e in entries}


def _top(rows: list[list[Any]], key, limit: int) -> list[list[Any]]:
    return sorted(rows, key=key)[:limit]


def compute(
    records: list[JsonObject],
    *,
    players: list[JsonObject],
    modes: list[str],
    presets: list[JsonObject],
    ratings: Any,
) -> JsonObject:
    """Run one chronological pass over the records and derive every highlight."""
    states: dict[int, _Player] = {}
    race_tags: list[list[list[Any]]] = []
    mode_best: dict[int, int] = {}
    mode_steps: dict[int, list[list[Any]]] = {m: [] for m in range(len(modes))}
    photos: list[list[Any]] = []
    blowouts: list[list[Any]] = []
    big_fields: list[list[Any]] = []
    tag_counts: Counter[str] = Counter()

    # Omnivore and Apex Predator ask for fewer modes when the data has fewer than their target.
    targets = {badge.id: badge.target for badge in BADGES}
    targets["omnivore"] = min(OMNIVORE_MODES, len(modes))
    targets["apex_predator"] = min(APEX_PREDATOR_MODES, len(modes))

    preset_rows = [
        {"races": 0, "entries": 0, "bad": 0, "times": [], "wins": [], "fields": 0, "best": None}
        for _ in presets
    ]

    day_races: Counter[str] = Counter()
    pair_counts: Counter[tuple[int, int]] = Counter()
    total_seconds = 0.0
    palindromes = 0
    palindrome_example: float | None = None
    night_finishes = 0
    finishes_total = 0
    upsets = 0
    alphabetical_races = 0

    for ri, rec in enumerate(records):
        entries = rec["e"]
        n = rec["n"]
        mode = rec["m"]
        mode_name = modes[mode]
        preset_name = presets[rec["p"]]["n"] if rec["p"] is not None else None
        race_ordinal = date.fromisoformat(rec["d"]).toordinal()
        day_races[rec["d"]] += 1

        # Winner and runner-up (identified, finishing).
        winner_at = next(
            (i for i, e in enumerate(entries) if e[1] == 1 and _is_finisher(e[1], e[2])),
            None,
        )
        winner = entries[winner_at] if winner_at is not None else None
        runner_up = next((e for e in entries if e[1] == 2 and _is_finisher(e[1], e[2])), None)

        tags: dict[str, list[list[Any]]] = {kind: [] for kind in TAG_KINDS}

        # Mode record: the race's fastest time against the mode record.
        timed = [e for e in entries if _is_finisher(e[1], e[2]) and e[3] is not None]
        if timed:
            # Records and PBs compare whole seconds, the precision the page shows.
            fastest = min(timed, key=lambda e: e[3])
            fastest_seconds = round(fastest[3])
            previous = mode_best.get(mode)
            if previous is None or fastest_seconds < previous:
                if previous is not None:
                    tags["record"].append(["record", fastest[0], fastest_seconds, previous])
                mode_best[mode] = fastest_seconds
                mode_steps[mode].append([ri, fastest[0], fastest_seconds])

        # Preset record: the race's fastest time against the preset's record, unless the
        # wider mode record already tagged this race. The preset row still holds the
        # previous record here; it is updated further down.
        if timed and rec["p"] is not None and not tags["record"]:
            preset_best = preset_rows[rec["p"]]["best"]
            if preset_best is not None and fastest_seconds < preset_best[1]:
                tags["preset_record"].append(
                    ["preset_record", fastest[0], fastest_seconds, preset_best[1]]
                )

        # Upset, from pre-race ratings.
        if winner is not None and len(entries) >= UPSET_MIN_ENTRANTS:
            pre = ratings.pre[ri]
            candidates = [
                position
                for position, entry in enumerate(entries)
                if (state := states.get(entry[0])) is not None
                and state.finishes >= PROVISIONAL_FINISHES
            ]
            if candidates:
                favourite = max(candidates, key=lambda position: pre[position][0])
                favourite_entry = entries[favourite]
                if favourite_entry[0] != winner[0]:
                    chance = ratings.win_chance(
                        pre[winner_at], pre[favourite]
                    )
                    if chance <= UPSET_MAX_CHANCE:
                        tags["upset"].append(
                            ["upset", winner[0], favourite_entry[0], round(chance * 100, 1)]
                        )
                        upsets += 1

        # Photo finish, blowout and BigBrain.
        snipers: set[int] = set()
        sniped: set[int] = set()
        big_brain: set[int] = set()
        margin: float | None = None
        if (
            winner is not None
            and runner_up is not None
            and winner[3] is not None
            and runner_up[3] is not None
        ):
            margin = runner_up[3] - winner[3]
            if 0 <= margin < PHOTO_MAX_MARGIN:
                tags["photo"].append(["photo", winner[0], runner_up[0], round(margin, 2)])
                photos.append([ri, winner[0], runner_up[0], margin])
                snipers = {winner[0]}
                sniped = {runner_up[0]}
            if n >= BIG_BRAIN_MIN_FIELD and margin >= BIG_BRAIN_MIN_MARGIN:
                big_brain = {winner[0]}
            if n >= BLOWOUT_MIN_FIELD:
                blowouts.append([ri, winner[0], runner_up[0], margin])
        if winner is not None and n >= BIG_FIELD_SIZE:
            big_fields.append([ri, winner[0], n])

        # God Route and Upsetti need the pre-race ranking, so they run before state updates.
        god_route: set[int] = set()
        if margin is not None and margin > GOD_ROUTE_MIN_MARGIN and n >= GOD_ROUTE_MIN_FIELD:
            if _top_rated(states, race_ordinal, entries) != winner[0]:
                god_route = {winner[0]}
        upsetti: set[int] = set()
        if margin is not None and margin >= UPSETTI_MIN_MARGIN and n >= UPSETTI_MIN_FIELD:
            if _top_rated(states, race_ordinal, entries) == runner_up[0]:
                upsetti = {runner_up[0]}

        middle = _middle_finisher(entries, n)
        step_letter = _alphabetical_field(entries, n, rec["names"])
        rettel_pets = _alphabetical_field(entries, n, rec["names"], reverse=True)
        if step_letter:
            alphabetical_races += 1
        retimed = _retimed(timed)
        # A DNF against the only other racer, who finished, neither extends nor ends a finish run.
        excused_dnf = n == EXCUSED_DNF_FIELD and any(_is_finisher(e[1], e[2]) for e in entries)

        # Per-entry updates, in entry order.
        for position, (player, place, status, seconds) in enumerate(entries):
            state = states.get(player)
            if state is None:
                state = states[player] = _Player()
            finisher = _is_finisher(place, status)
            won = finisher and place == 1
            last_ordinal = state.last_ordinal if state.races else None

            state.races += 1
            if not state.dates or state.dates[-1] != rec["d"]:
                state.dates.append(rec["d"])
            if finisher:
                state.finishes += 1
                state.modes_done.add(mode)
                finishes_total += 1
                if mode_name == GLITCHED_MODE:
                    state.glitched += 1
                if mode_name == MYSTERY_MODE:
                    state.mystery += 1
                if mode_name == INVERTED_MODE:
                    state.inverted += 1
                if state.day != rec["d"]:
                    state.day = rec["d"]
                    state.day_finishes = 0
                state.day_finishes += 1
                state.best_day_finishes = max(state.best_day_finishes, state.day_finishes)
                if rec["h"] in NIGHT_HOURS:
                    night_finishes += 1
                    state.night_finishes += 1
            if won:
                if state.wins == 0:
                    tags["first_win"].append(["first_win", player])
                state.wins += 1
                state.modes_won.add(mode)
            if mode_name == ENTRANCE_MODE:
                state.entrance_days.step(race_ordinal)
                state.entrance_weeks.step((race_ordinal - 1) // 7)
            state.comment_streak = state.comment_streak + 1 if player in rec["c"] else 0
            state.best_comment_streak = max(state.best_comment_streak, state.comment_streak)
            state.runs["win"].step(ri, won)
            state.runs["podium"].step(ri, finisher and place <= 3)
            if not (excused_dnf and status == DNF_STATUS_CODE):
                state.runs["finish"].step(ri, finisher)

            palindrome = False
            if finisher and seconds is not None:
                total_seconds += seconds
                palindrome = is_palindrome_time(seconds)
                if palindrome:
                    palindromes += 1
                    if palindrome_example is None:
                        palindrome_example = seconds
                whole_seconds = round(seconds)
                previous_best = state.best_by_mode.get(mode)
                if previous_best is None:
                    state.best_by_mode[mode] = whole_seconds
                    state.pbs.setdefault(mode, []).append([ri, whole_seconds])
                elif whole_seconds < previous_best:
                    tags["pb"].append(["pb", player, whole_seconds, previous_best])
                    state.best_by_mode[mode] = whole_seconds
                    state.pbs[mode].append([ri, whole_seconds])
                if rec["p"] is not None:
                    state.preset_times[rec["p"]].append(seconds)
                if mode_name == MYSTERY_MODE:
                    state.mystery_fast |= seconds < BOTH_ENDS_FAST_SECONDS
                    state.mystery_slow |= seconds > BOTH_ENDS_SLOW_SECONDS

            if state.races in MILESTONES:
                tags["milestone"].append(["milestone", player, state.races])

            moment = _Moment(
                player,
                state,
                place,
                finisher,
                won,
                seconds,
                palindrome,
                mode_name,
                preset_name,
                n,
                race_ordinal,
                last_ordinal,
                snipers,
                sniped,
                god_route,
                upsetti,
                big_brain,
                middle,
                step_letter,
                rettel_pets,
                retimed,
            )
            for badge in BADGES:
                if badge.id in state.badges:
                    continue
                if badge.progress is not None:
                    unlocked = badge.progress(state) >= targets[badge.id]
                else:
                    unlocked = badge.earned(moment)
                if unlocked:
                    state.badges[badge.id] = ri
                    if badge.id != "first_blood":
                        tags["badge"].append(["badge", player, badge.id])

            state.mean = ratings.post[ri][position][0]
            state.last_ordinal = race_ordinal

        for entry_a in range(len(entries)):
            for entry_b in range(entry_a + 1, len(entries)):
                a, b = entries[entry_a][0], entries[entry_b][0]
                pair_counts[(a, b) if a < b else (b, a)] += 1

        preset = rec["p"]
        if preset is not None:
            row = preset_rows[preset]
            row["races"] += 1
            row["entries"] += len(entries)
            row["fields"] += n
            row["bad"] += sum(1 for e in entries if not _is_finisher(e[1], e[2]))
            for e in timed:
                row["times"].append(e[3])
                if row["best"] is None or round(e[3]) < row["best"][1]:
                    row["best"] = [e[0], round(e[3]), ri]
            if winner is not None and winner[3] is not None:
                row["wins"].append(winner[3])

        flat = [tag for kind in TAG_KINDS for tag in tags[kind]]
        for tag in flat:
            tag_counts[tag[0]] += 1
        race_tags.append(flat)

    # Presets.
    preset_stats: list[JsonObject] = []
    preset_medians: dict[int, float] = {}
    for index, row in enumerate(preset_rows):
        if row["times"]:
            preset_medians[index] = statistics.median(row["times"])
        preset_stats.append(
            {
                "races": row["races"],
                "entries": row["entries"],
                "dnf_rate": round(row["bad"] / row["entries"], 4) if row["entries"] else None,
                "median_seconds": round(preset_medians[index]) if index in preset_medians else None,
                "median_win_seconds": round(statistics.median(row["wins"])) if row["wins"] else None,
                "mean_field": round(row["fields"] / row["races"], 2) if row["races"] else None,
                "best": row["best"],
            }
        )
    eligible = [
        index
        for index, row in enumerate(preset_stats)
        if row["races"] >= PRESET_MIN_RACES and row["dnf_rate"] is not None
    ]
    hardest = (
        max(eligible, key=lambda index: (preset_stats[index]["dnf_rate"], -index))
        if eligible
        else None
    )

    # Players.
    player_out: list[JsonObject] = []
    daily: dict[int, tuple[list[Any], list[Any]]] = {}
    for index in range(len(players)):
        state = states.get(index)
        if state is None:
            player_out.append(_empty_player())
            continue
        daily[index] = _daily_streaks(state.dates)
        best_preset = None
        for preset, times in state.preset_times.items():
            median = preset_medians.get(preset)
            if (
                preset_stats[preset]["races"] < PRESET_MIN_RACES
                or len(times) < BEST_PRESET_MIN_FINISHES
                or not median
            ):
                continue
            ratio = statistics.median(times) / median
            if best_preset is None or ratio < best_preset[1]:
                best_preset = [preset, ratio]
        if best_preset:
            best_preset[1] = round(best_preset[1], 3)
        player_out.append(
            {
                "badges": dict(state.badges),
                "progress": _progress(state, targets),
                "pbs": {str(mode): steps for mode, steps in sorted(state.pbs.items())},
                "streaks": {
                    "daily": {"best": daily[index][0], "current": daily[index][1]},
                    **{
                        kind: {
                            "best": state.runs[kind].best_value(),
                            "current": state.runs[kind].current_value(),
                        }
                        for kind in STREAK_KINDS
                    },
                },
                "best_preset": best_preset,
            }
        )

    records_out = {
        "modes": [{"m": m, "steps": mode_steps[m]} for m in range(len(modes))],
        "photo": [
            [ri, w, r, round(margin, 2)]
            for ri, w, r, margin in _top(photos, lambda row: (row[3], row[0]), BOARD_TOP)
        ],
        "blowouts": [
            [ri, w, r, round(margin)]
            for ri, w, r, margin in _top(
                [row for row in blowouts if row[3] >= 0],
                lambda row: (-row[3], row[0]),
                BOARD_TOP,
            )
        ],
        "big_field": _top(big_fields, lambda row: (-row[2], row[0]), BOARD_TOP),
        "most_races": _top(
            [[index, state.races] for index, state in states.items()],
            lambda row: (-row[1], row[0]),
            BOARD_TOP,
        ),
        "rivals": _top(
            [[a, b, count] for (a, b), count in pair_counts.items()],
            lambda row: (-row[2], row[0], row[1]),
            BOARD_TOP,
        ),
        "streaks": _streak_boards(states, daily),
    }

    holders = {badge.id: 0 for badge in BADGES}
    for state in states.values():
        for badge_id in state.badges:
            holders[badge_id] += 1

    stats = _racer_stats(states, daily, pair_counts)
    claims = _claims(stats, states, holders)
    for index, player in enumerate(player_out):
        player["claim"] = claims.get(index)

    result = {
        "race_tags": race_tags,
        "players": player_out,
        "badges": [
            {
                "id": badge.id,
                "label": badge.label,
                "description": badge.description,
                "target": targets[badge.id],
                "image": badge_image_paths(badge.id)[0],
                "still": badge_image_paths(badge.id)[1],
            }
            for badge in BADGES
        ],
        "records": records_out,
        "preset_stats": preset_stats,
        "hardest_preset": hardest,
        "diagnostics": {
            "tag_counts": {kind: tag_counts[kind] for kind in TAG_KINDS},
            "badge_holders": holders,
            "racer_count": len(states),
            "upset_share": round(upsets / len(records), 4) if records else 0.0,
        },
    }
    result["facts"] = _facts(
        records=records,
        players=players,
        modes=modes,
        presets=presets,
        states=states,
        result=result,
        daily=daily,
        day_races=day_races,
        total_seconds=total_seconds,
        palindromes=palindromes,
        palindrome_example=palindrome_example,
        night_finishes=night_finishes,
        finishes_total=finishes_total,
        alphabetical_races=alphabetical_races,
        stats=stats,
    )
    return result


def _empty_player() -> JsonObject:
    empty = {"best": None, "current": None}
    return {
        "badges": {},
        "progress": {},
        "pbs": {},
        "streaks": {
            "daily": dict(empty),
            "win": dict(empty),
            "podium": dict(empty),
            "finish": dict(empty),
        },
        "best_preset": None,
        "claim": None,
    }


def _longest_break(dates: list[str]) -> int:
    """The most days between two consecutive race days; 0 under two dates (the Touched Grass measure)."""
    ordinals = [date.fromisoformat(d).toordinal() for d in dates]
    return max((b - a for a, b in zip(ordinals, ordinals[1:])), default=0)


def _racer_stats(
    states: dict[int, _Player],
    daily: dict[int, tuple[list[Any], list[Any]]],
    pair_counts: Counter[tuple[int, int]],
) -> dict[str, dict[int, int]]:
    """One number per racer for each claim kind; the facts and the claims both read it."""
    opponents: dict[int, set[int]] = defaultdict(set)
    for a, b in pair_counts:
        opponents[a].add(b)
        opponents[b].add(a)
    runs = {kind: _best_run(kind) for kind in STREAK_KINDS}
    return {
        "wins": {i: s.wins for i, s in states.items()},
        "win_streak": {i: runs["win"](s) for i, s in states.items()},
        "podium_streak": {i: runs["podium"](s) for i, s in states.items()},
        "finish_streak": {i: runs["finish"](s) for i, s in states.items()},
        "races": {i: s.races for i, s in states.items()},
        "daily": {i: daily[i][0][0] for i in states},
        "pbs": {i: sum(len(steps) - 1 for steps in s.pbs.values()) for i, s in states.items()},
        "opponents": {i: len(opponents[i]) for i in states},
        "night": {i: s.night_finishes for i, s in states.items()},
        "comeback": {i: _longest_break(s.dates) for i, s in states.items()},
    }


def _claims(
    stats: dict[str, dict[int, int]],
    states: dict[int, _Player],
    holders: dict[str, int],
) -> dict[int, list[Any] | None]:
    """Each racer's rarest accomplishment as [kind, value, count], or None when none is rare enough."""
    ranked = {kind: sorted(stats[kind].values()) for kind in CLAIM_KINDS}
    badge_order = {badge.id: position for position, badge in enumerate(BADGES)}
    claims: dict[int, list[Any] | None] = {}
    for index, state in states.items():
        options: list[tuple[int, int, list[Any]]] = []
        for position, kind in enumerate(CLAIM_KINDS):
            value = stats[kind][index]
            if value >= CLAIM_MIN_VALUE:
                count = len(ranked[kind]) - bisect.bisect_left(ranked[kind], value)
                options.append((count, position, [kind, value, count]))
        if state.badges:
            rarest = min(state.badges, key=lambda b: (holders[b], badge_order[b]))
            options.append((holders[rarest], len(CLAIM_KINDS), ["badge", rarest, holders[rarest]]))
        best = min(options, key=lambda option: option[:2], default=None)
        claims[index] = best[2] if best and best[0] <= CLAIM_MAX_SHARE * len(states) else None
    return claims


def _progress(state: _Player, targets: dict[str, int]) -> dict[str, int]:
    """Best value so far for each locked counter badge that takes more than one step."""
    return {
        badge.id: badge.progress(state)
        for badge in BADGES
        if badge.progress is not None
        and targets[badge.id] > 1
        and badge.id not in state.badges
    }


def _streak_boards(
    states: dict[int, _Player], daily: dict[int, tuple[list[Any], list[Any]]]
) -> JsonObject:
    boards: JsonObject = {}
    sources = {
        "daily": {index: pair for index, pair in daily.items()},
        **{
            kind: {
                index: (state.runs[kind].best_value(), state.runs[kind].current_value())
                for index, state in states.items()
            }
            for kind in STREAK_KINDS
        },
    }
    # Active streaks depend on today's date, so the page derives them from each racer's
    # own current run; only the all-time boards are exported.
    for kind, by_player in sources.items():
        best_rows = [
            [index, *best] for index, (best, _current) in by_player.items() if best
        ]
        order = lambda row: (-row[1], row[3], row[0])  # noqa: E731
        boards[kind] = {"best": sorted(best_rows, key=order)[:BOARD_BEST]}
    return boards


def _facts(
    *,
    records: list[JsonObject],
    players: list[JsonObject],
    modes: list[str],
    presets: list[JsonObject],
    states: dict[int, _Player],
    result: JsonObject,
    daily: dict[int, tuple[list[Any], list[Any]]],
    day_races: Counter[str],
    total_seconds: float,
    palindromes: int,
    palindrome_example: float | None,
    night_finishes: int,
    finishes_total: int,
    alphabetical_races: int,
    stats: dict[str, dict[int, int]],
) -> list[list[str]]:
    """Sentences as [before, headline, after]; the page highlights the headline."""

    def name(index: int) -> str:
        return players[index]["name"]

    def count(n: int) -> str:
        return f"{n:,}"

    def noun(n: int, word: str) -> str:
        return word if n == 1 else word + "s"

    def day(iso: str) -> str:
        when = date.fromisoformat(iso)
        return f"{MONTHS[when.month - 1]} {when.day}, {when.year}"

    def leader(kind: str) -> tuple[int, int] | None:
        """The racer with the highest value for a stat, ties to the lowest index."""
        values = stats[kind]
        if not values:
            return None
        index = max(values, key=lambda i: (values[i], -i))
        return index, values[index]

    facts: list[list[str]] = []
    if records and total_seconds:
        hours = round(total_seconds / 3600)
        facts.append(
            [
                "Racers have spent ",
                count(hours),
                f" {noun(hours, 'hour')} finishing {count(len(records))} {noun(len(records), 'race')}.",
            ]
        )

    photo = result["records"]["photo"]
    if photo:
        _ri, winner, runner_up, margin = photo[0]
        facts.append(
            [
                "The closest photo finish was ",
                f"{margin:.2f}",
                f" seconds, with {name(winner)} edging out {name(runner_up)}.",
            ]
        )

    by_races = sorted(
        (m for m in range(len(modes)) if modes[m] != UNKNOWN_MODE),
        key=lambda m: (-sum(1 for r in records if r["m"] == m), m),
    )
    for m in by_races[:FACT_RECORD_MODES]:
        steps = result["records"]["modes"][m]["steps"]
        if steps:
            _ri, holder, seconds = steps[-1]
            facts.append(
                [
                    f"The {mode_label(modes[m])} record is ",
                    format_duration(seconds),
                    f", held by {name(holder)}.",
                ]
            )

    if daily:
        index, (best, _current) = max(
            daily.items(), key=lambda item: (item[1][0][0], -item[0])
        )
        if best[0] > 1:
            facts.append(
                [
                    f"{name(index)} raced on ",
                    count(best[0]),
                    f" {noun(best[0], 'day')} in a row, from {day(best[1])} to {day(best[2])}.",
                ]
            )

    finish_board = result["records"]["streaks"]["finish"]["best"]
    if finish_board:
        index, length, *_rest = finish_board[0]
        facts.append(
            [f"{name(index)} finished ", count(length), f" {noun(length, 'race')} in a row."]
        )

    rivals = result["records"]["rivals"]
    if rivals:
        a, b, shared = rivals[0]
        facts.append(
            [f"{name(a)} and {name(b)} have shared ", count(shared), f" {noun(shared, 'race')}."]
        )

    if palindromes:
        verb = "reads" if palindromes == 1 else "read"
        facts.append(
            [
                "",
                count(palindromes),
                f" finish {noun(palindromes, 'time')} {verb} the same both ways, "
                f"like {format_duration(palindrome_example)}.",
            ]
        )

    hardest = result["hardest_preset"]
    if hardest is not None:
        row = result["preset_stats"][hardest]
        facts.append(
            [
                f"The {presets[hardest]['n']} preset is the hardest, with ",
                f"{row['dnf_rate'] * 100:.0f}%",
                " of entrants failing to finish.",
            ]
        )

    big = result["records"]["big_field"]
    if big:
        _ri, winner, size = big[0]
        facts.append([f"{name(winner)} won the biggest field yet, ", count(size), " entrants."])

    omnivores = result["diagnostics"]["badge_holders"]["omnivore"]
    if omnivores:
        facts.append(
            [
                "",
                count(omnivores),
                f" {noun(omnivores, 'racer')} {'has' if omnivores == 1 else 'have'} finished "
                f"races in {min(OMNIVORE_MODES, len(modes))} or more modes.",
            ]
        )

    if day_races:
        busiest, races = min(day_races.items(), key=lambda item: (-item[1], item[0]))
        facts.append(
            [f"The busiest day was {day(busiest)}, with ", count(races), f" {noun(races, 'race')}."]
        )

    if finishes_total and night_finishes:
        facts.append(
            [
                "",
                f"{night_finishes / finishes_total * 100:.0f}%",
                " of finishes come from races starting between midnight and 5 AM ET.",
            ]
        )

    most = result["records"]["most_races"]
    if most:
        facts.append(
            [f"{name(most[0][0])} has entered ", count(most[0][1]), f" {noun(most[0][1], 'race')}."]
        )

    blowouts = result["records"]["blowouts"]
    if blowouts and blowouts[0][3] > 0:
        _ri, winner, runner_up, margin = blowouts[0]
        facts.append(
            [
                f"{name(winner)} beat {name(runner_up)} by ",
                format_duration(margin),
                ", the biggest winning margin yet.",
            ]
        )

    for kind, middle in (("win", " won "), ("podium", " finished on the podium ")):
        board = result["records"]["streaks"][kind]["best"]
        if board and board[0][1] >= 2:
            index, length, *_rest = board[0]
            facts.append(
                [f"{name(index)}{middle}", count(length), f" {noun(length, 'race')} in a row."]
            )

    top = leader("wins")
    if top and top[1] > 0:
        facts.append([f"{name(top[0])} has won ", count(top[1]), f" {noun(top[1], 'race')}."])

    mode_wins: Counter[tuple[int, int]] = Counter(
        (entry[0], record["m"])
        for record in records
        if modes[record["m"]] != UNKNOWN_MODE
        for entry in record["e"]
        if entry[1] == 1 and _is_finisher(entry[1], entry[2])
    )
    if mode_wins:
        (index, m), wins = min(mode_wins.items(), key=lambda item: (-item[1], *item[0]))
        facts.append(
            [
                f"{name(index)} has won ",
                count(wins),
                f" {mode_label(modes[m])} {noun(wins, 'race')}.",
            ]
        )

    upsets = [tag for tags in result["race_tags"] for tag in tags if tag[0] == "upset"]
    if upsets:
        # Tags are in race order, so the first of equal chances is the earliest race.
        _kind, winner, favourite, chance = min(upsets, key=lambda tag: tag[3])
        facts.append(
            [
                f"{name(winner)} had a ",
                f"{chance:.1f}%",
                f" win chance against {name(favourite)} and won, the biggest upset yet.",
            ]
        )

    record_breaks = Counter(
        tag[1] for tags in result["race_tags"] for tag in tags if tag[0] == "record"
    )
    if record_breaks:
        index = max(record_breaks, key=lambda i: (record_breaks[i], -i))
        times = record_breaks[index]
        facts.append(
            [
                f"{name(index)} has broken a mode record ",
                count(times),
                f" {noun(times, 'time')}.",
            ]
        )

    if records:
        latest = max(date.fromisoformat(r["d"]).toordinal() for r in records)
        standing: list[tuple[int, int, list[Any]]] = []
        for m, mode_records in enumerate(result["records"]["modes"]):
            steps = mode_records["steps"]
            if modes[m] == UNKNOWN_MODE or not steps:
                continue
            age = latest - date.fromisoformat(records[steps[-1][0]]["d"]).toordinal()
            if age >= 1:
                standing.append((age, m, steps[-1]))
        if standing:
            age, m, (_ri, holder, seconds) = min(standing, key=lambda row: (-row[0], row[1]))
            facts.append(
                [
                    f"The {mode_label(modes[m])} record has stood for ",
                    count(age),
                    f" {noun(age, 'day')}, since {name(holder)} ran {format_duration(seconds)}.",
                ]
            )

    top = leader("opponents")
    if top and top[1] > 0:
        facts.append(
            [
                f"{name(top[0])} has raced against ",
                count(top[1]),
                f" different {noun(top[1], 'racer')}.",
            ]
        )

    if states:
        index = max(states, key=lambda i: (len(states[i].badges), -i))
        if states[index].badges:
            facts.append(
                [f"{name(index)} holds ", count(len(states[index].badges)), f" of the {len(BADGES)} badges."]
            )

    holders = result["diagnostics"]["badge_holders"]
    unlocked = [badge for badge in BADGES if holders[badge.id] > 0]
    if unlocked:
        rarest = min(unlocked, key=lambda badge: holders[badge.id])  # min keeps table order on ties
        held = holders[rarest.id]
        facts.append(
            [
                "Only ",
                count(held),
                f" {noun(held, 'racer')} {'holds' if held == 1 else 'hold'} {rarest.label}.",
            ]
        )

    top = leader("night")
    if top and top[1] > 0:
        facts.append(
            [
                f"{name(top[0])} has finished ",
                count(top[1]),
                f" {noun(top[1], 'race')} that started between midnight and 5 AM ET.",
            ]
        )

    top = leader("comeback")
    if top and top[1] >= TOUCHED_GRASS_DAYS:
        facts.append(
            [f"{name(top[0])} came back after ", count(top[1]), f" {noun(top[1], 'day')} away."]
        )

    weekdays = Counter(WEEKDAYS[date.fromisoformat(r["d"]).weekday()] for r in records)
    if weekdays:
        weekday, races = min(
            weekdays.items(), key=lambda item: (-item[1], WEEKDAYS.index(item[0]))
        )
        facts.append(
            [
                f"{weekday} is the busiest day of the week, with ",
                count(races),
                f" {noun(races, 'race')}.",
            ]
        )

    if alphabetical_races:
        verb = "has" if alphabetical_races == 1 else "have"
        facts.append(
            [
                "",
                count(alphabetical_races),
                f" {noun(alphabetical_races, 'race')} {verb} finished in perfect alphabetical order.",
            ]
        )

    slow = [
        index
        for index, row in enumerate(result["preset_stats"])
        if row["races"] >= PRESET_MIN_RACES and row["median_win_seconds"]
    ]
    if slow:
        index = max(
            slow, key=lambda i: (result["preset_stats"][i]["median_win_seconds"], -i)
        )
        facts.append(
            [
                f"{presets[index]['n']} has the slowest median winning time, ",
                format_duration(result["preset_stats"][index]["median_win_seconds"]),
                ".",
            ]
        )

    winners = sum(1 for state in states.values() if state.wins > 0)
    if winners:
        facts.append(
            ["", count(winners), f" of {count(len(states))} racers have won a race."]
        )

    return facts
