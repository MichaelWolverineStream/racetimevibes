# ALTTPR Step Ladder Race Signals

A static infographic dashboard built from the public racetime.gg archive, from 2025-04-20, for the `alttpr-ladder` category. Beyond the schedule analytics it tracks racers: a ladder rating, personal bests, streaks, badges, records, and a feed of the latest results.

## Requirements

- Python 3.11 or newer
- A network connection when refreshing the data
- ImageMagick 7, only for `scripts/badge_images.py`, which converts badge art

The data builder and web page have no third-party runtime dependencies.

## Refresh the data

```bash
python3 scripts/race_archive.py
python3 scripts/build_stats.py
```

Refreshing has two steps. `race_archive.py` saves new raw race records from racetime.gg into `archive/`. `build_stats.py` then builds the dashboard datasets from that archive alone; it makes no network requests. It atomically writes three datasets: the aggregate to `site/data/race-stats.json`; the per-racer dataset to `site/data/participants.json`, in which each race carries its Eastern Time date and start hour, its seed preset, and its derived tags, and each racer carries a rating, badges, streaks and personal bests; and the racers' post-race comments to `site/data/comments.json`, which the page fetches after its first render so it never delays the board. Use `--archive` on either script to point at a different archive, and `--output`, `--participants-output` and `--comments-output` on `build_stats.py` to redirect each dataset.

### The race archive

`archive/races/alttpr-ladder/<slug>.json` holds one file per race, named after the race. Each file stores the race exactly as the API's past-races listing returned it, with entrant details, inside an envelope that records when it was fetched:

```json
{"schema_version": 1, "fetched_at": "2026-10-27T10:59:46Z", "race": {"name": "alttpr-ladder/dazzling-matt-4030", "...": "..."}}
```

The archive covers every race in the category from 2025-04-20; the builder drops only the `ladder_test` mode. `archive/racer-names.json` maps each racer's stable user ID to their current display name.

The listing is sorted by completion time, newest first, 100 races per page. A sync reads it backwards from page one and stops once it passes a horizon. The horizon is one day before the newest archived race, or the oldest archived race still waiting for a moderator to record it, whichever is earlier. On a normal day that is one request for page one plus one for the category leaderboards. With an empty archive the sync reads every page, so the first run is a full backfill of about 33 requests. Transient network failures are retried with bounded backoff, and the sync checks completion-time ordering and race names before writing anything.

Finished races still change until a moderator records them, usually within two days: `recorded` flips, `score_change` is filled in, and racers add comments. So a race's file is rewritten while the race is unrecorded and its data changes. Once the race is recorded, is marked unrecordable, or finished more than 14 days ago, its file is never touched again. Files are written oldest first, so an interrupted sync leaves no gap behind the newest archived race.

If the archive ever holds fewer races than the API reports, the sync reads every page once to fill the gap, and fails if races are still missing. If an unrecorded race disappears from the listing, its last archived copy is kept and the sync prints a warning.

User IDs are stable, but display names can change, and each race file keeps the name a racer had when it was fetched. Each sync merges the names from the category leaderboards into `racer-names.json`; the leaderboards cover nearly every racer with a recorded race. The dashboard shows a racer under that name, or, if they have no entry, under the name on their most recent race.

### Privacy

`race-stats.json` is anonymous: entrant user IDs are used only as in-memory deduplication keys, and the file contains cohort totals and identity-coverage statistics, never entrant arrays, IDs, names, profile links, or Twitch fields. `participants.json` publishes each racer's public display name and stable user ID because a leaderboard requires them, plus the filename of their racetime.gg avatar, taken from their most recent race and kept only if it is hosted on `https://racetime.gg/media/`. `comments.json` publishes the raw post-race comments of included races, stripped and capped at 500 characters, keyed by race name and user ID. The page loads avatar images directly from racetime.gg with `referrerpolicy="no-referrer"`, so racetime.gg sees a viewer's IP address when avatars are shown. Full names, discriminators, pronouns, flair, Twitch fields and racetime.gg points and score fields remain unpublished.

`archive/` is different. It mirrors racetime.gg's unauthenticated public API verbatim, including entrants' full user profiles and post-race comments. It is committed to this public repository, so anyone can read it there, but it sits outside `site/` and is never deployed with the dashboard.

## Preview the dashboard

```bash
python3 -m http.server 8000 -d site
```

Open <http://localhost:8000>. The page must be served over HTTP because it loads the generated JSON with `fetch`; opening `site/index.html` directly will not work in browsers that restrict local file requests.

## Version the static assets

```bash
python3 scripts/version_assets.py
```

Run this after editing `site/index.html` or any versioned asset listed in `VERSIONED_ASSETS` in `scripts/version_assets.py`, then commit the result. It hashes the markup and those assets into a single token, rewrites every reference to `asset?v=<token>`, and writes `site/version.json`.

Two things follow from that. A deploy changes the asset URLs, so returning visitors always fetch the new files instead of a cached copy. And `site/version-check.js` polls `version.json` every five minutes, plus whenever the tab regains focus, and reloads the page once if the published token no longer matches the one embedded in the loaded document.

The reload is guarded by `sessionStorage`: a tab reloads at most once per newly published version. Without that guard a browser still serving a cached `index.html` would report the old token forever and reload in a loop.

`python3 scripts/version_assets.py --check` reports staleness without writing. The test suite runs the same check, so CI fails if an asset changed without a restamp.

The self-hosted Barlow fonts in `site/assets/fonts/` are referenced from `styles.css`, not stamped. Their filenames carry the family and weight, so replace a font by adding a file under a new name rather than overwriting an existing one.

## Publish with GitHub Pages

The workflow in `.github/workflows/pages.yml` runs the test suite and deploys the `site` directory whenever `main` is pushed. It can also be started manually from the repository's **Actions** tab.

For the first deployment:

1. Push the repository to GitHub.
2. Open **Settings > Pages** in the GitHub repository.
3. Under **Build and deployment**, set **Source** to **GitHub Actions**.
4. Open **Actions > Deploy GitHub Pages** and run the workflow, or push another commit to `main`.

The published URL is <https://michaelwolverinestream.github.io/racetimevibes/>. The workflow deploys the committed aggregate JSON.

## Scheduled data refresh

The workflow in `.github/workflows/update-data.yml` runs `scripts/race_archive.py` and then `scripts/build_stats.py` three times daily, at 02:17, 10:17 and 18:17 UTC. It can also be started manually from **Actions > Update race data**. Whenever the sync added or refreshed race files, it commits `archive/` to `main` as `YYYY-MM-DD data`. When anything other than `generated_at` differs in the dashboard datasets, the same commit includes `site/data`, and the workflow then starts **Deploy GitHub Pages**. That start has to be explicit: pushes made with the workflow's `GITHUB_TOKEN` do not trigger other workflows. Runs that find nothing new commit nothing, and a failed sync or build commits nothing.

You can still refresh by hand with the two commands above and commit `archive/` and `site/data`. Pull first, because the scheduled job may have pushed a data commit since your last sync. If a pull conflicts in `archive/`, discard your local archive changes, pull, and run both scripts again.

## Metric definitions

- Only records whose `status.value` is `finished` are included.
- The dataset covers every archived finished race, beginning 2025-04-20. Dates are `America/New_York` local start dates.
- Races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric.
- Schedule buckets use `America/New_York`, including the seasonal switch between EST and EDT.
- Periods are calendar quarters (`Q1` to `Q4`) determined from the race's Eastern Time start. Edge periods are marked partial when the archive does not cover the full quarter.
- Race turnout uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries.
- Identified racers are deduplicated from stable public user IDs within the selected period. Identity coverage is the share of entries carrying such an ID. Cohorts are one race, occasional (2-5), regular (6-20), and core (21+); cohort entry shares use identified entries as the denominator.
- Unique-player metrics cover archived finished races only. They do not estimate people from cancelled rooms, and anonymized entries contribute to identity coverage but not player or cohort counts.
- Quarterly participation is average entries per finished race: total race entries divided by races in that calendar quarter.
- Hour, weekday-hour, and mode averages use the same entries-per-finished-race definition.
- Total entrants per hour sums recorded entries for the selected reporting period.
- Racerooms with fewer than two entrants are cancelled and are not available through the archive. The dashboard estimates their count under a uniform hourly-opening assumption. Let $F_h$ be observed finished rooms in hour $h$ and let $B_6$ be the median of the six busiest hourly counts. The central estimate is $\sum_h \max(0, B_6 - F_h)$, rounded to the nearest room.
- The displayed sensitivity range repeats that calculation with the median of the 12 busiest hours as the lower baseline and the maximum observed hour as the upper baseline. This is sensitivity analysis, not a confidence interval. It estimates rooms missing from the archive and is separate from API records explicitly marked `cancelled`.
- Medians and quartiles describe the observed race-level distribution. The displayed 95% ranges are normal confidence intervals for the mean using the sample standard deviation; cells with fewer than two races have no interval.
- Adjusted weekday-hour lift subtracts the matching mode-by-calendar-quarter mean from every race, then averages those residuals within each weekday-hour cell. This reduces mode and broad time-period mix effects.
- Mode is the normalized first token in the `Step Ladder Series - [mode]` prefix of `info`. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.

### Racer leaderboard

- **Racetime.gg points are ignored entirely.** `score` and `score_change` are never published or used by any racer metric. The dashboard's rating is in-house (see Ladder rating); racetime.gg's recorded `score` is read in only one place, as the benchmark in the rating backtest.
- Ranking counts first, second, and third place finishes regardless of field size, sorted by golds, then silvers, then bronzes, then total races, then name. There is no minimum-races cutoff. Because a podium in a field of three is not a podium in a field of thirty, read the rank alongside the head-to-head win rate.
- A racer counts as finishing a race only when their entrant `status.value` is `done` **and** the API reports a `place`. Everything else counts toward DNF, except `dq`, which is tracked separately on the racer page.
- Average, best, and worst times cover finished races only and come from `finish_time`, rounded to whole seconds (the rating, tags and records use the exact fractional time). Racers with no finishes show `--` rather than zero.
- Head-to-head pairs every racer in a race against every other racer in that same race. The lower `place` wins. Any finisher beats any non-finisher. Two non-finishers draw.
- **Win %** on the leaderboard is the share of races won outright: first places divided by races entered. A race that ended in a DNF still counts in the denominator.
- **H2H win %** is wins divided by wins plus losses across every pairwise matchup. Draws are reported in their own column on the racer page and are excluded from the denominator. This normalizes for field size: finishing fifth of ten is five wins and four losses, so a racer who never wins outright can still rate highly by consistently placing near the top of large fields.
- The trend compares the win rate over the last five decisive meetings against the career rate with that opponent, and appears only after more than five decisive meetings. A move of more than five percentage points shows as up or down; anything smaller shows as steady.
- The optional **5+ races only** button hides racers with fewer than five races. It counts races in the currently selected mode, so a veteran with only a few races in one mode drops out of that mode's board. Ranks keep their full-board position, so a filtered view shows gaps.
- The optional **Raced in last 30 days** button hides racers with no race in the currently selected mode during the 30 Eastern Time calendar days ending today. Any entry counts, including a DNF or DQ. It only hides rows: the stats shown stay those of the chosen date range, and ranks keep their position, so a filtered view shows gaps.
- **Count races from** limits the leaderboard to races dated inside a chosen range, inclusive, using the Eastern Time race date. Presets cover all time, the last 30 or 90 days, and this year, and the From and To dates can be set by hand. Every column, including H2H win %, and the rank are recalculated from races in the range with the same rules as above, and **5+ races only** counts races in the range. Racer pages always cover the whole archive.
- By default the leaderboard covers the whole archive. The reporting-period toggle applies to the dashboard panels only.

The candidate rankings are observational leads for schedule experiments, not causal estimates. They do not control for announcements, competing events, holidays, organizer effects, or player availability, and sparse windows may look extreme by chance. Use the strongest windows to design prospective schedule tests and compare repeated outcomes before making permanent changes.

Data freshness is build-time: the dashboard is at most one daily refresh behind racetime.gg. The browser reads the local aggregate file and never crawls racetime.gg.

### Finish times

- Each dot is one timed finish: an entrant whose status is `done` with a `place` and a `finish_time`. DNF, DQ, and untimed entries are not plotted.
- The leaderboard's finish-time chart draws one violin per mode, side by side, with finish time on the vertical axis. The shape is a Gaussian kernel density estimate with Silverman's bandwidth, $0.9 \cdot \min(s, \mathrm{IQR}/1.34) \cdot n^{-1/5}$, cut at the fastest and slowest observed finish. Each dot is spread sideways within the shape by a fixed pseudo-random offset, so dots keep their place when filters change. A mode whose finishes all share one time shows dots without a shape.
- The bold line marks each mode's median. Its tooltip and the mode summary table also give the quartiles, using the same median-of-halves definition as the dashboard's other quartiles.
- The chart plots exactly the racers listed in the table, in every mode, so the mode, **Count races from**, **5+ races only**, **Raced in last 30 days**, and the name search all apply. With a specific mode selected, its violin stays in full color and the other modes fade, still showing those racers' finishes.
- Each mode keeps one color everywhere. The eight hues come from a palette checked for color-vision deficiency in this fixed left-to-right order, which is why the violins are not sorted by speed.
- Racer pages cover the whole archive. Their finish-time-over-time field chart follows the racer page's mode toggle; under **All modes** it shows the racer's most-raced mode and says so. Its dots are colored by each racer's podium count in that mode, in fixed bins of 0, 1–2, 3–9, 10–24, and 25+, with everyone else faded and the racer drawn on top. A third chart repeats the leaderboard's violins for every racer and mode, with the racer's finishes highlighted.
- The racer's trend is an ordinary least-squares line of finish time against race date. The shaded band is a 95% confidence interval for the fitted line, $\hat{y}(x) \pm t \cdot s \sqrt{1/n + (x - \bar{x})^2 / S_{xx}}$, using two-sided Student t critical values with $n - 2$ degrees of freedom, rounded down to the nearest tabulated row. It needs at least three finishes on two or more days, and it describes past races, not a forecast.
- The **When they race** heatmap places every race the racer entered, in every mode, into four Eastern Time start blocks: midnight to 6 AM, 6 AM to noon, noon to 6 PM, and 6 PM to midnight. The number in each cell is races entered.
- Cell color shows either **H2H win %** (wins divided by wins plus losses, as on the leaderboard; blue above 50%, red below) or **Podium %** (podium finishes divided by races entered, so DNFs count). Cells with fewer than three races are dashed and faded, because one or two races can swing a rate to 0% or 100%.

## Ladder rating

`scripts/ladder_rating.py` rates every racer from who beat whom. It is in-house and deliberately not racetime.gg's score.

### In plain words

**Only placement counts.** Finish times are never read, because the game is volatile and a seed's luck says little about skill. A win by one second is worth the same as a win by an hour. Within a race, every pair of entrants is a small match: a finisher beats anyone placed behind them, any finisher beats a DNF or DQ, and two non-finishers, or two racers sharing a place, are skipped. Beat someone you were expected to lose to and the rating moves a lot; beat someone you were expected to beat and it barely moves. Newcomers move the most, because the ladder knows least about them.

One rating covers every mode. **Winning never costs points**, and every newcomer shows exactly **1000** and climbs as the ladder gets to know them. **Your recent races matter most**: a race's weight halves after about 13 later races. Nothing decays while you are away.

### Model

This is the Bayesian Bradley-Terry "full pair" model of Weng and Lin (2011), the method behind OpenSkill. Each racer has a skill $\mu$ and an uncertainty $\sigma$, and a newcomer starts at $\mu_0 = 25$, $\sigma_0 = 8$. For every racer $i$, sum over the opponents $q$ they have a decisive pair with, all from the pre-race states, so the update is simultaneous:

$$c = \sqrt{\sigma_i^2 + \sigma_q^2 + 2\beta^2} \qquad p = \frac{1}{1 + e^{(\mu_q - \mu_i)/c}} \qquad s = 1 \text{ (ahead)},\ 0 \text{ (behind)}$$

$$\Omega = \sum_q \frac{\sigma_i^2}{c}(s - p) \qquad \Delta = \sum_q \frac{\sigma_i}{c} \cdot \frac{\sigma_i^2}{c^2}\, p(1-p)$$

$$\mu_i \leftarrow \mu_i + \Omega \qquad \sigma_i^2 \leftarrow \max\big(\sigma_i^2 \cdot \max(1 - \Delta, \kappa),\ \sigma_{floor}^2\big)$$

The win chance that $a$ beats $b$ is $1 / (1 + e^{-(\mu_a - \mu_b)/\sqrt{\sigma_a^2 + \sigma_b^2 + 2\beta^2}})$. It drives upset tags, God Route, and the head-to-head "Win chance" column, and never uses points.

### Recency

Uncertainty shrinks with every race but never below $\sigma_{floor}$. The floor keeps every race's update above a minimum size, so old races fade by **race count**, not by the calendar: a race's weight halves after about 13 later races, and a racer who is away keeps their points.

### Parameters and tuning

| Parameter | Value | Meaning |
|---|---|---|
| $\beta$ | 12 | per-match performance noise |
| $\sigma_0$ | 8 | a newcomer's starting uncertainty |
| $\sigma_{floor}$ | 4 | uncertainty never shrinks below this |
| $\kappa$ | 1e-4 | smallest share of its variance a racer keeps after a race |
| $\mu_0$ | 25 | a newcomer's starting skill |

$\beta$ and $\sigma_0$ were picked by log loss on races before 2026-01-01 only, over $\beta \in \{8, 10, 12\}$ and $\sigma_0 \in \{6, 8\}$. The floor was not tuned for accuracy: it was set to 4 by choice, for a "livelier" ladder. A veteran's win is worth about +14 points and last place or a DNF about -13. A calmer floor predicts a little better but feels stuck. 2026 races were never used for tuning, so the comparison below is out of sample.

### Points

The displayed points are $1000 + s \cdot (\mu - k\sigma)$, the "conservative rating" idea from TrueSkill and Xbox levels. With $k = \mu_0 / \sigma_0 = 3.125$ a newcomer shows exactly 1000, and as $\sigma$ shrinks the points climb toward $\mu$. The scale $s = 400 / (\ln 10 \cdot \sqrt{2\sigma_{floor}^2 + 2\beta^2}) \approx 9.71$ makes 400 points about 10:1 odds between established racers, as in Elo. The pipeline publishes each racer's points, $\mu$ and $\sigma$ (4 decimal places), and every race entry's points after that race.

### The guarantee

A winner's points never fall. Every pair a winner is in is decided in their favour, so $\Omega > 0$ and $\mu$ rises; $\sigma$ never rises, so $\mu - k\sigma$ rises too, and rounding is monotone. Finish times have no effect at all. Tests enforce both across every archived race.

### What racetime.gg does differently

racetime.gg runs TrueSkill with default parameters on finishing order only, ties every DNF and DQ behind the last finisher, shows $\max(0, \mathrm{round}((\mu - 2\sigma) \cdot 100))$ so newcomers start at 833 and the score climbs with volume as $\sigma$ shrinks, and keeps one leaderboard per goal. Both ratings read placement only. The ladder rating rates all modes together, starts everyone at 1000, shows points on an Elo-style scale, and its floor keeps scores responsive.

### Backtest

`python3 scripts/ladder_rating.py --backtest` replays the archive in order. Before each race it predicts every decisive pair of entrants: finishers by place, a finisher over a non-finisher, and two non-finishers are skipped. racetime.gg is scored from the `score` each entrant carried into the race, which the archive stores (833 when missing); its log loss uses the best-fit logistic curve on the score gap, fitted on the same pairs, which flatters it. The report also counts winners whose displayed points fell, and the median points a veteran (past their 50th race) moves per race.

```text
                                     pairs  accuracy  log loss
From 2026-01-01 (out of sample)
  ladder rating                     46,247     0.722     0.543
  racetime.gg recorded score        46,247     0.694     0.588  best-fit logistic, in-sample
Before 2026-01-01 (tuning period)
  ladder rating                     26,796     0.703     0.562
  racetime.gg recorded score        26,796     0.706     0.573  best-fit logistic, in-sample
Winners who lost points: 0 of 3,220
Median veteran move per race (past race 50): 9 points
```

### Trade-offs

- A crushing win counts the same as a narrow one.
- Experience is rewarded: a prodigy with few races sits below established racers until $\sigma$ shrinks.
- Early on, even a loss can add points, because uncertainty falls faster than skill.
- Clearly weaker racers still drift down, because the number stays honest.
- Predictions trail the old margin-based model slightly (73.3% against 72.2% of 2026 pairs), in exchange for no lost winners and scores that climb. They stay well ahead of racetime.gg's.

## Highlights

`scripts/highlights.py` derives every tag, badge, streak, preset statistic and record in one chronological pass, and `participants.json` publishes the results. A **finisher** has status `done` and a `place`; a **timed** finisher also has a time. The **winner** is the identified entrant with place 1; where a winner is anonymous, winner-dependent tags are skipped. A racer is **provisional** at a point in time with fewer than 5 finishes before that race. Thresholds are constants at the top of the module.

### Race tags

Within a race, tags appear in the order record, first win, upset, photo, PB, milestone, badge.

- **Mode record:** the race's fastest time beats the mode's record in whole seconds, the precision the page shows. The first race in a mode sets the baseline and is not tagged.
- **First win:** the racer's first place-1 result in any mode.
- **Upset:** the winner's pre-race win chance against the favourite is 15% or less. The favourite is the highest pre-race rating among the entrants who are not provisional; the race needs at least 3 identified entrants, and the winner must not be the favourite. 4.5% of races qualify.
- **Photo finish:** places 1 and 2 are both timed finishers separated by less than 5 seconds. The margin uses the exact times.
- **PB:** a strict improvement, in whole seconds, on the racer's previous best timed finish in the same mode. The first timed finish in a mode is the baseline and is not tagged.
- **Milestone:** the racer's entry count, in any status, reaches 25, 50, 100, 250, 500 or 1000.
- **Badge:** a badge newly unlocked in this race. First Blood is never tagged, because First win covers it.

### Badges

Each badge records the index of the race that unlocked it. Shares are of the 397 racers as of 2026-10-27.

| Badge | Rule | Held by |
|---|---|---|
| First Blood | Win a race. | 56% |
| Hat Trick | Win 3 races in a row, in the racer's own sequence. | 15% |
| OkDudeLucksack | Win 9 races in a row. | 0.8% |
| No Dodge | Finish 10 races in a row. | 45% |
| More No Dodge | Finish 40 races in a row. | 11% |
| Nice | Finish 69 races in a row. | 3.8% |
| CenturianV | Finish 105 races. | 12% |
| BlazeIt | Finish 420 races. | 0% |
| >174 | Enter 175 races you don't win. | 3.8% |
| Omnivore | Finish a race in 7 different modes. | 3.8% |
| Sniper | Win by less than 5 seconds. | 7.1% |
| Sniped! | Finish 2nd, less than 5 seconds behind the winner. | 7.1% |
| RETIME | Finish less than 1 second from another racer. | 17% |
| Palindrome | Finish with a time whose H:MM:SS digits, hour unpadded and colons removed, read the same both ways. | 34% |
| God Route | Finish ahead of the top-rated racer while ranked outside the top 10. | 19% |
| Big Game Hunter | Win a race with 16 or more entrants, anonymous entrants included. | 13% |
| Glitched | Finish a glitched race. | 10% |
| Glitched! | Finish 10 glitched races. | 3.5% |
| G1!tch3d!!! | Finish 30 glitched races. | 1.8% |
| G%!#ch@d!#? | Finish 100 glitched races. | 0% |
| Jet Seed | Finish a Casual Boots race in under 70 minutes. | 9.1% |
| Jet Lag | Win a Casual Boots race in 100 minutes or more. | 2.8% |
| Entranced | Enter an entrance race on 5 days in a row. | 7.1% |
| Entranced! | Enter an entrance race in 20 weeks in a row. | 2.8% |
| Both Ends | Finish a mystery race in under 30 minutes, and another in over 130 minutes. | 0.3% |
| Grocery Stick | Finish in the exact middle of an odd field of 5 or more entrants. | 56% |
| Rando Sicko | Finish 5 races in one day. | 0.3% |
| Step Letter | Be in a race of 5 or more where everyone finished in alphabetical order. | 2.3% |

A badge is either a counter, which unlocks when a count reaches its target and fills its border as it progresses, or a one-race badge, which unlocks on a single result. On a racer page, earned badges come first in the order they were won, oldest first, and locked badges follow in table order. Hovering a badge shows the share of racers who hold it, to two significant digits, counted on the page from the current data. In the list below, a **finish** is status `done` with a place, a **day** is the race's Eastern Time date, a **week** runs Monday to Sunday, and the **field** is the race's entrant count, which includes DNFs and anonymous entrants.

- **Streak and count badges:** win and finish runs follow the racer's own race sequence across all modes, and a DNF or DQ ends a finish run. CenturianV and BlazeIt count finishes. >174 counts every race entered and not won, DNFs included. The Glitched badges count finishes in the glitched mode.
- **Entranced and Entranced!:** any entry in the entrance mode counts, DNFs included, and several races on one day or in one week count once.
- **Rando Sicko:** finishes only, counted per day.
- **Sniper and Sniped!:** the photo tag's condition. Places 1 and 2 are both timed and the margin is under 5 seconds. Sniper goes to the winner and Sniped! to the runner-up.
- **RETIME:** two finishers' raw times, before rounding to whole seconds, are less than 1 second apart, at any places. Both earn it, and so does everyone in a chain of such gaps. Only identified, timed finishers count.
- **Jet Seed and Jet Lag:** the seed preset is named exactly Casual Boots.
- **Both Ends:** one mystery finish under 30 minutes and another over 130 minutes, in either order. It unlocks on the race that completes the pair.
- **Grocery Stick:** the field is odd and at least 5, and the finisher whose place is the middle one, `(field + 1) / 2`, earns it.
- **Step Letter:** the field is at least 5, every entrant is identified and finished, and the names in place order never decrease when compared without case. Every entrant earns it.
- **God Route:** "top-rated" and "top 10" rank the racers by pre-race rating among those who are not provisional and raced in the previous 90 days. The rule applies when the top-rated racer is in the race, and "ahead" uses the leaderboard's pairing rule: a lower place wins, and any finisher beats a non-finisher.
- **Omnivore:** needs 7 of the 8 modes, because requiring all 8 left 0.8% of racers holding it.

Each badge ships an animated image and a still under `site/assets/badges/`. Earned badges animate, and locked badges show the grey still. To add or remove a badge, see [Badge_Guide.md](Badge_Guide.md).

The badge art in `site/assets/badges/` includes [Twemoji](https://github.com/twitter/twemoji) graphics, licensed [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/), for Step Letter, BlazeIt, Both Ends, Grocery Stick and RETIME.

### Streaks

- **Daily:** consecutive Eastern Time dates with at least one entry. The current streak is the run ending on the racer's last race day.
- **Win, podium and finish:** runs over the racer's own race sequence across all modes. Podium means places 1-3; finish means finisher. The current streak is the run that includes the racer's latest race, and is absent if that race broke it.

### Presets

A preset is the name after the mode in the race's `info` first line, trimmed, cut at any seed URL (along with a room slug such as `owner/slug` just before it), with `[x]` turned into `(x)` and any `Grab Bag (...)` collapsed to `Grab Bag`. Each preset belongs to its mode. Statistics are races, entries, DNF rate (entries that are not finishers), median time, median winning time, mean field size, and the fastest finish.

- **Hardest preset:** the highest DNF rate among presets with 8 or more races.
- **A racer's best preset:** the lowest ratio of the racer's median time to the preset's median time, among presets with 8 or more races where the racer has 3 or more timed finishes.

### Records

- **Mode records:** one progression step per race that sets a new record, per mode.
- **Photo finishes** and **blowouts:** the 10 smallest and 10 largest margins between places 1 and 2.
- **Big fields:** the 10 largest fields that had an identified winner.
- **Most races:** the 10 racers with the most entries.
- **Streak boards:** the 10 best streaks of each kind. Whether a streak is still active depends on today's date, so the page's **Active now** view ranks every racer's current streak of two or more: a daily streak whose last day is today or yesterday in Eastern Time, or any other kind whose latest race is within the last 30 days.
- **Facts:** about a dozen English sentences generated from the data, such as the hardest preset or the busiest day. A fact without enough data is skipped.

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

- Races are the dashboard's included races, and entrants are identified by stable user ID; anonymous entries are skipped. Any entry counts, DNFs and DQs included, because entering is what a clash takes away.
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

## Dashboard sections

The page runs top to bottom as: **01 Latest** (the newest results with their tags), **02 Racers** (the leaderboard, with the rating column), **03 Records** (the hall of records), **04-10** the analytics panels (schedule, participation, and finish times), and a page per racer with their rating, badges, personal bests, and charts. A racer's rivals (Nemesis and Favourite victim) sit directly under the badges and are always computed across all modes, whatever mode is selected. There is no race-day calendar.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use mocked API responses and do not require network access.

The suite also rebuilds all three datasets from the committed `archive/` and fails if they differ from the committed `site/data` files, ignoring `generated_at`. After changing `scripts/build_stats.py`, `scripts/participant_stats.py` or any module they call, run `python3 scripts/build_stats.py` and commit the result.

## Project layout

```text
scripts/race_archive.py           Incremental sync of raw races and racer names
scripts/build_stats.py            Archive validation and aggregation
scripts/participant_stats.py      Per-racer dataset: records, avatars, assembly of schema 3
scripts/ladder_rating.py          Ladder rating model and backtest
scripts/highlights.py             Tags, badges, streaks, presets, records and facts
scripts/comment_stats.py          Racer comments dataset
scripts/mode_overlap.py           Mode overlap analysis and report
scripts/badge_images.py           Converts badge art to the standard WebP pair (needs ImageMagick 7)
scripts/version_assets.py         Content versioning for the static assets
tests/test_race_archive.py        Archive sync unit tests
tests/test_build_stats.py         Pipeline unit tests
tests/test_participant_stats.py   Racer dataset unit tests
tests/test_ladder_rating.py       Rating model unit tests
tests/test_highlights.py          Tag, badge, streak and record unit tests
tests/test_comment_stats.py       Comments dataset unit tests
tests/test_mode_overlap.py        Mode overlap unit tests
tests/test_site_globals.py        Checks that site scripts declare no clashing globals
tests/test_badge_assets.py        Badge image pair checks
tests/test_version_assets.py      Asset versioning unit tests
.github/workflows/pages.yml       Test and deploy to GitHub Pages
.github/workflows/update-data.yml Scheduled archive sync, data build, commit, and deploy
archive/races/                    Raw race records, one file per race
archive/racer-names.json          Current racer display names
site/data/race-stats.json         Generated aggregate dataset
site/data/participants.json       Generated per-racer dataset
site/data/comments.json           Generated racer comments, loaded lazily
site/version.json                 Generated content version manifest
site/index.html                   Static dashboard document
site/app.js                       Native SVG chart rendering
site/racer-charts.js              Finish-time violins, scatter, trend, and start-time heatmap
site/leaderboard.js               Racer leaderboard and head-to-head views
site/store.js                     Local storage wrapper and followed racers
site/ui.js, ui.css                Shared avatars, speech bubbles, tags, badges, and dialogs
site/feed.js, feed.css            Latest results feed
site/records.js, records.css      Hall of records
site/racer-extras.js, .css        Racer page badges, rivals, personal bests, and rating chart
site/easter-eggs.js, .css         Hidden extras
site/version-check.js             Reloads the page when a new version ships
site/styles.css                   Responsive visual system
site/assets/fonts/                Self-hosted Barlow fonts (SIL Open Font License)
site/assets/badges/               Badge images, an animated WebP and a still for each badge
Badge_Guide.md                    How to add or remove a badge
```

API behavior and field definitions are recorded in [Racetime_API.md](Racetime_API.md).