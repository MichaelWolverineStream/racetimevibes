---
paths:
  - "site/**"
---

# Site: frontend scripts and CSS

What the page shows and does is specified in `docs/dashboard.md`. Update it in the same change as any visible behaviour change, then run `python3 scripts/version_assets.py`.

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
- A new race mode needs a colour slot in `MODE_COLOR_SLOTS` (`racer-charts.js`), or it falls back to grey. Don't reorder `--mode-1..8`: the order keeps neighbouring violins distinguishable for colour-blind viewers.
- No `innerHTML`: build the DOM with `element`/`svgElement` and `textContent`, because names and comments are user data.
- Values JS mirrors from CSS carry a "must match" comment (`BADGE_TILE_*`, `MONOGRAM_TINTS` in `ui.js`). Change both sides together.

## CSS (`site/*.css`)

- Tokens live on `:root` in `styles.css`: colours, data roles (`--series`, `--mode-*`, `--band-*`, `--tag-*`), the type scale `--text-*`, `--shadow`, `--scrim`, and the overlay stack, lowest first: `--layer-backdrop`, `--layer-dialog`, `--layer-effects`, `--layer-tooltip`. Prefer tokens over literals, and name the token, not a number, when a comment mentions a layer.
- `--radius` is for surfaces and controls only. Avatars, the category artwork and the retro `.ui-dialog` keep a literal `4px` on purpose.
- `--mode-*` means modes, `--band-*` means schedule bands (Primetime heatmap only), and each kind in `TAG_KINDS` (`ui.js`) has one `--tag-*` token. A new tag kind needs its own token, checked for colour-blind separation. `--tag-record` and `--tag-preset-record` alias `--tag-pb` on purpose; the record shine in `ui.css` sets them apart.
- `[hidden]` is globally `display: none !important`; don't add per-component `[hidden]` rules. `a` and `summary` get a global `:focus-visible` ring, and every new interactive element needs a visible focus state. Reduced motion is handled at the end of `styles.css`.
- Desktop-first, but usable at phone width. Reuse the `620px` and `980px` breakpoints. A component's stylesheet loads after `styles.css`, so put its mobile overrides in that file.
- `styles.css` is organised by `/* Section */` comments. Add rules to the matching section and never reorder existing ones: the cascade depends on order.

## Checking the page

- `for f in site/*.js; do node --check "$f"; done`, then `python3 -m http.server 8000 -d site`: the page needs HTTP, not `file://`.
- Visual checks with headless Chrome: Chrome won't go below a window about 500px wide, so test phone widths with a 390px iframe in a wrapper page. Tall windows inflate `vh` containers (the leaderboard is `max-height: 70vh`). In a sandboxed session, headless Chrome must run outside the sandbox.
