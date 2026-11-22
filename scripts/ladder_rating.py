#!/usr/bin/env python3
"""Rate ladder racers from who beat whom, and backtest it against racetime.gg.

The spec is README.md, under "Ladder rating": the model (the Bayesian
Bradley-Terry "full pair" model of Weng and Lin, 2011), recency, the parameters
and their tuning, the points scale, the guarantee that a winner's points never
fall, the mode and season ratings, and the backtest. Change that section with
any behaviour change here. This docstring only maps the spec onto the code.

- `rate` is the overall rating. `rate_by_mode` runs the same model and
  parameters on each mode's races alone, and `rate_by_season` on each season's
  races, overall and per mode.
- `_update_race` updates every entrant of a race at once from their pre-race
  states, scaled by `_damping`, (entrants - 1) ** -ALPHA.
- `_win_chance` reads mu and sigma, never the displayed points.
- `backtest` replays the archive and scores the pairs from OUT_OF_SAMPLE_START
  apart from the tuning period. racetime.gg's own rating (`racetime/rating.py`
  and `models/user.py` in its source) is scored from each entrant's stored
  `score`.

BETA, SIGMA0 and ALPHA were picked by `rating_lab.py` from races before
OUT_OF_SAMPLE_START only. Never retune on later races. A veteran's median win
is worth +10 points, finishing last in a race without DNFs -9, and a DNF -12.

Result on the archive as of 2026-11-01:

                                         pairs  accuracy  log loss
    From 2026-01-01 (out of sample)
      ladder rating                     47,576     0.728     0.534
      ladder rating, own mode only      47,576     0.720     0.547
      racetime.gg recorded score        47,576     0.695     0.587  best-fit logistic, in-sample
    Before 2026-01-01 (tuning period)
      ladder rating                     26,796     0.707     0.555
      ladder rating, own mode only      26,796     0.687     0.580
      racetime.gg recorded score        26,796     0.706     0.573  best-fit logistic, in-sample
    Winners who lost points: 0 of 3,258
    Mode winners who lost points: 0 of 3,258
    Median veteran move per race (past race 50): 8 points
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BETA = 8.0  # per-match performance noise, in skill units
SIGMA0 = 12.0  # a newcomer's starting uncertainty
SIGMA_FLOOR = 4.0  # uncertainty never shrinks below this
ALPHA = 0.5  # field-size damping: a race's update is scaled by (entrants - 1) ** -ALPHA
KAPPA = 1e-4  # smallest share of its variance a racer keeps after one race
MU0 = 25.0  # a newcomer's starting skill
BASE = 1000
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


def _display(state: State, params: JsonObject) -> int:
    """Return the points of a `(mean, variance)` state: a conservative, rounded rating."""
    mean, variance = state
    return round(params["base"] + params["scale"] * (mean - params["k"] * math.sqrt(variance)))


@dataclass(frozen=True)
class _Ratings:
    """Per-entry states before and after each race, and the points formula.

    States are `(mean, variance)` in skill units, where a higher mean is better.
    """

    pre: list[list[State]]
    post: list[list[State]]
    params: JsonObject

    def display(self, state: State) -> int:
        """Return the points for a `(mean, variance)` state."""
        return _display(state, self.params)

    def win_chance(self, a: State, b: State) -> float:
        """Return the chance that a racer in state `a` beats one in state `b`."""
        return _win_chance(a, b, self.params["beta"])


@dataclass(frozen=True)
class RatingResult(_Ratings):
    """One rating across every mode, plus each racer's final rating."""

    players: list[PlayerRating | None]


@dataclass(frozen=True)
class ModeRatings(_Ratings):
    """Each mode rated on its own races alone, with the same model and parameters.

    `pre` and `post` line up with the input records, and hold each entry's state
    in that race's own mode. `players` maps a mode index to every racer's final
    rating in that mode, or None for a racer who never raced it.
    """

    players: dict[int, list[PlayerRating | None]]


@dataclass(frozen=True)
class SeasonRatings(_Ratings):
    """Each season rated on its own races alone, overall and in each mode.

    `pre` and `post` line up with the input records and hold each entry's state in
    the race's season; `mode_pre` and `mode_post` hold it in the race's season and
    mode. A record outside every season has empty lists in all four.
    """

    mode_pre: list[list[State]]
    mode_post: list[list[State]]

    def points(self) -> list[list[tuple[int, int]]]:
        """Each entry's season points and season-mode points after its race, per record."""
        return [
            [(self.display(after), self.display(mode_after)) for after, mode_after in zip(post, mode_post)]
            for post, mode_post in zip(self.post, self.mode_post)
        ]


def _damping(entries: list[Any], alpha: float) -> float:
    """Return the scale of one race's update, (entrants - 1) ** -alpha; 1 with alpha 0."""
    return max(len(entries) - 1, 1) ** -alpha


def _update_race(
    states: list[list[float]],
    entries: list[Any],
    *,
    beta: float,
    sigma_floor: float,
    kappa: float,
    alpha: float,
) -> None:
    """Update the `[mean, variance]` states in place, every racer from the pre-race states.

    Every pair counts as a match, but a race's pairs share their racers, so the
    whole update is damped by the field size: without it a 32-racer race would
    count as 31 independent results.
    """
    damping = _damping(entries, alpha)
    old = [(mean, variance) for mean, variance in states]
    floor_variance = sigma_floor * sigma_floor
    two_beta_sq = 2 * beta * beta
    sqrt_variances = [math.sqrt(variance) for _mean, variance in old]
    omegas = [0.0] * len(old)
    deltas = [0.0] * len(old)
    # Each unordered pair is decided once and feeds both racers. With `i` ascending
    # outer and `q` ascending inner, every racer still meets its partners in ascending
    # order, so the float sums match a loop over ordered pairs bit for bit. The two
    # chances keep their own exp calls: `1 - chance` would differ in the low bits.
    for i, (mean_i, variance_i) in enumerate(old):
        for q in range(i + 1, len(old)):
            score = _outcome(entries[i], entries[q])
            if score is None:
                continue
            mean_q, variance_q = old[q]
            c = math.sqrt(variance_i + variance_q + two_beta_sq)
            chance_i = 1 / (1 + math.exp((mean_q - mean_i) / c))
            chance_q = 1 / (1 + math.exp((mean_i - mean_q) / c))
            omegas[i] += variance_i / c * (score - chance_i)
            deltas[i] += sqrt_variances[i] / c * variance_i / (c * c) * chance_i * (1 - chance_i)
            omegas[q] += variance_q / c * ((1.0 - score) - chance_q)
            deltas[q] += sqrt_variances[q] / c * variance_q / (c * c) * chance_q * (1 - chance_q)
    for i, (mean_i, variance_i) in enumerate(old):
        # Uncertainty only ever shrinks, and not below the floor.
        shrunk = max(variance_i * max(1 - damping * deltas[i], kappa), floor_variance)
        states[i][0] = mean_i + damping * omegas[i]
        states[i][1] = min(variance_i, shrunk)


def _params(*, beta: float, sigma0: float, sigma_floor: float, kappa: float, alpha: float) -> JsonObject:
    """Return the published parameters, shared by the overall and the mode ratings."""
    return {
        "model": "bradley-terry",
        "base": BASE,
        "scale": SCALE,
        "k": MU0 / sigma0,
        "mu0": MU0,
        "beta": beta,
        "sigma0": sigma0,
        "sigma_floor": sigma_floor,
        "kappa": kappa,
        "alpha": alpha,
        "provisional_finishes": PROVISIONAL_FINISHES,
    }


def rate(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float = BETA,
    sigma0: float = SIGMA0,
    sigma_floor: float = SIGMA_FLOOR,
    kappa: float = KAPPA,
    alpha: float = ALPHA,
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
        _update_race(states, entries, beta=beta, sigma_floor=sigma_floor, kappa=kappa, alpha=alpha)
        post.append([(mean, variance) for mean, variance in states])

        for (player, place, status, _seconds), (mean, variance) in zip(entries, states):
            means[player] = mean
            variances[player] = variance
            if _is_finisher(place, status):
                finishes[player] += 1

    params = _params(beta=beta, sigma0=sigma0, sigma_floor=sigma_floor, kappa=kappa, alpha=alpha)
    players = [
        None
        if means[player] is None
        else PlayerRating(
            rating=_display((means[player], variances[player]), params),
            mu=round(means[player], 4),
            sigma=round(math.sqrt(variances[player]), 4),
            finishes=finishes[player],
        )
        for player in range(player_count)
    ]
    return RatingResult(pre=pre, post=post, players=players, params=params)


def rate_by_mode(
    records: list[JsonObject],
    player_count: int,
    *,
    beta: float = BETA,
    sigma0: float = SIGMA0,
    sigma_floor: float = SIGMA_FLOOR,
    kappa: float = KAPPA,
    alpha: float = ALPHA,
) -> ModeRatings:
    """Rate every racer separately in each mode, over chronological C1 race records.

    Each mode is `rate` run on that mode's races alone, so a racer debuts at the
    starting rating in every mode they enter.
    """
    pre: list[list[State]] = [[] for _ in records]
    post: list[list[State]] = [[] for _ in records]
    players: dict[int, list[PlayerRating | None]] = {}
    for mode in sorted({record["m"] for record in records}):
        indexes = [index for index, record in enumerate(records) if record["m"] == mode]
        result = rate(
            [records[index] for index in indexes],
            player_count,
            beta=beta,
            sigma0=sigma0,
            sigma_floor=sigma_floor,
            kappa=kappa,
            alpha=alpha,
        )
        for index, race_pre, race_post in zip(indexes, result.pre, result.post):
            pre[index] = race_pre
            post[index] = race_post
        players[mode] = result.players
    params = _params(beta=beta, sigma0=sigma0, sigma_floor=sigma_floor, kappa=kappa, alpha=alpha)
    return ModeRatings(pre=pre, post=post, players=players, params=params)


def rate_by_season(
    records: list[JsonObject],
    player_count: int,
    seasons: list[int | None],
    *,
    beta: float = BETA,
    sigma0: float = SIGMA0,
    sigma_floor: float = SIGMA_FLOOR,
    kappa: float = KAPPA,
    alpha: float = ALPHA,
) -> SeasonRatings:
    """Rate every racer separately in each season, overall and in each mode.

    `seasons` gives each record's season, or None for a race outside every season.
    Each season is `rate` and `rate_by_mode` run on that season's races alone, so a
    racer debuts at the starting rating in every season, and in every mode of it.
    """
    if len(seasons) != len(records):
        raise ValueError("Every record needs a season, or None")
    tuning = {"beta": beta, "sigma0": sigma0, "sigma_floor": sigma_floor, "kappa": kappa, "alpha": alpha}
    pre: list[list[State]] = [[] for _ in records]
    post: list[list[State]] = [[] for _ in records]
    mode_pre: list[list[State]] = [[] for _ in records]
    mode_post: list[list[State]] = [[] for _ in records]
    for season in sorted({season for season in seasons if season is not None}):
        indexes = [index for index, number in enumerate(seasons) if number == season]
        chosen = [records[index] for index in indexes]
        overall = rate(chosen, player_count, **tuning)
        by_mode = rate_by_mode(chosen, player_count, **tuning)
        for position, index in enumerate(indexes):
            pre[index] = overall.pre[position]
            post[index] = overall.post[position]
            mode_pre[index] = by_mode.pre[position]
            mode_post[index] = by_mode.post[position]
    return SeasonRatings(
        pre=pre,
        post=post,
        mode_pre=mode_pre,
        mode_post=mode_post,
        params=_params(**tuning),
    )


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
    result: _Ratings,
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
        from scripts import build_stats, participant_stats, race_archive, schedule_archive
    except ImportError:
        import build_stats
        import participant_stats
        import race_archive
        import schedule_archive

    if archive_dir is None:
        archive_dir = race_archive.ARCHIVE_DIR
    races = [envelope["race"] for envelope in race_archive.load_races(archive_dir)]
    spans = schedule_archive.season_spans(schedule_archive.load_schedule(archive_dir))
    contexts = sorted(
        build_stats.included_race_contexts(races, spans),
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
    mode_evaluation: PairScore  # the per-mode rating, on the same pairs as `evaluation`
    mode_tuning: PairScore
    mode_winners: int
    mode_winners_lost_points: int
    veteran_move: float | None  # median absolute points moved per race past the VETERAN_RACES-th


def _points_behaviour(records: list[JsonObject], result: _Ratings) -> tuple[int, int, float | None]:
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
    mode_result = rate_by_mode(records, player_count)
    tuning = [index for index, record in enumerate(records) if record["d"] < OUT_OF_SAMPLE_START]
    evaluation = [index for index, record in enumerate(records) if record["d"] >= OUT_OF_SAMPLE_START]
    winners, lost, veteran_move = _points_behaviour(records, result)
    mode_winners, mode_lost, _mode_veteran_move = _points_behaviour(records, mode_result)
    return BacktestReport(
        evaluation=_score_pairs(records, result, scores, evaluation),
        tuning=_score_pairs(records, result, scores, tuning),
        winners=winners,
        winners_lost_points=lost,
        mode_evaluation=_score_pairs(records, mode_result, scores, evaluation)[0],
        mode_tuning=_score_pairs(records, mode_result, scores, tuning)[0],
        mode_winners=mode_winners,
        mode_winners_lost_points=mode_lost,
        veteran_move=veteran_move,
    )


def _format_report(report: BacktestReport) -> str:
    lines = [f"{'':34}{'pairs':>8}{'accuracy':>10}{'log loss':>10}"]
    sections = (
        (report.evaluation, report.mode_evaluation, f"From {OUT_OF_SAMPLE_START} (out of sample)"),
        (report.tuning, report.mode_tuning, f"Before {OUT_OF_SAMPLE_START} (tuning period)"),
    )
    for (ours, racetime), mode_score, title in sections:
        lines.append(title)
        for label, score, note in (
            ("ladder rating", ours, ""),
            ("ladder rating, own mode only", mode_score, ""),
            ("racetime.gg recorded score", racetime, "  best-fit logistic, in-sample"),
        ):
            lines.append(
                f"  {label:<32}{score.pairs:>8,}{score.accuracy:>10.3f}"
                f"{score.log_loss:>10.3f}{note}"
            )
    lines.append(
        f"Winners who lost points: {report.winners_lost_points:,} of {report.winners:,}"
    )
    lines.append(
        f"Mode winners who lost points: {report.mode_winners_lost_points:,} "
        f"of {report.mode_winners:,}"
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
