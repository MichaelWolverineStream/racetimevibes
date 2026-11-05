#!/usr/bin/env python3
"""Propose a season of ladder schedules from past participation, as four sets.

The method in plain words
-------------------------
Every slot the alttpr.racing schedule ever held is joined to the race it became.
A slot that never became an archived race was cancelled, because rooms with
fewer than two entrants are never archived, so it counts as zero entrants. Over
the last year, a race's expected turnout is its category's average in that
four-hour part of the Eastern Time day, adjusted for the exact hour of the
date's day group and for the mode.

Days fall into three groups: Work (Monday to Wednesday), Casual (Thursday and
Friday) and Prime (Saturday, Sunday and the common US days off). The plan file
sets each group's prime and quiet hours; the other hours are normal. It also sets
the season: its first day and its length in weeks. A season's races end at
midnight after the last Thursday of those weeks, and the days left are the pause
before the next season.

The plan file lists the categories and modes to run; the planner picks none of
its own. Its between categories (glitched and spoiler) race only between two
other races; every other category is a main category. Each set runs a fixed
number of main races a day per day group, and the between races come on top.
Each category first gets a featured race in prime time, on a weekday of its own,
at the same hour all season. Each date then takes its main races at the hours
that hold the most prime races, at least two hours apart and, outside prime
time, preferably three, while leaving the next date room for its races. Each
race goes to the main category furthest below its share of that band, by
expected turnout. A between category races in a gap 1 to 2 hours after one main
race and 1 to 2 hours before the next whenever it is behind its own share. A
category never races in a band where its rooms almost never fired. The planner
is deterministic.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import difflib
import json
import math
import re
import sys
from bisect import bisect_left, bisect_right, insort
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from html import escape
from itertools import combinations, pairwise, permutations, product
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Supports both `python3 scripts/schedule_planner.py` and `from scripts import schedule_planner`.
try:
    from scripts import (
        build_stats,
        mode_overlap,
        participant_stats,
        race_archive,
        schedule_archive,
    )
except ImportError:
    import build_stats
    import mode_overlap
    import participant_stats
    import race_archive
    import schedule_archive

# alttpr.racing names no archetypes, so this map is checked against the races they became.
ARCHETYPE_CATEGORIES = {
    1: "simple",
    2: "entrance",
    3: "inverted",
    4: "keys",
    5: "glitched",
    6: "fun",
    7: "mystery",
    9: "spoiler",
}
CONFIG_PATH = Path(__file__).resolve().parents[1] / "schedule-plan.json"
TIMEZONE = build_stats.SCHEDULE_TIMEZONE
SEED_RACE_PATTERN = re.compile(r"getseed\.php\?race=(\d+)")

HISTORY_DAYS = 365
# The newest day is left out, because its rooms may not be archived yet.
SETTLE = timedelta(days=1)
JOIN_EARLY = timedelta(minutes=5)
JOIN_LATE = timedelta(minutes=30)
BLOCK_HOURS = 4
SHRINK_SLOTS = 20
FIT_PASSES = 2
DAY_GROUPS = ("work", "casual", "prime")
# Monday to Sunday; a holiday counts as a prime day.
WEEKDAY_GROUPS = ("work", "work", "work", "casual", "casual", "prime", "prime")
HOLIDAY_NAMES = (
    "New Year's Day",
    "Memorial Day",
    "Independence Day",
    "Labor Day",
    "Thanksgiving",
    "Black Friday",
    "Christmas Eve",
    "Christmas Day",
    "New Year's Eve",
)
BANDS = ("prime", "normal", "quiet")
# A plan file band: "first-last", Eastern Time hours with both ends included.
BAND_RANGE = re.compile(r"(\d{1,2})-(\d{1,2})")
# A category may race in a band where its rooms fired at least this often.
NEAR_NEVER_RATE = 0.10
# Today's grid runs a race every two hours.
TODAY_PER_DAY = 12
LOW_RATE = 0.20
RACE_HOURS = 2
RACE_LENGTH = timedelta(hours=RACE_HOURS)
# Outside prime time, main races prefer to start at least this far apart.
WIDE_GAP_HOURS = 3
WIDE_GAP = timedelta(hours=WIDE_GAP_HOURS)
FEATURED_SHRINK = 5
MIN_MODE_SLOTS = 20
# A season runs DEFAULT_WEEKS weeks, pause included, and its races end at midnight
# after its last DEFAULT_LAST_DAY.
DEFAULT_WEEKS = 9
DEFAULT_LAST_DAY = "thursday"
EFFECTS = ("avoid", "single", "block")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
CATEGORY_KEYS = {"modes", "dailyMax", "weeklyMax", "onlyPrimeTime", "between"}
CONFIG_KEYS = {"bands", "composition", "events", "season"}
SEASON_KEYS = {"start", "weeks", "lastDay"}
EVENT_KEYS = {"name", "weekly", "date", "start", "timezone", "hours", "effect", "categories"}
CSV_COLUMNS = ("date", "time", "category", "mode", "mode_id", "utc")
RATE_COLUMNS = (
    "category",
    "weekday",
    "hour",
    "band",
    "scheduled",
    "fired",
    "rate",
    "low",
    "closed",
)

JsonObject = dict[str, Any]
Pair = frozenset[str]


class ScheduleError(RuntimeError):
    """Raised when the history or the configuration cannot produce a schedule."""


@dataclass(frozen=True)
class RaceSet:
    """How many races a proposal holds on a date, by the date's day group."""

    name: str
    per_day: dict[str, int]

    @property
    def weekly(self) -> int:
        """Races in a week without holidays."""
        return sum(self.per_day[group] for group in WEEKDAY_GROUPS)

    @property
    def description(self) -> str:
        work, casual, prime = (self.per_day[group] for group in DAY_GROUPS)
        return (
            f"{work} main races a day Monday to Wednesday, {casual} on Thursday and Friday, "
            f"and {prime} at weekends and on holidays: {self.weekly} a week."
        )


SETS = (
    RaceSet("light", {"work": 7, "casual": 8, "prime": 9}),
    RaceSet("medium", {"work": 8, "casual": 9, "prime": 10}),
    RaceSet("full", {"work": 9, "casual": 10, "prime": 11}),
    RaceSet("bonanza", {"work": 10, "casual": 11, "prime": 12}),
)

SHARES = ("entrants", "fill", "equal")


@dataclass(frozen=True)
class Rules:
    """The planner's own rules. SHIPPED is what the report runs; schedule_lab.py varies them."""

    # Each category gets a featured race in prime time, on a weekday of its own.
    featured: bool = True
    # How a band's races are shared: "entrants" by each category's expected entrants in
    # the band, "fill" by how often its rooms fired there, "equal" alike.
    shares: str = "entrants"
    # A category never races in a band where its rooms fired less than NEAR_NEVER_RATE.
    band_rule: bool = True
    # The plan file's between categories start only between two main races, on top of
    # the main races. Off, they are main categories like the others.
    between: bool = True
    # Outside prime time, main races prefer to start WIDE_GAP_HOURS apart.
    wide_gaps: bool = True
    # Prime-time races run the category's favourite mode.
    favourite: bool = True
    # A race avoids the category that ran at its hour a week and a day earlier.
    rotation: bool = True

    def __post_init__(self) -> None:
        if self.shares not in SHARES:
            raise ScheduleError(
                f"Unknown share rule {self.shares!r}; expected one of {', '.join(SHARES)}"
            )


SHIPPED = Rules()


# Calendar --------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@cache
def holiday_dates(year: int) -> frozenset[date]:
    """Return the common US days off in a year, on their own dates, with no observed days."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return frozenset(
        {
            date(year, 1, 1),  # New Year's Day
            _last_weekday(year, 5, 0),  # Memorial Day
            date(year, 7, 4),  # Independence Day
            _nth_weekday(year, 9, 0, 1),  # Labor Day
            thanksgiving,
            thanksgiving + timedelta(days=1),  # Black Friday
            date(year, 12, 24),  # Christmas Eve
            date(year, 12, 25),  # Christmas Day
            date(year, 12, 31),  # New Year's Eve
        }
    )


def is_holiday(day: date) -> bool:
    return day in holiday_dates(day.year)


def day_group(day: date, holidays: bool = True) -> str:
    """Return a date's day group; with `holidays`, a holiday is a prime day."""
    if holidays and is_holiday(day):
        return "prime"
    return WEEKDAY_GROUPS[day.weekday()]


@dataclass(frozen=True)
class HistorySlot:
    started_at: datetime
    season: int
    category: str
    mode_id: int
    entrants: int
    fired: bool


@dataclass(frozen=True)
class Model:
    """Factors of expected entrants per slot, and the fired share per category and block."""

    block: dict[str, list[float]]
    # Per day group, one factor per hour.
    hour: dict[str, list[float]]
    mode: dict[int, float]
    mode_slots: Counter[int]
    fill: dict[str, list[float]]
    # Whether a holiday counts as a prime day.
    holidays: bool = True

    def group(self, day: date) -> str:
        return day_group(day, self.holidays)

    def at(self, category: str, mode_factor: float, group: str, hour: int) -> float:
        return self.block[category][hour // BLOCK_HOURS] * self.hour[group][hour] * mode_factor

    def expected(self, category: str, mode_factor: float, local: datetime) -> float:
        return self.at(category, mode_factor, self.group(local.date()), local.hour)


@dataclass(frozen=True)
class Mode:
    name: str
    id: int
    factor: float
    # The category of the mode's archetype, which its races count under.
    source: str
    slots: int = 0
    retired: bool = False


@dataclass(frozen=True)
class Category:
    """A plan category: a key of the plan's composition, with its modes in plan order."""

    name: str
    modes: tuple[Mode, ...]
    daily_max: int | None = None
    weekly_max: int | None = None
    only_prime: bool = False
    # Its races start only between two main races.
    between: bool = False

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(mode.source for mode in self.modes))

    @property
    def mixed(self) -> bool:
        return len(self.sources) > 1

    @property
    def favourite_mode(self) -> Mode:
        """The most popular mode: one with enough slots first, then by factor, then plan order."""
        return min(
            self.modes,
            key=lambda mode: (
                mode.slots < MIN_MODE_SLOTS,
                -mode.factor,
                self.modes.index(mode),
            ),
        )

    def at(self, model: Model, group: str, hour: int) -> float:
        """Expected entrants: the mean over the modes, each at its own category."""
        return sum(
            model.at(mode.source, mode.factor, group, hour) for mode in self.modes
        ) / len(self.modes)

    def expected(self, model: Model, local: datetime) -> float:
        return self.at(model, model.group(local.date()), local.hour)


@dataclass(frozen=True)
class PairScore:
    score: float | None
    weak: bool


@dataclass(frozen=True)
class Event:
    name: str
    effect: str
    categories: frozenset[str]
    windows: tuple[tuple[datetime, datetime], ...]
    # Whether the event repeats every week.
    weekly: bool = False

    def touches(self, started_at: datetime) -> bool:
        """Return whether a race starting then would run during the event."""
        ends_at = started_at + RACE_LENGTH
        return any(start < ends_at and started_at < end for start, end in self.windows)


@dataclass(frozen=True)
class Start:
    """An hour of a date where a race may start."""

    utc: datetime
    local: datetime
    group: str
    band: str
    # The band value: the plan categories' mean expected entrants at the group-hour.
    value: float


@dataclass(frozen=True)
class Race:
    """One placed race of a proposal."""

    start: Start
    category: str
    featured: bool
    # A race of a between category, which starts only between two main races.
    between: bool


# History ---------------------------------------------------------------------


def seed_race_id(info: Any) -> int | None:
    match = SEED_RACE_PATTERN.search(info) if isinstance(info, str) else None
    return int(match.group(1)) if match else None


def slot_time(row: JsonObject) -> datetime:
    return datetime.fromtimestamp(int(row["time"]), UTC)


def history_slots(
    schedule: list[JsonObject], modes: list[JsonObject], races: list[JsonObject]
) -> tuple[list[HistorySlot], datetime, datetime]:
    """Join the scheduled slots of the history window to the races they became.

    Returns the slots and the window's UTC start and end.
    """
    modes_by_id = {mode["id"]: mode for mode in modes}
    by_seed: dict[int, JsonObject] = {}
    unseeded: list[JsonObject] = []
    excluded: set[int] = set()
    newest = None
    for race in races:
        context, reason = build_stats.classify_race(race)
        seed = seed_race_id(race.get("info"))
        if context is None:
            if reason[0] == "mode" and seed is not None:
                excluded.add(seed)
            continue
        newest = max(newest or context["started_at"], context["started_at"])
        if seed is None:
            unseeded.append(context)
        else:
            by_seed[seed] = context
    if newest is None:
        raise ScheduleError("The archive has no races to learn from")
    end = newest - SETTLE
    start = end - timedelta(days=HISTORY_DAYS)

    slots = []
    used: set[str] = set()
    for row in sorted(schedule, key=lambda row: (int(row["time"]), row["id"])):
        started_at = slot_time(row)
        if not start <= started_at < end or row["raceId"] in excluded:
            continue
        mode = modes_by_id.get(row["mode"])
        if mode is None:
            raise ScheduleError(f"Slot {row['id']} names mode {row['mode']}, which is not listed")
        category = ARCHETYPE_CATEGORIES.get(mode["archetype"])
        context = by_seed.get(row["raceId"]) or _race_by_time(
            unseeded, started_at, category, used
        )
        if context is not None:
            used.add(context["race"]["name"])
            slots.append(
                HistorySlot(
                    started_at=started_at,
                    season=row["season"],
                    category=context["mode"],
                    mode_id=row["mode"],
                    entrants=context["entrants_count"],
                    fired=True,
                )
            )
        elif row["raceId"] is None:
            continue  # pulled from the schedule
        elif category is None:
            raise ScheduleError(
                f"Mode {mode['name']} has archetype {mode['archetype']}, which has no category"
            )
        else:
            slots.append(
                HistorySlot(
                    started_at=started_at,
                    season=row["season"],
                    category=category,
                    mode_id=row["mode"],
                    entrants=0,
                    fired=False,
                )
            )
    if not slots:
        raise ScheduleError("No scheduled slots fall in the history window")
    return slots, start, end


def _race_by_time(
    contexts: list[JsonObject], started_at: datetime, category: str | None, used: set[str]
) -> JsonObject | None:
    """Return the race without a seed id that started at this slot in its category."""
    for context in contexts:
        if (
            context["mode"] == category
            and context["race"]["name"] not in used
            and started_at - JOIN_EARLY <= context["started_at"] <= started_at + JOIN_LATE
        ):
            return context
    return None


# Demand model ----------------------------------------------------------------


def _shrink(actual: float, predicted: float, count: int, prior: float) -> float:
    ratio = actual / predicted if predicted > 0 else prior
    return (count * ratio + SHRINK_SLOTS * prior) / (count + SHRINK_SLOTS)


def fit_model(slots: list[HistorySlot], *, holidays: bool = True) -> Model:
    """Fit block × group-hour × mode to the entrants per slot by backfitting.

    The group-hour factor is per day group and hour, so it carries both the group's level
    and its shape over the day. With `holidays`, a slot on a holiday counts as a prime day.
    """
    rows = []
    for slot in slots:
        local = slot.started_at.astimezone(TIMEZONE)
        group = day_group(local.date(), holidays)
        rows.append((slot.category, local.hour, group, slot.mode_id, slot.entrants))
    categories = sorted({row[0] for row in rows})
    blocks = 24 // BLOCK_HOURS
    hour = {group: [1.0] * 24 for group in DAY_GROUPS}
    mode: dict[int, float] = {}
    block: dict[str, list[float]] = {}

    def fit_block() -> None:
        base = [hour[g][h] * mode.get(m, 1.0) for _c, h, g, m, _x in rows]
        actual: dict[str, float] = defaultdict(float)
        predicted: dict[str, float] = defaultdict(float)
        for (c, _h, _g, _m, x), b in zip(rows, base):
            actual[c] += x
            predicted[c] += b
        rate = {c: actual[c] / predicted[c] if predicted[c] > 0 else 0.0 for c in categories}
        block_actual = [0.0] * blocks
        block_predicted = [0.0] * blocks
        for (c, h, _g, _m, x), b in zip(rows, base):
            block_actual[h // BLOCK_HOURS] += x
            block_predicted[h // BLOCK_HOURS] += rate[c] * b
        overall = [
            block_actual[i] / block_predicted[i] if block_predicted[i] > 0 else 1.0
            for i in range(blocks)
        ]
        sums: dict[tuple[str, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        for (c, h, _g, _m, x), b in zip(rows, base):
            cell = sums[c, h // BLOCK_HOURS]
            cell[0] += x
            cell[1] += b
            cell[2] += 1
        for c in categories:
            block[c] = []
            for i in range(blocks):
                actual_sum, predicted_sum, count = sums.get((c, i), (0.0, 0.0, 0))
                block[c].append(
                    _shrink(actual_sum, predicted_sum, count, rate[c] * overall[i])
                )

    def refit(key, others) -> dict[Any, float]:
        sums: dict[Any, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        for row in rows:
            cell = sums[key(row)]
            cell[0] += row[4]
            cell[1] += others(row)
            cell[2] += 1
        return {k: _shrink(a, p, n, 1.0) for k, (a, p, n) in sums.items()}

    fit_block()
    for _pass in range(FIT_PASSES):
        fitted = refit(
            lambda r: (r[2], r[1]),
            lambda r: block[r[0]][r[1] // BLOCK_HOURS] * mode.get(r[3], 1.0),
        )
        smooth = {}
        for g in DAY_GROUPS:
            raw = [fitted.get((g, h), 1.0) for h in range(24)]
            smooth[g] = [(raw[h - 1] + 2 * raw[h] + raw[(h + 1) % 24]) / 4 for h in range(24)]
        # Each block's twelve group-hours average 1, so a group keeps its level per block.
        hour = {g: [0.0] * 24 for g in DAY_GROUPS}
        for i in range(blocks):
            hours = range(i * BLOCK_HOURS, (i + 1) * BLOCK_HOURS)
            mean = sum(smooth[g][h] for g in DAY_GROUPS for h in hours) / (
                len(DAY_GROUPS) * BLOCK_HOURS
            )
            for g in DAY_GROUPS:
                for h in hours:
                    hour[g][h] = smooth[g][h] / mean

        mode = refit(
            lambda r: r[3],
            lambda r: block[r[0]][r[1] // BLOCK_HOURS] * hour[r[2]][r[1]],
        )
        fit_block()

    fired: dict[str, list[list[int]]] = defaultdict(lambda: [[0, 0] for _ in range(blocks)])
    for slot, (c, h, _g, _m, _x) in zip(slots, rows):
        fired[c][h // BLOCK_HOURS][0] += slot.fired
        fired[c][h // BLOCK_HOURS][1] += 1
    fill = {}
    for c, cells in fired.items():
        overall = sum(f for f, _n in cells) / sum(n for _f, n in cells)
        fill[c] = [f / n if n else overall for f, n in cells]

    return Model(
        block=block,
        hour=hour,
        mode=mode,
        mode_slots=Counter(row[3] for row in rows),
        fill=fill,
        holidays=holidays,
    )


# Configuration ---------------------------------------------------------------


def load_config(path: Path) -> JsonObject:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ScheduleError(f"Config {path} does not exist") from error
    except json.JSONDecodeError as error:
        raise ScheduleError(f"Config {path} is not valid JSON") from error
    if not isinstance(config, dict) or set(config) - CONFIG_KEYS or "bands" not in config:
        raise ScheduleError(
            "The config must be an object with bands, composition, events and season"
        )
    if not isinstance(config.get("composition"), dict) or not config["composition"]:
        raise ScheduleError("The config needs a non-empty composition object")
    if not isinstance(config.get("events", []), list):
        raise ScheduleError("The config's events must be a list")
    return config


def _range_hours(group: str, band: str, value: Any) -> list[int]:
    match = BAND_RANGE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ScheduleError(f'bands: {group} {band} must be a range of hours such as "12-15"')
    first, last = int(match[1]), int(match[2])
    if first > 23 or last > 23:
        raise ScheduleError(f"bands: {group} {band} names an hour outside 0 to 23")
    # A first hour after the last wraps past midnight.
    return [(first + offset) % 24 for offset in range((last - first) % 24 + 1)]


def parse_bands(raw: Any) -> dict[tuple[str, int], str]:
    """Return the plan file's band of every day group's hours: prime, quiet, or normal."""
    if not isinstance(raw, dict) or set(raw) != set(DAY_GROUPS):
        raise ScheduleError("bands must hold exactly " + ", ".join(DAY_GROUPS))
    layout = {}
    for group in DAY_GROUPS:
        entry = raw[group]
        if not isinstance(entry, dict) or set(entry) != {"prime", "quiet"}:
            raise ScheduleError(f"bands: {group} must hold exactly prime and quiet")
        prime = _range_hours(group, "prime", entry["prime"])
        quiet = _range_hours(group, "quiet", entry["quiet"])
        if set(prime) & set(quiet):
            raise ScheduleError(f"bands: {group}'s prime and quiet hours overlap")
        for hour in range(24):
            layout[group, hour] = "prime" if hour in prime else "quiet" if hour in quiet else "normal"
    return layout


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _mode_key(name: str) -> str:
    """Compare names ignoring case, spacing and the style of brackets."""
    return " ".join(name.casefold().replace("[", "(").replace("]", ")").split())


def _find_mode(category: str, label: Any, api_modes: list[JsonObject]) -> JsonObject:
    """Return the one alttpr.racing mode a plan name means; an exact match wins."""
    if not isinstance(label, str) or not label:
        raise ScheduleError(f"{category}: modes must be a list of mode names")
    matches = [mode for mode in api_modes if mode["name"] == label] or [
        mode for mode in api_modes if _mode_key(mode["name"]) == _mode_key(label)
    ]
    if len(matches) > 1:
        ids = ", ".join(str(mode["id"]) for mode in sorted(matches, key=lambda mode: mode["id"]))
        raise ScheduleError(f"{category}: {label!r} names several modes (ids {ids})")
    if not matches:
        close = difflib.get_close_matches(label, [mode["name"] for mode in api_modes], n=3)
        hint = f"; did you mean {', '.join(repr(name) for name in close)}?" if close else ""
        raise ScheduleError(f"{category}: no mode is called {label!r}{hint}")
    return matches[0]


def resolve_composition(
    config: JsonObject, api_modes: list[JsonObject], model: Model
) -> tuple[list[Category], list[str]]:
    """Return the plan categories with their modes in plan order, and those left out."""
    categories = []
    left_out = []
    for name, entry in config["composition"].items():
        if not isinstance(entry, dict) or set(entry) - CATEGORY_KEYS or "modes" not in entry:
            raise ScheduleError(
                f"{name}: needs modes, and its allowed keys are "
                + ", ".join(sorted(CATEGORY_KEYS))
            )
        if not isinstance(entry["modes"], list):
            raise ScheduleError(f"{name}: modes must be a list of mode names")
        for key in ("dailyMax", "weeklyMax"):
            if key in entry and not _is_count(entry[key]):
                raise ScheduleError(f"{name}: {key} must be a whole number of at least 1")
        only_prime = entry.get("onlyPrimeTime", False)
        if not isinstance(only_prime, bool):
            raise ScheduleError(f"{name}: onlyPrimeTime must be true or false")
        between = entry.get("between", False)
        if not isinstance(between, bool):
            raise ScheduleError(f"{name}: between must be true or false")
        if not entry["modes"]:
            left_out.append(name)
            continue

        modes = []
        for label in entry["modes"]:
            found = _find_mode(name, label, api_modes)
            source = ARCHETYPE_CATEGORIES.get(found["archetype"])
            if source is None:
                raise ScheduleError(
                    f"{name}: {found['name']} has archetype {found['archetype']}, "
                    "which has no category"
                )
            if source not in model.block:
                raise ScheduleError(
                    f"{name}: {found['name']} is a {source} mode, and {source} has no "
                    "slots in the history window"
                )
            modes.append(
                Mode(
                    name=found["name"],
                    id=found["id"],
                    factor=model.mode.get(found["id"], 1.0),
                    source=source,
                    slots=model.mode_slots[found["id"]],
                    retired=not found["active"],
                )
            )
        if len({mode.id for mode in modes}) < len(modes):
            raise ScheduleError(f"{name}: a mode is listed twice")
        categories.append(
            Category(
                name=name,
                modes=tuple(modes),
                daily_max=entry.get("dailyMax"),
                weekly_max=entry.get("weeklyMax"),
                only_prime=only_prime,
                between=between,
            )
        )

    if not categories:
        raise ScheduleError("The composition lists no modes")
    if all(category.between for category in categories):
        raise ScheduleError("Every plan category is a between category; at least one must not be")
    return categories, left_out


def parse_events(
    raw_events: list[Any], categories: set[str], period_start: datetime, period_end: datetime
) -> list[Event]:
    """Resolve each event's occurrences that reach into the period, as UTC windows."""
    events = []
    for index, raw in enumerate(raw_events):
        label = f"Event {index + 1}"
        if not isinstance(raw, dict) or set(raw) - EVENT_KEYS:
            raise ScheduleError(f"{label}: allowed keys are {', '.join(sorted(EVENT_KEYS))}")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ScheduleError(f"{label} needs a name")
        label = f"Event {name!r}"
        effect = raw.get("effect")
        if effect not in EFFECTS:
            raise ScheduleError(f"{label}: effect must be one of {', '.join(EFFECTS)}")
        listed = raw.get("categories")
        if effect == "avoid":
            if (
                not isinstance(listed, list)
                or not listed
                or any(category not in categories for category in listed)
            ):
                raise ScheduleError(f"{label}: avoid needs a list of known categories")
        elif listed is not None:
            raise ScheduleError(f"{label}: only avoid takes categories")
        hours = raw.get("hours")
        if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
            raise ScheduleError(f"{label}: hours must be a positive number")
        try:
            zone = ZoneInfo(raw.get("timezone"))
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ScheduleError(f"{label}: timezone must be an IANA name such as UTC") from error
        try:
            starts_at = time.fromisoformat(raw.get("start"))
        except (TypeError, ValueError) as error:
            raise ScheduleError(f"{label}: start must be HH:MM") from error

        if ("weekly" in raw) == ("date" in raw):
            raise ScheduleError(f"{label} needs exactly one of weekly and date")
        if "weekly" in raw:
            if raw["weekly"] not in WEEKDAYS:
                raise ScheduleError(f"{label}: weekly must be a weekday name such as monday")
            weekday = WEEKDAYS.index(raw["weekly"])
            first = period_start.astimezone(zone).date() - timedelta(days=7)
            last = period_end.astimezone(zone).date()
            days = [
                first + timedelta(days=offset)
                for offset in range((last - first).days + 1)
                if (first + timedelta(days=offset)).weekday() == weekday
            ]
        else:
            try:
                days = [date.fromisoformat(raw["date"])]
            except (TypeError, ValueError) as error:
                raise ScheduleError(f"{label}: date must be YYYY-MM-DD") from error

        windows = []
        for day in days:
            start = datetime.combine(day, starts_at, tzinfo=zone).astimezone(UTC)
            end = start + timedelta(hours=hours)
            if start < period_end and period_start < end:
                windows.append((start, end))
        events.append(
            Event(
                name=name,
                effect=effect,
                categories=frozenset(listed or ()),
                windows=tuple(windows),
                weekly="weekly" in raw,
            )
        )
    return events


def expand_events(events: list[Event], categories: list[Category]) -> list[Event]:
    """Make an avoid event that lists a category also avoid each plan category with its modes."""
    expanded = []
    for event in events:
        if event.effect == "avoid":
            mixed_in = {
                category.name
                for category in categories
                if event.categories & set(category.sources)
            }
            event = replace(event, categories=event.categories | mixed_in)
        expanded.append(event)
    return expanded


# Overlap ---------------------------------------------------------------------


def pair_scores(contexts: list[JsonObject], categories: list[str]) -> dict[Pair, PairScore]:
    """Score every pair of categories with the mode overlap analysis, without intervals."""
    monthly = mode_overlap.monthly_entries(contexts)
    scores = {}
    for a, b in combinations(categories, 2):
        pair = mode_overlap.pair_overlap(monthly, a, b, weights=[])
        scores[frozenset((a, b))] = PairScore(score=pair.score, weak=pair.weak)
    return scores


def category_pairs(
    categories: list[Category], pairs: dict[Pair, PairScore]
) -> dict[Pair, PairScore]:
    """Score plan categories by their sources: the highest of their sources' scores, weak when
    any is. The score is missing when they share a source or any source score is missing.
    """
    scores = {}
    for first, second in combinations(categories, 2):
        sources = [
            pairs.get(frozenset((a, b)), PairScore(None, True))
            for a in first.sources
            for b in second.sources
        ]
        known = [pair.score for pair in sources]
        scores[frozenset((first.name, second.name))] = PairScore(
            score=None if None in known else max(known),
            weak=any(pair.weak for pair in sources),
        )
    return scores


# Period ----------------------------------------------------------------------


@dataclass(frozen=True)
class Season:
    """The plan file's season. A start of None means the first day after the mirror."""

    start: date | None
    weeks: int
    last_day: int  # weekday, Monday 0


def parse_season(raw: Any) -> Season:
    """Validate the plan file's optional season; a missing key takes its default."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - SEASON_KEYS:
        raise ScheduleError("season must be an object with at most start, weeks and lastDay")
    start = raw.get("start")
    if start is not None:
        try:
            start = date.fromisoformat(start)
        except (TypeError, ValueError) as error:
            raise ScheduleError("season: start must be YYYY-MM-DD") from error
    weeks = raw.get("weeks", DEFAULT_WEEKS)
    if not _is_count(weeks):
        raise ScheduleError("season: weeks must be a whole number of at least 1")
    last_day = raw.get("lastDay", DEFAULT_LAST_DAY)
    if last_day not in WEEKDAYS:
        raise ScheduleError("season: lastDay must be a weekday name such as thursday")
    return Season(start=start, weeks=weeks, last_day=WEEKDAYS.index(last_day))


def default_start(schedule: list[JsonObject]) -> date:
    """Return the first date whose midnight ET is at least two hours after the last slot."""
    if not schedule:
        raise ScheduleError("The schedule mirror is empty; run schedule_archive.py first")
    last = max(slot_time(row) for row in schedule)
    day = last.astimezone(TIMEZONE).date()
    while datetime.combine(day, time(0), tzinfo=TIMEZONE) < last + RACE_LENGTH:
        day += timedelta(days=1)
    return day


def last_race_day(start: date, weeks: int, last_day: int) -> date:
    """Return the last weekday `last_day` in the `weeks` weeks from the start date."""
    day = start + timedelta(weeks=weeks, days=-1)
    return day - timedelta(days=(day.weekday() - last_day) % 7)


def season_bounds(start: date, weeks: int, last_day: int) -> tuple[datetime, datetime]:
    """Return the season's UTC bounds: midnight ET on the start date to midnight ET after
    its last race day."""
    first = datetime.combine(start, time(0), tzinfo=TIMEZONE)
    end_day = last_race_day(start, weeks, last_day) + timedelta(days=1)
    end = datetime.combine(end_day, time(0), tzinfo=TIMEZONE)
    return first.astimezone(UTC), end.astimezone(UTC)


def plan_season(
    config: JsonObject,
    schedule: list[JsonObject],
    start: date | None = None,
    weeks: int | None = None,
) -> tuple[date, int, int]:
    """Return the season's first day, weeks and last weekday. `start` and `weeks` override
    the plan file; without either, the first day follows the mirrored schedule."""
    season = parse_season(config.get("season"))
    weeks = season.weeks if weeks is None else weeks
    if weeks < 1:
        raise ScheduleError("weeks must be at least 1")
    first_day = start or season.start or default_start(schedule)
    return first_day, weeks, season.last_day


def period_dates(period_start: datetime, period_end: datetime) -> list[date]:
    """Return every Eastern Time date in the period, counted by date, so DST cannot shift it."""
    first = period_start.astimezone(TIMEZONE).date()
    last = (period_end - timedelta(microseconds=1)).astimezone(TIMEZONE).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _local(naive: datetime) -> datetime | None:
    """Localise a wall-clock time, or None when DST skips it. A repeated hour takes its first."""
    local = naive.replace(tzinfo=TIMEZONE)
    if local.astimezone(UTC).astimezone(TIMEZONE).replace(tzinfo=None) != naive:
        return None
    return local


# Bands -----------------------------------------------------------------------


def group_bands(
    layout: dict[tuple[str, int], str], model: Model, categories: list[Category]
) -> dict[tuple[str, int], tuple[str, float]]:
    """Give each day group's hours their plan file band and their value: the plan
    categories' mean expected entrants there."""
    return {
        (group, hour): (
            layout[group, hour],
            sum(category.at(model, group, hour) for category in categories) / len(categories),
        )
        for group in DAY_GROUPS
        for hour in range(24)
    }


def band_rates(
    slots: list[HistorySlot], model: Model, bands: dict
) -> dict[str, dict[str, tuple[int, int]]]:
    """Return, per category, its fired and scheduled history slots in each band.

    A slot's band is that of its own date's day group at its hour.
    """
    counts: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: {band: [0, 0] for band in BANDS}
    )
    for slot in slots:
        local = slot.started_at.astimezone(TIMEZONE)
        cell = counts[slot.category][bands[model.group(local.date()), local.hour][0]]
        cell[0] += slot.fired
        cell[1] += 1
    return {
        category: {band: (fired, scheduled) for band, (fired, scheduled) in by_band.items()}
        for category, by_band in counts.items()
    }


def fire_rate(counts: tuple[int, int]) -> float:
    """Fired over scheduled; a band with no slots never fired."""
    fired, scheduled = counts
    return fired / scheduled if scheduled else 0.0


def open_bands(
    category: Category, rates: dict[str, dict[str, tuple[int, int]]], *, band_rule: bool = True
) -> tuple[str, ...]:
    """Return the bands a plan category may race in.

    With the band rule, every source must have fired at least NEAR_NEVER_RATE of its slots
    there. With onlyPrimeTime, only prime time.
    """
    bands = ("prime",) if category.only_prime else BANDS
    if band_rule:
        bands = tuple(
            band
            for band in bands
            if all(fire_rate(rates[source][band]) >= NEAR_NEVER_RATE for source in category.sources)
        )
    if not bands:
        raise ScheduleError(f"{category.name} may race in no band")
    return bands


def share_weights(
    categories: list[Category],
    model: Model,
    bands: dict,
    rates: dict[str, dict[str, tuple[int, int]]],
    open_by_name: dict[str, tuple[str, ...]],
    shares: str,
) -> dict[str, dict[str, float]]:
    """Return each plan category's weight in each band; 0 where the band is closed to it.

    "entrants": its mean expected entrants over the band's weekday-hours, each weekday in
    its own day group. "fill": the mean over its modes of their category's fire rate there.
    "equal": 1.
    """
    cells = {
        band: [
            (WEEKDAY_GROUPS[weekday], hour)
            for weekday in range(7)
            for hour in range(24)
            if bands[WEEKDAY_GROUPS[weekday], hour][0] == band
        ]
        for band in BANDS
    }
    weights = {}
    for category in categories:
        weights[category.name] = {}
        for band in BANDS:
            if band not in open_by_name[category.name] or not cells[band]:
                weight = 0.0
            elif shares == "equal":
                weight = 1.0
            elif shares == "fill":
                weight = sum(
                    fire_rate(rates[mode.source][band]) for mode in category.modes
                ) / len(category.modes)
            else:
                weight = sum(
                    category.at(model, group, hour) for group, hour in cells[band]
                ) / len(cells[band])
            weights[category.name][band] = weight
    return weights


# Featured races --------------------------------------------------------------


def featured_slots(
    categories: list[Category],
    model: Model,
    history: list[HistorySlot],
    bands: dict,
    open_by_name: dict[str, tuple[str, ...]],
    events: list[Event],
    dates: list[date],
) -> dict[str, tuple[int, int]]:
    """Give each category a weekday of its own and a prime hour on it, for the whole season.

    A candidate is a prime hour of the weekday's own day group, unless a weekly event
    rules its race out in any week of the period: a block event, or an avoid event that
    lists the category. A between category's candidates run from 01:00 to 22:00 only, so
    its bracket can lie on the same date. Each category's best candidate on a weekday has the highest
    `(entrants + FEATURED_SHRINK · E) / (slots + FEATURED_SHRINK)`, from its sources' past
    slots at that weekday-hour, ties to the earlier hour. The weekdays are assigned to
    maximise the summed score; on a tie the first assignment found, in weekday order,
    wins. Returns {category: (weekday, hour)}.
    """
    if len(categories) > len(WEEKDAYS):
        raise ScheduleError(
            f"{len(categories)} categories cannot each have a featured race on a weekday "
            "of its own"
        )
    weekly = [event for event in events if event.weekly and event.effect in ("block", "avoid")]
    record: dict[tuple[str, int, int], list[int]] = defaultdict(lambda: [0, 0])
    for slot in history:
        local = slot.started_at.astimezone(TIMEZONE)
        entry = record[slot.category, local.weekday(), local.hour]
        entry[0] += 1
        entry[1] += slot.entrants

    def ruled_out(name: str, weekday: int, hour: int) -> bool:
        for day in dates:
            local = _local(datetime.combine(day, time(hour))) if day.weekday() == weekday else None
            if local is not None and any(
                event.touches(local.astimezone(UTC))
                and (event.effect == "block" or name in event.categories)
                for event in weekly
            ):
                return True
        return False

    best: dict[str, dict[int, tuple[float, int]]] = {}
    for category in categories:
        best[category.name] = {}
        if "prime" not in open_by_name[category.name]:
            continue
        for weekday in range(7):
            group = WEEKDAY_GROUPS[weekday]
            options = []
            for hour in range(24):
                if (
                    bands[group, hour][0] != "prime"
                    # A between race needs a start 1 to 2 hours on each side, on its own date.
                    or (category.between and not 1 <= hour <= 22)
                    or ruled_out(category.name, weekday, hour)
                ):
                    continue
                races = sum(record[source, weekday, hour][0] for source in category.sources)
                entrants = sum(record[source, weekday, hour][1] for source in category.sources)
                if category.mixed:
                    expected = category.at(model, group, hour)
                else:
                    expected = model.at(
                        category.sources[0], category.favourite_mode.factor, group, hour
                    )
                score = (entrants + FEATURED_SHRINK * expected) / (races + FEATURED_SHRINK)
                options.append((score, -hour))
            if options:
                score, negative_hour = max(options)
                best[category.name][weekday] = (score, -negative_hour)

    names = [category.name for category in categories]
    chosen: tuple[float, tuple[int, ...]] | None = None
    for weekdays in permutations(range(7), len(names)):
        if any(weekday not in best[name] for name, weekday in zip(names, weekdays)):
            continue
        total = sum(best[name][weekday][0] for name, weekday in zip(names, weekdays))
        if chosen is None or total > chosen[0]:
            chosen = (total, weekdays)
    if chosen is None:
        raise ScheduleError(
            "The featured races cannot each have a weekday of their own with a prime hour "
            "left"
        )
    return {name: (weekday, best[name][weekday][1]) for name, weekday in zip(names, chosen[1])}


# Filling ---------------------------------------------------------------------


HourScore = tuple[int, int, int, int, int, float]


def choose_hours(
    options: list[Start],
    *,
    need: int,
    fixed: Sequence[Start] = (),
    forced: Sequence[Start] = (),
    previous: Start | None = None,
    wide_gaps: bool = True,
    following: Callable[[Start | None], int] | None = None,
) -> tuple[HourScore, list[Start]] | None:
    """Pick a date's main race hours.

    `fixed` starts hold main races already, and `forced` starts must be picked; both are
    taken. Any of `options` may be picked. Every two taken starts are at least a race length
    apart, and at most `need` starts are picked, forced ones included. Among such picks the
    winner has the most hours; then the most hours `following` gives the next date, from the
    last main race taken (or `previous`); then the most prime; then the fewest short gaps;
    then the most normal; then the highest value; then the earliest hours. A short gap is two
    consecutive main races, counted on from `previous`, less than WIDE_GAP apart unless both
    are prime; it counts only with `wide_gaps`.

    Returns the score `(hours, next hours, prime, -short gaps, normal, value)` and the picked
    starts in time order, or None when the fixed and forced starts cannot all be taken.
    """
    items = sorted(
        [(start, "fixed") for start in fixed]
        + [(start, "forced") for start in forced]
        + [(start, "free") for start in options],
        key=lambda item: item[0].utc,
    )
    count = len(items)
    # Whether a fixed or forced start lies at this index or later.
    bound = [False] * (count + 1)
    for index in range(count - 1, -1, -1):
        bound[index] = bound[index + 1] or items[index][1] != "free"

    def short(first: Start | None, second: Start) -> int:
        return int(
            wide_gaps
            and first is not None
            and second.utc - first.utc < WIDE_GAP
            and not (first.band == "prime" and second.band == "prime")
        )

    @cache
    def best(last: int, left: int) -> tuple[HourScore, tuple[int, ...]] | None:
        # `last` is the index of the latest start taken, or -1 before the first.
        before = items[last][0] if last >= 0 else previous
        found = []
        if not bound[last + 1]:
            following_hours = following(before) if following is not None else 0
            found.append(((0, following_hours, 0, 0, 0, 0.0), ()))
        for index in range(last + 1, count):
            start, kind = items[index]
            if last >= 0 and start.utc - items[last][0].utc < RACE_LENGTH:
                if kind != "free":
                    break
                continue
            picked = kind != "fixed"
            if not picked or left > 0:
                rest = best(index, left - picked)
                if rest is not None:
                    (hours, following_hours, prime, gaps, normal, value), picks = rest
                    found.append(
                        (
                            (
                                hours + picked,
                                following_hours,
                                prime + (picked and start.band == "prime"),
                                gaps - short(before, start),
                                normal + (picked and start.band == "normal"),
                                value + (start.value if picked else 0.0),
                            ),
                            (index, *picks) if picked else picks,
                        )
                    )
            if kind != "free":
                # A fixed or forced start cannot be passed over.
                break
        # max keeps the first on a tie, which takes the earlier start.
        return max(found, key=lambda entry: entry[0]) if found else None

    result = best(-1, max(need, 0))
    if result is None:
        return None
    return result[0], [items[index][0] for index in result[1]]


def clash(
    starts: list[datetime],
    categories: list[str],
    expected: list[float],
    pairs: dict[Pair, PairScore],
) -> float:
    """Return the expected entrants lost to races less than a race length apart.

    Each such pair costs its pair score × the smaller expected entrants, charged to the race
    with fewer, or to the later race on a tie. A missing pair score counts as 1. No race is
    charged more than its own expected entrants.
    """
    order = sorted(range(len(starts)), key=lambda index: starts[index])
    charged = [0.0] * len(starts)
    for position, first in enumerate(order):
        for second in order[position + 1 :]:
            if starts[second] - starts[first] >= RACE_LENGTH:
                break
            pair = pairs.get(frozenset((categories[first], categories[second])))
            score = 1.0 if pair is None or pair.score is None else pair.score
            smaller = first if expected[first] < expected[second] else second
            charged[smaller] += score * expected[smaller]
    return sum(min(charge, expected[index]) for index, charge in enumerate(charged))


class Filler:
    """Places a proposal's races date by date, keeping every rule.

    Featured races come first. Each date then picks its main race hours (choose_hours) and
    gives them main categories, prime first. A date's between races go into the gaps
    between its main races once the next date's main races are placed.
    """

    def __init__(
        self,
        categories: list[Category],
        model: Model,
        bands: dict,
        open_by_name: dict[str, tuple[str, ...]],
        weights: dict[str, dict[str, float]],
        events: list[Event],
        rules: Rules,
    ) -> None:
        self.names = [category.name for category in categories]
        self.between_set = {
            category.name for category in categories if rules.between and category.between
        }
        self.main_names = [name for name in self.names if name not in self.between_set]
        self.between_names = [name for name in self.names if name in self.between_set]
        self.order = {name: rank for rank, name in enumerate(self.names)}
        self.limits = {
            category.name: (category.daily_max, category.weekly_max) for category in categories
        }
        self.model = model
        self.bands = bands
        self.open = open_by_name
        self.weights = weights
        self.rules = rules
        self.by_effect = {
            effect: [event for event in events if event.effect == effect] for effect in EFFECTS
        }
        # Placed races by UTC start, kept sorted; and the main races' starts alone.
        self.times: list[datetime] = []
        self.main_times: list[datetime] = []
        self.placed: dict[datetime, Race] = {}
        self.on_day: dict[date, list[Race]] = defaultdict(list)
        # Placed races by wall-clock start, for the repeat preferences.
        self.by_local: dict[datetime, Race] = {}
        # Main races per date.
        self.day_races: Counter[date] = Counter()
        self.day_count: Counter[tuple[date, str]] = Counter()
        self.week_count: Counter[tuple[date, str]] = Counter()
        # Races so far per band and category, and per band, for the shares.
        self.taken: Counter[tuple[str, str]] = Counter()
        self.band_races: Counter[str] = Counter()
        self.band_weight = {
            band: sum(weights[name][band] for name in self.names) for band in BANDS
        }
        # Picked main race hours that no main category could take.
        self.empty = 0

    @staticmethod
    def _week(day: date) -> date:
        return day - timedelta(days=day.weekday())

    def start(self, day: date, hour: int) -> Start | None:
        """The start at a date's hour, or None when DST skips it."""
        local = _local(datetime.combine(day, time(hour)))
        if local is None:
            return None
        group = self.model.group(day)
        band, value = self.bands[group, hour]
        return Start(utc=local.astimezone(UTC), local=local, group=group, band=band, value=value)

    def place(self, start: Start, name: str, *, featured: bool = False) -> None:
        race = Race(start=start, category=name, featured=featured, between=name in self.between_set)
        day = start.local.date()
        insort(self.times, start.utc)
        if not race.between:
            insort(self.main_times, start.utc)
            self.day_races[day] += 1
        self.placed[start.utc] = race
        self.on_day[day].append(race)
        self.by_local[start.local.replace(tzinfo=None)] = race
        self.day_count[day, name] += 1
        self.week_count[self._week(day), name] += 1
        self.taken[start.band, name] += 1
        self.band_races[start.band] += 1

    def neighbours(
        self, utc: datetime, *, main: bool = False
    ) -> tuple[Race | None, Race | None]:
        """The placed races just before and just after a start; with `main`, the main races."""
        times = self.main_times if main else self.times
        index = bisect_left(times, utc)
        before = self.placed[times[index - 1]] if index > 0 else None
        after = self.placed[times[index]] if index < len(times) else None
        return before, after

    def near(self, utc: datetime) -> list[Race]:
        """The placed races that start less than a race length from this start."""
        index = bisect_left(self.times, utc - RACE_LENGTH + timedelta(microseconds=1))
        close = []
        while index < len(self.times) and self.times[index] < utc + RACE_LENGTH:
            if self.times[index] != utc:
                close.append(self.placed[self.times[index]])
            index += 1
        return close

    def touches(self, effect: str, utc: datetime, name: str | None = None) -> bool:
        return any(
            event.touches(utc) and (name is None or name in event.categories)
            for event in self.by_effect[effect]
        )

    def single_clash(self, utc: datetime) -> bool:
        """Whether a race here would start less than a race length from another while either
        is inside a single event."""
        close = self.near(utc)
        return bool(close) and (
            self.touches("single", utc)
            or any(self.touches("single", race.start.utc) for race in close)
        )

    def fits(self, start: Start, name: str) -> bool:
        """Whether a category can take a start without breaking a rule with the races placed."""
        day = start.local.date()
        daily_max, weekly_max = self.limits[name]
        return not (
            start.band not in self.open[name]
            or self.touches("avoid", start.utc, name)
            or (daily_max is not None and self.day_count[day, name] >= daily_max)
            or (weekly_max is not None and self.week_count[self._week(day), name] >= weekly_max)
            or any(
                race is not None and race.category == name
                for race in self.neighbours(start.utc, main=name not in self.between_set)
            )
            or self.single_clash(start.utc)
        )

    def choose(self, start: Start, names: list[str]) -> str:
        """Pick among fitting categories: not last week's at this hour, then the one furthest
        below its share of the band, then not yesterday's at this hour, then plan order."""
        naive = start.local.replace(tzinfo=None)
        week = self.by_local.get(naive - timedelta(days=7))
        day = self.by_local.get(naive - timedelta(days=1))
        rotate = self.rules.rotation

        def key(name: str) -> tuple[bool, float, bool, int]:
            weight = self.weights[name][start.band]
            return (
                rotate and week is not None and week.category == name,
                self.taken[start.band, name] / weight if weight > 0 else math.inf,
                rotate and day is not None and day.category == name,
                self.order[name],
            )

        return min(names, key=key)

    def due(self, name: str, band: str) -> bool:
        """Whether a between category is at or below the band's pace: all its races so far
        over the summed weight of every plan category there."""
        weight = self.weights[name][band]
        if weight <= 0:
            return False
        return self.taken[band, name] / weight <= self.band_races[band] / self.band_weight[band]

    def free(self, day: date) -> list[Start]:
        """The date's hours that exist, hold no race and are outside every block event."""
        starts = []
        for hour in range(24):
            start = self.start(day, hour)
            if (
                start is not None
                and start.utc not in self.placed
                and not self.touches("block", start.utc)
            ):
                starts.append(start)
        return sorted(starts, key=lambda start: start.utc)

    def open_hours(self, day: date) -> list[Start]:
        """The date's free starts at least a race length from every main race, and not less
        than a race length from a race while either is inside a single event."""
        return [
            start
            for start in self.free(day)
            if not any(not race.between for race in self.near(start.utc))
            and not self.single_clash(start.utc)
        ]

    def bracketed(self, utc: datetime) -> bool:
        """Whether a start lies 1 to 2 hours after one main race and 1 to 2 hours before the
        next, with no other race between those two."""
        index = bisect_left(self.main_times, utc)
        if index == 0 or index == len(self.main_times):
            return False
        before, after = self.main_times[index - 1], self.main_times[index]
        hour = timedelta(hours=1)
        if not (hour <= utc - before <= RACE_LENGTH and hour <= after - utc <= RACE_LENGTH):
            return False
        inside = bisect_left(self.times, after) - bisect_right(self.times, before)
        return inside == int(utc in self.placed)

    def bracket_open(self, day: date, start: Start) -> bool:
        """Whether the date has a free start 1 to 2 hours before this one and another 1 to 2
        hours after it."""
        hour = timedelta(hours=1)
        free = self.free(day)
        return any(hour <= start.utc - other.utc <= RACE_LENGTH for other in free) and any(
            hour <= other.utc - start.utc <= RACE_LENGTH for other in free
        )

    def place_featured(
        self, featured: dict[str, tuple[int, int]], dates: list[date], wanted: dict[date, int]
    ) -> dict[str, list[str]]:
        """Place every featured race of the period; return each category's skipped dates.

        A featured race is skipped where DST removes its hour, a block event covers it or an
        avoid event that lists its category covers it. A between category's featured race is
        also skipped on a date that wants fewer than two main races or has no open bracket.
        Two featured races less than a race length apart are an error.
        """
        skipped: dict[str, list[str]] = defaultdict(list)
        for day in dates:
            for name, (weekday, hour) in featured.items():
                if day.weekday() != weekday:
                    continue
                start = self.start(day, hour)
                if (
                    start is None
                    or self.touches("block", start.utc)
                    or self.touches("avoid", start.utc, name)
                    or (
                        name in self.between_set
                        and (wanted[day] < 2 or not self.bracket_open(day, start))
                    )
                ):
                    skipped[name].append(day.isoformat())
                    continue
                close = self.near(start.utc)
                if close:
                    raise ScheduleError(
                        f"The featured races of {name} and {close[0].category} would start "
                        f"less than {RACE_HOURS} hours apart"
                    )
                self.place(start, name, featured=True)
        return dict(skipped)

    def previous_main(self, day: date) -> Start | None:
        """The last main race placed before the date's midnight."""
        midnight = datetime.combine(day, time(0), tzinfo=TIMEZONE).astimezone(UTC)
        index = bisect_left(self.main_times, midnight)
        return self.placed[self.main_times[index - 1]].start if index else None

    def capacity(self, day: date, wanted: int) -> Callable[[Start | None], int]:
        """How many main races a date could still take, at most `wanted` with its featured
        ones, after a main race at the given start (or none)."""
        options = self.open_hours(day)
        fixed = [race.start for race in self.on_day[day] if not race.between]
        first = options[0].utc if options else None

        @cache
        def count(cutoff: datetime | None) -> int:
            usable = [start for start in options if cutoff is None or start.utc >= cutoff]
            result = choose_hours(usable, need=wanted - len(fixed), fixed=fixed, wide_gaps=False)
            return result[0][0] if result is not None else 0

        def after(last: Start | None) -> int:
            cutoff = None if last is None else last.utc + RACE_LENGTH
            if cutoff is not None and first is not None and cutoff <= first:
                cutoff = None
            return count(cutoff)

        return after

    def fill_mains(self, day: date, wanted: int, next_wanted: int | None = None) -> None:
        """Bring a date up to `wanted` main races.

        Its hours also keep as many main races as they can for the next date, which wants
        `next_wanted`. A between race already on the date must get a main race 1 to 2 hours
        on each side; when no pick can do that, the date picks without it. A picked hour that
        no main category fits stays empty and counts in `empty`.
        """
        following = (
            self.capacity(day + timedelta(days=1), next_wanted)
            if next_wanted is not None
            else None
        )
        options = self.open_hours(day)
        fixed = [race.start for race in self.on_day[day] if not race.between]
        sides: list[list[Start | None]] = []
        for race in self.on_day[day]:
            if not race.between:
                continue
            for offsets in ((-2, -1), (1, 2)):
                times = {race.start.utc + timedelta(hours=offset) for offset in offsets}
                if any(
                    moment in self.placed and not self.placed[moment].between
                    for moment in times
                ):
                    sides.append([None])
                else:
                    sides.append([start for start in options if start.utc in times])
        found = []
        for combo in product(*sides):
            forced = sorted(
                {start for start in combo if start is not None}, key=lambda start: start.utc
            )
            result = choose_hours(
                [start for start in options if start not in forced],
                need=wanted - len(fixed),
                fixed=fixed,
                forced=forced,
                previous=self.previous_main(day),
                wide_gaps=self.rules.wide_gaps,
                following=following,
            )
            if result is not None:
                found.append(result)
        if not found:
            result = choose_hours(
                options,
                need=wanted - len(fixed),
                fixed=fixed,
                previous=self.previous_main(day),
                wide_gaps=self.rules.wide_gaps,
                following=following,
            )
            if result is None:
                raise ScheduleError(f"The featured races of {day} cannot all be kept")
            found.append(result)
        # The best score, then the earliest hours.
        _, picked = max(
            found,
            key=lambda entry: (entry[0], [-start.utc.timestamp() for start in entry[1]]),
        )
        for band in BANDS:
            for start in picked:
                if start.band != band:
                    continue
                names = [name for name in self.main_names if self.fits(start, name)]
                if names:
                    self.place(start, self.choose(start, names))
                else:
                    self.empty += 1

    def fill_between(self, day: date) -> None:
        """Place a date's between races in the gaps between its main races.

        Gap hours go prime first, then by value; each takes a between category that is due
        in its band and fits, if any.
        """
        if not self.between_names:
            return
        for band in BANDS:
            hours = sorted(
                (start for start in self.free(day) if start.band == band),
                key=lambda start: (-start.value, start.utc),
            )
            for start in hours:
                if not self.bracketed(start.utc):
                    continue
                names = [
                    name
                    for name in self.between_names
                    if self.due(name, band) and self.fits(start, name)
                ]
                if names:
                    self.place(start, self.choose(start, names))

    def short_gaps(self) -> int:
        """Consecutive main races less than WIDE_GAP apart, unless both start in prime time."""
        return sum(
            later - earlier < WIDE_GAP
            and not (
                self.placed[earlier].start.band == "prime"
                and self.placed[later].start.band == "prime"
            )
            for earlier, later in pairwise(self.main_times)
        )

    def unbracketed(self) -> int:
        """Between races without a main race 1 to 2 hours on each side."""
        return sum(
            not self.bracketed(utc) for utc in self.times if self.placed[utc].between
        )

    def races(self) -> list[Race]:
        return [self.placed[utc] for utc in self.times]


# Proposals -------------------------------------------------------------------


def plan_proposal(
    race_set: RaceSet,
    *,
    history: list[HistorySlot],
    model: Model,
    categories: list[Category],
    layout: dict[tuple[str, int], str],
    pairs: dict[Pair, PairScore],
    events: list[Event],
    period_start: datetime,
    period_end: datetime,
    rules: Rules,
) -> JsonObject:
    """Place one set's races over the period.

    `layout` is the plan file's bands (see parse_bands), `pairs` scores the plan categories
    (see category_pairs), and the avoid events already avoid each plan category with a mode
    from a category they list (see expand_events). `rules` is SHIPPED for the report;
    schedule_lab.py passes others.
    """
    if not rules.between:
        categories = [replace(category, between=False) for category in categories]
    names = [category.name for category in categories]
    by_name = {category.name: category for category in categories}
    bands = group_bands(layout, model, categories)
    rates = band_rates(history, model, bands)
    open_by_name = {
        category.name: open_bands(category, rates, band_rule=rules.band_rule)
        for category in categories
    }
    weights = share_weights(categories, model, bands, rates, open_by_name, rules.shares)
    dates = period_dates(period_start, period_end)
    featured = (
        featured_slots(categories, model, history, bands, open_by_name, events, dates)
        if rules.featured
        else {}
    )

    filler = Filler(categories, model, bands, open_by_name, weights, events, rules)
    wanted = {day: race_set.per_day[model.group(day)] for day in dates}
    skipped = filler.place_featured(featured, dates, wanted)
    for index, day in enumerate(dates):
        filler.fill_mains(day, wanted[day], wanted.get(day + timedelta(days=1)))
        # The previous date's gaps now have their main races on both sides.
        if index:
            filler.fill_between(dates[index - 1])
    if dates:
        filler.fill_between(dates[-1])
    placed = filler.races()

    turn: Counter[str] = Counter()
    races = []
    for race in placed:
        category = by_name[race.category]
        start = race.start
        favourite = not category.mixed and (
            race.featured or (rules.favourite and start.band == "prime")
        )
        if favourite:
            mode = category.favourite_mode
        else:
            mode = category.modes[turn[category.name] % len(category.modes)]
            turn[category.name] += 1
        races.append(
            {
                "utc": start.utc.isoformat().replace("+00:00", "Z"),
                "date": start.local.date().isoformat(),
                "time": start.local.strftime("%H:00"),
                "weekday": start.local.weekday(),
                "hour": start.local.hour,
                "group": start.group,
                "band": start.band,
                "category": category.name,
                "mode": mode.name,
                "mode_id": mode.id,
                "source": mode.source,
                "expected": model.expected(mode.source, mode.factor, start.local),
                "fill": model.fill[mode.source][start.local.hour // BLOCK_HOURS],
                "featured": race.featured,
                "between": race.between,
            }
        )

    lost = clash(
        [race.start.utc for race in placed],
        [race.category for race in placed],
        [race["expected"] for race in races],
        pairs,
    )
    repeats = 0
    for race in placed:
        earlier = filler.by_local.get(race.start.local.replace(tzinfo=None) - timedelta(days=7))
        if (
            earlier is not None
            and earlier.category == race.category
            and not race.featured
            and not earlier.featured
        ):
            repeats += 1

    split = {
        name: Counter({"prime": 0, "normal": 0, "quiet": 0, "featured": 0, "between": 0})
        for name in names
    }
    for race in races:
        counts = split[race["category"]]
        counts[race["band"]] += 1
        counts["featured"] += race["featured"]
        counts["between"] += race["between"]
    featured_races = Counter(race["category"] for race in races if race["featured"])
    per_date = Counter({day.isoformat(): 0 for day in dates})
    per_date.update(race["date"] for race in races if not race["between"])
    shortfall = [
        {"date": day.isoformat(), "races": per_date[day.isoformat()], "wanted": wanted[day]}
        for day in dates
        if per_date[day.isoformat()] < wanted[day]
    ]
    unbracketed = filler.unbracketed()
    notices = []
    if shortfall:
        notices.append(
            f"On {len(shortfall)} of {len(dates)} dates fewer main races fit than the day "
            "group asks for."
        )
    if filler.empty:
        notices.append(f"{filler.empty} picked hours had no category that could take them.")
    if unbracketed:
        notices.append(
            f"{unbracketed} between races lack a main race 1 to 2 hours on one side."
        )
    description = race_set.description
    if filler.between_names:
        description += " Between races come on top."
    return {
        "set": race_set.name,
        "description": description,
        "per_day": dict(race_set.per_day),
        "weekly_races": race_set.weekly,
        "between_categories": list(filler.between_names),
        "composition": [
            {
                "category": category.name,
                "sources": list(category.sources),
                "mixed": category.mixed,
                "daily_max": category.daily_max,
                "weekly_max": category.weekly_max,
                "only_prime": category.only_prime,
                "between": category.name in filler.between_set,
                "favourite_mode": None if category.mixed else category.favourite_mode.name,
                "open_bands": list(open_by_name[category.name]),
                "weights": weights[category.name],
                "modes": [
                    {
                        "name": mode.name,
                        "id": mode.id,
                        "source": mode.source,
                        "factor": mode.factor,
                        "slots": mode.slots,
                        "retired": mode.retired,
                    }
                    for mode in category.modes
                ],
            }
            for category in categories
        ],
        "bands": [
            {"group": group, "hour": hour, "band": band, "value": value}
            for (group, hour), (band, value) in sorted(
                bands.items(), key=lambda item: (DAY_GROUPS.index(item[0][0]), item[0][1])
            )
        ],
        "pairs": [
            {
                "a": a,
                "b": b,
                "score": pairs[frozenset((a, b))].score,
                "weak": pairs[frozenset((a, b))].weak,
            }
            for a, b in combinations(names, 2)
        ],
        "featured": [
            {
                "category": name,
                "weekday": weekday,
                "hour": hour,
                "between": name in filler.between_set,
                # A mixed category's featured race takes its modes in turn.
                "mode": None if by_name[name].mixed else by_name[name].favourite_mode.name,
                "mode_id": None if by_name[name].mixed else by_name[name].favourite_mode.id,
                "rotates": [mode.name for mode in by_name[name].modes]
                if by_name[name].mixed
                else [],
                "races": featured_races[name],
                "skipped": skipped.get(name, []),
            }
            for name, (weekday, hour) in featured.items()
        ],
        "races": len(races),
        "between": sum(race["between"] for race in races),
        "day_races": {
            "fewest": min(per_date.values(), default=0),
            "most": max(per_date.values(), default=0),
        },
        "days": len(dates),
        "shortfall": shortfall,
        "empty_hours": filler.empty,
        "short_gaps": filler.short_gaps(),
        "unbracketed": unbracketed,
        "expected_entrants": sum(race["expected"] for race in races) - lost,
        "expected_cancelled": sum(1 - race["fill"] for race in races),
        "event_conflicts": event_conflicts(races, events),
        "repeats": repeats,
        "categories": [
            {"category": category, "races": sum(counts[band] for band in BANDS), **counts}
            for category, counts in split.items()
        ],
        "holidays": [day.isoformat() for day in dates if model.holidays and is_holiday(day)],
        "notice": " ".join(notices) or None,
        "schedule": races,
    }


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_conflicts(races: list[JsonObject], events: list[Event]) -> int:
    """Count races that break an event: blocked times, avoided categories, and races less
    than a race length from another during a single event."""
    starts = [_utc(race["utc"]) for race in races]
    ordered = sorted(starts)
    conflicts = 0
    for race, started_at in zip(races, starts):
        # More than the race itself starts less than a race length either side.
        close = (
            bisect_left(ordered, started_at + RACE_LENGTH)
            - bisect_left(ordered, started_at - RACE_LENGTH + timedelta(microseconds=1))
            > 1
        )
        for event in events:
            if not event.touches(started_at):
                continue
            conflicts += (
                event.effect == "block"
                or (event.effect == "avoid" and race["category"] in event.categories)
                or (event.effect == "single" and close)
            )
    return conflicts


def baseline(
    slots: list[HistorySlot], schedule: list[JsonObject], model: Model, end: datetime
) -> JsonObject | None:
    """Score the last season that finished inside the window, next to what happened."""
    season_end: dict[int, datetime] = {}
    for row in schedule:
        started_at = slot_time(row)
        season_end[row["season"]] = max(season_end.get(row["season"], started_at), started_at)
    finished = [season for season, last in season_end.items() if last < end]
    seasons = {slot.season for slot in slots} & set(finished)
    if not seasons:
        return None
    season = max(seasons)
    chosen = [slot for slot in slots if slot.season == season]
    expected = cancelled = 0.0
    for slot in chosen:
        local = slot.started_at.astimezone(TIMEZONE)
        expected += model.expected(slot.category, model.mode.get(slot.mode_id, 1.0), local)
        cancelled += 1 - model.fill[slot.category][local.hour // BLOCK_HOURS]
    first = min(slot.started_at for slot in chosen).astimezone(TIMEZONE)
    last = max(slot.started_at for slot in chosen).astimezone(TIMEZONE)
    return {
        "season": season,
        "start": first.date().isoformat(),
        "end": last.date().isoformat(),
        "races": len(chosen),
        "expected_entrants": expected,
        "actual_entrants": sum(slot.entrants for slot in chosen),
        "expected_cancelled": cancelled,
        "actual_cancelled": sum(not slot.fired for slot in chosen),
    }


def success_rates(
    slots: list[HistorySlot], bands: dict, rates: dict[str, dict[str, tuple[int, int]]]
) -> list[JsonObject]:
    """Return one row per category and weekday-hour: its scheduled and fired slots and the rate.

    The rate is None when the weekday-hour was never scheduled. It is low below `LOW_RATE`.
    The band is that of the weekday's own day group at the hour, and it is closed when the
    category fired there less than NEAR_NEVER_RATE, the band rule of `open_bands`.
    """
    scheduled: Counter[tuple[str, int, int]] = Counter()
    fired_slots: Counter[tuple[str, int, int]] = Counter()
    for slot in slots:
        local = slot.started_at.astimezone(TIMEZONE)
        key = (slot.category, local.weekday(), local.hour)
        scheduled[key] += 1
        fired_slots[key] += slot.fired
    rows = []
    for category in sorted({slot.category for slot in slots}):
        for weekday in range(7):
            for hour in range(24):
                key = (category, weekday, hour)
                band = bands[WEEKDAY_GROUPS[weekday], hour][0]
                rate = fired_slots[key] / scheduled[key] if scheduled[key] else None
                rows.append(
                    {
                        "category": category,
                        "weekday": weekday,
                        "hour": hour,
                        "band": band,
                        "scheduled": scheduled[key],
                        "fired": fired_slots[key],
                        "rate": rate,
                        "low": rate is not None and rate < LOW_RATE,
                        "closed": fire_rate(rates[category][band]) < NEAR_NEVER_RATE,
                    }
                )
    return rows


def analyse(
    schedule: list[JsonObject],
    modes: list[JsonObject],
    races: list[JsonObject],
    config: JsonObject,
    *,
    start: date | None = None,
    weeks: int | None = None,
    pairs: dict[Pair, PairScore] | None = None,
    generated_at: datetime | None = None,
) -> JsonObject:
    """Learn the demand, then build one proposal per set as plain data for the reports.

    `start` and `weeks` override the plan file's season. `pairs` scores categories, not
    plan categories.
    """
    first_day, weeks, last_day = plan_season(config, schedule, start, weeks)
    slots, history_start, history_end = history_slots(schedule, modes, races)
    model = fit_model(slots)
    categories, left_out = resolve_composition(config, modes, model)
    layout = parse_bands(config.get("bands"))
    if pairs is None:
        sources = sorted({source for category in categories for source in category.sources})
        pairs = pair_scores(build_stats.included_race_contexts(races), sources)

    period_start, period_end = season_bounds(first_day, weeks, last_day)
    known = set(ARCHETYPE_CATEGORIES.values()) | set(config["composition"])
    events = expand_events(
        parse_events(config.get("events", []), known, period_start, period_end), categories
    )
    planned = category_pairs(categories, pairs)
    proposals = [
        plan_proposal(
            race_set,
            history=slots,
            model=model,
            categories=categories,
            layout=layout,
            pairs=planned,
            events=events,
            period_start=period_start,
            period_end=period_end,
            rules=SHIPPED,
        )
        for race_set in SETS
    ]

    bands = group_bands(layout, model, categories)
    rates = band_rates(slots, model, bands)
    dates = period_dates(period_start, period_end)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "generated_at": generated.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "parameters": {
            "history_days": HISTORY_DAYS,
            "shrink_slots": SHRINK_SLOTS,
            "fit_passes": FIT_PASSES,
            "day_groups": list(WEEKDAY_GROUPS),
            "holidays": list(HOLIDAY_NAMES),
            "bands": config["bands"],
            "near_never_rate": NEAR_NEVER_RATE,
            "sets": [
                {"name": race_set.name, "per_day": dict(race_set.per_day), "weekly": race_set.weekly}
                for race_set in SETS
            ],
            "race_hours": RACE_HOURS,
            "wide_gap_hours": WIDE_GAP_HOURS,
            "featured_shrink": FEATURED_SHRINK,
            "min_mode_slots": MIN_MODE_SLOTS,
            "low_rate": LOW_RATE,
        },
        "history": {
            "start": history_start.astimezone(TIMEZONE).date().isoformat(),
            "end": history_end.astimezone(TIMEZONE).date().isoformat(),
            "slots": len(slots),
            "fired": sum(slot.fired for slot in slots),
            "cancelled": sum(not slot.fired for slot in slots),
        },
        "period": {
            "start": period_start.astimezone(TIMEZONE).isoformat(timespec="minutes"),
            "end": period_end.astimezone(TIMEZONE).isoformat(timespec="minutes"),
            "weeks": weeks,
            "last_day": dates[-1].isoformat(),
            "next_season": (first_day + timedelta(weeks=weeks)).isoformat(),
            "days": len(dates),
            "today_races": len(dates) * TODAY_PER_DAY,
            "holidays": [day.isoformat() for day in dates if is_holiday(day)],
        },
        "left_out": left_out,
        "events": [
            {
                "name": event.name,
                "effect": event.effect,
                "categories": sorted(event.categories),
                "weekly": event.weekly,
                "occurrences": len(event.windows),
            }
            for event in events
        ],
        "baseline": baseline(slots, schedule, model, history_end),
        "band_rates": [
            {
                "category": category,
                "band": band,
                "fired": rates[category][band][0],
                "scheduled": rates[category][band][1],
                "rate": fire_rate(rates[category][band]),
                "closed": fire_rate(rates[category][band]) < NEAR_NEVER_RATE,
            }
            for category in sorted(rates)
            for band in BANDS
        ],
        "success_rates": success_rates(slots, bands, rates),
        "proposals": proposals,
    }


# Reports ---------------------------------------------------------------------


WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GROUP_LABELS = {"work": "Work", "casual": "Casual", "prime": "Prime"}
GROUP_DAYS = {
    "work": "Monday to Wednesday",
    "casual": "Thursday and Friday",
    "prime": "Saturday, Sunday and holidays",
}
# Shown in the lists of dates, so a long run of short dates stays one line.
_LISTED_DATES = 8


def _limits(category: JsonObject) -> list[str]:
    """Return a plan category's limits as short phrases."""
    notes = []
    if category["daily_max"] is not None:
        notes.append(f"at most {category['daily_max']} a day")
    if category["weekly_max"] is not None:
        notes.append(f"at most {category['weekly_max']} a week")
    if category["only_prime"]:
        notes.append("prime time only")
    return notes


def _mode_notes(mode: JsonObject, mixed: bool) -> list[str]:
    """Return a mode's notes: its category in a mixed plan category, retired, never run."""
    notes = [mode["source"]] if mixed else []
    if mode["retired"]:
        notes.append("retired")
    if not mode["slots"]:
        notes.append("no slots in the window")
    return notes


def _mode_label(mode: JsonObject, mixed: bool) -> str:
    notes = _mode_notes(mode, mixed)
    return mode["name"] + (f" ({', '.join(notes)})" if notes else "") + f" ×{mode['factor']:.2f}"


def _hour_ranges(hours: list[int]) -> str:
    """Return start hours as runs, such as '00:00 to 05:00, 23:00'."""
    runs: list[list[int]] = []
    for hour in sorted(hours):
        if runs and hour == runs[-1][1] + 1:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    return ", ".join(
        f"{first:02d}:00" if first == last else f"{first:02d}:00 to {last:02d}:00"
        for first, last in runs
    )


def _band_of(result: JsonObject) -> dict[tuple[str, int], str]:
    """Return each group-hour's band. It is the same in every proposal."""
    return {(row["group"], row["hour"]): row["band"] for row in result["proposals"][0]["bands"]}


def _band_hours(result: JsonObject, group: str, band: str) -> list[int]:
    bands = _band_of(result)
    return [hour for hour in range(24) if bands[group, hour] == band]


def _a_day(proposal: JsonObject) -> str:
    """Races a day in the Work, Casual and Prime groups, such as '7/8/9'."""
    return "/".join(str(proposal["per_day"][group]) for group in DAY_GROUPS)


def _per_race(proposal: JsonObject) -> float:
    return proposal["expected_entrants"] / proposal["races"] if proposal["races"] else 0.0


def _ranked(result: JsonObject) -> list[JsonObject]:
    return sorted(result["proposals"], key=lambda proposal: -_per_race(proposal))


def _dates(items: list[str]) -> str:
    """Return the first few dates of a list, with the count of the rest."""
    shown = ", ".join(items[:_LISTED_DATES])
    return shown + (f" and {len(items) - _LISTED_DATES} more" if len(items) > _LISTED_DATES else "")


def _shortfall(proposal: JsonObject) -> str:
    """Return the dates short of races, with each date's races of those wanted."""
    return _dates([f"{row['date']} ({row['races']} of {row['wanted']})" for row in proposal["shortfall"]])


def _featured_text(item: JsonObject) -> str:
    mode = item["mode"] or f"rotates: {', '.join(item['rotates'])}"
    skipped = f", skipped {_dates(item['skipped'])}" if item["skipped"] else ""
    between = ", between race" if item["between"] else ""
    return (
        f"{item['category']} {WEEKDAY_SHORT[item['weekday']]} {item['hour']:02d}:00 {mode} "
        f"({item['races']} races{between}{skipped})"
    )


def _event_text(event: JsonObject) -> str:
    listed = f" {', '.join(event['categories'])}" if event["categories"] else ""
    when = "weekly" if event["weekly"] else "dated"
    return f"Event {event['name']}: {event['effect']}{listed}, {when}, {event['occurrences']} in the period."


def _band_sentence(result: JsonObject, group: str) -> str:
    """Return one group's bands as text, such as 'prime 12:00 to 15:00; quiet ...; 13 hours normal'."""
    prime = _band_hours(result, group, "prime")
    quiet = _band_hours(result, group, "quiet")
    return (
        f"prime {_hour_ranges(prime)} ({len(prime)} hours); "
        f"quiet {_hour_ranges(quiet)} ({len(quiet)} hours); "
        f"the other {24 - len(prime) - len(quiet)} hours normal"
    )


def _a_week(proposal: JsonObject) -> float:
    """Return the between races a week, averaged over the period."""
    return proposal["between"] / proposal["days"] * 7 if proposal["days"] else 0.0


def _score_text(pair: JsonObject | None) -> str:
    """Return a pair score as '0.24', 'n/a' or '0.20 (weak evidence)'."""
    if pair is None or pair["score"] is None:
        return "n/a"
    return f"{pair['score']:.2f}" + (" (weak evidence)" if pair["weak"] else "")


def _between_scores(proposal: JsonObject) -> list[tuple[str, str, str]]:
    """Return (between category, main category, score text) for every such pair."""
    pairs = {frozenset((pair["a"], pair["b"])): pair for pair in proposal["pairs"]}
    mains = [item["category"] for item in proposal["composition"] if not item["between"]]
    return [
        (name, main, _score_text(pairs.get(frozenset((name, main)))))
        for name in proposal["between_categories"]
        for main in mains
    ]


CLASH_RULE = (
    "Two races less than 2 hours apart lose their pair score times the smaller race's "
    "expected entrants, charged to the smaller race (the later one on a tie) and capped at "
    "that race's own expected entrants. A missing score counts as 1."
)


def format_report(result: JsonObject) -> str:
    """Return the plain-text summary: the composition, the ranking, then each set in detail."""
    history = result["history"]
    period = result["period"]
    first = result["proposals"][0]
    lines = [
        f"Schedule proposals for {period['start'][:10]} to {period['last_day']} "
        f"({period['days']} days, Eastern Time), in a season of {period['weeks']} weeks. "
        f"The next season starts on {period['next_season']}.",
        f"Learned from {history['slots']:,} scheduled slots, {history['start']} to "
        f"{history['end']}: {history['fired']:,} fired, {history['cancelled']:,} cancelled.",
    ]
    lines += [_event_text(event) for event in result["events"]]
    holidays = period["holidays"]
    lines.append(
        f"Holidays in the period, which take the prime day group: {', '.join(holidays)}."
        if holidays
        else "No holidays fall in the period."
    )
    base = result["baseline"]
    if base:
        lines.append(
            f"Today, season {base['season']} ({base['start']} to {base['end']}): "
            f"{base['races']} races, {base['expected_entrants']:,.0f} expected entrants "
            f"({base['actual_entrants']:,} actual), {base['expected_cancelled']:,.0f} expected "
            f"cancelled rooms ({base['actual_cancelled']:,} actual)."
        )
    lines.append(f"Today's grid holds {period['today_races']} races in the period.")

    lines += ["", "Bands by day group, set by hand in the plan file (start hours, Eastern Time):"]
    for group in DAY_GROUPS:
        lines.append(f"  {GROUP_LABELS[group]} ({GROUP_DAYS[group]}): {_band_sentence(result, group)}.")

    lines += ["", f"Composition, the same in all {len(result['proposals'])} sets:"]
    for category in first["composition"]:
        modes = ", ".join(_mode_label(mode, category["mixed"]) for mode in category["modes"])
        limits = _limits(category) + (["between category"] if category["between"] else [])
        suffix = f" [{', '.join(limits)}]" if limits else ""
        favourite = (
            "its modes take turns"
            if category["mixed"]
            else f"favourite {category['favourite_mode']}"
        )
        weights = ", ".join(f"{band} {weight:.2f}" for band, weight in category["weights"].items())
        lines.append(
            f"  {category['category']}{suffix}: {modes}; {favourite}; "
            f"open in {', '.join(category['open_bands'])}; weights {weights}."
        )
    if result["left_out"]:
        lines.append(f"  Left out, with no modes listed: {', '.join(result['left_out'])}.")

    lines.append("")
    if first["between_categories"]:
        lines.append(
            "Between categories, whose races start only between two main races "
            "(pair score with each main category):"
        )
        for name, main, score in _between_scores(first):
            lines.append(f"  {name} + {main}: {score}")
        lines.append(f"  {CLASH_RULE}")
    else:
        lines.append("No between categories: every race is a main race.")

    lines += [
        "",
        "Ranked by expected entrants per race:",
        f"  {'set':<9}{'races':>7}{'a week':>8}{'a day W/C/P':>13}{'per race':>10}"
        f"{'entrants':>10}{'cancelled':>11}{'between':>9}{'short gaps':>12}{'shortfall':>11}{'repeats':>9}"
        f"{'conflicts':>11}",
    ]
    for rank, proposal in enumerate(_ranked(result)):
        lines.append(
            f"  {proposal['set']:<9}{proposal['races']:>7}{proposal['weekly_races']:>8}"
            f"{_a_day(proposal):>13}{_per_race(proposal):>10.2f}"
            f"{proposal['expected_entrants']:>10,.0f}{proposal['expected_cancelled']:>11,.0f}"
            f"{_a_week(proposal):>9.1f}{proposal['short_gaps']:>12}{len(proposal['shortfall']):>11}"
            f"{proposal['repeats']:>9}"
            f"{proposal['event_conflicts']:>11}{'  best' if rank == 0 else ''}"
        )

    for proposal in result["proposals"]:
        lines += [
            "",
            f"{proposal['set'].capitalize()}: {proposal['description']}",
            f"  {proposal['races']} races, {proposal['between']} of them between races "
            f"({_a_week(proposal):.1f} a week; {proposal['weekly_races']} main races a week, "
            f"{proposal['day_races']['fewest']} to {proposal['day_races']['most']} a day), "
            f"{proposal['expected_entrants']:,.0f} expected entrants "
            f"({_per_race(proposal):.2f} a race), "
            f"{proposal['expected_cancelled']:,.0f} expected cancelled rooms, "
            f"{proposal['short_gaps']} short gaps, {proposal['repeats']} weekly repeats, "
            f"{proposal['event_conflicts']} event conflicts.",
        ]
        if proposal["shortfall"]:
            lines.append(f"  Short of races on: {_shortfall(proposal)}.")
        lines.append("  Featured: " + "; ".join(_featured_text(item) for item in proposal["featured"]) + ".")
        if proposal["notice"]:
            lines.append(f"  {proposal['notice']}")
        lines.append(
            f"  {'category':<12}{'races':>7}{'prime':>7}{'normal':>8}{'quiet':>7}"
            f"{'featured':>10}{'between':>9}"
        )
        for row in proposal["categories"]:
            lines.append(
                f"  {row['category']:<12}{row['races']:>7}{row['prime']:>7}"
                f"{row['normal']:>8}{row['quiet']:>7}{row['featured']:>10}{row['between']:>9}"
            )
    return "\n".join(lines)


def write_outputs(result: JsonObject, directory: Path) -> list[Path]:
    """Write each proposal as CSV and API-shaped JSON, and the success rates as CSV.

    Returns the paths written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for proposal in result["proposals"]:
        stem = f"schedule-{proposal['set']}"
        csv_path = directory / f"{stem}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(CSV_COLUMNS)
            for race in proposal["schedule"]:
                writer.writerow(
                    [
                        race["date"],
                        race["time"],
                        race["category"],
                        race["mode"],
                        race["mode_id"],
                        race["utc"],
                    ]
                )
        written.append(csv_path)

        rows = [
            {"time": str(int(_utc(race["utc"]).timestamp())), "mode": race["mode_id"]}
            for race in proposal["schedule"]
        ]
        json_path = directory / f"{stem}.json"
        json_path.write_text(json.dumps(rows, separators=(",", ":")) + "\n", encoding="utf-8")
        written.append(json_path)

    rates_path = directory / "success-rates.csv"
    with rates_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(RATE_COLUMNS)
        for row in result["success_rates"]:
            writer.writerow(
                [
                    row["category"],
                    WEEKDAY_SHORT[row["weekday"]],
                    row["hour"],
                    row["band"],
                    row["scheduled"],
                    row["fired"],
                    "" if row["rate"] is None else f"{row['rate']:.3f}",
                    str(row["low"]).lower(),
                    str(row["closed"]).lower(),
                ]
            )
    written.append(rates_path)
    return written


_CSS = """
/* One reading column: summary first, then each proposal, then events and detail. */
:root {
  --bg: #f2f5f3; --surface: #ffffff; --fg: #17211d; --muted: #607069; --line: #cbd3cf;
  --seq: #1d4f5f; --on-seq: #ffffff;
  --low: #12634a; --moderate: #8a6200; --on-moderate: #ffffff; --high: #c84a3e;
  --display: "Barlow Condensed", "Avenir Next Condensed", "DIN Condensed", sans-serif;
  --font: "Barlow", "Avenir Next", Avenir, "Trebuchet MS", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111916; --surface: #18221e; --fg: #e3ebe7; --muted: #95a59e; --line: #2d3a35;
    --seq: #8ebccc; --on-seq: #0d1714;
    --low: #5cc49a; --moderate: #e0b04a; --on-moderate: #1a1405; --high: #f07f72;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #111916; --surface: #18221e; --fg: #e3ebe7; --muted: #95a59e; --line: #2d3a35;
  --seq: #8ebccc; --on-seq: #0d1714;
  --low: #5cc49a; --moderate: #e0b04a; --on-moderate: #1a1405; --high: #f07f72;
  color-scheme: dark;
}
body { background: var(--bg); color: var(--fg); font: 15px/1.5 var(--font); }
main {
  max-width: 1040px; margin: 0 auto; display: grid; gap: 32px; padding-block: 24px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
}
h1, h2, h3, h4 { font-family: var(--display); text-wrap: balance; }
h1 { font-size: 34px; margin: 0; line-height: 1.1; }
h2 { font-size: 21px; margin: 0 0 4px; }
h3 { font-size: 17px; margin: 8px 0 0; }
p, ul { margin: 0; max-width: 68ch; }
section { display: grid; gap: 12px; min-width: 0; }
.lede { font-size: 17px; }
.muted, .note { color: var(--muted); font-size: 13px; }
.notice { border-left: 4px solid var(--moderate); background: var(--surface); padding: 10px 14px; border-radius: 4px; }
.wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 10px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--line); }
.summary th { white-space: normal; vertical-align: bottom; }
th { color: var(--muted); font-weight: 600; }
tr:last-child td, tr:last-child th { border-bottom: 0; }
.num { text-align: right; }
.badge { border: 1px solid var(--muted); border-radius: 3px; padding: 0 4px; color: var(--muted); font-size: 11px; margin-left: 6px; }
.badge.star { border-color: var(--moderate); color: var(--moderate); }
svg { display: block; }
svg text { fill: var(--fg); font: 11px var(--font); }
svg text.on { fill: var(--on-seq); font-weight: 700; font-size: 10px; }
svg text.on-feat { fill: var(--on-moderate); font-weight: 700; font-size: 10px; }
.grid { min-width: 720px; width: 100%; max-width: 900px; }
.grid .axis { fill: var(--muted); font-size: 12px; }
.grid .letter { fill: var(--muted); font-size: 11px; font-weight: 700; }
.cell { stroke: var(--surface); stroke-width: 1; }
.run { stroke: none; }
.b-prime { fill: color-mix(in srgb, var(--low) 24%, var(--surface)); }
.b-normal { fill: var(--surface); }
.b-quiet { fill: color-mix(in srgb, var(--muted) 16%, var(--surface)); }
.std { fill: var(--seq); }
.feat { fill: var(--moderate); }
.btw { fill: var(--seq); stroke: var(--high); stroke-width: 3; }
.btw-feat { fill: var(--moderate); stroke: var(--high); stroke-width: 3; }
h4 { font-size: 15px; margin: 6px 0 0; font-weight: 600; }
.week { padding: 8px; }
.key { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; font-size: 12px; color: var(--muted); }
.key span { display: inline-flex; gap: 6px; align-items: center; }
.swatch { width: 14px; height: 14px; box-sizing: border-box; display: inline-block; }
.swatch.b-prime { background: color-mix(in srgb, var(--low) 24%, var(--surface)); border: 1px solid var(--low); }
.swatch.b-normal { background: var(--surface); border: 1px solid var(--line); }
.swatch.b-quiet { background: color-mix(in srgb, var(--muted) 16%, var(--surface)); border: 1px dashed var(--muted); }
.swatch.std { background: var(--seq); }
.swatch.feat { background: var(--moderate); }
.swatch.btw { background: var(--seq); border: 3px solid var(--high); }
.swatch.btw-feat { background: var(--moderate); border: 3px solid var(--high); }
details { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 8px 12px; }
details > section { margin-top: 12px; }
summary { cursor: pointer; font-weight: 600; }
summary:focus-visible { outline: 2px solid var(--seq); outline-offset: 2px; }
"""

_WEEKDAY_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CELL = 30


def _esc(value: Any) -> str:
    return escape(str(value))


def _name(proposal: JsonObject) -> str:
    return _esc(proposal["set"].capitalize())


def _table(head: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    """Return a scrolling table. Cells are already-escaped HTML."""
    header = "".join(
        f'<th class="num">{cell}</th>' if i in numeric else f"<th>{cell}</th>"
        for i, cell in enumerate(head)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{cell}</td>' if i in numeric else f"<td>{cell}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        f'<div class="wrap"><table><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _summary_table(result: JsonObject) -> str:
    rows = []
    base = result["baseline"]
    if base:
        rows.append(
            [
                f"Today (season {_esc(base['season'])})",
                f"{base['races']:,}",
                "n/a",
                "n/a",
                f"{base['expected_entrants']:,.0f} expected<br>{base['actual_entrants']:,} actual",
                f"{base['expected_entrants'] / base['races']:.2f}" if base["races"] else "n/a",
                f"{base['expected_cancelled']:,.0f} expected<br>{base['actual_cancelled']:,} actual",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
            ]
        )
    rows.append(
        [
            "Today&rsquo;s grid, this period",
            f"{result['period']['today_races']:,}",
            str(TODAY_PER_DAY * 7),
            "/".join([str(TODAY_PER_DAY)] * len(DAY_GROUPS)),
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
        ]
    )
    best = _ranked(result)[0]
    for proposal in result["proposals"]:
        mark = ' <span class="badge">best per race</span>' if proposal is best else ""
        rows.append(
            [
                f"<strong>{_name(proposal)}</strong>{mark}",
                f"{proposal['races']:,}",
                str(proposal["weekly_races"]),
                _a_day(proposal),
                f"{proposal['expected_entrants']:,.0f}",
                f"{_per_race(proposal):.2f}" if proposal["races"] else "n/a",
                f"{proposal['expected_cancelled']:,.0f}",
                f"{_a_week(proposal):.1f}",
                str(proposal["short_gaps"]),
                str(len(proposal["shortfall"])),
                str(proposal["repeats"]),
                str(proposal["event_conflicts"]),
            ]
        )
    head = [
        "Plan",
        "Races",
        "Main races a week",
        "A day (Work / Casual / Prime)",
        "Expected entrants",
        "Per race",
        "Cancelled rooms",
        "Between races a week",
        "Short gaps",
        "Shortfall dates",
        "Weekly repeats",
        "Event conflicts",
    ]
    # The summary's headings wrap, so all twelve columns fit a desktop page.
    return f'<div class="summary">{_table(head, rows, numeric=set(range(1, 12)))}</div>'


def _band_key(extra: str = "") -> str:
    return (
        '<div class="key">'
        '<span><i class="swatch b-prime"></i>prime time (P)</span>'
        '<span><i class="swatch b-normal"></i>normal hours</span>'
        f'<span><i class="swatch b-quiet"></i>quiet hours (Q)</span>{extra}</div>'
    )


def _band_layout(result: JsonObject) -> str:
    """Day group by hour grid: each cell is the band of that hour in that group."""
    bands = _band_of(result)
    values = {(row["group"], row["hour"]): row["value"] for row in result["proposals"][0]["bands"]}
    size, left, head = _CELL, 56, 22
    width, height = left + 24 * size + 2, head + len(DAY_GROUPS) * size + 2
    parts = [
        f'<svg class="grid" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Band by hour for each day group">'
    ]
    for hour in range(24):
        parts.append(
            f'<text class="axis" x="{left + hour * size + size / 2}" y="{head - 7}" '
            f'text-anchor="middle">{hour}</text>'
        )
    for row, group in enumerate(DAY_GROUPS):
        y = head + row * size
        parts.append(
            f'<text class="axis" x="{left - 6}" y="{y + size / 2 + 4}" '
            f'text-anchor="end">{GROUP_LABELS[group]}</text>'
        )
        for hour in range(24):
            band = bands[group, hour]
            letter = {"prime": "P", "quiet": "Q"}.get(band, "")
            title = (
                f"{GROUP_LABELS[group]} days, {hour:02d}:00 ET: {band} time, "
                f"value {values[group, hour]:.2f} expected entrants"
            )
            parts.append(
                f"<g><title>{_esc(title)}</title>"
                f'<rect class="cell b-{band}" x="{left + hour * size}" y="{y}" '
                f'width="{size}" height="{size}"/>'
                f'<text class="letter" x="{left + hour * size + size / 2}" '
                f'y="{y + size / 2 + 4}" text-anchor="middle">{letter}</text></g>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _band_section(result: JsonObject) -> str:
    plan = result["parameters"]["bands"]
    groups = "".join(
        f"<li><strong>{GROUP_LABELS[group]}</strong> ({GROUP_DAYS[group]}): "
        f"{_esc(_band_sentence(result, group))}. Plan file: prime "
        f"{_esc(plan[group]['prime'])}, quiet {_esc(plan[group]['quiet'])}.</li>"
        for group in DAY_GROUPS
    )
    return (
        "<section><h2>Bands</h2>"
        '<p class="note">The bands are set by hand in the plan file, one set of ranges per '
        "day group, and are not ranked from turnout. Each range gives Eastern Time start "
        "hours, both ends included; the hours of neither range are normal. Each cell is a "
        "start hour. Hover it for the model&rsquo;s value of that hour, the mean expected "
        "entrants over the plan categories, to see whether a band still fits the demand."
        f'</p><ul class="note">{groups}</ul>'
        f'{_band_key()}<div class="wrap" style="padding:8px">{_band_layout(result)}</div></section>'
    )


def _composition_section(result: JsonObject) -> str:
    """The plan categories, which every set shares."""
    rows = []
    for category in result["proposals"][0]["composition"]:
        name = _esc(category["category"])
        if category["mixed"]:
            name += '<span class="badge">mixed</span>'
        if category["between"]:
            name += '<span class="badge star">between category</span>'
        modes = []
        for mode in category["modes"]:
            text = f"{_esc(mode['name'])} &times;{mode['factor']:.2f}"
            if mode["name"] == category["favourite_mode"]:
                text += '<span class="badge star">&#9733; favourite</span>'
            for note in _mode_notes(mode, category["mixed"]):
                text += f'<span class="badge">{_esc(note)}</span>'
            modes.append(text)
        if category["mixed"]:
            modes.append('<span class="badge star">&#9733; modes take turns</span>')
        weights = "<br>".join(
            f"{_esc(band)} {weight:.2f}" for band, weight in category["weights"].items()
        )
        rows.append(
            [
                name,
                "<br>".join(modes),
                _esc(", ".join(_limits(category)) or "none"),
                _esc(", ".join(category["open_bands"])),
                weights,
            ]
        )
    head = ["Category", "Modes and turnout factor", "Limits", "Open bands", "Weight by band"]
    left_out = (
        f'<p class="note">Left out, with no modes listed: {_esc(", ".join(result["left_out"]))}.</p>'
        if result["left_out"]
        else ""
    )
    near_never = result["parameters"]["near_never_rate"]
    return (
        "<section><h2>Composition</h2>"
        '<p class="note">Every set runs exactly these categories and modes, from the plan '
        "file. A category races only in its open bands: those where its rooms fired at least "
        f"{near_never:.0%} of the time (every source, for a mixed category). A weight is the "
        "category&rsquo;s mean expected entrants in the band, and shares a band between "
        f"categories.</p>{_table(head, rows)}{left_out}</section>"
    )


def _between_section(result: JsonObject) -> str:
    """The between categories: their races in each set, and their pair score with each main one."""
    first = result["proposals"][0]
    if not first["between_categories"]:
        body = '<p class="muted">No plan category is a between category: every race is a main race.</p>'
    else:
        scores: dict[str, list[str]] = defaultdict(list)
        for name, main, score in _between_scores(first):
            scores[name].append(f"{_esc(main)} {_esc(score)}")
        rows = []
        for name in first["between_categories"]:
            races = [
                next(row["between"] for row in proposal["categories"] if row["category"] == name)
                for proposal in result["proposals"]
            ]
            rows.append([_esc(name), *map(str, races), "<br>".join(scores[name])])
        head = ["Between category"] + [f"{_name(p)} races" for p in result["proposals"]]
        head.append("Pair score with each main category")
        body = _table(head, rows, set(range(1, len(result["proposals"]) + 1)))
    return (
        "<section><h2>Between categories</h2>"
        '<p class="note">A between category shares few racers with the main ones, so its '
        "races start only in a gap between two main races: 1 or 2 hours after one and 1 or 2 "
        "hours before the next, one race to a gap. They come on top of a set&rsquo;s races a "
        f"day. {_esc(CLASH_RULE)} A lower score costs fewer entrants.</p>{body}</section>"
    )


def _week_grid(
    result: JsonObject,
    proposal: JsonObject,
    days: list[date],
    label: str,
) -> str:
    """Date by hour grid of consecutive dates: band shading, and each race in its hour.

    The shading is one rectangle per run of equal bands, and only races carry hover
    text, so a whole season stays a small page.
    """
    bands = _band_of(result)
    holidays = set(proposal["holidays"])
    by_start = {(race["date"], race["hour"]): race for race in proposal["schedule"]}
    size, left, head = _CELL, 100, 22
    width, height = left + 24 * size + 2, head + len(days) * size + 2
    parts = [
        f'<svg class="grid" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_esc(label)}, {_esc(proposal["set"])}">'
    ]
    for hour in range(24):
        parts.append(
            f'<text class="axis" x="{left + hour * size + size / 2}" y="{head - 7}" '
            f'text-anchor="middle">{hour}</text>'
        )
    for row, day in enumerate(days):
        iso = day.isoformat()
        holiday = iso in holidays
        group = "prime" if holiday else WEEKDAY_GROUPS[day.weekday()]
        y = head + row * size
        name = f"{WEEKDAY_SHORT[day.weekday()]} {day:%m-%d}" + (" ★" if holiday else "")
        parts.append(
            f'<text class="axis" x="{left - 6}" y="{y + size / 2 + 4}" text-anchor="end">'
            f"{_esc(name)}</text>"
        )
        hour = 0
        while hour < 24:
            band = bands[group, hour]
            end = hour
            while end + 1 < 24 and bands[group, end + 1] == band:
                end += 1
            parts.append(
                f'<rect class="run b-{band}" x="{left + hour * size}" y="{y}" '
                f'width="{(end - hour + 1) * size}" height="{size}"/>'
            )
            hour = end + 1
        for hour in range(24):
            race = by_start.get((iso, hour))
            if race is None:
                continue
            x = left + hour * size
            title = (
                f"{iso}{' (holiday)' if holiday else ''} {hour:02d}:00 ET, "
                f"{bands[group, hour]} time: {race['category']}, {race['mode']}"
            )
            if race["featured"]:
                title += ", featured"
            if race["between"]:
                title += ", between race"
            if race["between"]:
                kind = "btw-feat" if race["featured"] else "btw"
            else:
                kind = "feat" if race["featured"] else "std"
            text = "on-feat" if race["featured"] else "on"
            parts.append(
                f"<g><title>{_esc(title)}</title>"
                f'<rect class="{kind}" x="{x + 3}" y="{y + 3}" '
                f'width="{size - 6}" height="{size - 6}" rx="3"/>'
                f'<text class="{text}" x="{x + size / 2}" y="{y + size / 2 + 4}" '
                f'text-anchor="middle">{_esc(race["category"][:3])}</text></g>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _weeks(result: JsonObject) -> list[list[date]]:
    """Return the period's dates in Monday to Sunday weeks, the first and last maybe partial."""
    first = date.fromisoformat(result["period"]["start"][:10])
    weeks: list[list[date]] = []
    for offset in range(result["period"]["days"]):
        day = first + timedelta(days=offset)
        if not weeks or day.weekday() == 0:
            weeks.append([])
        weeks[-1].append(day)
    return weeks


def _full_schedule(result: JsonObject, proposal: JsonObject) -> str:
    """Every date of the period, one grid under a heading for each Monday to Sunday week."""
    blocks = []
    for number, days in enumerate(_weeks(result), 1):
        label = f"Week {number}: {days[0]:%a %Y-%m-%d} to {days[-1]:%a %Y-%m-%d}"
        blocks.append(
            f"<h4>{_esc(label)}</h4>"
            f'<div class="wrap week">{_week_grid(result, proposal, days, f"Week {number}")}</div>'
        )
    return "".join(blocks)


_SCHEDULE_KEY = _band_key(
    '<span><i class="swatch std"></i>race (first letters of its category)</span>'
    '<span><i class="swatch feat"></i>featured race</span>'
    '<span><i class="swatch btw"></i>between race</span>'
    '<span><i class="swatch btw-feat"></i>featured between race</span>'
    "<span>&#9733; holiday</span>"
)


def _featured_table(proposal: JsonObject) -> str:
    rows = [
        [
            _esc(item["category"]) + ('<span class="badge">between race</span>' if item["between"] else ""),
            _WEEKDAY_LONG[item["weekday"]],
            f"{item['hour']:02d}:00",
            _esc(item["mode"] or f"rotates: {', '.join(item['rotates'])}"),
            str(item["races"]),
            _esc(", ".join(item["skipped"]) or "none"),
        ]
        for item in proposal["featured"]
    ]
    head = ["Category", "Weekday", "Hour (ET)", "Mode", "Races", "Skipped dates"]
    return _table(head, rows, {4})


def _categories_table(proposal: JsonObject) -> str:
    rows = [
        [
            _esc(item["category"]),
            str(item["races"]),
            str(item["prime"]),
            str(item["normal"]),
            str(item["quiet"]),
            str(item["featured"]),
            str(item["between"]),
        ]
        for item in proposal["categories"]
    ]
    head = ["Category", "Races", "Prime", "Normal", "Quiet", "Featured", "Between"]
    return _table(head, rows, set(range(1, 7)))


def _proposal_section(result: JsonObject, proposal: JsonObject, is_first: bool) -> str:
    notice = (
        f'<p class="notice"><strong>Notice.</strong> {_esc(proposal["notice"])}</p>'
        if proposal["notice"]
        else ""
    )
    short = (
        f'<p class="notice"><strong>Short of races on:</strong> {_esc(_shortfall(proposal))}.</p>'
        if proposal["shortfall"]
        else ""
    )
    return (
        f"<details{' open' if is_first else ''}>"
        f"<summary>{_name(proposal)}: {proposal['weekly_races']} main races a week, "
        f"{proposal['races']:,} races in the period</summary>"
        f'<section><p class="note">{_esc(proposal["description"])}</p>{notice}{short}'
        "<h3>Full schedule</h3>"
        '<p class="note">Every date of the period by start hour, Eastern Time, one grid for '
        "each Monday to Sunday week. Cells are shaded by the band of that date&rsquo;s day "
        "group, and a holiday takes the Prime group&rsquo;s bands. Hover a race for its date, "
        f"hour, band, category and mode.</p>{_SCHEDULE_KEY}{_full_schedule(result, proposal)}"
        "<h3>Featured races</h3>"
        f"{_featured_table(proposal)}"
        "<h3>Categories</h3>"
        f"{_categories_table(proposal)}</section></details>"
    )


def _proposal_sections(result: JsonObject) -> str:
    blocks = "".join(
        _proposal_section(result, proposal, index == 0)
        for index, proposal in enumerate(result["proposals"])
    )
    return f"<section><h2>The four proposals</h2>{blocks}</section>"


def _event_section(result: JsonObject) -> str:
    rows = [
        [
            _esc(event["name"]),
            _esc(event["effect"]),
            "every week" if event["weekly"] else "one date",
            _esc(", ".join(event["categories"]) or "all"),
            str(event["occurrences"]),
        ]
        for event in result["events"]
    ]
    body = (
        _table(["Event", "Effect", "When", "Categories", "In the period"], rows, {4})
        if rows
        else '<p class="muted">No external events in the plan.</p>'
    )
    return f"<section><h2>Events</h2>{body}</section>"


def _holiday_section(result: JsonObject) -> str:
    dates = [date.fromisoformat(day) for day in result["period"]["holidays"]]
    listed = ", ".join(f"{WEEKDAY_SHORT[day.weekday()]} {day.isoformat()}" for day in dates)
    names = ", ".join(result["parameters"]["holidays"])
    body = (
        f"{_esc(listed)} count as Prime days."
        if dates
        else "No holidays fall in the period."
    )
    return (
        f"<section><h2>Holidays</h2><p>{body}</p>"
        f'<p class="note">The planner treats these as holidays: {_esc(names)}. A holiday '
        "takes the Prime day group, with its bands and its races a day. Featured races keep "
        "their weekday and hour.</p></section>"
    )


def _method_note(result: JsonObject) -> str:
    p = result["parameters"]
    sets = "; ".join(
        f"{race_set['name'].capitalize()} "
        + ", ".join(str(race_set["per_day"][group]) for group in DAY_GROUPS)
        + f" ({race_set['weekly']} a week)"
        for race_set in p["sets"]
    )
    groups = " ".join(
        f"{GROUP_LABELS[group]}: {_esc(_band_sentence(result, group))}." for group in DAY_GROUPS
    )
    return (
        "<p>Every slot the alttpr.racing schedule ever held is joined to the race it became. "
        "A slot that never became an archived race was cancelled (rooms with fewer than two "
        "entrants are never archived), so it counts as zero entrants. Over the last "
        f"{p['history_days']} days, a race&rsquo;s expected turnout is its category&rsquo;s "
        "average in that four-hour part of the Eastern Time day, adjusted for the exact hour "
        "of the date&rsquo;s day group and for the mode.</p>"
        "<p>Days fall into three groups: Work (Monday to Wednesday), Casual (Thursday and "
        "Friday) and Prime (Saturday, Sunday and the common US days off: "
        f"{_esc(', '.join(p['holidays']))}). The plan file sets each group&rsquo;s prime "
        "time and quiet hours by hand; the other hours are normal. "
        f"{groups}</p>"
        "<p>The plan file lists the categories and modes; the planner picks none of its own. "
        "Between categories race only in a gap between two main races, on top of the main "
        "races. Each set runs a fixed number of main races a day in the Work, Casual and "
        f"Prime groups: {_esc(sets)}.</p>"
        "<p>Each category first gets a featured race in prime time, on a weekday of its own, "
        "at the same hour every week, picked from its own record at that hour. Each date "
        f"then takes its main races at least {p['race_hours']} hours apart, at the hours "
        "that hold the most prime races, then the most normal ones. Outside prime time "
        f"{p['wide_gap_hours']} hours apart is preferred, so a race goes to a quiet hour "
        "before a normal-hour gap shrinks to 2 hours. A short gap is two consecutive main "
        f"races less than {p['wide_gap_hours']} hours apart, unless both start in prime time. "
        "A lookahead keeps a date from ending so late that the next date cannot reach its "
        "races a day.</p>"
        "<p>Main prime-time races are given out first, then normal, then quiet. Each race goes "
        "to the main category furthest below its share of that band, and a band is shared by "
        "expected turnout. A race avoids the category that ran at the same hour a week "
        "earlier, then the one that ran a day earlier, and never breaks a daily or weekly "
        "limit or an event&rsquo;s rule. A between category races whenever it is behind its "
        "own share of a band, in a gap 1 or 2 hours after one main race and 1 or 2 hours "
        "before the next, one race to a gap.</p>"
        f"<p>A category never races in a band where its rooms fired less than "
        f"{p['near_never_rate']:.0%} of the time. Its prime-time races run its favourite "
        "mode, and its other races take its modes in turn.</p>"
        "<p><strong>Limits.</strong> Expected turnout is an average of the past year, not a "
        "forecast of growth or decline. Cancelled slots are inferred from missing archived "
        "races. The bands are set by hand, not learned: the page shows the model&rsquo;s "
        "value of each hour next to them, so a band that no longer fits the demand can be "
        "seen. The demand model cannot see that a category&rsquo;s races draw fewer racers "
        "the more of them run on one date, so the strongest categories take the most races. "
        "A band can be closed to a category by bad luck when it has few slots there. Holiday "
        "factors come almost entirely from weekends. Overlap scores are an upper bound on "
        "conflict between categories, and the ladder has rarely run races an hour apart. "
        "Spoiler&rsquo;s overlap scores are weak evidence and high, so its between races "
        "score low even though the plan file treats it as a category that shares few racers. "
        "A mode with no recent slots counts at its category&rsquo;s average.</p>"
    )


def render_html(result: JsonObject) -> str:
    """Return the self-contained HTML report: no scripts, no external requests.

    The page follows the claude.ai Artifact page contract: publishing wraps it
    in the document skeleton, so it starts at its own <title> and <style>.
    """
    history = result["history"]
    period = result["period"]
    weekly = [race_set["weekly"] for race_set in result["parameters"]["sets"]]
    fewest, most = min(weekly), max(weekly)
    return f"""<title>Schedule Proposals</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{_CSS}</style>
<main>
<header>
<h1>Schedule Proposals</h1>
<p class="lede">Four ladder schedules for {period['days']} days, from {fewest} to {most}
races a week plus the races that start between them, built from how many racers came to
every past slot.</p>
<p class="muted">Period {_esc(period['start'][:10])} to {_esc(period['last_day'])}
({period['days']} days, Eastern Time), in a season of {period['weeks']} weeks; the next
season starts on {_esc(period['next_season'])}. Learned from {history['slots']:,} scheduled slots,
{_esc(history['start'])} to {_esc(history['end'])}: {history['fired']:,} fired,
{history['cancelled']:,} cancelled. Generated {_esc(result['generated_at'][:10])}.</p>
</header>
<section><h2>Summary</h2>
<p class="note">Today&rsquo;s grid would run {period['today_races']:,} races in the period.
Expected entrants count each race&rsquo;s turnout from the history.</p>
{_summary_table(result)}</section>
{_band_section(result)}
{_composition_section(result)}
{_between_section(result)}
{_proposal_sections(result)}
{_event_section(result)}
{_holiday_section(result)}
<section><h2>Method and limits</h2>{_method_note(result)}</section>
</main>
"""


_RATES_CSS = """
:root { --low-rate: #fbe3df; --on-low-rate: #8f2a20; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --low-rate: #4a2420; --on-low-rate: #ffb4a9; }
}
:root[data-theme="dark"] { --low-rate: #4a2420; --on-low-rate: #ffb4a9; }
/* 24 hour columns fit the 1040px reading column; a phone scrolls the grid in its box. */
.rates th, .rates td { padding: 3px 2px; text-align: center; }
.rates th:first-child { text-align: left; padding-left: 8px; }
.rates td { min-width: 28px; border-left: 1px solid var(--line); font-size: 12px; }
.rates td.r-low { background: var(--low-rate); color: var(--on-low-rate); font-weight: 700; outline: 2px solid var(--on-low-rate); outline-offset: -2px; }
.rates td.r-closed { background-image: repeating-linear-gradient(135deg, transparent 0 4px, var(--line) 4px 5px); color: var(--muted); }
.rates td.r-closed.r-low { color: var(--on-low-rate); }
.rates .x { margin-left: 2px; font-weight: 700; }
.swatch.r-low { background: var(--low-rate); border: 2px solid var(--on-low-rate); }
.swatch.r-closed { background: var(--surface) repeating-linear-gradient(135deg, transparent 0 4px, var(--muted) 4px 5px); border: 1px solid var(--line); }
"""


def _rate_cell(row: JsonObject) -> str:
    """Return one grid cell: the whole-percent rate, marked low and closed, blank if untried."""
    classes = [name for name, flag in (("r-low", row["low"]), ("r-closed", row["closed"])) if flag]
    attribute = f' class="{" ".join(classes)}"' if classes else ""
    if row["rate"] is None:
        return f"<td{attribute}></td>"
    title = f"{row['fired']} fired of {row['scheduled']} scheduled"
    marker = '<span class="x">&times;</span>' if row["closed"] else ""
    return f'<td{attribute} title="{_esc(title)}">{row["rate"] * 100:.0f}{marker}</td>'


def _band_rate_table(rows: list[JsonObject]) -> str:
    """Return one category's fired and scheduled slots and fire rate in each band."""
    body = [
        [
            _esc(row["band"]),
            f"{row['fired']:,}",
            f"{row['scheduled']:,}",
            f"{row['rate']:.0%}" if row["scheduled"] else "n/a",
            '<span class="badge">closed</span>' if row["closed"] else "open",
        ]
        for row in rows
    ]
    return _table(["Band", "Fired", "Scheduled", "Rate", "Status"], body, {1, 2, 3})


def _rate_grid(category: str, rows: list[JsonObject], band_rows: list[JsonObject]) -> str:
    """Return the weekday-by-hour table of one category, and its band rates."""
    head = "<th></th>" + "".join(f"<th>{hour}</th>" for hour in range(24))
    body = "".join(
        f"<tr><th>{WEEKDAY_SHORT[weekday]}</th>"
        + "".join(_rate_cell(row) for row in rows if row["weekday"] == weekday)
        + "</tr>"
        for weekday in range(7)
    )
    scheduled = sum(row["scheduled"] for row in rows)
    fired = sum(row["fired"] for row in rows)
    return (
        f"<section><h2>{_esc(category)}</h2>"
        f'<p class="note">{fired:,} fired of {scheduled:,} scheduled, {fired / scheduled:.0%}.</p>'
        f'<div class="wrap"><table class="rates"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
        f"{_band_rate_table(band_rows)}</section>"
    )


def render_rates_html(result: JsonObject) -> str:
    """Return the success-rate page: a weekday-by-hour grid per category, same page contract."""
    by_category: dict[str, list[JsonObject]] = defaultdict(list)
    for row in result["success_rates"]:
        by_category[row["category"]].append(row)
    by_band: dict[str, list[JsonObject]] = defaultdict(list)
    for row in result["band_rates"]:
        by_band[row["category"]].append(row)

    def share(category: str) -> float:
        rows = by_category[category]
        return sum(row["fired"] for row in rows) / max(1, sum(row["scheduled"] for row in rows))

    ordered = sorted(by_category, key=lambda category: (-share(category), category))
    history = result["history"]
    low = result["parameters"]["low_rate"]
    near_never = result["parameters"]["near_never_rate"]
    grids = "".join(
        _rate_grid(category, by_category[category], by_band[category]) for category in ordered
    )
    return f"""<title>Category Success Rates</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{_CSS}{_RATES_CSS}</style>
<main>
<header>
<h1>Category Success Rates</h1>
<p class="lede">For each category, weekday and hour: how often a scheduled slot fired.</p>
<p class="muted">History window {_esc(history['start'])} to {_esc(history['end'])}, Eastern
Time. Generated {_esc(result['generated_at'][:10])}. Categories are ordered by their overall
fired share, highest first, and every category with slots in the window has a grid.</p>
</header>
<section><h2>Legend</h2>
<div class="key">
<span><span class="swatch r-low"></span>low: a rate below {low:.0%}, shown bold and outlined</span>
<span><span class="swatch r-closed"></span>&times; closed: the category fired less than
{near_never:.0%} of its slots in this hour&rsquo;s band, so the planner will not use the band</span>
<span>blank: never scheduled</span>
</div>
<p class="note">A cell shows the rate as a whole percentage; hover it for the fired and
scheduled counts. The table under each grid gives the category&rsquo;s fire rate in each
band, prime, normal and quiet, as the planner counts it. A mixed plan category, such as
throwback, has no grid of its own, because its races count under their own categories.</p></section>
{grids}
</main>
"""


# Command line ----------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--archive",
        type=Path,
        default=race_archive.ARCHIVE_DIR,
        help=f"archive directory (default: {race_archive.ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"composition and events (default: {CONFIG_PATH.name})",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="first day, YYYY-MM-DD (default: the plan file's season start, "
        "else the first day after the mirrored schedule)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=None,
        help=f"season length in weeks, pause included (default: the plan file's, "
        f"else {DEFAULT_WEEKS})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write each proposal as CSV and JSON, and the success rates as CSV, to this directory",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="also write the self-contained HTML report to this path",
    )
    parser.add_argument(
        "--rates",
        type=Path,
        default=None,
        help="also write the success-rate page to this path",
    )
    args = parser.parse_args()
    if args.weeks is not None and args.weeks < 1:
        parser.error("--weeks must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = analyse(
            schedule_archive.load_schedule(args.archive),
            schedule_archive.load_modes(args.archive),
            [envelope["race"] for envelope in race_archive.load_races(args.archive)],
            load_config(args.config),
            start=args.start,
            weeks=args.weeks,
        )
    except (
        ScheduleError,
        race_archive.ArchiveError,
        participant_stats.ParticipantDataError,
    ) as error:
        print(f"Schedule planning failed: {error}", file=sys.stderr)
        return 1

    print(format_report(result))
    if args.out is not None:
        for path in write_outputs(result, args.out):
            print(f"Wrote {path}")
    if args.html is not None:
        args.html.write_text(render_html(result), encoding="utf-8")
        print(f"Wrote {args.html}")
    if args.rates is not None:
        args.rates.write_text(render_rates_html(result), encoding="utf-8")
        print(f"Wrote {args.rates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
