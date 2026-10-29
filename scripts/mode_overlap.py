#!/usr/bin/env python3
"""Measure how much the ladder modes share racers, to find modes that can share a slot.

The method in plain words
-------------------------
The bot runs one mode per slot. Two modes can run in the same slot without
making racers choose only if few racers race both. For a pair of modes A and
B, only the Eastern Time calendar months when both were on the schedule count,
meaning each had at least four races that month. Over those co-active months,
the directed share A->B is the share of A's entries made by racers who also
entered B that same month: if A ran against B, this share of A's field also
races B. Any entry counts, DNFs included, because entering is what a clash
takes away.

The chance baseline A->B is the share expected if A's racers entered B at B's
general rate: B's racers as a share of every racer active that month, averaged
over the co-active months and weighted by A's entries. A share near its
baseline means B is simply popular; a share well below it means the two modes
draw different crowds.

A pair's score is the larger of its two directions, because a pairing is only
as good as its effect on the mode that loses more. A score below 0.25 rates
low, below 0.5 moderate, and high otherwise. A pair with fewer than six
co-active months is weak evidence. The 95% interval resamples racers with
replacement, keeping each racer's months together, with a fixed seed.

This is an upper bound on the conflict: racing both modes in a month does not
prove a racer would want both slots on the same day. The bot never runs two
modes at once, so the true forced choice cannot be observed.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html import escape
from itertools import combinations
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/mode_overlap.py` and `from scripts import mode_overlap`.
try:
    from scripts import build_stats, participant_stats, race_archive
except ImportError:
    import build_stats
    import participant_stats
    import race_archive

MIN_MONTHLY_RACES = 4
MIN_CO_ACTIVE_MONTHS = 6
LOW_OVERLAP = 0.25
HIGH_OVERLAP = 0.5
BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 20261029

JsonObject = dict[str, Any]
# Racer ID -> (A entries shared with B, A entries), for one directed pair.
Contributions = dict[str, tuple[int, int]]


class ModeOverlapError(RuntimeError):
    """Raised when the races cannot produce a mode overlap analysis."""


@dataclass(frozen=True)
class MonthlyEntries:
    """Entries per racer, month and mode, with each month's races and half-year."""

    racers: dict[str, dict[str, Counter[str]]]
    races: dict[str, Counter[str]]
    periods: dict[str, JsonObject]


@dataclass(frozen=True)
class DirectedOverlap:
    share: float | None
    chance: float | None
    contributions: Contributions


@dataclass(frozen=True)
class PairOverlap:
    a: str
    b: str
    a_to_b: float | None
    b_to_a: float | None
    a_to_b_chance: float | None
    b_to_a_chance: float | None
    score: float | None
    ci_low: float | None
    ci_high: float | None
    co_active_months: int
    rating: str
    weak: bool


def month_key(local_started_at: datetime) -> str:
    return local_started_at.strftime("%Y-%m")


def monthly_entries(contexts: list[JsonObject]) -> MonthlyEntries:
    """Count each identified racer's entries per Eastern Time month and mode."""
    if not contexts:
        raise ModeOverlapError("No races to analyse")
    racers: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    races: dict[str, Counter[str]] = defaultdict(Counter)
    periods: dict[str, JsonObject] = {}
    diagnostics: Counter[str] = Counter()
    for context in contexts:
        local_started_at = context["local_started_at"]
        month = month_key(local_started_at)
        mode = context["mode"]
        races[month][mode] += 1
        periods[month] = build_stats.six_month_period(local_started_at)
        for user_id, *_rest in participant_stats._race_entries(context["race"], diagnostics):
            racers[user_id][month][mode] += 1
    return MonthlyEntries(
        racers={racer: dict(months) for racer, months in racers.items()},
        races=dict(races),
        periods=periods,
    )


def scheduled_months(monthly: MonthlyEntries, mode: str) -> set[str]:
    """Return the months in which `mode` had at least MIN_MONTHLY_RACES races."""
    return {
        month
        for month, counts in monthly.races.items()
        if counts[mode] >= MIN_MONTHLY_RACES
    }


def _month_reach(
    monthly: MonthlyEntries, min_entries: int
) -> dict[str, tuple[int, Counter[str]]]:
    """Map each month to (active racers, racers with min_entries entries per mode)."""
    active: Counter[str] = Counter()
    reach: dict[str, Counter[str]] = defaultdict(Counter)
    for by_month in monthly.racers.values():
        for month, counts in by_month.items():
            active[month] += 1
            for mode, entries in counts.items():
                if entries >= min_entries:
                    reach[month][mode] += 1
    return {month: (active[month], reach[month]) for month in active}


def directed_overlap(
    monthly: MonthlyEntries,
    a: str,
    b: str,
    months: set[str],
    *,
    min_entries: int = 1,
    month_reach: dict[str, tuple[int, Counter[str]]] | None = None,
) -> DirectedOverlap:
    """Return the share of A's entries in `months` made by racers who also raced B.

    A racer counts as shared in a month when they entered B at least
    `min_entries` times that month. The share and chance are None when A has
    no entries in `months`.
    """
    if month_reach is None:
        month_reach = _month_reach(monthly, min_entries)
    contributions: Contributions = {}
    a_entries_by_month: Counter[str] = Counter()
    for racer, by_month in monthly.racers.items():
        shared = total = 0
        for month, counts in by_month.items():
            if month not in months or not counts[a]:
                continue
            total += counts[a]
            a_entries_by_month[month] += counts[a]
            if counts[b] >= min_entries:
                shared += counts[a]
        if total:
            contributions[racer] = (shared, total)

    a_entries = sum(a_entries_by_month.values())
    if not a_entries:
        return DirectedOverlap(share=None, chance=None, contributions=contributions)
    shared_entries = sum(shared for shared, _total in contributions.values())
    expected = 0.0
    for month, entries in a_entries_by_month.items():
        active, reach = month_reach[month]
        expected += entries * reach[b] / active
    return DirectedOverlap(
        share=shared_entries / a_entries,
        chance=expected / a_entries,
        contributions=contributions,
    )


def rate(score: float | None) -> str:
    if score is None:
        return "unmeasured"
    if score < LOW_OVERLAP:
        return "low"
    if score < HIGH_OVERLAP:
        return "moderate"
    return "high"


def bootstrap_weights(
    racer_ids: list[str],
    *,
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> list[Counter[str]]:
    """Return how often each racer is drawn in each resampling round."""
    rng = random.Random(seed)
    population = sorted(racer_ids)
    return [Counter(rng.choices(population, k=len(population))) for _ in range(rounds)]


def _resampled_share(contributions: Contributions, weight: Counter[str]) -> float | None:
    shared = total = 0
    for racer, (racer_shared, racer_total) in contributions.items():
        draws = weight[racer]
        shared += draws * racer_shared
        total += draws * racer_total
    return shared / total if total else None


def bootstrap_interval(
    a_to_b: Contributions,
    b_to_a: Contributions,
    weights: list[Counter[str]],
) -> tuple[float | None, float | None]:
    """Return the 2.5th and 97.5th percentiles of the resampled pair score."""
    scores = []
    for weight in weights:
        forward = _resampled_share(a_to_b, weight)
        backward = _resampled_share(b_to_a, weight)
        # A round that drew none of either mode's racers has no score; skip it.
        if forward is not None and backward is not None:
            scores.append(max(forward, backward))
    if len(scores) < 2:
        return None, None
    cuts = statistics.quantiles(scores, n=40, method="inclusive")
    return cuts[0], cuts[-1]


def _score(forward: float | None, backward: float | None) -> float | None:
    if forward is None or backward is None:
        return None
    return max(forward, backward)


def pair_overlap(
    monthly: MonthlyEntries,
    a: str,
    b: str,
    *,
    min_entries: int = 1,
    weights: list[Counter[str]] | None = None,
    month_reach: dict[str, tuple[int, Counter[str]]] | None = None,
) -> PairOverlap:
    """Measure one pair over the months when both modes were on the schedule."""
    if month_reach is None:
        month_reach = _month_reach(monthly, min_entries)
    if weights is None:
        weights = bootstrap_weights(list(monthly.racers))
    months = scheduled_months(monthly, a) & scheduled_months(monthly, b)
    forward = directed_overlap(
        monthly, a, b, months, min_entries=min_entries, month_reach=month_reach
    )
    backward = directed_overlap(
        monthly, b, a, months, min_entries=min_entries, month_reach=month_reach
    )
    score = _score(forward.share, backward.share)
    if score is None:
        ci_low = ci_high = None
    else:
        ci_low, ci_high = bootstrap_interval(
            forward.contributions, backward.contributions, weights
        )
    return PairOverlap(
        a=a,
        b=b,
        a_to_b=forward.share,
        b_to_a=backward.share,
        a_to_b_chance=forward.chance,
        b_to_a_chance=backward.chance,
        score=score,
        ci_low=ci_low,
        ci_high=ci_high,
        co_active_months=len(months),
        rating=rate(score),
        weak=len(months) < MIN_CO_ACTIVE_MONTHS,
    )


def _pair_trend(
    monthly: MonthlyEntries,
    pair: PairOverlap,
    period_ids: list[str],
    *,
    min_entries: int,
    month_reach: dict[str, tuple[int, Counter[str]]],
) -> list[JsonObject]:
    """Return the pair score within each half-year, without an interval."""
    months = scheduled_months(monthly, pair.a) & scheduled_months(monthly, pair.b)
    trend = []
    for period_id in period_ids:
        period_months = {
            month for month in months if monthly.periods[month]["id"] == period_id
        }
        forward = directed_overlap(
            monthly,
            pair.a,
            pair.b,
            period_months,
            min_entries=min_entries,
            month_reach=month_reach,
        )
        backward = directed_overlap(
            monthly,
            pair.b,
            pair.a,
            period_months,
            min_entries=min_entries,
            month_reach=month_reach,
        )
        trend.append(
            {
                "period": period_id,
                "score": _score(forward.share, backward.share),
                "co_active_months": len(period_months),
            }
        )
    return trend


def _mode_summaries(contexts: list[JsonObject], monthly: MonthlyEntries) -> list[JsonObject]:
    races: Counter[str] = Counter(context["mode"] for context in contexts)
    entries: Counter[str] = Counter()
    racers: dict[str, set[str]] = defaultdict(set)
    for racer, by_month in monthly.racers.items():
        for counts in by_month.values():
            for mode, count in counts.items():
                entries[mode] += count
                racers[mode].add(racer)
    summaries = []
    for mode in races:
        months = sorted(scheduled_months(monthly, mode))
        summaries.append(
            {
                "mode": mode,
                "races": races[mode],
                "entries": entries[mode],
                "racers": len(racers[mode]),
                "scheduled_months": len(months),
                "first_scheduled": months[0] if months else None,
                "last_scheduled": months[-1] if months else None,
            }
        )
    summaries.sort(key=lambda summary: (-summary["entries"], summary["mode"]))
    return summaries


def analyse(
    contexts: list[JsonObject],
    *,
    min_entries: int = 1,
    generated_at: datetime | None = None,
    bootstrap_rounds: int = BOOTSTRAP_ROUNDS,
) -> JsonObject:
    """Measure every mode pair and return plain data for the text and HTML reports."""
    if min_entries < 1:
        raise ModeOverlapError("min_entries must be at least 1")
    monthly = monthly_entries(contexts)
    month_reach = _month_reach(monthly, min_entries)
    weights = bootstrap_weights(list(monthly.racers), rounds=bootstrap_rounds)
    modes = _mode_summaries(contexts, monthly)
    mode_names = [summary["mode"] for summary in modes]

    period_by_id = {period["id"]: period for period in monthly.periods.values()}
    period_ids = sorted(period_by_id)
    pairs = []
    for a, b in combinations(mode_names, 2):
        pair = pair_overlap(
            monthly,
            a,
            b,
            min_entries=min_entries,
            weights=weights,
            month_reach=month_reach,
        )
        trend = _pair_trend(
            monthly, pair, period_ids, min_entries=min_entries, month_reach=month_reach
        )
        pairs.append(asdict(pair) | {"trend": trend})
    order = {mode: index for index, mode in enumerate(mode_names)}
    pairs.sort(
        key=lambda pair: (
            pair["score"] is None,
            pair["score"] or 0,
            order[pair["a"]],
            order[pair["b"]],
        )
    )

    months = sorted(monthly.races)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "parameters": {
            "min_monthly_races": MIN_MONTHLY_RACES,
            "min_entries": min_entries,
            "min_co_active_months": MIN_CO_ACTIVE_MONTHS,
            "low_overlap": LOW_OVERLAP,
            "high_overlap": HIGH_OVERLAP,
            "bootstrap_rounds": bootstrap_rounds,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "source": {
            "race_count": len(contexts),
            "entry_count": sum(summary["entries"] for summary in modes),
            "racer_count": len(monthly.racers),
            "first_month": months[0],
            "last_month": months[-1],
        },
        "modes": modes,
        "periods": [
            {"id": period_id, "label": period_by_id[period_id]["label"]}
            for period_id in period_ids
        ],
        "pairs": pairs,
    }


def _figure(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _pair_label(pair: JsonObject) -> str:
    return f"{pair['a']} / {pair['b']}"


def _co_active(months: int) -> str:
    return f"{months} co-active month{'' if months == 1 else 's'}"


def format_report(result: JsonObject) -> str:
    """Return the ranked pair table as plain text."""
    source = result["source"]
    parameters = result["parameters"]
    min_entries = parameters["min_entries"]
    lines = [
        f"Mode overlap over {source['race_count']:,} races and "
        f"{source['racer_count']:,} racers, {source['first_month']} to "
        f"{source['last_month']} (Eastern Time months).",
        f"A month counts for a pair when both modes had at least "
        f"{parameters['min_monthly_races']} races. A racer is shared with at least "
        f"{min_entries} {'entry' if min_entries == 1 else 'entries'} in the other "
        "mode that month.",
        "",
        f"{'pair':<22}{'score':>6}{'95% interval':>14}{'A->B':>7}{'chance':>8}"
        f"{'B->A':>7}{'chance':>8}{'months':>8}  rating",
    ]
    for pair in result["pairs"]:
        interval = (
            "–"
            if pair["ci_low"] is None
            else f"{pair['ci_low']:.2f}-{pair['ci_high']:.2f}"
        )
        weak = ", weak evidence" if pair["weak"] else ""
        lines.append(
            f"{_pair_label(pair):<22}{_figure(pair['score']):>6}"
            f"{interval:>14}{_figure(pair['a_to_b']):>7}"
            f"{_figure(pair['a_to_b_chance']):>8}{_figure(pair['b_to_a']):>7}"
            f"{_figure(pair['b_to_a_chance']):>8}{pair['co_active_months']:>8}"
            f"  {pair['rating']}{weak}"
        )
    return "\n".join(lines)


# The dashboard's palette and type stacks (site/styles.css), with a matching dark theme.
_CSS = """
/* One reading column: summary first, then the ranked pairs, then the detail. */
:root {
  --bg: #f2f5f3; --surface: #ffffff; --fg: #17211d; --muted: #607069; --line: #cbd3cf;
  --seq: #1d4f5f; --on-seq: #ffffff;
  --low: #12634a; --moderate: #8a6200; --high: #c84a3e;
  --display: "Barlow Condensed", "Avenir Next Condensed", "DIN Condensed", sans-serif;
  --font: "Barlow", "Avenir Next", Avenir, "Trebuchet MS", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111916; --surface: #18221e; --fg: #e3ebe7; --muted: #95a59e; --line: #2d3a35;
    --seq: #8ebccc; --on-seq: #0d1714;
    --low: #5cc49a; --moderate: #e0b04a; --high: #f07f72;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #111916; --surface: #18221e; --fg: #e3ebe7; --muted: #95a59e; --line: #2d3a35;
  --seq: #8ebccc; --on-seq: #0d1714;
  --low: #5cc49a; --moderate: #e0b04a; --high: #f07f72;
  color-scheme: dark;
}
body { background: var(--bg); color: var(--fg); font: 15px/1.5 var(--font); }
main {
  max-width: 1040px; margin: 0 auto; display: grid; gap: 32px; padding-block: 24px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
}
h1, h2 { font-family: var(--display); text-wrap: balance; }
h1 { font-size: 34px; margin: 0; line-height: 1.1; }
h2 { font-size: 21px; margin: 0 0 4px; }
p, ul { margin: 0; max-width: 68ch; }
section { display: grid; gap: 12px; min-width: 0; }
.lede { font-size: 17px; }
.muted, .note { color: var(--muted); font-size: 13px; }
.summary { padding-left: 20px; display: grid; gap: 6px; }
.wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 10px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; }
tr:last-child td { border-bottom: 0; }
.num { text-align: right; }
.rating { font-weight: 600; }
.rating::before { content: ""; display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; background: currentColor; }
.low { color: var(--low); } .moderate { color: var(--moderate); } .high { color: var(--high); }
.unmeasured { color: var(--muted); }
.weak { border: 1px dashed var(--muted); border-radius: 3px; padding: 0 4px; color: var(--muted); font-size: 11px; margin-left: 6px; }
svg { display: block; }
svg text { fill: var(--fg); font: 12px var(--font); }
.heat { min-width: 560px; max-width: 760px; width: 100%; }
.heat .axis { fill: var(--muted); font-size: 12px; }
.guide { stroke: var(--muted); stroke-opacity: .45; stroke-dasharray: 2 2; }
.hatch { stroke: var(--muted); stroke-width: 1.5; }
.cell { stroke: var(--surface); stroke-width: 2; }
.bar { opacity: .85; } .bar.weak-bar { opacity: .4; }
.whisker { stroke: var(--fg); stroke-width: 1.5; fill: none; }
.key { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--muted); }
.ramp { width: 120px; height: 10px; border-radius: 2px; background: linear-gradient(90deg, var(--surface), var(--seq)); border: 1px solid var(--line); }
"""


def _rating_cell(pair: JsonObject) -> str:
    weak = '<span class="weak">weak evidence</span>' if pair["weak"] else ""
    rating = pair["rating"]
    return f'<span class="rating {rating}">{rating}</span>{weak}'


def _score_bar(pair: JsonObject, parameters: JsonObject) -> str:
    """A 0-1 bar for the score with the 95% interval as a whisker."""
    width, height, pad = 132, 18, 4

    def x(value: float) -> float:
        return pad + value * (width - 2 * pad)

    guides = "".join(
        f'<line class="guide" x1="{x(parameters[key]):.1f}" x2="{x(parameters[key]):.1f}" '
        f'y1="0" y2="{height}"/>'
        for key in ("low_overlap", "high_overlap")
    )
    body = ""
    if pair["score"] is not None:
        weak_class = " weak-bar" if pair["weak"] else ""
        body = (
            f'<rect class="bar {pair["rating"]}{weak_class}" x="{x(0):.1f}" y="4" '
            f'width="{x(pair["score"]) - x(0):.1f}" height="10" fill="currentColor"/>'
        )
        if pair["ci_low"] is not None:
            body += (
                f'<path class="whisker" d="M{x(pair["ci_low"]):.1f} 9H{x(pair["ci_high"]):.1f}'
                f'M{x(pair["ci_low"]):.1f} 5V13M{x(pair["ci_high"]):.1f} 5V13"/>'
            )
    tip = (
        f"{pair['a']} / {pair['b']}: score {_figure(pair['score'])}, "
        f"95% interval {_figure(pair['ci_low'])} to {_figure(pair['ci_high'])}"
    )
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img"><title>{escape(tip)}</title>{guides}{body}</svg>'
    )


def _direction(share: float | None, chance: float | None) -> str:
    return f"{_figure(share)} <span class=\"muted\">(chance {_figure(chance)})</span>"


def _pair_table(result: JsonObject) -> str:
    rows = []
    for pair in result["pairs"]:
        a, b = escape(pair["a"]), escape(pair["b"])
        rows.append(
            f'<tr><td>{a} / {b}</td>'
            f'<td class="{pair["rating"]}">{_score_bar(pair, result["parameters"])}</td>'
            f'<td class="num">{_figure(pair["score"])}</td>'
            f'<td>{_direction(pair["a_to_b"], pair["a_to_b_chance"])}</td>'
            f'<td>{_direction(pair["b_to_a"], pair["b_to_a_chance"])}</td>'
            f'<td class="num">{pair["co_active_months"]}</td>'
            f"<td>{_rating_cell(pair)}</td></tr>"
        )
    return (
        '<div class="wrap"><table><thead><tr><th>Pair</th>'
        "<th>Score and 95% interval</th><th class=\"num\">Score</th>"
        "<th>A to B</th><th>B to A</th><th class=\"num\">Co-active months</th>"
        f"<th>Rating</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _heatmap(result: JsonObject) -> str:
    names = [mode["mode"] for mode in result["modes"]]
    cells = {}
    for pair in result["pairs"]:
        cells[(pair["a"], pair["b"])] = (pair["a_to_b"], pair["a_to_b_chance"], pair)
        cells[(pair["b"], pair["a"])] = (pair["b_to_a"], pair["b_to_a_chance"], pair)
    size, left, top = 64, 110, 52
    width, height = left + size * len(names), top + size * len(names)
    parts = [
        f'<svg class="heat" viewBox="0 0 {width} {height}" role="img">'
        '<title>Directed overlap between modes</title>'
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><line class="hatch" x1="0" y1="0" x2="0" y2="6"/>'
        "</pattern></defs>",
        f'<text class="axis" x="{left}" y="12">Runs against (column)</text>',
        f'<text class="axis" text-anchor="middle" '
        f'transform="translate(14 {top + size * len(names) / 2}) rotate(-90)">'
        "Field at risk (row)</text>",
    ]
    for index, name in enumerate(names):
        label = escape(name)
        parts.append(
            f'<text x="{left + size * index + size / 2}" y="{top - 8}" '
            f'text-anchor="middle">{label}</text>'
            f'<text x="{left - 8}" y="{top + size * index + size / 2 + 4}" '
            f'text-anchor="end">{label}</text>'
        )
    for row, row_name in enumerate(names):
        for col, col_name in enumerate(names):
            x, y = left + size * col, top + size * row
            cell = f'x="{x}" y="{y}" width="{size}" height="{size}"'
            if row == col:
                parts.append(f'<rect {cell} class="cell" style="fill: var(--bg)"/>')
                continue
            share, chance, pair = cells[(row_name, col_name)]
            weak = pair["weak"] or share is None
            strength = 0 if share is None else round(share * 100)
            ink = "var(--on-seq)" if strength > 55 and not weak else "var(--fg)"
            fill = f"color-mix(in srgb, var(--seq) {strength}%, var(--surface))"
            opacity = "; fill-opacity: 0.45" if weak else ""
            hatch = f'<rect {cell} fill="url(#hatch)"/>' if weak else ""
            note = " (weak evidence)" if pair["weak"] else ""
            tip = (
                f"{row_name} field also races {col_name}: {_figure(share)}, "
                f"chance {_figure(chance)}, {_co_active(pair['co_active_months'])}{note}"
            )
            parts.append(
                f"<g><title>{escape(tip)}</title>"
                f'<rect {cell} class="cell" style="fill: {fill}{opacity}"/>{hatch}'
                f'<text x="{x + size / 2}" y="{y + size / 2 + 4}" text-anchor="middle" '
                f'style="fill: {ink}">{_figure(share)}</text></g>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _mode_table(result: JsonObject) -> str:
    last_month = result["source"]["last_month"]
    rows = "".join(
        f"<tr><td>{escape(mode['mode'])}</td><td class=\"num\">{mode['races']:,}</td>"
        f"<td class=\"num\">{mode['racers']:,}</td>"
        f"<td class=\"num\">{mode['scheduled_months']}</td>"
        f"<td>{escape(mode['first_scheduled'] or '–')}</td>"
        f"<td>{escape(mode['last_scheduled'] or '–')}</td>"
        f"<td>{'yes' if mode['last_scheduled'] == last_month else 'no'}</td></tr>"
        for mode in result["modes"]
    )
    return (
        '<div class="wrap"><table><thead><tr><th>Mode</th><th class="num">Races</th>'
        '<th class="num">Racers</th><th class="num">Months on schedule</th>'
        "<th>First scheduled</th><th>Last scheduled</th><th>On the schedule now</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _trend_table(result: JsonObject) -> str:
    head = "".join(f"<th class=\"num\">{escape(p['label'])}</th>" for p in result["periods"])
    rows = []
    for pair in result["pairs"]:
        cells = "".join(
            f'<td class="num" title="{_co_active(point["co_active_months"])}">'
            f"{_figure(point['score'])}</td>"
            for point in pair["trend"]
        )
        rows.append(f"<tr><td>{escape(pair['a'])} / {escape(pair['b'])}</td>{cells}</tr>")
    return (
        f'<div class="wrap"><table><thead><tr><th>Pair</th>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _summary_points(result: JsonObject) -> list[str]:
    """Return short, data-driven findings as escaped HTML fragments."""
    pairs = result["pairs"]
    low = [pair for pair in pairs if pair["rating"] == "low"]
    points = []
    solid = [escape(_pair_label(pair)) for pair in low if not pair["weak"]]
    weak = [escape(_pair_label(pair)) for pair in low if pair["weak"]]
    if solid:
        points.append(f"Low overlap with solid evidence: {', '.join(solid)}.")
    else:
        points.append("No pair rates low with solid evidence.")
    if weak:
        points.append(f"Also low, but on weak evidence: {', '.join(weak)}.")
    partners = Counter(name for pair in low for name in (pair["a"], pair["b"]))
    if partners:
        most = max(partners.values())
        names = ", ".join(
            escape(mode["mode"]) for mode in result["modes"] if partners[mode["mode"]] == most
        )
        points.append(
            f"Most low-rated partners: {names} ({most} of {len(result['modes']) - 1})."
        )
    below_chance = [
        escape(_pair_label(pair))
        for pair in pairs
        if not pair["weak"]
        and pair["score"] is not None
        and pair["a_to_b"] < pair["a_to_b_chance"]
        and pair["b_to_a"] < pair["b_to_a_chance"]
    ]
    if below_chance:
        points.append(
            "Below chance in both directions, so these modes draw different crowds: "
            f"{', '.join(below_chance)}."
        )
    last_month = result["source"]["last_month"]
    current = {mode["mode"] for mode in result["modes"] if mode["last_scheduled"] == last_month}
    live = [
        pair for pair in pairs if pair["score"] is not None and {pair["a"], pair["b"]} <= current
    ]
    if live:
        best = live[0]
        points.append(
            f"Lowest pair among modes on the schedule now: {escape(_pair_label(best))} at "
            f"{_figure(best['score'])} ({best['rating']}"
            f"{', weak evidence' if best['weak'] else ''})."
        )
    return points


def _method_note(parameters: JsonObject) -> str:
    low, high = parameters["low_overlap"], parameters["high_overlap"]
    return (
        "<p>For a pair of modes A and B, look only at the Eastern Time months when both were "
        f"on the schedule (at least {parameters['min_monthly_races']} races that month). "
        "Ask what share of A's entries came from racers who also raced B that month: if A ran "
        "against B, that share of A's field would have to choose. The pair's score is the "
        "larger of the two directions. Any entry counts, DNFs included.</p>"
        "<p>The chance baseline is the share expected if A's racers entered B at B's general "
        "rate. A share well below it means the modes draw different crowds; near it, B is "
        f"simply popular. Scores below {low} rate low, below {high} moderate, otherwise high. "
        f"Fewer than {parameters['min_co_active_months']} co-active months is weak evidence. "
        f"The 95% interval resamples racers ({parameters['bootstrap_rounds']:,} rounds, "
        "fixed seed).</p>"
        "<p><strong>Limits.</strong> The score is an upper bound on conflict: racing both modes "
        "in a month does not prove a racer would want both slots on the same day, and the bot "
        "never runs two modes at once, so true forced choice cannot be observed. Pairs are "
        "measured over different months, so a paused mode is compared on the racers of its "
        "own era.</p>"
    )


def render_html(result: JsonObject) -> str:
    """Return the self-contained HTML report: no scripts, no external requests.

    The page follows the claude.ai Artifact page contract: publishing wraps it
    in the document skeleton, so it starts at its own <title> and <style>.
    """
    source = result["source"]
    generated = escape(result["generated_at"][:10])
    summary = "".join(f"<li>{point}</li>" for point in _summary_points(result))
    return f"""<title>Mode Overlap</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{_CSS}</style>
<main>
<header>
<h1>Mode Overlap</h1>
<p class="lede">Which ladder modes can share a time slot without making racers choose?</p>
<p class="muted">{source['race_count']:,} races, {source['racer_count']:,} racers,
{escape(source['first_month'])} to {escape(source['last_month'])} (Eastern Time months).
Generated {generated}.</p>
</header>
<section><h2>Summary</h2><ul class="summary">{summary}</ul></section>
<section><h2>Pairs ranked by overlap</h2>
<p class="note">Lowest first. Bars run from 0 to 1; dashed guides mark the low and high
thresholds and the whisker is the 95% interval. A to B is the share of the first mode's
entries made by racers who also raced the second.</p>
{_pair_table(result)}</section>
<section><h2>Who also races the other mode</h2>
<p class="note">Each cell is the share of the row mode's field that also races the column
mode. Hatched cells are weak or unmeasured pairs. Hover for the chance baseline.</p>
<div class="wrap" style="padding:8px">{_heatmap(result)}</div>
<div class="key"><span>0</span><span class="ramp"></span><span>1</span></div></section>
<section><h2>Modes</h2>{_mode_table(result)}</section>
<section><h2>Overlap by half-year</h2>
<p class="note">Pair score within each half-year. An en dash means no co-active month.
Hover a cell for its co-active months.</p>{_trend_table(result)}</section>
<section><h2>Method and limits</h2>{_method_note(result['parameters'])}</section>
</main>
"""


def load_contexts(archive_dir: Path = race_archive.ARCHIVE_DIR) -> list[JsonObject]:
    """Return the dashboard's included race contexts from the archive."""
    races = [envelope["race"] for envelope in race_archive.load_races(archive_dir)]
    return build_stats.included_race_contexts(races)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--archive",
        type=Path,
        default=race_archive.ARCHIVE_DIR,
        help=f"raw race archive directory (default: {race_archive.ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=1,
        help="entries in the other mode that month that make a racer shared (default: 1)",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="also write the self-contained HTML report to this path",
    )
    args = parser.parse_args()
    if args.min_entries < 1:
        parser.error("--min-entries must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = analyse(load_contexts(args.archive), min_entries=args.min_entries)
    except (
        ModeOverlapError,
        race_archive.ArchiveError,
        participant_stats.ParticipantDataError,
    ) as error:
        print(f"Mode overlap failed: {error}", file=sys.stderr)
        return 1

    print(format_report(result))
    if args.html is not None:
        args.html.write_text(render_html(result), encoding="utf-8")
        print(f"Wrote {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
