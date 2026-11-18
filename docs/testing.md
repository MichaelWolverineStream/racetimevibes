# Testing

- **Purpose:** the standard every test follows: which suite a test belongs to, what may block a deploy, and how to write one.
- **Read before:** adding, moving, merging or deleting a test.
- **Code:** `tests/`, `tests/analyses/`, and the test steps of `.github/workflows/` (commands and workflows in [operations.md › Tests](operations.md#tests)).

## Tiers

| Tier | Lives in | Checks | Runs in | Blocks a deploy |
|---|---|---|---|---|
| Code | `tests/`, on inputs the test builds | script and page logic | `pages.yml`, locally | Yes |
| Committed | `Committed*` classes in `tests/` | `archive/`, `season-ratings/` and the files `site/` publishes | `update-data.yml` before it commits, `pages.yml` | Yes |
| Analysis | `tests/analyses/` | the standalone analyses in [analyses.md](analyses.md) | `analysis-tests.yml`, locally | No |
| Monitor | `tests/analyses/test_rating_monitors.py`, the planner's `CommittedDataTests` | statistics and upstream drift on the growing archive | the weekly `analysis-tests.yml` run | No; a failure is the alert |

`python3 -m unittest discover -s tests -k Committed` runs the Committed tier alone, as the bot does.

## Where a new test goes

1. **Does it read the archive, or a file the bot rewrites?** Put it in a `Committed*` class in `tests/`. It asserts a rule that holds for every archive (a privacy promise, a schema, a rule 8 invariant), never a statistic.
2. **Does it check a script or the page?** Put it in `tests/`, on inputs the test builds. Production code that an analysis also uses (`schedule_archive.join_races`, the season rule) is tested here, once.
3. **Does it cover a standalone analysis, or a statistic over the growing archive?** Put it in `tests/analyses/`. A statistic there fails the weekly run, not a deploy.

## Rules for every test

1. **One behaviour, named.** The name is a sentence that states the behaviour, and the body proves all of it: "equal or slower" runs both cases.
2. **Assert the rule, not the value.** Compare with the constant, the docs table or a property. A literal belongs only where it is the contract itself, such as the badge table's order.
3. **Test through public functions.** A private helper gets its own test only when a failure through the public path would be hard to diagnose. The same goes for an oracle written in a test file, such as the contrast checker in `test_site_theme.py`.
4. **One fact, once, at the lowest level.** Don't re-assert what a lower-level test already pins. Near-identical cases become one `subTest` table.
5. **Read mirrored values on both sides.** When Python, JS or CSS each spell out the same value, a test parses both copies. A "must match" comment is not a check.
6. **Contracts are allowlists on the real file.** Privacy and schema checks assert what a published file may hold, and run on the committed file as well as on fixtures.
7. **Hermetic.**
   - Mock every response; no test uses the network.
   - Write only to temporary directories, cleaned up with `addCleanup`.
   - Close what you open, including `HTTPError`, which leaks a temporary file.
   - Inject the clock, and seed the randomness.
8. **The deploy gate is deterministic.** A production test fails only when something is wrong. A threshold on a statistic over the growing archive is a monitor.
9. **A missing committed file fails.** It never skips, because a skip passes silently.
10. **Check fixture premises once.** Assert a premise that other tests rely on in one `FixtureTests` class, and never present it as a rule.
11. **Every class sits above `if __name__ == "__main__"`.** Otherwise, running the file directly skips it.
12. **Keep tests readable (DAMP).** Helpers build inputs, and the assertions stay in the test. A readable, self-contained test beats a clever shared one.

## Budgets

| Suite | Budget |
|---|---|
| Production, warm | about 10 s; no single test over 5 s |
| Committed tier | under 10 s |
| Analysis | about a minute; no single test over 10 s |

The workflows print the ten slowest tests (`--durations 10`, which needs Python 3.12 or newer). When a test breaks its budget, share its slow setup through `setUpClass` or a cached fixture. Don't cut coverage.

## Before handing back

- Run the file, then its suite.
- Check that a new test can fail: break the rule it guards by hand once, watch it fail, then restore the rule.
- A delete or a merge names the test that already covers the behaviour.

Sources: Kent Beck, *Test Desiderata*; Google Testing Blog, *Testing on the Toilet*; Vladimir Khorikov, *Unit Testing: Principles, Practices, and Patterns*; Ham Vocke, *The Practical Test Pyramid*; Martin Fowler, *Deployment Pipeline*.
