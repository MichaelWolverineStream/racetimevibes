#!/usr/bin/env python3
"""Compare schedule strategies that break the planner's rules with the shipped rules.

The method in plain words
-------------------------
`schedule_planner.py` keeps a fixed set of rules. Each variant here breaks one
of them, and a last variant combines the ones worth breaking. Every variant
plans the same season as the planner, for all four sets, and is compared with
the shipped rules. The planner is deterministic, so each variant, set and fold
runs once. The plan file's bands, composition and events are the same in every
run; only the rules change.

Scores are out of sample. The history's Monday to Sunday weeks, in Eastern
Time, alternate between two halves. A proposal is planned with the demand
model and the history of one half and scored with the model of the other, then
the halves swap and the two scores are compared one fold at a time. The open
bands (the band rule) and the featured record come from the planning half's
history, because the planner computes them from the history it is given, so both
halves are out of sample for them. Only the Mode overlap pair scores come from
the whole archive, as the planner reads them. They price the clash of races less
than 2 hours apart, which includes the between races, through the planner's own
`clash`.

The demand model cannot see that a category's races draw fewer racers the more
of them run on one date. In the history, a category's only race on a date drew
a little more than the model expects, and its fourth drew about half. So every
race is scored with its half's ratio of actual to expected entrants, and of
fired to expected rooms, for its category's count on that date: 1, 2, 3, or 4
and more, each shrunk toward 1 with SHRINK_SLOTS slots.

Four measures are compared, per set: expected entrants a race, the weakest plan
category's entrants a race, expected entrants over the season, and expected
cancelled rooms. A variant is disqualified when any plan category keeps fewer
than VOLUME_FLOOR of its shipped races in either fold. A measure is better when
its mean change over the two folds is at least TOLERANCE in its good direction
and it is better in both folds, and worse when its mean change is at least
TOLERANCE in its bad direction. A variant wins when it is not disqualified, no
measure is worse in any set, and at least one is better.

The combined variant merges every winner; with fewer than two, every variant
that is not disqualified and has no worse measure; with fewer than two of
those, it is not run. Winners whose gain may rest on something the model
cannot price, such as racers tiring of the same slot, or joining a second race
more easily when races start 3 hours apart, are listed for a real
trial rather than recommended, and so is a combined variant that holds one.

All of these rules were fixed before the comparison first ran.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/schedule_lab.py` and `from scripts import schedule_lab`.
try:
    from scripts import (
        build_stats,
        participant_stats,
        race_archive,
        schedule_archive,
    )
    from scripts import schedule_planner as planner
except ImportError:
    import build_stats
    import participant_stats
    import race_archive
    import schedule_archive
    import schedule_planner as planner

JsonObject = dict[str, Any]

TOLERANCE = 0.005
VOLUME_FLOOR = 0.75
# A category's races on one date are counted 1, 2, 3, or this many and more.
SATURATION_BUCKETS = 4
# Weeks alternate between the halves counted from this Monday.
WEEK_ZERO = date(2000, 1, 3)
HALVES = ("A", "B")
WHOLE = "all"
# Plan with one part of the history and score with another; the whole year is in sample.
FOLDS = (("A", "B"), ("B", "A"), (WHOLE, WHOLE))
# No saturation: every ratio is 1, as in the planner's own score.
IDENTITY = {bucket: (1.0, 1.0) for bucket in range(1, SATURATION_BUCKETS + 1)}


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    higher_is_better: bool


MEASURES = (
    Measure("per_race", "entrants a race", True),
    Measure("weakest", "weakest category a race", True),
    Measure("total", "season entrants", True),
    Measure("cancelled", "expected cancelled rooms", False),
)
# Counted per fold, and averaged over the out-of-sample folds for the report.
DIAGNOSTICS = ("closed", "crowded", "between", "short_gaps", "shortfall", "empty_hours", "uncovered", "weekly_races")


@dataclass(frozen=True)
class Variant:
    key: str
    title: str
    # The shipped rule the variant breaks, in docs/analyses.md's words.
    breaks: str
    rules: planner.Rules = planner.SHIPPED
    # The plan file's dailyMax, weeklyMax and onlyPrimeTime apply.
    plan_caps: bool = True
    # The planning model counts holidays as prime days. The scoring model always does.
    holidays: bool = True
    # What the demand model cannot price, when the variant's gain may rest on it.
    model_blind: str | None = None


SHIPPED = Variant("shipped", "Shipped", "nothing")
# The levers a variant may change besides its rules, which count field by field.
LEVERS = ("plan_caps", "holidays")

VARIANTS = (
    Variant(
        "open-bands",
        "Every band open",
        "a category never races in a band where its rooms fired less than 10% of the time",
        rules=replace(planner.SHIPPED, band_rule=False),
        model_blind="a category races in bands where it almost never fired; the model extrapolates",
    ),
    Variant(
        "no-featured",
        "No featured races",
        "each plan category gets one race in prime time, on a weekday of its own, alternating week by week between two adjacent hours",
        rules=replace(planner.SHIPPED, featured=False),
    ),
    Variant(
        "fill-shares",
        "Shares by fill",
        "a band is shared by expected turnout",
        rules=replace(planner.SHIPPED, shares="fill"),
    ),
    Variant(
        "equal-shares",
        "Equal shares",
        "a band is shared by expected turnout",
        rules=replace(planner.SHIPPED, shares="equal"),
    ),
    Variant(
        "no-between",
        "No between races",
        "Glitched and Spoiler are between categories: their races always start between two "
        "other races, never in a slot of their own, and come on top of the main races a day",
        rules=replace(planner.SHIPPED, between=False),
        model_blind="the clash cost of a between race comes from Mode overlap scores, an upper "
        "bound that is weak evidence for Spoiler, so the model may overcharge the shipped rule",
    ),
    Variant(
        "two-hour-gaps",
        "Two-hour gaps",
        "outside prime time, 3 hours apart is preferred",
        rules=replace(planner.SHIPPED, wide_gaps=False),
        model_blind="the model cannot see that racers join consecutive races more easily, or "
        "that a long race overlaps the next one, when races start 3 hours apart rather than 2",
    ),
    Variant(
        "no-favourite",
        "No favourite mode",
        "a category's prime-time races run its most popular mode",
        rules=replace(planner.SHIPPED, favourite=False),
    ),
    Variant(
        "no-rotation",
        "No rotation",
        "a race avoids the category that ran at the same hour a week earlier, then the one that ran there a day earlier",
        rules=replace(planner.SHIPPED, rotation=False),
        model_blind="racers do not tire of one category at a repeated hour",
    ),
    Variant(
        "no-plan-caps",
        "No plan caps",
        "the plan file's dailyMax, weeklyMax and onlyPrimeTime apply",
        plan_caps=False,
    ),
    Variant(
        "no-holidays",
        "No holidays",
        "a holiday counts as a prime-group day, with that group's factors, bands and races a day",
        holidays=False,
    ),
    Variant(
        "no-stagger",
        "No alternate weeks",
        "in alternate weeks, races move an hour to reach the two-hour sections the week before left empty",
        rules=replace(planner.SHIPPED, stagger=False),
        model_blind="the model cannot see the racers who can only join at hours a fixed weekly grid "
        "leaves empty, so it counts only the cost of moving races",
    ),
    Variant(
        "no-every-hour",
        "No every-hour floor",
        "a category marked everyHour gets at least one race at every hour of its open bands",
        rules=replace(planner.SHIPPED, every_hour=False),
        model_blind="the model cannot see the racers who can only join at the hours a category would "
        "leave empty, so it counts only the cost of covering them",
    ),
    Variant(
        "no-draw",
        "Best hours",
        "each date draws its hours among the equally good sets, with odds by their value",
        rules=replace(planner.SHIPPED, draw=False),
        model_blind="the model cannot see the racers who can only join at hours the best-value grid "
        "never uses, so it counts only the cost of the draw",
    ),
)


class LabError(RuntimeError):
    """Raised when the variants cannot be compared."""


def changed_levers(variant: Variant) -> dict[str, Any]:
    """Return what a variant changes from shipped: each Rules field as `rules.<name>`, and
    each other lever by its name."""
    changed = {}
    for rule in fields(planner.Rules):
        value = getattr(variant.rules, rule.name)
        if value != getattr(planner.SHIPPED, rule.name):
            changed[f"rules.{rule.name}"] = value
    for lever in LEVERS:
        if getattr(variant, lever) != getattr(SHIPPED, lever):
            changed[lever] = getattr(variant, lever)
    return changed


def combine(parts: list[Variant]) -> Variant:
    """Merge the variants' changes into one variant; two parts may not change one lever."""
    merged: dict[str, Any] = {}
    for part in parts:
        for name, value in changed_levers(part).items():
            if name in merged and merged[name] != value:
                raise LabError(f"Two variants change {name} differently")
            merged[name] = value
    rules = replace(
        planner.SHIPPED,
        **{name.removeprefix("rules."): v for name, v in merged.items() if name.startswith("rules.")},
    )
    others = {name: value for name, value in merged.items() if not name.startswith("rules.")}
    blind = "; ".join(part.model_blind for part in parts if part.model_blind) or None
    return Variant(
        "combined",
        "Combined",
        "the rules of " + ", ".join(part.key for part in parts),
        rules=rules,
        model_blind=blind,
        **others,
    )


# History ---------------------------------------------------------------------


def halves(slots: list[planner.HistorySlot]) -> dict[str, list[planner.HistorySlot]]:
    """Split the slots by alternate Monday to Sunday weeks in Eastern Time."""
    parts: dict[str, list[planner.HistorySlot]] = {half: [] for half in HALVES}
    for slot in slots:
        day = slot.started_at.astimezone(planner.TIMEZONE).date()
        parts[HALVES[(day - WEEK_ZERO).days // 7 % 2]].append(slot)
    return parts


def saturation(
    slots: list[planner.HistorySlot], model: planner.Model
) -> dict[int, tuple[float, float]]:
    """Per count of a category's slots on one ET date (1, 2, 3, SATURATION_BUCKETS and
    more): actual over expected entrants, and fired over expected fill, each shrunk toward 1."""
    per_date = Counter(
        (slot.started_at.astimezone(planner.TIMEZONE).date(), slot.category) for slot in slots
    )
    # entrants, expected entrants, fired, expected fill, slots
    sums: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])
    for slot in slots:
        local = slot.started_at.astimezone(planner.TIMEZONE)
        bucket = min(per_date[local.date(), slot.category], SATURATION_BUCKETS)
        entry = sums[bucket]
        entry[0] += slot.entrants
        entry[1] += model.expected(slot.category, model.mode.get(slot.mode_id, 1.0), local)
        entry[2] += slot.fired
        entry[3] += model.fill[slot.category][local.hour // planner.BLOCK_HOURS]
        entry[4] += 1
    table = {}
    for bucket in range(1, SATURATION_BUCKETS + 1):
        entrants, expected, fired, fill, count = sums[bucket]
        table[bucket] = (
            planner._shrink(entrants, expected, int(count), 1.0),
            planner._shrink(fired, fill, int(count), 1.0),
        )
    return table


@dataclass(frozen=True)
class Context:
    """What every run reads: the history and its halves, per part a scoring model (with
    holidays), a planning model without them and a saturation table, the Mode overlap pair
    scores and the season."""

    api_modes: list[JsonObject]
    config: JsonObject
    # The plan file's bands, parsed: the same for every variant and fold.
    layout: dict[tuple[str, int], str]
    slots: dict[str, list[planner.HistorySlot]]
    models: dict[str, planner.Model]
    # The same fits with holidays counted as ordinary days, for the no-holidays variant.
    plain_models: dict[str, planner.Model]
    saturation: dict[str, dict[int, tuple[float, float]]]
    # Category pair scores over the whole archive, as the planner reads them.
    pairs: dict[planner.Pair, planner.PairScore]
    events: list[planner.Event]
    period_start: datetime
    period_end: datetime
    history_start: datetime
    history_end: datetime


def build_context(
    schedule: list[JsonObject],
    api_modes: list[JsonObject],
    races: list[JsonObject],
    config: JsonObject,
    *,
    start: date | None = None,
    weeks: int | None = None,
    pairs: dict[planner.Pair, planner.PairScore] | None = None,
) -> Context:
    """Learn everything the runs share, the way `schedule_planner.analyse` does."""
    slots, history_start, history_end = planner.history_slots(schedule, api_modes, races)
    parts = {WHOLE: slots, **halves(slots)}
    models = {name: planner.fit_model(part) for name, part in parts.items()}
    categories, _left_out = planner.resolve_composition(config, api_modes, models[WHOLE])
    sources = sorted({source for category in categories for source in category.sources})
    for half in HALVES:
        missing = [source for source in sources if source not in models[half].block]
        if missing:
            raise LabError(
                f"Half {half} of the history has no slots for {', '.join(missing)}, "
                "so it can neither plan nor score them"
            )
    if pairs is None:
        pairs = planner.pair_scores(build_stats.included_race_contexts(races), sources)
    period_start, period_end = planner.season_bounds(
        *planner.plan_season(config, schedule, start, weeks)
    )
    known = set(schedule_archive.ARCHETYPE_CATEGORIES.values()) | set(config["composition"])
    return Context(
        api_modes=api_modes,
        config=config,
        layout=planner.parse_bands(config.get("bands")),
        slots=parts,
        models=models,
        plain_models={name: planner.fit_model(part, holidays=False) for name, part in parts.items()},
        saturation={name: saturation(parts[name], models[name]) for name in parts},
        pairs=pairs,
        events=planner.parse_events(config.get("events", []), known, period_start, period_end),
        period_start=period_start,
        period_end=period_end,
        history_start=history_start,
        history_end=history_end,
    )


# Runs ------------------------------------------------------------------------


def variant_config(config: JsonObject, variant: Variant) -> JsonObject:
    """Return the plan file as the variant reads it: without its caps when they are lifted.

    The caps are dailyMax, weeklyMax and onlyPrimeTime. The bands, the events, `between` and
    `everyHour` stay: `between` and the every-hour floor are rules their own levers change,
    not caps."""
    if variant.plan_caps:
        return config
    capped = {"dailyMax", "weeklyMax", "onlyPrimeTime"}
    return {
        **config,
        "composition": {
            name: {key: value for key, value in entry.items() if key not in capped}
            for name, entry in config["composition"].items()
        },
    }


def score(
    proposal: JsonObject,
    model: planner.Model,
    table: dict[int, tuple[float, float]],
    pairs: dict[planner.Pair, planner.PairScore],
) -> JsonObject:
    """Score a proposal's races with a model and a saturation table.

    Each race's expected entrants and fill are the model's, times the table's ratios for its
    source category's races that date; the clash is `planner.clash`'s, from those entrants,
    for the races of any category, between races included. A plan category without races
    weighs 0 a race.
    """
    races = proposal["schedule"]
    per_date = Counter((race["date"], race["source"]) for race in races)
    entrants, fills, starts = [], [], []
    for race in races:
        started_at = datetime.fromisoformat(race["utc"].replace("Z", "+00:00"))
        local = started_at.astimezone(planner.TIMEZONE)
        entrants_ratio, fired_ratio = table[
            min(per_date[race["date"], race["source"]], SATURATION_BUCKETS)
        ]
        expected = model.expected(race["source"], model.mode.get(race["mode_id"], 1.0), local)
        entrants.append(expected * entrants_ratio)
        block_fill = model.fill[race["source"]][local.hour // planner.BLOCK_HOURS]
        fills.append(min(1.0, block_fill * fired_ratio))
        starts.append(started_at)
    clash = planner.clash(starts, [race["category"] for race in races], entrants, pairs)

    names = [entry["category"] for entry in proposal["composition"]]
    by_category = {name: {"races": 0, "entrants": 0.0} for name in names}
    for race, value in zip(races, entrants):
        by_category[race["category"]]["races"] += 1
        by_category[race["category"]]["entrants"] += value
    per_race = {
        name: entry["entrants"] / entry["races"] if entry["races"] else 0.0
        for name, entry in by_category.items()
    }
    weakest = min(names, key=lambda name: (per_race[name], names.index(name)))
    total = sum(entrants) - clash
    return {
        "races": len(races),
        "per_race": total / len(races) if races else 0.0,
        "weakest": per_race[weakest],
        "weakest_category": weakest,
        "total": total,
        "cancelled": sum(1 - fill for fill in fills),
        "clash": clash,
        "categories": by_category,
        "crowded": sum(count >= SATURATION_BUCKETS for count in per_date.values()),
    }


def closed_band_races(
    proposal: JsonObject,
    categories: list[planner.Category],
    history: list[planner.HistorySlot],
    model: planner.Model,
    layout: dict[tuple[str, int], str],
) -> int:
    """Count the races in a band the shipped band rule closes to their category, judged by
    the planning half's own history and the plan file's bands (`layout`)."""
    bands = planner.group_bands(layout, model, categories)
    rates = planner.band_rates(history, model, bands)
    closed = {}
    for category in categories:
        try:
            shut = set(planner.BANDS) - set(planner.open_bands(category, rates))
        except planner.ScheduleError:
            shut = set(planner.BANDS)
        closed[category.name] = shut
    return sum(race["band"] in closed[race["category"]] for race in proposal["schedule"])


def uncovered_cells(proposal: JsonObject) -> int:
    """Count the (category, hour) cells where a category the plan file marks every-hour has no
    race at an hour whose band is open to it in some day group.

    Judged from the proposal's composition flags, bands and races by hour, not from the
    planner's own `uncovered`, so every variant is held to the plan file whatever its rules."""
    open_hours: dict[str, set[int]] = {}
    for entry in proposal["composition"]:
        if entry["every_hour"]:
            bands = set(entry["open_bands"])
            open_hours[entry["category"]] = {
                row["hour"] for row in proposal["bands"] if row["band"] in bands
            }
    by_hour = {row["category"]: row["by_hour"] for row in proposal["categories"]}
    return sum(
        by_hour[name][hour] == 0 for name, hours in open_hours.items() for hour in hours
    )


@dataclass(frozen=True)
class Job:
    variant: Variant
    set_name: str
    plan: str
    scored: str


def run_job(context: Context, job: Job) -> JsonObject:
    """Plan one proposal and score it. A variant the planner cannot plan returns its error."""
    variant = job.variant
    head = {"variant": variant.key, "set": job.set_name, "plan": job.plan, "scored": job.scored}
    models = context.models if variant.holidays else context.plain_models
    model = models[job.plan]
    history = context.slots[job.plan]
    try:
        categories, _left_out = planner.resolve_composition(
            variant_config(context.config, variant), context.api_modes, model
        )
        planned = planner.category_pairs(categories, context.pairs)
        race_set = next(rules for rules in planner.SETS if rules.name == job.set_name)
        proposal = planner.plan_proposal(
            race_set,
            history=history,
            model=model,
            categories=categories,
            layout=context.layout,
            pairs=planned,
            events=planner.expand_events(context.events, categories),
            period_start=context.period_start,
            period_end=context.period_end,
            rules=variant.rules,
        )
        closed = closed_band_races(proposal, categories, history, model, context.layout)
    except planner.ScheduleError as error:
        return {**head, "error": str(error)}

    # In sample, the whole-year model scores without saturation: the planner's own score.
    table = IDENTITY if job.plan == job.scored else context.saturation[job.scored]
    scored = score(proposal, context.models[job.scored], table, planned)
    return {
        **head,
        **scored,
        "closed": closed,
        "between": proposal["between"],
        "short_gaps": proposal["short_gaps"],
        "shortfall": len(proposal["shortfall"]),
        "empty_hours": proposal["empty_hours"],
        "uncovered": uncovered_cells(proposal),
        "weekly_races": proposal["weekly_races"],
        "notice": proposal["notice"],
    }


def jobs_for(variants: list[Variant]) -> list[Job]:
    return [
        Job(variant, race_set.name, plan, scored)
        for variant in variants
        for race_set in planner.SETS
        for plan, scored in FOLDS
    ]


_WORKER_CONTEXT: Context | None = None


def _start_worker(context: Context) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = context


def _work(job: Job) -> JsonObject:
    return run_job(_WORKER_CONTEXT, job)


# Judging ---------------------------------------------------------------------


def fold_means(results: list[JsonObject]) -> dict[tuple[str, str], JsonObject]:
    """Collect each variant's runs per set.

    An entry holds the two out-of-sample folds in FOLDS order as `folds`, and their means
    for the report. An out-of-sample run that failed makes the whole entry its error. The
    in-sample run, which never decides, adds its entrants a race as `in_sample`, or its
    error as `in_sample_error`.
    """
    grouped: dict[tuple[str, str], list[JsonObject]] = defaultdict(list)
    for result in results:
        grouped[result["variant"], result["set"]].append(result)
    means = {}
    for key, group in grouped.items():
        order = {fold: index for index, fold in enumerate(FOLDS)}
        group.sort(key=lambda result: order[result["plan"], result["scored"]])
        cross = [result for result in group if result["plan"] != result["scored"]]
        inside = [result for result in group if result["plan"] == result["scored"]]
        failed = [result["error"] for result in cross if "error" in result]
        if failed:
            means[key] = {"error": failed[0]}
            continue
        entry: JsonObject = {
            measure.key: sum(result[measure.key] for result in cross) / len(cross)
            for measure in MEASURES
        }
        entry["folds"] = cross
        entry["races"] = sum(result["races"] for result in cross) / len(cross)
        names = list(cross[0]["categories"])
        entry["categories"] = {
            name: {
                field: sum(result["categories"][name][field] for result in cross) / len(cross)
                for field in ("races", "entrants")
            }
            for name in names
        }
        for field in DIAGNOSTICS:
            entry[field] = sum(result[field] for result in cross) / len(cross)
        entry["in_sample"] = inside[0].get("per_race") if inside else None
        entry["in_sample_error"] = inside[0].get("error") if inside else None
        entry["notices"] = sorted({result["notice"] for result in cross if result["notice"]})
        means[key] = entry
    return means


def verdict(changes: list[float]) -> str:
    """Judge one measure from its relative change per fold, positive in its good direction."""
    mean = sum(changes) / len(changes)
    if mean >= TOLERANCE and all(change > 0 for change in changes):
        return "better"
    if mean <= -TOLERANCE:
        return "worse"
    return "same"


def judge(
    means: dict[tuple[str, str], JsonObject], key: str, set_names: list[str]
) -> JsonObject:
    """Judge a variant against shipped, per set: each measure, and the volume guard."""
    sets = {}
    for set_name in set_names:
        entry, base = means[key, set_name], means["shipped", set_name]
        if "error" in base:
            raise LabError(f"The shipped rules failed for {set_name}")
        if "error" in entry:
            sets[set_name] = {"error": entry["error"]}
            continue
        pairs = list(zip(entry["folds"], base["folds"]))
        measures = {}
        for measure in MEASURES:
            sign = 1 if measure.higher_is_better else -1
            changes = [
                sign * (fold[measure.key] - shipped[measure.key]) / shipped[measure.key]
                if shipped[measure.key]
                else 0.0
                for fold, shipped in pairs
            ]
            measures[measure.key] = {
                "verdict": verdict(changes),
                "change": sign * sum(changes) / len(changes),
            }
        short = sorted(
            {
                name
                for fold, shipped in pairs
                for name, counts in shipped["categories"].items()
                if fold["categories"].get(name, {"races": 0})["races"]
                < VOLUME_FLOOR * counts["races"]
            }
        )
        sets[set_name] = {"measures": measures, "guard": not short, "short": short}
    return sets


def wins(judgement: JsonObject) -> bool:
    """Not disqualified, no measure worse in any set, and at least one better."""
    sets = judgement.values()
    if any("error" in entry or not entry["guard"] for entry in sets):
        return False
    verdicts = [m["verdict"] for entry in sets for m in entry["measures"].values()]
    return "worse" not in verdicts and "better" in verdicts


def harmless(judgement: JsonObject) -> bool:
    """Not disqualified and no measure worse in any set."""
    sets = judgement.values()
    return all(
        "error" not in entry
        and entry["guard"]
        and all(m["verdict"] != "worse" for m in entry["measures"].values())
        for entry in sets
    )


def combination(variants: list[Variant], judgements: dict[str, JsonObject]) -> list[Variant]:
    """The variants the combined variant merges: the winners, or failing two of them the
    harmless ones, or none."""
    winners = [variant for variant in variants if wins(judgements[variant.key])]
    if len(winners) >= 2:
        return winners
    quiet = [variant for variant in variants if harmless(judgements[variant.key])]
    return quiet if len(quiet) >= 2 else []


def compare(
    context: Context,
    *,
    workers: int = 1,
    only: str | None = None,
) -> JsonObject:
    """Run shipped and the variants, judge them, then run and judge the combined variant."""
    singles = [variant for variant in VARIANTS if only in (None, variant.key)]
    if not singles:
        raise LabError(f"No variant {only}; known: {', '.join(v.key for v in VARIANTS)}")
    pool = (
        ProcessPoolExecutor(workers, initializer=_start_worker, initargs=(context,))
        if workers > 1
        else None
    )

    def run(variants: list[Variant]) -> list[JsonObject]:
        jobs = jobs_for(variants)
        if pool is None:
            return [run_job(context, job) for job in jobs]
        return list(pool.map(_work, jobs))

    try:
        results = run([SHIPPED, *singles])
        means = fold_means(results)
        set_names = [race_set.name for race_set in planner.SETS]
        judgements = {variant.key: judge(means, variant.key, set_names) for variant in singles}
        parts = combination(singles, judgements) if only is None else []
        combined = combine(parts) if parts else None
        if combined is not None:
            results += run([combined])
            means = fold_means(results)
            judgements["combined"] = judge(means, "combined", set_names)
    finally:
        if pool is not None:
            pool.shutdown()

    everyone = [SHIPPED, *singles, *([combined] if combined else [])]
    recommended = [
        v.key for v in singles if wins(judgements[v.key]) and not v.model_blind
    ]
    trial = [v.key for v in singles if wins(judgements[v.key]) and v.model_blind]
    if combined is not None and wins(judgements["combined"]):
        (trial if combined.model_blind else recommended).append("combined")
    return {
        "period": {
            "start": context.period_start.astimezone(planner.TIMEZONE).date().isoformat(),
            # The last race day: the period ends at midnight after it.
            "end": planner.period_dates(context.period_start, context.period_end)[-1].isoformat(),
        },
        "history": {
            "start": context.history_start.astimezone(planner.TIMEZONE).date().isoformat(),
            "end": context.history_end.astimezone(planner.TIMEZONE).date().isoformat(),
            "slots": {name: len(part) for name, part in context.slots.items()},
        },
        "saturation": {
            name: {str(bucket): list(ratios) for bucket, ratios in table.items()}
            for name, table in context.saturation.items()
        },
        "variants": [
            {
                "key": variant.key,
                "title": variant.title,
                "breaks": variant.breaks,
                "changes": changed_levers(variant),
                "model_blind": variant.model_blind,
            }
            for variant in everyone
        ],
        "combined_parts": [part.key for part in parts],
        "means": [
            {"variant": key[0], "set": key[1], **entry} for key, entry in sorted(means.items())
        ],
        "judgements": judgements,
        "recommended": recommended,
        "trial": trial,
        "results": results,
    }


# Report ----------------------------------------------------------------------


SYMBOLS = {"better": "+", "same": "=", "worse": "-"}


def _entry(result: JsonObject, key: str, set_name: str) -> JsonObject | None:
    """A variant's means for a set, or None when it was not run."""
    for row in result["means"]:
        if row["variant"] == key and row["set"] == set_name:
            return row
    return None


def _weakest_name(entry: JsonObject) -> str:
    def per_race(name: str) -> float:
        counts = entry["categories"][name]
        return counts["entrants"] / counts["races"] if counts["races"] else 0.0

    return min(entry["categories"], key=per_race)


def _variant_row(result: JsonObject, key: str, set_name: str) -> str | None:
    entry = _entry(result, key, set_name)
    if entry is None:
        return None
    if "error" in entry:
        return f"  {key:<16}could not be planned: {entry['error']}"
    judged = result["judgements"].get(key, {}).get(set_name)
    cells = [f"  {key:<16}", f"{entry['races']:>6.0f}"]
    for measure, digits in zip(MEASURES, (2, 2, 0, 0)):
        cells.append(f"{entry[measure.key]:>10,.{digits}f}")
        if judged is None:
            cells.append(" " * 9)
        else:
            mark = judged["measures"][measure.key]
            cells.append(f"{mark['change']:>+8.1%}{SYMBOLS[mark['verdict']]}")
    if judged is None:
        cells.append(f"  {'':<6}{'':<4}")
    else:
        guard = "ok" if judged["guard"] else "short"
        cells.append(f"  {guard:<6}{'yes' if wins(result['judgements'][key]) else 'no':<4}")
    if entry["in_sample_error"] is not None:
        cells.append(f"{'failed':>10}")
    else:
        cells.append(f"{entry['in_sample']:>10.2f}" if entry["in_sample"] is not None else " " * 10)
    cells.append(f"  {_weakest_name(entry)}")
    line = "".join(cells)
    if judged is not None and judged["short"]:
        line += f"; short of races: {', '.join(judged['short'])}"
    return line


def format_report(result: JsonObject) -> str:
    whole = result["saturation"][WHOLE]
    slots = result["history"]["slots"]
    set_names = [race_set.name for race_set in planner.SETS]
    lines = [
        f"Schedule strategy comparison for {result['period']['start']} to "
        f"{result['period']['end']} (Eastern Time).",
        f"History {result['history']['start']} to {result['history']['end']}: "
        f"{slots[WHOLE]:,} slots, in halves of {slots['A']:,} and {slots['B']:,} by "
        "alternate weeks.",
        f"One deterministic run per variant, set and fold. Scored out of sample with "
        f"saturation. A change counts from {TOLERANCE:.1%}, and each category keeps "
        f"{VOLUME_FLOOR:.0%} of its shipped races.",
        "Saturation over the whole year, actual over expected entrants / rooms, by a "
        "category's races on a date: "
        + ", ".join(
            f"{bucket}{'+' if int(bucket) == SATURATION_BUCKETS else ''} "
            f"{ratios[0]:.2f}/{ratios[1]:.2f}"
            for bucket, ratios in whole.items()
        )
        + ".",
    ]
    header = (
        f"  {'variant':<16}{'races':>6}{'a race':>10}{'':9}{'weakest':>10}{'':9}"
        f"{'season':>10}{'':9}{'cancelled':>10}{'':9}  {'guard':<6}{'win':<4}{'in sample':>10}"
        "  weakest category"
    )
    for set_name in set_names:
        lines += ["", f"{set_name.capitalize()}:", header]
        for variant in result["variants"]:
            row = _variant_row(result, variant["key"], set_name)
            if row is not None:
                lines.append(row)
    lines += [
        "",
        "Columns: each measure with its mean change from shipped over the two out-of-sample "
        "folds and its verdict (+ better, = same, - worse); in sample is the whole-year "
        "plan's entrants a race, scored without saturation, or failed when the planner "
        "could not plan it.",
    ]
    for set_name in set_names:
        lines += ["", f"Per category, entrants a race (races), {set_name.capitalize()}:"]
        for variant in result["variants"]:
            entry = _entry(result, variant["key"], set_name)
            if entry is not None and "error" not in entry:
                lines.append(f"  {variant['key']:<16}{_categories(entry)}")
    for set_name in set_names:
        lines += [
            "",
            f"Diagnostics, {set_name.capitalize()}, per season: races in a closed band, "
            f"category-dates with {SATURATION_BUCKETS}+ races, between races, short gaps, "
            "shortfall dates, empty hours, uncovered hours:",
        ]
        for variant in result["variants"]:
            entry = _entry(result, variant["key"], set_name)
            if entry is not None and "error" not in entry:
                lines.append(
                    f"  {variant['key']:<16}{entry['closed']:>6.0f}{entry['crowded']:>6.0f}"
                    f"{entry['between']:>6.0f}{entry['short_gaps']:>6.0f}"
                    f"{entry['shortfall']:>6.0f}{entry['empty_hours']:>6.0f}{entry['uncovered']:>6.0f}"
                )
    lines += ["", "What each variant breaks:"]
    for variant in result["variants"][1:]:
        blind = f" Model-blind: {variant['model_blind']}." if variant["model_blind"] else ""
        lines.append(f"  {variant['key']}: {variant['title']}. Breaks: {variant['breaks']}.{blind}")
    lines.append("")
    if result["combined_parts"]:
        lines.append("Combined merges: " + ", ".join(result["combined_parts"]) + ".")
    else:
        lines.append("Combined was not run: fewer than two variants qualified.")
    lines.append(
        "Recommended: " + (", ".join(result["recommended"]) or "none") + ". "
        "Needs a real trial: " + (", ".join(result["trial"]) or "none") + "."
    )
    return "\n".join(lines)


def _categories(entry: JsonObject | None) -> str:
    if entry is None or "error" in entry:
        return ""
    parts = []
    for name, counts in entry["categories"].items():
        per_race = counts["entrants"] / counts["races"] if counts["races"] else 0.0
        parts.append(f"{name} {per_race:.2f} ({counts['races']:.0f})")
    return ", ".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, default=race_archive.ARCHIVE_DIR,
                        help=f"archive directory (default: {race_archive.ARCHIVE_DIR})")
    parser.add_argument("--config", type=Path, default=planner.CONFIG_PATH,
                        help=f"composition and events (default: {planner.CONFIG_PATH.name})")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="first day, YYYY-MM-DD (default: the planner's)")
    parser.add_argument("--weeks", type=int, default=None,
                        help="season length in weeks, pause included (default: the planner's)")
    parser.add_argument("--variant", default=None,
                        help="run only this variant against shipped, without the combined one")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="worker processes (default: one per CPU)")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write every number to this JSON file")
    args = parser.parse_args()
    if args.weeks is not None and args.weeks < 1:
        parser.error("--weeks must be at least 1")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    try:
        context = build_context(
            schedule_archive.load_schedule(args.archive),
            schedule_archive.load_modes(args.archive),
            [envelope["race"] for envelope in race_archive.load_races(args.archive)],
            planner.load_config(args.config),
            start=args.start,
            weeks=args.weeks,
        )
        result = compare(context, workers=args.jobs, only=args.variant)
    except (
        LabError,
        planner.ScheduleError,
        race_archive.ArchiveError,
        participant_stats.ParticipantDataError,
    ) as error:
        print(f"Schedule comparison failed: {error}", file=sys.stderr)
        return 1

    print(format_report(result))
    if args.json is not None:
        generated = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        args.json.write_text(
            json.dumps({"generated_at": generated, **result}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
