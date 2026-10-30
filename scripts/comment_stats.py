#!/usr/bin/env python3
"""Build the racer comments dataset from fetched ladder races."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Supports both `python3 scripts/build_stats.py` and `from scripts import comment_stats`.
try:
    from scripts import participant_stats
except ImportError:
    import participant_stats

COMMENTS_SCHEMA_VERSION = 2
MAX_COMMENT_LENGTH = 500
CHEST_COUNT_RANGE = (30, 300)
CHESTS_PER_HOUR_RANGE = (30, 175)

# A chest count is a run of exactly two or three digits.
CHEST_CANDIDATE = re.compile(r"(?<!\d)\d{2,3}(?!\d)")
# Text before the number ends in "/": it is the seed's total ("164/216").
TOTAL_BEFORE = re.compile(r"/\s*$")
# "<", ">" or "+" attached on either side makes it a bound ("<174", "170+").
BOUND_BEFORE = re.compile(r"[<>+]$")
BOUND_AFTER = re.compile(r"[<>+]")
# A comparative word right before the number ("more than 174", "sub100").
WORD_BEFORE = re.compile(r"\b(?:more than|less than|over|under|sub)\s*$", re.IGNORECASE)
# Followed by "CPH": a pace, not a count ("91cph").
CPH_AFTER = re.compile(r"\s?cph\b", re.IGNORECASE)
# Followed by "%" or a time unit ("92m", "100%", "190s").
UNIT_AFTER = re.compile(
    r"\s?(?:%|(?:m|mins?|minutes?|h|hrs?|hours?|s|secs?|seconds?)\b)", re.IGNORECASE
)
# Part of a clock time ("1:45").
CLOCK_BEFORE = re.compile(r"\d:$")
CLOCK_AFTER = re.compile(r":\d")

JsonObject = dict[str, Any]


class CommentDataError(RuntimeError):
    """Raised when entrant comments cannot be read as expected."""


def _is_chest_candidate(value: int, before: str, after: str) -> bool:
    """Apply the value range and the skip rules to one number."""
    low, high = CHEST_COUNT_RANGE
    if not low <= value <= high:
        return False
    return not (
        TOTAL_BEFORE.search(before)
        or BOUND_BEFORE.search(before)
        or BOUND_AFTER.match(after)
        or WORD_BEFORE.search(before)
        or CPH_AFTER.match(after)
        or UNIT_AFTER.match(after)
        or CLOCK_BEFORE.search(before)
        or CLOCK_AFTER.match(after)
    )


def parse_chest_count(text: str, finish_seconds: float | None) -> int | None:
    """Return the first chest count in the text that implies a plausible pace.

    A count is plausible when chests per hour, at the given finish time, falls
    inside CHESTS_PER_HOUR_RANGE. Without a finish time there is no count.
    """
    if finish_seconds is None:
        return None
    low, high = CHESTS_PER_HOUR_RANGE
    for match in CHEST_CANDIDATE.finditer(text):
        value = int(match.group())
        if not _is_chest_candidate(value, text[: match.start()], text[match.end() :]):
            continue
        if low <= value * 3600 / finish_seconds <= high:
            return value
    return None


def _finish_seconds(entrant: JsonObject) -> float | None:
    """Return a finisher's time in seconds; None for anyone who did not finish."""
    status = entrant.get("status")
    if not isinstance(status, dict) or status.get("value") != "done":
        return None
    return participant_stats.parse_finish_seconds(entrant.get("finish_time"))


def _race_comments(race: JsonObject) -> list[list[Any]]:
    entrants = race.get("entrants")
    if not isinstance(entrants, list):
        raise CommentDataError(
            f"Race {race.get('name', '<unnamed>')} is missing its entrants array"
        )

    comments = []
    for entrant in entrants:
        if not isinstance(entrant, dict):
            continue
        user = entrant.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        text = entrant.get("comment")
        if not isinstance(user_id, str) or not user_id or not isinstance(text, str):
            continue
        text = text.strip()[:MAX_COMMENT_LENGTH].strip()
        if text:
            comments.append(
                [user_id, text, parse_chest_count(text, _finish_seconds(entrant))]
            )
    return comments


def build_comments_payload(
    contexts: list[JsonObject],
    *,
    generated_at: datetime | None = None,
) -> JsonObject:
    """Map each included race with comments to its [user id, text, chest count] entries.

    The chest count is parsed from the text and is None when it has none.
    """
    ordered = sorted(
        contexts,
        key=lambda context: (context["started_at"], context["race"].get("name") or ""),
    )
    races: dict[str, list[list[Any]]] = {}
    for context in ordered:
        race = context["race"]
        comments = _race_comments(race)
        if comments:
            races[race["name"]] = comments

    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema_version": COMMENTS_SCHEMA_VERSION,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "races": races,
    }


write_compact_json = participant_stats.write_compact_json
