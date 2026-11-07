---
paths:
  - "scripts/race_archive.py"
  - "scripts/racer_bios.py"
  - "scripts/schedule_archive.py"
  - "archive/**"
  - "tests/test_race_archive.py"
  - "tests/test_racer_bios.py"
  - "tests/test_schedule_archive.py"
---

# Archive and syncs

- **Spec:** `docs/archive.md` (files, sync, names, avatars, bios, schedule mirror) and `docs/racetime-api.md` (endpoints and fields read). Update them in the same change.
- `archive/` is never edited by hand and never copied into `site/`. Only the sync scripts write it.
- Write archive files with `race_archive.write_json_atomic` (minified JSON, one trailing newline) or `write_bytes_atomic`.
- A field newly read from racetime.gg or alttpr.racing that reaches `site/` must fit README › Privacy.
- `schedule_archive.join_races` is the one slot-to-race join, and `season_starts`/`season_of` the one season rule; the build and the planner share them. Their terms are in `docs/definitions.md`.
- Tests mock every response and never use the network.
