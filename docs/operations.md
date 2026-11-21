# Operations

- **Purpose:** how to refresh the data, restamp the static assets, run the tests, and how the workflows test, refresh and deploy.
- **Read before:** running the pipeline by hand, or changing a workflow or `version_assets.py`.
- **Code:** `.github/workflows/`, `scripts/version_assets.py`, and the pipeline scripts in [README › Quick start](../README.md#quick-start).

## Refreshing the data

Run the steps in the quick-start order:

1. `race_archive.py` saves new races, racer names and avatar observations into `archive/` (see [archive.md](archive.md)).
2. `racer_bios.py` saves the bios of racers who raced since their last check (see [Bios](archive.md#bios)). It reads the races just synced, and the build turns bios into [Pokémon avatars](avatars.md#pokémon-avatars).
3. `schedule_archive.py` mirrors the ladder schedule (see [Ladder schedule mirror](archive.md#ladder-schedule-mirror)). The build joins every slot to its race, so it refuses an archive without a mirror.
4. `build_stats.py` builds the [published datasets](datasets.md) from the archive alone. `--lock-seasons` also writes the ledger of every ended, settled season into `season-ratings/` (see [Season locking](datasets.md#season-locking)); only the scheduled job passes it.
5. `avatar_mirror.py` runs last, because it reads the file names `build_stats.py` wrote to `participants.json` (see [Avatar mirror](avatars.md#avatar-mirror)).

Options:

- `--archive` points the syncs, the build, `avatar_mirror.py` and `ladder_rating.py` at another archive.
- `--output`, `--participants-output` and `--comments-output` redirect each dataset of `build_stats.py`.
- `avatar_mirror.py` takes `--participants` and `--output`.
- `--pokemon` on `build_stats.py`, `avatar_mirror.py` and `pokemon_avatars.py` points at another sprite directory, and `pokemon_avatars.py --refresh-pokedex` rewrites the pokédex.
- `ladder_rating.py --backtest` replays the archive and scores the rating's predictions (see [Backtest](../README.md#backtest)).

## Versioning static assets

Run `python3 scripts/version_assets.py` after editing `site/index.html` or any file in `VERSIONED_ASSETS`. It hashes them into one token, rewrites every reference to `asset?v=<token>` and writes `site/version.json`. A deploy therefore changes the asset URLs, so browsers never use a stale cached copy, and an open page reloads onto the new version (see [Automatic updates](dashboard.md#automatic-updates)).

- `--check` reports staleness without writing, and a test runs the same check.
- `--site` points it at another site directory.
- A new stamped site file goes into `VERSIONED_ASSETS`; a test fails when `index.html` references a local stamped file that is not listed.
- The Barlow fonts in `site/assets/fonts/` are not stamped: replace one by adding a file under a new name.

## Tests

```bash
python3 -m unittest discover -s tests                 # production suite, about ten seconds; gates every deploy
python3 -m unittest discover -s tests -k Committed    # its committed-data checks alone, as the bot runs them
python3 -m unittest discover -s tests/analyses        # analysis suite, about a minute
```

How to write a test, and which suite and tier it belongs to, is in [testing.md](testing.md). No test uses the network.

The production suite's `Committed*` classes check the committed files.
- They rebuild all three datasets from the committed `archive/` and `season-ratings/`, and fail on any difference from `site/data` other than `generated_at`. Commit a rebuild with every build change.
- They check the avatar files:
  - every file in `site/avatars/` is named by `participants.json` and is a valid raster image;
  - every `pokemon-*` avatar is a byte copy of its sprite;
  - `assets/pokemon/` holds one square palette PNG of at least 256 pixels for every species from 1 to 1025.
- A published avatar whose file is missing does not fail, so a vanished upload cannot block a deploy. A missing `site/avatars/` folder does fail.

`tests/analyses/` covers the standalone analyses in [analyses.md](analyses.md). It has no `__init__.py`, so the production discovery skips it.
- `test_rating_monitors.py` holds the rating monitors: statistics over the growing archive, such as the rating beating racetime.gg on 2026 pairs.
- A monitor fails the weekly run instead of blocking a deploy. A failure is a prompt to look at the numbers with `ladder_rating.py --backtest`, never a reason to retune on 2026 races.

## Deployment

- `.github/workflows/pages.yml` runs the production suite and `node --check` on `site/*.js`, then deploys `site/` whenever `main` is pushed, or from **Actions › Deploy GitHub Pages**. Pages must be set to deploy from GitHub Actions.
- After the tests pass, their job uploads the Pages artifact; a separate deploy job waits for it, avoiding an immediate artifact lookup that can race the upload.
- `.github/workflows/analysis-tests.yml` runs the analysis suite on pushes to `main` and on pull requests that change `scripts/`, `tests/analyses/`, `schedule-plan.json` or the workflow itself, weekly against the newest archive (the run that carries the rating monitors), and on demand. It never blocks a deploy.

### Scheduled data refresh

`.github/workflows/update-data.yml` runs `race_archive.py`, `schedule_archive.py`, `build_stats.py --lock-seasons` and `avatar_mirror.py`, then the production suite's `Committed*` tests (`-k Committed`), the deploy's checks on the archive and the published files. An outside trigger starts it through `workflow_dispatch` about every two hours, a scheduled run at 00:17 UTC is the daily backup, and **Actions › Update race data** starts it by hand. One input, `sync-bios`, off by default and not offered on a manual run, also runs `racer_bios.py` between the race and schedule syncs.

- When a sync added or refreshed a race or schedule file, or the build locked a season, it commits `archive/` and `season-ratings/` to `main` as `YYYY-MM-DD data`.
- When the datasets changed beyond `generated_at`, or `site/avatars/` changed, the same commit includes `site/data` and `site/avatars`, and the workflow starts **Deploy GitHub Pages** explicitly, because pushes made with `GITHUB_TOKEN` trigger no workflows.
- A failed schedule sync does not stop the run: the step is `continue-on-error`, so it shows as failed while the rest goes on. The sync writes nothing when it fails, so the build uses the mirror of the last good sync, which can only hold back the newest slots (see **Counted through** under [Schedule](definitions.md#schedule)) and delay a season lock.
- A run that finds nothing new, or whose race sync, bio sync, build, mirror or committed-data check fails, commits nothing. `main` therefore never holds data that the deploy would refuse.

`.github/workflows/update-bios.yml` runs daily at 01:47 UTC and only calls `update-data.yml` with `sync-bios` on. A manual **Update racer bios** run is how to sync bios on demand.

- It sets no `concurrency` group on purpose: the callee's `update-data` group already queues it behind a running data run, and the same group on the caller would deadlock.
- GitHub keeps one waiting run per group, so a data run can cancel a waiting bio run. Nothing is lost: a racer stays due until checked.

### Naming a season

From Season 11 the previous season's champion picks an animal. Add `"N": {"animal": "…"}` to `season-names.json`, run `python3 scripts/build_stats.py` (the rules are under [Season fields](datasets.md#season-fields)), run the tests, and commit the file with `site/data`. Pass no `--lock-seasons`: the ledgers stay untouched.

### Giving a badge by hand

Add `{"racer": "<user id>", "badge": "<badge id>", "date": "YYYY-MM-DD"}`, with an optional `"note"`, to `overrides` in `badge-overrides.json`. The user ID is in the racer page's address. Run `python3 scripts/build_stats.py` (the rules are under [Badges given by hand](highlights.md#badges-given-by-hand)), run the tests, and commit the file with `site/data`. Pass no `--lock-seasons`. An entry that breaks a rule fails every build, the bot's too, so build before you push.

### Refreshing by hand

Pull first, because the bot may have pushed since your last sync. Run the refresh steps and commit `archive/`, `site/data` and `site/avatars`. Never pass `--lock-seasons` locally: locking is the bot's job. `racer_bios.py` and `schedule_archive.py` are optional by hand, as long as `archive/ladder-schedule/` exists.

- If a pull conflicts in `archive/`, discard your archive changes, pull, and rerun `race_archive.py`, `schedule_archive.py`, `build_stats.py` and `avatar_mirror.py`.
- If it conflicts in `season-ratings/`, take upstream: ledgers are deterministic.
