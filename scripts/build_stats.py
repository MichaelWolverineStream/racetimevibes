#!/usr/bin/env python3
"""Fetch ALTTPR ladder races and build the static dashboard dataset."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SOURCE_URL = "https://racetime.gg/alttpr-ladder/races/data"
PER_PAGE = 100
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.2
NETWORK_ATTEMPTS = 3
COLLECTION_ATTEMPTS = 2
USER_AGENT = "racetimevibes/1.0 (+static infographic data builder)"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "site" / "data" / "race-stats.json"

SCHEDULE_TIMEZONE_NAME = "America/New_York"
SCHEDULE_TIMEZONE = ZoneInfo(SCHEDULE_TIMEZONE_NAME)
DATA_START_LOCAL_DATE = date(2026, 1, 1)
DATA_START_LOCAL = datetime(2026, 1, 1, tzinfo=SCHEDULE_TIMEZONE)
DATA_START_UTC = DATA_START_LOCAL.astimezone(UTC)
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
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
    query = urlencode(
        {"page": page, "per_page": PER_PAGE, "show_entrants": "true"}
    )
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
        except (URLError, TimeoutError, ConnectionError) as error:
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


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} has no UTC offset")
    return parsed.astimezone(UTC)


def _completion_time(race: JsonObject) -> datetime:
    value = race.get("ended_at") or race.get("cancelled_at")
    try:
        return _parse_timestamp(value, "ended_at/cancelled_at")
    except (TypeError, ValueError) as error:
        name = race.get("name", "<unnamed>")
        raise DataError(f"Race {name} has no valid completion timestamp") from error


def _collect_once(
    page_loader: PageLoader,
) -> tuple[list[JsonObject], int, int, int]:
    first_payload = page_loader(1)
    expected_count, expected_pages, first_races = _page_metadata(first_payload, 1)
    newest_name = first_races[0].get("name") if first_races else None
    races: list[JsonObject] = []
    previous_completion: datetime | None = None
    fetched_page_count = 0

    for page in range(1, expected_pages + 1):
        if page == 1:
            page_races = first_races
        else:
            time.sleep(REQUEST_DELAY_SECONDS)
            payload = page_loader(page)
            count, num_pages, page_races = _page_metadata(payload, page)
            if count != expected_count or num_pages != expected_pages:
                raise ConsistencyError(
                    f"Pagination changed on page {page}: "
                    f"expected {expected_count} records/{expected_pages} pages, "
                    f"received {count} records/{num_pages} pages"
                )

        page_completions = []
        for race in page_races:
            completion = _completion_time(race)
            if previous_completion is not None and completion > previous_completion:
                raise ConsistencyError(
                    "Races were not returned in non-increasing completion order"
                )
            previous_completion = completion
            page_completions.append(completion)

        races.extend(page_races)
        fetched_page_count = page
        if page_completions and page_completions[-1] < DATA_START_UTC:
            break

    names = [race.get("name") for race in races]
    if any(not isinstance(name, str) or not name for name in names):
        raise DataError("At least one race is missing its unique name")
    unique_names = set(names)
    if len(unique_names) != len(names):
        raise ConsistencyError("Duplicate race names were returned across pages")
    if fetched_page_count == expected_pages and len(unique_names) != expected_count:
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

    return races, expected_count, expected_pages, fetched_page_count


def collect_races(
    page_loader: PageLoader | None = None,
    *,
    collection_attempts: int = COLLECTION_ATTEMPTS,
) -> tuple[list[JsonObject], int, int, int]:
    """Fetch a consistent cutoff-bounded snapshot, retrying once if it changes."""
    loader = page_loader or fetch_page
    for attempt in range(1, collection_attempts + 1):
        try:
            return _collect_once(loader)
        except ConsistencyError:
            if attempt == collection_attempts:
                raise
    raise AssertionError("collection retry loop exited unexpectedly")


def parse_started_at(value: Any) -> datetime:
    return _parse_timestamp(value, "started_at")


def extract_mode(info: str) -> str:
    match = MODE_PATTERN.search(info)
    if not match:
        return "unknown"
    mode = match.group(1).strip().lower()
    return mode or "unknown"


def _average(total: int, count: int) -> float | None:
    return total / count if count else None


def _distribution(values: list[float], *, lower_bound: float | None = None) -> JsonObject:
    if not values:
        return {
            "mean": None,
            "median": None,
            "q1": None,
            "q3": None,
            "standard_deviation": None,
            "ci95_low": None,
            "ci95_high": None,
        }

    ordered = sorted(values)
    count = len(ordered)
    mean = sum(ordered) / count
    middle = count // 2
    lower_half = ordered[:middle]
    upper_half = ordered[(count + 1) // 2 :]
    median = statistics.median(ordered)
    q1 = statistics.median(lower_half) if lower_half else median
    q3 = statistics.median(upper_half) if upper_half else median
    standard_deviation = statistics.stdev(ordered) if count > 1 else None
    if standard_deviation is None:
        ci95_low = None
        ci95_high = None
    else:
        margin = 1.96 * standard_deviation / math.sqrt(count)
        ci95_low = mean - margin
        ci95_high = mean + margin
        if lower_bound is not None:
            ci95_low = max(lower_bound, ci95_low)

    return {
        "mean": mean,
        "median": median,
        "q1": q1,
        "q3": q3,
        "standard_deviation": standard_deviation,
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
    }


def _entry_summary(values: list[int]) -> JsonObject:
    distribution = _distribution(values, lower_bound=0)
    return {
        "race_count": len(values),
        "entrant_total": sum(values),
        "average_racers": distribution["mean"],
        "median_racers": distribution["median"],
        "q1_racers": distribution["q1"],
        "q3_racers": distribution["q3"],
        "standard_deviation": distribution["standard_deviation"],
        "ci95_low": distribution["ci95_low"],
        "ci95_high": distribution["ci95_high"],
    }


def _estimated_deleted_rooms(counts: list[int], top_hour_count: int) -> tuple[float, int]:
    busiest_counts = sorted(counts, reverse=True)[:top_hour_count]
    baseline = statistics.median(busiest_counts)
    raw_estimate = sum(max(0, baseline - count) for count in counts)
    return baseline, math.floor(raw_estimate + 0.5)


def _room_cancellation_estimate(hourly_output: list[JsonObject]) -> JsonObject:
    counts = [item["race_count"] for item in hourly_output]
    if not counts:
        raise DataError("Room cancellation estimate requires hourly observations")

    baseline, estimated_deleted = _estimated_deleted_rooms(counts, 6)
    _, lower_estimate = _estimated_deleted_rooms(counts, 12)
    upper_baseline, upper_estimate = _estimated_deleted_rooms(counts, 1)
    observed_finished = sum(counts)
    estimated_opened = observed_finished + estimated_deleted

    return {
        "method": "top_6_median_clamped_hourly_deficit",
        "top_hour_count": 6,
        "baseline_rooms_per_hour": baseline,
        "observed_finished_rooms": observed_finished,
        "estimated_deleted_rooms": estimated_deleted,
        "estimated_opened_rooms": estimated_opened,
        "estimated_deleted_share": (
            estimated_deleted / estimated_opened if estimated_opened else 0
        ),
        "sensitivity": {
            "lower_method": "top_12_median_clamped_hourly_deficit",
            "lower_estimated_deleted_rooms": lower_estimate,
            "upper_method": "maximum_hour_clamped_hourly_deficit",
            "upper_baseline_rooms_per_hour": upper_baseline,
            "upper_estimated_deleted_rooms": upper_estimate,
        },
    }


def _entrant_identity_counts(
    race: JsonObject, entrants_count: int
) -> tuple[tuple[str, ...], int]:
    entrants = race.get("entrants")
    race_name = race.get("name", "<unnamed>")
    if not isinstance(entrants, list):
        raise DataError(f"Race {race_name} is missing its entrants array")
    if len(entrants) != entrants_count:
        raise DataError(
            f"Race {race_name} reports {entrants_count} entrants but returned "
            f"{len(entrants)} entrant records"
        )

    entrant_ids = []
    unidentified_count = 0
    for entrant in entrants:
        user = entrant.get("user") if isinstance(entrant, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        if isinstance(user_id, str) and user_id:
            entrant_ids.append(user_id)
        else:
            unidentified_count += 1

    if len(set(entrant_ids)) != len(entrant_ids):
        raise DataError(f"Race {race_name} contains a duplicate entrant user ID")
    return tuple(entrant_ids), unidentified_count


def _unique_player_summary(records: list[JsonObject]) -> JsonObject:
    appearances: Counter[str] = Counter()
    unidentified_entry_count = 0
    for record in records:
        appearances.update(record["entrant_ids"])
        unidentified_entry_count += record["unidentified_entry_count"]

    identified_entry_count = sum(appearances.values())
    identified_player_count = len(appearances)
    appearance_counts = list(appearances.values())
    entrant_total = sum(record["entrants_count"] for record in records)
    cohort_definitions = (
        ("one_race", lambda count: count == 1),
        ("occasional", lambda count: 2 <= count <= 5),
        ("regular", lambda count: 6 <= count <= 20),
        ("core", lambda count: count >= 21),
    )
    cohorts = []
    for cohort_id, matches in cohort_definitions:
        cohort_counts = [count for count in appearance_counts if matches(count)]
        player_count = len(cohort_counts)
        entry_count = sum(cohort_counts)
        cohorts.append(
            {
                "id": cohort_id,
                "player_count": player_count,
                "player_share": (
                    player_count / identified_player_count
                    if identified_player_count
                    else 0
                ),
                "entry_count": entry_count,
                "entry_share": (
                    entry_count / identified_entry_count if identified_entry_count else 0
                ),
            }
        )

    if identified_entry_count + unidentified_entry_count != entrant_total:
        raise DataError("Entrant identity counts do not reconcile with entrant totals")

    return {
        "identified_player_count": identified_player_count,
        "identified_entry_count": identified_entry_count,
        "unidentified_entry_count": unidentified_entry_count,
        "identity_coverage": (
            identified_entry_count / entrant_total if entrant_total else 0
        ),
        "average_races_per_player": (
            identified_entry_count / identified_player_count
            if identified_player_count
            else None
        ),
        "median_races_per_player": (
            statistics.median(appearance_counts) if appearance_counts else None
        ),
        "repeat_player_count": sum(count >= 2 for count in appearance_counts),
        "cohorts": cohorts,
    }


def _adjusted_summary(values: list[float]) -> JsonObject:
    distribution = _distribution(values)
    return {
        "adjusted_average": distribution["mean"],
        "adjusted_median": distribution["median"],
        "adjusted_standard_deviation": distribution["standard_deviation"],
        "adjusted_ci95_low": distribution["ci95_low"],
        "adjusted_ci95_high": distribution["ci95_high"],
    }


def _date_range(started_at_values: list[datetime]) -> JsonObject | None:
    if not started_at_values:
        return None
    return {
        "start": min(started_at_values).isoformat().replace("+00:00", "Z"),
        "end": max(started_at_values).isoformat().replace("+00:00", "Z"),
    }


def _aggregate_records(records: list[JsonObject]) -> JsonObject:
    hourly: dict[int, list[int]] = {hour: [] for hour in range(24)}
    weekdays: dict[int, list[int]] = {weekday: [] for weekday in range(7)}
    weekday_hours: dict[tuple[int, int], list[JsonObject]] = {
        (weekday, hour): [] for weekday in range(7) for hour in range(24)
    }
    modes: dict[str, list[int]] = defaultdict(list)

    for record in records:
        hour = record["local_started_at"].hour
        weekday = record["local_started_at"].weekday()
        mode = record["mode"]
        entrants_count = record["entrants_count"]
        hourly[hour].append(entrants_count)
        weekdays[weekday].append(entrants_count)
        weekday_hours[(weekday, hour)].append(record)
        modes[mode].append(entrants_count)

    hourly_output = [
        {"hour": hour} | _entry_summary(values)
        for hour, values in hourly.items()
    ]
    weekday_output = [
        {
            "weekday": weekday,
            "label": WEEKDAY_LABELS[weekday],
        }
        | _entry_summary(values)
        for weekday, values in weekdays.items()
    ]
    weekday_hour_output = []
    for (weekday, hour), values in weekday_hours.items():
        entrant_values = [record["entrants_count"] for record in values]
        adjusted_values = [record["adjusted_entries"] for record in values]
        weekday_hour_output.append(
            {
                "weekday": weekday,
                "weekday_label": WEEKDAY_LABELS[weekday],
                "hour": hour,
            }
            | _entry_summary(entrant_values)
            | _adjusted_summary(adjusted_values)
        )
    mode_output = [
        {"mode": mode} | _entry_summary(values)
        for mode, values in modes.items()
    ]
    mode_output.sort(
        key=lambda item: (-(item["average_racers"] or 0), item["mode"])
    )
    started_at_values = [record["started_at"] for record in records]
    overall = _entry_summary([record["entrants_count"] for record in records])

    return {
        **overall,
        "included_date_range": _date_range(started_at_values),
        "hourly": hourly_output,
        "weekdays": weekday_output,
        "weekday_hourly": weekday_hour_output,
        "modes": mode_output,
        "unique_players": _unique_player_summary(records),
        "room_cancellation_estimate": _room_cancellation_estimate(hourly_output),
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


def _period_coverage(
    period: JsonObject,
    records: list[JsonObject],
    archive_start: date,
    archive_end: date,
) -> JsonObject:
    observed_dates = [record["local_started_at"].date() for record in records]
    period_start = date.fromisoformat(period["start"])
    period_end = date.fromisoformat(period["end"])
    is_complete = archive_start <= period_start and archive_end >= period_end
    return {
        "is_complete": is_complete,
        "completeness": "complete" if is_complete else "partial",
        "observed_local_date_range": {
            "start": min(observed_dates).isoformat(),
            "end": max(observed_dates).isoformat(),
        },
    }


def aggregate_races(
    races: list[JsonObject],
    api_record_count: int,
    api_page_count: int,
    *,
    fetched_page_count: int | None = None,
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
    excluded_before_data_start = 0

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

        local_started_at = started_at.astimezone(SCHEDULE_TIMEZONE)
        if local_started_at.date() < DATA_START_LOCAL_DATE:
            excluded_before_data_start += 1
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

        entrant_ids, unidentified_entry_count = _entrant_identity_counts(
            race, entrants_count
        )
        period = six_month_period(local_started_at)
        record = {
            "started_at": started_at,
            "local_started_at": local_started_at,
            "entrants_count": entrants_count,
            "entrant_ids": entrant_ids,
            "unidentified_entry_count": unidentified_entry_count,
            "mode": mode,
            "period_id": period["id"],
        }
        included_records.append(record)
        period_records[period["id"]].append(record)
        period_metadata[period["id"]] = period

    if not included_records:
        raise DataError("No races remained after filtering and validation")

    mode_period_values: dict[tuple[str, str], list[int]] = defaultdict(list)
    for record in included_records:
        mode_period_values[(record["mode"], record["period_id"])].append(
            record["entrants_count"]
        )
    mode_period_means = {
        key: sum(values) / len(values) for key, values in mode_period_values.items()
    }
    for record in included_records:
        baseline = mode_period_means[(record["mode"], record["period_id"])]
        record["adjusted_entries"] = record["entrants_count"] - baseline

    overall = _aggregate_records(included_records)
    periods = []
    archive_start = min(record["local_started_at"].date() for record in included_records)
    archive_end = max(record["local_started_at"].date() for record in included_records)
    for period_id in sorted(period_records):
        records = period_records[period_id]
        period = (
            period_metadata[period_id]
            | _period_coverage(
                period_metadata[period_id], records, archive_start, archive_end
            )
            | _aggregate_records(records)
        )
        periods.append(period)

    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    included_count = overall["race_count"]

    return {
        "schema_version": 4,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "source_url": SOURCE_URL,
        "timezone": {
            "label": "Eastern Time",
            "name": SCHEDULE_TIMEZONE_NAME,
        },
        "source": {
            "data_start_local": DATA_START_LOCAL_DATE.isoformat(),
            "api_record_count": api_record_count,
            "api_page_count": api_page_count,
            "fetched_record_count": len(races),
            "fetched_page_count": fetched_page_count or api_page_count,
            "included_race_count": included_count,
            "included_entrant_count": overall["entrant_total"],
            "excluded_fetched_race_count": len(races) - included_count,
            "included_date_range": overall["included_date_range"],
        },
        "hourly": overall["hourly"],
        "weekdays": overall["weekdays"],
        "weekday_hourly": overall["weekday_hourly"],
        "modes": overall["modes"],
        "unique_players": overall["unique_players"],
        "room_cancellation_estimate": overall["room_cancellation_estimate"],
        "periods": periods,
        "diagnostics": {
            "excluded_before_data_start": excluded_before_data_start,
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
    races, api_record_count, api_page_count, fetched_page_count = collect_races()
    stats = aggregate_races(
        races,
        api_record_count,
        api_page_count,
        fetched_page_count=fetched_page_count,
    )
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