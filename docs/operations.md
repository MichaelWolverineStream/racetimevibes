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
python3 -m unittest discover -s tests             # production suite, a few seconds; gates every deploy
python3 -m unittest discover -s tests/analyses    # analysis suite, about half a minute
```

No test uses the network. The production suite rebuilds all three datasets from the committed `archive/` and `season-ratings/` and fails on any difference from `site/data` other than `generated_at`, so commit a rebuild with every build change. It also checks the committed assets: every file in `site/avatars/` is named by `participants.json` and is a valid raster image (a missing one does not fail, so a vanished upload cannot block a deploy), every `pokemon-*` avatar is a byte copy of its sprite, and `assets/pokemon/` holds one square palette PNG of at least 256 pixels for every species from 1 to 1025.

`tests/analyses/` covers the standalone analyses in [analyses.md](analyses.md). It has no `__init__.py`, so the production discovery skips it.

## Deployment

- `.github/workflows/pages.yml` runs the production suite and deploys `site/` whenever `main` is pushed, or from **Actions › Deploy GitHub Pages**. Pages must be set to deploy from GitHub Actions.
- `.github/workflows/analysis-tests.yml` runs the analysis suite on pushes to `main` and on pull requests that change `scripts/`, `tests/analyses/`, `schedule-plan.json` or the workflow itself, weekly against the newest archive, and on demand. It never blocks a deploy.

### Scheduled data refresh

`.github/workflows/update-data.yml` runs `race_archive.py`, `build_stats.py --lock-seasons` and `avatar_mirror.py` every two hours, at 17 minutes past each even hour UTC, or from **Actions › Update race data**. Two inputs, both off by default and not offered on a manual run, add steps before the build: `sync-bios` runs `racer_bios.py`, and `sync-schedule` runs `schedule_archive.py`.

- When the sync added or refreshed a race, or the build locked a season, it commits `archive/` and `season-ratings/` to `main` as `YYYY-MM-DD data`.
- When the datasets changed beyond `generated_at`, or `site/avatars/` changed, the same commit includes `site/data` and `site/avatars`, and the workflow starts **Deploy GitHub Pages** explicitly, because pushes made with `GITHUB_TOKEN` trigger no workflows.
- A run that finds nothing new, or whose sync, build or mirror fails, commits nothing.

`.github/workflows/update-bios.yml` runs daily at 05:47 UTC, between data runs, and only calls `update-data.yml` with both inputs on. A manual **Update racer bios** run is how to sync bios and the schedule on demand.

- It sets no `concurrency` group on purpose: the callee's `update-data` group already queues it behind a running data run, and the same group on the caller would deadlock.
- GitHub keeps one waiting run per group, so a data run can cancel a waiting bio run. Nothing is lost: a racer stays due until checked.

### Naming a season

From Season 11 the previous season's champion picks an animal. Add `"N": {"animal": "…"}` to `season-names.json`, run `python3 scripts/build_stats.py` (the rules are under [Season fields](datasets.md#season-fields)), run the tests, and commit the file with `site/data`. Pass no `--lock-seasons`: the ledgers stay untouched.

### Refreshing by hand

Pull first, because the bot may have pushed since your last sync. Run the refresh steps and commit `archive/`, `site/data` and `site/avatars`. Never pass `--lock-seasons` locally: locking is the bot's job. `racer_bios.py` and `schedule_archive.py` are optional by hand, as long as `archive/ladder-schedule/` exists.

- If a pull conflicts in `archive/`, discard your archive changes, pull, and rerun `race_archive.py`, `schedule_archive.py`, `build_stats.py` and `avatar_mirror.py`.
- If it conflicts in `season-ratings/`, take upstream: ledgers are deterministic.
