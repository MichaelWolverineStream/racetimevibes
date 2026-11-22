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
- `schedule_archive.join_races` is the one slot-to-race join, and `season_starts`, `season_spans` and `season_of` the one season rule; the build and the planner share them, and the bio sync reads `season_spans`. Their terms are in `docs/definitions.md`.
