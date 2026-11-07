"""Shared fixtures for the schedule planner, report, rates and lab tests.

Not a test module: unittest discovery skips it, and the test modules import it.
"""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from scripts import schedule_planner as sp

EASTERN = ZoneInfo("America/New_York")
GENERATED_AT = datetime(2026, 11, 3, tzinfo=UTC)
NEWEST = datetime(2026, 6, 1, 12, tzinfo=UTC)

CATEGORIES = ("simple", "entrance", "keys", "mystery")
MODES = [
    {"id": 11, "archetype": 1, "name": "Simple Open", "slug": "so", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 12, "archetype": 1, "name": "Simple Old", "slug": "sold", "active": False, "grabbag": False, "schedule_weight": 1},
    {"id": 13, "archetype": 1, "name": "Simple Open", "slug": "so2", "active": False, "grabbag": False, "schedule_weight": 1},
    {"id": 21, "archetype": 2, "name": "Ent A", "slug": "ea", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 22, "archetype": 2, "name": "Ent B", "slug": "eb", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 41, "archetype": 4, "name": "Keys A", "slug": "ka", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 42, "archetype": 4, "name": "Keys B", "slug": "kb", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 61, "archetype": 6, "name": "Fun One", "slug": "f1", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 62, "archetype": 6, "name": "Fun Two", "slug": "f2", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 71, "archetype": 7, "name": "Myst A", "slug": "ma", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 72, "archetype": 7, "name": "Myst B", "slug": "mb", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 15, "archetype": 1, "name": "<i>Chaos&Co", "slug": "chaos", "active": True, "grabbag": False, "schedule_weight": 1},
    {"id": 80, "archetype": 8, "name": "Odd", "slug": "odd", "active": True, "grabbag": False, "schedule_weight": 1},
]


def race(number, category, started_at, entrants=5, *, seeded=True):
    info = f"Step Ladder Series - [{category}] - Open"
    if seeded:
        info += f"\nopen - https://alttpr.racing/getseed.php?race={number} - (x)"
    return {
        "name": f"alttpr-ladder/r-{number}",
        "status": {"value": "finished"},
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entrants_count": entrants,
        "info": info,
        "entrants": [],
    }


def slot(number, race_id, started_at, mode, season=1):
    return {
        "id": number,
        "raceId": race_id,
        "season": season,
        "time": str(int(started_at.timestamp())),
        "mode": mode,
    }


def newest_race():
    return race(9000, "simple", NEWEST)


def history(entries):
    """entries: (category, mode_id, local datetime, entrants, fired)."""
    return [
        sp.HistorySlot(local.astimezone(UTC), 1, category, mode_id, entrants, fired)
        for category, mode_id, local, entrants, fired in entries
    ]


_WORLD = None
# Each category does best at midday on its own weekday.
FEATURED_DAY = {"simple": 1, "entrance": 2, "keys": 3, "mystery": 4}
WORLD_HOURS = 120 * 24


def fired_slot(i):
    """In the small hours about one slot in three is a room that never filled; the rest fire."""
    local = (datetime(2026, 3, 1, 12, tzinfo=UTC) + timedelta(hours=i)).astimezone(EASTERN)
    return local.hour >= 8 or (i * 37 // 5 + i // 24) % 3 != 0


def world():
    """Four months of hourly slots in four categories, busier in the evening and at weekends.

    The slots run from 2026-03-01 to 2026-06-29, so Memorial Day (2026-05-25) is a holiday.
    Simple ran its retired mode 12 for the first forty days, then its active mode 11.
    Rooms only ever fail to fill in the small hours, so the evening bands stay regular.
    """
    global _WORLD
    if _WORLD is not None:
        return _WORLD
    begin = datetime(2026, 3, 1, 12, tzinfo=UTC)
    schedule, races = [], []
    for i in range(WORLD_HOURS):
        at = begin + timedelta(hours=i)
        local = at.astimezone(EASTERN)
        index = (i + i // 24) % 4
        category = CATEGORIES[index]
        mode_id = {"simple": 12 if i < 40 * 24 else 11, "entrance": 21 + (i // 4) % 2, "keys": 41, "mystery": 71}[category]
        entrants = (
            [3, 5, 4, 2][index]
            + (6 if 18 <= local.hour else 2 if 8 <= local.hour < 18 else 0)
            + (1 if local.weekday() >= 5 else 0)
            + (2 if mode_id == 22 else 0)
            + (8 if local.hour in (12, 13, 14) and local.weekday() == FEATURED_DAY[category] else 0)
        )
        schedule.append(slot(i + 1, i + 1, at, mode_id, season=1 + i // (WORLD_HOURS // 2)))
        if fired_slot(i):
            races.append(race(i + 1, category, at + timedelta(minutes=2), entrants))
    _WORLD = (schedule, MODES, races)
    return _WORLD


PAIRS = {
    frozenset(("simple", "entrance")): sp.PairScore(0.1, False),
    frozenset(("keys", "mystery")): sp.PairScore(0.1, False),
    frozenset(("simple", "keys")): sp.PairScore(0.15, False),
    frozenset(("simple", "mystery")): sp.PairScore(0.24, False),
    frozenset(("entrance", "keys")): sp.PairScore(0.22, False),
    frozenset(("entrance", "mystery")): sp.PairScore(0.2, True),
}
# The world's bands, close to how its own turnout ranks the hours: evenings are prime.
WORLD_BANDS = {
    "work": {"prime": "18-23", "quiet": "1-6"},
    "casual": {"prime": "18-23", "quiet": "2-7"},
    "prime": {"prime": "16-23", "quiet": "0-5"},
}
WORLD_LAYOUT = sp.parse_bands(WORLD_BANDS)

# The plan file's composition, by alttpr.racing mode names.
BASE_COMPOSITION = {
    "simple": {"modes": ["Simple Old"]},
    "entrance": {"modes": ["Ent A", "Ent B"]},
    "keys": {"modes": ["Keys A"]},
    "mystery": {"modes": ["Myst A"]},
}
# Throwback mixes keys and mystery modes, so its races count under both.
MIXED_COMPOSITION = {
    "simple": {"modes": ["Simple Old"]},
    "entrance": {"modes": ["Ent A", "Ent B"]},
    "throwback": {"modes": ["Keys A", "Myst A"]},
}
# Daily and weekly caps, and a plan category that lists no modes.
LIMIT_COMPOSITION = {
    "simple": {"modes": ["Simple Old"], "dailyMax": 3},
    "entrance": {"modes": ["Ent A", "Ent B"], "dailyMax": 4},
    "keys": {"modes": ["Keys A"], "weeklyMax": 5},
    "mystery": {"modes": ["Myst A"], "dailyMax": 3},
    "<b>fun</b>": {"modes": []},
}
# Mystery starts only between two main races.
BETWEEN_COMPOSITION = {
    **BASE_COMPOSITION,
    "mystery": {"modes": ["Myst A"], "between": True},
}
# Simple and entrance should race at every hour of their open bands.
EVERY_HOUR_COMPOSITION = {
    **BASE_COMPOSITION,
    "simple": {"modes": ["Simple Old"], "everyHour": True},
    "entrance": {"modes": ["Ent A", "Ent B"], "everyHour": True},
}
# Plan names that need escaping on a page.
MARKUP_COMPOSITION = {
    **BASE_COMPOSITION,
    "simple": {"modes": ["Simple Old", "<i>Chaos&Co"]},
}
EVENTS = [
    {"name": "Weekly Entrance", "weekly": "monday", "start": "01:00", "timezone": "UTC",
     "hours": 3, "effect": "avoid", "categories": ["entrance"]},
    {"name": "Tournament", "date": "2026-09-10", "start": "14:00",
     "timezone": "America/New_York", "hours": 5, "effect": "block"},
    {"name": "Fridays", "weekly": "friday", "start": "00:00",
     "timezone": "America/New_York", "hours": 24, "effect": "single"},
]
MARKUP_EVENTS = [
    {"name": "<img src=x>", "weekly": "friday", "start": "00:00",
     "timezone": "UTC", "hours": 2, "effect": "single"},
]
# September 2026: Labor Day (2026-09-07) is a holiday.
START = date(2026, 9, 1)
# Five weeks from Tuesday 1 September, racing until the last Wednesday: all of September.
SEASON = {"weeks": 5, "lastDay": "wednesday"}


def period(start=START):
    """The fixture season's UTC bounds from a start date."""
    return sp.season_bounds(start, SEASON["weeks"], sp.WEEKDAYS.index(SEASON["lastDay"]))


PERIOD = period()
TOURNAMENT = "2026-09-10"


def run(composition=None, *, pairs=PAIRS, events=(), bands=None, **kwargs):
    config = {
        "bands": bands or WORLD_BANDS,
        "composition": composition or BASE_COMPOSITION,
        "events": list(events),
        "season": SEASON,
    }
    schedule, modes, races = world()
    return sp.analyse(
        schedule, modes, races, config, start=kwargs.pop("start", START),
        pairs=pairs, generated_at=GENERATED_AT, **kwargs,
    )


def fitted():
    schedule, modes, races = world()
    slots, _s, _e = sp.history_slots(schedule, modes, races)
    return sp.fit_model(slots)


def world_history():
    schedule, modes, races = world()
    return sp.history_slots(schedule, modes, races)[0]


def categories(composition=None):
    chosen, _left = sp.resolve_composition(
        {"composition": composition or BASE_COMPOSITION}, MODES, fitted()
    )
    return chosen


_RUNS = {}


def cached(key, composition=None, **kwargs):
    """Run analyse once per key, so tests can share a result."""
    if key not in _RUNS:
        _RUNS[key] = run(composition, **kwargs)
    return _RUNS[key]


def main_result():
    return cached("main", BASE_COMPOSITION, events=EVENTS)


def mixed_result():
    return cached("mixed", MIXED_COMPOSITION, events=EVENTS)


def limit_result():
    return cached("limits", LIMIT_COMPOSITION, events=EVENTS)


def between_result():
    return cached("between", BETWEEN_COMPOSITION, events=EVENTS)


def every_hour_result():
    return cached("every-hour", EVERY_HOUR_COMPOSITION, events=EVENTS)


def proposal(result, name):
    return next(p for p in result["proposals"] if p["set"] == name)


def race_set(name):
    return next(s for s in sp.SETS if s.name == name)


def local_of(race):
    return datetime.fromisoformat(race["utc"].replace("Z", "+00:00")).astimezone(EASTERN)


def rules_inputs(composition=None):
    """What `analyse` hands `plan_proposal` for the synthetic world, without events."""
    slots = world_history()
    model = sp.fit_model(slots)
    chosen = categories(composition)
    period_start, period_end = PERIOD
    return dict(
        history=slots, model=model, categories=chosen, layout=WORLD_LAYOUT,
        pairs=sp.category_pairs(chosen, PAIRS), events=[],
        period_start=period_start, period_end=period_end,
    )


def planned_with(rules, set_name="light", composition=None):
    return sp.plan_proposal(race_set(set_name), rules=rules, **rules_inputs(composition))


def flat_model(names, blocks=None, *, holidays=True):
    """A model whose expected entrants are the block factor alone, the same at every hour."""
    blocks = blocks or {}
    return sp.Model(
        block={name: [blocks.get(name, 1.0)] * 6 for name in names},
        hour={group: [1.0] * 24 for group in sp.DAY_GROUPS},
        mode={},
        mode_slots=Counter(),
        fill={name: [0.5] * 6 for name in names},
        holidays=holidays,
    )


def plain_category(name, factor=1.0, *, source=None, slots=100, **limits):
    """A plan category with one mode, counted under `source` (default: its own name)."""
    return sp.Category(
        name, (sp.Mode(f"{name} mode", 1, factor, source or name, slots),), **limits
    )


def mixed_category(name, *parts, **limits):
    """A plan category with a mode per (source, factor, slots) part, in plan order."""
    modes = tuple(
        sp.Mode(f"{name} {index}", index, factor, source, slots)
        for index, (source, factor, slots) in enumerate(parts)
    )
    return sp.Category(name, modes, **limits)


def bands_by(rule):
    """rule(group, hour) -> (band, value), for every group-hour."""
    return {(group, hour): rule(group, hour) for group in sp.DAY_GROUPS for hour in range(24)}


def layout(group, hour):
    """Quiet small hours, prime afternoons and normal otherwise, a little dearer later on.

    Every group gets 6 quiet (0 to 5), 6 prime (12 to 17) and 12 normal hours.
    """
    band = "quiet" if hour < 6 else "prime" if 12 <= hour < 18 else "normal"
    return band, 1.0 + hour / 100


LAYOUT = bands_by(layout)


def api_mode(mode_id, archetype, name, active=True):
    return {"id": mode_id, "archetype": archetype, "name": name, "slug": name.lower(),
            "active": active, "grabbag": False, "schedule_weight": 1}


HAND_MODES = [
    api_mode(21, 2, "Crosskeys"),
    api_mode(22, 2, "Crosskeys 2024"),
    api_mode(23, 2, "Old Entrance", active=False),
    api_mode(11, 1, "S One"),
    api_mode(41, 4, "K One"),
    api_mode(51, 5, "HMG [VT]"),
    api_mode(52, 5, "No Logic Double-Rods [DR]"),
    # Same name as 51 apart from case and brackets.
    api_mode(56, 5, "hmg (vt)"),
    api_mode(71, 7, "M One"),
    # A category the history never saw, and an archetype with no category.
    api_mode(61, 6, "F One"),
    api_mode(81, 8, "Odd One"),
]
HAND_MODEL = sp.Model(
    block={name: [1.0] * 6 for name in ("simple", "entrance", "keys", "glitched", "mystery")},
    hour={group: [1.0] * 24 for group in sp.DAY_GROUPS},
    mode={21: 1.5, 22: 2.0, 51: 3.0},
    mode_slots=Counter({21: 50, 22: 7, 23: 40, 11: 30, 41: 30, 51: 25}),
    fill={},
)


def compose(composition):
    return sp.resolve_composition({"composition": composition}, HAND_MODES, HAND_MODEL)


def mode_names(category):
    return [mode.name for mode in category.modes]
