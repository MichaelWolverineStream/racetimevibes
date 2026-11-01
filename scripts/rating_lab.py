#!/usr/bin/env python3
"""Compare rating algorithms with the ladder rating, on the same pairs and rules.

The method in plain words
-------------------------
Every candidate replays the archive in order and, before each race, predicts
every decisive pair of entrants exactly as `ladder_rating.py --backtest` does:
finishers by place, a finisher over a non-finisher, and two non-finishers or
equal places skipped. All candidates are scored on the identical pairs, and
only placement is ever read.

A candidate is eligible only if it keeps every promise the ladder makes,
overall and in every mode: a winner's displayed points never fall, every
newcomer and every mode debut shows 1000, nothing changes while a racer is
away, a mode rating reads only that mode's races with the overall parameters,
and finish times have no effect.

Tuning reads only races before OUT_OF_SAMPLE_START: `tune` cuts its input there
itself, so no later race can reach it. It scores two folds, July to September
and October to December 2025. The months before are warm-up, because everyone
in them is new to the archive. A setting must move a veteran's points at least
as much as the shipped rating does over the same folds, which keeps the
"livelier" ladder the floor was chosen for. Among the lively settings within
one standard error of the best, the simplest wins: no field-size damping and
no provisional K first, then the values nearest the shipped ones. The choices
are frozen in CHOSEN before any race from OUT_OF_SAMPLE_START on is scored.

The comparison then runs once, on the races from OUT_OF_SAMPLE_START on. The
primary measure is pair log loss, as a paired difference from the shipped
rating. Its interval is the wider of two bootstraps, one resampling races and
one resampling Eastern Time ISO weeks, and its one-sided p-value is the larger
of the two, Holm-adjusted across the candidates. A candidate earns a change
only if it is eligible, Holm-significant, at least PRACTICAL_DELTA better,
better in both halves of the period, and no worse when each race counts once.
A new model must also beat the retuned shipped model the same way; otherwise
retuning is the simpler change. Everything else in the report describes the
candidates and never decides.

Result on the archive as of 2026-11-01, run once with CHOSEN frozen:

                       pairs  accuracy  log loss  delta (95% CI)
    shipped           47,576     0.723    0.5414
    BT full, retuned  47,576     0.728    0.5341  -0.0074 (-0.0091, -0.0056)
    TM full           47,576     0.728    0.5347  -0.0068 (-0.0088, -0.0048)
    Elo               47,576     0.727    0.5368  -0.0046 (-0.0060, -0.0033)
    Plackett-Luce     47,576     0.716    0.5854  +0.0440 (+0.0361, +0.0517)
    BT partial pair   47,576     0.693    0.5875  +0.0461 (+0.0396, +0.0530)
    Every candidate kept every promise, overall and in every mode.
    Recommendation: retune to beta 8, sigma0 12, floor 4, alpha 0.5.

The retune was adopted: `ladder_rating` now runs the retuned setting, and
SHIPPED keeps the rating it was compared with.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from itertools import product
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable

try:
    from scripts import ladder_rating
except ImportError:
    import ladder_rating


JsonObject = dict[str, Any]
State = tuple[float, ...]
Update = Callable[[list[list[float]], list[Any]], None]

MU0 = ladder_rating.MU0
BASE = ladder_rating.BASE
KAPPA = ladder_rating.KAPPA
VETERAN_RACES = ladder_rating.VETERAN_RACES
OUT_OF_SAMPLE_START = ladder_rating.OUT_OF_SAMPLE_START
TUNING_FOLDS = (("2025-07-01", "2025-10-01"), ("2025-10-01", OUT_OF_SAMPLE_START))
SECOND_HALF_START = "2026-07-01"

PROBABILITY_CLIP = 1e-6  # every predicted chance is kept inside [clip, 1 - clip]
# The smallest log-loss gain worth a change: about twice the 0.0017 that floor
# 3 gains over the shipped floor 4 on September to December 2025, the accuracy
# already traded for a livelier ladder. It was set before any candidate was built.
PRACTICAL_DELTA = 0.003
SIGNIFICANCE = 0.05
RESAMPLES = 2000
BOOTSTRAP_SEED = 0
CALIBRATION_BINS = 10
EXPERIENCE_BUCKETS = ((0, 0), (1, 4), (5, 19), (20, 49), (50, None))  # prior races, lesser side
FIELD_BUCKETS = ((2, 2), (3, 5), (6, 10), (11, None))
LEADERBOARD_RACES = 20  # racers compared in the final standings need this many races
LEADERBOARD_TOP = 10


@dataclass(frozen=True)
class Result:
    """Per-entry states before and after each race, and the two rules that read them.

    `pre` and `post` line up with the input records, as in `ladder_rating._Ratings`.
    """

    pre: list[list[State]]
    post: list[list[State]]
    win_chance: Callable[[State, State], float]
    display: Callable[[State], int]


def _replay(
    update: Update, initial: State, records: list[JsonObject], player_count: int
) -> tuple[list[list[State]], list[list[State]]]:
    """Run `update` over chronological records, every racer starting from `initial`.

    `update` changes one race's state lists in place, every racer from the
    pre-race states, so a racer's state changes only in the races they enter.
    """
    states: list[State | None] = [None] * player_count
    pre: list[list[State]] = []
    post: list[list[State]] = []
    for record in records:
        entries = record["e"]
        race = [
            list(initial if states[player] is None else states[player])
            for player, *_rest in entries
        ]
        pre.append([tuple(state) for state in race])
        update(race, entries)
        post.append([tuple(state) for state in race])
        for (player, *_rest), state in zip(entries, post[-1]):
            states[player] = state
    return pre, post


def by_mode(
    rate: Callable[..., Result], records: list[JsonObject], player_count: int, **params: Any
) -> Result:
    """Rate each mode on its own races alone, with the same parameters.

    This is `ladder_rating.rate_by_mode` for any candidate, so a mode rating can
    never read another mode's races.
    """
    pre: list[list[State]] = [[] for _ in records]
    post: list[list[State]] = [[] for _ in records]
    result = rate([], player_count, **params)
    for mode in sorted({record["m"] for record in records}):
        indexes = [index for index, record in enumerate(records) if record["m"] == mode]
        result = rate([records[index] for index in indexes], player_count, **params)
        for index, race_pre, race_post in zip(indexes, result.pre, result.post):
            pre[index] = race_pre
            post[index] = race_post
    return Result(pre, post, result.win_chance, result.display)


LOGISTIC_TEN_TO_ONE = math.log(10)  # the skill gap, in units of c, that a logistic chance makes 10:1
NORMAL_TEN_TO_ONE = NormalDist().inv_cdf(10 / 11)  # the same gap under a normal chance


def _weng_lin_display(
    *, beta: float, sigma0: float, sigma_floor: float, ten_to_one: float = LOGISTIC_TEN_TO_ONE
) -> Callable[[State], int]:
    """Return the ladder's points rule for these parameters.

    It is `ladder_rating`'s formula with the scale recomputed for the setting
    and for the family's own win chance, so 400 points stay about 10:1 odds
    between established racers and point moves compare across settings and
    families. The liveliness bar reads these moves, so a wrong scale would
    hold a family to a different bar.
    """
    k = MU0 / sigma0
    scale = 400 / (ten_to_one * math.sqrt(2 * sigma_floor**2 + 2 * beta**2))

    def display(state: State) -> int:
        mean, variance = state
        return round(BASE + scale * (mean - k * math.sqrt(variance)))

    return display


def _bradley_terry_full(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    alpha: float,
    kappa: float = KAPPA,
) -> None:
    """The ladder rating's own update, which took its damping from this comparison.

    Damping multiplies a winner's positive omega by a positive number, so a
    winner's mean still rises and the guarantee holds.
    """
    ladder_rating._update_race(
        states, entries, beta=beta, sigma_floor=sigma_floor, kappa=kappa, alpha=alpha
    )


def rate_bradley_terry_full(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float,
    sigma0: float,
    sigma_floor: float,
    alpha: float,
) -> Result:
    """The ladder rating, with field-size damping as an extra parameter."""
    update = partial(_bradley_terry_full, beta=beta, sigma_floor=sigma_floor, alpha=alpha)
    pre, post = _replay(update, (MU0, sigma0 * sigma0), records, player_count)
    return Result(
        pre,
        post,
        partial(ladder_rating._win_chance, beta=beta),
        _weng_lin_display(beta=beta, sigma0=sigma0, sigma_floor=sigma_floor),
    )


def _apply_weng_lin(
    states: list[list[float]],
    old: list[tuple[float, float]],
    omegas: list[float],
    deltas: list[float],
    *,
    damping: float,
    sigma_floor: float,
    kappa: float,
) -> None:
    """Finish a Weng-Lin race the way the ladder does, from each racer's summed omega and delta."""
    floor_variance = sigma_floor * sigma_floor
    for state, (mean, variance), omega, delta in zip(states, old, omegas, deltas):
        shrunk = max(variance * max(1 - damping * delta, kappa), floor_variance)
        state[0] = mean + damping * omega
        state[1] = min(variance, shrunk)


def _normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _normal_terms(x: float) -> tuple[float, float]:
    """Return Weng & Lin's v(x) = pdf / cdf and w(x) = v * (v + x) for a win with draw margin 0.

    Far in the lower tail the cdf underflows, so, as OpenSkill does, use the
    limits v = -x and w = 1 for x < 0, else 0.
    """
    cdf = _normal_cdf(x)
    if cdf < sys.float_info.epsilon:
        return -x, 1.0 if x < 0 else 0.0
    v = math.exp(-x * x / 2) / math.sqrt(2 * math.pi) / cdf
    return v, v * (v + x)


def _thurstone_mosteller(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    alpha: float,
    kappa: float = KAPPA,
) -> None:
    """Weng & Lin's Algorithm 3: every decided pair, with a normal win chance and draw margin 0.

    A winner's mean never falls. A pair they win adds var / c * v(x), and v is
    positive, so omega is positive for a sole or shared first place with anyone
    behind them, and 0 if everyone shares it. Their pairs are never losses, and
    delta is a sum of w >= 0 terms because v(x) + x >= 0, so no variance rises.
    Damping is positive and leaves both signs alone.
    """
    damping = ladder_rating._damping(entries, alpha)
    old = [(mean, variance) for mean, variance in states]
    omegas = [0.0] * len(old)
    deltas = [0.0] * len(old)
    for i, (mean_i, variance_i) in enumerate(old):
        for q, (mean_q, variance_q) in enumerate(old):
            if q == i:
                continue
            score = ladder_rating._outcome(entries[i], entries[q])
            if score is None:
                continue
            c = math.sqrt(variance_i + variance_q + 2 * beta * beta)
            x = (mean_i - mean_q) / c
            gamma = math.sqrt(variance_i) / c
            v, w = _normal_terms(x if score == 1.0 else -x)
            omegas[i] += (variance_i / c * v) * (1 if score == 1.0 else -1)
            deltas[i] += gamma * variance_i / (c * c) * w
    _apply_weng_lin(states, old, omegas, deltas, damping=damping, sigma_floor=sigma_floor, kappa=kappa)


def rate_thurstone_mosteller(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float,
    sigma0: float,
    sigma_floor: float,
    alpha: float,
) -> Result:
    """Thurstone-Mosteller full pair: the ladder's pairs and floor, with a normal win chance."""
    update = partial(_thurstone_mosteller, beta=beta, sigma_floor=sigma_floor, alpha=alpha)
    pre, post = _replay(update, (MU0, sigma0 * sigma0), records, player_count)

    def win_chance(a: State, b: State) -> float:
        return _normal_cdf((a[0] - b[0]) / math.sqrt(a[1] + b[1] + 2 * beta * beta))

    display = _weng_lin_display(
        beta=beta, sigma0=sigma0, sigma_floor=sigma_floor, ten_to_one=NORMAL_TEN_TO_ONE
    )
    return Result(pre, post, win_chance, display)


def _partial_pairs(entries: list[Any]) -> list[tuple[int, int]]:
    """Return the pairs of Algorithm 2, as (better, worse) entry positions.

    Finishers are grouped by place, and every member of a place meets every
    member of the next place; the worst place meets every non-finisher.
    Grouping keeps racers who share a place on equal terms whatever the entry
    order, as non-finishers are. Non-finishers never meet each other, and with
    no finishers there are no pairs.
    """
    places: dict[int, list[int]] = defaultdict(list)
    others = []
    for i, (_player, place, status, _seconds) in enumerate(entries):
        if ladder_rating._is_finisher(place, status):
            places[place].append(i)
        else:
            others.append(i)
    groups = [places[place] for place in sorted(places)]
    pairs = [(a, b) for upper, lower in zip(groups, groups[1:]) for a in upper for b in lower]
    if groups:
        pairs += [(a, other) for a in groups[-1] for other in others]
    return pairs


def _bradley_terry_partial(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    alpha: float,
    kappa: float = KAPPA,
) -> None:
    """Weng & Lin's Algorithm 2: the full-pair logistic terms, summed over `_partial_pairs` only.

    A winner's mean never falls. Each pair they are in is a win or undecided, so
    every term of omega is (1 - chance) > 0 or absent, and every term of delta
    is chance * (1 - chance) >= 0. A race with no decided pair changes nothing.
    """
    damping = ladder_rating._damping(entries, alpha)
    old = [(mean, variance) for mean, variance in states]
    omegas = [0.0] * len(old)
    deltas = [0.0] * len(old)
    for a, b in _partial_pairs(entries):
        for i, q in ((a, b), (b, a)):
            score = ladder_rating._outcome(entries[i], entries[q])
            if score is None:
                continue
            (mean_i, variance_i), (mean_q, variance_q) = old[i], old[q]
            c = math.sqrt(variance_i + variance_q + 2 * beta * beta)
            chance = 1 / (1 + math.exp((mean_q - mean_i) / c))
            omegas[i] += variance_i / c * (score - chance)
            deltas[i] += math.sqrt(variance_i) / c * variance_i / (c * c) * chance * (1 - chance)
    _apply_weng_lin(states, old, omegas, deltas, damping=damping, sigma_floor=sigma_floor, kappa=kappa)


def rate_bradley_terry_partial(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float,
    sigma0: float,
    sigma_floor: float,
    alpha: float,
) -> Result:
    """Bradley-Terry partial pair: the ladder's rating, comparing each racer with neighbours only."""
    update = partial(_bradley_terry_partial, beta=beta, sigma_floor=sigma_floor, alpha=alpha)
    pre, post = _replay(update, (MU0, sigma0 * sigma0), records, player_count)
    return Result(
        pre,
        post,
        partial(ladder_rating._win_chance, beta=beta),
        _weng_lin_display(beta=beta, sigma0=sigma0, sigma_floor=sigma_floor),
    )


def _plackett_luce(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    alpha: float,
    kappa: float = KAPPA,
) -> None:
    """Weng & Lin's Algorithm 4, with the ladder's tie rule.

    Each racer is rated against their own view of the race: everyone except the
    others who share their place, or, for a non-finisher, the other non-finishers.
    In the view finishers rank by place and non-finishers share one rank below
    them. Racers of one rank have one chance and share their terms, so the sum
    over members divided by the rank's size is one term per rank.

    A winner's mean never falls. In their view they are alone at rank 1, so omega
    is var / c * (1 - r) with r = exp(mu_i / c) over a sum that includes their own
    term, hence r <= 1 and omega >= 0 (> 0 with anyone behind them). Delta is a
    sum of r * (1 - r) >= 0. Without the view a stronger co-winner's omega
    could go negative.
    """
    damping = ladder_rating._damping(entries, alpha)
    old = [(mean, variance) for mean, variance in states]
    ranks = [
        place if ladder_rating._is_finisher(place, status) else math.inf for _player, place, status, _seconds in entries
    ]
    omegas = [0.0] * len(old)
    deltas = [0.0] * len(old)
    for i, (mean_i, variance_i) in enumerate(old):
        view = [s for s in range(len(old)) if s == i or ranks[s] != ranks[i]]
        c = math.sqrt(sum(old[s][1] + beta * beta for s in view))
        top = max(old[s][0] for s in view)
        weight: dict[float, float] = defaultdict(float)
        for s in view:
            weight[ranks[s]] += math.exp((old[s][0] - top) / c)
        mine = math.exp((mean_i - top) / c)
        omega = 0.0
        delta = 0.0
        behind = 0.0
        for rank in sorted(weight, reverse=True):
            behind += weight[rank]
            if rank <= ranks[i]:
                chance = mine / behind
                omega += (1.0 if rank == ranks[i] else 0.0) - chance
                delta += chance * (1 - chance)
        omegas[i] = variance_i / c * omega
        deltas[i] = math.sqrt(variance_i) / c * variance_i / (c * c) * delta
    _apply_weng_lin(states, old, omegas, deltas, damping=damping, sigma_floor=sigma_floor, kappa=kappa)


def rate_plackett_luce(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float,
    sigma0: float,
    sigma_floor: float,
    alpha: float,
) -> Result:
    """Plackett-Luce: the whole finishing order at once, with the ladder's floor and tie rule."""
    update = partial(_plackett_luce, beta=beta, sigma_floor=sigma_floor, alpha=alpha)
    pre, post = _replay(update, (MU0, sigma0 * sigma0), records, player_count)
    return Result(
        pre,
        post,
        partial(ladder_rating._win_chance, beta=beta),
        _weng_lin_display(beta=beta, sigma0=sigma0, sigma_floor=sigma_floor),
    )


ELO_BASE = 1000.0


def _elo_k(races: int, *, k0: float, k_min: float, provisional: float) -> float:
    """Return the step size: k0 for a debut, falling in a line to k_min after `provisional` races."""
    if provisional <= 0:
        return k_min
    return k_min + (k0 - k_min) * max(0, provisional - races) / provisional


def _elo(
    states: list[list[float]],
    entries: list[Any],
    *,
    k0: float,
    k_min: float,
    provisional: float,
    alpha: float,
) -> None:
    """Multi-player Elo: each decided pair is one Elo game, summed, with a K that shrinks as a racer gains races.

    A state is [rating, races entered before this race]. A winner's rating
    never falls: their pairs are wins or undecided, each adds K * (1 - E) with E
    below 1, and K and the damping are positive.
    """
    damping = ladder_rating._damping(entries, alpha)
    old = [(rating, races) for rating, races in states]
    for i, (rating_i, races_i) in enumerate(old):
        surprise = 0.0
        for q, (rating_q, _races_q) in enumerate(old):
            if q == i:
                continue
            score = ladder_rating._outcome(entries[i], entries[q])
            if score is None:
                continue
            surprise += score - 1 / (1 + 10 ** ((rating_q - rating_i) / 400))
        k = _elo_k(races_i, k0=k0, k_min=k_min, provisional=provisional)
        states[i][0] = rating_i + damping * k * surprise
        states[i][1] = races_i + 1


def _elo_win_chance(a: State, b: State) -> float:
    return 1 / (1 + 10 ** ((b[0] - a[0]) / 400))


def rate_elo(
    records: list[JsonObject],
    player_count: int,
    *,
    k0: float,
    k_min: float,
    provisional: float,
    alpha: float,
) -> Result:
    """Multi-player Elo from 1000, shown as the rounded rating."""
    update = partial(_elo, k0=k0, k_min=k_min, provisional=provisional, alpha=alpha)
    pre, post = _replay(update, (ELO_BASE, 0), records, player_count)
    return Result(pre, post, _elo_win_chance, lambda state: round(state[0]))


@dataclass(frozen=True)
class Family:
    """A candidate model: how it rates, its pre-registered grid, and what counts as simplest."""

    label: str
    short: str
    rate: Callable[..., Result]
    axes: dict[str, tuple[float, ...]]
    natural_minimum: frozenset[str]  # axes whose lowest value is a natural bound, not a grid edge
    simplicity: Callable[[JsonObject], tuple]
    canonical: Callable[[JsonObject], JsonObject] = lambda params: params

    def grid(self) -> list[JsonObject]:
        """Return every distinct setting, in axis order."""
        settings: list[JsonObject] = []
        for values in product(*self.axes.values()):
            params = self.canonical(dict(zip(self.axes, values)))
            if params not in settings:
                settings.append(params)
        return settings


# The ladder rating as shipped when this comparison ran, before the retune it
# recommended: the baseline every delta is measured against. It is written out,
# not read from `ladder_rating`, so the comparison stays reproducible.
SHIPPED = {"beta": 12.0, "sigma0": 8.0, "sigma_floor": 4.0, "alpha": 0.0}
# Pre-registered as beta 8-20, sigma0 6-10 and floor 2-6. The first tuning run
# chose beta 8, sigma0 6 and 10, and floor 6, all grid edges, so the edge rule
# extended them before any later race was scored.
WENG_LIN_AXES = {
    "beta": (4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0),
    "sigma0": (4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0),
    "sigma_floor": (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
    "alpha": (0.0, 0.5, 1.0),
}


# The second run still chose partial pair's beta 4 and sigma0 14, so only its
# axes were extended again: growing the shared grid would have moved settings
# the other families had already settled. The third run chose sigma0 20 and
# floor 2, so they were extended once more, the last round.
PARTIAL_PAIR_AXES = {
    "beta": (2.0, 3.0, *WENG_LIN_AXES["beta"]),
    "sigma0": (*WENG_LIN_AXES["sigma0"], 16.0, 20.0, 25.0, 30.0, 40.0),
    "sigma_floor": (1.0, *WENG_LIN_AXES["sigma_floor"]),
    "alpha": WENG_LIN_AXES["alpha"],
}


def _weng_lin_simplicity(axes: dict[str, tuple[float, ...]]) -> Callable[[JsonObject], tuple]:
    """No damping first, then the fewest grid steps from the shipped values."""

    def simplicity(params: JsonObject) -> tuple:
        steps = sum(
            abs(axes[name].index(params[name]) - axes[name].index(SHIPPED[name]))
            for name in ("beta", "sigma0", "sigma_floor")
        )
        return (params["alpha"] != 0, steps)

    return simplicity


# Pre-registered as k0 32-96 and k_min 8-24, extended by the edge rule after
# the first tuning run chose 96 and 24.
ELO_AXES = {
    "k0": (32.0, 48.0, 64.0, 96.0, 128.0, 192.0),
    "k_min": (8.0, 12.0, 16.0, 24.0, 32.0, 48.0),
    "provisional": (0.0, 5.0, 10.0, 20.0),
    "alpha": (0.0, 0.5, 1.0),
}


def _elo_simplicity(params: JsonObject) -> tuple:
    """No provisional K first, then no damping."""
    return (params["provisional"] != 0, params["alpha"] != 0)


def _elo_canonical(params: JsonObject) -> JsonObject:
    """Without a provisional period k0 has no effect, so pin it to k_min and let the grid dedupe."""
    return {**params, "k0": params["k_min"]} if params["provisional"] == 0 else params


RETUNED = "bradley_terry_full"
FAMILIES: dict[str, Family] = {
    RETUNED: Family(
        "Bradley-Terry full pair, retuned",
        "BT full",
        rate_bradley_terry_full,
        WENG_LIN_AXES,
        frozenset({"alpha"}),
        _weng_lin_simplicity(WENG_LIN_AXES),
    ),
    "thurstone_mosteller": Family(
        "Thurstone-Mosteller full pair",
        "TM full",
        rate_thurstone_mosteller,
        WENG_LIN_AXES,
        frozenset({"alpha"}),
        _weng_lin_simplicity(WENG_LIN_AXES),
    ),
    "partial_pair": Family(
        "Bradley-Terry partial pair",
        "BT part",
        rate_bradley_terry_partial,
        PARTIAL_PAIR_AXES,
        frozenset({"alpha"}),
        _weng_lin_simplicity(PARTIAL_PAIR_AXES),
    ),
    "plackett_luce": Family(
        "Plackett-Luce",
        "PL",
        rate_plackett_luce,
        WENG_LIN_AXES,
        frozenset({"alpha"}),
        _weng_lin_simplicity(WENG_LIN_AXES),
    ),
    "elo": Family(
        "Elo, multi-player",
        "Elo",
        rate_elo,
        ELO_AXES,
        frozenset({"provisional", "alpha"}),
        _elo_simplicity,
        _elo_canonical,
    ),
}

# Each family's setting, frozen from `--tune` before the comparison first runs.
CHOSEN: dict[str, JsonObject] = {
    "bradley_terry_full": {"beta": 8.0, "sigma0": 12.0, "sigma_floor": 4.0, "alpha": 0.5},
    "thurstone_mosteller": {"beta": 12.0, "sigma0": 8.0, "sigma_floor": 3.0, "alpha": 0.5},
    "partial_pair": {"beta": 5.0, "sigma0": 40.0, "sigma_floor": 4.0, "alpha": 0.0},
    "plackett_luce": {"beta": 6.0, "sigma0": 8.0, "sigma_floor": 5.0, "alpha": 0.0},
    "elo": {"k0": 128.0, "k_min": 24.0, "provisional": 10.0, "alpha": 0.5},
}


def rate_shipped(records: list[JsonObject], player_count: int) -> Result:
    return rate_bradley_terry_full(records, player_count, **SHIPPED)


# Scoring


@dataclass(frozen=True)
class RaceScore:
    """One race's decisive pairs, summed. A pair's loss is -log(winner's chance)."""

    pairs: int
    loss: float
    hits: float
    brier: float


@dataclass(frozen=True)
class Summary:
    pairs: int
    accuracy: float
    log_loss: float
    brier: float
    race_log_loss: float  # each race's mean pair loss, averaged over races


def _clip(chance: float) -> float:
    return min(max(chance, PROBABILITY_CLIP), 1 - PROBABILITY_CLIP)


def pair_chances(records: list[JsonObject], result: Result, indexes: list[int]) -> list[list[float]]:
    """Return, per selected race, each decisive pair's pre-race chance that its winner wins."""
    return [
        [
            _clip(result.win_chance(result.pre[index][winner], result.pre[index][loser]))
            for winner, loser in ladder_rating._decisive_pairs(records[index]["e"])
        ]
        for index in indexes
    ]


def race_scores(chances: list[list[float]]) -> list[RaceScore]:
    return [
        RaceScore(
            len(race),
            -sum(math.log(chance) for chance in race),
            sum(ladder_rating._hit(chance) for chance in race),
            sum((1 - chance) ** 2 for chance in race),
        )
        for race in chances
    ]


def summarise(scores: list[RaceScore]) -> Summary:
    scored = [score for score in scores if score.pairs]
    pairs = sum(score.pairs for score in scored)
    if not pairs:
        raise ValueError("No decisive entrant pairs to score")
    return Summary(
        pairs,
        sum(score.hits for score in scored) / pairs,
        sum(score.loss for score in scored) / pairs,
        sum(score.brier for score in scored) / pairs,
        statistics.fmean(score.loss / score.pairs for score in scored),
    )


# Statistics


@dataclass(frozen=True)
class Difference:
    """Candidate minus baseline pair log loss, paired on the same pairs; below 0 is better."""

    delta: float
    low: float
    high: float
    p_value: float  # one-sided: the share of resamples where the candidate is not better


def paired_bootstrap(
    baseline: list[RaceScore],
    candidate: list[RaceScore],
    blocks: list[list[int]],
    *,
    resamples: int = RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Difference:
    """Resample whole blocks of races with replacement, keeping each race's pairs together.

    Pairs in a race share entrants, so a pair-level bootstrap would be far too
    narrow. The interval is the 2.5th to 97.5th percentile, as in mode_overlap.
    """
    differences = [sum(candidate[j].loss - baseline[j].loss for j in block) for block in blocks]
    pairs = [sum(baseline[j].pairs for j in block) for block in blocks]
    delta = sum(differences) / sum(pairs)
    rng = random.Random(seed)
    population = range(len(blocks))
    resampled = []
    for _ in range(resamples):
        picks = rng.choices(population, k=len(blocks))
        resampled.append(sum(differences[b] for b in picks) / sum(pairs[b] for b in picks))
    cuts = statistics.quantiles(resampled, n=40, method="inclusive")
    p_value = (sum(1 for value in resampled if value >= 0) + 1) / (resamples + 1)
    return Difference(delta, cuts[0], cuts[-1], p_value)


def race_blocks(scores: list[RaceScore]) -> list[list[int]]:
    """Each race with a decisive pair is its own block."""
    return [[position] for position, score in enumerate(scores) if score.pairs]


def week_blocks(records: list[JsonObject], indexes: list[int], scores: list[RaceScore]) -> list[list[int]]:
    """Group races by Eastern Time ISO week, which also keeps a week's regulars together."""
    weeks: dict[tuple[int, int], list[int]] = defaultdict(list)
    for position, (index, score) in enumerate(zip(indexes, scores)):
        if score.pairs:
            year, week, _day = date.fromisoformat(records[index]["d"]).isocalendar()
            weeks[(year, week)].append(position)
    return list(weeks.values())


def holm(p_values: dict[str, float]) -> dict[str, float]:
    """Return Holm-adjusted p-values, which control the chance of any false win."""
    adjusted = {}
    running = 0.0
    for rank, name in enumerate(sorted(p_values, key=p_values.get)):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[name]))
        adjusted[name] = running
    return adjusted


def clustered_se(baseline: list[RaceScore], candidate: list[RaceScore]) -> float:
    """Return the standard error of the paired difference in pair log loss, clustered by race.

    It is the race bootstrap's analytic counterpart: cheap and deterministic
    enough to run for every setting in a grid.
    """
    pairs = sum(score.pairs for score in baseline)
    differences = [c.loss - b.loss for b, c in zip(baseline, candidate)]
    delta = sum(differences) / pairs
    residuals = (d - delta * b.pairs for d, b in zip(differences, baseline))
    return math.sqrt(sum(r * r for r in residuals)) / pairs


def kendall_tau(x: list[float], y: list[float]) -> float | None:
    """Return Kendall's tau-b, which allows ties in either order, or None if undefined."""
    concordant = discordant = tied_x = tied_y = 0
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            dx = (x[i] > x[j]) - (x[i] < x[j])
            dy = (y[i] > y[j]) - (y[i] < y[j])
            tied_x += dx == 0
            tied_y += dy == 0
            concordant += dx * dy > 0
            discordant += dx * dy < 0
    total = len(x) * (len(x) - 1) // 2
    denominator = math.sqrt((total - tied_x) * (total - tied_y))
    return (concordant - discordant) / denominator if denominator else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


# The ladder's promises


@dataclass(frozen=True)
class Rules:
    """How a rating kept the ladder's promises over a run; each count should be 0."""

    winners: int
    winners_lost_points: int
    debuts_not_at_base: int
    changed_while_away: int
    times_change_result: bool

    @property
    def kept(self) -> bool:
        return not (
            self.winners_lost_points
            or self.debuts_not_at_base
            or self.changed_while_away
            or self.times_change_result
        )


def _with_seconds(records: list[JsonObject], seconds: Callable[[float | None], float | None]):
    return [
        {**record, "e": [(player, place, status, seconds(time)) for player, place, status, time in record["e"]]}
        for record in records
    ]


def check_rules(
    records: list[JsonObject],
    result: Result,
    rerun: Callable[[list[JsonObject]], Result],
    racer: Callable[[JsonObject, int], Any] = lambda record, player: player,
) -> Rules:
    """Check every promise over a run; `racer` keys a racer's state, per mode for a mode rating.

    `rerun` rates the same races again, used with every finish time removed and
    with every finish time replaced by noise: neither may change any state.
    """
    winners, lost, _move = ladder_rating._points_behaviour(records, result)
    last: dict[Any, State] = {}
    debuts = changed = 0
    for record, pre, post in zip(records, result.pre, result.post):
        for (player, *_rest), before, after in zip(record["e"], pre, post):
            key = racer(record, player)
            if key not in last:
                debuts += result.display(before) != BASE
            elif last[key] != before:
                changed += 1
            last[key] = after
    noise = random.Random(BOOTSTRAP_SEED)
    variants = (
        _with_seconds(records, lambda _time: None),
        _with_seconds(records, lambda _time: noise.uniform(600.0, 30_000.0)),
    )
    reruns = (rerun(variant) for variant in variants)
    times_matter = any((other.pre, other.post) != (result.pre, result.post) for other in reruns)
    return Rules(winners, lost, debuts, changed, times_matter)


def _by_mode_racer(record: JsonObject, player: int) -> tuple[int, int]:
    return (player, record["m"])


# Tuning


@dataclass(frozen=True)
class Setting:
    params: JsonObject
    log_loss: float  # pooled over the tuning folds
    fold_log_loss: tuple[float, ...]
    veteran_move: float | None
    scores: list[RaceScore] = field(repr=False, compare=False)


@dataclass(frozen=True)
class Tuning:
    family: str
    liveliness: float  # the shipped rating's veteran median move over the folds
    settings: list[Setting]  # every setting, lowest log loss first
    chosen: Setting
    best: Setting  # the lowest log loss among lively settings
    standard_error: float  # of the chosen setting's paired difference from `best`
    fold_best: tuple[JsonObject, ...]  # each fold's own best lively setting
    edges: list[str]


def _before(records: list[JsonObject], day: str) -> list[JsonObject]:
    """Return the records dated before `day`.

    They are a prefix: records are in start order and Eastern dates rise with
    it. The check makes sure of that rather than assuming it.
    """
    cut = next((index for index, record in enumerate(records) if record["d"] >= day), len(records))
    if any(record["d"] < day for record in records[cut:]):
        raise ValueError("Records are not in date order")
    return records[:cut]


def _veteran_moves(records: list[JsonObject], result: Result, selected: set[int]) -> list[int]:
    """Return the points moved per race by veterans, as `ladder_rating._points_behaviour` counts them."""
    races: Counter[int] = Counter()
    moves = []
    for index, (record, pre, post) in enumerate(zip(records, result.pre, result.post)):
        for (player, *_rest), before, after in zip(record["e"], pre, post):
            if index in selected and races[player] >= VETERAN_RACES:
                moves.append(abs(result.display(after) - result.display(before)))
            races[player] += 1
    return moves


def _edges(family: Family, params: JsonObject) -> list[str]:
    """Return the chosen values that sit on a grid edge.

    An axis the setting makes irrelevant, such as Elo's k0 without a
    provisional period, is never an edge.
    """
    edges = []
    for name, values in family.axes.items():
        if len({tuple(family.canonical({**params, name: value}).items()) for value in values}) == 1:
            continue
        if len(values) > 1 and params[name] == max(values):
            edges.append(f"{name}={params[name]:g} is the grid's largest")
        elif len(values) > 1 and params[name] == min(values) and name not in family.natural_minimum:
            edges.append(f"{name}={params[name]:g} is the grid's smallest")
    return edges


def tune(name: str, records: list[JsonObject], player_count: int) -> Tuning:
    """Pick a family's setting from races before OUT_OF_SAMPLE_START alone.

    `records` may run past the cut. They are cut here, first, so no later race
    is ever rated or scored.
    """
    family = FAMILIES[name]
    records = _before(records, OUT_OF_SAMPLE_START)
    folds = [
        [index for index, record in enumerate(records) if start <= record["d"] < end]
        for start, end in TUNING_FOLDS
    ]
    window = [index for fold in folds for index in fold]
    selected = set(window)
    liveliness = _median(_veteran_moves(records, rate_shipped(records, player_count), selected))
    if liveliness is None:
        raise ValueError("The tuning folds have no veteran races to measure liveliness")

    settings = []
    for params in family.grid():
        result = family.rate(records, player_count, **params)
        scores = race_scores(pair_chances(records, result, window))
        fold_scores = (scores[: len(folds[0])], scores[len(folds[0]) :])
        settings.append(
            Setting(
                params,
                summarise(scores).log_loss,
                tuple(summarise(fold).log_loss for fold in fold_scores),
                _median(_veteran_moves(records, result, selected)),
                scores,
            )
        )
    settings.sort(key=lambda setting: setting.log_loss)

    lively = [s for s in settings if s.veteran_move is not None and s.veteran_move >= liveliness]
    if not lively:
        raise ValueError(f"{name}: no setting is as lively as the shipped rating; extend the grid")
    best = lively[0]
    near = [
        setting
        for setting in lively
        if setting.log_loss - best.log_loss <= clustered_se(best.scores, setting.scores)
    ]
    chosen = min(near, key=lambda setting: (family.simplicity(setting.params), setting.log_loss))
    fold_best = tuple(
        min(lively, key=lambda setting: setting.fold_log_loss[fold]).params for fold in range(len(folds))
    )
    return Tuning(
        name,
        liveliness,
        settings,
        chosen,
        best,
        clustered_se(best.scores, chosen.scores),
        fold_best,
        _edges(family, chosen.params),
    )


def _format_params(params: JsonObject) -> str:
    return " ".join(f"{name}={value:g}" for name, value in params.items())


def format_tuning(tunings: list[Tuning]) -> str:
    lines = [
        f"Tuning on {TUNING_FOLDS[0][0]} to {TUNING_FOLDS[-1][1]} (before {OUT_OF_SAMPLE_START} only),"
        f" in folds {', '.join(f'{start}..{end}' for start, end in TUNING_FOLDS)}"
    ]
    for tuning in tunings:
        family = FAMILIES[tuning.family]
        lively = [s for s in tuning.settings if s.veteran_move is not None and s.veteran_move >= tuning.liveliness]
        unconstrained = tuning.settings[0]
        lines += [
            "",
            f"{family.label} ({tuning.family}): {len(tuning.settings)} settings,"
            f" {len(lively)} at least as lively as shipped (veteran median move >= {tuning.liveliness:g})",
            f"  chosen      {_format_params(tuning.chosen.params)}  log loss {tuning.chosen.log_loss:.4f}"
            f"  ({tuning.chosen.log_loss - tuning.best.log_loss:+.4f} from best, SE {tuning.standard_error:.4f})",
            f"  best lively {_format_params(tuning.best.params)}  log loss {tuning.best.log_loss:.4f}",
            f"  unconstrained best {_format_params(unconstrained.params)}  log loss {unconstrained.log_loss:.4f}"
            f"  veteran move {unconstrained.veteran_move}",
        ]
        for fold, params in enumerate(tuning.fold_best):
            lines.append(f"  fold {fold + 1} best {_format_params(params)}")
        stable = len({tuple(params.items()) for params in tuning.fold_best}) == 1
        lines.append(f"  folds {'agree' if stable else 'disagree'}")
        lines += [f"  EDGE: {edge}" for edge in tuning.edges]
        lines.append(f"  {'setting':<44}{'log loss':>9}{'fold 1':>8}{'fold 2':>8}{'move':>6}")
        for setting in lively[:10]:
            folds = "".join(f"{loss:>8.4f}" for loss in setting.fold_log_loss)
            lines.append(
                f"  {_format_params(setting.params):<44}{setting.log_loss:>9.4f}{folds}{setting.veteran_move:>6g}"
            )
    lines += ["", "CHOSEN = {"]
    lines += [f"    {tuning.family!r}: {tuning.chosen.params!r}," for tuning in tunings]
    lines.append("}")
    return "\n".join(lines)


# Comparison


@dataclass(frozen=True)
class Evaluated:
    """One candidate run over the whole archive and scored from OUT_OF_SAMPLE_START on."""

    name: str
    label: str
    short: str
    params: JsonObject
    result: Result
    mode_result: Result
    rules: Rules
    mode_rules: Rules
    chances: list[list[float]]
    scores: list[RaceScore]
    mode_scores: list[RaceScore]
    recalibration: float  # logistic slope on the logit of the chance, fitted before OUT_OF_SAMPLE_START


@dataclass(frozen=True)
class Evidence:
    """What the decision rule reads, for one candidate against one baseline."""

    eligible: bool
    difference: Difference  # the wider of the race and week bootstraps, with the larger p-value
    adjusted_p: float
    halves: tuple[float, float]
    race_log_loss_ok: bool

    @property
    def qualifies(self) -> bool:
        return (
            self.eligible
            and self.adjusted_p < SIGNIFICANCE
            and self.difference.delta <= -PRACTICAL_DELTA
            and all(half < 0 for half in self.halves)
            and self.race_log_loss_ok
        )


def verdict(versus_shipped: dict[str, Evidence], versus_retuned: dict[str, Evidence]) -> tuple[str, str | None]:
    """Return the pre-registered recommendation: ("switch", family), ("retune", RETUNED) or ("keep", None)."""
    switch = [
        name
        for name, evidence in versus_shipped.items()
        if name != RETUNED and evidence.qualifies and versus_retuned[name].qualifies
    ]
    if switch:
        return "switch", min(switch, key=lambda name: versus_shipped[name].difference.delta)
    if versus_shipped[RETUNED].qualifies:
        return "retune", RETUNED
    return "keep", None


def _logit(chance: float) -> float:
    return math.log(chance / (1 - chance))


def evaluate(
    name: str,
    label: str,
    short: str,
    rate: Callable[..., Result],
    params: JsonObject,
    records: list[JsonObject],
    player_count: int,
    evaluation: list[int],
    window: list[int],
) -> Evaluated:
    result = rate(records, player_count, **params)
    mode_result = by_mode(rate, records, player_count, **params)
    rules = check_rules(records, result, lambda variant: rate(variant, player_count, **params))
    mode_rules = check_rules(
        records,
        mode_result,
        lambda variant: by_mode(rate, variant, player_count, **params),
        _by_mode_racer,
    )
    chances = pair_chances(records, result, evaluation)
    tuning_gaps = [_logit(chance) for race in pair_chances(records, result, window) for chance in race]
    return Evaluated(
        name,
        label,
        short,
        params,
        result,
        mode_result,
        rules,
        mode_rules,
        chances,
        race_scores(chances),
        race_scores(pair_chances(records, mode_result, evaluation)),
        ladder_rating._logistic_slope(tuning_gaps),
    )


def _evidence(
    candidate: Evaluated,
    baseline: Evaluated,
    race_blocks_: list[list[int]],
    week_blocks_: list[list[int]],
    halves: tuple[list[int], list[int]],
) -> tuple[bool, Difference, tuple[float, float], bool]:
    by_race = paired_bootstrap(baseline.scores, candidate.scores, race_blocks_)
    by_week = paired_bootstrap(baseline.scores, candidate.scores, week_blocks_)
    wider = max(by_race, by_week, key=lambda difference: difference.high - difference.low)
    difference = Difference(wider.delta, wider.low, wider.high, max(by_race.p_value, by_week.p_value))
    half_deltas = tuple(
        (sum(candidate.scores[j].loss - baseline.scores[j].loss for j in half))
        / sum(baseline.scores[j].pairs for j in half)
        for half in halves
    )
    race_log_loss_ok = summarise(candidate.scores).race_log_loss <= summarise(baseline.scores).race_log_loss
    eligible = candidate.rules.kept and candidate.mode_rules.kept
    return eligible, difference, half_deltas, race_log_loss_ok


def _compare_all(
    candidates: list[Evaluated],
    baseline: Evaluated,
    race_blocks_: list[list[int]],
    week_blocks_: list[list[int]],
    halves: tuple[list[int], list[int]],
) -> dict[str, Evidence]:
    raw = {c.name: _evidence(c, baseline, race_blocks_, week_blocks_, halves) for c in candidates}
    adjusted = holm({name: parts[1].p_value for name, parts in raw.items()})
    return {
        name: Evidence(eligible, difference, adjusted[name], half_deltas, race_ok)
        for name, (eligible, difference, half_deltas, race_ok) in raw.items()
    }


def _calibration_error(chances: list[float]) -> float:
    """Return the expected calibration error over equal-count bins, from the favourite's side.

    Sorting by the favourite's chance alone keeps tied chances in input order,
    so the bins never sort wins apart from losses.
    """
    favourites = sorted(
        ((max(chance, 1 - chance), 1.0 if chance > 0.5 else 0.5 if chance == 0.5 else 0.0) for chance in chances),
        key=lambda favourite: favourite[0],
    )
    error = 0.0
    for bin_index in range(CALIBRATION_BINS):
        start = bin_index * len(favourites) // CALIBRATION_BINS
        end = (bin_index + 1) * len(favourites) // CALIBRATION_BINS
        chunk = favourites[start:end]
        if chunk:
            predicted = statistics.fmean(chance for chance, _won in chunk)
            observed = statistics.fmean(won for _chance, won in chunk)
            error += len(chunk) / len(favourites) * abs(predicted - observed)
    return error


def _prior_races(records: list[JsonObject]) -> list[list[int]]:
    """Return each entry's number of earlier races, in any mode."""
    races: Counter[int] = Counter()
    prior = []
    for record in records:
        prior.append([races[player] for player, *_rest in record["e"]])
        for player, *_rest in record["e"]:
            races[player] += 1
    return prior


def _bucket(value: int, buckets: tuple[tuple[int, int | None], ...]) -> str:
    for low, high in buckets:
        if value >= low and (high is None or value <= high):
            return f"{low}+" if high is None else f"{low}" if low == high else f"{low}-{high}"
    raise ValueError(f"No bucket for {value}")


def _segments(
    records: list[JsonObject], evaluation: list[int], prior: list[list[int]]
) -> list[list[tuple[str, str, str]]]:
    """Return each scored pair's experience, field-size and mode labels, aligned with `pair_chances`."""
    labels = []
    for index in evaluation:
        entries = records[index]["e"]
        field_size = _bucket(len(entries), FIELD_BUCKETS)
        labels.append(
            [
                (
                    _bucket(min(prior[index][winner], prior[index][loser]), EXPERIENCE_BUCKETS),
                    field_size,
                    records[index]["mode"],
                )
                for winner, loser in ladder_rating._decisive_pairs(entries)
            ]
        )
    return labels


def _race_order(records: list[JsonObject], result: Result, evaluation: list[int]) -> tuple[float, float, int]:
    """Return how often the favourite won and the mean tau-b of predicted against actual order.

    Only races with at least 3 entrants count. A racer's predicted strength is
    their summed win chance against the rest of the field. Non-finishers share
    last place.
    """
    favourite_won = []
    taus = []
    for index in evaluation:
        entries = records[index]["e"]
        if len(entries) < 3:
            continue
        pre = result.pre[index]
        strength = [
            sum(result.win_chance(pre[i], pre[q]) for q in range(len(entries)) if q != i)
            for i in range(len(entries))
        ]
        places = [place for _p, place, status, _s in entries if ladder_rating._is_finisher(place, status)]
        if not places:
            continue
        last = max(places) + 1
        standing = [
            -place if ladder_rating._is_finisher(place, status) else -last
            for _player, place, status, _seconds in entries
        ]
        top = max(strength)
        favourites = [i for i, value in enumerate(strength) if value == top]
        winners = [i for i, value in enumerate(standing) if value == -1]
        if winners:
            favourite_won.append(len(set(favourites) & set(winners)) / len(favourites))
        tau = kendall_tau(strength, standing)
        if tau is not None:
            taus.append(tau)
    return statistics.fmean(favourite_won), statistics.fmean(taus), len(taus)


@dataclass(frozen=True)
class Feel:
    """How the displayed points behave over the whole archive."""

    veteran_median: float | None
    veteran_p90: float | None
    below_par_gains: float  # share of below-par results (more pairs lost than won) whose points rose
    veteran_dnf_move: float | None  # median points change of a veteran's DNF
    veteran_last_move: float | None  # median points change of a veteran finishing last, no DNFs in the race
    leaderboard_tau: float | None  # final points against shipped, racers with LEADERBOARD_RACES+ races
    top_overlap: int  # racers shared by this and the shipped top LEADERBOARD_TOP


def _final_points(records: list[JsonObject], result: Result) -> dict[int, int]:
    points = {}
    for record, post in zip(records, result.post):
        for (player, *_rest), after in zip(record["e"], post):
            points[player] = result.display(after)
    return points


def _feel(records: list[JsonObject], result: Result, shipped: Result) -> Feel:
    moves = _veteran_moves(records, result, set(range(len(records))))
    races: Counter[int] = Counter()
    below_par = gains = 0
    dnf_moves = []
    last_moves = []
    for record, pre, post in zip(records, result.pre, result.post):
        entries = record["e"]
        won = Counter()
        lost = Counter()
        for winner, loser in ladder_rating._decisive_pairs(entries):
            won[winner] += 1
            lost[loser] += 1
        finishers = [ladder_rating._is_finisher(place, status) for _p, place, status, _s in entries]
        worst = max((entry[1] for entry, done in zip(entries, finishers) if done), default=None)
        for position, ((player, place, status, _seconds), before, after) in enumerate(zip(entries, pre, post)):
            change = result.display(after) - result.display(before)
            if lost[position] > won[position]:
                below_par += 1
                gains += change > 0
            if races[player] >= VETERAN_RACES:
                if not finishers[position]:
                    dnf_moves.append(change)
                elif all(finishers) and place == worst and len(entries) > 1:
                    last_moves.append(change)
            races[player] += 1
    counts = Counter(player for record in records for player, *_rest in record["e"])
    ours = _final_points(records, result)
    theirs = _final_points(records, shipped)
    regulars = sorted(player for player, count in counts.items() if count >= LEADERBOARD_RACES)

    def top(points: dict[int, int]) -> set[int]:
        return set(sorted(regulars, key=lambda player: (-points[player], player))[:LEADERBOARD_TOP])

    return Feel(
        _median(moves),
        statistics.quantiles(moves, n=10)[-1] if len(moves) > 1 else None,
        gains / below_par if below_par else 0.0,
        _median(dnf_moves),
        _median(last_moves),
        kendall_tau([theirs[p] for p in regulars], [ours[p] for p in regulars]),
        len(top(ours) & top(theirs)),
    )


def compare(
    records: list[JsonObject], player_count: int, racetime_scores: list[list[int]], chosen: dict[str, JsonObject]
) -> str:
    """Run the frozen comparison and return the report."""
    if set(chosen) != set(FAMILIES):
        raise ValueError(f"CHOSEN must name every family: {sorted(FAMILIES)}")
    evaluation = [index for index, record in enumerate(records) if record["d"] >= OUT_OF_SAMPLE_START]
    window = [
        index
        for index, record in enumerate(records)
        if TUNING_FOLDS[0][0] <= record["d"] < TUNING_FOLDS[-1][1]
    ]
    run = partial(evaluate, records=records, player_count=player_count, evaluation=evaluation, window=window)
    shipped = run("shipped", "ladder rating, shipped", "shipped", rate_bradley_terry_full, SHIPPED)
    candidates = [
        run(name, family.label, family.short, family.rate, chosen[name]) for name, family in FAMILIES.items()
    ]
    everyone = [shipped, *candidates]
    if len({summarise(e.scores).pairs for e in everyone}) != 1:
        raise AssertionError("Candidates were scored on different pairs")

    blocks_by_race = race_blocks(shipped.scores)
    blocks_by_week = week_blocks(records, evaluation, shipped.scores)
    halves = (
        [j for j, index in enumerate(evaluation) if records[index]["d"] < SECOND_HALF_START],
        [j for j, index in enumerate(evaluation) if records[index]["d"] >= SECOND_HALF_START],
    )
    versus_shipped = _compare_all(candidates, shipped, blocks_by_race, blocks_by_week, halves)
    retuned = next(c for c in candidates if c.name == RETUNED)
    challengers = [c for c in candidates if c.name != RETUNED]
    versus_retuned = _compare_all(challengers, retuned, blocks_by_race, blocks_by_week, halves)
    decision, winner = verdict(versus_shipped, versus_retuned)

    summaries = {e.name: summarise(e.scores) for e in everyone}
    lines = [
        f"Archive: {len(records):,} races to {records[-1]['d']};"
        f" scored from {OUT_OF_SAMPLE_START}: {len(evaluation):,} races,"
        f" {summaries['shipped'].pairs:,} pairs, {len(blocks_by_week)} weeks",
        "",
        "Chosen settings (frozen before this run)",
    ]
    for e in everyone:
        lines.append(f"  {e.short:<9}{e.label:<40}{_format_params(e.params)}")

    lines += [
        "",
        "Pair log loss from OUT_OF_SAMPLE_START, against shipped (95% CI: the wider of race and week bootstraps)",
        f"  {'':<9}{'accuracy':>9}{'log loss':>9}{'delta':>9}{'CI low':>9}{'CI high':>9}"
        f"{'Holm p':>8}{'per race':>9}{'Brier':>7}{'ECE':>7}{'recal.':>8}",
    ]
    for e in everyone:
        s = summaries[e.name]
        flat = [chance for race in e.chances for chance in race]
        recalibrated = statistics.fmean(ladder_rating._softplus(-e.recalibration * _logit(c)) for c in flat)
        if e.name == "shipped":
            comparison = f"{'':>9}{'':>9}{'':>9}{'':>8}"
        else:
            evidence = versus_shipped[e.name]
            d = evidence.difference
            comparison = f"{d.delta:>+9.4f}{d.low:>+9.4f}{d.high:>+9.4f}{evidence.adjusted_p:>8.3f}"
        lines.append(
            f"  {e.short:<9}{s.accuracy:>9.3f}{s.log_loss:>9.4f}{comparison}"
            f"{s.race_log_loss:>9.4f}{s.brier:>7.4f}{_calibration_error(flat):>7.4f}{recalibrated:>8.4f}"
        )
    racetime = ladder_rating._score_pairs(records, shipped.result, racetime_scores, evaluation)[1]
    lines += [
        f"  {'coin':<9}{0.5:>9.3f}{math.log(2):>9.4f}",
        f"  {'racetime':<9}{racetime.accuracy:>9.3f}{racetime.log_loss:>9.4f}  best-fit logistic, in-sample",
    ]

    lines += ["", "Each half of the period: delta against shipped", f"  {'':<9}{'Jan-Jun':>9}{'Jul-':>9}"]
    for e in candidates:
        first, second = versus_shipped[e.name].halves
        lines.append(f"  {e.short:<9}{first:>+9.4f}{second:>+9.4f}")

    lines += ["", f"New models against the retuned {FAMILIES[RETUNED].short}", f"  {'':<9}{'delta':>9}{'CI low':>9}{'CI high':>9}{'Holm p':>8}{'Jan-Jun':>9}{'Jul-':>9}"]
    for e in challengers:
        evidence = versus_retuned[e.name]
        d = evidence.difference
        lines.append(
            f"  {e.short:<9}{d.delta:>+9.4f}{d.low:>+9.4f}{d.high:>+9.4f}{evidence.adjusted_p:>8.3f}"
            f"{evidence.halves[0]:>+9.4f}{evidence.halves[1]:>+9.4f}"
        )

    lines += [
        "",
        "The ladder's promises over the whole archive (each count should be 0)",
        f"  {'':<9}{'scope':<9}{'winners':>8}{'lost pts':>9}{'debut!=1000':>12}{'moved away':>11}{'times':>6}  kept",
    ]
    for e in everyone:
        for scope, rules in (("overall", e.rules), ("modes", e.mode_rules)):
            lines.append(
                f"  {e.short:<9}{scope:<9}{rules.winners:>8,}{rules.winners_lost_points:>9}"
                f"{rules.debuts_not_at_base:>12}{rules.changed_while_away:>11}"
                f"{'yes' if rules.times_change_result else 'no':>6}  {'yes' if rules.kept else 'NO'}"
            )

    lines += ["", "Own-mode ratings, scored on the same pairs", f"  {'':<9}{'accuracy':>9}{'log loss':>9}"]
    for e in everyone:
        s = summarise(e.mode_scores)
        lines.append(f"  {e.short:<9}{s.accuracy:>9.3f}{s.log_loss:>9.4f}")

    prior = _prior_races(records)
    labels = _segments(records, evaluation, prior)
    header = "".join(f"{e.short:>9}" for e in everyone)
    for title, position, order in (
        ("prior races, lesser side", 0, [_bucket(low, EXPERIENCE_BUCKETS) for low, _high in EXPERIENCE_BUCKETS]),
        ("field size", 1, [_bucket(low, FIELD_BUCKETS) for low, _high in FIELD_BUCKETS]),
        ("mode", 2, sorted({label[2] for race in labels for label in race})),
    ):
        lines += ["", f"Pair log loss by {title}", f"  {'':<12}{'pairs':>7}{header}"]
        for segment in order:
            row = []
            count = 0
            for e in everyone:
                losses = [
                    -math.log(chance)
                    for race, race_labels in zip(e.chances, labels)
                    for chance, label in zip(race, race_labels)
                    if label[position] == segment
                ]
                count = len(losses)
                row.append(f"{statistics.fmean(losses):>9.4f}" if losses else f"{'--':>9}")
            lines.append(f"  {segment:<12}{count:>7,}{''.join(row)}")
    seasoned = []
    for e in everyone:
        losses = [
            -math.log(chance)
            for race, race_labels in zip(e.chances, labels)
            for chance, label in zip(race, race_labels)
            if label[0] != "0"
        ]
        seasoned.append(f"{statistics.fmean(losses):>9.4f}")
    lines.append(f"  {'no debuts':<12}{'':>7}{''.join(seasoned)}")

    lines += ["", "Race order (3+ entrants)", f"  {'':<9}{'fav. won':>9}{'tau-b':>7}"]
    for e in everyone:
        won, tau, _count = _race_order(records, e.result, evaluation)
        lines.append(f"  {e.short:<9}{won:>9.3f}{tau:>7.3f}")

    lines += [
        "",
        f"Displayed points over the whole archive (veterans: past race {VETERAN_RACES};"
        f" standings: {LEADERBOARD_RACES}+ races)",
        f"  {'':<9}{'vet med':>8}{'vet p90':>8}{'below-par gains':>16}{'vet DNF':>8}{'vet last':>9}"
        f"{'tau vs shipped':>15}{f'top {LEADERBOARD_TOP} shared':>14}",
    ]
    for e in everyone:
        feel = _feel(records, e.result, shipped.result)
        lines.append(
            f"  {e.short:<9}{_number(feel.veteran_median):>8}{_number(feel.veteran_p90):>8}"
            f"{feel.below_par_gains:>16.3f}{_number(feel.veteran_dnf_move):>8}{_number(feel.veteran_last_move):>9}"
            f"{_number(feel.leaderboard_tau, '.3f'):>15}{feel.top_overlap:>14}"
        )

    lines += ["", "Decision (pre-registered)"]
    for e in candidates:
        evidence = versus_shipped[e.name]
        half_width = (evidence.difference.high - evidence.difference.low) / 2
        notes = [
            "eligible" if evidence.eligible else "INELIGIBLE",
            f"Holm p {evidence.adjusted_p:.3f}",
            f"delta {evidence.difference.delta:+.4f}",
            "both halves better" if all(h < 0 for h in evidence.halves) else "not better in both halves",
            "per-race loss no worse" if evidence.race_log_loss_ok else "per-race loss worse",
            f"CI half-width {half_width:.4f}" + (" (underpowered)" if half_width > PRACTICAL_DELTA else ""),
        ]
        lines.append(f"  {e.short:<9}{'qualifies' if evidence.qualifies else 'does not qualify'}: {', '.join(notes)}")
    if decision == "switch":
        lines.append(f"Recommendation: switch to {FAMILIES[winner].label}")
    elif decision == "retune":
        lines.append(f"Recommendation: retune the ladder rating to {_format_params(chosen[RETUNED])}")
    else:
        lines.append("Recommendation: keep the shipped ladder rating")
        qualified = [name for name, evidence in versus_shipped.items() if name != RETUNED and evidence.qualifies]
        if qualified:
            lines.append(
                "  Note: " + ", ".join(FAMILIES[name].short for name in qualified)
                + " beat shipped but not the retuned model, and retuning alone does not clear the bar"
            )
    return "\n".join(lines)


def _number(value: float | None, spec: str = "g") -> str:
    return "--" if value is None else format(value, spec)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tune",
        action="store_true",
        help=f"pick each family's setting from races before {OUT_OF_SAMPLE_START} and print CHOSEN",
    )
    parser.add_argument("--family", choices=sorted(FAMILIES), help="with --tune, tune this family only")
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="raw race archive directory (default: the repository archive)",
    )
    args = parser.parse_args()
    if args.family and not args.tune:
        parser.error("--family needs --tune")
    return args


def main() -> int:
    args = parse_args()
    records, player_count, racetime_scores = ladder_rating._backtest_records(args.archive)
    if args.tune:
        names = [args.family] if args.family else list(FAMILIES)
        print(format_tuning([tune(name, records, player_count) for name in names]))
        return 0
    if not CHOSEN:
        raise SystemExit("CHOSEN is empty: run --tune, then freeze its choices in CHOSEN")
    # Tuning reads only races before OUT_OF_SAMPLE_START, so redoing it here
    # proves CHOSEN is what it picks without looking at any later race.
    print("Checking CHOSEN against --tune (a few minutes)...", file=sys.stderr)
    tuned = {name: tune(name, records, player_count).chosen.params for name in FAMILIES}
    stale = {name: params for name, params in tuned.items() if CHOSEN.get(name) != params}
    if stale:
        raise SystemExit(f"CHOSEN differs from what --tune picks: {stale}")
    print(compare(records, player_count, racetime_scores, CHOSEN))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
