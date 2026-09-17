#!/usr/bin/env python3
"""Fetch ALTTPR ladder races and build the static dashboard dataset."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE_URL = "https://racetime.gg/alttpr-ladder/races/data"
PER_PAGE = 100
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.2
NETWORK_ATTEMPTS = 3
COLLECTION_ATTEMPTS = 2
USER_AGENT = "racetimevibes/1.0 (+static infographic data builder)"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "site" / "data" / "race-stats.json"

FIXED_EDT = timezone(timedelta(hours=-4), name="EDT")
MODE_PATTERN = re.compile(
    r"^\s*Step\s+Ladder\s+Series\s*-\s*\[([^\[\]]+)\]",
    re.IGNORECASE,
)
EXCLUDED_MODES = {"ladder test", "ladder_test"}

JsonObject = dict[str, Any]
PageLoader = Callable[[int], JsonObject]


class DataError(RuntimeError):
    """Raised when API data cannot produce a trustworthy aggregate."""


class ConsistencyError(DataError):
    """Raised when the paginated collection changes or contains duplicates."""


def fetch_page(page: int) -> JsonObject:
    """Fetch one API page with bounded retries for transient failures."""
    query = urlencode({"page": page, "per_page": PER_PAGE})
    request = Request(
        f"{SOURCE_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )

    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise DataError(f"Page {page} returned a non-object JSON response")
            return payload
        except HTTPError as error:
            is_transient = error.code == 429 or 500 <= error.code < 600
            if not is_transient or attempt == NETWORK_ATTEMPTS:
                raise DataError(f"Page {page} failed with HTTP {error.code}") from error
        except (URLError, TimeoutError) as error:
            if attempt == NETWORK_ATTEMPTS:
                raise DataError(f"Page {page} could not be fetched: {error}") from error
        except json.JSONDecodeError as error:
            raise DataError(f"Page {page} returned invalid JSON") from error

        time.sleep(2 ** (attempt - 1))

    raise AssertionError("network retry loop exited unexpectedly")


def _page_metadata(payload: JsonObject, page: int) -> tuple[int, int, list[JsonObject]]:
    count = payload.get("count")
    num_pages = payload.get("num_pages")
    races = payload.get("races")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(num_pages, int)
        or isinstance(num_pages, bool)
        or num_pages < 1
        or not isinstance(races, list)
        or any(not isinstance(race, dict) for race in races)
    ):
        raise DataError(f"Page {page} has invalid pagination data")
    return count, num_pages, races


def _collect_once(page_loader: PageLoader) -> tuple[list[JsonObject], int, int]:
    first_payload = page_loader(1)
    expected_count, expected_pages, first_races = _page_metadata(first_payload, 1)
    races = list(first_races)
    newest_name = first_races[0].get("name") if first_races else None

    for page in range(2, expected_pages + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = page_loader(page)
        count, num_pages, page_races = _page_metadata(payload, page)
        if count != expected_count or num_pages != expected_pages:
            raise ConsistencyError(
                f"Pagination changed on page {page}: "
                f"expected {expected_count} records/{expected_pages} pages, "
                f"received {count} records/{num_pages} pages"
            )
        races.extend(page_races)

    names = [race.get("name") for race in races]
    if any(not isinstance(name, str) or not name for name in names):
        raise DataError("At least one race is missing its unique name")
    unique_names = set(names)
    if len(unique_names) != len(names):
        raise ConsistencyError("Duplicate race names were returned across pages")
    if len(unique_names) != expected_count:
        raise ConsistencyError(
            f"Expected {expected_count} unique races, received {len(unique_names)}"
        )

    time.sleep(REQUEST_DELAY_SECONDS)
    final_payload = page_loader(1)
    final_count, final_pages, final_races = _page_metadata(final_payload, 1)
    final_newest_name = final_races[0].get("name") if final_races else None
    if (
        final_count != expected_count
        or final_pages != expected_pages
        or final_newest_name != newest_name
    ):
        raise ConsistencyError("Race collection changed while pages were being fetched")

    return races, expected_count, expected_pages


def collect_races(
    page_loader: PageLoader | None = None,
    *,
    collection_attempts: int = COLLECTION_ATTEMPTS,
) -> tuple[list[JsonObject], int, int]:
    """Fetch a consistent snapshot, retrying the complete traversal once."""
    loader = page_loader or fetch_page
    for attempt in range(1, collection_attempts + 1):
        try:
            return _collect_once(loader)
        except ConsistencyError:
            if attempt == collection_attempts:
                raise
    raise AssertionError("collection retry loop exited unexpectedly")


def parse_started_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("started_at is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("started_at has no UTC offset")
    return parsed.astimezone(UTC)


def extract_mode(info: str) -> str:
    match = MODE_PATTERN.search(info)
    if not match:
        return "unknown"
    mode = match.group(1).strip().lower()
    return mode or "unknown"


def _average(total: int, count: int) -> float | None:
    return total / count if count else None


def _date_range(started_at_values: list[datetime]) -> JsonObject | None:
    if not started_at_values:
        return None
    return {
        "start": min(started_at_values).isoformat().replace("+00:00", "Z"),
        "end": max(started_at_values).isoformat().replace("+00:00", "Z"),
    }


def _aggregate_records(records: list[JsonObject]) -> JsonObject:
    hourly: dict[int, dict[str, int]] = {
        hour: {"race_count": 0, "entrant_total": 0} for hour in range(24)
    }
    modes: dict[str, dict[str, int]] = defaultdict(
        lambda: {"race_count": 0, "entrant_total": 0}
    )

    for record in records:
        hour = record["local_started_at"].hour
        mode = record["mode"]
        entrants_count = record["entrants_count"]
        hourly[hour]["race_count"] += 1
        hourly[hour]["entrant_total"] += entrants_count
        modes[mode]["race_count"] += 1
        modes[mode]["entrant_total"] += entrants_count

    hourly_output = [
        {
            "hour": hour,
            "race_count": values["race_count"],
            "entrant_total": values["entrant_total"],
            "average_racers": _average(values["entrant_total"], values["race_count"]),
        }
        for hour, values in hourly.items()
    ]
    mode_output = [
        {
            "mode": mode,
            "race_count": values["race_count"],
            "entrant_total": values["entrant_total"],
            "average_racers": _average(values["entrant_total"], values["race_count"]),
        }
        for mode, values in modes.items()
    ]
    mode_output.sort(
        key=lambda item: (-(item["average_racers"] or 0), item["mode"])
    )
    started_at_values = [record["started_at"] for record in records]

    return {
        "race_count": len(records),
        "entrant_total": sum(record["entrants_count"] for record in records),
        "included_date_range": _date_range(started_at_values),
        "hourly": hourly_output,
        "modes": mode_output,
    }


def six_month_period(local_started_at: datetime) -> JsonObject:
    first_half = local_started_at.month <= 6
    half = 1 if first_half else 2
    start_month = 1 if first_half else 7
    end_month = 6 if first_half else 12
    return {
        "id": f"{local_started_at.year}-H{half}",
        "label": f"{'Jan-Jun' if first_half else 'Jul-Dec'} {local_started_at.year}",
        "start": f"{local_started_at.year}-{start_month:02d}-01",
        "end": f"{local_started_at.year}-{end_month:02d}-{30 if end_month == 6 else 31}",
    }


def aggregate_races(
    races: list[JsonObject],
    api_record_count: int,
    num_pages: int,
    *,
    generated_at: datetime | None = None,
) -> JsonObject:
    """Filter and aggregate races into the static page's JSON schema."""
    excluded_statuses: Counter[str] = Counter()
    excluded_modes: Counter[str] = Counter()
    invalid_records: Counter[str] = Counter()
    included_records: list[JsonObject] = []
    period_records: dict[str, list[JsonObject]] = defaultdict(list)
    period_metadata: dict[str, JsonObject] = {}
    unknown_mode_count = 0

    for race in races:
        status = race.get("status")
        status_value = status.get("value") if isinstance(status, dict) else None
        if status_value != "finished":
            excluded_statuses[str(status_value or "missing")] += 1
            continue

        try:
            started_at = parse_started_at(race.get("started_at"))
        except (TypeError, ValueError):
            invalid_records["invalid_started_at"] += 1
            continue

        entrants_count = race.get("entrants_count")
        if (
            not isinstance(entrants_count, int)
            or isinstance(entrants_count, bool)
            or entrants_count < 0
        ):
            invalid_records["invalid_entrants_count"] += 1
            continue

        info = race.get("info")
        if not isinstance(info, str):
            invalid_records["invalid_info"] += 1
            continue

        mode = extract_mode(info)
        if mode in EXCLUDED_MODES:
            excluded_modes[mode] += 1
            continue
        if mode == "unknown":
            unknown_mode_count += 1

        local_started_at = started_at.astimezone(FIXED_EDT)
        period = six_month_period(local_started_at)
        record = {
            "started_at": started_at,
            "local_started_at": local_started_at,
            "entrants_count": entrants_count,
            "mode": mode,
        }
        included_records.append(record)
        period_records[period["id"]].append(record)
        period_metadata[period["id"]] = period

    overall = _aggregate_records(included_records)
    periods = []
    for period_id in sorted(period_records):
        period = period_metadata[period_id] | _aggregate_records(period_records[period_id])
        periods.append(period)

    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    included_count = overall["race_count"]

    return {
        "schema_version": 2,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "source_url": SOURCE_URL,
        "timezone": {"label": "EDT (UTC-04:00)", "utc_offset": "-04:00"},
        "source": {
            "api_record_count": api_record_count,
            "fetched_record_count": len(races),
            "num_pages": num_pages,
            "included_race_count": included_count,
            "included_entrant_count": overall["entrant_total"],
            "excluded_race_count": len(races) - included_count,
            "included_date_range": overall["included_date_range"],
        },
        "hourly": overall["hourly"],
        "modes": overall["modes"],
        "periods": periods,
        "diagnostics": {
            "excluded_by_status": dict(sorted(excluded_statuses.items())),
            "excluded_by_mode": dict(sorted(excluded_modes.items())),
            "invalid_records": dict(sorted(invalid_records.items())),
            "unknown_mode_count": unknown_mode_count,
        },
    }


def write_json_atomic(payload: JsonObject, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=False)
        output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, output_path)


def build(output_path: Path = OUTPUT_PATH) -> JsonObject:
    races, api_record_count, num_pages = collect_races()
    stats = aggregate_races(races, api_record_count, num_pages)
    write_json_atomic(stats, output_path)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"output JSON path (default: {OUTPUT_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        stats = build(args.output)
    except DataError as error:
        print(f"Data build failed: {error}", file=sys.stderr)
        return 1

    source = stats["source"]
    print(
        f"Wrote {args.output} with {source['included_race_count']} finished races "
        f"from {source['api_record_count']} API records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())