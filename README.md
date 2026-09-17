# ALTTPR Step Ladder Race Signals

A static infographic dashboard built from the public racetime.gg archive for the `alttpr-ladder` category.

## Requirements

- Python 3.11 or newer
- A network connection when refreshing the data

The data builder and web page have no third-party runtime dependencies.

## Refresh the data

```bash
python3 scripts/build_stats.py
```

The script requests the API's maximum of 100 records per page, discovers the page count dynamically, validates that the collection did not change during pagination, and atomically writes `site/data/race-stats.json`. Transient network failures are retried with bounded backoff.

## Preview the dashboard

```bash
python3 -m http.server 8000 -d site
```

Open <http://localhost:8000>. The page must be served over HTTP because it loads the generated JSON with `fetch`; opening `site/index.html` directly will not work in browsers that restrict local file requests.

## Metric definitions

- Only records whose `status.value` is `finished` are included.
- Races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric.
- Schedule buckets use `America/New_York`, including the seasonal switch between EST and EDT.
- Six-month periods are calendar half-years (`Jan-Jun` and `Jul-Dec`) determined from the race's Eastern Time start. Edge periods are marked partial when the archive does not cover the full half-year.
- Race turnout uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries, not unique people.
- Six-month participation is average entries per finished race: total race entries divided by races in that calendar half-year.
- Hour, weekday-hour, and mode averages use the same entries-per-finished-race definition.
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