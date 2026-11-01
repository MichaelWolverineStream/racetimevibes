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
python3 scripts/avatar_mirror.py
```

Refreshing has three steps. `race_archive.py` saves new raw race records, racer names and racer avatar observations from racetime.gg into `archive/`. `build_stats.py` then builds the dashboard datasets from that archive alone; it makes no network requests. It atomically writes three datasets, all minified: the aggregate to `site/data/race-stats.json`; the per-racer dataset to `site/data/participants.json`, in which each race carries its Eastern Time date and start hour, its seed preset, and its derived tags, and each racer carries a rating, badges, streaks and personal bests; and the racers' post-race comments, each with the chest count parsed from it, to `site/data/comments.json`, which the page fetches after its first render so it never delays the board. Use `--archive` on either script to point at a different archive, and `--output`, `--participants-output` and `--comments-output` on `build_stats.py` to redirect each dataset.

`avatar_mirror.py` runs last, because it reads the avatar file names that `build_stats.py` wrote to `participants.json`. It makes `site/avatars/` hold exactly those files, so the page serves every avatar from its own origin. For each racer it works out the URL behind the published file name from `archive/`. It downloads only the files the directory is missing, pausing 0.2 seconds between requests, and checks each one before keeping it: the first bytes must match a PNG, JPEG, GIF or WebP image that agrees with the file extension, and the file must be at most 2,000,000 bytes. It deletes every regular file in `site/avatars/` that `participants.json` does not name, so a racer's old picture goes when a new one arrives. A download or check that fails prints a `Warning:` line and the run continues; the file is tried again on the next run, and meanwhile the page shows the racer's monogram. The script ends with `Avatars: N downloaded, M removed, K failed.` and exits with 1 only if `participants.json` is unreadable or names a file the archive does not explain, in which case it asks you to run `build_stats.py` first. A normal run makes no requests; the first run makes one per racer with an avatar. Use `--participants`, `--archive` and `--output` to point it elsewhere.

### The race archive

`archive/races/alttpr-ladder/<slug>.json` holds one file per race, named after the race. Each file stores the race exactly as the API's past-races listing returned it, with entrant details, inside an envelope that records when it was fetched:

```json
{"schema_version": 1, "fetched_at": "2026-10-27T10:59:46Z", "race": {"name": "alttpr-ladder/dazzling-matt-4030", "...": "..."}}
```

The archive covers every race in the category from 2025-04-20; the builder drops only the `ladder_test` mode. `archive/racer-names.json` maps each racer's stable user ID to their current display name. `archive/racer-avatars.json` maps it to the avatar URL (or `null`) the category leaderboards last showed for them, with the time the sync first saw that value. Every archive file is minified JSON: no whitespace between tokens, and one trailing newline.

The listing is sorted by completion time, newest first, 100 races per page. A sync reads it backwards from page one and stops once it passes a horizon. The horizon is one day before the newest archived race, or the oldest archived race still waiting for a moderator to record it, whichever is earlier. On a normal day that is one request for page one plus one for the category leaderboards. With an empty archive the sync reads every page, so the first run is a full backfill of about 33 requests. Transient network failures are retried with bounded backoff, and the sync checks completion-time ordering and race names before writing anything.

Finished races still change until a moderator records them, usually within two days: `recorded` flips, `score_change` is filled in, and racers add comments. So a race's file is rewritten while the race is unrecorded and its data changes. Once the race is recorded, is marked unrecordable, or finished more than 14 days ago, its file is never touched again. Files are written oldest first, so an interrupted sync leaves no gap behind the newest archived race.

If the archive ever holds fewer races than the API reports, the sync reads every page once to fill the gap, and fails if races are still missing. If an unrecorded race disappears from the listing, its last archived copy is kept and the sync prints a warning.

User IDs are stable, but display names can change, and each race file keeps the name a racer had when it was fetched. Each sync merges the names from the category leaderboards into `racer-names.json`; the leaderboards cover nearly every racer with a recorded race. The dashboard shows a racer under that name, or, if they have no entry, under the name on their most recent race.

Avatars work the same way but never cost a request. The leaderboards response already carries each ranked racer's current avatar, so each sync merges it into `racer-avatars.json` as `[avatar URL or null, first-seen time]`, where the time is when the sync received the leaderboards. A user the leaderboards list without an `avatar` field is left as they were, not cleared. A racer's entry keeps its time while the value stays the same, entries are never removed, and the file is rewritten only when a value is added or changes, so an ordinary sync leaves it untouched. A racer who uploads a new picture gets a new URL, so the change is picked up at the next sync even if they have not raced. A change and a revert to the same file name within a single sync interval goes unnoticed.

### Privacy

`race-stats.json` is anonymous: entrant user IDs are used only as in-memory deduplication keys, and the file contains cohort totals and identity-coverage statistics, never entrant arrays, IDs, names, profile links, or Twitch fields. `participants.json` publishes each racer's public display name and stable user ID because a leaderboard requires them, plus the file name of their mirrored avatar. That name is the first 16 hex digits of the SHA-256 of the avatar's racetime.gg URL, plus the URL's lowercase extension, for example `3f9ac01d2e7b8a64.jpg`. A racer has one only if the URL is directly under `https://racetime.gg/media/`, names the file in URL-safe ASCII (letters, digits, `.`, `_`, `~`, `-` and percent escapes), and ends in `png`, `jpg`, `jpeg`, `gif` or `webp`; the name carries nothing from the upload's own file name. The avatar follows the racer's most recently observed profile: the category leaderboards each sync, else their latest race file among the races the dashboard includes, so never a `ladder_test` race, whichever is newer, and the leaderboards win a tie. A racer whose latest observation has no avatar has none. `comments.json` publishes the raw post-race comments of included races, stripped and capped at 500 characters, keyed by race name and user ID, each with the chest count parsed from it, or `null`. `site/avatars/` holds copies of exactly the files `participants.json` names, raster images only, and the page serves avatars from there, so a viewer's browser never contacts racetime.gg for them. The copies are byte for byte, so any metadata an upload carries, such as a JPEG's EXIF block, is served as racetime.gg serves it. A replaced or removed avatar leaves `site/avatars/` but stays in the repository's git history. Full names, discriminators, pronouns, flair, Twitch fields and racetime.gg points and score fields remain unpublished.

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

Two things follow from that. A deploy changes the asset URLs, so returning visitors always fetch the new files instead of a cached copy. And `site/version-check.js` checks `version.json` when the page loads, whenever the tab regains focus, and every five minutes, and reloads the page once if the published token no longer matches the one embedded in the loaded document. A check on load or on regaining focus reloads at once. A five-minute poll that finds a new version while the page is on screen waits instead, and reloads at the next visibility change, so the page never reloads under someone reading it. A tab that stays on screen the whole time therefore keeps the old version until it is hidden or refocused.

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

The workflow in `.github/workflows/update-data.yml` runs `scripts/race_archive.py`, `scripts/build_stats.py` and `scripts/avatar_mirror.py` three times daily, at 02:17, 10:17 and 18:17 UTC. It can also be started manually from **Actions > Update race data**. Whenever the sync added or refreshed race files, it commits `archive/` to `main` as `YYYY-MM-DD data`. When anything other than `generated_at` differs in the dashboard datasets, or `site/avatars/` changed, the same commit includes `site/data` and `site/avatars`, and the workflow then starts **Deploy GitHub Pages**. That start has to be explicit: pushes made with the workflow's `GITHUB_TOKEN` do not trigger other workflows. Runs that find nothing new commit nothing, and a failed sync, build or mirror commits nothing.

You can still refresh by hand with the three commands above and commit `archive/`, `site/data` and `site/avatars`. Pull first, because the scheduled job may have pushed a data commit since your last sync. If a pull conflicts in `archive/`, discard your local archive changes, pull, and run `race_archive.py` and `build_stats.py` again, then `avatar_mirror.py`.

## Metric definitions

- Only records whose `status.value` is `finished` are included.
- The dataset covers every archived finished race, beginning 2025-04-20. Dates are `America/New_York` local start dates.
- Races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric.
- Schedule buckets use `America/New_York`, including the seasonal switch between EST and EDT.
- Periods are calendar quarters (`Q1` to `Q4`) determined from the race's Eastern Time start. Edge periods are marked partial when the archive does not cover the full quarter.
- Race turnout uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries.
- Identified racers are deduplicated from stable public user IDs within the selected period. Identity coverage is the share of entries carrying such an ID. Cohorts are one race, occasional (2-5), regular (6-20), and core (21+); cohort entry shares use identified entries as the denominator.
- Unique-player metrics cover archived finished races only. They do not estimate people from cancelled rooms, and anonymized entries contribute to identity coverage but not player or cohort counts.
- Monthly participation is average entries per finished race: total race entries divided by races in that calendar month.
- Entries per day is a month's total race entries divided by the calendar days the archive covers in that month, so the first and current months use only their covered days. Months without a finished race still appear, with no average and zero entries per day.
- Hour, weekday-hour, and mode averages use the same entries-per-finished-race definition.
- Total entrants per hour sums recorded entries for the selected reporting period.
- Racerooms with fewer than two entrants are cancelled and are not available through the archive. The dashboard estimates their count under a uniform hourly-opening assumption. Let $F_h$ be observed finished rooms in hour $h$ and let $B_6$ be the median of the six busiest hourly counts. The central estimate is $\sum_h \max(0, B_6 - F_h)$, rounded to the nearest room.
- The displayed sensitivity range repeats that calculation with the median of the 12 busiest hours as the lower baseline and the maximum observed hour as the upper baseline. This is sensitivity analysis, not a confidence interval. It estimates rooms missing from the archive and is separate from API records explicitly marked `cancelled`.
- Medians and quartiles describe the observed race-level distribution. The displayed 95% ranges are normal confidence intervals for the mean using the sample standard deviation; cells with fewer than two races have no interval.
- Adjusted weekday-hour lift subtracts the matching mode-by-calendar-quarter mean from every race, then averages those residuals within each weekday-hour cell. This reduces mode and broad time-period mix effects.
- Mode is the normalized first token in the `Step Ladder Series - [mode]` prefix of `info`. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.

### Racer leaderboard

- **Racetime.gg points are ignored entirely.** `score` and `score_change` are never published or used by any racer metric. The dashboard's rating is in-house (see Ladder rating); racetime.gg's recorded `score` is read in only one place, as the benchmark in the rating backtest.
- By default the board is sorted by the ladder rating (see Ladder rating), highest first, with ties broken by name. The rating always covers all modes and the whole archive, whatever mode or dates are selected. There is no minimum-races cutoff. The number beside each racer is the row's position as shown, so it renumbers whenever a sort, filter or search changes the table.
- While the board is sorted by rating, a ▲ or ▼ beside the number shows how far the racer has moved in the overall rating ranks over the last 7 days, and **NEW** marks a racer whose first race falls in that window. Rating ranks are competition ranks: one more than the number of racers rated strictly higher, so tied racers share a rank. The racer page's **Rank & Rating** tile and the **Your racers** sidebar use the same ranks.
- The 1st, 2nd and 3rd columns count first, second, and third place finishes regardless of field size. Because a podium in a field of three is not a podium in a field of thirty, read them alongside the head-to-head win rate.
- **Badges** counts the badges a racer has earned, across all modes and the whole archive.
- A racer counts as finishing a race only when their entrant `status.value` is `done` **and** the API reports a `place`. Everything else counts toward DNF, except `dq`, which is tracked separately on the racer page.
- **Median time** covers finished races only and comes from `finish_time`, rounded to whole seconds (the rating, tags and records use the exact fractional time). With an even number of finishes it is the mean of the middle two. Racers with no finishes show `--` rather than zero.
- Head-to-head pairs every racer in a race against every other racer in that same race. The lower `place` wins. Any finisher beats any non-finisher. Two non-finishers draw.
- **Win %** on the leaderboard is the share of races won outright: first places divided by races entered. A race that ended in a DNF still counts in the denominator.
- **H2H win %** is wins divided by wins plus losses across every pairwise matchup. Draws are reported in their own column on the racer page and are excluded from the denominator. This normalizes for field size: finishing fifth of ten is five wins and four losses, so a racer who never wins outright can still rate highly by consistently placing near the top of large fields.
- The trend compares the win rate over the last five decisive meetings against the career rate with that opponent, and appears only after more than five decisive meetings. A move of more than five percentage points shows as up or down; anything smaller shows as steady.
- The optional **5+ races only** button hides racers with fewer than five races. It counts races in the currently selected mode, so a veteran with only a few races in one mode drops out of that mode's board.
- The optional **Raced in last 30 days** button hides racers with no race in the currently selected mode during the 30 Eastern Time calendar days ending today. Any entry counts, including a DNF or DQ. It only hides rows: the stats shown stay those of the chosen date range.
- The optional **Followed** button shows only the racers starred in this browser. Unstarring a racer while it is on drops their row at once.
- **Count races from** limits the leaderboard to races dated inside a chosen range, inclusive, using the Eastern Time race date. Presets cover all time, the last 30 or 90 days, and this year, and the From and To dates can be set by hand. Every column except Rating and Badges, including H2H win %, is recalculated from races in the range with the same rules as above, and **5+ races only** counts races in the range. Racer pages always cover the whole archive.
- By default the leaderboard covers the whole archive. The reporting-period toggle applies to the dashboard panels only.

The candidate rankings are observational leads for schedule experiments, not causal estimates. They do not control for announcements, competing events, holidays, organizer effects, or player availability, and sparse windows may look extreme by chance. Use the strongest windows to design prospective schedule tests and compare repeated outcomes before making permanent changes.

Data freshness is build-time: the dashboard is at most one daily refresh behind racetime.gg. The browser reads the local aggregate file and never crawls racetime.gg.

### Finish times

- Each dot is one timed finish: an entrant whose status is `done` with a `place` and a `finish_time`. DNF, DQ, and untimed entries are not plotted.
- The finish-time violin charts draw one violin per seed preset, side by side, with finish time on the vertical axis. Each violin holds every racer's timed finishes in that preset across the whole archive. The shape is a Gaussian kernel density estimate with Silverman's bandwidth, $0.9 \cdot \min(s, \mathrm{IQR}/1.34) \cdot n^{-1/5}$, cut at the fastest and slowest observed finish. Each dot is spread sideways within the shape by a fixed pseudo-random offset, so dots keep their place across redraws. A preset whose finishes all share one time shows dots without a shape.
- The bold line marks the preset's median time and the gold line its median winning time. Both come from the preset statistics (see Presets), which use exact times, so they match the presets table to the second. The median's tooltip also gives the finishes, the racers, and the quartiles, using the same median-of-halves definition as the dashboard's other quartiles.
- **Which presets:** a preset is charted only with 10 or more races in total, and a chart shows at most 8. The chart's ranking picks the 8; they are then laid out in the fixed mode order, keeping the ranking within a mode. A chart always has 8 equal slots, so a violin is the same width in every chart, and fewer violins sit centred. When presets are left out, a note above the chart says how many.
  - **Hall of records › By mode** charts the picked mode's presets, ranked by total races, then by name. The presets table below it lists every preset.
  - **Racer pages** chart the presets the racer entered most, DNFs included, in the mode picked on the page, or across every mode under **All modes**. Ties go to total races, then name. The racer's finishes are highlighted.
- Each violin takes its mode's color, and each mode keeps one color everywhere. The eight hues come from a palette checked for color-vision deficiency in this fixed left-to-right order, which is why the violins follow mode order rather than speed. When a chart mixes modes, each violin's label also names its mode, so color is never the only cue.
- Preset names wrap onto two lines and end in an ellipsis if they still don't fit; every tooltip gives the mode and the full name. A chart narrower than 880px keeps that width and scrolls sideways.
- Racer pages cover the whole archive. Their finish-time-over-time field chart follows the racer page's mode toggle; under **All modes** it shows the racer's most-raced mode and says so. Its dots are colored by each racer's podium count in that mode, in fixed bins of 0, 1–2, 3–9, 10–24, and 25+, with everyone else faded and the racer drawn on top.
- The racer's trend is an ordinary least-squares line of finish time against race date. The shaded band is a 95% confidence interval for the fitted line, $\hat{y}(x) \pm t \cdot s \sqrt{1/n + (x - \bar{x})^2 / S_{xx}}$, using two-sided Student t critical values with $n - 2$ degrees of freedom, rounded down to the nearest tabulated row. It needs at least three finishes on two or more days, and it describes past races, not a forecast.
- The **When they race** heatmap places every race the racer entered, in every mode, into four Eastern Time start blocks: midnight to 6 AM, 6 AM to noon, noon to 6 PM, and 6 PM to midnight. The number in each cell is races entered.
- Cell color shows either **H2H win %** (wins divided by wins plus losses, as on the leaderboard; blue above 50%, red below) or **Podium %** (podium finishes divided by races entered, so DNFs count). Cells with fewer than three races are dashed and faded, because one or two races can swing a rate to 0% or 100%.

### Chest counts (CR and CPH)

- **CR** is the number of chests a racer says they opened, and **CPH** is chests per hour: CR × 3600 ÷ finish seconds, rounded to a whole number. Both are soft, self-reported metrics. CR is parsed from the racer's own post-race comment, so it is only as accurate as the comment, and some comments will give a wrong number.
- The build scans each comment for runs of exactly two or three digits. A run is skipped if it is below 30 or above 300, follows a `/` (the seed's total, as in `164/216`), has `<`, `>` or `+` directly attached on either side, follows `more than`, `less than`, `over`, `under` or `sub` as a whole word, is followed by `CPH` or by `%` or a time unit (`m`, `min`, `h`, `hr`, `s`, `sec` and their longer or plural forms), or is part of a clock time such as `1:45`. A `-` does not skip a number.
- CR is the first remaining number whose CPH falls between 30 and 175, inclusive. If there is none, the entry has no CR. Only entrants with status `done` and a finish time can have one, so a comment on a DNF or DQ is never parsed.
- A comment may name several numbers, and the first plausible one wins, so `200 check 199/216` reads as 200. A number like the 50 in `50/50` or the 75 in `19.75` can also pass when it fits the pace range.
- The parsed CR is stored in `comments.json` and CPH is computed in the browser from the finish time in `participants.json`. They appear only where races are listed row by row: each finisher's row in an expanded race under Latest results, and the racer page's **View finish times** table.

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

The win chance that $a$ beats $b$ is $1 / (1 + e^{-(\mu_a - \mu_b)/\sqrt{\sigma_a^2 + \sigma_b^2 + 2\beta^2}})$. It drives upset tags and the head-to-head "Win chance" column, and never uses points.

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

Each badge records the index of the race that unlocked it. Shares are of the 397 racers as of 2026-10-31.

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
| Sniped! | Finish 2nd, less than 5 seconds behind the winner. | 7.3% |
| RETIME | Finish less than 1 second from another racer. | 17% |
| Palindrome | Finish with a time whose H:MM:SS digits, hour unpadded and colons removed, read the same both ways. | 34% |
| God Route | Win by more than 10 minutes in a field of 7 or more while not its top-rated racer. | 13% |
| Big Game Hunter | Win a race with 16 or more entrants, anonymous entrants included. | 13% |
| Glitched | Finish a glitched race. | 10% |
| Glitched! | Finish 10 glitched races. | 3.5% |
| G1!tch3d!!! | Finish 30 glitched races. | 1.8% |
| G%!#ch@d!#? | Finish 100 glitched races. | 0% |
| The Upside Down | Finish an inverted race. | 31% |
| Mirror Universe | Finish 30 inverted races. | 1.8% |
| Jet Seed | Finish a Casual Boots race in under 70 minutes. | 9.1% |
| Jet Lag | Win a Casual Boots race in 100 minutes or more. | 2.8% |
| Driving Lesson | Finish a spoiler race. | 8.3% |
| Cruisin' | Win a spoiler race. | 3.3% |
| Entranced | Enter an entrance race on 5 days in a row. | 7.1% |
| Entranced! | Enter an entrance race in 20 weeks in a row. | 2.8% |
| What? | Enter a mystery race. | 30% |
| I Like It Here | Finish 10 mystery races. | 4.5% |
| BUSTIN' | Finish 30 mystery races. | 1.8% |
| Both Ends | Finish a mystery race in under 30 minutes, and another in over 130 minutes. | 0.3% |
| Grocery Stick | Finish in the exact middle of an odd field of 5 or more entrants. | 56% |
| Rando Sicko | Finish 5 races in one day. | 0.8% |
| Touched Grass | Enter a race after 30 or more days away, counting from 2026-08-01. | 20% |
| Yap | Comment on 11 races in a row. | 8.8% |
| Step Letter | Be in a race of 5 or more where everyone finished in alphabetical order. | 2.3% |
| Rettel Pets | Be in a race of 5 or more where everyone finished in reverse alphabetical order. | 1.3% |

A badge is either a counter, which unlocks when a count reaches its target and fills its border as it progresses, or a one-race badge, which unlocks on a single result. On a racer page, earned badges come first in the order they were won, oldest first, and locked badges follow in table order. Hovering a badge shows the share of racers who hold it, to two significant digits, counted on the page from the current data. Clicking a badge, here or in the Latest results sidebar, opens a panel with every racer who holds it, earliest unlock first. The sidebar's **Latest badges** lists the 12 most recent unlocks by anyone, however long ago. In the list below, a **finish** is status `done` with a place, a **day** is the race's Eastern Time date, a **week** runs Monday to Sunday, and the **field** is the race's entrant count, which includes DNFs and anonymous entrants.

- **Streak and count badges:** win and finish runs follow the racer's own race sequence across all modes, and a DNF or DQ ends a finish run. CenturianV and BlazeIt count finishes. >174 counts every race entered and not won, DNFs included. The Glitched badges count finishes in the glitched mode, The Upside Down and Mirror Universe count finishes in the inverted mode, and I Like It Here and BUSTIN' count finishes in the mystery mode.
- **What?:** any entry in the mystery mode counts, DNFs included.
- **Yap:** the racer's own races in order, DNFs included, each with a comment that isn't blank, the same comments that `comments.json` publishes. A race without one ends the run. Only whether a comment exists reaches `highlights.py`; its text never enters `participants.json`.
- **Touched Grass:** the racer needs an earlier race, and the gap since it is counted only from 2026-08-01, so a break that started before then counts from that date and the earliest unlock is 2026-08-31. Any entry counts as the return, DNFs included.
- **Entranced and Entranced!:** any entry in the entrance mode counts, DNFs included, and several races on one day or in one week count once.
- **Rando Sicko:** finishes only, counted per day.
- **Sniper and Sniped!:** the photo tag's condition. Places 1 and 2 are both timed and the margin is under 5 seconds. Sniper goes to the winner and Sniped! to the runner-up.
- **RETIME:** two finishers' raw times, before rounding to whole seconds, are less than 1 second apart, at any places. Both earn it, and so does everyone in a chain of such gaps. Only identified, timed finishers count.
- **Jet Seed and Jet Lag:** the seed preset is named exactly Casual Boots.
- **Both Ends:** one mystery finish under 30 minutes and another over 130 minutes, in either order. It unlocks on the race that completes the pair.
- **Grocery Stick:** the field is odd and at least 5, and the finisher whose place is the middle one, `(field + 1) / 2`, earns it.
- **Step Letter:** the field is at least 5, every entrant is identified and finished, and the names in place order never decrease when compared without case. Every entrant earns it.
- **Rettel Pets:** Step Letter's rule with the names in place order never increasing.
- **God Route:** places 1 and 2 are both timed and more than 10 minutes apart, using the exact times, and the field is at least 7. "Top-rated" is the entrant with the best pre-race rating among those who are not provisional and raced in the previous 90 days. When no entrant qualifies, the winner earns it.
- **Omnivore:** needs 7 of the 8 modes, because requiring all 8 left 0.8% of racers holding it.

Each badge ships an animated image and a still under `site/assets/badges/`. Earned badges animate, and locked badges show the grey still. To add or remove a badge, see [Badge_Guide.md](Badge_Guide.md).

The badge art in `site/assets/badges/` includes [Twemoji](https://github.com/twitter/twemoji) graphics, licensed [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/), for Step Letter, Rettel Pets, BlazeIt, Both Ends, Grocery Stick and RETIME.

### Streaks

- **Daily:** consecutive Eastern Time dates with at least one entry. The current streak is the run ending on the racer's last race day.
- **Win, podium and finish:** runs over the racer's own race sequence across all modes. Podium means places 1-3; finish means finisher. The current streak is the run that includes the racer's latest race, and is absent if that race broke it.

### Presets

A preset is the name after the mode in the race's `info` first line, trimmed, cut at any seed URL (along with a room slug such as `owner/slug` just before it), with `[x]` turned into `(x)` and any `Grab Bag (...)` collapsed to `Grab Bag`. Each preset belongs to its mode. Statistics are races, entries, DNF rate (entries that are not finishers), median time, median winning time, mean field size, and the fastest finish.

- **Hardest preset:** the highest DNF rate among presets with 8 or more races.
- **A racer's best preset:** the lowest ratio of the racer's median time to the preset's median time, among presets with 8 or more races where the racer has 3 or more timed finishes.

### Records

- **Mode records:** one progression step per race that sets a new record, per mode.
- **Photo finishes** and **blowouts:** the 10 smallest and 10 largest margins between places 1 and 2. Blowouts count only fields of 5 or more racers.
- **Big fields:** the 10 largest fields that had an identified winner.
- **Most races:** the 10 racers with the most entries.
- **Most-met rivals:** the 10 pairs of racers who entered the most races together. Any entry counts, DNFs and DQs included.
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

The page runs top to bottom as: **01 Latest** (the newest results with their tags), **02 Racers** (the leaderboard, with the rating column), **03 Records** (the hall of records), **04-10** the analytics panels (schedule, participation, and finish times), and a page per racer with their rating, badges, personal bests, and charts. Records runs in three parts. **Across all modes** holds the record cards and streak boards. **Record progression** has a step chart per mode, three to a row, on one shared date axis with each mode's own time axis; each header shows the current record, and the chart draws the first recorded time hollow, each break filled, and the current record with a halo. **By mode** has a mode picker that drives the current-record summary strip, the finish-time violins of the mode's presets (see Finish times), and the presets table, whose Gold median column is the preset's median winning time. Only By mode follows the picker, and it opens on the most-raced mode. A racer page opens with a summary strip: Rank & Rating, Podiums, Record W-L-D, H2H Win Rate, Badges, Nemesis and Favourite Victim. Every tile covers all modes and the whole archive, whatever mode is selected. Nemesis and Favourite Victim are the opponents with the lowest and highest head-to-head win rate among those with at least five decisive meetings. Below the badges and personal bests, the rating chart has one point per race day: the racer's rating after their last race that day, across all modes. Like the record charts it is a step line that holds each rating until the next race day and runs on to the latest race in the archive; it draws the first race day hollow, the current rating with a halo, and the starting rating of 1000 as a dashed line. Pointing anywhere in the plot snaps to the nearest race day, whose tooltip gives that day's change and races. There is no race-day calendar. A nav bar under the masthead links to the ten sections, grouped as Racing (01-03) and Ladder Data (04-10); Ladder Data is hidden on phone-width screens, and the whole bar is hidden on racer pages.

### Share card

The **Share** button beside **Follow** on a racer page opens a panel with a 600 × 300 PNG of the racer's standing. The browser draws it from the loaded data, and nothing is uploaded. The panel offers four actions:

- **Copy image**, where the browser can write images to the clipboard.
- **Download**, named `<name>-step-ladder-<data date>.png`.
- **Share…**, where the browser can share files, mostly on phones.
- **Copy link**, the racer page's address.

Like the summary strip, everything on the card covers all modes and the whole archive.

- **Identity:**
  - The avatar, or the page's tinted letter when the racer has none.
  - The name, shrunk and then shortened to fit.
  - The month of the racer's first race.
- **Standing:**
  - The rating rank, out of all rated racers.
  - The rating, labelled provisional below 5 finishes.
  - Under the rank, the 7-day rank movement as on the leaderboard: ▲, ▼ or new.
  - A racer without a rating shows Unrated.
- **Rating line:** the rating chart's race-day step line, with the starting rating dashed and the current rating haloed.
  - The highest rating is marked **Peak** while the racer is below it. **All-time high** shows when they are on it.
  - The line needs two race days. Without it, the four numbers spread across the card.
- **Numbers:** races, wins (first places), podiums and H2H win %, as on the leaderboard and the summary strip.
- **Badges:** earned badges only, rarest first by holder count. Badges held by equally many racers keep the order they were won.
  - The row holds 15 tiles. With more than 15 badges, the last tile reads +N for the rest.
  - The rarest badge is named with its holder share.
  - A racer with no badges sees instead the locked counter badge with the largest share of its target done, with a progress bar. With no progress anywhere, the card says "No badges yet".
- **Nemesis:** the racer page's Nemesis, with the racer's W–L against them. Favourite Victim is left off, so a shared card never singles out a racer the sharer beats.
- **Footer:** the site name and address, and the date of the data snapshot in Eastern Time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use mocked API responses and do not require network access. The avatar tests include a check that every file in the committed `site/avatars/` is named by the committed `participants.json` and is a valid raster image. A named file that is missing does not fail it, so an avatar that racetime.gg stops serving cannot block a deploy.

The suite also rebuilds all three datasets from the committed `archive/` and fails if they differ from the committed `site/data` files, ignoring `generated_at`. After changing `scripts/build_stats.py`, `scripts/participant_stats.py` or any module they call, run `python3 scripts/build_stats.py` and commit the result.

## Project layout

```text
scripts/race_archive.py           Incremental sync of raw races, racer names and avatar observations
scripts/build_stats.py            Archive validation and aggregation
scripts/participant_stats.py      Per-racer dataset: records, avatars, assembly of schema 3
scripts/avatar_mirror.py          Avatar file names and the mirror of them in site/avatars/
scripts/ladder_rating.py          Ladder rating model and backtest
scripts/highlights.py             Tags, badges, streaks, presets, records and facts
scripts/comment_stats.py          Racer comments dataset
scripts/mode_overlap.py           Mode overlap analysis and report
scripts/badge_images.py           Converts badge art to the standard WebP pair (needs ImageMagick 7)
scripts/version_assets.py         Content versioning for the static assets
tests/test_race_archive.py        Archive sync unit tests
tests/test_avatar_mirror.py       Avatar naming, precedence and mirror unit tests
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
archive/racer-avatars.json        Last observed leaderboard avatar of each racer
site/data/race-stats.json         Generated aggregate dataset
site/data/participants.json       Generated per-racer dataset
site/data/comments.json           Generated racer comments, loaded lazily
site/avatars/                     Mirrored avatar images, one for each avatar participants.json names
site/version.json                 Generated content version manifest
site/index.html                   Static dashboard document
site/app.js                       Native SVG chart rendering
site/racer-charts.js              Finish-time violins, scatter, trend, start-time heatmap, and step charts
site/leaderboard.js               Racer leaderboard and head-to-head views
site/store.js                     Local storage wrapper and followed racers
site/ui.js, ui.css                Shared avatars, speech bubbles, tags, badges, and dialogs
site/feed.js, feed.css            Latest results feed
site/records.js, records.css      Hall of records
site/racer-extras.js, .css        Racer page summary strip, badges, personal bests, and rating chart
site/share-card.js, .css          Racer share card: a 600 x 300 PNG drawn in the browser
site/easter-eggs.js, .css         Hidden extras
site/version-check.js             Reloads the page when a new version ships
site/styles.css                   Responsive visual system
site/assets/fonts/                Self-hosted Barlow fonts (SIL Open Font License)
site/assets/badges/               Badge images, an animated WebP and a still for each badge
Badge_Guide.md                    How to add or remove a badge
```

API behavior and field definitions are recorded in [Racetime_API.md](Racetime_API.md).