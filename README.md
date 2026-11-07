# ALTTPR Step Ladder Race Signals

A static infographic dashboard built from the public racetime.gg archive, from 2025-04-20, for the `alttpr-ladder` category. Beyond the schedule analytics it tracks racers: a ladder rating, overall, per mode and per season, personal bests, streaks, badges, records, and a feed of the latest results.

The dashboard is published at <https://michaelwolverinestream.github.io/racetimevibes/>.

This README is the spec. It has three parts:

1. [Running the project](#running-the-project): setup, the data refresh, tests and deployment.
2. [The data](#the-data): the archive, the published datasets, and every rule the build computes, from the ladder rating to tags and badges.
3. [The dashboard](#the-dashboard): what the page does with that data in the browser.

Other documents:

- [Badge_Guide.md](Badge_Guide.md): how to add, remove or change a badge.
- [Analyses.md](Analyses.md): the standalone analyses, mode overlap, race schedule proposals, the schedule strategy comparison and the rating comparison.
- [Racetime_API.md](Racetime_API.md): the racetime.gg endpoints and fields the scripts read.

## Running the project

### Requirements

- Python 3.11 or newer, standard library only. The site is plain files with no build step.
- A network connection for the syncs and the avatar mirror.
- ImageMagick 7, only for `scripts/badge_images.py` (badge art) and `scripts/pokemon_images.py` (sprite framing).

### Quick start

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

### Refreshing the data

Run the steps in the quick-start order:

1. `race_archive.py` saves new races, racer names and avatar observations into `archive/` (see [The race archive](#the-race-archive)).
2. `racer_bios.py` saves the bios of racers who raced since their last check (see [Bios](#bios)). It reads the races just synced, and the build turns bios into [Pokémon avatars](#pokémon-avatars).
3. `schedule_archive.py` mirrors the ladder schedule (see [Ladder schedule mirror](#ladder-schedule-mirror)). The build joins every slot to its race, so it refuses an archive without a mirror.
4. `build_stats.py` builds the [published datasets](#published-datasets) from the archive alone. `--lock-seasons` also writes the ledger of every ended, settled season into `season-ratings/` (see [Season ratings](#season-ratings)); only the scheduled job passes it.
5. `avatar_mirror.py` runs last, because it reads the file names `build_stats.py` wrote to `participants.json` (see [Avatar mirror](#avatar-mirror)).

Options: `--archive` points the syncs and the build at another archive. `--output`, `--participants-output` and `--comments-output` redirect each dataset. `avatar_mirror.py` takes `--participants`, `--archive` and `--output`. `--pokemon` on `build_stats.py`, `avatar_mirror.py` and `pokemon_avatars.py` points at another sprite directory, and `pokemon_avatars.py --refresh-pokedex` rewrites the pokédex.

### Versioning static assets

Run `python3 scripts/version_assets.py` after editing `site/index.html` or any file in `VERSIONED_ASSETS`. It hashes them into one token, rewrites every reference to `asset?v=<token>` and writes `site/version.json`. A deploy therefore changes the asset URLs, so browsers never use a stale cached copy, and an open page reloads onto the new version (see [Automatic updates](#automatic-updates)). `--check` reports staleness without writing, and a test runs the same check. The Barlow fonts in `site/assets/fonts/` are not stamped: replace one by adding a file under a new name.

### Tests

```bash
python3 -m unittest discover -s tests             # production suite, a few seconds; gates every deploy
python3 -m unittest discover -s tests/analyses    # analysis suite, about half a minute
```

No test uses the network. The production suite rebuilds all three datasets from the committed `archive/` and `season-ratings/` and fails on any difference from `site/data` other than `generated_at`, so commit a rebuild with every build change. It also checks the committed assets: every file in `site/avatars/` is named by `participants.json` and is a valid raster image (a missing one does not fail, so a vanished upload cannot block a deploy), every `pokemon-*` avatar is a byte copy of its sprite, and `assets/pokemon/` holds one square palette PNG of at least 256 pixels for every species from 1 to 1025.

`tests/analyses/` covers the standalone analyses in [Analyses.md](Analyses.md). It has no `__init__.py`, so the production discovery skips it.

### Deployment

`.github/workflows/pages.yml` runs the production suite and deploys `site/` whenever `main` is pushed, or from **Actions › Deploy GitHub Pages**. Pages must be set to deploy from GitHub Actions. `.github/workflows/analysis-tests.yml` runs the analysis suite on changes to `scripts/`, `tests/analyses/` or `schedule-plan.json`, weekly against the newest archive, and on demand; it never blocks a deploy.

#### Scheduled data refresh

`.github/workflows/update-data.yml` runs `race_archive.py`, `build_stats.py --lock-seasons` and `avatar_mirror.py` every two hours, at 17 minutes past each even hour UTC, or from **Actions › Update race data**. Two inputs, both off by default and not offered on a manual run, add steps before the build: `sync-bios` runs `racer_bios.py`, and `sync-schedule` runs `schedule_archive.py`.

- When the sync added or refreshed a race, or the build locked a season, it commits `archive/` and `season-ratings/` to `main` as `YYYY-MM-DD data`.
- When the datasets changed beyond `generated_at`, or `site/avatars/` changed, the same commit includes `site/data` and `site/avatars`, and the workflow starts **Deploy GitHub Pages** explicitly, because pushes made with `GITHUB_TOKEN` trigger no workflows.
- A run that finds nothing new, or whose sync, build or mirror fails, commits nothing.

`.github/workflows/update-bios.yml` runs daily at 05:47 UTC, between data runs, and only calls `update-data.yml` with both inputs on. A manual **Update racer bios** run is how to sync bios and the schedule on demand.

- It sets no `concurrency` group on purpose: the callee's `update-data` group already queues it behind a running data run, and the same group on the caller would deadlock.
- GitHub keeps one waiting run per group, so a data run can cancel a waiting bio run. Nothing is lost: a racer stays due until checked.

#### Refreshing by hand

Pull first, because the bot may have pushed since your last sync. Run the refresh steps and commit `archive/`, `site/data` and `site/avatars`. Never pass `--lock-seasons` locally: locking is the bot's job. `racer_bios.py` and `schedule_archive.py` are optional by hand, as long as `archive/ladder-schedule/` exists. If a pull conflicts in `archive/`, discard your archive changes, pull, and rerun `race_archive.py`, `schedule_archive.py`, `build_stats.py` and `avatar_mirror.py`. If it conflicts in `season-ratings/`, take upstream: ledgers are deterministic.

### Project layout

```text
scripts/race_archive.py           Incremental sync of races, racer names and avatar observations; the atomic file writers
scripts/racer_bios.py             Sync of racers' profile bios, with their history, and its loader
scripts/schedule_archive.py       alttpr.racing schedule mirror, its loaders, the slot-to-race join and the season start rule
scripts/build_stats.py            Archive validation and aggregation, including the schedule panels and bands
scripts/participant_stats.py      Per-racer dataset: records, avatars and assembly
scripts/ladder_rating.py          Ladder rating, overall, per mode and per season, and its backtest
scripts/season_ratings.py         Season membership, the lock conditions and the season ledgers
scripts/highlights.py             Tags, badges, streaks, presets, records, claims and facts
scripts/comment_stats.py          Racer comments dataset and the chest-count parser
scripts/avatar_mirror.py          Avatar file names and the mirror in site/avatars/, including Pokémon copies
scripts/pokemon_avatars.py        Favourite and random Pokémon, sprite paths and the pokédex refresh
scripts/badge_images.py           Badge art to the standard WebP pair (ImageMagick 7)
scripts/pokemon_images.py         Pokémon sprites fitted to the avatar frame (ImageMagick 7)
scripts/version_assets.py         Content versioning for the static assets
scripts/mode_overlap.py, schedule_planner.py, schedule_lab.py, rating_lab.py
                                  Standalone analyses, never run by the build (see Analyses.md)
tests/test_<module>.py            Production suite, one file per module, plus site globals and badge assets
tests/analyses/                   Analysis suite and its schedule fixtures
.github/workflows/                pages.yml (test and deploy), analysis-tests.yml, update-data.yml, update-bios.yml
archive/                          Verbatim racetime.gg races, racer names, avatars and bios, plus ladder-schedule/; never deployed
season-ratings/                   One ledger per locked season, written once by the bot; never deployed
assets/pokemon/                   Pokémon sprites and pokedex.json; never deployed
schedule-plan.json                Season, bands, composition and events for the schedule planner
site/data/                        Generated race-stats.json, participants.json and comments.json (loaded lazily)
site/avatars/                     Mirrored avatars, exactly those participants.json names
site/version.json                 Generated content version manifest
site/index.html, styles.css       Page and responsive visual system
site/app.js                       Base layer: SVG charts, tooltips and formatters
site/leaderboard.js               Data loading, routing, the leaderboard and head-to-head views
site/racer-charts.js              Finish-time violins, presets table and step charts
site/store.js                     Local storage and followed racers
site/ui.js, ui.css                Shared avatars, speech bubbles, tags, badges and dialogs
site/top-rated, feed, records, racer-extras, share-card, easter-eggs (.js, .css)
                                  Top-rated banner, latest results, hall of records, racer page extras, player card, hidden extras
site/version-check.js, visitor-counter.js
                                  Reload on a new version; masthead visitor counter
site/assets/                      Category artwork, Barlow fonts (SIL OFL) and badge images
README.md, Analyses.md, Badge_Guide.md, Racetime_API.md, CLAUDE.md
                                  This spec, the analyses, the badge checklist, the API notes and the agent rules
```

## The data

Everything in this part is computed by the Python scripts in `scripts/` and published in `site/data/`. The browser only reads the result (see [The dashboard](#the-dashboard)).

### The race archive

#### Files

`archive/races/alttpr-ladder/<slug>.json` holds one file per race, named after the race. Each file stores the race exactly as the API's past-races listing returned it, with entrant details, inside an envelope that records when it was fetched:

```json
{"schema_version": 1, "fetched_at": "2026-10-27T10:59:46Z", "race": {"name": "alttpr-ladder/dazzling-matt-4030", "...": "..."}}
```

- The archive covers every race in the category from 2025-04-20; the builder drops only the `ladder_test` mode.
- `archive/racer-names.json` maps each racer's stable user ID to their current display name.
- `archive/racer-avatars.json` maps it to the avatar URL (or `null`) the category leaderboards last showed for them, with the time the sync first saw that value.
- `archive/racer-bios.json` maps it to their bio and its history, written by `racer_bios.py`, not by the race sync (see [Bios](#bios)).
- Every archive file is minified JSON: no whitespace between tokens, and one trailing newline.

What the archive contains, and why it is never deployed, is under [Privacy](#privacy).

#### How a sync works

The listing is sorted by completion time, newest first, 100 races per page. A sync reads it backwards from page one and stops once it passes a horizon. The horizon is one day before the newest archived race, or the oldest archived race still waiting for a moderator to record it, whichever is earlier. A normal sync is one request for page one plus one for the category leaderboards. With an empty archive the sync reads every page, so the first run is a full backfill of about 33 requests. Transient network failures are retried with bounded backoff, and the sync checks completion-time ordering and race names before writing anything.

Finished races still change until a moderator records them, usually within two days: `recorded` flips, `score_change` is filled in, and racers add comments. So a race's file is rewritten while the race is unrecorded and its data changes. Once the race is recorded, is marked unrecordable, or finished more than 14 days ago, its file is never touched again. Files are written oldest first, so an interrupted sync leaves no gap behind the newest archived race.

If the archive ever holds fewer races than the API reports, the sync reads every page once to fill the gap, and fails if races are still missing. If an unrecorded race disappears from the listing, its last archived copy is kept and the sync prints a warning.

#### Names and avatars

User IDs are stable, but display names can change, and each race file keeps the name a racer had when it was fetched. Each sync merges the names from the category leaderboards into `racer-names.json`; the leaderboards cover nearly every racer with a recorded race. The dashboard shows a racer under that name, or, if they have no entry, under the name on their most recent race.

Avatars work the same way but never cost a request. The leaderboards response already carries each ranked racer's current avatar, so each sync merges it into `racer-avatars.json` as `[avatar URL or null, first-seen time]`, where the time is when the sync received the leaderboards.

- A user the leaderboards list without an `avatar` field is left as they were, not cleared.
- A racer's entry keeps its time while the value stays the same, and entries are never removed.
- The file is rewritten only when a value is added or changes, so an ordinary sync leaves it untouched.
- A racer who uploads a new picture gets a new URL, so the change is picked up at the next sync even if they have not raced. A change and a revert to the same file name within a single sync interval goes unnoticed.

#### Bios

A racer's bio is on their public profile page, `https://racetime.gg/user/<id>`, and in no JSON API, so `racer_bios.py` reads the page's HTML. It stores the text of the first `<p class="bio">`, links included, stripped and cut to 2,000 characters, the length the field allows. A profile with no bio, or an empty one, is stored as `null`. Entries are `[bio, checked-at time, since, earlier]`, with times in UTC, sorted by user ID.

- **Bio:** the text or `null`. **Checked-at time:** when the profile was last fetched.
- **Since:** when the current bio was first seen. **Earlier:** the racer's earlier bios, oldest first, each `[bio, since]`. A fetch that finds a different bio moves the old one, with its time, to the end of `earlier`; one that finds the same bio changes only the checked-at time. A racer's first fetch is not a change.
- The history began on 2026-11-05, so an entry stored before then has an unknown `since` and no earlier bios. Only the first earlier bio, or `since` when there are none, can be unknown (`null`). Any other entry shape is refused.
- Earlier bios are kept so a change of [favourite Pokémon](#pokémon-avatars) can be dated (see [Latest results](#latest-results)). Like the rest of `archive/`, they are never deployed.

- **Who is due:** a racer of an included race (see [Definitions](#definitions)) with no stored entry, or who entered an included race that finished after the time of their entry. A racer who has not raced since their bio was checked costs nothing, so a normal daily run fetches only the racers who raced that day.
- **Pacing:** one request per racer, 1 second apart, with the same bounded retries as the race sync. A page is read up to 1,000,000 bytes, and a longer one is a failure.
- **A 404** means a hidden or deleted profile. It is stored as `null`, so the racer is not asked for again until they race again.
- **A failure** is any other HTTP error, network error, oversized page, or user ID that is not letters and digits (never put into a URL). It prints a `Warning:` line and keeps the racer's old entry, so they are due again on the next run.
- **Early stop:** after 5 failures in a row the run stops with a warning, because racetime.gg is refusing profile pages. The racers not yet reached stay due. A success resets the count. The script still exits 0, so the rest of the refresh goes on with the bios it has.
- **Challenge pages:** a page without a `user-profile` block, such as a challenge or error page, is a failure. It never becomes a stored `null` bio.
- **The file** is rewritten only when at least one racer was checked, so a run that finds nobody due changes nothing.
- The script ends with `Bios: N checked, K failed; M name a favourite Pokémon.`, where `M` counts the stored bios that name one, `random` included (see [Pokémon avatars](#pokémon-avatars)). It exits with 1 only if the archive cannot be read. If the pokédex cannot be read, it prints a `Warning:` line and leaves the count out, because the bios are already saved.

#### Ladder schedule mirror

`scripts/schedule_archive.py` mirrors the alttpr.racing ladder schedule, so the build and the [schedule planner](Analyses.md#race-schedule-proposals) can see every slot, including the rooms that were cancelled and so never reached racetime.gg. The daily bot run syncs it (see [Scheduled data refresh](#scheduled-data-refresh)), and `build_stats.py` reads it and fails without it.

- **What it fetches:**
  - `https://alttpr.racing/api/v1/modes`, the modes with their archetype, name and `active` flag;
  - `https://alttpr.racing/api/v1/schedule/<n>` for seasons 1, 2, …, stopping at the first season that answers 404. Season 0 is never fetched: it is an alias of the current season, with local-time strings.
- **Where it writes:** `archive/ladder-schedule/modes.json` and `archive/ladder-schedule/season-<n>.json`. Each file wraps the response verbatim as `{"schema_version": 1, "fetched_at": …, "modes" | "schedule": [...]}`, minified with one trailing newline.
- **Validation:** every response is validated before any file is written. A slot needs integer `id`, `season` and `mode`, a `raceId` that is an integer or null, a `time` of digits (Unix seconds), and its file's season. A mode needs integer `id` and `archetype`, a string `name`, and a boolean `active`. Any failure writes nothing.
- **Rewrites:** a file is rewritten only when its body changed, so a run without news changes nothing. Files are never deleted.
- **Reading it back:** `load_schedule` returns every slot once by its `id`; the same id with different content is an error.
- **Seasons:** `season_starts`, `season_of` and `stray_slots` also live in this module, for the season rule under **Seasons** in [Definitions](#definitions).
- **The join:** `join_races` also lives in this module. It matches slots to included races by the rules under **Schedule** in [Definitions](#definitions), for the build and the planner alike.
- **Race ids arrive late.** alttpr.racing fills in a slot's `raceId` only when the room is created. A mirror is therefore only as current as its last sync, and the build counts slots only up to the latest one with a `raceId` (see **Counted through** in [Definitions](#definitions)).
- **Output:** the script ends with `Schedule: N seasons, M slots, K files changed.` and exits with 1 on any failure.
- **Privacy:** the schedule holds slot times, mode ids and alttpr.racing race ids, nothing about racers. Like the rest of `archive/`, it is never deployed.

### Published datasets

`build_stats.py` builds three datasets from the archive and writes each one atomically, minified:

| File | Contents |
|---|---|
| `site/data/race-stats.json` | The anonymous aggregate behind the Ladder Data panels (see [Schedule and participation metrics](#schedule-and-participation-metrics)). |
| `site/data/participants.json` | The per-racer dataset. Each race carries its Eastern Time date and start hour, its seed preset, and its derived tags. Each racer carries an overall rating and one for each mode they have raced, badges, streaks and a claim to fame. `avatar_changes` lists the racers whose avatar changed, newest first, each with the Eastern Time date the change was first seen. Each race also carries its season, `s`, when it has one, and each entry the racer's points in the season and in the season's mode after the race. `seasons` lists every season that holds a race (see [Season ratings](#season-ratings)). |
| `site/data/comments.json` | The racers' post-race comments, each with the chest count parsed from it (see [Chest counts](#chest-counts-cr-and-cph)). The page fetches it after its first render, so it never delays the board. |

`race-stats.json` and `participants.json` carry a schema version, and the page refuses data whose version it does not expect.

The season fields of `participants.json` (schema 13):

- **`seasons`:** an array, oldest first, of `{n, first, last, ended}` for every season that holds at least one race. `first` and `last` are the Eastern Time dates of the season's first and latest race. `ended` is true once the season is locked or a later season has started. The current season is the last element. The array is empty when there is no schedule, and the page then shows everything all-time.
- **`races[].s`:** the race's season number. It is absent for a race with no season.
- **Entries:** `races[].e[k]` is `[player, place, status, seconds, overall_after, mode_after, season_after, season_mode_after]`. `[6]` and `[7]` are integers when the race has `s`, and both `null` otherwise.
- **No per-player season fields.** A racer's standing in a season is derived from their entries: their last `[6]` among the season's races for the overall rating, and their last `[7]` among the season's races in a mode for that mode's rating. Their season finishes are their finisher entries in those races, and they are provisional below 5.
- **`diagnostics.seasons`:** `stray_slots`, `races_without_season`, `left_out_of_locked_seasons` and `mismatched_locked_races`.
- `rating`, each player's rating, $\mu$, $\sigma$, finishes and mode ratings, and `leaderboards` are unchanged and all-time.
 `site/avatars/` holds the mirrored avatar images (see [Avatar mirror](#avatar-mirror)). What each of these may contain is fixed by [Privacy](#privacy).

### Avatar mirror

`avatar_mirror.py` makes `site/avatars/` hold exactly the files `participants.json` names, so the page serves every avatar from its own origin.

- For each racer it works out the URL behind the published file name from `archive/`.
- It downloads only the files the directory is missing, pausing 0.2 seconds between requests. A normal run makes no requests; the first run makes one per racer with an avatar.
- It checks each download before keeping it: the first bytes must match a PNG, JPEG, GIF or WebP image that agrees with the file extension, and the file must be at most 2,000,000 bytes.
- It deletes every file or symlink in `site/avatars/` that `participants.json` does not name, so a racer's old picture goes when a new one arrives.
- It copies a Pokémon avatar from `assets/pokemon/` instead, with no request (see [Pokémon avatars](#pokémon-avatars)). The sprite `pokemon-201-b.png` comes from `201-b.png`. A file is copied only if the directory is missing it or holds a copy that differs from the sprite, and only if the sprite is a PNG image. A local read is cheap, so every run compares the copies, and a replaced sprite reaches `site/avatars/` on the next run.
- A download or check that fails prints a `Warning:` line and the run continues; the file is tried again on the next run, and meanwhile the page shows the racer's monogram. A sprite that cannot be read or is not a PNG fails the same way.
- The script ends with `Avatars: N downloaded, C copied, M removed, K failed.` It exits with 1 only if the archive cannot be read, or if `participants.json` is unreadable or names a file the archive does not explain, in which case it asks you to run `build_stats.py` first. A `pokemon-` name with no sprite in `assets/pokemon/` counts as such a file, so it is treated like a stale `participants.json`.

### Pokémon avatars

A racer's avatar can be a Pokémon. The Pokémon is the racer's favourite, if their bio names one, or else a random one. A racer whose bio names `random` as their favourite gets a random one too. `scripts/pokemon_avatars.py` makes both choices, and `build_stats.py` publishes the result as the racer's avatar file name in `participants.json`.

**Precedence.** For each racer, in this order:

1. A favourite Pokémon named in their bio, even when they have a racetime.gg avatar. If the favourite is `random`, a random Pokémon, again even over a racetime.gg avatar.
2. Their racetime.gg avatar, when they have one that can be mirrored (see [Privacy](#privacy)).
3. A random Pokémon.

Nobody is left without an avatar when `assets/pokemon/` has sprites.

**The phrase.** The bio is read from `archive/racer-bios.json` (see [Bios](#bios)). The text is lowercased, accents are removed, and `♀` and `♂` become `f` and `m`. Each line is cut into words of letters and digits, so punctuation, such as the colon in "favourite pokemon: Mew", is ignored.

- A phrase is two words next to each other on one line. The first is favourite-like and the second Pokémon-like.
- A word is favourite-like if it is `fav` or `fave`, or at least 0.8 similar to `favourite` or `favorite`. It is Pokémon-like if it is `pkmn`, or at least 0.8 similar to `pokemon`. Similarity is the ratio of Python's `difflib.SequenceMatcher`, set as `PHRASE_WORD_CUTOFF`.
- "My" is not required, and neither is anything before the phrase.
- The words after the phrase are the name. One linking word, `is`, `s` (as in "pokemon's"), `are` or `was`, is skipped first.
- If the first word of the name is random-like, at least 0.8 similar to `random` (`RANDOM_WORDS`), the favourite is `random` and the rest of the name is ignored. So "favourite pokemon: Random!" and "fav pokemon is rando" both ask for a random Pokémon, but "my favourite pokemon changes at random" and "favourite pokemon randomizer" do not. No word of a Pokémon name is random-like, so this is checked before the name and never hides a Pokémon.
- The name is on the same line as the phrase. The name never continues onto the next line.
- Hits are tried in text order, line by line. The first hit whose name resolves, or is `random`, wins. A hit whose name resolves to nothing is skipped, and a later hit can still win.

**The name.** At most 4 words (`NAME_MAX_WORDS`) after the phrase are used, so "pikachu is the best" still resolves. The name is tried as the first 4 words, then the first 3, 2 and 1, longest first. Names come from the pokédex's two lists: Pokémon, including alternate forms such as `charizard-mega-x` (ids from 10001), and species, such as `deoxys`. A name only counts if its id has a sprite.

1. **Exact:** a candidate matches if its words joined without spaces equal a name with its hyphens removed, or if its words, in any order, equal a name's hyphen-separated parts. This is tried at every length before any fuzzy match, so an exact short name beats a fuzzy longer one. If both keys match, the lower id wins.
2. **Fuzzy:** otherwise, the candidate is compared, words sorted, with every name, again with its parts sorted. A name needs a similarity of at least 0.85 (`NAME_CUTOFF`), which keeps ordinary words such as "starter", "unknown" or "zard" from fuzzing to Stantler, Unown or Zarude. The best similarity wins and a tie goes to the lower id. The longest candidate with any name at or above the cutoff wins.

So `Mr. Mime` and `mrmime` give Mr. Mime, `Mega Charizard X` gives `charizard-mega-x`, `Pikachuu` gives Pikachu, and `Deoxys` gives Deoxys.

**Assignment.** `assign` takes the racers in a fixed order: by the position of their first included race, then by user ID. It makes two passes.

1. **Favourites:** each racer whose bio names a Pokémon gets it.
2. **Random:** each racer whose favourite is `random`, and each racer with neither a favourite nor a racetime.gg avatar, gets a random base Pokémon, in their place in the order. A favourite of `random` therefore gives a racer what they would get with no racetime.gg avatar at all. The Pokémon is picked from the 1,025 species, that is, ids 1 to 1025 and never an alternate form. It is one of those the fewest racers already use. Racers who take a favourite count as users of its exact id, and so do those earlier in this pass. A favourite alternate form, such as `charizard-mega-x`, therefore does not take its base species out of the random pass. Ties are broken by a ranking from the SHA-256 of `<user id>/<pokédex number>`, lowest first, so the pick is fixed per racer.

A Pokémon can have several sprites, such as the Unown letters. The racer gets the sprite file, favourite or random, that the fewest racers use, with the same tie-break from the SHA-256 of `<user id>/<file name>`. A published name is `pokemon-` plus the file name, for example `pokemon-201-b.png`. There is no limit on how many racers share a Pokémon: once every one is in use, the least used are picked. If `assets/pokemon/` has no species, nobody gets a random Pokémon.

**Stability.** The order depends only on racers' first races, so a new racer joins at the end and their random Pokémon changes nobody else's, unless a race reaches the archive after a later-starting one. A random pick changes only when something before the racer's turn changes: a favourite is added, changed or removed, or an earlier racer enters or leaves the random pass by gaining or losing an upload or a favourite, or, while they have an upload, by naming or dropping `random`. A favourite Pokémon changes only with the bio, but its sprite can change when an earlier racer starts or stops favouring the same Pokémon.

**The pokédex.** `assets/pokemon/` is committed and never deployed; `site/avatars/` gets copies of the sprites in use. It holds the sprites, named `<pokédex number>.png` or `<number>-<variant>.png`, and `pokedex.json`, which maps ids to names for `pokemon` and `species`. `python3 scripts/pokemon_avatars.py --refresh-pokedex` rewrites `pokedex.json` from PokeAPI's `/pokemon/` and `/pokemon-species/` lists, following their `next` links, and writes nothing if a list is incomplete, changes while it is read, or links outside PokeAPI. CI never runs it. A new generation also needs its sprites added.

**The sprites.** `python3 scripts/pokemon_images.py` generates the committed sprites from 96 × 96 originals in `_tmp_assets/pokemon/`, which are not committed. It skips the `0.png` placeholder and never touches `pokedex.json`; run `avatar_mirror.py` afterwards. Each sprite's transparency is cut at 50 %, as the palette PNG stores it, so a pixel less than half opaque is margin. The sprite is trimmed of its transparent margin and centred in a square whose side is its longest side, so that side runs edge to edge with no margin. It is scaled up with nearest-neighbour sampling by the smallest whole factor that reaches 256 pixels (`MIN_SIDE`, four times the 64-pixel racer-page avatar, so every avatar scales it down), and written as a palette PNG. A sprite whose written content is not exactly its trimmed content scaled up fails and is not replaced. Rerunning it on its own output changes nothing.

### Privacy

- **`race-stats.json`** is anonymous. It holds slot and race counts, distributions and bands, never entrant arrays, IDs, names, profile links, or Twitch fields. The schedule's slot and race ids are used only inside the build.
- **`participants.json`** publishes each racer's public display name and stable user ID, because a leaderboard requires them, plus the file name of their mirrored avatar.
  - That name is the first 16 hex digits of the SHA-256 of the avatar's racetime.gg URL, plus the URL's lowercase extension, for example `3f9ac01d2e7b8a64.jpg`. It carries nothing from the upload's own file name.
  - A racer has one only if the URL is directly under `https://racetime.gg/media/`, names the file in URL-safe ASCII (letters, digits, `.`, `_`, `~`, `-` and `%`, as in percent escapes), and ends in `png`, `jpg`, `jpeg`, `gif` or `webp`.
  - The avatar follows the racer's most recently observed profile: the category leaderboards each sync, or their latest race file among the races the dashboard includes, so never a `ladder_test` race, whichever is newer, and the leaderboards win a tie. A racer whose latest observation has no avatar has none, unless a Pokémon fills in (see [Pokémon avatars](#pokémon-avatars)).
  - A Pokémon avatar's name is `pokemon-` plus a sprite file name, `pokemon-<pokédex number>[-<variant>].png`, for example `pokemon-201-b.png`. It reveals only which Pokémon the racer has, which comes from their public bio or from a random pick. Bio text is never published.
  - `avatar_changes` adds, for racers whose avatar changed while the archive was watching, the Eastern Time date the archive first saw the change (see [Latest results](#latest-results)). It reveals nothing about the old avatar, which is neither named nor mirrored, and nothing from the bio beyond the date the favourite Pokémon changed.
- **`comments.json`** publishes the raw post-race comments of included races, stripped and capped at 500 characters, keyed by race name and user ID, each with the chest count parsed from it, or `null`.
- **`site/avatars/`** holds copies of exactly the files `participants.json` names, raster images only. The page serves avatars from there, so a viewer's browser never contacts racetime.gg for them.
  - The copies are byte for byte, so any metadata an upload carries, such as a JPEG's EXIF block, is served as racetime.gg serves it.
  - A replaced or removed avatar leaves `site/avatars/` but stays in the repository's git history.
- **Season fields** in `participants.json` are integers, dates and flags tied to the racers' published IDs. They add no field the contract above excludes.
- **Never published:** full names, discriminators, pronouns, flair, Twitch fields and racetime.gg points and score fields.

`archive/` is different. It mirrors racetime.gg's unauthenticated public API verbatim, including entrants' full user profiles and post-race comments. It also holds racers' public profile bios, verbatim from their racetime.gg profile pages, with the earlier versions it has seen since 2026-11-05. It is committed to this public repository, so anyone can read it there, but it sits outside `site/` and is never deployed with the dashboard.

`season-ratings/` is also outside `site/`. It holds racers' user IDs with their season points, is committed beside `archive/`, and is never deployed.

### Definitions

These terms mean the same in every part of this README.

**Races**

- **Included race:** only records whose `status.value` is `finished` are included. The datasets cover every archived finished race, beginning 2025-04-20.
- **Excluded mode:** races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric.
- **Mode:** the text in the brackets of the `Step Ladder Series - [mode]` prefix of `info`, trimmed and lowercased. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.
- **Preset:** the name after the mode in the race's `info` first line, trimmed, cut at any seed URL (along with a room slug such as `owner/slug` just before it), with `[x]` turned into `(x)` and any `Grab Bag (...)` collapsed to `Grab Bag`. Each preset belongs to its mode.
- **Field:** the race's entrant count, which includes DNFs and anonymous entrants.

**Time**

- All dates, hours and schedule buckets use `America/New_York` (Eastern Time), including the seasonal switch between EST and EDT. A race's **date**, or **day**, is its Eastern Time local start date.
- A **week** runs Monday to Sunday.

**Entrants**

- **Identified:** an entry carrying a stable public user ID. Anonymized entries are not identified.
- **Finisher:** an entrant whose status is `done` and who has a `place`. A **timed** finisher also has a `finish_time`. Everyone else, DNF or DQ, is a non-finisher.
- **Winner:** the identified entrant with place 1.
- **Provisional:** a racer with fewer than 5 finishes, in any mode, before a given race. A mode rating has its own provisional rule (see [Mode ratings](#mode-ratings)).

**Schedule**

These terms use the [ladder schedule mirror](#ladder-schedule-mirror). `schedule_archive.join_races` implements the join, and the build and the schedule planner share it.

- **Slot:** one row of the mirrored schedule. Its start is its `time`, in Unix seconds. Slots are taken in `(time, id)` order.
- **Join:** a slot becomes an included race in one of two ways, tried in this order.
  1. The slot's `raceId` matches the number in an included race's `getseed.php?race=<n>`.
  2. Failing that, it matches an included race without such a number. The race must be in the category of the slot mode's archetype and start between 5 minutes before and 30 minutes after the slot (`JOIN_EARLY` and `JOIN_LATE`). Each such race matches once.
- **Category of an archetype:** `ARCHETYPE_CATEGORIES` maps an archetype to a race mode: 1 simple, 2 entrance, 3 inverted, 4 keys, 5 glitched, 6 fun, 7 mystery, 9 spoiler. **Archetype 8** (modes 20 to 24) has no category, so its slots join only by seed. That is not an error in the build.
- **Slot outcomes:**
  - A slot whose `raceId` belongs to a race excluded for its mode (`ladder_test`) is dropped.
  - A slot with a null `raceId` and no race is **pulled**: it was removed from the schedule.
  - Every other slot is a **scheduled race**. It **started** if it joined a race. Otherwise it **never started**, and it counts as **0 racers**.
- **Racers per scheduled race:** the joined race's entrant count, which is the field (see **Races**), or 0 for a scheduled race that never started.
- **Counted through:** a slot counts in the schedule panels only when it starts on or after the archive's first date (`data_start_local`, Eastern Time), and no later than the earlier of two times:
  - one day before the start of the newest included race (`SETTLE`), because rooms may still be open;
  - the start of the latest slot in the mirror that has a `raceId`.

  The second time is a staleness guard: alttpr.racing fills in a `raceId` only when a room is created, so a mirror synced once a day must never make recent slots look pulled. With a daily sync the first time is almost always the earlier one, and the guard bites only when a sync fails or someone builds with an old mirror. When no slot has a `raceId`, nothing counts. The result is published as `source.schedule_counted_through` and shown on the page.
- **Scheduled day:** an Eastern Time date inside the archive's dates that holds at least one slot. Pulled slots count. Slots of `ladder_test` races do not. The days that are not scheduled are the gaps between seasons and the time before the first slot, so April 2025 has none.
  - Scheduled days deliberately ignore **counted through**. The schedule is published ahead, so whether a day has races is known before its outcomes are, and the latest days stay in the per-day averages.
  - A season the mirror does not list yet only delays its days. Their entries leave the numerator too, so the average is never inflated.
- **Slot times, not seasons:** the panels bucket a slot by its own Eastern Time start and never by its season number. API seasons overlap, so the season of a slot says little about when it ran.

**Seasons**

These terms drive the [season ratings](#season-ratings). `schedule_archive.season_starts` and `season_of` implement them.

- **Season start:** season 1 starts at its first slot. Every later season starts at its first slot after the middle of the season before it, which is the later of that season's start and the median time of its slots. A season with no slot after that point has no start yet. alttpr.racing lists no future slots, so a season's end is only known once the next season's first slot appears.
  - A race's season comes from this rule, not from the season number its slot carries. alttpr.racing files the odd slot under the wrong season, such as 3 season 1 slots inside season 9, and the rule keeps such a slot from moving a boundary. `diagnostics.seasons.stray_slots` counts the slots outside their own season's run.
- **Season:** the span from 5 minutes (`JOIN_EARLY`) before its start until 5 minutes before the next season's start. A race belongs to the season its start falls in. Races before season 1's span have none, and `diagnostics.seasons.races_without_season` counts them.
- **Current season:** the newest season holding at least one included race.

### Schedule and participation metrics

`build_stats.py` computes these for `race-stats.json`. They drive the [Ladder data panels](#ladder-data-panels).

- **Periods** are calendar quarters (`Q1` to `Q4`). A race belongs to the quarter of its Eastern Time start, and a counted slot to the quarter of its own Eastern Time start. Edge periods are marked partial when the archive does not cover the full quarter. Each period keeps the finished-race summary and the schedule metrics below, so its slot counts add up to the overall ones. A counted slot in a quarter with no finished race is a build error.
- **Race turnout** uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries, not people.
- **Monthly participation** is the average entries per finished race: total race entries divided by the finished races in that calendar month. A month's finished-race summary also gives the median, the middle half (quartiles) and the 95% range.
- **Entries per scheduled day** is the entries of that month's finished races that started on scheduled days (`scheduled_day_entrant_total`), divided by the month's scheduled days (`scheduled_day_count`, see **Scheduled day** in [Definitions](#definitions)). It is null, not zero, in a month with no scheduled day. Months without a finished race still appear.
- **Scheduled races per hour** counts the counted slots that are scheduled races, by their Eastern Time start hour, and how many started. The hourly rows have `scheduled_count` and `started_count`. The start rate is `started_count / scheduled_count`.
- **Median racers per scheduled race** is, for each hour, the median of racers per scheduled race over that hour's scheduled races, never-started ones counting 0. `q1_racers` and `q3_racers` are the quartiles of the same values. All three are null when nothing was scheduled in the hour. A median of 0 is real: fewer than half of that hour's scheduled races started.
- **Weekday-hour cells** are the same counts for each of the 168 weekday and Eastern hour combinations: `scheduled_count`, `started_count`, `entrant_total` and `average_racers`, which is `entrant_total / scheduled_count`, or null when nothing was scheduled.
- **Bands** colour the weekday-hour cells, computed separately for all data and for each quarter:
  - Only cells with at least 5 scheduled races (`BAND_MIN_SCHEDULED`) are banded and count toward the quartiles.
  - `q1` and `q3` are the quartiles of those cells' `average_racers`.
  - A cell is `prime` if its `average_racers` is at least `q3`, else `quiet` if it is at most `q1`, else `normal`. A tie at a quartile goes to `prime` or `quiet`.
  - A cell with fewer than 5 scheduled races has no band, and when no cell qualifies none has one.
- **Medians and quartiles** describe the observed distribution. Quartiles are the median of each half of the sorted values, leaving out the middle value when the count is odd. The 95% ranges are normal confidence intervals for the mean using the sample standard deviation, and the ranges for entry counts never go below zero. A group with fewer than two values has no range.
- **Diagnostics** report `diagnostics.schedule`: `scheduled_count`, `started_count`, `pulled_count`, and `unscheduled_race_count`, the included races up to **counted through** that no slot became.

Some of this is deliberate behaviour of the data, not a defect:

- **Thin quarters.** A partial quarter, the current one included, has few scheduled races per cell, so most of its cells are unbanded until about mid-quarter. All data and full quarters are fully banded.
- **A sawtooth in the hourly counts.** Every third hour (2, 5, 8 and so on to 23) has about two-thirds of the scheduled races of the others, because seasons alternate their hour grids.
- **Overnight medians.** The median racers per scheduled race is 0 in the overnight hours, such as 1 to 6 AM, because fewer than half of those scheduled races start.
- **Before the schedule.** A race that started before the first slot joins no slot, so the schedule panels leave it out. The first such races, in April 2025, are in the monthly turnout but not in a per-day value.

### Ladder rating

`scripts/ladder_rating.py` rates every racer from who beat whom. It is in-house and deliberately not racetime.gg's score.

**Racetime.gg points are ignored entirely.** `score` and `score_change` are never published or used by any racer metric. racetime.gg's recorded `score` is read in only one place, as the benchmark in the [Backtest](#backtest).

#### In plain words

**Only placement counts.** Finish times are never read, because the game is volatile and a seed's luck says little about skill. A win by one second is worth the same as a win by an hour. Within a race, every pair of entrants is a small match: a finisher beats anyone placed behind them, any finisher beats a DNF or DQ, and two non-finishers, or two racers sharing a place, are skipped. Beat someone you were expected to lose to and the rating moves a lot; beat someone you were expected to beat and it barely moves. Newcomers move the most, because the ladder knows least about them.

The overall rating covers every mode, and each mode also has a rating of its own (see [Mode ratings](#mode-ratings)). **Winning never costs points**, and every newcomer shows exactly **1000** and climbs as the ladder gets to know them. **Your recent races matter most**: a race's weight halves after about 14 later races of five racers. A big race moves you less than its number of opponents suggests, because its results all share the same racers. Nothing decays while you are away.

#### Model

This is the Bayesian Bradley-Terry "full pair" model of Weng and Lin (2011), the method behind OpenSkill. Each racer has a skill $\mu$ and an uncertainty $\sigma$, and a newcomer starts at $\mu_0 = 25$, $\sigma_0 = 12$. For every racer $i$, sum over the opponents $q$ they have a decisive pair with, all from the pre-race states, so the update is simultaneous:

$$c = \sqrt{\sigma_i^2 + \sigma_q^2 + 2\beta^2} \qquad p = \frac{1}{1 + e^{(\mu_q - \mu_i)/c}} \qquad s = 1 \text{ (ahead)},\ 0 \text{ (behind)}$$

$$\Omega = \sum_q \frac{\sigma_i^2}{c}(s - p) \qquad \Delta = \sum_q \frac{\sigma_i}{c} \cdot \frac{\sigma_i^2}{c^2}\, p(1-p)$$

$$d = (n - 1)^{-\alpha} \qquad \mu_i \leftarrow \mu_i + d\,\Omega \qquad \sigma_i^2 \leftarrow \max\big(\sigma_i^2 \cdot \max(1 - d\,\Delta, \kappa),\ \sigma_{floor}^2\big)$$

$d$ damps a race of $n$ entrants by its size. Every pair counts as a match, but a race's pairs share their racers, so they are not independent results: without $d$ a 32-racer race would count as 31 of them, and with $\alpha = \tfrac12$ it counts about as much as 6.

The win chance that $a$ beats $b$ is $1 / (1 + e^{-(\mu_a - \mu_b)/\sqrt{\sigma_a^2 + \sigma_b^2 + 2\beta^2}})$. It drives upset tags and the head-to-head "Win chance" column, and never uses points. Both always use the overall rating, even with a mode picked (see [Mode ratings](#mode-ratings)).

#### Recency

Uncertainty shrinks with every race but never below $\sigma_{floor}$. The floor keeps every race's update above a minimum size, so old races fade by **race count**, not by the calendar, and a racer who is away keeps their points. For a racer at the floor against evenly matched opponents, a race's weight halves after about $\ln 2 / g$ later races of $n$ entrants, where $g = (n-1)^{1-\alpha}\, \sigma_{floor}^2 / \big(4(2\sigma_{floor}^2 + 2\beta^2)\big)$: about 14 races of 5 entrants, 28 of 2 and 5 of 32. Before damping it was the same 14 races of 5, but 55 of 2, and under 2 of 32, so one big race could all but overwrite a rating.

#### Parameters and tuning

| Parameter | Value | Meaning |
|---|---|---|
| $\beta$ | 8 | per-match performance noise |
| $\sigma_0$ | 12 | a newcomer's starting uncertainty |
| $\sigma_{floor}$ | 4 | uncertainty never shrinks below this |
| $\alpha$ | ½ | field-size damping: a race's update is scaled by $(n-1)^{-\alpha}$ |
| $\kappa$ | 1e-4 | smallest share of its variance a racer keeps after a race |
| $\mu_0$ | 25 | a newcomer's starting skill |

$\beta$, $\sigma_0$ and $\alpha$ were picked by `scripts/rating_lab.py` from races before 2026-01-01 only, among settings whose veterans move at least as much as the previous rating's ($\beta = 12$, $\sigma_0 = 8$, no damping), and then compared with four other algorithms on 2026 races, once (see [Rating comparison](Analyses.md#rating-comparison)). The floor was not tuned for accuracy: it was set to 4 by choice, for a "livelier" ladder. A calmer floor predicts a little better but feels stuck. A veteran's median win is worth +10 points, finishing last in a race without DNFs -9, and a DNF -12. 2026 races were never used for tuning, so the comparison is out of sample.

#### Points

The displayed points are $1000 + s \cdot (\mu - k\sigma)$, the "conservative rating" idea from TrueSkill and Xbox levels. With $k = \mu_0 / \sigma_0 \approx 2.083$ a newcomer shows exactly 1000, and as $\sigma$ shrinks the points climb toward $\mu$. The scale $s = 400 / (\ln 10 \cdot \sqrt{2\sigma_{floor}^2 + 2\beta^2}) \approx 13.73$ makes 400 points about 10:1 odds between established racers, as in Elo. The pipeline publishes each racer's points, $\mu$ and $\sigma$ (4 decimal places), overall and in each mode they have raced, and every race entry's points after that race: overall, in the race's mode, and, for a race in a season, in the season and in the season's mode (see [Season ratings](#season-ratings)). The season points are null for a race outside every season.

#### The guarantee

A winner's points never fall. Every pair a winner is in is decided in their favour, so $\Omega > 0$, and $d > 0$, so $\mu$ rises; $\sigma$ never rises, so $\mu - k\sigma$ rises too, and rounding is monotone. Finish times have no effect at all. Tests enforce both across every archived race, for the overall rating and for every mode rating, and for every season's overall and mode rating.

#### Mode ratings

Each mode also has its own rating: the same model with the same parameters, run on that mode's races alone. Nothing was retuned for it. A mode rating never reads another mode's races, so a racer's first race in a mode starts at exactly 1000 however experienced they are elsewhere, and a racer is provisional in a mode until they have 5 finishes in it. The guarantee holds in every mode. Seasons have mode ratings too (see [Season ratings](#season-ratings)).

Mode ratings are for standings: the leaderboard with a mode selected and the rating chart on a racer page with a mode picked. Predictions stay on the overall rating, because it predicts results inside a mode better than the mode's own rating does: on 2026 pairs, a log loss of 0.534 against 0.546 (see [Backtest](#backtest)). Most modes have too few races for a rating that starts from nothing, and the gap is widest in the smallest modes. So the head-to-head Win chance column, the Upset tag and the top-rated racer of God Route, Big Routing and Upsetti always use the overall rating.

#### Season ratings

A **season** is one run of the ladder, as the alttpr.racing schedule numbers them (see **Season** in [Definitions](#definitions)). Each season also has its own ratings, overall and in every mode, beside the all-time ones: the same model with the same parameters, run on that season's races alone. Nothing was retuned for it. `ladder_rating.rate_by_season` computes them.

- **Full reset:** every racer debuts at exactly 1000 in every season, and again in every mode of it, however they rated before. A racer is provisional in a season, or in a season's mode, until they have 5 finishes there.
- **Standings only:** season ratings carry no $\mu$ or $\sigma$. Predictions stay on the all-time overall rating, as they do for mode ratings: win chance, the Upset tag, and the top-rated racer of God Route, Big Routing and Upsetti.
- **Which season a race is in:** the season whose span holds its start (see [Definitions](#definitions)). Every included race counts, scheduled or not, and a race in the break between two seasons counts toward the earlier one. The 5 `JOIN_EARLY` minutes let a race that started just before its first slot count.
- **Schedule lag:** the schedule mirror syncs once a day and races every two hours, so a new season's first day can count toward the old season until the next daily schedule sync. This is display only: it cannot lock a wrong boundary (see below).

**Locking.** A season's ratings are locked once the season has ended and none of its races awaits recording, so they never change again, even if the rating model does. `python3 scripts/build_stats.py --lock-seasons` builds as usual and then locks every season without a ledger, once all four hold:

1. The next season started at least a day ago (`SETTLE`).
2. At least 10 slots follow the next season's start (`LOCK_CONFIRM_SLOTS`), and none of them is the season's own. A misfiled slot that put the next season's start too early would be followed by the season's own slots, so it can never fix a wrong boundary: the season stays open.
3. The archive holds a race that started after the next season's start, so the race sync has covered the season's end.
4. Every race of the season is settled (`race_archive.is_settled`): recorded, unrecordable, or older than the 14 days after which the archive stops refreshing it. A race that was never recorded cannot block a lock forever.

Only the data bot passes `--lock-seasons` (see [Scheduled data refresh](#scheduled-data-refresh)). A plain build never locks. The build prints `Locked the ratings of season N.` or `Season N stays open: <reason>.` for every season without a ledger.

**The ledger.** `season-ratings/season-N.json`, beside `archive/` and outside `site/`, holds one locked season: `{schema_version: 1, season, start, end, rating, races}`.

- **Contents:** `start` and `end` are the season's span in UTC, and `rating` is the parameters at lock time. Each race is `{r, mode, e}`: the race slug, the mode name, and `e`, a list of `[user ID, season points, season-mode points]` in entry order. It is keyed by slug, user ID and mode name, never by indexes, so it survives a rebuild that renumbers races and racers.
- **Deterministic:** it holds no wall-clock time, so two locks of the same archive write identical bytes.
- **Written once:** it is created exclusively and never overwritten. Deleting a ledger is the only way to lock that season again, with the rating as it is then.
- **What the build does with it:** a locked season's races and points come from its ledger, whatever the rating computes today. A race the spans place in a locked season that the ledger does not hold, or a ledger race whose mode or entrants no longer match, gets no season and is never re-rated. Both are counted in `diagnostics.seasons` (`left_out_of_locked_seasons`, `mismatched_locked_races`).
- **No visible change:** while the rating is unchanged, locking changes no published byte. Changing the rating changes the open season only.

#### Backtest

`python3 scripts/ladder_rating.py --backtest` replays the archive and, before each race, predicts every decisive pair of entrants from the overall rating, from the race's mode rating and from racetime.gg's recorded `score`. It also counts winners whose points fell and the median move of a veteran. The method, the latest figures, how racetime.gg's own rating differs, and the rating's trade-offs are in [Backtest](Analyses.md#backtest).

### Highlights

`scripts/highlights.py` derives every tag, badge, streak, preset statistic, record, claim to fame and fact in one chronological pass, and `participants.json` publishes the results. Finishers, timed finishers, the winner and provisional racers are as defined under [Definitions](#definitions); where a winner is anonymous, winner-dependent tags are skipped. Every tag and badge that reads a rating reads the overall rating. Thresholds are constants at the top of the module.

#### Race tags

Within a race, tags appear in the order mode record, preset record, first win, upset, photo, PB, milestone, badge.

Each kind has its own colour, and so does the Hardest preset tag. The colour fills the tag's dot and outlines its pill. The colours stay distinct from each other under colour-blind simulation, and every tag names its kind, so colour is never the only cue.

The two record tags are the exception: they share the PB tag's green, because a record is a PB for the whole ladder, and they shine instead. Their pill has a green-tinted fill and a soft glow, their dot is a four-point sparkle, and a sheen sweeps across them every four seconds. With reduced motion the sheen stays still and the rest remains.

- **Mode record:** the race's fastest time beats the mode's record in whole seconds, the precision the page shows. The first race in a mode sets the baseline and is not tagged.
- **Preset record:** the race's fastest time beats the preset's record in whole seconds, and the race does not set a mode record. A mode record always beats the preset's record too, so a race carries one record tag at most, the wider one. The first race in a preset sets the baseline and is not tagged. A race without a preset has no preset record.
- **First win:** the racer's first place-1 result in any mode.
- **Upset:** the winner's pre-race win chance against the favourite is 15% or less. The favourite is the highest pre-race overall rating among the entrants who are not provisional; the race needs at least 3 identified entrants, and the winner must not be the favourite. 3.7% of races qualify.
- **Photo finish:** places 1 and 2 are both timed finishers separated by less than 5 seconds. The margin uses the exact times.
- **PB:** a strict improvement, in whole seconds, on the racer's previous best timed finish in the same mode. The first timed finish in a mode is the baseline and is not tagged.
- **Milestone:** the racer's entry count, in any status, reaches 25, 50, 100, 250, 500 or 1000.
- **Badge:** a badge newly unlocked in this race. First Blood is never tagged, because First win covers it.

#### Badges

Each badge records the index of the race that unlocked it.

| Badge | Rule |
|---|---|
| First Blood | Win a race. |
| Hat Trick | Win 3 races in a row, in the racer's own sequence. |
| Hummers | Win 6 races in a row. |
| OkDudeLucksack | Win 9 races in a row. |
| Double D | Win 10 races. |
| Quarter Pounder | Win 25 races. |
| Full Pounder | Win 50 races. |
| On the Ped | Finish on the podium in 5 races in a row. |
| Ped Regular | Finish on the podium in 10 races in a row. |
| Bridesmaid | Finish 2nd in 3 races in a row. |
| No Dodge | Finish 10 races in a row. |
| More No Dodge | Finish 40 races in a row. |
| Nice | Finish 69 races in a row. |
| CenturianV | Finish 105 races. |
| BlazeIt | Finish 420 races. |
| >174 | Enter 175 races you don't win. |
| Omnivore | Finish a race in 7 different modes. |
| Apex Predator | Win a race in 7 different modes. |
| Connoisseur | Finish 20 different seed presets. |
| Rival | Finish ahead of the same opponent 20 times. |
| Sniper | Win by less than 5 seconds. |
| Sniped! | Finish 2nd, less than 5 seconds behind the winner. |
| RETIME | Finish less than 1 second from another racer. |
| Palindrome | Finish with a time whose H:MM:SS digits, hour unpadded and colons removed, read the same both ways. |
| Déjà Vu | Finish two races with exactly the same time. |
| God Route | Win by more than 10 minutes in a field of 7 or more while not its top-rated racer. |
| Big Routing | Win by more than 10 minutes in a field of 7 or more as its top-rated racer. |
| Upsetti | Finish 2nd, 10 minutes or more behind the winner, in a field of 7 or more as its top-rated racer. |
| BigBrain | Win by 15 minutes or more in a field of 9 or more. |
| Big Game Hunter | Win a race with 16 or more entrants, anonymous entrants included. |
| Glitched | Finish a glitched race. |
| Glitched! | Finish 10 glitched races. |
| G1!tch3d!!! | Finish 30 glitched races. |
| G%!#ch@d!#? | Finish 100 glitched races. |
| The Upside Down | Finish an inverted race. |
| Mirror Universe | Finish 30 inverted races. |
| Keys | Finish a keys race. |
| Locksmith | Finish 30 keys races. |
| Simpler Times | Finish 10 simple races. |
| Simpler Life | Finish 30 simple races. |
| Had Fun | Finish a fun race. |
| Kept Having Fun | Finish 10 fun races. |
| Jet Seed | Finish a Casual Boots race in under 70 minutes. |
| Jet Lag | Win a Casual Boots race in 100 minutes or more. |
| Driving Lesson | Finish a spoiler race. |
| Cruisin' | Win a spoiler race. |
| Entrance | Finish an entrance race. |
| Entranced | Enter an entrance race on 5 days in a row. |
| Entranced! | Enter an entrance race in 20 weeks in a row. |
| What? | Enter a mystery race. |
| I Like It Here | Finish 10 mystery races. |
| BUSTIN' | Finish 30 mystery races. |
| Both Ends | Finish a mystery race in under 30 minutes, and another in over 130 minutes. |
| Grocery Stick | Finish in the exact middle of an odd field of 5 or more entrants. |
| Lanterne Rouge | Finish last in a field of 10 or more where everyone finished. |
| Rando Sicko | Finish 5 races in one day. |
| Touched Grass | Enter a race after 30 or more days away, counting from 2026-08-01. |
| Yap | Comment on 11 races in a row. |
| Step Letter | Be in a race of 5 or more where everyone finished in alphabetical order. |
| Rettel Pets | Be in a race of 5 or more where everyone finished in reverse alphabetical order. |
| Badger | Collect 30 badges. |

A badge is either a counter, which unlocks when a count reaches its target, or a one-race badge, which unlocks on a single result. In the notes below, a **finish** is a finisher's result, and **day**, **week** and **field** are as defined under [Definitions](#definitions). How the page shows badges is under [Racer pages](#racer-pages).

- **Streak and count badges:** win and finish runs follow the racer's own race sequence across all modes, and a DNF or DQ ends a finish run. The one exception is a DNF in a field of two whose other racer finished: it neither ends the finish run nor counts toward it. A DNF still ends the run when both racers DNF or the other was DQ'd. CenturianV and BlazeIt count finishes. Double D, Quarter Pounder and Full Pounder count wins, in a row or not. On the Ped and Ped Regular use the podium run (places 1-3), which, unlike the finish run, has no DNF exception. >174 counts every race entered and not won, DNFs included. A mode's count badges count finishes in that mode.
- **Bridesmaid:** 2nd places in a row in the racer's own race sequence. Any other result ends the run, a DNF included, even in a field of two.
- **Connoisseur:** distinct seed presets finished. A preset is one row of the published preset list, so a name that two modes share, such as MMMM (Not Slow), counts twice.
- **Rival:** finishes ahead of one opponent, across all modes. Like an H2H win, it counts each opponent who placed lower or didn't finish. An equal place counts for neither racer.
- **Déjà Vu:** two finishes with the same time in whole seconds, the precision the page shows, in any modes. It unlocks on the second of them.
- **Lanterne Rouge:** named after the last-placed rider of the Tour de France. The field is at least 10, every entrant is identified and finished, as for Step Letter, and the finisher whose place equals the field earns it.
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
- **Names for Step Letter and Rettel Pets:** each entrant's name as archived with that race, normally within hours of it, not their current name, so a later rename never adds or removes either badge. The archive was first filled in bulk on 2026-10-27, so earlier races carry the names racers had on that date.
- **God Route:** places 1 and 2 are both timed and more than 10 minutes apart, using the exact times, and the field is at least 7. "Top-rated" is the entrant with the best pre-race overall rating among those who are not provisional and raced in the previous 90 days. When no entrant qualifies, the winner earns it.
- **Big Routing:** God Route's condition, but the winner must be the top-rated entrant, as defined for God Route. When no entrant qualifies, nobody earns it. Every such win earns exactly one of God Route and Big Routing.
- **Upsetti:** places 1 and 2 are both timed and at least 10 minutes apart, using the exact times, the field is at least 7, and place 2 is the top-rated entrant, as defined for God Route. When no entrant qualifies, or the top-rated entrant finishes elsewhere or doesn't finish, nobody earns it. Unlike God Route's "more than", exactly 10 minutes is enough.
- **BigBrain:** places 1 and 2 are both timed and at least 15 minutes apart, using the exact times, and the field is at least 9. Ratings play no part.
- **Omnivore:** needs 7 of the 8 modes, because requiring all 8 left 0.8% of racers holding it.
- **Apex Predator:** Omnivore's rule for wins: a win in 7 different modes.
- **Badger:** counts every badge the racer holds, First Blood included. It unlocks in the same race as the 30th badge.

Each badge ships an animated image and a still under `site/assets/badges/`. To add or remove a badge, see [Badge_Guide.md](Badge_Guide.md).

The badge art in `site/assets/badges/` includes [Twemoji](https://github.com/twitter/twemoji) graphics, licensed [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/), for Step Letter, Rettel Pets, BlazeIt, Both Ends, Grocery Stick and RETIME.

#### Streaks

- **Daily:** consecutive Eastern Time dates with at least one entry. The current streak is the run ending on the racer's last race day.
- **Win, podium and finish:** runs over the racer's own race sequence across all modes. Podium means places 1-3; finish means finisher. A finish run skips a DNF in a field of two whose other racer finished, as under [Badges](#badges). The current streak is the run still open after the racer's latest race, and is absent if that race broke it.

#### Presets

Presets are named as defined under [Definitions](#definitions). Statistics are races, entries, DNF rate (entries that are not finishers), median time, median winning time, mean field size, and the fastest finish.

- **Hardest preset:** the highest DNF rate among presets with 8 or more races.

#### Records

- **Mode records:** one progression step per race that sets a new record, per mode.
- **Photo finishes:** the 10 smallest margins between places 1 and 2 among photo finishes (see [Race tags](#race-tags)).
- **Blowouts:** the 10 largest margins between places 1 and 2, in fields of 5 or more racers.
- **Big fields:** the 10 largest fields of 16 or more that had an identified winner.
- **Most races:** the 10 racers with the most entries.
- **Most-met rivals:** the 10 pairs of racers who entered the most races together. Any entry counts, DNFs and DQs included.
- **Streak boards:** the 10 best streaks of each kind. Whether a streak is still active depends on today's date, so the page works that out (see [Hall of records](#hall-of-records)).
- **Facts:** about thirty English sentences generated from the data, such as the hardest preset, the biggest upset or the busiest day of the week. A fact without enough data is skipped.
  - Each fact is published as three strings: the text before its headline number, the number, and the text after. The page highlights exactly that number, even when a racer's name holds digits.
  - A streak fact needs a streak of 2 or more, and the comeback fact a break of 30 days or more, measured as for Touched Grass but with no start date.
  - Mode facts leave out the `unknown` mode.
  - Every fact comes from the archive alone. A record's age counts to the archive's latest race date, not to today.

#### Claim to fame

A racer's claim to fame is the one thing about them that the fewest racers match. The [Featured daily racer](#featured-daily-racer) card shows it.

- **Stats:** wins, longest win streak, longest podium streak, longest finish streak, races entered, longest daily streak, PBs, different opponents, night finishes and longest comeback, in that order.
  - PBs count improvements, as the PB tag does, so a mode's first timed finish is not one.
  - Different opponents counts every racer met in a race, DNFs and DQs included, as Most-met rivals does.
  - Night finishes are finishes in races starting between midnight and 5 AM ET.
  - The longest comeback is the most days between two of the racer's consecutive race days.
- **Count:** a stat takes part only at 2 or more. Its count is the number of racers whose value is at least as high, the racer included, so tied racers share a count.
- **Badge:** the racer's rarest badge takes part too, with its holder count as the count. Among equally rare badges, the one earlier in the badge table wins.
- **Pick:** the lowest count wins. Equal counts go to the stat earlier in the list above, and a badge comes after every stat. A racer has no claim when the lowest count is above 10% of the racers who have entered a race.

### Chest counts (CR and CPH)

- **CR** is the number of chests a racer says they opened, and **CPH** is chests per hour: CR × 3600 ÷ finish seconds, rounded to a whole number. Both are soft, self-reported metrics. CR is parsed from the racer's own post-race comment, so it is only as accurate as the comment, and some comments will give a wrong number.
- The build scans each comment for runs of exactly two or three digits. A run is skipped if it:
  - is below 30 or above 300;
  - follows a `/`, with or without a space (the seed's total, as in `164/216`);
  - has `<`, `>` or `+` directly attached on either side;
  - follows `more than`, `less than`, `over`, `under` or `sub` as a whole word, with or without a space (`sub100`);
  - is followed by `CPH`, or by `%` or a time unit (`m`, `min`, `h`, `hr`, `s`, `sec` and their longer or plural forms);
  - is part of a clock time such as `1:45`.

  A `-` does not skip a number.
- CR is the first remaining number whose CPH falls between 30 and 175, inclusive. If there is none, the entry has no CR. Only entrants with status `done` and a finish time can have one, so a comment on a DNF or DQ is never parsed.
- A comment may name several numbers, and the first plausible one wins, so `200 check 199/216` reads as 200. A number like the 50 in `50/50` or the 75 in `19.75` can also pass when it fits the pace range.
- The parsed CR is stored in `comments.json`, and CPH is computed in the browser from the finish time in `participants.json` (see [Latest results](#latest-results)).

## The dashboard

Everything in this part happens in the browser: the scripts in `site/` read the datasets above and compute the rest on the page. Data freshness is build-time: the dashboard trails racetime.gg by up to about two hours, the interval of the [scheduled data refresh](#scheduled-data-refresh). The browser reads the local data files and never crawls racetime.gg.

### Page layout and navigation

The page runs top to bottom:

1. The [Top-rated banner](#top-rated-banner).
2. **01 Latest**: the newest results with their tags.
3. **02 Racers**: the leaderboard, with its season select and the rating column.
4. **03 Records**: the hall of records.
5. **04-07**: the Ladder Data panels, on the schedule, participation, traffic and turnout.

Each racer also has a page with their ratings, badges, latest results, and charts (see [Racer pages](#racer-pages)).

The masthead holds the category artwork, the title block, a racer comment and the visitor counter, with their tops aligned. The title block ends with **Data snapshot**, the age of the published data: "just now" under a minute, then whole minutes, hours or days, counted from when the data was last built and updated every minute. Hovering it shows the exact UTC time. The comment bubble is at most 450px wide. The visitor counter hides at 1199px and below, and at 980px and below the comment moves to a row of its own under the artwork and title.

A nav bar under the masthead links to the seven sections, grouped as Racing (01-03) and Ladder Data (04-07), whose links are Schedule, Participation, Traffic and Turnout. Ladder Data is hidden on phone-width screens, and the whole bar is hidden on racer pages. The top-rated banner sits between the nav bar and Latest, and is hidden on racer pages too.

### Top-rated banner

The banner crowns the top-rated racer in each category: **Overall**, then every mode in mode order except `unknown`, which only groups races whose mode could not be read. The featured daily racer uses the same categories.

- **Scope:** a two-button toggle in the banner header, **Season N** (the current season, pressed by default) and **All time**, in a group labelled "Rating period". Toggling redraws the tiles in place, and the choice is not persisted. With no seasons there is no toggle and the banner is all-time.
- **Who:** a tile shows exactly the top of the leaderboard sorted by rating in its category and scope: the season's rating under a season, the all-time rating under **All time**. There are no filters and no decay, so a leader keeps the crown until someone rates higher. A season tile shows the season rating, and runners-up use the season's ranks.
- **Ties:** ranks are competition ranks, so racers tied at the top share one tile. It shows up to three avatars, names the first two in name order, and adds **+N** for the rest.
- **Tile:** the category, the avatar, the name and the rating. Each name links to that racer's page, opened on the tile's mode (**All modes** for Overall). Racer pages are all-time whatever the scope. A category where nobody is rated in the scope has no tile.
- **Runners-up:** under the leader, the next five racers in the category, as small avatars side by side, in rank order and then name order. Tied leaders are not repeated there. Hovering or focusing one shows its rank, name and rating, such as "#2 Alice · 1834", and clicking it opens that racer's page on the tile's mode. A category with fewer rated racers shows fewer.
- **Layout:** one row that keeps names whole and scrolls sideways when it is wider than the screen.

### Latest results

- Each race shows its [race tags](#race-tags). Expanding a race lists each entrant with their change in the season's rating in that race's mode: `+12`, `−9`, or `±0`, with a true minus sign and no arrow. ▲ and ▼ are for rank movement only, so a points change never looks like a rank change. The change is the entrant's season-mode points after the race minus their points before it, or minus the starting 1000 in their first race of that mode in that season. Its tooltip reads, for example, "Season 10 Keys rating after this race: 1234". A race with no season shows no change.
- **Entrant tags:** in an expanded race, each entrant's row shows the race tags that name that entrant, between the name and the finish time, in race-tag order. Each tag stands alone there, with the same label and tooltip it would have for one racer on the race row, so Upset and Photo finish appear on the winner. At phone width the tags drop to a line under the name.
- **CR and CPH** (see [Chest counts](#chest-counts-cr-and-cph)) appear only where races are listed row by row: each finisher's row in an expanded race, and each of the racer's own rows in a racer page's [Latest results](#racer-pages).
- The sidebar holds **Your racers**, the **Featured daily racer**, **Fact of the day**, **Latest badges** and **Latest avatars**, in that order.
- **Latest badges** lists the 12 most recent unlocks by anyone, however long ago. Clicking a badge opens its holders panel, as on a racer page.
- **Latest avatars** shows the 6 racers whose avatar changed most recently, newest first, each with how long ago the change was first seen. The section is hidden when there are none. A racer's avatar follows the [Pokémon avatars](#pokémon-avatars) precedence, so two kinds of change count, and the later one dates the racer:
  - **Upload:** the racer's mirrorable racetime.gg upload changed, appeared or went. `avatar_mirror.py` sorts a racer's avatar observations as it does to pick the current avatar (see [Privacy](#privacy)), and compares their mirrored file names, so an upload that cannot be mirrored counts as none. A change needs an earlier observation with a different value; its time is the first observation of the current value after the last different one. A racer whose upload went shows the random Pokémon that replaced it. An upload change does not count while a favourite Pokémon, or a favourite of `random`, hides the upload.
  - **Favourite:** the favourite Pokémon named in the racer's bio was added, switched or removed. `pokemon_avatars.py` reads the favourite of each bio in the racer's [bio history](#bios), oldest first; the change is the last bio whose favourite differs from the one before it, at that bio's `since`. An edit that keeps the same favourite is not a change. `random` is a favourite of its own, so naming or dropping it is a change. A racer without an upload who names or drops `random` is listed too, although their random Pokémon stays the same.
  - **Not counted:** a racer's first avatar, so a new racer is not listed, and a random Pokémon that moves because other racers changed (see [Pokémon avatars](#pokémon-avatars)).
  - **When:** the archive can only date what it saw, so a change is dated by the sync that first saw it, and nothing from before the archive tracked it is known. The leaderboards are synced every two hours. A bio is only checked after its racer races, so a favourite picked between races is dated by the check after their next race; bio history began on 2026-11-05. The leaderboard file keeps only each racer's latest upload, so a switch back to an earlier picture before any race file recorded the one in between leaves no trace. The page shows the Eastern Time date; changes on the same day are ordered by the exact time first seen.

#### Featured daily racer

The Latest sidebar's **Featured daily racer**, between **Your racers** and **Fact of the day**, spotlights one racer a day.

- **Candidates:** racers named by a race tag (see [Race tags](#race-tags)) in races from the 14 Eastern Time calendar days before today. Today's races do not count. A tag names the racer it is about: the record setter, the first-time winner, the upset or photo-finish winner, the PB setter, the milestone racer or the badge earner.
- **Exclusion:** a racer in the top 3 of any category (see [Top-rated banner](#top-rated-banner)) at the end of yesterday is not a candidate, in either scope: all time or the current season. Ranks are competition ranks, so ties can put more than three racers in a top 3.
- **Pick:** candidates are listed in name order. The pick starts at the candidate given by the 32-bit FNV-1a hash of `featured YYYY-MM-DD`, using today's Eastern date, modulo the number of candidates. It steps forward, wrapping around, past anyone who has climbed into a top 3, in either scope, since.
- **Stability:** everyone sees the same racer all day. A data refresh changes the pick only when the featured racer reaches a top 3, in either scope, that day. A page left open past midnight keeps its racer until it is reloaded.
- **Card:** the **Your racers** card with up to two more lines. Like that card, it shows the racer's 7-day rank movement in the current season's overall ranks (see [Racer leaderboard](#racer-leaderboard)). The section is hidden when there are no candidates.
  - **Highlight:** the racer's latest highlight in the window and how long ago it was, such as "★ 250th race · 11 days ago".
  - **Claim to fame:** the racer's [claim to fame](#claim-to-fame), such as "✦ 1 of 7 racers with 7+ wins in a row". A count of 1 reads "✦ Ladder best: …" instead. A badge claim shows the badge's art and name, and clicking it opens the badge's holders panel. A racer without a claim has no such line.

#### Fact of the day

The Latest sidebar's **Fact of the day**, between the **Featured daily racer** and **Latest badges**, shows one of the [facts](#records) a day.

- **Rotation:** with N facts, every fact shows once in each cycle of N days, and a fact that shows is not repeated for the next 7 days.
  - Day D counts the days from 1970-01-01 to today's Eastern date, and the cycle is ⌊D / N⌋.
  - A cycle's shuffle is the fact indexes sorted by the 32-bit FNV-1a hash of `CYCLE INDEX SEED`, ties going to the lower index. The seed is `fact of the day 2`, set as `FACT_SEED` in `feed.js`. Changing it picks a new schedule from that day on.
  - **The 7-day rule:** the 7 facts that close the previous cycle's shuffle move to the back of this cycle's first 14 slots, keeping their order, so none of them can show in this cycle's first 7 days.
  - **Why the rule needs no history:** only those 14 slots move, so every cycle closes exactly as its shuffle does, and nothing about past days is stored.
  - **Fewer facts:** the rule needs at least 21 facts. With fewer, the 7 becomes ⌊N / 3⌋ and the 14 twice that.
  - Day D shows the fact at position D mod N of the result.
  - The rule holds while the list of facts stays the same. A fact that appears or drops out changes N and reshuffles the schedule, so in the week after that, a fact can repeat early.
- **Stability:** everyone sees the same fact all day, though a data refresh can update its numbers.
- **Highlight:** the fact's headline number is set larger, in the display face.

### Racer statistics

The browser computes these from `participants.json`. The leaderboard, the racer pages and the player card all use these definitions.

- A racer counts as finishing a race only when they are a [finisher](#definitions): their entrant `status.value` is `done` **and** the API reports a `place`. Everything else counts toward DNF, except `dq`, which is tracked separately on the racer page.
- **1st, 2nd and 3rd** count first, second, and third place finishes regardless of field size. Because a podium in a field of three is not a podium in a field of thirty, read them alongside the head-to-head win rate.
- **Median time** covers finished races only and comes from `finish_time`, rounded to whole seconds (the rating, tags and records use the exact fractional time). With an even number of finishes it is the mean of the middle two. Racers with no finishes show `--` rather than zero.
- **Win %** is the share of races won outright: first places divided by races entered. A race that ended in a DNF still counts in the denominator.
- **Head-to-head** pairs every racer in a race against every other racer in that same race. The lower `place` wins. Any finisher beats any non-finisher. Two non-finishers draw.
- **H2H win %** is wins divided by wins plus losses across every pairwise matchup. Draws are reported in their own column on the racer page and are excluded from the denominator. This normalizes for field size: finishing fifth of ten is five wins and four losses, so a racer who never wins outright can still rate highly by consistently placing near the top of large fields.
- **Trend** compares the win rate over the last five decisive meetings against the career rate with that opponent, and appears only after more than five decisive meetings. A move of more than five percentage points shows as up or down; anything smaller shows as steady.

### Racer leaderboard

- **Season:** a **Season** select above the table picks the scope: every season, newest first and the current one by default, then **All seasons**. With no seasons the select is hidden and the board covers the whole archive. Changing it resets the sort to the default.
  - Under a season, every column except Badges is calculated from the races of that season, and of the selected mode when one is picked, with the rules under [Racer statistics](#racer-statistics). That includes H2H win %.
  - Under **All seasons**, every column covers the whole archive.
  - A racer with no race in the scope has no row. The table's status text names the season.
- **Rating:** by default the board is sorted by the rating, highest first, with ties broken by name. The Rating column follows the selected scope and mode. Under a season it shows the [season rating](#season-ratings), overall under **All modes** and else in the selected mode. Under **All seasons** it shows the all-time [Ladder rating](#ladder-rating), overall or in the selected mode (see [Mode ratings](#mode-ratings)). Its tooltip names the scope, such as "Season 10 rating · Keys" or "All-time rating". A racer is provisional, or unrated, in a scope by the rules of that scope. There is no minimum-races cutoff.
- **Position:** the number beside each racer is the row's position as shown, so it renumbers whenever a sort, filter or search changes the table.
- **Rank movement:** while the board is sorted by rating, highest first, a ▲ or ▼ beside the number shows how far the racer has moved over the last 7 days in the ranks the Rating column shows: the selected scope's overall ranks under **All modes**, else the selected mode's. **NEW** marks a racer who had no rating there 7 days ago. Under a season that is their first race in the season, or in the season's mode, in that window, so in a season's first week everyone in it is **NEW**. A season that ended more than 7 days ago shows no movement.
- **Ranks:** rating ranks are competition ranks: one more than the number of racers rated strictly higher, so tied racers share a rank. The racer page's rating strip uses the all-time overall ranks. The **Your racers** and **Featured daily racer** sidebar cards show 7-day rank movement in the current season's overall ranks, as ▲N, ▼N or NEW, with a tooltip naming the scope, such as "Season 10 rank, last 7 days". With no seasons they use the all-time ranks.
- **Columns:** 1st, 2nd, 3rd, Median time, Win % and H2H win % are defined under [Racer statistics](#racer-statistics). **Badges** counts the badges a racer has earned, across all modes and the whole archive, whatever season is selected.
- **5+ races only** is an optional button that hides racers with fewer than five races. It counts races in the selected season and mode, so a veteran with only a few races in one mode, or in one season, drops out of that board.
- **Raced in last 30 days** is an optional button that hides racers with no race in the currently selected mode during the 30 Eastern Time calendar days ending today. Any entry counts, including a DNF or DQ. It only hides rows: the stats shown stay those of the selected season.
- **Followed** is an optional button that shows only the racers starred in this browser. Unstarring a racer while it is on drops their row at once.
- Racer pages always cover the whole archive. The reporting-period toggle applies to the dashboard panels only.

### Hall of records

Records runs in three parts. Only By mode follows the mode picker.

- **Across all modes** holds the record cards and streak boards (see [Records](#records)). Its **Active now** view ranks every racer's current streak of two or more: a daily streak whose last day is today or yesterday in Eastern Time, or any other kind whose latest race is within the last 30 days.
- **Record progression** has a step chart per mode, on one shared date axis with each mode's own time axis. The charts sit three to a row on wide screens, two at 1100px and below, and one at phone width. Each header shows the current record, and the chart draws the first recorded time hollow, each break filled, and the current record with a halo.
- **By mode** has a mode picker that drives the current-record summary strip, the finish-time violins of the mode's presets, and the presets table, whose Gold median column is the preset's median winning time (see [Finish-time violins and preset tables](#finish-time-violins-and-preset-tables)). It opens on the most-raced mode.

### Finish-time violins and preset tables

- Each dot is one timed finish: an entrant whose status is `done` with a `place` and a `finish_time`. DNF, DQ, and untimed entries are not plotted.
- The finish-time violin charts draw one violin per seed preset, side by side, with finish time on the vertical axis. Each violin holds every racer's timed finishes in that preset across the whole archive. The shape is a Gaussian kernel density estimate with Silverman's bandwidth, $0.9 \cdot \min(s, \mathrm{IQR}/1.34) \cdot n^{-1/5}$, using $s$ alone when the IQR is zero, cut at the fastest and slowest observed finish. Each dot is spread sideways within the shape by a fixed pseudo-random offset, so dots keep their place across redraws. A preset whose finishes all share one time shows dots without a shape.
- The bold line marks the preset's median time and the gold line its median winning time. Both come from the preset statistics (see [Presets](#presets)), which use exact times, so they match the presets table to the second. The median's tooltip also gives the finishes, the racers, and the quartiles, using the same median-of-halves definition as the dashboard's other quartiles.
- **Which presets:** a preset is charted only with 10 or more races in total, and a chart shows at most 8. The chart's ranking picks the 8; they are then laid out in the fixed mode order, keeping the ranking within a mode. A chart always has 8 equal slots, so a violin is the same width in every chart, and fewer violins sit centred. When presets are left out, a note above the chart says how many.
  - **Hall of records › By mode** charts the picked mode's presets, ranked by total races, then by name. The presets table below it lists every preset.
  - **Racer pages** chart the presets the racer entered most, DNFs included, in the mode picked on the page, or across every mode under **All modes**. Ties go to total races, then name. The racer's finishes are highlighted.
- Each violin takes its mode's color, and each mode keeps one color everywhere. The eight hues come from a palette checked for color-vision deficiency in this fixed left-to-right order, which is why the violins follow mode order rather than speed. When a chart mixes modes, each violin's label also names its mode, so color is never the only cue.
- Preset names wrap onto two lines and end in an ellipsis if they still don't fit; every tooltip gives the mode and the full name. A chart narrower than 880px keeps that width and scrolls sideways.
- **Racer presets table:** below the violins, a racer page lists every preset the racer entered in the picked mode, in the same sortable table as Hall of records › By mode. Under **All modes** it lists every mode's presets and adds a Mode column. Every column is the racer's own, defined like the preset statistics (see [Presets](#presets)) but over the racer's entries only: **Races** entered, DNFs included; **Median time** of their timed finishes and **Gold median** of their timed wins, each rounded to the second; **DNF %**, their entries that are not finishers; **Avg racers**, the mean field size of those races; and **Fastest**, their best time, linked to its race, the earliest on a tie. The Hardest tag still marks the category-wide hardest preset. The table opens sorted by races, in the violins' ranking, and goes back to that whenever the mode or the racer changes.

### Ladder data panels

Sections 04-07 chart `race-stats.json`. [Schedule and participation metrics](#schedule-and-participation-metrics) defines every number they show, and **Schedule** in [Definitions](#definitions) defines the slots they count.

- The reporting-period toggle applies to these panels only; the leaderboard has its own season select. The toolbar also shows **Scheduled races counted through** the Eastern Time date and hour in `source.schedule_counted_through`, the last slot the schedule panels count. Races later than that are not in them.
- **04 Primetime heatmap:** a 7 by 24 grid of weekday against Eastern Time hour. A cell's colour is its band, primetime, normal or quiet, and its text is the racers per scheduled race to one decimal, or "·" when nothing was scheduled. The legend adds "Fewer than 5 scheduled races" for the cells without a band. A cell's tooltip gives its band, the racers per scheduled race, and how many of its scheduled races started.
- **05 Turnout over time:** two monthly charts, the average racers per finished race and the race entries per scheduled day. Months without a scheduled day break the second line. Its tooltip gives the entries and the scheduled days behind each value, and the table lists the scheduled days.
- **06 When races start:** the scheduled races per Eastern Time hour, as bars split into started and never started, with the start rate in the table.
- **07 When races draw a crowd:** the median racers per scheduled race for each hour. Never-started races count 0, so overnight bars can be zero, which is real (see [Schedule and participation metrics](#schedule-and-participation-metrics)). The tooltip and table add the middle half and the start rate, so a 0 is explained.
- The bands are observational leads for schedule experiments, not causal estimates. They do not control for announcements, competing events, holidays, organizer effects, or player availability, and cells with few scheduled races may look extreme by chance. Use the primetime cells to design prospective schedule tests and compare repeated outcomes before making permanent changes.

### Racer pages

- **Heading:** the top line has **← All racers** at the left, and **Follow** and the **Create player card** button (see [Player card](#player-card)) at the right.
  - **Profile:** under it, a column 450px wide, narrowing to 320px before the stats move, holds the name and, under the name, the avatar beside a speech bubble with the racer's newest comment. The avatar and the bubble share a vertical centre, and the bubble's mode, place and race day sit under it. A comment over 80 characters is cut with "…", and hovering the bubble shows the whole comment. **↻**, right after the bubble, steps back through their older comments. A racer without comments has no bubble.
  - **Stats:** a card right of the profile, from the name's top to the panel's right edge, under the actions. A sheen crosses it once when the page opens. Its top row has Podiums, H2H Win Rate and Badges, each a label over a value. Its bottom row has Nemesis and Favourite Victim, each with their avatar and the racer's W-L against them, the Nemesis's in red and the Favourite Victim's in blue.
    - Podiums gives firsts, seconds and thirds in the leaderboard's gold, silver and bronze.
    - H2H Win Rate and Badges each have a thin bar under the value, filled to the win rate and to the share of badges earned. A win rate of "--" has no bar.
    - Below 980px wide the card moves under the profile, and below 620px each rival takes a line of its own.
  - Every stat covers all modes and the whole archive, whatever mode is selected. Among opponents with at least five decisive meetings, **Nemesis** is the one with the lowest head-to-head win rate below 50%, and **Favourite Victim** the one with the highest above 50%; ties go to the opponent met more often.
- **Rating strip:** the racer's current all-time rating overall, then in every mode they are rated in, in mode order. Each tile gives their rank out of every racer rated there, marked provisional below 5 finishes there, and its tooltip explains the rating.
- **Badges:** earned badges come first in the order they were won, oldest first, and locked badges follow in table order (see [Badges](#badges)). Earned badges animate, and locked badges show the grey still. A counter badge fills its border as it progresses. Hovering a badge shows the share of racers who hold it, to two significant digits, counted on the page from the current data. Clicking a badge, here or in the Latest results sidebar, opens a panel with every racer who holds it, earliest unlock first.
- **Latest results:** every race the racer entered, newest first, under a heading per month: the 5 latest at first, then 20 more at a time with **Show 20 more**. It covers every mode, whatever mode is selected.
  - Each row is the racer's own result: the date and Eastern Time start hour, the mode, the preset, their place (blank for a DNF or DQ), their comment, their finish time or DNF or DQ, the field size, their CR and CPH, the race tags that name them, and their change in the season's rating in that race's mode, shown as in [Latest results](#latest-results).
  - The comment fits on one line, and hovering it shows the whole comment. Up to four tags show, then +N. Tags follow the entrant-tag rule under [Latest results](#latest-results), so Upset and Photo finish appear only on races the racer won.
  - Clicking a row expands the whole race exactly as Latest results does, with the racer's own row highlighted.
- **Mode picker:** it sits below the rating strip, the badges and Latest results. Everything above it covers all modes, and everything below it follows it.
- **Rating chart:** it is all-time. Its heading gives the racer's rank and rating in the picked mode, overall under **All modes**, out of every racer rated there, marked provisional below 5 finishes there.
  - The chart has one point per race day: the racer's rating in the picked mode after their last race that day. With a mode picked it covers only that mode's race days and takes the mode's colour.
  - Like the record charts it is a step line that holds each rating until the next race day and runs on to the latest race in the archive. It draws the first race day hollow, the current rating with a halo, and the starting rating of 1000 as a dashed line.
  - Pointing anywhere in the plot snaps to the nearest race day, whose tooltip gives that day's change and races. There is no race-day calendar.
- **Below the rating chart**, the picker also drives the finish-time violins of the racer's presets, the racer presets table (see [Finish-time violins and preset tables](#finish-time-violins-and-preset-tables)), and the head-to-head table, whose Win chance column always uses the overall rating.

#### Player card

The **Create player card** button beside **Follow** on a racer page opens the **Player card** panel with a 600 × 300 PNG of the racer's standing. The browser draws it from the loaded data, and nothing is uploaded. The panel offers four actions:

- **Copy image**, where the browser can write images to the clipboard.
- **Download**, named `<name>-step-ladder-<data date>.png`. `<name>` is the racer's name lowercased, with accents dropped and every other run of characters that are not letters or digits turned into one `-`, or `racer` if nothing is left.
- **Share…**, where the browser can share files, mostly on phones.
- **Copy link**, the racer page's address.

Like the racer page's heading stats, everything on the card covers all modes and the whole archive.

- **Identity:**
  - The avatar, or the page's tinted letter when the racer has none.
  - The name, shrunk and then shortened to fit.
  - The month of the racer's first race.
- **Standing:**
  - The rating rank, out of all rated racers.
  - The rating, labelled provisional below 5 finishes.
  - Under the rank, the 7-day rank movement in the all-time overall ranks, as on the leaderboard under **All seasons**: ▲, ▼ or new.
  - A racer without a rating shows Unrated.
- **Rating line:** the overall rating's race-day step line, as the rating chart draws it under **All modes**, with the starting rating dashed and the current rating haloed.
  - The highest rating is marked **Peak** while the racer is below it. **All-time high** shows when they are on it.
  - The line needs two race days. Without it, the four numbers spread across the card.
- **Numbers:** races, wins (first places), podiums and H2H win %, as on the leaderboard and the racer page's heading.
- **Badges:** earned badges only, rarest first by holder count. Badges held by equally many racers keep the order they were won.
  - The row holds 15 tiles. With more than 15 badges, the last tile reads +N for the rest.
  - The rarest badge is named with its holder share.
  - A racer with no badges sees instead the locked counter badge with the largest share of its target done, with a progress bar. With no progress anywhere, the card says "No badges yet".
- **Nemesis:** the racer page's Nemesis, with the racer's W–L against them. Favourite Victim is left off, so a shared card never singles out a racer the sharer beats.
- **Footer:** the site name and address, and the date of the data snapshot in Eastern Time.

### Automatic updates

`site/version-check.js` checks `version.json` when the page loads, whenever the tab becomes visible again, and every five minutes. It reloads the page once if the published token no longer matches the one embedded in the loaded document (see [Versioning static assets](#versioning-static-assets)).

- A check on load or when the tab becomes visible reloads at once.
- A five-minute poll that finds a new version while the page is on screen waits instead, and reloads at the next visibility change, so the page never reloads under someone reading it. A tab that stays on screen the whole time therefore keeps the old version until it is hidden.
- The reload is guarded by `sessionStorage`: a tab reloads at most once per newly published version. Without that guard a browser still serving a cached `index.html` would report the old token forever and reload in a loop.
