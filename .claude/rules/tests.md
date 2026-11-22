---
paths:
  - "tests/**"
---

# Tests

- **Standard:** `docs/testing.md`. Every new or changed test follows it; update it in the same change as any new convention.
- A class that reads the archive or a published file is named `Committed*`. It asserts rules, never statistics.
- Statistics over the growing archive are monitors in `tests/analyses/test_rating_monitors.py`, never in `tests/`.
- Production code that an analysis also uses is tested in `tests/`, not `tests/analyses/`.
- Name each test as a sentence stating one behaviour, and make the body prove all of it.
- Assert against the constant or a property, not a copied literal.
- A value mirrored between Python, JS and CSS is checked by parsing both sides.
- Hermetic tests:
  - temporary directories with `addCleanup`;
  - close what you open, `HTTPError` included;
  - inject the clock, and seed the randomness.
- A missing committed file fails; it never skips. Every class sits above the `__main__` guard.
- Fold near-identical cases into one `subTest` table. A delete or merge names the test that already covers it.
