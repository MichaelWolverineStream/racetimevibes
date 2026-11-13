---
paths:
  - "site/**"
---

# Site: frontend scripts and CSS

What the page shows and does is specified in `docs/dashboard.md`. Update it in the same change as any visible behaviour change, then run `python3 scripts/version_assets.py`.

## Frontend scripts (`site/*.js`)

Classic `<script defer>` files sharing one global scope, loaded in `index.html` order. The one exception is `theme.js`: it loads blocking in `<head>`, before the stylesheets, so the theme is set before the first paint. `Store` doesn't exist yet at that point, so `theme.js` reads its own storage key, whose prefix must match `STORAGE_PREFIX`. It declares no globals.

**Load order and module shape**
- Use another script's globals only inside functions that run later (render, init, handlers), never at a file's or an IIFE body's top level. If `const X = (() => …)()` throws, even `typeof X` then throws, and the `typeof Feed`/`Records`/`RacerExtras`/`FaceOff` checks in `leaderboard.js` would take down `loadParticipants`.
- A feature with an API is `const Name = (() => { …; return { … }; })();` (`UI`, `Feed`, `TopRated`, `Records`, `RacerExtras`, `FaceOff`, `EasterEggs`, `Store`, `Following`). A self-running script is a bare `(() => { … })();` (`version-check.js`, `visitor-counter.js`).
- Declare at top level only helpers meant to be shared. `tests/test_site_globals.py` fails if two scripts declare the same top-level name.

**Shared helpers: reuse, don't rebuild**
- `app.js` is the base layer: `svgElement`, `mountChart`, `bindTooltip`/`showTooltip`/`hideTooltip`, the touch helpers `isTouchPointer`, `tapEchoing` and `showTouchTooltip`, and formatters such as `formatModeName`.
- `leaderboard.js` owns data and routing: `participants`, `playerHash`, `modeLabel`, `formatDuration`, `formatRaceDate`.
  - **Routes and views:** it owns the route formats (`playerHash`, `faceoffHash`, `parseRoute`) and the views (`showPlayer`, `showFaceOff`, `showLeaderboard`). Every other view calls `leaveFaceOff`. A feature module such as `FaceOff` is called by the view, and changes its pair by replacing the hash, so the router stays the one path that renders.
  - `renderSortableTable` treats a column without `sortValue` as an unsortable action column: its header is a plain label.
  - `ratingCategories()` is the one definition of a rating category (overall plus every mode but `unknown`), used by the top-rated banner and the featured racer.
  - `currentSeason()` returns the newest season or null. A season is a number; `null` means all time. `ratingFor`, `ratingRanks`, `ratingRankDeltas` and the other rank helpers take an optional `season`, so callers passing nothing stay all-time. Predictions (`hasRating`, `winChance`), the racer page's rating strip and chart, and the player card never pass one.
  - ▲/▼ is for rank movement only. A points change shows as +N, −N or ±0.
- `ui.js` exposes `UI`: `avatar`, `monogramLetter`, `monogramTint`, `speech`, `tag`, `modeDot`, `badge`, `badgePanel`, `modalPanel`, `dialogBox`, `time`, `comments`.
- The private `element(tag, className, text)` copies in `ui.js`, `feed.js`, `records.js`, `racer-extras.js`, `share-card.js` and `faceoff.js` are deliberate: sharing one would need a load-time dependency or about 140 renamed call sites.

**Modals and events**
- Modals use `holdModal(close)` in `ui.js`: it makes `main` and `footer` inert, closes on Escape and restores focus. A new modal box starts from `UI.modalPanel({ className, labelledBy, onClose })`, which returns `{ panel, close }`.
- Close a modal in exactly this order: remove its nodes, clear `openDialog`, call `release()` (lifts `inert`, then restores focus), call `onClose`. Focus can't move into an inert page, and an `onClose` that opens a dialog must find `openDialog` cleared.
- Non-modal toasts never touch `inert`. Custom event names are constants, such as `FOLLOWING_EVENT` in `store.js`.

**Tooltips and touch**
- Information goes in a tooltip through `bindTooltip`, never in `title=`, which never shows on a touch screen.
- `bindTooltip` handles touch itself:
  - A tap toggles the tooltip. A link, button or summary keeps its action instead.
  - For `TAP_ECHO_MS` after a touch, the mouse and focus handlers ignore the events the browser replays for the tap (`tapEchoing()`).
- A chart with its own pointer handling:
  - shows touch readouts with `showTouchTooltip(target, text, chart)`, so a touch outside the chart, or a scroll, closes them;
  - gates its mouse and focus handlers with `tapEchoing()`.
- Touch code paths act only when `isTouchPointer(event)`, so mouse behaviour stays exactly as it was.

**Data and markup**
- Mode labels (`modeLabel`, `formatModeName`) are display only. Lookups take the raw mode id: `modeColor`, `participants.modes`, hash routes.
- A new race mode needs a colour slot in `MODE_COLOR_SLOTS` (`racer-charts.js`), or it falls back to grey. Don't reorder `--mode-1..8`: the order keeps neighbouring violins distinguishable for colour-blind viewers.
- No `innerHTML`: build the DOM with `element`/`svgElement` and `textContent`, because names and comments are user data.
- Values JS mirrors from CSS carry a "must match" comment (`BADGE_TILE_*`, `MONOGRAM_TINTS` in `ui.js`). Change both sides together.

## CSS (`site/*.css`)

- Tokens live on `:root` in `styles.css`: colours, data roles (`--series`, `--mode-*`, `--band-*`, `--tag-*`, `--corner-a`/`--corner-b`), the type scale `--text-*`, `--shadow`, `--scrim`, and the overlay stack, lowest first: `--layer-backdrop`, `--layer-dialog`, `--layer-effects`, `--layer-tooltip`. Prefer tokens over literals, and name the token, not a number, when a comment mentions a layer.
- **Dark theme.** `theme.js` sets `data-theme` on `<html>`, and the `/* Dark theme */` section after `:root` redeclares every `:root` token whose value holds a literal colour, repeating any value that stays the same. Aliases follow on their own. `tests/test_site_theme.py` fails on a token missing from the dark block and checks the contrast pairs in both themes.
  - Pick dark values by role, not by inverting:
    - Large surfaces stay deep (`--brand-fill` with `--on-brand` for the nav band).
    - Small fills go light with dark `--on-fill` text.
    - Strong borders use `--rule`, never `--ink`.
    - Text on medal fills uses `--on-medal`.
    - The tooltip uses `--tooltip-*`.
    - Shadows and sheens mix `--shadow-color` and `--shine-color` with their own alpha.
  - A dark mode or tag hue keeps its light hue and lightness order. It needs 3:1 against `--panel`, and its colour-blind separation from the others must stay at least that of the light set. Check both before changing one.
  - The LCD hit counter, the Zelda dialog (`--zelda-*`), the easter-egg glow and the nav band's white sheen are the same in both themes on purpose.
  - `theme.js` mirrors `--brand-fill` for the browser toolbar and spells out `STORAGE_PREFIX`. `tests/test_site_theme.py` checks both.
- `--corner-a` and `--corner-b` are Face Off's red and blue corner data role, with their own values, so a corner never reads as a mode. They colour fills, rings and bars, never text, and `tests/test_site_theme.py` checks both at 3:1 against the panel. `faceoff.css` sets `--corner` and `--lean` per side, and its rules use only those two.
- `--radius` is for surfaces and controls only. Avatars, the category artwork and the retro `.ui-dialog` keep a literal `4px` on purpose.
- `--mode-*` means modes, `--band-*` means schedule bands (Primetime heatmap only), and each kind in `TAG_KINDS` (`ui.js`) has one `--tag-*` token. A new tag kind needs its own token, checked for colour-blind separation. `--tag-record` and `--tag-preset-record` alias `--tag-pb` on purpose; the record shine in `ui.css` sets them apart. `ui-tag--rare` and `ui-tag--legendary` are badge rarity modifiers that `UI.tag` adds from `BADGE_RARITIES` (`ui.js`), not tag kinds, so they need no token.
- `[hidden]` is globally `display: none !important`; don't add per-component `[hidden]` rules. `a` and `summary` get a global `:focus-visible` ring, and every new interactive element needs a visible focus state. Reduced motion is handled at the end of `styles.css`.
- Desktop-first, and good on phones. Desktop (1024px and up, with a mouse) must not change when you work on phones.
  - Narrow-screen layout goes in the `980px` and `620px` blocks (the feed also has `@container feed` tiers).
  - Input-method changes go in `@media (hover: none)` or `(pointer: coarse)`: showing what hover reveals, bigger hit areas, scroll containment.
  - A component's stylesheet loads after `styles.css`, so put its mobile overrides in that file.
- A viewport-height cap writes `svh` on the line after its `vh` fallback, so it doesn't jump as a phone's address bar slides away.
- A sideways scroller at 980px and below uses the scroll-shadow backgrounds in `styles.css` (`.table-wrap--leaderboard`, `.mode-toggle`), with `--scroll-cue` as the shadow. The cover gradient must match the ground behind the container.
- A table with a sticky column uses `border-collapse: separate`. A collapsed border is painted by the table, so a sticky cell's background can cover it.
- `styles.css` is organised by `/* Section */` comments. Add rules to the matching section and never reorder existing ones: the cascade depends on order.

## Checking the page

- `for f in site/*.js; do node --check "$f"; done`, then `python3 -m http.server 8000 -d site`: the page needs HTTP, not `file://`.
- Visual checks with headless Chrome: Chrome won't go below a window about 500px wide, so test phone widths with a 390px iframe in a wrapper page. Tall windows inflate `vh` containers (the leaderboard is `max-height: 70vh`). In a sandboxed session, headless Chrome must run outside the sandbox.
