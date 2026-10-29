#!/usr/bin/env python3
"""Build the racer comments dataset from fetched ladder races."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Supports both `python3 scripts/build_stats.py` and `from scripts import comment_stats`.
try:
    from scripts import participant_stats
except ImportError:
    import participant_stats

COMMENTS_SCHEMA_VERSION = 1
MAX_COMMENT_LENGTH = 500

JsonObject = dict[str, Any]


class CommentDataError(RuntimeError):
    """Raised when entrant comments cannot be read as expected."""


def _race_comments(race: JsonObject) -> list[list[str]]:
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
            comments.append([user_id, text])
    return comments


def build_comments_payload(
    contexts: list[JsonObject],
    *,
    generated_at: datetime | None = None,
) -> JsonObject:
    """Map each included race with comments to its [user id, text] pairs."""
    ordered = sorted(
        contexts,
        key=lambda context: (context["started_at"], context["race"].get("name") or ""),
    )
    races: dict[str, list[list[str]]] = {}
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
