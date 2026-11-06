# CLAUDE.md

A static dashboard for the ALTTPR Step Ladder category on racetime.gg.
- Python scripts in `scripts/` sync raw races into `archive/` and build JSON datasets into `site/data/`.
- `site/` is a no-build HTML/CSS/JS page, deployed to GitHub Pages.
- `README.md` is the full spec. `Badge_Guide.md` explains how to add or remove a badge. `Analyses.md` documents the standalone analyses and the rating backtest. `Racetime_API.md` lists the racetime.gg endpoints and fields the scripts read.

## The most important rules

1. **Never hand-edit generated files. Regenerate them; the tests check.**
   - `site/data/*.json`: `python3 scripts/build_stats.py`. `test_matches_a_rebuild_from_the_archive` rebuilds them from `archive/` and fails on any difference but `generated_at`.
   - `site/avatars/`: `python3 scripts/avatar_mirror.py`, after `build_stats.py`. It downloads racetime.gg avatars and copies Pokémon sprites from `assets/pokemon/`. Never add, edit, delete or hand-pick a file there.
   - `assets/pokemon/pokedex.json`: `python3 scripts/pokemon_avatars.py --refresh-pokedex`.
   - `assets/pokemon/*.png`: `python3 scripts/pokemon_images.py`, from the originals in `_tmp_assets/pokemon/`. Run `avatar_mirror.py` after.
   - The `?v=` stamps in `site/index.html` and `site/version.json`: `python3 scripts/version_assets.py`, after any change to `site/` HTML, CSS or JS. A test fails if a stamp is stale.
   - `season-ratings/season-N.json`: written once by `build_stats.py --lock-seasons`. Never edit or regenerate one. Deleting a ledger relocks that season with today's rating, so do it only on purpose.
2. **A bot commits to `main` every two hours.** `update-data.yml` syncs, builds with `--lock-seasons`, mirrors avatars, and commits `archive/`, `season-ratings/`, `site/data` and `site/avatars` as `YYYY-MM-DD data`. `update-bios.yml` calls it daily with `sync-bios` and `sync-schedule`, which also run `racer_bios.py` and `schedule_archive.py`.
   - Pull before you start.
   - Conflict in `archive/`: discard your archive changes, pull, then rerun `race_archive.py`, `schedule_archive.py`, `build_stats.py` and `avatar_mirror.py`.
   - Conflict in `season-ratings/`: take upstream; the ledgers are deterministic. Build locally without `--lock-seasons`; only the bot locks.
3. **`archive/` is a verbatim mirror of the racetime.gg API, plus the alttpr.racing schedule in `archive/ladder-schedule/`.** Never edit it, and never move or copy any of it into `site/`: it holds full profiles, comments and verbatim bios with their history, and is never deployed.
4. **Publish only what the privacy contract allows** (README › Privacy). Any new published field must fit it.
   - `race-stats.json` is anonymous: no IDs, names or entrant arrays.
   - `participants.json` carries only display names, stable user IDs and mirrored avatar file names: the hash of a `racetime.gg/media/` raster URL plus its extension, or `pokemon-` plus a sprite file name. Plus `avatar_changes`, the Eastern date an avatar change (upload or bio favourite) was first seen, never the old avatar or anything else from the bio.
   - `site/avatars/` holds exactly the files `participants.json` names, as validated raster images.
   - `comments.json` carries comments capped at 500 characters.
   - `season-ratings/` (user IDs and season points) sits beside `archive/` and is never deployed.
   - Never publish bio text, full names, discriminators, pronouns, Twitch fields or racetime.gg score fields.
5. **Schema versions are set in Python and checked in JS. Bump both together**, or the page shows "could not be loaded".
   - `participants.json`: `PARTICIPANT_SCHEMA_VERSION` in `participant_stats.py` and `leaderboard.js`.
   - `race-stats.json`: `"schema_version"` in `build_stats.py` and `validateData` in `app.js`.
6. **The README is the spec.** Update it in the same change as any behaviour change. Badges also follow the checklist in `Badge_Guide.md`.
7. **No third-party dependencies, no build step.** Python is stdlib-only, 3.11+. The site is plain files. ImageMagick 7 is needed only by `badge_images.py` and `pokemon_images.py`.
8. **Ladder rating invariants are tested across the whole archive**, for the overall, every mode, and every season and season-mode track:
   - A winner's points never fall. Finish times never affect the rating.
   - Mode and season ratings use the overall parameters, untuned; every racer debuts at 1000 in every season. A locked season's ledger wins over what the rating computes today.
   - Predictions (win chance, Upset, God Route, Big Routing, Upsetti) always use the overall rating.
   - `β`, `σ₀` and `α` were tuned by `rating_lab.py` on races before 2026-01-01. Never retune on 2026 data. Evaluate with `python3 scripts/ladder_rating.py --backtest`.
9. **All dates, hours and day buckets are `America/New_York`.** The `ladder_test` mode is excluded from everything.
10. **Frontend scripts share one global scope.** No script may touch another script's globals while it loads (see below).

## Data pipeline

```bash
python3 scripts/race_archive.py     # network: incremental sync into archive/
python3 scripts/racer_bios.py       # network, daily in CI: bios into archive/racer-bios.json
python3 scripts/schedule_archive.py # network, daily in CI: archive/ladder-schedule/; the build needs it
python3 scripts/build_stats.py      # offline: archive/ → site/data/{race-stats,participants,comments}.json
python3 scripts/avatar_mirror.py    # network: site/avatars/ ⇄ the avatars participants.json names
```

Where the logic lives:
- `participant_stats.py`: the per-racer dataset, including `avatar_changes`.
- `ladder_rating.py`: overall, per-mode and per-season (`rate_by_season`) ratings. `season_ratings.py`: season membership, lock conditions and ledger files.
- `highlights.py`: tags, badges, streaks, presets, records and facts in one chronological pass; thresholds are constants at the top.
- `comment_stats.py`: comments and the chest-count parser.
- `avatar_mirror.py`: avatar file names, the current observation, upload change times, and the mirror.
- `pokemon_avatars.py`: bio phrase and name match, favourite and random assignment, favourite change times, sprite paths, pokédex refresh.
- `racer_bios.py`: the bio sync, bio history and loader.
- `schedule_archive.py`: the schedule mirror, loaders, the slot-to-race join (shared by the build and the planner), and the season rule (`season_starts`, `season_of`).
- `build_stats.py`: race aggregates and schedule metrics and bands.
- `race_archive.py` also holds the shared atomic writers, `write_json_atomic` (minified JSON) and `write_bytes_atomic`. Reuse them for archive and dataset files. Ledgers use `season_ratings.write_ledger`, which refuses to replace a file.
- Standalone analyses the build never runs: `mode_overlap.py`, `rating_lab.py` (tunes on pre-2026 races only), `schedule_planner.py` (reads `schedule-plan.json` and the schedule mirror) and `schedule_lab.py` (needs worker processes; `--jobs 1` in a sandbox).

A new race mode needs a colour slot in `MODE_COLOR_SLOTS` (`site/racer-charts.js`), or it falls back to grey. Don't reorder `--mode-1..8`: the order keeps neighbouring violins distinguishable for colour-blind viewers.

## Tests

- `python3 -m unittest discover -s tests`: the production suite, a few seconds. `pages.yml` runs it and gates deploys.
- `python3 -m unittest discover -s tests/analyses`: the analysis suite, about 25 s. `analysis-tests.yml` runs it on changes to `scripts/` or `tests/analyses/`. `tests/analyses/` has no `__init__.py`, so the first command skips it.
- Tests never use the network; API responses are mocked. After changing a build script, rebuild and commit `site/data` with the change.

## Frontend scripts (`site/*.js`)

Classic `<script defer>` files sharing one global scope, loaded in `index.html` order.

**Load order and module shape**
- Use another script's globals only inside functions that run later (render, init, handlers), never at a file's or an IIFE body's top level. If `const X = (() => …)()` throws, even `typeof X` then throws, and the `typeof Feed`/`Records`/`RacerExtras` checks in `leaderboard.js` would take down `loadParticipants`.
- A feature with an API is `const Name = (() => { …; return { … }; })();` (`UI`, `Feed`, `TopRated`, `Records`, `RacerExtras`, `EasterEggs`, `Store`, `Following`). A self-running script is a bare `(() => { … })();` (`version-check.js`, `visitor-counter.js`).
- Declare at top level only helpers meant to be shared. `tests/test_site_globals.py` fails if two scripts declare the same top-level name.

**Shared helpers: reuse, don't rebuild**
- `app.js` is the base layer: `svgElement`, `mountChart`, `bindTooltip`/`showTooltip`/`hideTooltip`, and formatters such as `formatModeName`.
- `leaderboard.js` owns data and routing: `participants`, `playerHash`, `modeLabel`, `formatDuration`, `formatRaceDate`.
  - `ratingCategories()` is the one definition of a rating category (overall plus every mode but `unknown`), used by the top-rated banner and the featured racer.
  - `currentSeason()` returns the newest season or null. A season is a number; `null` means all time. `ratingFor`, `ratingRanks`, `ratingRankDeltas` and the other rank helpers take an optional `season`, so callers passing nothing stay all-time. Predictions (`hasRating`, `winChance`), the racer page's rating strip and chart, and the player card never pass one.
  - ▲/▼ is for rank movement only. A points change shows as +N, −N or ±0.
- `ui.js` exposes `UI`: `avatar`, `monogramLetter`, `monogramTint`, `speech`, `tag`, `modeDot`, `badge`, `badgePanel`, `modalPanel`, `dialogBox`, `time`, `comments`.
- The private `element(tag, className, text)` copies in `ui.js`, `feed.js`, `records.js`, `racer-extras.js` and `share-card.js` are deliberate: sharing one would need a load-time dependency or about 140 renamed call sites.

**Modals and events**
- Modals use `holdModal(close)` in `ui.js`: it makes `main` and `footer` inert, closes on Escape and restores focus. A new modal box starts from `UI.modalPanel({ className, labelledBy, onClose })`, which returns `{ panel, close }`.
- Close a modal in exactly this order: remove its nodes, clear `openDialog`, call `release()` (lifts `inert`, then restores focus), call `onClose`. Focus can't move into an inert page, and an `onClose` that opens a dialog must find `openDialog` cleared.
- Non-modal toasts never touch `inert`. Custom event names are constants, such as `FOLLOWING_EVENT` in `store.js`.

**Data and markup**
- Mode labels (`modeLabel`, `formatModeName`) are display only. Lookups take the raw mode id: `modeColor`, `participants.modes`, hash routes.
- No `innerHTML`: build the DOM with `element`/`svgElement` and `textContent`, because names and comments are user data.
- Values JS mirrors from CSS carry a "must match" comment (`BADGE_TILE_*`, `MONOGRAM_TINTS` in `ui.js`). Change both sides together.

## CSS (`site/*.css`)

- Tokens live on `:root` in `styles.css`: colours, data roles (`--series`, `--mode-*`, `--band-*`, `--tag-*`), the type scale `--text-*`, `--shadow`, `--scrim`, and the overlay stack, lowest first: `--layer-backdrop`, `--layer-dialog`, `--layer-effects`, `--layer-tooltip`. Prefer tokens over literals, and name the token, not a number, when a comment mentions a layer.
- `--radius` is for surfaces and controls only. Avatars, the category artwork and the retro `.ui-dialog` keep a literal `4px` on purpose.
- `--mode-*` means modes, `--band-*` means schedule bands (Primetime heatmap only), and each kind in `TAG_KINDS` (`ui.js`) has one `--tag-*` token. A new tag kind needs its own token, checked for colour-blind separation. `--tag-record` and `--tag-preset-record` alias `--tag-pb` on purpose; the record shine in `ui.css` sets them apart.
- `[hidden]` is globally `display: none !important`; don't add per-component `[hidden]` rules. `a` and `summary` get a global `:focus-visible` ring, and every new interactive element needs a visible focus state. Reduced motion is handled at the end of `styles.css`.
- Desktop-first, but usable at phone width. Reuse the `620px` and `980px` breakpoints. A component's stylesheet loads after `styles.css`, so put its mobile overrides in that file.
- `styles.css` is organised by `/* Section */` comments. Add rules to the matching section and never reorder existing ones: the cascade depends on order.

## Before handing work back

```bash
for f in site/*.js; do node --check "$f"; done    # if JS changed
python3 scripts/build_stats.py                    # if scripts/ changed
python3 scripts/version_assets.py                 # if site/ HTML, CSS or JS changed
python3 -m unittest discover -s tests             # always
python3 -m unittest discover -s tests/analyses    # if scripts/ or tests/analyses/ changed
python3 -m http.server 8000 -d site               # the page needs HTTP, not file://
```

Visual checks with headless Chrome: Chrome won't go below a window about 500px wide, so test phone widths with a 390px iframe in a wrapper page. Tall windows inflate `vh` containers (the leaderboard is `max-height: 70vh`). In a sandboxed session, headless Chrome must run outside the sandbox.
