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
- Hour buckets use a fixed EDT offset of UTC-04:00 for the entire historical dataset. They do not switch to EST seasonally.
- Six-month periods are calendar half-years (`Jan-Jun` and `Jul-Dec`) determined from the race start in that same fixed UTC-04:00 offset.
- Race turnout uses `entrants_count`, which includes entrants who were disqualified or forfeited.
- Six-month participation is average racers per finished race: total race entries divided by races in that calendar half-year.
- Average racers by hour is total entrants divided by finished races starting in that hour.
- Average racers by mode is total entrants divided by finished races assigned to that mode.
- Mode is the normalized first token in the `Step Ladder Series - [mode]` prefix of `info`. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.

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