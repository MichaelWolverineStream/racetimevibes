---
name: badges
description: 'Checklist to add, remove, rename or change a racer badge in this repo: the BADGES row in scripts/highlights.py, its threshold constant, its art made by badge_images.py, its tests and its docs row. Use when a task touches a badge, a badge threshold, label, description, id or art.'
---

# Badges

Each badge is one row in `BADGES` in `scripts/highlights.py` plus two images. The page has no badge-specific code: it renders whatever `participants.json` lists, and counts holder shares itself (`buildIndexes` in `site/leaderboard.js`).

| What | Where |
|---|---|
| Badge table, also the order of locked badges on a racer page | `BADGES` in `scripts/highlights.py` |
| Thresholds | Constants at the top of `highlights.py` |
| Per-racer counts | `_Player`, updated in the per-entry loop of `compute()` |
| One racer's result in one race | `_Moment`, built just before the badge loop in `compute()` |
| Art | `site/assets/badges/<id>.webp` and `<id>-still.webp`, made by `scripts/badge_images.py` (ImageMagick 7) |
| Tests | `BadgeTests` in `tests/test_highlights.py`, `tests/test_badge_assets.py`, `tests/test_participant_stats.py` |
| Docs | The table under [Badges](../../../docs/highlights.md#badges) in `docs/highlights.md` |

**Two kinds.** A row has exactly one rule. A **counter** badge has `progress=`, which takes a `_Player` and returns the best value so far; it unlocks at `target`, and a target above 1 shows a progress border until then. A **one-race** badge has `earned=`, which takes a `_Moment` and returns `True` when that result earns it.

Badges are recomputed from the whole archive on every build and never taken away, so a new badge goes to every racer who already qualifies. "Latest badges" lists the 12 newest unlocks, so old unlocks of a new badge show there only if they are among them.

## Add a badge

1. **Art:** `python3 scripts/badge_images.py path/to/art.webp my_badge [--pixel-art] [--still-frame N]`
   - Point it at the original art, never at an earlier output.
   - `--pixel-art` for drawn sprites under 128 px (the script can't tell). `--still-frame` when frame 0 is blank. 128×128 WebPs that are static or loop forever are copied unchanged.
   - Check both files at 40 px, the size the page shows. Art that needs credit (Twemoji) goes in the Twemoji line under [Credits](../../../README.md#credits) in the README.
2. **Rule** in `highlights.py`: a threshold constant, then a `Badge(...)` row where it should sit among locked badges, with its description built from the constant.
   - A new per-racer count is a `_Player` field (`__slots__` and `__init__`), updated in the per-entry loop before the badge loop.
   - A fact about the whole race is a set of players computed next to `snipers` and `middle`, added to `_Moment`.
3. **A rule that names a mode or preset** adds its constant to `test_badge_rules_use_names_that_exist_in_the_data` (`tests/test_participant_stats.py`), which fails if racetime renames it.
4. **Tests** in `BadgeTests`: one just below the target and one at it, plus the id in `test_badge_table_order`. `duel()` and `solo_finishes()` use fixed times; for a time-based rule build races with `record()`, `done(player, place, seconds)` and `dnf(player)`.
5. **Rebuild and check:** `build_stats.py`, `version_assets.py`, `python3 -m unittest discover -s tests`, then look at a racer page with `python3 -m http.server 8000 -d site`.
6. **Docs:** a row in the Badges table in `docs/highlights.md`, plus a note under it if the rule needs explaining.

## Remove a badge

1. Delete its `BADGES` row, its two images, its tests, its id in `test_badge_table_order`, and its row and note in `docs/highlights.md`.
2. Delete any constant or `_Player` field only it used, with the lines in `compute()` that update it (check with `grep`).
3. If the art was Twemoji, update the README credit, and remove the footer credit in `site/index.html` once no Twemoji art remains.
4. Rebuild and test as above. `test_badge_assets.py` fails on any leftover image.

## Change a badge

- **Art:** rerun `badge_images.py` with the same id; it overwrites both files.
- **Threshold:** change the constant. The description follows; the tests and `docs/highlights.md` don't.
- **Label or description:** edit the row. The page shows labels in capitals.
- **Id:** avoid it. If you must, rename both images too. Browsers store no badge ids.

## Conventions

- **Ids:** snake_case, unique, stable once published (tested).
- **Descriptions:** one imperative sentence ending in a full stop.
- **Badger** counts the rows above it, including unlocks in the same race, so it stays last.
- **First Blood** never gets a `badge` tag, because the first-win tag covers it: the one special case in `compute()`.
- **Art standard:** 128×128 WebP on a transparent canvas; animations loop forever; the still is static, a byte copy of the main image for static art. `test_badge_assets.py` enforces it.
