#!/usr/bin/env python3
"""Derive race tags, streaks, badges, records and facts from normalized races."""

from __future__ import annotations

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
LUCKSACK_WINS = 9
NO_DODGE_FINISHES = 10
MORE_NO_DODGE_FINISHES = 40
NICE_FINISHES = 69
CENTURIAN_FINISHES = 105
BLAZE_IT_FINISHES = 420
OVER_174_NON_WINS = 175
OMNIVORE_MODES = 7
GLITCHED_MODE = "glitched"
GLITCHED_TIERS = (1, 10, 30, 100)
CASUAL_BOOTS_PRESET = "Casual Boots"
JET_SEED_MAX_SECONDS = 4200
JET_LAG_MIN_SECONDS = 6000
ENTRANCE_MODE = "entrance"
ENTRANCED_DAYS = 5
ENTRANCED_WEEKS = 20
MYSTERY_MODE = "mystery"
BOTH_ENDS_FAST_SECONDS = 1800
BOTH_ENDS_SLOW_SECONDS = 7800
MIDDLE_MIN_FIELD = 5
RANDO_SICKO_FINISHES = 5
STEP_LETTER_MIN_FIELD = 5
NIGHT_HOURS = range(0, 5)
BIG_FIELD_SIZE = 16
GOD_ROUTE_TOP = 10
RANKING_WINDOW_DAYS = 90
PRESET_MIN_RACES = 8
BEST_PRESET_MIN_FINISHES = 3
BOARD_BEST = 10
BOARD_TOP = 10
FACT_RECORD_MODES = 3

DONE_STATUS_CODE = 0
STREAK_KINDS = ("win", "podium", "finish")
TAG_KINDS = ("record", "first_win", "upset", "photo", "pb", "milestone", "badge")

BADGE_IMAGE_DIR = "assets/badges"

JsonObject = dict[str, Any]


def _is_finisher(place: int | None, status_code: int) -> bool:
    return status_code == DONE_STATUS_CODE and place is not None


def is_palindrome_time(seconds: float) -> bool:
    """True when H:MM:SS, hour unpadded and colons removed, reads the same both ways."""
    whole = round(seconds)
    digits = f"{whole // 3600}{whole % 3600 // 60:02d}{whole % 60:02d}"
    return digits == digits[::-1]


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
        "day",
        "day_finishes",
        "best_day_finishes",
        "mystery_fast",
        "mystery_slow",
        "entrance_days",
        "entrance_weeks",
        "modes_done",
        "best_by_mode",
        "pbs",
        "badges",
        "runs",
        "dates",
        "preset_times",
        "mean",
        "last_ordinal",
    )

    def __init__(self) -> None:
        self.races = 0
        self.finishes = 0
        self.wins = 0
        self.glitched = 0
        self.day = ""
        self.day_finishes = 0
        self.best_day_finishes = 0
        self.mystery_fast = False
        self.mystery_slow = False
        self.entrance_days = _CalendarRun()
        self.entrance_weeks = _CalendarRun()
        self.modes_done: set[int] = set()
        self.best_by_mode: dict[int, int] = {}
        self.pbs: dict[int, list[list[int]]] = {}
        self.badges: dict[str, int] = {}
        self.runs = {kind: _Run() for kind in STREAK_KINDS}
        self.dates: list[str] = []
        self.preset_times: dict[int, list[float]] = defaultdict(list)
        self.mean: float | None = None
        self.last_ordinal = 0


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
    snipers: set[int]
    sniped: set[int]
    slayers: set[int]
    middle: set[int]
    step_letter: set[int]
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
        f"Finish ahead of the top-rated racer while ranked outside the top {GOD_ROUTE_TOP}.",
        earned=lambda m: m.player in m.slayers,
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
        "step_letter",
        "Step Letter",
        f"Be in a race of {STEP_LETTER_MIN_FIELD} or more where everyone finished "
        "in alphabetical order.",
        earned=lambda m: m.player in m.step_letter,
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


def _beats(a: tuple, b: tuple) -> bool:
    """Leaderboard H2H rule: lower place wins, any finisher beats a non-finisher."""
    a_finished = _is_finisher(a[1], a[2])
    b_finished = _is_finisher(b[1], b[2])
    if a_finished and b_finished:
        return a[1] < b[1]
    return a_finished and not b_finished


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


def _step_letter_field(entries: list[Any], n: int, players: list[JsonObject]) -> set[int]:
    """Every entrant, when all of a big enough field finished in alphabetical order."""
    if n < STEP_LETTER_MIN_FIELD or len(entries) != n:
        return set()
    if not all(_is_finisher(e[1], e[2]) for e in entries):
        return set()
    names = [players[e[0]]["name"].casefold() for e in sorted(entries, key=lambda e: e[1])]
    if names != sorted(names):
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

    # Omnivore asks for fewer modes when the data has fewer than its target.
    targets = {badge.id: badge.target for badge in BADGES}
    targets["omnivore"] = min(OMNIVORE_MODES, len(modes))

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

        # Photo finish and blowout.
        snipers: set[int] = set()
        sniped: set[int] = set()
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
            blowouts.append([ri, winner[0], runner_up[0], margin])
        if winner is not None and n >= BIG_FIELD_SIZE:
            big_fields.append([ri, winner[0], n])

        # God Route: needs the pre-race ranking, so it runs before state updates.
        slayers: set[int] = set()
        entrant_ids = {e[0] for e in entries}
        ranking = _ranking_top(states, race_ordinal)
        if ranking and ranking[0] in entrant_ids:
            number_one = next(e for e in entries if e[0] == ranking[0])
            top_ten = set(ranking[:GOD_ROUTE_TOP])
            for entry in entries:
                if entry[0] not in top_ten and _beats(entry, number_one):
                    slayers.add(entry[0])

        middle = _middle_finisher(entries, n)
        step_letter = _step_letter_field(entries, n, players)
        retimed = _retimed(timed)

        # Per-entry updates, in entry order.
        for position, (player, place, status, seconds) in enumerate(entries):
            state = states.get(player)
            if state is None:
                state = states[player] = _Player()
            finisher = _is_finisher(place, status)
            won = finisher and place == 1

            state.races += 1
            if not state.dates or state.dates[-1] != rec["d"]:
                state.dates.append(rec["d"])
            if finisher:
                state.finishes += 1
                state.modes_done.add(mode)
                finishes_total += 1
                if mode_name == GLITCHED_MODE:
                    state.glitched += 1
                if state.day != rec["d"]:
                    state.day = rec["d"]
                    state.day_finishes = 0
                state.day_finishes += 1
                state.best_day_finishes = max(state.best_day_finishes, state.day_finishes)
                if rec["h"] in NIGHT_HOURS:
                    night_finishes += 1
            if won:
                if state.wins == 0:
                    tags["first_win"].append(["first_win", player])
                state.wins += 1
            if mode_name == ENTRANCE_MODE:
                state.entrance_days.step(race_ordinal)
                state.entrance_weeks.step((race_ordinal - 1) // 7)
            state.runs["win"].step(ri, won)
            state.runs["podium"].step(ri, finisher and place <= 3)
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
                snipers,
                sniped,
                slayers,
                middle,
                step_letter,
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
        "streaks": _streak_boards(states, daily),
    }

    holders = {badge.id: 0 for badge in BADGES}
    for state in states.values():
        for badge_id in state.badges:
            holders[badge_id] += 1

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
        pair_counts=pair_counts,
        total_seconds=total_seconds,
        palindromes=palindromes,
        palindrome_example=palindrome_example,
        night_finishes=night_finishes,
        finishes_total=finishes_total,
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
    }


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
    pair_counts: Counter[tuple[int, int]],
    total_seconds: float,
    palindromes: int,
    palindrome_example: float | None,
    night_finishes: int,
    finishes_total: int,
) -> list[str]:
    def name(index: int) -> str:
        return players[index]["name"]

    def plural(count: int, noun: str) -> str:
        return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"

    facts: list[str] = []
    if records and total_seconds:
        facts.append(
            f"Racers have spent {round(total_seconds / 3600):,} hours finishing "
            f"{plural(len(records), 'race')}."
        )

    photo = result["records"]["photo"]
    if photo:
        _ri, winner, runner_up, margin = photo[0]
        facts.append(
            f"The closest photo finish was {margin:.2f} seconds, "
            f"with {name(winner)} edging out {name(runner_up)}."
        )

    by_races = sorted(
        range(len(modes)),
        key=lambda m: (-sum(1 for r in records if r["m"] == m), m),
    )
    for m in by_races[:FACT_RECORD_MODES]:
        steps = result["records"]["modes"][m]["steps"]
        if steps:
            _ri, holder, seconds = steps[-1]
            facts.append(
                f"The {modes[m]} record is {format_duration(seconds)}, held by {name(holder)}."
            )

    if daily:
        index, (best, _current) = max(
            daily.items(), key=lambda item: (item[1][0][0], -item[0])
        )
        if best[0] > 1:
            facts.append(
                f"{name(index)} raced on {plural(best[0], 'day')} in a row, "
                f"from {best[1]} to {best[2]}."
            )

    finish_board = result["records"]["streaks"]["finish"]["best"]
    if finish_board:
        index, length, *_rest = finish_board[0]
        facts.append(f"{name(index)} finished {plural(length, 'race')} in a row.")

    if pair_counts:
        (a, b), count = min(pair_counts.items(), key=lambda item: (-item[1], item[0]))
        facts.append(f"{name(a)} and {name(b)} have shared {plural(count, 'race')}.")

    if palindromes:
        facts.append(
            f"{plural(palindromes, 'finish time')} read the same both ways, "
            f"like {format_duration(palindrome_example)}."
        )

    hardest = result["hardest_preset"]
    if hardest is not None:
        row = result["preset_stats"][hardest]
        label = presets[hardest]["n"]
        facts.append(
            f"The {label} preset is the hardest, with {row['dnf_rate'] * 100:.0f}% "
            f"of entrants failing to finish."
        )

    big = result["records"]["big_field"]
    if big:
        _ri, winner, size = big[0]
        facts.append(f"{name(winner)} won the biggest field yet, {size} entrants.")

    omnivores = result["diagnostics"]["badge_holders"]["omnivore"]
    if omnivores:
        facts.append(
            f"{plural(omnivores, 'racer')} {'has' if omnivores == 1 else 'have'} finished a race in "
            f"{min(OMNIVORE_MODES, len(modes))} different modes."
        )

    if day_races:
        day, count = min(day_races.items(), key=lambda item: (-item[1], item[0]))
        facts.append(f"The busiest day was {day}, with {plural(count, 'race')}.")

    if finishes_total and night_finishes:
        facts.append(
            f"{night_finishes / finishes_total * 100:.0f}% of finishes come from races "
            f"starting between midnight and 5 AM ET."
        )

    most = result["records"]["most_races"]
    if most:
        facts.append(f"{name(most[0][0])} has entered {plural(most[0][1], 'race')}.")

    return facts
