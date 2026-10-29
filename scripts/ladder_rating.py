#!/usr/bin/env python3
"""Rate ladder racers from who beat whom, and backtest it against racetime.gg.

The model in plain words
------------------------
Only placement counts. Finish times are never read, so a win by one second and
a win by an hour are worth the same. Every racer has a skill estimate (a mean
mu) and an uncertainty (sigma). A newcomer starts at mu 25, sigma 8. One rating
covers every mode.

Within a race, every pair of entrants is a small match. A finisher with a
better place beats one with a worse place, and any finisher beats a DNF, DQ or
other non-finisher. Two non-finishers are skipped, and so are equal places,
so a shared win is still a win against everyone else. Each racer's rating moves by how much better or worse they did than their
skill predicted against each opponent, scaled by their own uncertainty, so
upsets and newcomers move the most. This is the Bayesian Bradley-Terry
"full pair" model of Weng and Lin (2011), the method behind OpenSkill. All
racers in a race are updated at once from their pre-race states.

Recency comes from race count, not the calendar. Uncertainty shrinks with every
race but never below SIGMA_FLOOR, which keeps every race's update above a
minimum: a race's weight halves after about 13 later races. Nothing decays
while a racer is away.

The displayed points are BASE + SCALE * (mu - K * sigma), the "conservative
rating" of TrueSkill and Xbox levels. K is MU0 / SIGMA0, so every newcomer
shows exactly BASE and climbs as the ladder learns about them. SCALE makes 400
points about 10:1 odds between established racers, as in Elo. The win chance of
racer a over b is 1 / (1 + exp(-(mu_a - mu_b) / sqrt(s_a^2 + s_b^2 + 2 BETA^2))).
Points are never used to predict.

The guarantee
-------------
A winner's points never fall. Every pair a winner is in is decided in their
favour, so their mean rises; sigma never rises, so mu - K * sigma rises too, and
rounding is monotone. Finish times have no effect at all. Tests enforce both.

Why not racetime.gg's rating
----------------------------
racetime.gg (`racetime/rating.py`, `models/user.py`) runs TrueSkill with default
parameters on finishing order only, ties every DNF and DQ behind the last
finisher, and shows max(0, round((mu - 2 sigma) * 100)), so newcomers start at
833 and the score climbs with volume as sigma shrinks. It keeps one leaderboard
per goal. This model also reads placement only, but it shows a different
scale, rates all modes together, and never lets a winner lose points.

Backtest and tuning
-------------------
`python3 scripts/ladder_rating.py --backtest` replays the archive in order and,
before each race, predicts every decisive entrant pair: finishers by place, and
a finisher beats a non-finisher; two non-finishers are skipped. racetime.gg is
scored from the pre-race `score` each entrant carried, which the archive stores
(833 when missing). Its log loss uses the best-fit logistic curve on the score
gap, fitted on the same pairs, which flatters it. The report also counts
winners whose displayed points fell (the guarantee says none) and the median
points a veteran (past their 50th race) moves per race.

BETA and SIGMA0 were picked by log loss on races before OUT_OF_SAMPLE_START
only (the archive starts 2025-04-20), over BETA in {8, 10, 12} and SIGMA0 in
{6, 8}. SIGMA_FLOOR was not tuned for accuracy: the user chose a "livelier"
ladder, where a veteran's win is worth about +14 points and last place or a DNF
about -13, over a calmer floor that predicts a little better. Accuracy and log
loss therefore trail the old margin model slightly, in exchange for no lost
winners.

Result on the archive as of 2026-10-27:

                                         pairs  accuracy  log loss
    From 2026-01-01 (out of sample)
      ladder rating                     46,247     0.722     0.543
      racetime.gg recorded score        46,247     0.694     0.588  best-fit logistic, in-sample
    Before 2026-01-01 (tuning period)
      ladder rating                     26,796     0.703     0.562
      racetime.gg recorded score        26,796     0.706     0.573  best-fit logistic, in-sample
    Winners who lost points: 0 of 3,220
    Median veteran move per race (past race 50): 9 points
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BETA = 12.0  # per-match performance noise, in skill units
SIGMA0 = 8.0  # a newcomer's starting uncertainty
SIGMA_FLOOR = 4.0  # uncertainty never shrinks below this
KAPPA = 1e-4  # smallest share of its variance a racer keeps after one race
MU0 = 25.0  # a newcomer's starting skill
BASE = 1000
K = MU0 / SIGMA0  # makes a newcomer's points exactly BASE
SCALE = 400 / (math.log(10) * math.sqrt(2 * SIGMA_FLOOR**2 + 2 * BETA**2))
PROVISIONAL_FINISHES = 5
VETERAN_RACES = 50
DONE_STATUS = 0

RACETIME_NEW_SCORE = 833
OUT_OF_SAMPLE_START = "2026-01-01"

JsonObject = dict[str, Any]
State = tuple[float, float]


def _is_finisher(place: int | None, status: int) -> bool:
    return status == DONE_STATUS and place is not None


def _outcome(first: Any, second: Any) -> float | None:
    """Return the score of `first` against `second`: 1, 0, or None if undecided.

    Equal places are undecided rather than a draw, so co-winners only ever collect wins.
    """
    _player, place_a, status_a, _seconds = first
    _player, place_b, status_b, _seconds = second
    finished_a = _is_finisher(place_a, status_a)
    finished_b = _is_finisher(place_b, status_b)
    if finished_a and finished_b:
        return 1.0 if place_a < place_b else 0.0 if place_b < place_a else None
    if finished_a:
        return 1.0
    if finished_b:
        return 0.0
    return None


def _win_chance(a: State, b: State, beta: float) -> float:
    (mean_a, variance_a), (mean_b, variance_b) = a, b
    spread = math.sqrt(variance_a + variance_b + 2 * beta * beta)
    return 1 / (1 + math.exp(-(mean_a - mean_b) / spread))


@dataclass(frozen=True)
class PlayerRating:
    rating: int
    mu: float
    sigma: float
    finishes: int
    provisional: bool


@dataclass(frozen=True)
class RatingResult:
    """Per-entry states before and after each race, plus each racer's final rating.

    States are `(mean, variance)` in skill units, where a higher mean is better.
    """

    pre: list[list[State]]
    post: list[list[State]]
    players: list[PlayerRating | None]
    params: JsonObject

    def display(self, state: State) -> int:
        """Return the points for a `(mean, variance)` state."""
        mean, variance = state
        params = self.params
        return round(params["base"] + params["scale"] * (mean - params["k"] * math.sqrt(variance)))

    def win_chance(self, a: State, b: State) -> float:
        """Return the chance that a racer in state `a` beats one in state `b`."""
        return _win_chance(a, b, self.params["beta"])


def _update_race(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    kappa: float,
) -> None:
    """Update the `[mean, variance]` states in place, every racer from the pre-race states."""
    old = [(mean, variance) for mean, variance in states]
    floor_variance = sigma_floor * sigma_floor
    for i, (mean_i, variance_i) in enumerate(old):
        omega = 0.0
        delta = 0.0
        for q, (mean_q, variance_q) in enumerate(old):
            if q == i:
                continue
            score = _outcome(entries[i], entries[q])
            if score is None:
                continue
            c = math.sqrt(variance_i + variance_q + 2 * beta * beta)
            chance = 1 / (1 + math.exp((mean_q - mean_i) / c))
            omega += variance_i / c * (score - chance)
            delta += math.sqrt(variance_i) / c * variance_i / (c * c) * chance * (1 - chance)
        # Uncertainty only ever shrinks, and not below the floor.
        shrunk = max(variance_i * max(1 - delta, kappa), floor_variance)
        states[i][0] = mean_i + omega
        states[i][1] = min(variance_i, shrunk)


def rate(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float = BETA,
    sigma0: float = SIGMA0,
    sigma_floor: float = SIGMA_FLOOR,
    kappa: float = KAPPA,
) -> RatingResult:
    """Rate every racer over chronological C1 race records.

    The keyword arguments exist for tuning and tests; the module constants are
    the published parameters.
    """
    means: list[float | None] = [None] * player_count
    variances = [0.0] * player_count
    finishes = [0] * player_count
    pre: list[list[State]] = []
    post: list[list[State]] = []

    for record in records:
        entries = record["e"]
        for player, *_rest in entries:
            if means[player] is None:
                means[player] = MU0
                variances[player] = sigma0 * sigma0

        players = [entry[0] for entry in entries]
        pre.append([(means[player], variances[player]) for player in players])
        states = [[means[player], variances[player]] for player in players]
        _update_race(states, entries, beta=beta, sigma_floor=sigma_floor, kappa=kappa)
        post.append([(mean, variance) for mean, variance in states])

        for (player, place, status, _seconds), (mean, variance) in zip(entries, states):
            means[player] = mean
            variances[player] = variance
            if _is_finisher(place, status):
                finishes[player] += 1

    params = {
        "model": "bradley-terry",
        "base": BASE,
        "scale": SCALE,
        "k": MU0 / sigma0,
        "mu0": MU0,
        "beta": beta,
        "sigma0": sigma0,
        "sigma_floor": sigma_floor,
        "kappa": kappa,
        "provisional_finishes": PROVISIONAL_FINISHES,
    }
    result = RatingResult(pre=pre, post=post, players=[], params=params)
    players = [
        None
        if means[player] is None
        else PlayerRating(
            rating=result.display((means[player], variances[player])),
            mu=round(means[player], 4),
            sigma=round(math.sqrt(variances[player]), 4),
            finishes=finishes[player],
            provisional=finishes[player] < PROVISIONAL_FINISHES,
        )
        for player in range(player_count)
    ]
    return RatingResult(pre=pre, post=post, players=players, params=params)


@dataclass(frozen=True)
class PairScore:
    pairs: int
    accuracy: float
    log_loss: float


def _decisive_pairs(entries: list[Any]) -> list[tuple[int, int]]:
    """Return (winner, loser) entry positions for every pair with a clear result.

    Two finishers are ordered by place; a finisher beats a non-finisher; two
    non-finishers, or two finishers on the same place, are skipped.
    """
    pairs = []
    for first in range(len(entries)):
        _player, place_a, status_a, _seconds = entries[first]
        for second in range(first + 1, len(entries)):
            _player, place_b, status_b, _seconds = entries[second]
            finished_a = _is_finisher(place_a, status_a)
            finished_b = _is_finisher(place_b, status_b)
            if finished_a and finished_b:
                if place_a < place_b:
                    pairs.append((first, second))
                elif place_b < place_a:
                    pairs.append((second, first))
            elif finished_a:
                pairs.append((first, second))
            elif finished_b:
                pairs.append((second, first))
    return pairs


def _hit(chance: float) -> float:
    return 1.0 if chance > 0.5 else 0.5 if chance == 0.5 else 0.0


def _softplus(x: float) -> float:
    """log(1 + exp(x)) without overflow."""
    return max(x, 0.0) + math.log1p(math.exp(-abs(x)))


def _logistic_slope(gaps: list[float]) -> float:
    """Return the k minimizing the mean log loss of P(win) = 1 / (1 + exp(-k * gap)).

    The loss is convex and its curvature falls as |k| grows, so Newton's method
    from 0 approaches the optimum monotonically without overshooting.
    """
    slope = 0.0
    for _ in range(100):
        gradient = 0.0
        curvature = 0.0
        for gap in gaps:
            win = 0.5 * (1 + math.tanh(0.5 * slope * gap))
            gradient -= gap * (1 - win)
            curvature += gap * gap * win * (1 - win)
        if curvature <= 0:
            break
        step = gradient / curvature
        slope -= step
        if abs(step) <= 1e-12 * max(abs(slope), 1e-12):
            break
    return slope


def _score_pairs(
    records: list[JsonObject],
    result: RatingResult,
    scores: list[list[int]],
    selected: list[int],
) -> tuple[PairScore, PairScore]:
    """Score this rating and racetime's recorded scores on the selected races' pairs."""
    hits = 0.0
    loss = 0.0
    racetime_hits = 0.0
    gaps = []
    for race_index in selected:
        pre = result.pre[race_index]
        race_scores = scores[race_index]
        for winner, loser in _decisive_pairs(records[race_index]["e"]):
            chance = result.win_chance(pre[winner], pre[loser])
            hits += _hit(chance)
            loss -= math.log(chance)
            gap = race_scores[winner] - race_scores[loser]
            racetime_hits += 1.0 if gap > 0 else 0.5 if gap == 0 else 0.0
            gaps.append(gap)
    count = len(gaps)
    if not count:
        raise ValueError("The backtest period has no decisive entrant pairs")
    slope = _logistic_slope(gaps)
    racetime_loss = sum(_softplus(-slope * gap) for gap in gaps) / count
    return (
        PairScore(count, hits / count, loss / count),
        PairScore(count, racetime_hits / count, racetime_loss),
    )


def _backtest_records(
    archive_dir: Path | None,
) -> tuple[list[JsonObject], int, list[list[int]]]:
    """Return C1 records for every included archived race, the racer count, and
    each entry's racetime.gg pre-race score.

    Seconds are whole (as `participant_stats` rounds them), which is close enough
    for a backtest.
    """
    # Imported here, not at module level, so the pipeline can import this module
    # without an import cycle. Supports both `python3 scripts/ladder_rating.py`
    # and `from scripts import ladder_rating`.
    try:
        from scripts import build_stats, participant_stats, race_archive
    except ImportError:
        import build_stats
        import participant_stats
        import race_archive

    if archive_dir is None:
        archive_dir = race_archive.ARCHIVE_DIR
    races = [envelope["race"] for envelope in race_archive.load_races(archive_dir)]
    contexts = sorted(
        build_stats.included_race_contexts(races),
        key=lambda context: (context["started_at"], context["race"].get("name") or ""),
    )
    modes = sorted({context["mode"] for context in contexts})
    mode_index = {mode: index for index, mode in enumerate(modes)}
    player_index: dict[str, int] = {}
    diagnostics: Counter[str] = Counter()
    records = []
    scores = []
    for context in contexts:
        race = context["race"]
        recorded = {
            entrant["user"].get("id"): entrant.get("score")
            for entrant in race["entrants"]
            if isinstance(entrant.get("user"), dict)
        }
        entries = []
        race_scores = []
        for user_id, _name, place, status, seconds in participant_stats._race_entries(
            race, diagnostics
        ):
            player = player_index.setdefault(user_id, len(player_index))
            entries.append((player, place, status, seconds))
            score = recorded.get(user_id)
            race_scores.append(RACETIME_NEW_SCORE if score is None else score)
        records.append(
            {
                "r": race.get("name"),
                "m": mode_index[context["mode"]],
                "mode": context["mode"],
                "d": context["local_started_at"].date().isoformat(),
                "h": context["local_started_at"].hour,
                "n": len(race["entrants"]),
                "p": None,
                "e": entries,
            }
        )
        scores.append(race_scores)
    return records, len(player_index), scores


@dataclass(frozen=True)
class BacktestReport:
    """Pair scores as `(ours, racetime)`, plus how the displayed points behaved."""

    evaluation: tuple[PairScore, PairScore]
    tuning: tuple[PairScore, PairScore]
    winners: int
    winners_lost_points: int
    veteran_move: float | None  # median absolute points moved per race past the VETERAN_RACES-th


def _points_behaviour(records: list[JsonObject], result: RatingResult) -> tuple[int, int, float | None]:
    """Return (winners, winners whose points fell, median veteran move)."""
    races = Counter()
    winners = 0
    lost = 0
    veteran_moves = []
    for record, pre, post in zip(records, result.pre, result.post):
        for (player, place, status, _seconds), before, after in zip(record["e"], pre, post):
            change = result.display(after) - result.display(before)
            if _is_finisher(place, status) and place == 1:
                winners += 1
                lost += change < 0
            if races[player] >= VETERAN_RACES:
                veteran_moves.append(abs(change))
            races[player] += 1
    median = statistics.median(veteran_moves) if veteran_moves else None
    return winners, lost, median


def backtest(archive_dir: Path | None = None) -> BacktestReport:
    """Predict every decisive entrant pair from pre-race ratings, chronologically.

    "evaluation" is the races from OUT_OF_SAMPLE_START on and "tuning" is the
    races before it, each as `(ours, racetime)`. Racetime's log loss uses the
    best-fit logistic curve on its score gap, fitted on the same pairs
    (in-sample, so it flatters racetime).
    """
    records, player_count, scores = _backtest_records(archive_dir)
    result = rate(records, player_count)
    tuning = [index for index, record in enumerate(records) if record["d"] < OUT_OF_SAMPLE_START]
    evaluation = [index for index, record in enumerate(records) if record["d"] >= OUT_OF_SAMPLE_START]
    winners, lost, veteran_move = _points_behaviour(records, result)
    return BacktestReport(
        evaluation=_score_pairs(records, result, scores, evaluation),
        tuning=_score_pairs(records, result, scores, tuning),
        winners=winners,
        winners_lost_points=lost,
        veteran_move=veteran_move,
    )


def _format_report(report: BacktestReport) -> str:
    lines = [f"{'':34}{'pairs':>8}{'accuracy':>10}{'log loss':>10}"]
    sections = (
        (report.evaluation, f"From {OUT_OF_SAMPLE_START} (out of sample)"),
        (report.tuning, f"Before {OUT_OF_SAMPLE_START} (tuning period)"),
    )
    for (ours, racetime), title in sections:
        lines.append(title)
        for label, score, note in (
            ("ladder rating", ours, ""),
            ("racetime.gg recorded score", racetime, "  best-fit logistic, in-sample"),
        ):
            lines.append(
                f"  {label:<32}{score.pairs:>8,}{score.accuracy:>10.3f}"
                f"{score.log_loss:>10.3f}{note}"
            )
    lines.append(
        f"Winners who lost points: {report.winners_lost_points:,} of {report.winners:,}"
    )
    move = "n/a" if report.veteran_move is None else f"{report.veteran_move:g}"
    lines.append(f"Median veteran move per race (past race {VETERAN_RACES}): {move} points")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="compare pairwise predictions with racetime.gg's recorded scores",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="raw race archive directory (default: the repository archive)",
    )
    args = parser.parse_args()
    if not args.backtest:
        parser.error("nothing to do; pass --backtest")
    return args


def main() -> int:
    args = parse_args()
    print(_format_report(backtest(args.archive)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
