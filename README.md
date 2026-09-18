# ALTTPR Step Ladder Race Signals

A static infographic dashboard built from the 2026 public racetime.gg archive for the `alttpr-ladder` category.

## Requirements

- Python 3.11 or newer
- A network connection when refreshing the data

The data builder and web page have no third-party runtime dependencies.

## Refresh the data

```bash
python3 scripts/build_stats.py
```

The script requests the API's maximum of 100 records per page with entrant details enabled. It validates completion-time ordering and stops after the first complete page that crosses the January 1, 2026 Eastern Time boundary, so older archive pages are not collected. A final page-one check confirms that the source did not change during pagination, and the aggregate is atomically written to `site/data/race-stats.json`. Transient network failures are retried with bounded backoff.

Entrant user IDs are used only as in-memory deduplication keys. The generated file contains cohort totals and identity-coverage statistics, never entrant arrays, IDs, names, profile links, or Twitch fields.

## Preview the dashboard

```bash
python3 -m http.server 8000 -d site
```

Open <http://localhost:8000>. The page must be served over HTTP because it loads the generated JSON with `fetch`; opening `site/index.html` directly will not work in browsers that restrict local file requests.

## Publish with GitHub Pages

The workflow in `.github/workflows/pages.yml` runs the test suite and deploys the `site` directory whenever `main` is pushed. It can also be started manually from the repository's **Actions** tab.

For the first deployment:

1. Push the repository to GitHub.
2. Open **Settings > Pages** in the GitHub repository.
3. Under **Build and deployment**, set **Source** to **GitHub Actions**.
4. Open **Actions > Deploy GitHub Pages** and run the workflow, or push another commit to `main`.

The published URL is <https://michaelwolverinestream.github.io/racetimevibes/>. The workflow deploys the committed aggregate JSON; refresh it with `python3 scripts/build_stats.py` and commit the result when the data should change.

## Metric definitions

- Only records whose `status.value` is `finished` are included.
- The dataset begins January 1, 2026 in `America/New_York`. The local start date is authoritative at the UTC New Year boundary; no 2025 races or periods are published.
- Races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric.
- Schedule buckets use `America/New_York`, including the seasonal switch between EST and EDT.
- Six-month periods are calendar half-years (`Jan-Jun` and `Jul-Dec`) determined from the race's Eastern Time start. Edge periods are marked partial when the archive does not cover the full half-year.
- Race turnout uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries.
- Identified racers are deduplicated from stable public user IDs within the selected period. Identity coverage is the share of entries carrying such an ID. Cohorts are one race, occasional (2-5), regular (6-20), and core (21+); cohort entry shares use identified entries as the denominator.
- Unique-player metrics cover archived finished races only. They do not estimate people from deleted rooms, and anonymized entries contribute to identity coverage but not player or cohort counts.
- Six-month participation is average entries per finished race: total race entries divided by races in that calendar half-year.
- Hour, weekday-hour, and mode averages use the same entries-per-finished-race definition.
- Total entrants per hour sums recorded entries for the selected 2026 reporting period.
- Racerooms with fewer than two entrants are deleted and are not available through the archive. The dashboard estimates their count under a uniform hourly-opening assumption. Let $F_h$ be observed finished rooms in hour $h$ and let $B_6$ be the median of the six busiest hourly counts. The central estimate is $\sum_h \max(0, B_6 - F_h)$, rounded to the nearest room.
- The displayed sensitivity range repeats that calculation with the median of the 12 busiest hours as the lower baseline and the maximum observed hour as the upper baseline. This is sensitivity analysis, not a confidence interval. It estimates rooms missing from the archive and is separate from API records explicitly marked `cancelled`.
- Medians and quartiles describe the observed race-level distribution. The displayed 95% ranges are normal confidence intervals for the mean using the sample standard deviation; cells with fewer than two races have no interval.
- Adjusted weekday-hour lift subtracts the matching mode-by-calendar-half-year mean from every race, then averages those residuals within each weekday-hour cell. This reduces mode and broad time-period mix effects.
- Mode is the normalized first token in the `Step Ladder Series - [mode]` prefix of `info`. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.

The candidate rankings are observational leads for schedule experiments, not causal estimates. They do not control for announcements, competing events, holidays, organizer effects, or player availability, and sparse windows may look extreme by chance. Use the strongest windows to design prospective schedule tests and compare repeated outcomes before making permanent changes.

Data freshness is manual and build-time. The browser reads the local aggregate file and never crawls racetime.gg.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use mocked API responses and do not require network access.

## Project layout

```text
scripts/build_stats.py       Data retrieval, validation, and aggregation
tests/test_build_stats.py    Pipeline unit tests
site/data/race-stats.json    Generated aggregate dataset
site/index.html              Static dashboard document
site/app.js                  Native SVG chart rendering
site/styles.css              Responsive visual system
```

API behavior and field definitions are recorded in [Racetime_API.md](Racetime_API.md).