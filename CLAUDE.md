# CLAUDE.md

This repo is a static dashboard for the ALTTPR Step Ladder category on racetime.gg.
- Python scripts in `scripts/` sync raw races into `archive/` and build JSON datasets into `site/data/`.
- `site/` is a no-build HTML/CSS/JS page, deployed to GitHub Pages.
- `README.md` is the full spec. `Badge_Guide.md` explains how to add or remove a badge.

## The most important rules

1. **Never hand-edit generated files. Regenerate them, and expect the tests to check.**
   - **Datasets:** `site/data/*.json` comes from `python3 scripts/build_stats.py`. `test_matches_a_rebuild_from_the_archive` rebuilds them from `archive/` and fails on any difference, ignoring `generated_at`.
   - **Asset stamps:** the `?v=` tokens in `site/index.html` and `site/version.json` come from `python3 scripts/version_assets.py`. Run it after any change to `site/` HTML, CSS or JS. A test fails if the stamp is stale.
2. **A bot commits to `main` every two hours.** `.github/workflows/update-data.yml` syncs, builds, and commits `archive/` and `site/data` as `YYYY-MM-DD data`.
   - Pull before you start.
   - If a pull conflicts in `archive/`: discard your local archive changes, pull, then rerun `race_archive.py` and `build_stats.py`.
3. **`archive/` is a verbatim mirror of the racetime.gg API.**
   - Never edit it by hand.
   - Never move or copy any of it into `site/`. It holds full user profiles and comments, and it is deliberately never deployed.
4. **Publish only what the privacy contract allows** (README › Privacy). Any new field in a published dataset must fit it.
   - `race-stats.json` is anonymous: no IDs, names or entrant arrays.
   - `participants.json` may carry only display names, stable user IDs and avatar filenames, and only from `racetime.gg/media/`.
   - `comments.json` carries comments capped at 500 characters.
   - Never publish full names, discriminators, pronouns, Twitch fields or racetime.gg score fields.
5. **Each dataset's schema version is set in Python and checked in JS. Bump both sides together.** The page refuses data whose version doesn't match, and shows "could not be loaded".

   | Dataset | Python | JS |
   |---|---|---|
   | `participants.json` | `PARTICIPANT_SCHEMA_VERSION` in `participant_stats.py` | `leaderboard.js` |
   | `race-stats.json` | `"schema_version": 8` in `build_stats.py` | `validateData` in `app.js` |

6. **The README is the spec. Update it in the same change as any behaviour change.** It defines every metric, tag, badge, streak, record and UI rule precisely.
   - Badges have a fixed checklist in `Badge_Guide.md`: the table, the art, the tests and the README row.
7. **No third-party dependencies and no build step.**
   - Python is stdlib-only, version 3.11 or newer.
   - The site is plain files.
   - ImageMagick 7 is needed only by `scripts/badge_images.py`.
8. **Ladder rating invariants are tested across the whole archive:**
   - A winner's points never fall.
   - Finish times never affect the rating.
   - `β` and `σ₀` were tuned on races before 2026-01-01, so that 2026 results stay out of sample. Do not retune on 2026 data. Use `python3 scripts/ladder_rating.py --backtest` to evaluate.
9. **All dates, hours and day buckets are in `America/New_York` (Eastern Time).** The `ladder_test` mode is excluded from everything.
10. **Frontend scripts share one global scope.** No script may touch another script's globals while it loads; see below.

## Data pipeline

```bash
python3 scripts/race_archive.py    # network: incremental sync into archive/
python3 scripts/build_stats.py     # offline: archive/ → site/data/{race-stats,participants,comments}.json
```

- **Where the logic lives:**
  - `participant_stats.py`: the per-racer dataset.
  - `ladder_rating.py`: the rating.
  - `highlights.py`: tags, badges, streaks, presets, records and facts, all in one chronological pass. Thresholds are constants at the top of the module.
  - `comment_stats.py`: comments and the chest-count parser.
- `mode_overlap.py` is a standalone analysis. The build never runs it.
- **Tests never use the network**, because API responses are mocked. After changing any build script, rebuild the data and include `site/data` in the change.
- **A new race mode needs a colour slot** in `MODE_COLOR_SLOTS` (`site/racer-charts.js`). Without one it falls back to a muted grey. The order of `--mode-1..8` keeps neighbouring violins distinguishable for colour-blind viewers, so don't reorder them.

## Frontend scripts (`site/*.js`)

The scripts are classic `<script defer>` files that share one global scope. They load in the order given in `index.html`.

### Load order and module shape

- **Use another script's globals only inside functions that run later**: render, init or event handlers. Never use them at a file's top level, or at the top level of an IIFE body.
  - Why: if `const X = (() => …)()` throws, `X` can never be read again. Even `typeof X !== "undefined"` then throws. The `typeof Feed`, `typeof Records` and `typeof RacerExtras` checks in `leaderboard.js` would take down `loadParticipants`.
- **Module shape:**
  - A feature with an API is `const Name = (() => { …; return { … }; })();`. Examples: `UI`, `Feed`, `Records`, `RacerExtras`, `EasterEggs`, `Store`, `Following`.
  - A self-running script is a bare `(() => { … })();`. Examples: `version-check.js`, `visitor-counter.js`.
  - Declare at the top level only helpers meant to be shared.
  - `tests/test_site_globals.py` fails if two scripts declare the same top-level name.

### Shared helpers: reuse, don't rebuild

- **`app.js`** is the base layer: `svgElement`, `mountChart`, `bindTooltip`/`showTooltip`/`hideTooltip`, and formatters such as `formatModeName`.
- **`leaderboard.js`** owns the data and routing: `participants`, `playerHash`, `modeLabel`, `formatDuration`, `formatRaceDate`.
- **`ui.js`** exposes `UI`: `avatar`, `speech`, `tag`, `modeDot`, `badge`, `badgePanel`, `dialogBox`, `time`, `comments`.
- **The private `element(tag, className, text)` copies** in `ui.js`, `feed.js`, `records.js` and `racer-extras.js` are deliberate. Sharing one would need either a dependency at load time or renaming about 140 call sites.

### Modals and events

- **Modals use `holdModal(close)` in `ui.js`.** It makes `main` and `footer` inert, closes on Escape, and restores focus.
- **Close a modal in exactly this order:**
  1. Remove its nodes.
  2. Clear `openDialog`.
  3. Call `release()`, which lifts `inert` and then restores focus.
  4. Call `onClose`.

  Focus can't move into an inert page. An `onClose` that opens a new dialog must find `openDialog` already cleared.
- **Non-modal toasts never touch `inert`.**
- **Custom event names are constants**, such as `FOLLOWING_EVENT` in `store.js`.

### Data and markup

- **Mode labels are for display only.** `modeLabel` and `formatModeName` produce text such as "Entrance". Lookups always take the raw mode id: `modeColor`, `participants.modes` and hash routes.
- **No `innerHTML`.** Build the DOM with `element`/`svgElement` and `textContent`, because racer names and comments are user data.
- **Values that JS mirrors from CSS carry a "must match" comment**, for example `BADGE_TILE_*` and `MONOGRAM_TINTS` in `ui.js`. Change both sides together.

## CSS (`site/*.css`)

### Tokens

Tokens live on `:root` in `styles.css`:
- colours
- data roles: `--series`, `--mode-*`, `--cohort-*`
- the type scale: `--text-*`
- `--shadow` and `--scrim`
- the overlay stack, lowest first: `--layer-backdrop`, `--layer-dialog`, `--layer-effects`, `--layer-tooltip`

Rules for using them:
- Prefer tokens over literals.
- A comment that mentions an overlay layer names the token, not a number.
- **`--radius` is only for surfaces and controls.** Avatars, the category artwork and the retro `.ui-dialog` keep a literal `4px` on purpose.
- **Mode colours mean modes.** Don't use `--mode-*` for anything else.

### Global rules

- `[hidden]` is globally `display: none !important`. Don't add `[hidden]` rules per component.
- `a` and `summary` get a global `:focus-visible` ring. Every new interactive element needs a visible focus state.
- Reduced motion is handled globally at the end of `styles.css`.

### Mobile

- **The site is desktop-first, but it must stay usable at phone width.**
- Reuse the `620px` and `980px` breakpoints.
- A component's stylesheet loads after `styles.css` and its selectors often win. Put the component's mobile overrides in that same file.

### Organisation

- `styles.css` is organised by `/* Section */` comments.
- Add new rules to the matching section.
- Never reorder existing rules: the cascade depends on their order.

## Before handing work back

```bash
for f in site/*.js; do node --check "$f"; done   # if JS changed
python3 scripts/build_stats.py                   # if scripts/ changed
python3 scripts/version_assets.py                # if site/ HTML, CSS or JS changed
python3 -m unittest discover -s tests
python3 -m http.server 8000 -d site              # the page needs HTTP, not file://
```

Visual checks with headless Chrome:
- **Phone widths:** Chrome won't go below a window about 500px wide. Use a 390px iframe in a wrapper page instead.
- **Window height:** tall windows inflate `vh` containers. The leaderboard is `max-height: 70vh`.
- **Sandbox:** in a sandboxed Claude Code session, headless Chrome must run outside the sandbox.
