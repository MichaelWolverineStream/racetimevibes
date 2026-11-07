---
paths:
  - "scripts/mode_overlap.py"
  - "scripts/schedule_planner.py"
  - "scripts/schedule_lab.py"
  - "scripts/rating_lab.py"
  - "schedule-plan.json"
  - "tests/analyses/**"
---

# Standalone analyses

- **Spec:** `docs/analyses.md`. Update it in the same change.
- The build never runs these scripts, and they change nothing in `site/`.
- `schedule_planner.py` reads `schedule-plan.json` and the schedule mirror. `SHIPPED` holds the rules the doc describes, and `schedule_lab.py` breaks them one variant at a time.
- `schedule_lab.py` uses worker processes; in a sandbox, run it with `--jobs 1`.
- `rating_lab.py` tunes on races before 2026-01-01 only. Never tune on 2026 data.
- Test with `python3 -m unittest discover -s tests/analyses`. The suite has no `__init__.py`, and its shared fixtures are in `tests/analyses/schedule_fixtures.py`.
