# CLAUDE.md

A static dashboard for the ALTTPR Step Ladder category on racetime.gg.
- Python scripts in `scripts/` sync raw races into `archive/` and build JSON datasets into `site/data/`.
- `site/` is a no-build HTML/CSS/JS page, deployed to GitHub Pages.
- `README.md` (setup, ladder rating, privacy) and `docs/` are the spec: see the [Docs map](#docs-map). Area rules in `.claude/rules/` load with the files they cover, and the `badges` skill is the badge checklist.

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
4. **Publish only what the privacy contract allows** ([README › Privacy](README.md#privacy)). Any new published field must fit it.
   - `race-stats.json` is anonymous: no IDs, names or entrant arrays.
   - `participants.json` carries only display names, stable user IDs and mirrored avatar file names: the hash of a `racetime.gg/media/` raster URL plus its extension, or `pokemon-` plus a sprite file name. Plus `avatar_changes`, the Eastern date an avatar change (upload or bio favourite) was first seen, never the old avatar or anything else from the bio.
   - `site/avatars/` holds exactly the files `participants.json` names, as validated raster images.
   - `comments.json` carries comments capped at 500 characters.
   - `season-ratings/` (user IDs and season points) sits beside `archive/` and is never deployed.
   - Never publish bio text, full names, discriminators, pronouns, Twitch fields or racetime.gg score fields.
5. **Schema versions are set in Python and checked in JS. Bump both together**, or the page shows "could not be loaded".
   - `participants.json`: `PARTICIPANT_SCHEMA_VERSION` in `participant_stats.py` and `leaderboard.js`.
   - `race-stats.json`: `"schema_version"` in `build_stats.py` and `validateData` in `app.js`.
6. **The README and `docs/` are the spec.** Update the matching doc in the same change as any behaviour change. Badges follow the `badges` skill (`.claude/skills/badges/SKILL.md`).
7. **No third-party dependencies, no build step.** Python is stdlib-only, 3.11+. The site is plain files. ImageMagick 7 is needed only by `badge_images.py` and `pokemon_images.py`.
8. **Ladder rating invariants are tested across the whole archive**, for the overall, every mode, and every season and season-mode track:
   - A winner's points never fall. Finish times never affect the rating.
   - Mode and season ratings use the overall parameters, untuned; every racer debuts at 1000 in every season. A locked season's ledger wins over what the rating computes today.
   - Predictions (win chance, Upset, God Route, Big Routing, Upsetti) always use the overall rating.
   - `β`, `σ₀` and `α` were tuned by `rating_lab.py` on races before 2026-01-01. Never retune on 2026 data. Evaluate with `python3 scripts/ladder_rating.py --backtest`.
9. **All dates, hours and day buckets are `America/New_York`.** The `ladder_test` mode is excluded from everything.
10. **Frontend scripts share one global scope.** No script may touch another script's globals while it loads (see `.claude/rules/site.md`).

## Docs map

| Doc | Covers | Code |
|---|---|---|
| `README.md` | Setup, ladder rating (overall, mode, season, backtest), privacy | `ladder_rating.py` |
| `docs/definitions.md` | Included race, Eastern Time, finisher, winner, slot, season | `schedule_archive.py` (join, seasons) |
| `docs/archive.md` | Archive files, sync, names, avatar observations, bios, schedule mirror | `race_archive.py`, `racer_bios.py`, `schedule_archive.py` |
| `docs/racetime-api.md` | racetime.gg endpoints and fields read | `race_archive.py`, `racer_bios.py` |
| `docs/datasets.md` | `site/data` files, schedule metrics, season locking and ledgers, chest counts | `build_stats.py`, `participant_stats.py`, `season_ratings.py`, `season_names.py`, `comment_stats.py` |
| `docs/highlights.md` | Tags, badges, streaks, presets, records, claims, facts | `highlights.py`, `badge_images.py` |
| `docs/avatars.md` | Avatar mirror, Pokémon avatars, sprites, avatar changes | `avatar_mirror.py`, `pokemon_avatars.py`, `pokemon_images.py` |
| `docs/dashboard.md` | Everything the page shows and does | `site/` |
| `docs/operations.md` | Refresh steps and options, asset versioning, tests, workflows | `.github/workflows/`, `version_assets.py` |
| `docs/analyses.md` | Mode overlap, schedule planner and lab, rating lab | `mode_overlap.py`, `schedule_planner.py`, `schedule_lab.py`, `rating_lab.py` |

Shared code:
- `race_archive.py` holds the atomic writers, `write_json_atomic` (minified JSON) and `write_bytes_atomic`. Reuse them for archive and dataset files. Ledgers use `season_ratings.write_ledger`, which refuses to replace a file.
- Standalone analyses the build never runs: `mode_overlap.py`, `rating_lab.py` (tunes on pre-2026 races only), `schedule_planner.py` (reads `schedule-plan.json` and the schedule mirror) and `schedule_lab.py` (needs worker processes; `--jobs 1` in a sandbox).

## Data pipeline

```bash
python3 scripts/race_archive.py     # network: incremental sync into archive/
python3 scripts/racer_bios.py       # network, daily in CI: bios into archive/racer-bios.json
python3 scripts/schedule_archive.py # network, daily in CI: archive/ladder-schedule/; the build needs it
python3 scripts/build_stats.py      # offline: archive/ → site/data/{race-stats,participants,comments}.json
python3 scripts/avatar_mirror.py    # network: site/avatars/ ⇄ the avatars participants.json names
```

## Tests

- `python3 -m unittest discover -s tests`: the production suite, a few seconds. `pages.yml` runs it and gates deploys.
- `python3 -m unittest discover -s tests/analyses`: the analysis suite, about half a minute. `analysis-tests.yml` runs it on pushes and pull requests that change `scripts/`, `tests/analyses/` or `schedule-plan.json`, weekly and on demand; it never gates deploys. `tests/analyses/` has no `__init__.py`, so the first command skips it.
- Tests never use the network; API responses are mocked. After changing a build script, rebuild and commit `site/data` with the change.

## Before handing work back

```bash
for f in site/*.js; do node --check "$f"; done    # if JS changed
python3 scripts/build_stats.py                    # if scripts/ changed
python3 scripts/version_assets.py                 # if site/ HTML, CSS or JS changed
python3 -m unittest discover -s tests             # always
python3 -m unittest discover -s tests/analyses    # if scripts/ or tests/analyses/ changed
python3 -m http.server 8000 -d site               # the page needs HTTP, not file://
```

Then update the doc the Docs map names for any behaviour you changed. Visual checks of the page are in `.claude/rules/site.md`.
