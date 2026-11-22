---
paths:
  - "site/*.html"
  - "site/*.js"
  - "site/*.css"
  - "tests/test_site_*.py"
---

# Site: frontend scripts and CSS

What the page shows and does is specified in `docs/dashboard.md`. Update it in the same change as any visible behaviour change.

## Frontend scripts (`site/*.js`)

Classic `<script defer>` files sharing one global scope, loaded in `index.html` order. The one exception is `theme.js`: it loads blocking in `<head>`, before the stylesheets, so the theme is set before the first paint. `Store` doesn't exist yet at that point, so `theme.js` reads its own storage key, whose prefix must match `STORAGE_PREFIX`. It declares no globals.

**Load order and module shape**
- Use another script's globals only inside functions that run later (render, init, handlers), never at a file's or an IIFE body's top level. If `const X = (() => …)()` throws, even `typeof X` then throws, and every `typeof` guard in `leaderboard.js` that names `X` would take the page down with it (for example `typeof Feed` in `loadParticipants`).
- A feature with an API is `const Name = (() => { …; return { … }; })();` (`UI`, `Feed`, `TopRated`, `Upcoming`, `PoolRoom`, `Records`, `RacerExtras`, `ShareCard`, `FaceOff`, `EasterEggs`, `Store`, `Following`, `Me`). A self-running script is a bare `(() => { … })();` (`version-check.js`, `visitor-counter.js`).
- Declare at top level only helpers meant to be shared. `tests/test_site_globals.py` fails if two scripts declare the same top-level name.

**Shared helpers: reuse, don't rebuild**
- `app.js` is the base layer: `svgElement`, `mountChart`, `bindTooltip`/`showTooltip`/`hideTooltip`, the touch helpers `isTouchPointer`, `tapEchoing` and `showTouchTooltip`, and formatters such as `formatModeName`.
- `leaderboard.js` owns data and routing: `participants`, `playerHash`, `modeLabel`, `formatDuration`, `formatRaceDate`, `meIndex()` (the viewer's player index or null; an id the data lacks counts as nobody).
  - **Viewer time zone:** start times shown to the viewer (Upcoming races, Latest results rows and their date headings) go through `viewerTimeZone()` and `viewerDateHour(seconds)`. Everything else stays in the data's timezone (`todayInZone`, `race.d`).
  - **Routes and views:** it owns the route formats (`playerHash`, `faceoffHash`, `parseRoute`) and the views (`showPlayer`, `showFaceOff`, `showLeaderboard`). Every other view calls `leaveFaceOff`. A feature module such as `FaceOff` is called by the view, and changes its pair by replacing the hash, so the router stays the one path that renders.
  - **The board without a hash:** `openBoard` is the one way there, for the masthead's mark and Face Off's ← Back when it has nothing to step back to. A link to `./` would reload the page, so it pushes the bare address and calls `route` itself. Never send the board to the top with a hash route or `href="#"`: Back to an empty-hash entry must keep returning to where the board was left.
  - **Back links and `history.state`:** `route` stamps each new history entry with `{ origin }`, the view it was reached from (`entryOrigin`), so the entry before it is always that view. Face Off's ← Back and a racer page's ← Face Off call `history.back()` on that basis. A forward link back would make two pages send each other back and forth. Any `replaceState` must pass `history.state` on, as the racer page's mode picker does. A Face Off pick (`location.replace`) loses the state, so `entryOrigin` restores it from `currentView.origin`.
  - `renderSortableTable` treats a column without `sortValue` as an unsortable action column: its header is a plain label.
  - `ratingCategories()` is the page's one definition of a rating category (overall plus every mode but `unknown`), used by the top-rated banner and the racer page's rating strip. `scripts/featured.py` mirrors it for the top 3s the featured racer avoids.
  - `currentSeason()` returns the newest season or null. A season is a number; `null` means all time. `ratingFor`, `ratingRanks`, `ratingRankDeltas` and the other rank helpers take an optional `season`, so callers passing nothing stay all-time. Predictions (`hasRating`, `winChance`), the racer page's rating chart and the player card never pass one. The racer page's rating strip passes the current season unless its toggle is on All time.
  - ▲/▼ is for rank movement only. A points change shows as +N, −N or ±0.
- `ui.js` exposes `UI`: `avatarUrl`, `avatar`, `monogramLetter`, `monogramTint`, `speech`, `tag`, `modeDot`, `crown`, `followStar`, `markRacers`, `syncMarks`, `nextBadge`, `isPalindromeTime`, `time`, `badge`, `badgePanel`, `badgeShare`, `badgeRarity`, `modalPanel`, `dialogBox`, `comments`, `chestPace`, `relativeDay`.
- The private `element(tag, className, text)` copies in `ui.js`, `top-rated.js`, `upcoming.js`, `feed.js`, `pool-room.js`, `records.js`, `racer-extras.js`, `share-card.js` and `faceoff.js` are deliberate: sharing one would need a load-time dependency or about 140 renamed call sites.

**Modals and events**
- Modals use `holdModal(close)` in `ui.js`: it makes `main` and `footer` inert, closes on Escape and restores focus. A new modal box starts from `UI.modalPanel({ className, labelledBy, onClose })`, which returns `{ panel, close }`.
- Close a modal in exactly this order: remove its nodes, clear `openDialog`, call `release()` (lifts `inert`, then restores focus), call `onClose`. Focus can't move into an inert page, and an `onClose` that opens a dialog must find `openDialog` cleared.
- Non-modal toasts never touch `inert`. Custom event names are constants, such as `FOLLOWING_EVENT` and `ME_EVENT` in `store.js`.

**This is me**
- Mark any node that shows racers with `UI.markRacers(node, playerIndexes)`. It sets `data-racers` and the boolean `data-me`. One `ME_EVENT` listener re-syncs every mark, so nothing re-renders for a highlight. A cached node that was detached when the event fired calls `UI.syncMarks(root)` when it is re-attached.
- Content that exists only because a racer is set (the nav avatar, the YOU chip, the You card, the recap, the versus strip, Jump to me, the violin highlight) refills its own small host in place on `ME_EVENT`, never by re-rendering a list, so pages, open rows, focus and scroll survive.
- Style a mark per component with the row recipe (wash, 3px bar, bold name) or the avatar ring.
- `renderSortableTable` takes an optional `rowRacers(row)` that marks each row; a marked row in any `.table-wrap` table gets the recipe from `styles.css`.

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
- A value mirrored between JS, CSS and Python carries a "must match" comment (`BADGE_TILE_*`, `MONOGRAM_TINTS` in `ui.js`) and a test that parses both sides (`test_site_theme.py` for JS and CSS; `test_build_stats.py`, `test_participant_stats.py`, `test_comment_stats.py`, `test_featured.py` for Python). A new mirror gets its test in the same change. Change both sides together.
- The one mirror without a test is logic, not a value: `formatModeName` (`app.js`) and `mode_label` (`scripts/highlights.py`) must turn a mode id into the same label. Change both together.

## CSS (`site/*.css`)

- Tokens live on `:root` in `styles.css`: colours, data roles (`--series`, `--ladder-series*`, `--mode-*`, `--band-*`, `--tag-*`, `--corner-a`/`--corner-b`), the type scale `--text-*`, `--shadow`, `--scrim`, and the overlay stack, lowest first: `--layer-backdrop`, `--layer-dialog`, `--layer-effects`, `--layer-tooltip`. Prefer tokens over literals, and name the token, not a number, when a comment mentions a layer.
- **Dark theme.** `theme.js` sets `data-theme` on `<html>`, and the `/* Dark theme */` section after `:root` redeclares every `:root` token whose value holds a literal colour, repeating any value that stays the same. Aliases follow on their own. `tests/test_site_theme.py` fails on a token missing from the dark block and checks the contrast pairs in both themes.
  - Pick dark values by role, not by inverting:
    - Large surfaces stay deep (`--brand-fill` with `--on-brand` for the nav band).
    - Small fills go light with dark `--on-fill` text.
    - Strong borders use `--rule`, never `--ink`.
    - Text on medal fills uses `--on-medal`.
    - The tooltip uses `--tooltip-*`.
    - Shadows and sheens mix `--shadow-color` and `--shine-color` with their own alpha.
  - The LCD hit counter, the Zelda dialog (`--zelda-*`), the easter-egg glow and the nav band's white sheen are the same in both themes on purpose.
  - `theme.js` mirrors `--brand-fill` for the browser toolbar and spells out `STORAGE_PREFIX`. `tests/test_site_theme.py` checks both.
- `--me` (alias of `--accent`), `--me-wash` and `--negative-on-me` are the "This is me" role. `--me-wash` is a literal per theme, and hover keeps it because a deeper wash fails `--muted`. `[data-me]` swaps `--negative-on-me` in for `--negative`, so red text inside a marked row keeps 4.5:1; rules that colour text with `var(--red)` directly would miss it. `tests/test_site_theme.py` pairs text on `--me-wash` and checks `--me` against `--panel` and the wash.
- `--corner-a` and `--corner-b` are Face Off's red and blue corner data role, with their own values, so a corner never reads as a mode. They colour fills, rings and bars, never text, and `tests/test_site_theme.py` checks both at 3:1 against the panel. `faceoff.css` sets `--corner` and `--lean` per side, and its rules use only those two.
- `--radius` is for surfaces and controls only. Avatars, the category artwork and the retro `.ui-dialog` keep a literal `4px` on purpose.
- `--ladder-series` and `--ladder-series-muted` mean the Ladder Data season bars, the season in progress and the two Ladder Data panels' top rules.
- `--mode-*` means modes, `--band-*` means schedule bands (Primetime heatmap only), and each kind in `TAG_KINDS` (`ui.js`) has one `--tag-*` token, except `new`, which `ui.css` fills with `--accent` (no data tag or caller uses it today). A new tag kind needs its own token, checked for colour-blind separation. `--tag-record`, `--tag-preset-record` and `--tag-preset-pb` alias `--tag-pb` on purpose; the record shine in `ui.css` and the tag labels set them apart. `ui-tag--rare` and `ui-tag--legendary` are badge rarity modifiers that `UI.tag` adds from `BADGE_RARITIES` (`ui.js`), not tag kinds, so they need no token.
- `[hidden]` is globally `display: none !important`; don't add per-component `[hidden]` rules. `a` and `summary` get a global `:focus-visible` ring, and every new interactive element needs a visible focus state. The global reduced-motion rule is at the end of `styles.css`, and component stylesheets add their own reduced-motion overrides where needed.
- Desktop-first, and good on phones. Desktop (1024px and up, with a mouse) must not change when you work on phones.
  - Narrow-screen layout goes in the `980px` and `620px` blocks (the feed also has `@container feed` tiers).
  - Input-method changes go in `@media (hover: none)` or `(pointer: coarse)`: showing what hover reveals, bigger hit areas, scroll containment.
  - A component's stylesheet loads after `styles.css`, so put its mobile overrides in that file.
- A viewport-height cap writes `svh` on the line after its `vh` fallback, so it doesn't jump as a phone's address bar slides away.
- A sideways scroller at 980px and below uses the scroll-shadow backgrounds in `styles.css` (`.table-wrap--leaderboard`, `.mode-toggle`), with `--scroll-cue` as the shadow. The cover gradient must match the ground behind the container.
- A table with a sticky column uses `border-collapse: separate`. A collapsed border is painted by the table, so a sticky cell's background can cover it.
- `styles.css` is organised by `/* Section */` comments. Add rules to the matching section and never reorder existing ones: the cascade depends on order.

## Checking the page

- Visual checks with headless Chrome: Chrome won't go below a window about 500px wide, so test phone widths with a 390px iframe in a wrapper page. Tall windows inflate `vh` containers (the leaderboard is `max-height: 70vh`). In a sandboxed session, headless Chrome must run outside the sandbox.
