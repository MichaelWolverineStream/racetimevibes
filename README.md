# ALTTPR Step Ladder Race Signals

A static dashboard built from the public racetime.gg archive, from 2025-04-20, for the `alttpr-ladder` category. Beyond the schedule analytics it tracks racers: a ladder rating, overall, per mode and per season, personal bests, streaks, badges, records, and a feed of the latest results.

The dashboard is published at <https://michaelwolverinestream.github.io/racetimevibes/>.

- **Ratings:** an in-house ladder rating from placement alone, overall, per mode and per season, with win chances between any two racers.
- **Racers:** a leaderboard, racer pages, head-to-head records, player cards and followed racers.
- **Highlights:** race tags, badges, streaks, records and a feed of the latest results.
- **Schedule:** turnout and start rates by hour and weekday, joined with the alttpr.racing ladder schedule.

This README covers the project, the [ladder rating](#ladder-rating) and [privacy](#privacy). Everything else is in [docs/](#documentation).

## Requirements

- Python 3.11 or newer, standard library only. The site is plain HTML, CSS and JavaScript with no build step.
- A network connection for the syncs and the avatar mirror.
- ImageMagick 7, only for `scripts/badge_images.py` (badge art) and `scripts/pokemon_images.py` (sprite framing).

## Quick start

```bash
python3 scripts/race_archive.py       # sync new races from racetime.gg into archive/ (network)
python3 scripts/racer_bios.py         # optional: mirror racers' profile bios into archive/ (network)
python3 scripts/schedule_archive.py   # mirror the alttpr.racing ladder schedule into archive/ (network); the build needs it
python3 scripts/build_stats.py        # build site/data/*.json from archive/ (offline)
python3 scripts/avatar_mirror.py      # mirror the avatars participants.json names into site/avatars/ (network)
python3 scripts/version_assets.py     # restamp the asset URLs after editing site/
python3 -m unittest discover -s tests
python3 -m http.server 8000 -d site   # preview at http://localhost:8000; the page needs HTTP, not file://
```

Tests:

```bash
python3 -m unittest discover -s tests             # production suite, a few seconds; gates every deploy
python3 -m unittest discover -s tests/analyses    # analysis suite, about half a minute
```

## Deployment and data refresh

`.github/workflows/pages.yml` runs the production suite and deploys `site/` to GitHub Pages on every push to `main`. A bot syncs, builds and commits the data every two hours as `YYYY-MM-DD data`, so pull before you start, and never pass `--lock-seasons` locally. The refresh steps, their options, the tests, the workflows and how to resolve a conflict with the bot are in [docs/operations.md](docs/operations.md).

## Layout

```text
scripts/          Python pipeline: syncs, build, avatar mirror, asset versioning, and the standalone analyses
tests/            Production suite; tests/analyses/ holds the analysis suite
archive/          Verbatim racetime.gg races, racer names, avatars and bios, plus the ladder schedule; never deployed
season-ratings/   One ledger per locked season, written once by the bot; never deployed
assets/pokemon/   Pokémon sprites and pokedex.json; never deployed
site/             The page: HTML, CSS, JS, generated data in site/data/ and mirrored avatars in site/avatars/
docs/             Developer documentation, listed under Documentation
.github/          Workflows: test and deploy, analysis tests, scheduled data and bio refresh
.claude/          Agent rules and skills, beside CLAUDE.md
schedule-plan.json  Season, bands, composition and events for the schedule planner
```

## Ladder rating

`scripts/ladder_rating.py` rates every racer from who beat whom. It is in-house and deliberately not racetime.gg's score.

**Racetime.gg points are ignored entirely.** `score` and `score_change` are never published or used by any racer metric. racetime.gg's recorded `score` is read in only one place, as the benchmark in the [Backtest](#backtest).

### In plain words

**Only placement counts.** Finish times are never read, because the game is volatile and a seed's luck says little about skill. A win by one second is worth the same as a win by an hour. Within a race, every pair of entrants is a small match: a finisher beats anyone placed behind them, any finisher beats a DNF or DQ, and two non-finishers, or two racers sharing a place, are skipped. Beat someone you were expected to lose to and the rating moves a lot; beat someone you were expected to beat and it barely moves. Newcomers move the most, because the ladder knows least about them.

The overall rating covers every mode, and each mode also has a rating of its own (see [Mode ratings](#mode-ratings)). **Winning never costs points**, and every newcomer shows exactly **1000** and climbs as the ladder gets to know them. **Your recent races matter most**: a race's weight halves after about 14 later races of five racers. A big race moves you less than its number of opponents suggests, because its results all share the same racers. Nothing decays while you are away.

### Model

This is the Bayesian Bradley-Terry "full pair" model of Weng and Lin (2011), the method behind OpenSkill. Each racer has a skill $\mu$ and an uncertainty $\sigma$, and a newcomer starts at $\mu_0 = 25$, $\sigma_0 = 12$. For every racer $i$, sum over the opponents $q$ they have a decisive pair with, all from the pre-race states, so the update is simultaneous:

$$c = \sqrt{\sigma_i^2 + \sigma_q^2 + 2\beta^2} \qquad p = \frac{1}{1 + e^{(\mu_q - \mu_i)/c}} \qquad s = 1 \text{ (ahead)},\ 0 \text{ (behind)}$$

$$\Omega = \sum_q \frac{\sigma_i^2}{c}(s - p) \qquad \Delta = \sum_q \frac{\sigma_i}{c} \cdot \frac{\sigma_i^2}{c^2}\, p(1-p)$$

$$d = (n - 1)^{-\alpha} \qquad \mu_i \leftarrow \mu_i + d\,\Omega \qquad \sigma_i^2 \leftarrow \max\big(\sigma_i^2 \cdot \max(1 - d\,\Delta, \kappa),\ \sigma_{floor}^2\big)$$

$d$ damps a race of $n$ entrants by its size. Every pair counts as a match, but a race's pairs share their racers, so they are not independent results: without $d$ a 32-racer race would count as 31 of them, and with $\alpha = \tfrac12$ it counts about as much as 6.

The win chance that $a$ beats $b$ is $1 / (1 + e^{-(\mu_a - \mu_b)/\sqrt{\sigma_a^2 + \sigma_b^2 + 2\beta^2}})$. It drives upset tags and the head-to-head "Win chance" column, and never uses points. Both always use the overall rating, even with a mode picked (see [Mode ratings](#mode-ratings)).

### Recency

Uncertainty shrinks with every race but never below $\sigma_{floor}$. The floor keeps every race's update above a minimum size, so old races fade by **race count**, not by the calendar, and a racer who is away keeps their points. For a racer at the floor against evenly matched opponents, a race's weight halves after about $\ln 2 / g$ later races of $n$ entrants, where $g = (n-1)^{1-\alpha}\, \sigma_{floor}^2 / \big(4(2\sigma_{floor}^2 + 2\beta^2)\big)$: about 14 races of 5 entrants, 28 of 2 and 5 of 32.

### Parameters and tuning

| Parameter | Value | Meaning |
|---|---|---|
| $\beta$ | 8 | per-match performance noise |
| $\sigma_0$ | 12 | a newcomer's starting uncertainty |
| $\sigma_{floor}$ | 4 | uncertainty never shrinks below this |
| $\alpha$ | ½ | field-size damping: a race's update is scaled by $(n-1)^{-\alpha}$ |
| $\kappa$ | 1e-4 | smallest share of its variance a racer keeps after a race |
| $\mu_0$ | 25 | a newcomer's starting skill |

$\beta$, $\sigma_0$ and $\alpha$ were picked by `scripts/rating_lab.py` from races before 2026-01-01 only, among the settings that move a veteran's points at least as much as $\beta = 12$, $\sigma_0 = 8$ without damping do, and then compared with four other algorithms on 2026 races, once (see [Rating comparison](docs/analyses.md#rating-comparison)). The floor was not tuned for accuracy: it was set to 4 by choice, for a "livelier" ladder. A calmer floor predicts a little better but feels stuck. 2026 races were never used for tuning, so the comparison is out of sample.

### Points

The displayed points are $1000 + s \cdot (\mu - k\sigma)$, the "conservative rating" idea from TrueSkill and Xbox levels. With $k = \mu_0 / \sigma_0 \approx 2.083$ a newcomer shows exactly 1000, and as $\sigma$ shrinks the points climb toward $\mu$. The scale $s = 400 / (\ln 10 \cdot \sqrt{2\sigma_{floor}^2 + 2\beta^2}) \approx 13.73$ makes 400 points about 10:1 odds between established racers, as in Elo. The pipeline publishes each racer's points, $\mu$ and $\sigma$ (4 decimal places), overall and in each mode they have raced, and every race entry's points after that race: overall, in the race's mode, and, for a race in a season, in the season and in the season's mode (see [Season ratings](#season-ratings)). The season points are null for a race outside every season.

### The guarantee

A winner's points never fall. Every pair a winner is in is decided in their favour, so $\Omega > 0$, and $d > 0$, so $\mu$ rises; $\sigma$ never rises, so $\mu - k\sigma$ rises too, and rounding is monotone. Finish times have no effect at all. Tests enforce both across every archived race, for the overall rating and for every mode rating, and for every season's overall and mode rating.

### Mode ratings

Each mode also has its own rating: the same model with the same parameters, run on that mode's races alone. Nothing was retuned for it. A mode rating never reads another mode's races, so a racer's first race in a mode starts at exactly 1000 however experienced they are elsewhere, and a racer is provisional in a mode until they have 5 finishes in it. The guarantee holds in every mode. Seasons have mode ratings too (see [Season ratings](#season-ratings)).

Mode ratings are for standings: the leaderboard with a mode selected and the rating chart on a racer page with a mode picked. Predictions stay on the overall rating, because it predicts results inside a mode better than the mode's own rating does, as the [Backtest](#backtest) shows. Most modes have too few races for a rating that starts from nothing, and the gap is widest in the smallest modes. So the head-to-head Win chance column, the Upset tag and the top-rated racer of God Route, Big Routing and Upsetti always use the overall rating.

### Season ratings

A **season** is one run of the ladder, as the alttpr.racing schedule numbers them (see [Seasons](docs/definitions.md#seasons)). Each season also has its own ratings, overall and in every mode, beside the all-time ones: the same model with the same parameters, run on that season's races alone. Nothing was retuned for it. `ladder_rating.rate_by_season` computes them.

- **Full reset:** every racer debuts at exactly 1000 in every season, and again in every mode of it, however they rated before. A racer is provisional in a season, or in a season's mode, until they have 5 finishes there.
- **Standings only:** season ratings carry no $\mu$ or $\sigma$. Predictions stay on the all-time overall rating, as they do for mode ratings: win chance, the Upset tag, and the top-rated racer of God Route, Big Routing and Upsetti.
- **Which season a race is in:** the season whose span holds its start. Every included race counts, scheduled or not, and a race in the break between two seasons counts toward the earlier one. The 5 `JOIN_EARLY` minutes let a race that started just before its first slot count.
- **Schedule lag:** the schedule mirror syncs once a day and races every two hours, so a new season's first day can count toward the old season until the next daily schedule sync. This is display only: it cannot lock a wrong boundary.
- **Locked for good:** once a season has ended and its races are settled, the bot locks its ratings into a ledger in `season-ratings/`, and they never change again, even if the model does (see [Season locking](docs/datasets.md#season-locking) and [Season ledgers](docs/datasets.md#season-ledgers)).

### Backtest

`python3 scripts/ladder_rating.py --backtest` replays the archive in order. Before each race it predicts every decisive pair of entrants: finishers by place, a finisher over a non-finisher, and two non-finishers are skipped. It scores the pairs from 2026-01-01 (out of sample) apart from those before (the tuning period), by accuracy and log loss.

- The ladder rating is scored from the overall rating, and the mode ratings on the same pairs, each predicted from the two racers' ratings in the race's mode.
- racetime.gg is scored from the `score` each entrant carried into the race, which the archive stores (833 when missing). Its log loss uses the best-fit logistic curve on the score gap, fitted on the same pairs, which flatters it.
- The report also counts winners whose displayed points fell, overall and in their race's mode, and the median points a veteran (past their 50th race) moves per race.

**How racetime.gg differs.** racetime.gg runs TrueSkill with default parameters on finishing order only, ties every DNF and DQ behind the last finisher, shows $\max(0, \mathrm{round}((\mu - 2\sigma) \cdot 100))$ so newcomers start at 833 and the score climbs with volume as $\sigma$ shrinks, and keeps one leaderboard per goal. Both ratings read placement only. The ladder rating rates all modes together and each mode on its own, starts everyone at 1000, shows points on an Elo-style scale, and its floor keeps scores responsive.

**Trade-offs of the ladder rating:**

- A crushing win counts the same as a narrow one.
- Experience is rewarded: a prodigy with few races sits below established racers until $\sigma$ shrinks.
- A mode rating knows only its mode: a racer strong elsewhere starts a new mode at 1000 and climbs like any newcomer.
- Early on, even a loss can add points, because uncertainty falls faster than skill.
- Clearly weaker racers still drift down, because the number stays honest.
- A big race moves a rating less than its number of opponents suggests. Damping trades the swing of a large field for steadier, more accurate ratings.

## Privacy

- **`race-stats.json`** is anonymous. It holds slot and race counts, distributions and bands, never entrant arrays, IDs, names, profile links, or Twitch fields. The schedule's slot and race ids are used only inside the build.
- **`participants.json`** publishes each racer's public display name and stable user ID, because a leaderboard requires them, plus the file name of their mirrored avatar.
  - That name is the first 16 hex digits of the SHA-256 of the avatar's racetime.gg URL, plus the URL's lowercase extension, for example `3f9ac01d2e7b8a64.jpg`. It carries nothing from the upload's own file name.
  - A racer has one only if the URL is directly under `https://racetime.gg/media/`, names the file in URL-safe ASCII (letters, digits, `.`, `_`, `~`, `-` and `%`, as in percent escapes), and ends in `png`, `jpg`, `jpeg`, `gif` or `webp`.
  - The avatar follows the racer's most recently observed profile: the category leaderboards each sync, or their latest race file among the races the dashboard includes, so never a `ladder_test` race, whichever is newer, and the leaderboards win a tie. A racer whose latest observation has no avatar has none, unless a Pokémon fills in (see [Pokémon avatars](docs/avatars.md#pokémon-avatars)).
  - A Pokémon avatar's name is `pokemon-` plus a sprite file name, `pokemon-<pokédex number>[-<variant>].png`, for example `pokemon-201-b.png`. It reveals only which Pokémon the racer has, which comes from their public bio or from a random pick. Bio text is never published.
  - `avatar_changes` adds, for racers whose avatar changed while the archive was watching, the Eastern Time date the archive first saw the change (see [Avatar changes](docs/avatars.md#avatar-changes)). It reveals nothing about the old avatar, which is neither named nor mirrored, and nothing from the bio beyond the date the favourite Pokémon changed.
- **`comments.json`** publishes the raw post-race comments of included races, stripped and capped at 500 characters, keyed by race name and user ID, each with the chest count parsed from it, or `null`.
- **`site/avatars/`** holds copies of exactly the files `participants.json` names, raster images only. The page serves avatars from there, so a viewer's browser never contacts racetime.gg for them.
  - The copies are byte for byte, so any metadata an upload carries, such as a JPEG's EXIF block, is served as racetime.gg serves it.
  - A replaced or removed avatar leaves `site/avatars/` but stays in the repository's git history.
- **Season fields** in `participants.json` are integers, dates and flags tied to the racers' published IDs. They add no field the contract above excludes.
- **Never published:** full names, discriminators, pronouns, flair, Twitch fields and racetime.gg points and score fields.

`archive/` is different. It mirrors racetime.gg's unauthenticated public API verbatim, including entrants' full user profiles and post-race comments. It also holds racers' public profile bios, verbatim from their racetime.gg profile pages, with the earlier versions it has seen since 2026-11-05. It is committed to this public repository, so anyone can read it there, but it sits outside `site/` and is never deployed with the dashboard.

`season-ratings/` is also outside `site/`. It holds racers' user IDs with their season points, is committed beside `archive/`, and is never deployed.

## Documentation

| Document | What it covers | Read before |
|---|---|---|
| [docs/definitions.md](docs/definitions.md) | Included races, Eastern Time, finishers and winners, scheduled slots, seasons | Changing anything that counts races, entrants or slots |
| [docs/archive.md](docs/archive.md) | The archive files, how a sync works, names, avatars, bios and the schedule mirror | Changing a sync script or reading `archive/` |
| [docs/racetime-api.md](docs/racetime-api.md) | The racetime.gg endpoints and fields the scripts read | Reading a new endpoint or field |
| [docs/datasets.md](docs/datasets.md) | The three published datasets, the schedule metrics, season locking and ledgers, chest counts | Changing a field in `site/data/` or the season lock |
| [docs/highlights.md](docs/highlights.md) | Race tags, badges, streaks, presets, records and claims to fame | Changing `highlights.py` |
| [docs/avatars.md](docs/avatars.md) | The avatar mirror, Pokémon avatars, sprites and avatar changes | Changing an avatar script or `assets/pokemon/` |
| [docs/dashboard.md](docs/dashboard.md) | What the page shows and does, section by section | Changing `site/` |
| [docs/operations.md](docs/operations.md) | The refresh steps and options, asset versioning, tests, workflows and the bot | Running the pipeline by hand or changing a workflow |
| [docs/analyses.md](docs/analyses.md) | Mode overlap, schedule proposals, the schedule strategy comparison and the rating comparison | Changing a standalone analysis or `schedule-plan.json` |
| [.claude/skills/badges/SKILL.md](.claude/skills/badges/SKILL.md) | The checklist to add, remove or change a badge | Touching a badge |
| [CLAUDE.md](CLAUDE.md) | The rules for coding agents, with area rules in `.claude/rules/` | Working with an agent |

## Credits

- Race data from [racetime.gg](https://racetime.gg) and the ladder schedule from [alttpr.racing](https://alttpr.racing).
- The badge art in `site/assets/badges/` includes [Twemoji](https://github.com/twitter/twemoji) graphics, licensed [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/), for Step Letter, Rettel Pets, BlazeIt, Both Ends, Grocery Stick and RETIME.
- The Barlow fonts in `site/assets/fonts/` are licensed under the SIL Open Font License (see `site/assets/fonts/OFL.txt`).
