---
paths:
  - "scripts/highlights.py"
  - "scripts/featured.py"
  - "scripts/badge_images.py"
  - "scripts/badge_overrides.py"
  - "badge-overrides.json"
  - "site/assets/badges/**"
  - "tests/test_highlights.py"
  - "tests/test_featured.py"
  - "tests/test_badge_assets.py"
  - "tests/test_badge_overrides.py"
---

# Highlights and badges

- **Spec:** `docs/highlights.md` (race tags, badges, badges given by hand, streaks, presets, records, claims to fame, facts and the featured racer). Update it in the same change.
- Thresholds are constants at the top of `highlights.py`, and descriptions are built from them.
- Every tag and badge that reads a rating reads the overall rating.
