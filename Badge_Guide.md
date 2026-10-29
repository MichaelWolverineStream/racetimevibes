# Badge guide

How to add, remove or change a badge. Each badge is one row in the `BADGES` table in `scripts/highlights.py` plus two images. The page has no badge-specific code: it renders whatever `participants.json` lists.

## Where things live

| What | Where |
|---|---|
| Badge table (also the order of locked badges on a racer page) | `BADGES` in `scripts/highlights.py` |
| Thresholds | Constants at the top of `scripts/highlights.py` |
| Per-racer counts | `_Player`, updated in the per-entry loop of `compute()` |
| One racer's result in one race | `_Moment`, built just before the badge loop in `compute()` |
| Art | `site/assets/badges/<id>.webp` and `<id>-still.webp` |
| Art converter | `scripts/badge_images.py`, which needs ImageMagick 7 |
| Tests | `BadgeTests` in `tests/test_highlights.py`, `tests/test_badge_assets.py`, `tests/test_participant_stats.py` |
| Holder share in tooltips | Counted on the page in `buildIndexes` in `site/leaderboard.js`; nothing to update per badge |
| Docs | The Badges table in `README.md` |

## Two kinds of badge

Each `Badge` row has exactly one rule:

- **Counter badge:** `progress=` takes a `_Player` and returns the best value so far, such as a best run or a total. The badge unlocks when that value reaches `target`. Until then, a target above 1 shows a progress border.
- **One-race badge:** `earned=` takes a `_Moment` and returns `True` when that single result earns the badge.

Badges are recomputed from the whole archive on every build and are never taken away. A new badge therefore goes to every racer who already qualifies. The feed's "New badges" panel shows only unlocks from races in the last 7 days, so it doesn't fill up with old results.

## Add a badge

1. **Make the art.**

   ```sh
   python3 scripts/badge_images.py path/to/art.webp my_badge [--pixel-art] [--still-frame N]
   ```

   - Point the script at the original art, not at an earlier output.
   - Pass `--pixel-art` for drawn sprites smaller than 128 px. The script can't tell pixel art from other art, so you choose.
   - SVGs and WebPs that already meet the standard need no flags. 128×128 WebPs that are static or loop forever are copied unchanged.
   - Pass `--still-frame` when frame 0 of an animation is blank.
   - The page shows badges at 40 px, so check both files at that size.
   - If the art needs credit (Twemoji, for example), add it to the credit line under the README Badges table.
2. **Add the rule** in `scripts/highlights.py`.
   - Add a constant for the threshold, then a `Badge(...)` row where it should sit among locked badges. Build the description from the constant, as the other rows do.
   - If the rule needs a count that `_Player` doesn't keep yet, add a field to its `__slots__` and `__init__`. Update it in the per-entry loop, before the badge loop runs.
   - If the rule needs a fact about the whole race, compute a set of players next to `snipers` and `middle`, add it to `_Moment`, and pass it in.
3. **If the rule names a mode or preset,** add its constant to `test_badge_rules_use_names_that_exist_in_the_data` in `tests/test_participant_stats.py`. That test fails if racetime renames the mode or preset.
4. **Add tests** to `BadgeTests`: one just below the target and one at it. Then add the id to `test_badge_table_order`.
   - The `duel()` and `solo_finishes()` helpers use fixed finish times. For a rule that depends on times, build races with `record()`, `done(player, place, seconds)` and `dnf(player)`.
5. **Rebuild and check.**

   ```sh
   python3 scripts/build_stats.py         # rewrites site/data/*.json
   python3 scripts/version_assets.py      # changes nothing unless site CSS or JS changed
   python3 -m unittest discover -s tests
   python3 -m http.server 8000 -d site    # open a racer page at http://localhost:8000
   ```

6. **Add a row to the README** Badges table, plus a note if the rule needs explaining. To get the "Held by" share, divide the badge's count in `diagnostics.highlights.badge_holders` by `diagnostics.highlights.racer_count`, both in `site/data/participants.json`.

## Remove a badge

1. Delete its `BADGES` row and its two images.
2. Delete its tests, its id in `test_badge_table_order`, its README row, and its note under the README table, if it has one.
3. Delete any constant or `_Player` field that only it used, along with the lines in `compute()` that update that field. Check with `grep`.
4. If the art was Twemoji, update the README credit. Remove the footer credit in `site/index.html` once no Twemoji art remains.
5. Rebuild and run the tests as above. `test_badge_assets.py` fails on any leftover image.

## Change a badge

- **Art:** re-run `badge_images.py` with the same id; it overwrites both files.
- **Threshold:** change the constant. The description follows, but the tests and the README don't.
- **Label or description:** edit the row. The page shows labels in capitals.
- **Id:** avoid changing it. If you must, rename both images too. Browsers store no badge ids, so only the rebuilt data changes.

## Conventions

- **Ids:** snake_case, kept stable once published. A test checks the format and that ids are unique.
- **Descriptions:** one imperative sentence ending with a full stop.
- **First Blood:** it never gets a `badge` tag, because the first-win tag already covers it. This is the one special case in `compute()`.
- **Art standard:** 128×128 WebP on a transparent canvas. Animations loop forever. The still is static, and for static art it is a byte copy of the main image. `test_badge_assets.py` enforces this.
