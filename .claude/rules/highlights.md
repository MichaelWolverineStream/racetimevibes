---
paths:
  - "scripts/highlights.py"
  - "scripts/badge_images.py"
  - "site/assets/badges/**"
  - "tests/test_highlights.py"
  - "tests/test_badge_assets.py"
---

# Highlights and badges

- **Spec:** `docs/highlights.md` (race tags, badges, streaks, presets, records, claims to fame). Update it in the same change.
- To add, remove or change a badge, follow the `badges` skill (`.claude/skills/badges/SKILL.md`).
- Thresholds are constants at the top of `highlights.py`, and descriptions are built from them.
- Every tag and badge that reads a rating reads the overall rating.
- After a change, rebuild `site/data` with `python3 scripts/build_stats.py` and commit it with the change.
