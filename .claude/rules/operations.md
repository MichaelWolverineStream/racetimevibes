---
paths:
  - ".github/workflows/**"
  - "scripts/version_assets.py"
  - "tests/test_version_assets.py"
---

# Workflows and asset versioning

- **Spec:** `docs/operations.md` (refresh steps, options, versioning, tests, deployment, the scheduled refresh). Update it in the same change as any trigger, schedule, input or commit rule.
- `pages.yml` gates every deploy on the production suite. `analysis-tests.yml` never blocks a deploy.
- `update-data.yml` is the only caller of `build_stats.py --lock-seasons`. Its pushes use `GITHUB_TOKEN`, which triggers no workflow, so it starts **Deploy GitHub Pages** itself.
- `update-bios.yml` has no `concurrency` group on purpose: the same group as its callee would deadlock.
- `version_assets.py` hashes `site/index.html` and `VERSIONED_ASSETS` into one token, rewrites every asset reference to `?v=<token>` and writes `site/version.json`. The Barlow fonts are not stamped.
