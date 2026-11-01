# Standalone analyses

Two scripts in `scripts/` analyse the archive on their own. The dashboard build never runs them, and they change nothing in `site/`. The dashboard itself is specified in [README.md](README.md).

## Mode overlap

`scripts/mode_overlap.py` measures how much the modes share racers, to find modes that could run in the same slot without making racers pick one. It is a standalone analysis: the dashboard build does not run it, and it writes nothing unless asked. `--html` writes a self-contained report page, with no scripts or external requests, in the claude.ai Artifact page format, which leaves the document skeleton to the publisher.

```bash
python3 scripts/mode_overlap.py                  # ranked pair table
python3 scripts/mode_overlap.py --min-entries 2  # count only racers with 2+ entries in the other mode
python3 scripts/mode_overlap.py --html report.html
```

### In plain words

For a pair of modes A and B, look only at the months when both were on the schedule, and ask: what share of A's entries came from racers who also raced B that month? If A ran against B, that share of A's field would have to choose. The pair's score is the larger of the two directions, because a pairing is only as good as its effect on the mode that loses more.

### Definitions

- Races are the dashboard's included races (see [README › Definitions](README.md#definitions)), and entrants are identified by stable user ID; anonymous entries are skipped. Any entry counts, DNFs and DQs included, because entering is what a clash takes away.
- Months are Eastern Time calendar months. A mode is **on the schedule** in a month when it has at least 4 races that month. A pair's **co-active months** are the months when both modes are on the schedule, and every pair figure uses only those months.
- The **directed share A→B** is A's entries in the co-active months made by racers who entered B at least once that same month (`--min-entries` raises the once), divided by all of A's identified entries in those months.
- The **chance baseline A→B** is the share expected if A's racers entered B at B's general rate. In each co-active month, B's reach is the racers who met the same entry threshold in B divided by every racer with any entry that month. The baseline is that reach averaged over the co-active months, weighted by A's entries in each. A share well below its baseline means the two modes draw different crowds; a share near it means B is simply popular.
- The **pair score** is the larger of A→B and B→A. Below 0.25 it rates **low**, below 0.5 **moderate**, and **high** otherwise. A pair with fewer than 6 co-active months is **weak evidence**. A pair with no co-active months is unmeasured.
- The **95% interval** is a percentile bootstrap of the pair score: 1,000 rounds that each draw every racer in the archive with replacement, keeping all of a racer's months together, with a fixed seed so runs repeat exactly.
- The **trend** repeats the pair score within each calendar half-year, over the co-active months in that half-year, without an interval.
- Thresholds are constants at the top of the module.

### Limits

- The score is an upper bound on the conflict. Racing both modes in a month does not prove a racer would want both slots on the same day. The bot never runs two modes at once, so the true forced choice cannot be observed.
- Pairs are measured over different months, so a paused mode such as inverted is compared on the racers of its own era.

## Rating comparison

`scripts/rating_lab.py` compares other rating algorithms with the [ladder rating](README.md#ladder-rating), on the [backtest](README.md#backtest)'s pairs and under the ladder's promises. It is a standalone analysis: the dashboard build does not run it, and it changes nothing. Its recommendation, the retune below, was adopted on 2026-11-01. Throughout this section, **shipped** means the rating it was compared with ($\beta = 12$, $\sigma_0 = 8$, floor 4, no damping), which the lab keeps as its baseline so the comparison stays reproducible.

```bash
python3 scripts/rating_lab.py --tune               # each candidate's setting, from races before 2026 (several minutes)
python3 scripts/rating_lab.py --tune --family elo  # one candidate only
python3 scripts/rating_lab.py                      # re-derive CHOSEN from --tune, then the comparison
```

### Candidates

Every candidate reads placement only and is scored on exactly the pairs the backtest scores.

| Candidate | What it is |
|---|---|
| Bradley-Terry full pair, retuned | The ladder rating with every parameter re-picked, plus field-size damping: a race's update is scaled by $(n-1)^{-\alpha}$ for $n$ entrants, so a 32-racer race no longer counts as 31 independent matches |
| Thurstone-Mosteller full pair | Weng and Lin's Algorithm 3: the same pairs with a normal win chance |
| Bradley-Terry partial pair | Algorithm 2: each place meets only the next place, and the last place meets every non-finisher |
| Plackett-Luce | Algorithm 4: the whole finishing order at once, non-finishers tied last. Racers who share a place, and non-finishers, never count against each other |
| Elo, multi-player | Every decided pair is an Elo game, with a K that falls over a racer's first races |

Left out because they cannot keep the ladder's promises:
- Glicko-2: ratings decay while a racer is away.
- Whole-History Rating, TrueSkill Through Time and batch refits: ratings move when others race.
- Additive drift: a winner can lose points.
- Mode ratings seeded from the overall rating.

TrueSkill is left out because Thurstone-Mosteller is its close approximation.

### Rules

All of these were fixed before any 2026 race was scored for a candidate.

- **Promises.** A candidate is eligible only if, over the whole archive, overall and in every mode, it keeps all of these:
  - no winner's points fall;
  - every debut shows 1000;
  - no state changes while a racer is away;
  - finish times change nothing.

  Mode ratings use the overall parameters.
- **Tuning** reads races before 2026-01-01 only, and `tune` cuts there itself.
  - It scores July to September and October to December 2025. The earlier months are warm-up, because everyone in them is new.
  - A setting must move a veteran's points at least as much as the shipped rating does over those months, which is a median of 8. This keeps the livelier ladder.
  - Among the lively settings within one standard error of the best, the simplest wins.
  - A choice on a grid edge extends that axis and tunes again.
  - Points are scaled per family, so that 400 points are about 10:1 odds under that family's own win chance.
- **The comparison runs once.**
  - The primary measure is 2026 pair log loss, paired against the shipped rating.
  - The interval is the wider of a race bootstrap and an Eastern Time ISO-week bootstrap: 2,000 resamples, fixed seed.
  - The p-value is the larger of the two, Holm-adjusted across the candidates.
- **A change** needs all of these:
  - eligibility;
  - Holm significance;
  - at least 0.003 better. That is about twice the 0.0017 that floor 3 gains over floor 4 over September to December 2025, the accuracy already traded for liveliness;
  - better in both halves of 2026;
  - no worse when each race counts once.

  A new model must also beat the retuned ladder rating the same way; otherwise retuning is the simpler change.

### How the candidates were tested

- **Golden values.**
  - The OpenSkill reference package, version 6.2.0, run once outside the repository, supplied exact results for Algorithms 2 to 4 on fixed races.
  - Its configuration was first proved by reproducing `ladder_rating`'s own update to 1e-14.
  - The candidates match those results to 1e-13.
- **Fuzz tests** check the promises on thousands of random races and states, including shared wins, which the archive has never had.
- **A known-truth simulation** checks that every candidate recovers a true skill order from simulated races.
- **Harness tests** cover all of these:
  - a coin flip scores ln 2;
  - the shipped setting reproduces the backtest;
  - tuning gives the same answer whatever 2026 holds;
  - the promise checker catches each broken promise.
- **An independent review** before the run found that Thurstone-Mosteller's points were scaled for a logistic win chance, so that 400 points meant 92:1. That held it to a stricter liveliness bar. It was fixed and re-tuned before 2026 was scored.

### Result

On the archive as of the lab run on 2026-11-01: 47,576 pairs in 1,979 races from 2026-01-01. These figures are a frozen snapshot; the README's [Backtest](README.md#backtest) runs on the current archive, so its pair counts are higher. Δ is the change in log loss against shipped, below 0 is better, with its 95% interval.

```text
                       setting                          accuracy  log loss  Δ (95% CI)                  per race
ladder rating, shipped β12 σ₀8 floor 4                     0.723    0.5414                                0.5380
BT full pair, retuned  β8 σ₀12 floor 4 α½                  0.728    0.5341  -0.0074 (-0.0091, -0.0056)    0.5314
TM full pair           β12 σ₀8 floor 3 α½                  0.728    0.5347  -0.0068 (-0.0088, -0.0048)    0.5324
Elo, multi-player      K 128→24 over 10 races, α½          0.727    0.5368  -0.0046 (-0.0060, -0.0033)    0.5340
Plackett-Luce          β6 σ₀8 floor 5                      0.716    0.5854  +0.0440 (+0.0361, +0.0517)    0.5761
BT partial pair        β5 σ₀40 floor 4                     0.693    0.5875  +0.0461 (+0.0396, +0.0530)    0.5758
racetime.gg score                                          0.695    0.5871  best-fit logistic, in-sample
```

- **Recommendation: retune the ladder rating to β 8, σ₀ 12, floor 4, with field-size damping α ½.** Adopted on 2026-11-01. The production backtest reproduced the retuned line exactly on that archive.
  - It is better in both halves of 2026: −0.0059 and −0.0090.
  - It is better in every experience bucket and every field size.
  - It is better in every mode with more than 300 pairs, except mystery, where it ties.
  - The own-mode ratings improve too, from 0.5532 to 0.5465.
- **Every candidate kept every promise.**
- **No other family is worth switching to.**
  - Thurstone-Mosteller ties the retuned rating: +0.0006 against it, interval (−0.0000, +0.0013).
  - Elo trails the retuned rating by 0.0028.
  - Plackett-Luce and partial pair do much worse. Partial pair rates only neighbouring places, so it ignores most of a race's pairs. Plackett-Luce's win chances are poorly calibrated: an expected calibration error of 0.070, against 0.020 for shipped.
- **Retuning changes how points feel**, so weigh this before adopting it:
  - A veteran's median move per race falls from 9 to 8 points over the whole archive.
  - Their 90th-percentile move falls from 33 to 20, because damping shrinks the swings of big races.
  - A veteran's DNF costs a median of 12 points instead of 14, and finishing last costs 9 instead of 6.
  - The final standings stay close: Kendall's τ is 0.93 against shipped, and 9 of the top 10 are shared.

### Limits

- The shipped rating's 2026 score was public before this work; the candidates' 2026 scores were not.
- **Grid extensions.**
  - The first tuning run put every family on a grid edge, so the edge rule extended β down to 4, σ₀ to the range 4 to 14, the floor up to 8, and Elo's K values. All of this happened before 2026 was scored.
  - Partial pair was extended three times. It stopped when the best ten settings were within 0.0001 of each other, although σ₀ = 40 still sits on the edge.
- The archive has no tied places, so the tie rules are tested only on synthetic races.
