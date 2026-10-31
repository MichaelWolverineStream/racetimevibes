#!/usr/bin/env python3
"""Build the static dashboard dataset from the local race archive."""

from __future__ import annotations

import argparse
import calendar
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Supports both `python3 scripts/build_stats.py` and `from scripts import build_stats`.
try:
    from scripts import comment_stats, participant_stats, race_archive
except ImportError:
    import comment_stats
    import participant_stats
    import race_archive


DATA_DIR = Path(__file__).resolve().parents[1] / "site" / "data"
OUTPUT_PATH = DATA_DIR / "race-stats.json"
PARTICIPANTS_OUTPUT_PATH = DATA_DIR / "participants.json"
COMMENTS_OUTPUT_PATH = DATA_DIR / "comments.json"

SCHEDULE_TIMEZONE_NAME = "America/New_York"
SCHEDULE_TIMEZONE = ZoneInfo(SCHEDULE_TIMEZONE_NAME)
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MODE_PATTERN = re.compile(
    r"^\s*Step\s+Ladder\s+Series\s*-\s*\[([^\[\]]+)\]",
    re.IGNORECASE,
)
PRESET_PATTERN = re.compile(
    r"^\s*Step\s+Ladder\s+Series\s*-\s*\[[^\[\]]+\][ \t]*-[ \t]*([^\n]*)",
    re.IGNORECASE,
)
# Some rooms put the seed link and hash on the first line, as "Preset owner/slug - URL - (hash)".
PRESET_URL_TAIL = re.compile(r"(?:^|\s+)(?:-\s*)?https?://.*$")
# A room slug such as "jem041/xkeydrophunt"; crystal settings such as "7/7" have no letters.
PRESET_SLUG_TAIL = re.compile(r"\s+(?=[\w.-]*[A-Za-z])[\w.-]+/(?=[\w.-]*[A-Za-z])[\w.-]+$")
GRAB_BAG_PATTERN = re.compile(r"Grab Bag\s*\(", re.IGNORECASE)
EXCLUDED_MODES = {"ladder test", "ladder_test"}

JsonObject = dict[str, Any]


class DataError(RuntimeError):
    """Raised when archived data cannot produce a trustworthy aggregate."""


def parse_started_at(value: Any) -> datetime:
    return race_archive.parse_timestamp(value, "started_at")


def extract_mode(info: str) -> str:
    match = MODE_PATTERN.search(info)
    if not match:
        return "unknown"
    mode = match.group(1).strip().lower()
    return mode or "unknown"


def _collapse_grab_bag(preset: str) -> str:
    """Drop the parenthesized option list after "Grab Bag", nesting included."""
    match = GRAB_BAG_PATTERN.search(preset)
    if not match:
        return preset
    depth = 1
    end = match.end()
    while end < len(preset) and depth:
        depth += {"(": 1, ")": -1}.get(preset[end], 0)
        end += 1
    return f"{preset[: match.start()]}Grab Bag{preset[end:]}"


def extract_preset(info: str) -> str | None:
    """Return the normalized preset named after the mode on the first line."""
    match = PRESET_PATTERN.search(info)
    if not match:
        return None
    preset = match.group(1).strip()
    if PRESET_URL_TAIL.search(preset):
        preset = PRESET_SLUG_TAIL.sub("", PRESET_URL_TAIL.sub("", preset))
    preset = preset.replace("[", "(").replace("]", ")")
    preset = " ".join(preset.split())
    preset = " ".join(_collapse_grab_bag(preset).split())
    return preset or None


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


def _estimated_cancelled_rooms(counts: list[int], top_hour_count: int) -> tuple[float, int]:
    busiest_counts = sorted(counts, reverse=True)[:top_hour_count]
    baseline = statistics.median(busiest_counts)
    raw_estimate = sum(max(0, baseline - count) for count in counts)
    return baseline, math.floor(raw_estimate + 0.5)


def _room_cancellation_estimate(hourly_output: list[JsonObject]) -> JsonObject:
    counts = [item["race_count"] for item in hourly_output]
    if not counts:
        raise DataError("Room cancellation estimate requires hourly observations")

    baseline, estimated_cancelled = _estimated_cancelled_rooms(counts, 6)
    _, lower_estimate = _estimated_cancelled_rooms(counts, 12)
    upper_baseline, upper_estimate = _estimated_cancelled_rooms(counts, 1)
    observed_finished = sum(counts)
    estimated_opened = observed_finished + estimated_cancelled

    return {
        "method": "top_6_median_clamped_hourly_deficit",
        "top_hour_count": 6,
        "baseline_rooms_per_hour": baseline,
        "observed_finished_rooms": observed_finished,
        "estimated_cancelled_rooms": estimated_cancelled,
        "estimated_opened_rooms": estimated_opened,
        "estimated_cancelled_share": (
            estimated_cancelled / estimated_opened if estimated_opened else 0
        ),
        "sensitivity": {
            "lower_method": "top_12_median_clamped_hourly_deficit",
            "lower_estimated_cancelled_rooms": lower_estimate,
            "upper_method": "maximum_hour_clamped_hourly_deficit",
            "upper_baseline_rooms_per_hour": upper_baseline,
            "upper_estimated_cancelled_rooms": upper_estimate,
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


def quarter_period(local_started_at: date) -> JsonObject:
    year = local_started_at.year
    quarter = (local_started_at.month - 1) // 3 + 1
    start_month = 3 * quarter - 2
    end_month = 3 * quarter
    return {
        "id": f"{year}-Q{quarter}",
        "label": f"Q{quarter} {year}",
        "start": f"{year}-{start_month:02d}-01",
        "end": f"{year}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]}",
    }


def month_period(day: date) -> JsonObject:
    year = day.year
    month = day.month
    return {
        "id": f"{year}-{month:02d}",
        "label": f"{MONTH_LABELS[month - 1]} {year}",
        "start": f"{year}-{month:02d}-01",
        "end": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}",
        "quarter_id": quarter_period(day)["id"],
    }


def _monthly_turnout(
    records: list[JsonObject],
    archive_start: date,
    archive_end: date,
) -> list[JsonObject]:
    month_values: dict[str, list[int]] = defaultdict(list)
    for record in records:
        month = month_period(record["local_started_at"])
        month_values[month["id"]].append(record["entrants_count"])

    monthly = []
    year, month = archive_start.year, archive_start.month
    while (year, month) <= (archive_end.year, archive_end.month):
        period = month_period(date(year, month, 1))
        month_start = date.fromisoformat(period["start"])
        month_end = date.fromisoformat(period["end"])
        covered_start = max(archive_start, month_start)
        covered_end = min(archive_end, month_end)
        covered_day_count = (covered_end - covered_start).days + 1
        summary = _entry_summary(month_values[period["id"]])
        monthly.append(
            period
            | {
                "is_complete": archive_start <= month_start
                and archive_end >= month_end,
                "covered_day_count": covered_day_count,
            }
            | summary
            | {"entries_per_day": summary["entrant_total"] / covered_day_count}
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return monthly


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


def classify_race(race: JsonObject) -> tuple[JsonObject | None, tuple[str, str] | None]:
    """Return a race's mode and timing context, or the reason it is excluded."""
    status = race.get("status")
    status_value = status.get("value") if isinstance(status, dict) else None
    if status_value != "finished":
        return None, ("status", str(status_value or "missing"))

    try:
        started_at = parse_started_at(race.get("started_at"))
    except (TypeError, ValueError):
        return None, ("invalid", "invalid_started_at")

    local_started_at = started_at.astimezone(SCHEDULE_TIMEZONE)

    entrants_count = race.get("entrants_count")
    if (
        not isinstance(entrants_count, int)
        or isinstance(entrants_count, bool)
        or entrants_count < 0
    ):
        return None, ("invalid", "invalid_entrants_count")

    info = race.get("info")
    if not isinstance(info, str):
        return None, ("invalid", "invalid_info")

    mode = extract_mode(info)
    if mode in EXCLUDED_MODES:
        return None, ("mode", mode)

    return {
        "race": race,
        "started_at": started_at,
        "local_started_at": local_started_at,
        "entrants_count": entrants_count,
        "mode": mode,
        "preset": extract_preset(info),
    }, None


def included_race_contexts(races: list[JsonObject]) -> list[JsonObject]:
    """Return the context for every race that passes the dashboard filters."""
    contexts = []
    for race in races:
        context, _reason = classify_race(race)
        if context is not None:
            contexts.append(context)
    return contexts


def aggregate_races(
    races: list[JsonObject],
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
        context, reason = classify_race(race)
        if context is None:
            kind, detail = reason
            if kind == "status":
                excluded_statuses[detail] += 1
            elif kind == "mode":
                excluded_modes[detail] += 1
            else:
                invalid_records[detail] += 1
            continue

        mode = context["mode"]
        if mode == "unknown":
            unknown_mode_count += 1

        local_started_at = context["local_started_at"]
        entrants_count = context["entrants_count"]
        entrant_ids, unidentified_entry_count = _entrant_identity_counts(
            race, entrants_count
        )
        period = quarter_period(local_started_at)
        record = {
            "started_at": context["started_at"],
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
        "schema_version": 8,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "source_url": race_archive.SOURCE_URL,
        "timezone": {
            "label": "Eastern Time",
            "name": SCHEDULE_TIMEZONE_NAME,
        },
        "source": {
            "data_start_local": archive_start.isoformat(),
            "archived_race_count": len(races),
            "included_race_count": included_count,
            "included_entrant_count": overall["entrant_total"],
            "excluded_archived_race_count": len(races) - included_count,
            "included_date_range": overall["included_date_range"],
        },
        "hourly": overall["hourly"],
        "weekdays": overall["weekdays"],
        "weekday_hourly": overall["weekday_hourly"],
        "modes": overall["modes"],
        "unique_players": overall["unique_players"],
        "room_cancellation_estimate": overall["room_cancellation_estimate"],
        "periods": periods,
        "monthly": _monthly_turnout(included_records, archive_start, archive_end),
        "diagnostics": {
            "excluded_by_status": dict(sorted(excluded_statuses.items())),
            "excluded_by_mode": dict(sorted(excluded_modes.items())),
            "invalid_records": dict(sorted(invalid_records.items())),
            "unknown_mode_count": unknown_mode_count,
        },
    }


def build(
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    output_path: Path = OUTPUT_PATH,
    participants_output_path: Path = PARTICIPANTS_OUTPUT_PATH,
    comments_output_path: Path = COMMENTS_OUTPUT_PATH,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    races = [envelope["race"] for envelope in race_archive.load_races(archive_dir)]
    if not races:
        raise DataError(
            f"Archive {archive_dir} has no races; run scripts/race_archive.py first"
        )
    generated_at = datetime.now(UTC)
    contexts = included_race_contexts(races)
    stats = aggregate_races(races, generated_at=generated_at)
    participants = participant_stats.build_participant_payload(
        contexts,
        source_url=race_archive.SOURCE_URL,
        timezone_label=stats["timezone"]["label"],
        timezone_name=SCHEDULE_TIMEZONE_NAME,
        generated_at=generated_at,
        current_names=race_archive.load_racer_names(archive_dir),
    )
    comments = comment_stats.build_comments_payload(
        contexts, generated_at=generated_at
    )
    race_archive.write_json_atomic(stats, output_path)
    participant_stats.write_compact_json(participants, participants_output_path)
    participant_stats.write_compact_json(comments, comments_output_path)
    return stats, participants, comments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=race_archive.ARCHIVE_DIR,
        help=f"raw race archive directory (default: {race_archive.ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"output JSON path (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--participants-output",
        type=Path,
        default=PARTICIPANTS_OUTPUT_PATH,
        help=f"racer leaderboard JSON path (default: {PARTICIPANTS_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--comments-output",
        type=Path,
        default=COMMENTS_OUTPUT_PATH,
        help=f"racer comments JSON path (default: {COMMENTS_OUTPUT_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        stats, participants, comments = build(
            args.archive,
            args.output,
            args.participants_output,
            args.comments_output,
        )
    except (
        DataError,
        race_archive.ArchiveError,
        participant_stats.ParticipantDataError,
        comment_stats.CommentDataError,
    ) as error:
        print(f"Data build failed: {error}", file=sys.stderr)
        return 1

    source = stats["source"]
    print(
        f"Wrote {args.output} with {source['included_race_count']} finished races "
        f"from {source['archived_race_count']} archived races."
    )
    participant_diagnostics = participants["diagnostics"]
    print(
        f"Wrote {args.participants_output} with "
        f"{participant_diagnostics['player_count']} racers across "
        f"{participant_diagnostics['entry_count']} entries."
    )
    print(
        f"Wrote {args.comments_output} with "
        f"{sum(len(entries) for entries in comments['races'].values())} comments "
        f"across {len(comments['races'])} races."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
