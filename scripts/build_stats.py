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
    from scripts import (
        comment_stats,
        participant_stats,
        pokemon_avatars,
        race_archive,
        racer_bios,
        schedule_archive,
        season_names,
        season_ratings,
    )
except ImportError:
    import comment_stats
    import participant_stats
    import pokemon_avatars
    import race_archive
    import racer_bios
    import schedule_archive
    import season_names
    import season_ratings


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
# A weekday-hour gets a band only with at least this many scheduled races.
# Must match BAND_MIN_SCHEDULED in site/app.js and the heatmap legend in site/index.html.
BAND_MIN_SCHEDULED = 5

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


def _check_entrant_count(race: JsonObject, entrants_count: int) -> None:
    """Refuse a race whose entrants array disagrees with its entrant count."""
    entrants = race.get("entrants")
    race_name = race.get("name", "<unnamed>")
    if not isinstance(entrants, list):
        raise DataError(f"Race {race_name} is missing its entrants array")
    if len(entrants) != entrants_count:
        raise DataError(
            f"Race {race_name} reports {entrants_count} entrants but returned "
            f"{len(entrants)} entrant records"
        )


def _date_range(started_at_values: list[datetime]) -> JsonObject | None:
    if not started_at_values:
        return None
    return {
        "start": min(started_at_values).isoformat().replace("+00:00", "Z"),
        "end": max(started_at_values).isoformat().replace("+00:00", "Z"),
    }


def _race_summary(records: list[JsonObject]) -> JsonObject:
    """Summarize finished races: their entries and their date range."""
    return _entry_summary([record["entrants_count"] for record in records]) | {
        "included_date_range": _date_range([record["started_at"] for record in records]),
    }


def _slot_hourly(slots: list[JsonObject]) -> list[JsonObject]:
    """Scheduled races per Eastern start hour, with racers per scheduled race."""
    by_hour: dict[int, list[JsonObject]] = {hour: [] for hour in range(24)}
    for slot in slots:
        by_hour[slot["local_started_at"].hour].append(slot)
    hourly = []
    for hour, items in by_hour.items():
        distribution = _distribution([slot["racers"] for slot in items])
        hourly.append(
            {
                "hour": hour,
                "scheduled_count": len(items),
                "started_count": sum(slot["started"] for slot in items),
                "median_racers": distribution["median"],
                "q1_racers": distribution["q1"],
                "q3_racers": distribution["q3"],
            }
        )
    return hourly


def _assign_bands(cells: list[JsonObject]) -> None:
    """Band each weekday-hour against the quartiles of its racers per scheduled race.

    Only cells with at least BAND_MIN_SCHEDULED scheduled races are banded or count
    toward the quartiles; the others get no band.
    """
    eligible = [cell for cell in cells if cell["scheduled_count"] >= BAND_MIN_SCHEDULED]
    quartiles = _distribution([cell["average_racers"] for cell in eligible])
    for cell in cells:
        if cell["scheduled_count"] < BAND_MIN_SCHEDULED:
            cell["band"] = None
        elif cell["average_racers"] >= quartiles["q3"]:
            cell["band"] = "prime"
        elif cell["average_racers"] <= quartiles["q1"]:
            cell["band"] = "quiet"
        else:
            cell["band"] = "normal"


def _slot_weekday_hourly(slots: list[JsonObject]) -> list[JsonObject]:
    """Scheduled races per weekday and Eastern start hour, banded."""
    cells: dict[tuple[int, int], list[JsonObject]] = {
        (weekday, hour): [] for weekday in range(7) for hour in range(24)
    }
    for slot in slots:
        local = slot["local_started_at"]
        cells[(local.weekday(), local.hour)].append(slot)
    output = []
    for (weekday, hour), items in cells.items():
        entrant_total = sum(slot["racers"] for slot in items)
        output.append(
            {
                "weekday": weekday,
                "weekday_label": WEEKDAY_LABELS[weekday],
                "hour": hour,
                "scheduled_count": len(items),
                "started_count": sum(slot["started"] for slot in items),
                "entrant_total": entrant_total,
                "average_racers": _average(entrant_total, len(items)),
            }
        )
    _assign_bands(output)
    return output


def _slot_summary(slots: list[JsonObject]) -> JsonObject:
    return {"hourly": _slot_hourly(slots), "weekday_hourly": _slot_weekday_hourly(slots)}


def _scheduled_days(
    joined: list[schedule_archive.JoinedSlot], archive_start: date, archive_end: date
) -> set[date]:
    """Return the archive's Eastern dates that hold a slot, pulled slots included."""
    days = set()
    for slot in joined:
        day = slot.started_at.astimezone(SCHEDULE_TIMEZONE).date()
        if archive_start <= day <= archive_end:
            days.add(day)
    return days


def _schedule_counted_through(
    schedule: list[JsonObject], newest_started_at: datetime
) -> datetime | None:
    """Return the latest slot start the schedule panels count, or None when none can.

    That is the earlier of a day before the newest race, whose rooms may still be open,
    and the latest slot with a race id, which guards against a stale mirror.
    """
    created = [schedule_archive.slot_time(row) for row in schedule if row["raceId"] is not None]
    if not created:
        return None
    return min(newest_started_at - schedule_archive.SETTLE, max(created))


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
    scheduled_days: set[date],
) -> list[JsonObject]:
    month_values: dict[str, list[int]] = defaultdict(list)
    scheduled_entries: Counter[str] = Counter()
    for record in records:
        local_date = record["local_started_at"].date()
        month_id = month_period(local_date)["id"]
        month_values[month_id].append(record["entrants_count"])
        if local_date in scheduled_days:
            scheduled_entries[month_id] += record["entrants_count"]
    scheduled_day_counts = Counter(month_period(day)["id"] for day in scheduled_days)

    monthly = []
    year, month = archive_start.year, archive_start.month
    while (year, month) <= (archive_end.year, archive_end.month):
        period = month_period(date(year, month, 1))
        month_start = date.fromisoformat(period["start"])
        month_end = date.fromisoformat(period["end"])
        scheduled_day_count = scheduled_day_counts[period["id"]]
        scheduled_day_entrant_total = scheduled_entries[period["id"]]
        monthly.append(
            period
            | {
                "is_complete": archive_start <= month_start
                and archive_end >= month_end,
                "scheduled_day_count": scheduled_day_count,
                "scheduled_day_entrant_total": scheduled_day_entrant_total,
            }
            | _entry_summary(month_values[period["id"]])
            | {
                "entries_per_day": _average(
                    scheduled_day_entrant_total, scheduled_day_count
                )
            }
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


def included_envelopes(envelopes: list[JsonObject]) -> list[JsonObject]:
    """Return the archive envelopes whose races pass the dashboard filters."""
    return [
        envelope for envelope in envelopes if classify_race(envelope["race"])[0] is not None
    ]


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
    schedule: list[JsonObject],
    modes: list[JsonObject],
    generated_at: datetime | None = None,
) -> JsonObject:
    """Filter and aggregate races and the ladder schedule into the page's JSON schema."""
    excluded_statuses: Counter[str] = Counter()
    excluded_modes: Counter[str] = Counter()
    invalid_records: Counter[str] = Counter()
    excluded_seeds: set[int] = set()
    contexts: list[JsonObject] = []
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
                seed = schedule_archive.seed_race_id(race.get("info"))
                if seed is not None:
                    excluded_seeds.add(seed)
            else:
                invalid_records[detail] += 1
            continue

        if context["mode"] == "unknown":
            unknown_mode_count += 1
        _check_entrant_count(race, context["entrants_count"])

        local_started_at = context["local_started_at"]
        period = quarter_period(local_started_at)
        record = {
            "started_at": context["started_at"],
            "local_started_at": local_started_at,
            "entrants_count": context["entrants_count"],
        }
        contexts.append(context)
        included_records.append(record)
        period_records[period["id"]].append(record)
        period_metadata[period["id"]] = period

    if not included_records:
        raise DataError("No races remained after filtering and validation")

    archive_start = min(record["local_started_at"].date() for record in included_records)
    archive_end = max(record["local_started_at"].date() for record in included_records)
    newest_started_at = max(record["started_at"] for record in included_records)
    counted_through = _schedule_counted_through(schedule, newest_started_at)

    joined = schedule_archive.join_races(schedule, modes, contexts, excluded_seeds)
    scheduled_days = _scheduled_days(joined, archive_start, archive_end)
    slot_records: list[JsonObject] = []
    period_slots: dict[str, list[JsonObject]] = defaultdict(list)
    pulled_count = 0
    for slot in joined:
        local_started_at = slot.started_at.astimezone(SCHEDULE_TIMEZONE)
        if (
            counted_through is None
            or slot.started_at > counted_through
            or local_started_at.date() < archive_start
        ):
            continue
        if slot.mode is None:
            raise DataError(
                f"Schedule slot {slot.row['id']} names mode {slot.row['mode']}, "
                "which is not listed"
            )
        if slot.pulled:
            pulled_count += 1
            continue
        period_id = quarter_period(local_started_at)["id"]
        if period_id not in period_metadata:
            raise DataError(
                f"Schedule slot {slot.row['id']} falls in {period_id}, "
                "which has no finished race"
            )
        slot_record = {
            "local_started_at": local_started_at,
            "racers": slot.race["entrants_count"] if slot.race else 0,
            "started": slot.race is not None,
        }
        slot_records.append(slot_record)
        period_slots[period_id].append(slot_record)

    joined_races = {slot.race["race"]["name"] for slot in joined if slot.race}
    unscheduled_race_count = 0
    if counted_through is not None:
        unscheduled_race_count = sum(
            context["started_at"] <= counted_through
            and context["race"]["name"] not in joined_races
            for context in contexts
        )

    overall = _race_summary(included_records)
    periods = []
    for period_id in sorted(period_records):
        records = period_records[period_id]
        periods.append(
            period_metadata[period_id]
            | _period_coverage(
                period_metadata[period_id], records, archive_start, archive_end
            )
            | _race_summary(records)
            | _slot_summary(period_slots[period_id])
        )

    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    included_count = overall["race_count"]

    return {
        "schema_version": 9,
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
            "schedule_counted_through": (
                counted_through.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if counted_through
                else None
            ),
        },
        **_slot_summary(slot_records),
        "periods": periods,
        "monthly": _monthly_turnout(
            included_records, archive_start, archive_end, scheduled_days
        ),
        "diagnostics": {
            "excluded_by_status": dict(sorted(excluded_statuses.items())),
            "excluded_by_mode": dict(sorted(excluded_modes.items())),
            "invalid_records": dict(sorted(invalid_records.items())),
            "unknown_mode_count": unknown_mode_count,
            "schedule": {
                "scheduled_count": len(slot_records),
                "started_count": sum(slot["started"] for slot in slot_records),
                "pulled_count": pulled_count,
                "unscheduled_race_count": unscheduled_race_count,
            },
        },
    }


def build(
    archive_dir: Path = race_archive.ARCHIVE_DIR,
    output_path: Path = OUTPUT_PATH,
    participants_output_path: Path = PARTICIPANTS_OUTPUT_PATH,
    comments_output_path: Path = COMMENTS_OUTPUT_PATH,
    pokemon_dir: Path = pokemon_avatars.POKEMON_DIR,
    ledger_dir: Path | None = None,
    season_names_path: Path = season_names.NAMES_PATH,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    """Build and write the three datasets. It reads the season ledgers in `ledger_dir`,
    by default the one beside the archive, and never writes one."""
    envelopes = race_archive.load_races(archive_dir)
    races = [envelope["race"] for envelope in envelopes]
    if not races:
        raise DataError(
            f"Archive {archive_dir} has no races; run scripts/race_archive.py first"
        )
    schedule = schedule_archive.load_schedule(archive_dir)
    modes = schedule_archive.load_modes(archive_dir)
    if not schedule or not modes:
        raise DataError(
            f"Archive {archive_dir} has no ladder schedule; "
            "run scripts/schedule_archive.py first"
        )
    generated_at = datetime.now(UTC)
    contexts = included_race_contexts(races)
    stats = aggregate_races(
        races, schedule=schedule, modes=modes, generated_at=generated_at
    )
    participants = participant_stats.build_participant_payload(
        contexts,
        source_url=race_archive.SOURCE_URL,
        timezone_label=stats["timezone"]["label"],
        timezone_name=SCHEDULE_TIMEZONE_NAME,
        generated_at=generated_at,
        current_names=race_archive.load_racer_names(archive_dir),
        envelopes=included_envelopes(envelopes),
        avatar_observations=race_archive.load_racer_avatars(archive_dir),
        bios=racer_bios.load_racer_bios(archive_dir),
        pokemon=pokemon_avatars.load_catalogue(pokemon_dir),
        schedule=schedule,
        ledgers=season_ratings.load_ledgers(
            season_ratings.ledger_dir_for(archive_dir) if ledger_dir is None else ledger_dir
        ),
        schedule_modes=modes,
        season_names=season_names.load(season_names_path),
    )
    comments = comment_stats.build_comments_payload(
        contexts, generated_at=generated_at
    )
    race_archive.write_json_atomic(stats, output_path)
    race_archive.write_json_atomic(participants, participants_output_path)
    race_archive.write_json_atomic(comments, comments_output_path)
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
    parser.add_argument(
        "--pokemon",
        type=Path,
        default=pokemon_avatars.POKEMON_DIR,
        help=f"Pokémon sprites and pokédex (default: {pokemon_avatars.POKEMON_DIR})",
    )
    parser.add_argument(
        "--lock-seasons",
        action="store_true",
        help="after building, lock every finished season's ratings into a ledger in "
        f"{season_ratings.LEDGER_DIRECTORY}/ beside the archive (the data bot passes this)",
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
            args.pokemon,
        )
        locks = (
            season_ratings.lock_seasons(
                args.archive,
                season_ratings.ledger_dir_for(args.archive),
                participants,
                datetime.now(UTC),
            )
            if args.lock_seasons
            else []
        )
    except (
        DataError,
        race_archive.ArchiveError,
        pokemon_avatars.PokedexError,
        participant_stats.ParticipantDataError,
        comment_stats.CommentDataError,
        season_ratings.SeasonLedgerError,
        season_names.SeasonNamesError,
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
    for season, reason in locks:
        if reason is None:
            print(f"Locked the ratings of season {season}.")
        else:
            print(f"Season {season} stays open: {reason}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
