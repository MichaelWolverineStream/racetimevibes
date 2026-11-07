---
paths:
  - "scripts/build_stats.py"
  - "scripts/participant_stats.py"
  - "scripts/comment_stats.py"
  - "scripts/ladder_rating.py"
  - "scripts/season_ratings.py"
  - "site/data/**"
  - "season-ratings/**"
  - "tests/test_build_stats.py"
  - "tests/test_participant_stats.py"
  - "tests/test_comment_stats.py"
  - "tests/test_ladder_rating.py"
  - "tests/test_season_ratings.py"
---

# Build, datasets and ratings

- **Spec:** `docs/datasets.md` (the three files, schedule metrics, season locking and ledgers, chest counts), `docs/definitions.md` (included race, finisher, winner, slot, season) and README › Ladder rating. Update them in the same change.
- After changing a build script, run `python3 scripts/build_stats.py` (never `--lock-seasons`) and commit `site/data` with the change. The rebuild test fails on any other difference.
- A schema change bumps `PARTICIPANT_SCHEMA_VERSION` or `"schema_version"` in Python and the matching check in JS together.
- A new published field must fit README › Privacy.
- Rating changes keep the invariants in CLAUDE.md rule 8. Measure them with `python3 scripts/ladder_rating.py --backtest`; never tune on races from 2026-01-01.
- Never edit or regenerate `season-ratings/`. Ledgers are written only through `season_ratings.write_ledger`, which refuses to replace a file.
