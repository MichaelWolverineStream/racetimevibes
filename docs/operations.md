# Operations

- **Purpose:** how to refresh the data, restamp the static assets, run the tests, and how the workflows test, refresh and deploy.
- **Read before:** running the pipeline by hand, or changing a workflow, `version_assets.py` or `publish_site.py`.
- **Code:** `.github/workflows/`, `scripts/version_assets.py`, `scripts/publish_site.py`, and the pipeline scripts in [README › Quick start](../README.md#quick-start).

## Refreshing the data

Run the steps in the quick-start order:

1. `race_archive.py` saves new races, racer names and avatar observations into `archive/` (see [archive.md](archive.md)).
2. `schedule_archive.py` mirrors the ladder schedule (see [Ladder schedule mirror](archive.md#ladder-schedule-mirror)). The build joins every slot to its race, so it refuses an archive without a mirror.
3. `racer_bios.py` saves the bios of racers who raced since their last check (see [Bios](archive.md#bios)). It reads the races just synced and the schedule's season spans, which leave out season-break races, and the build turns bios into [Pokémon avatars](avatars.md#pokémon-avatars).
4. `build_stats.py` builds the [published datasets](datasets.md) from the archive alone. `--lock-seasons` also writes the ledger of every ended, settled season into `season-ratings/` (see [Season locking](datasets.md#season-locking)); only the scheduled job passes it.
5. `avatar_mirror.py` runs last, because it reads the file names `build_stats.py` wrote to `participants.json`. It converts new racetime.gg avatars to WebP, which needs ImageMagick (see [Avatar mirror](avatars.md#avatar-mirror)).

Options:

- `--archive` points the syncs, the build, `avatar_mirror.py` and `ladder_rating.py` at another archive.
- `--output`, `--participants-output` and `--comments-output` redirect each dataset of `build_stats.py`.
- `avatar_mirror.py` takes `--participants` and `--output`, and `--pending`, which prints how many racetime.gg avatars a run would download and does nothing else.
- `--pokemon` on `build_stats.py`, `avatar_mirror.py` and `pokemon_avatars.py` points at another sprite directory, and `pokemon_avatars.py --refresh-pokedex` rewrites the pokédex.
- `ladder_rating.py --backtest` replays the archive and scores the rating's predictions (see [Backtest](../README.md#backtest)).

## Versioning static assets

Run `python3 scripts/version_assets.py` after editing `site/index.html` or any file in `VERSIONED_ASSETS`. It hashes them into one token, rewrites every reference to `asset?v=<token>` and writes `site/version.json`. A deploy therefore changes the asset URLs, so browsers never use a stale cached copy, and an open page reloads onto the new version (see [Automatic updates](dashboard.md#automatic-updates)).

- `--check` reports staleness without writing, and a test runs the same check.
- `--site` points it at another site directory.
- A new stamped site file goes into `VERSIONED_ASSETS`; a test fails when `index.html` references a local stamped file that is not listed.
- The Barlow fonts in `site/assets/fonts/` are not stamped: replace one by adding a file under a new name.

## Browser caching

GitHub Pages sends `Cache-Control: max-age=600` on every file and cannot be configured. Its ETag and Last-Modified also change on every deploy, even for files the deploy did not change. The page is served as `climbers.alttpr.racing` through Cloudflare, so browser lifetimes are set there. Chrome's [Cache insight](https://developer.chrome.com/docs/performance/insights/cache) asks for at least 30 days on scripts, stylesheets, fonts and images.

| Tier | Files | `Cache-Control` | Why it is safe |
|---|---|---|---|
| Immutable | `*.js` and `*.css` requested with `?v=`; `assets/fonts/*` | `public, max-age=31536000, immutable` | A stamped URL never changes content (see [Versioning static assets](#versioning-static-assets)). A font is replaced under a new name. |
| 30 days | `avatars/*`, `assets/badges/*`, `assets/alttpr-ladder.png` | `public, max-age=2592000` | A new racetime.gg upload gets a new name. Badge art redrawn in place and reprocessed Pokémon sprites can show the old image for up to 30 days. |
| Revalidate | `/`, `index.html`, `version.json`, `data/*` | `no-cache` | The browser checks every time and gets a 304 when nothing changed. |

- **`index.html` must stay `no-cache`.** A year-long stamped URL is safe only while the page that names it is always current.
- **The page fetches the datasets with `cache: "no-cache"`.** A reload in the same deploy costs a 304, not the files. `version.json` is fetched with `no-store`.
- **Edge caching is untouched.** The rules set only the header the browser gets, so a deploy never needs a Cloudflare purge.
- **An unstamped `app.js` (no `?v=`) is not matched** and keeps Cloudflare's default browser TTL.

### The Cloudflare rules

They live on the `alttpr.racing` zone, outside this repo. Only the zone owner can change them. They are three Response Header Transform Rules (Rules › Transform Rules › Modify Response Header), each setting the static header `Cache-Control`. Each one names the host, so the zone's other subdomains are untouched. The response-code clause keeps a 404, such as a removed avatar, from being cached.

1. **Immutable**, value `public, max-age=31536000, immutable`:
   ```
   (http.host eq "climbers.alttpr.racing" and http.response.code in {200 304} and
    ((http.request.uri.query wildcard "v=*" and (http.request.uri.path wildcard "*.js" or http.request.uri.path wildcard "*.css"))
     or http.request.uri.path wildcard "/assets/fonts/*"))
   ```
2. **30 days**, value `public, max-age=2592000`:
   ```
   (http.host eq "climbers.alttpr.racing" and http.response.code in {200 304} and
    (http.request.uri.path wildcard "/avatars/*" or http.request.uri.path wildcard "/assets/badges/*"
     or http.request.uri.path eq "/assets/alttpr-ladder.png"))
   ```
3. **Revalidate**, value `no-cache`:
   ```
   (http.host eq "climbers.alttpr.racing" and
    (http.request.uri.path in {"/" "/index.html" "/version.json"} or http.request.uri.path wildcard "/data/*"))
   ```

Check them from the repo root. Each line prints the header its tier sets, and a bare `/app.js` prints Cloudflare's default:

```bash
B=https://climbers.alttpr.racing; V=$(curl -s $B/version.json | sed 's/.*"version":"\([^"]*\)".*/\1/')
for p in "/app.js?v=$V" "/styles.css?v=$V" /assets/fonts/barlow-400.woff2 \
         /assets/alttpr-ladder.png "/assets/badges/$(ls site/assets/badges | head -1)" "/avatars/$(ls site/avatars | head -1)" \
         / /index.html /version.json /data/race-stats.json /app.js; do
  printf '%-48s %s\n' "$p" "$(curl -sI "$B$p" | tr -d '\r' | awk -F': ' 'tolower($1)=="cache-control"{print $2}')"
done
```

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
  - every file in `site/avatars/` is a name `participants.json` gives, or the `-256` copy of one;
  - a racetime.gg file is a square WebP of at most 128 px (256 px for the copy), read with the stdlib `webp_info`, so the check needs no ImageMagick;
  - a Pokémon file is a PNG, and every `pokemon-*` avatar is a byte copy of its sprite;
  - `assets/pokemon/` holds one square palette PNG of at least 256 pixels for every species from 1 to 1025.
- A published avatar whose file is missing does not fail, so a vanished upload cannot block a deploy. A missing `site/avatars/` folder does fail.
- `ConvertAvatarTests` runs the converter on the real ImageMagick and is skipped when neither `magick` nor `convert` is installed. `pages.yml` has none, so they skip there. The bot runs them before it converts.

`tests/analyses/` has no `__init__.py`, so the production discovery skips it. What it holds, including the rating monitors, is under Tiers in [testing.md](testing.md#tiers). A monitor failure is a prompt to look at the numbers with `ladder_rating.py --backtest`, never a reason to retune on 2026 races.

## Deployment

- `.github/workflows/pages.yml` runs the production suite and `node --check` on `site/*.js`, then deploys `site/` whenever `main` is pushed, or from **Actions › Deploy GitHub Pages**. Pages must be set to deploy from GitHub Actions.
- **The deployed copy has no CSS comments.** After the checks, `python scripts/publish_site.py "$RUNNER_TEMP/site"` copies `site/` to the runner's temp directory and removes every comment line from its stylesheets. The comments are a third of the CSS once it is gzipped. Everything else, whitespace included, ships as committed.
  - Each CSS comment must have its lines to itself. A comment that shares a line with code fails the publish, and a test in the production suite (`test_publish_site.py`) fails first, so a stylesheet that would break the deploy never reaches it.
  - The `?v=` stamps hash the source, so a change to a comment alone also gives new asset URLs. That does no harm.
  - To preview what ships: `python3 scripts/publish_site.py "$TMPDIR/site" && python3 -m http.server 8000 -d "$TMPDIR/site"`. The output directory must not exist yet.
- Compression is left to the hosts: GitHub Pages gzips CSS, JS and JSON, and Cloudflare passes that through. A Brotli or zstd setting at Cloudflare would not shrink them, because its on-the-fly levels compress this CSS no better than GitHub's gzip.
- After the tests pass, their job uploads the Pages artifact; a separate deploy job waits for it, avoiding an immediate artifact lookup that can race the upload.
- `.github/workflows/analysis-tests.yml` runs the analysis suite on pushes to `main` and on pull requests that change `scripts/`, `tests/analyses/`, `schedule-plan.json` or the workflow itself, weekly against the newest archive (the run that carries the rating monitors), and on demand. It never blocks a deploy.

### Scheduled data refresh

`.github/workflows/update-data.yml` runs `race_archive.py`, `schedule_archive.py`, `build_stats.py --lock-seasons` and `avatar_mirror.py`, then the production suite's `Committed*` tests (`-k Committed`), the deploy's checks on the archive and the published files. An outside trigger starts it through `workflow_dispatch` about every two hours, a scheduled run at 00:17 UTC is the daily backup, and **Actions › Update race data** starts it by hand. One input, `sync-bios`, off by default and not offered on a manual run, also runs `racer_bios.py` right after the schedule sync.

- It commits to `main` as `YYYY-MM-DD data` when anything under `archive/` or `season-ratings/` changed (a race, schedule, name, avatar or bio file, or a newly locked ledger), or when the site changed.
- The site changed when the datasets changed beyond `generated_at`, or `site/avatars/` changed, even with no archive change, such as an avatar download that failed last run. The commit then includes `site/data` and `site/avatars`, and the workflow starts **Deploy GitHub Pages** explicitly, because pushes made with `GITHUB_TOKEN` trigger no workflows.
- The step "Install ImageMagick for new avatars" runs after the build and before the mirror, with `continue-on-error`. It runs `avatar_mirror.py --pending`, and only when the count is not 0 it installs ImageMagick with apt, checks that `convert -list format` lists WEBP as `rw+` (read, write, multi-frame), and runs the `ConvertAvatar` tests on ImageMagick 6. Most runs have nothing pending and skip the install.
  - A failure there never holds back race data: the mirror skips the downloads with a warning and the next run retries them.
  - A download that fails for good keeps the count above 0, so every run installs ImageMagick again until the avatar changes or the download works.
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
- If it conflicts in `site/avatars/`, take upstream: the bot converts with ImageMagick 6 and a local run with 7, so the same name can hold different bytes.
