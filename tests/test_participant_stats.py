import json
import re
import tempfile
import unittest
from datetime import UTC, datetime
from unittest import mock
from pathlib import Path

from scripts import avatar_mirror, build_stats, highlights, participant_stats, pokemon_avatars


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://racetime.gg/alttpr-ladder/races/data"
GENERATED_AT = datetime(2026, 9, 17, 12, tzinfo=UTC)


def entrant(user_id, *, status="done", place=None, finish_time=None, user=None):
    return {
        "user": {"id": user_id, "name": user_id} if user is None else user,
        "status": {"value": status},
        "place": place,
        "finish_time": finish_time,
    }


def race(
    name,
    *,
    entrants,
    started_at="2026-09-17T20:00:00.000Z",
    ended_at="2026-09-17T22:00:00.000Z",
    mode="simple",
    info=None,
    status="finished",
    entrants_count=None,
):
    return {
        "name": name,
        "status": {"value": status},
        "started_at": started_at,
        "ended_at": ended_at,
        "cancelled_at": None,
        "entrants_count": len(entrants) if entrants_count is None else entrants_count,
        "entrants": entrants,
        "info": info or f"Step Ladder Series - [{mode}] - Open",
    }


def placed_race(name, order, **kwargs):
    entrants = [
        entrant(user_id, place=index + 1, finish_time=f"PT{index + 10}M")
        for index, user_id in enumerate(order)
    ]
    return race(name, entrants=entrants, **kwargs)


def payload(races, current_names=None, avatar_observations=None, bios=None, pokemon=None):
    # Each race file was fetched when its race ended.
    envelopes = [{"fetched_at": item["ended_at"], "race": item} for item in races]
    return participant_stats.build_participant_payload(
        build_stats.included_race_contexts(races),
        source_url=SOURCE_URL,
        timezone_label="Eastern Time",
        timezone_name=build_stats.SCHEDULE_TIMEZONE_NAME,
        generated_at=GENERATED_AT,
        current_names=current_names,
        envelopes=envelopes,
        avatar_observations=avatar_observations,
        bios=bios,
        pokemon=pokemon,
    )


def leaderboard(result, mode):
    for board in result["leaderboards"]:
        if board["mode"] == mode:
            return board["rows"]
    raise AssertionError(f"No leaderboard for mode {mode!r}")


def row_for(result, mode, name):
    index = next(
        position
        for position, player in enumerate(result["players"])
        if player["name"] == name
    )
    for row in leaderboard(result, mode):
        if row["p"] == index:
            return row
    raise AssertionError(f"No {mode!r} row for {name!r}")


def ranked_names(result, mode):
    return [result["players"][row["p"]]["name"] for row in leaderboard(result, mode)]


class FinishTimeTests(unittest.TestCase):
    def test_parses_hours_minutes_and_seconds(self):
        self.assertEqual(participant_stats.parse_finish_time("PT1H23M45S"), 5025)

    def test_parses_fractional_seconds_by_rounding(self):
        self.assertEqual(participant_stats.parse_finish_time("PT45.4S"), 45)
        self.assertEqual(participant_stats.parse_finish_time("PT45.6S"), 46)

    def test_exact_seconds_keep_the_fraction(self):
        self.assertAlmostEqual(participant_stats.parse_finish_seconds("PT1M0.61S"), 60.61)
        self.assertIsNone(participant_stats.parse_finish_seconds(None))

    def test_parses_day_component(self):
        self.assertEqual(participant_stats.parse_finish_time("P1DT2H"), 93600)

    def test_missing_time_is_none(self):
        self.assertIsNone(participant_stats.parse_finish_time(None))

    def test_rejects_malformed_duration(self):
        for value in ("", "P", "PT", "1H23M", "garbage", "PT1X"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    participant_stats.ParticipantDataError, "ISO 8601 duration"
                ):
                    participant_stats.parse_finish_time(value)

    def test_rejects_non_string_duration(self):
        with self.assertRaisesRegex(
            participant_stats.ParticipantDataError, "is not a string"
        ):
            participant_stats.parse_finish_time(45)


class EntrantNormalizationTests(unittest.TestCase):
    def test_maps_known_and_unknown_statuses(self):
        result = payload(
            [
                race(
                    "statuses",
                    entrants=[
                        entrant("done-racer", place=1, finish_time="PT10M"),
                        entrant("dnf-racer", status="dnf"),
                        entrant("dq-racer", status="dq"),
                        entrant("odd-racer", status="in_progress"),
                    ],
                )
            ]
        )
        codes = {
            result["players"][entry[0]]["name"]: entry[2]
            for entry in result["races"][0]["e"]
        }
        self.assertEqual(codes["done-racer"], 0)
        self.assertEqual(codes["dnf-racer"], 1)
        self.assertEqual(codes["dq-racer"], 2)
        self.assertEqual(codes["odd-racer"], 3)
        self.assertEqual(
            result["diagnostics"]["status_counts"],
            {"done": 1, "dnf": 1, "dq": 1, "in_progress": 1},
        )

    def test_unidentified_entrant_is_dropped_but_still_counted(self):
        result = payload(
            [
                race(
                    "anonymous",
                    entrants=[
                        entrant("known", place=1, finish_time="PT10M"),
                        entrant("ignored", user={"id": None, "name": "Ghost"}),
                    ],
                )
            ]
        )
        self.assertEqual(result["races"][0]["n"], 2)
        self.assertEqual(len(result["races"][0]["e"]), 1)
        self.assertEqual(result["diagnostics"]["unidentified_entry_count"], 1)
        self.assertEqual(len(result["players"]), 1)

    def test_falls_back_to_user_id_when_name_is_missing(self):
        result = payload(
            [
                race(
                    "nameless",
                    entrants=[
                        entrant("abc123", user={"id": "abc123"}),
                        entrant("other", place=1),
                    ],
                )
            ]
        )
        self.assertIn(("abc123", "abc123"), [(p["id"], p["name"]) for p in result["players"]])

    def test_latest_race_name_wins_without_a_current_name(self):
        result = payload(
            [
                placed_race(
                    "early",
                    ["ana"],
                    started_at="2026-03-01T20:00:00.000Z",
                )
                | {"entrants": [entrant("ana", user={"id": "ana", "name": "Old Ana"})]},
                placed_race("late", ["ana"], started_at="2026-03-02T20:00:00.000Z"),
            ]
        )
        self.assertEqual([player["name"] for player in result["players"]], ["ana"])

    def test_current_name_overrides_race_names(self):
        result = payload(
            [placed_race("solo", ["ana", "ben"])],
            current_names={"ana": "Renamed Ana", "absent": "Not In Races"},
        )
        self.assertEqual(
            [(player["id"], player["name"]) for player in result["players"]],
            [("ben", "ben"), ("ana", "Renamed Ana")],
        )

    def test_rejects_duplicate_entrant_identity(self):
        with self.assertRaisesRegex(
            participant_stats.ParticipantDataError, "duplicate entrant user ID"
        ):
            payload([race("dupes", entrants=[entrant("same"), entrant("same")])])

    def test_rejects_invalid_place(self):
        with self.assertRaisesRegex(
            participant_stats.ParticipantDataError, "not a positive integer"
        ):
            payload([race("bad-place", entrants=[entrant("racer", place=0)])])


    def test_highlights_status_codes_match(self):
        self.assertEqual(highlights.DONE_STATUS_CODE, participant_stats.STATUS_CODES["done"])
        self.assertEqual(highlights.DNF_STATUS_CODE, participant_stats.STATUS_CODES["dnf"])


class LeaderboardTests(unittest.TestCase):
    def test_counts_medals_per_mode_and_overall(self):
        result = payload(
            [
                placed_race("r1", ["ana", "ben", "cid"], mode="keys"),
                placed_race("r2", ["ben", "ana", "cid"], mode="fun"),
            ]
        )
        self.assertEqual(result["modes"], ["fun", "keys"])

        keys_row = row_for(result, "keys", "ana")
        self.assertEqual((keys_row["first"], keys_row["second"]), (1, 0))

        overall_row = row_for(result, "all", "ana")
        self.assertEqual(
            (overall_row["first"], overall_row["second"], overall_row["races"]),
            (1, 1, 2),
        )
        self.assertEqual(overall_row["podiums"], 2)

    def test_sorts_by_medal_then_races_then_name(self):
        races = [
            placed_race("g1", ["aaa", "fill-1", "fill-2"]),
            placed_race("g2", ["aaa", "fill-1", "fill-2"]),
            placed_race("g3", ["bbb", "fill-1", "fill-2"]),
            placed_race("g4", ["fill-1", "bbb", "fill-2"]),
            placed_race("g5", ["ccc", "fill-1", "fill-2"]),
            placed_race("g6", ["fill-1", "fill-2", "ccc"]),
            placed_race("g7", ["ddd", "fill-1", "fill-2"]),
            placed_race("g8", ["fill-1", "fill-2", "fill-3", "ddd"]),
            placed_race("g9", ["fill-1", "fill-2", "fill-3", "ddd"]),
            placed_race("g10", ["eee", "fill-1", "fill-2"]),
            placed_race("g11", ["fill-1", "fill-2", "fill-3", "eee"]),
            placed_race("g12", ["mmm", "fill-1", "fill-2"]),
            placed_race("g13", ["fill-1", "fill-2", "fill-3", "mmm"]),
            placed_race("g14", ["zzz", "fill-1", "fill-2"]),
            placed_race("g15", ["fill-1", "fill-2", "fill-3", "zzz"]),
        ]
        tracked = {"aaa", "bbb", "ccc", "ddd", "eee", "mmm", "zzz"}
        order = [name for name in ranked_names(payload(races), "all") if name in tracked]
        self.assertEqual(order, ["aaa", "bbb", "ccc", "ddd", "eee", "mmm", "zzz"])

    def test_time_stats_cover_finishers_only(self):
        result = payload(
            [
                race(
                    "times-1",
                    entrants=[
                        entrant("racer", place=1, finish_time="PT10M"),
                        entrant("rival", place=2, finish_time="PT20M"),
                    ],
                ),
                race(
                    "times-2",
                    started_at="2026-09-18T20:00:00.000Z",
                    entrants=[
                        entrant("racer", place=1, finish_time="PT30M"),
                        entrant("rival", status="dnf"),
                    ],
                ),
            ]
        )
        racer = row_for(result, "all", "racer")
        self.assertEqual(racer["min_seconds"], 600)
        self.assertEqual(racer["max_seconds"], 1800)
        self.assertEqual(racer["median_seconds"], 1200)
        self.assertEqual(racer["finishes"], 2)

        rival = row_for(result, "all", "rival")
        self.assertEqual((rival["races"], rival["finishes"], rival["dnf"]), (2, 1, 1))
        self.assertEqual(rival["min_seconds"], 1200)

    def test_time_stats_are_null_without_finishes(self):
        result = payload(
            [
                race(
                    "dnf-only",
                    entrants=[
                        entrant("quitter", status="dnf"),
                        entrant("banned", status="dq"),
                    ],
                )
            ]
        )
        quitter = row_for(result, "all", "quitter")
        self.assertIsNone(quitter["median_seconds"])
        self.assertIsNone(quitter["min_seconds"])
        self.assertIsNone(quitter["max_seconds"])
        self.assertIsNone(quitter["best_place"])
        self.assertEqual((quitter["dnf"], quitter["dq"]), (1, 0))
        self.assertEqual(row_for(result, "all", "banned")["dq"], 1)

    def test_finisher_without_place_is_not_a_medalist(self):
        result = payload(
            [
                race(
                    "no-place",
                    entrants=[
                        entrant("ghost", finish_time="PT10M"),
                        entrant("solid", place=1, finish_time="PT11M"),
                    ],
                )
            ]
        )
        ghost = row_for(result, "all", "ghost")
        self.assertEqual((ghost["finishes"], ghost["first"], ghost["dnf"]), (0, 0, 1))
        self.assertEqual(result["diagnostics"]["finishes_without_place"], 1)


class PayloadShapeTests(unittest.TestCase):
    def test_races_are_ordered_oldest_first(self):
        result = payload(
            [
                placed_race("late", ["ana"], started_at="2026-03-02T20:00:00.000Z"),
                placed_race("early", ["ana"], started_at="2026-03-01T20:00:00.000Z"),
                placed_race("middle", ["ana"], started_at="2026-03-01T23:00:00.000Z"),
            ]
        )
        self.assertEqual(
            [race_row["r"] for race_row in result["races"]],
            ["early", "middle", "late"],
        )

    def test_records_eastern_start_hour_on_both_sides_of_dst(self):
        result = payload(
            [
                placed_race(
                    "winter",
                    ["ana"],
                    started_at="2026-01-15T03:30:00.000Z",
                    ended_at="2026-01-15T05:30:00.000Z",
                ),
                placed_race(
                    "summer",
                    ["ana"],
                    started_at="2026-07-15T03:30:00.000Z",
                    ended_at="2026-07-15T05:30:00.000Z",
                ),
            ]
        )
        self.assertEqual(
            [(race_row["r"], race_row["d"], race_row["h"]) for race_row in result["races"]],
            [("winter", "2026-01-14", 22), ("summer", "2026-07-14", 23)],
        )

    def test_filtering_matches_the_dashboard_aggregate(self):
        races = [
            placed_race("keep-1", ["ana", "ben"]),
            placed_race("keep-2", ["ana", "ben"], mode="keys"),
            placed_race("ladder-test", ["ana", "ben"], mode="ladder test"),
            race("cancelled", entrants=[entrant("ana")], status="cancelled"),
        ]
        stats = build_stats.aggregate_races(
            races, schedule=[], modes=[], generated_at=GENERATED_AT
        )
        result = payload(races)
        self.assertEqual(
            len(result["races"]), stats["source"]["included_race_count"]
        )
        self.assertEqual(
            result["diagnostics"]["entry_count"]
            + result["diagnostics"]["unidentified_entry_count"],
            stats["source"]["included_entrant_count"],
        )

    def test_requires_at_least_one_race(self):
        with self.assertRaisesRegex(
            participant_stats.ParticipantDataError, "No races were supplied"
        ):
            payload([])

    def test_omits_sensitive_racetime_fields(self):
        result = payload(
            [
                race(
                    "private",
                    entrants=[
                        {
                            "user": {
                                "id": "abc123",
                                "name": "Visible",
                                "full_name": "Visible#1234",
                                "discriminator": "1234",
                                "avatar": "https://example.test/a.png",
                                "pronouns": "they/them",
                                "flair": "moderator",
                                "twitch_name": "visible",
                                "twitch_channel": "https://twitch.tv/visible",
                            },
                            "status": {"value": "done"},
                            "place": 1,
                            "finish_time": "PT10M",
                            "score": 833,
                            "score_change": 12,
                            "comment": "good race",
                        }
                    ],
                )
            ]
        )
        serialized = json.dumps(result)
        for forbidden in (
            "full_name",
            "discriminator",
            "pronouns",
            "flair",
            "twitch_name",
            "twitch_channel",
            "comment",
            "score",
            "Visible#1234",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)
        self.assertEqual(
            [(player["id"], player["name"]) for player in result["players"]],
            [("abc123", "Visible")],
        )
        self.assertNotIn("a", result["players"][0])

    def test_writes_compact_json_atomically(self):
        result = payload([placed_race("solo", ["ana", "ben"])])
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "nested" / "participants.json"
            participant_stats.write_compact_json(result, output_path)
            text = output_path.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(result, separators=(",", ":")) + "\n")
        self.assertEqual(
            json.loads(text)["schema_version"],
            participant_stats.PARTICIPANT_SCHEMA_VERSION,
        )
        self.assertTrue(text.endswith("\n"))


class SchemaThreeTests(unittest.TestCase):
    def avatar_races(self):
        def with_avatar(user_id, avatar):
            return entrant(
                user_id,
                place=1 if user_id == "ana" else 2,
                finish_time="PT10M" if user_id == "ana" else "PT11M",
                user={"id": user_id, "name": user_id, "avatar": avatar},
            )

        return [
            race(
                "early",
                started_at="2026-03-01T20:00:00.000Z",
                ended_at="2026-03-01T22:00:00.000Z",
                entrants=[
                    with_avatar("ana", "https://racetime.gg/media/old.png"),
                    with_avatar("ben", "https://evil.example/x.png"),
                ],
            ),
            race(
                "late",
                started_at="2026-03-02T20:00:00.000Z",
                ended_at="2026-03-02T22:00:00.000Z",
                entrants=[
                    with_avatar("ana", "https://racetime.gg/media/new-1.jpg"),
                    with_avatar("ben", "https://racetime.gg/media/../x.png"),
                ],
            ),
        ]

    @staticmethod
    def avatars(result):
        return {player["name"]: player.get("a") for player in result["players"]}

    def test_avatar_is_the_mirror_name_of_the_latest_race_avatar(self):
        result = payload(self.avatar_races())
        self.assertEqual(
            self.avatars(result),
            {
                "ana": avatar_mirror.mirror_name("https://racetime.gg/media/new-1.jpg"),
                "ben": None,
            },
        )
        self.assertRegex(self.avatars(result)["ana"], r"^[0-9a-f]{16}\.jpg$")

    def test_off_origin_latest_avatar_drops_an_older_one(self):
        races = self.avatar_races()
        races[1]["entrants"][0]["user"]["avatar"] = "https://evil.example/x.png"
        result = payload(races)
        self.assertNotIn("a", next(p for p in result["players"] if p["id"] == "ana"))

    def test_a_newer_leaderboard_observation_beats_the_latest_race(self):
        url = "https://racetime.gg/media/fresh.png"
        result = payload(
            self.avatar_races(), avatar_observations={"ana": [url, "2026-03-05T00:00:00Z"]}
        )
        self.assertEqual(self.avatars(result)["ana"], avatar_mirror.mirror_name(url))

    def test_an_older_leaderboard_observation_loses_to_the_latest_race(self):
        url = "https://racetime.gg/media/stale.png"
        result = payload(
            self.avatar_races(), avatar_observations={"ana": [url, "2026-03-01T00:00:00Z"]}
        )
        self.assertEqual(
            self.avatars(result)["ana"],
            avatar_mirror.mirror_name("https://racetime.gg/media/new-1.jpg"),
        )

    def test_a_newer_null_observation_removes_the_avatar(self):
        result = payload(
            self.avatar_races(), avatar_observations={"ana": [None, "2026-03-05T00:00:00Z"]}
        )
        self.assertIsNone(self.avatars(result)["ana"])

    def test_percent_encoded_avatar_is_published(self):
        races = self.avatar_races()
        races[1]["entrants"][1]["user"]["avatar"] = (
            "https://racetime.gg/media/Petit_chat_pott%C3%A9.jpg"
        )
        self.assertRegex(self.avatars(payload(races))["ben"], r"^[0-9a-f]{16}\.jpg$")

    def test_observations_for_unknown_racers_add_no_player(self):
        result = payload(
            self.avatar_races(),
            avatar_observations={
                "ghost": ["https://racetime.gg/media/g.png", "2026-03-05T00:00:00Z"]
            },
        )
        self.assertEqual({player["id"] for player in result["players"]}, {"ana", "ben"})

    def test_payload_has_rating_fields_and_int_rating_after(self):
        result = payload(
            [
                placed_race("one", ["ana", "ben", "cid"], info="Step Ladder Series - [keys] - Open"),
                placed_race(
                    "two",
                    ["ben", "ana"],
                    started_at="2026-09-18T20:00:00.000Z",
                    info="Step Ladder Series - [simple]",
                ),
            ]
        )
        self.assertEqual(result["schema_version"], 11)
        for player in result["players"]:
            for key in ("rating", "mu", "sigma", "finishes", "mode_ratings", "badges",
                        "progress", "pbs", "streaks", "best_preset"):
                self.assertIn(key, player)
            self.assertNotIn("rd", player)
            self.assertNotIn("provisional", player)
        for race_row in result["races"]:
            for entry in race_row["e"]:
                self.assertEqual(len(entry), 6)
                self.assertIsInstance(entry[4], int)
                self.assertIsInstance(entry[5], int)
        self.assertEqual(result["races"][1]["p"], None)
        self.assertEqual(result["diagnostics"]["races_without_preset"], 1)

    def test_mode_ratings_cover_only_the_modes_a_racer_raced(self):
        result = payload(
            [
                placed_race("one", ["ana", "ben", "cid"], info="Step Ladder Series - [keys] - Open"),
                placed_race(
                    "two",
                    ["ben", "ana"],
                    started_at="2026-09-18T20:00:00.000Z",
                    info="Step Ladder Series - [simple]",
                ),
                placed_race(
                    "three",
                    ["ana", "ben"],
                    started_at="2026-09-19T20:00:00.000Z",
                    info="Step Ladder Series - [simple]",
                ),
            ]
        )
        by_id = {player["id"]: player for player in result["players"]}
        modes = {row["m"] for row in result["races"]}
        self.assertEqual(len(modes), 2)
        self.assertEqual(list(by_id["cid"]["mode_ratings"]), [str(result["races"][0]["m"])])
        self.assertEqual(set(by_id["ana"]["mode_ratings"]), {str(mode) for mode in modes})
        last = {}
        for race_row in result["races"]:
            for entry in race_row["e"]:
                last[(entry[0], race_row["m"])] = entry[5]
        index = {player["id"]: i for i, player in enumerate(result["players"])}
        for player_id, player in by_id.items():
            self.assertEqual(
                list(player["mode_ratings"]), sorted(player["mode_ratings"], key=int)
            )
            for key, fields in player["mode_ratings"].items():
                self.assertEqual(set(fields), {"rating", "mu", "sigma", "finishes"})
                self.assertEqual(fields["rating"], last[(index[player_id], int(key))])
        simple = str(result["races"][1]["m"])
        self.assertEqual(by_id["ana"]["mode_ratings"][simple]["finishes"], 2)
        self.assertEqual(by_id["ana"]["finishes"], 3)

    def test_presets_are_sorted_by_mode_then_name_and_indexed_by_races(self):
        result = payload(
            [
                placed_race("a", ["ana", "ben"], mode="keys",
                            info="Step Ladder Series - [keys] - beta"),
                placed_race("b", ["ana", "ben"], mode="fun",
                            info="Step Ladder Series - [fun] - Zed",
                            started_at="2026-09-18T20:00:00.000Z"),
                placed_race("c", ["ana", "ben"], mode="keys",
                            info="Step Ladder Series - [keys] - Alpha",
                            started_at="2026-09-19T20:00:00.000Z"),
            ]
        )
        self.assertEqual(
            [(preset["m"], preset["n"]) for preset in result["presets"]],
            [(0, "Zed"), (1, "Alpha"), (1, "beta")],
        )
        self.assertEqual([race_row["p"] for race_row in result["races"]], [2, 0, 1])
        self.assertEqual(result["diagnostics"]["races_without_preset"], 0)

    def test_comment_text_is_never_published(self):
        result = payload(
            [
                race(
                    "chatty",
                    entrants=[
                        {**entrant("ana", place=1, finish_time="PT10M"), "comment": "secret"},
                        entrant("ben", place=2, finish_time="PT11M"),
                    ],
                )
            ]
        )
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn('"comment"', json.dumps(result))

    def test_a_comment_that_is_not_blank_counts_toward_yap(self):
        result = payload(
            [
                race(
                    "chatty",
                    entrants=[
                        {**entrant("ana", place=1, finish_time="PT10M"), "comment": "gg"},
                        {**entrant("ben", place=2, finish_time="PT11M"), "comment": "   "},
                    ],
                )
            ]
        )
        progress = {player["id"]: player["progress"] for player in result["players"]}
        self.assertEqual(progress["ana"]["yap"], 1)
        self.assertEqual(progress["ben"]["yap"], 0)

    def test_alphabetical_badges_keep_the_names_of_race_time(self):
        entrants = [
            entrant(
                f"id-{name}",
                place=index + 1,
                finish_time=f"PT{index + 10}M",
                user={"id": f"id-{name}", "name": name},
            )
            for index, name in enumerate(["ana", "ben", "cid", "dee", "eve"])
        ]
        result = payload([race("abc", entrants=entrants)], current_names={"id-ana": "Zed"})
        self.assertIn("Zed", [player["name"] for player in result["players"]])
        for player in result["players"]:
            self.assertIn("step_letter", player["badges"])
        self.assertNotIn('"ana"', json.dumps(result))


def avatar_change_races():
    """Four racers over three races. ana, ben and dan change avatar; cid never does.

    The races end at 22:00Z, except the one that shows ben's new avatar, which ends at
    02:00Z the next day: still the previous evening in Eastern Time.
    """
    def with_avatar(user_id, place, avatar):
        return entrant(
            user_id,
            place=place,
            finish_time=f"PT{10 + place}M",
            user={"id": user_id, "name": user_id, "avatar": avatar},
        )

    def day(name, start, end, avatars):
        return race(
            name,
            started_at=start,
            ended_at=end,
            entrants=[with_avatar(u, i + 1, a) for i, (u, a) in enumerate(avatars)],
        )

    m = "https://racetime.gg/media/"
    return [
        day(
            "one",
            "2026-03-01T20:00:00.000Z",
            "2026-03-01T22:00:00.000Z",
            [("ana", f"{m}a1.png"), ("ben", f"{m}b1.png"), ("cid", f"{m}c1.png"), ("dan", f"{m}d1.png")],
        ),
        day(
            "two",
            "2026-03-02T20:00:00.000Z",
            "2026-03-02T22:00:00.000Z",
            [("ana", f"{m}a2.png"), ("ben", f"{m}b1.png"), ("cid", f"{m}c1.png"), ("dan", f"{m}d1.png")],
        ),
        day(
            "three",
            "2026-03-03T00:30:00.000Z",
            "2026-03-04T02:00:00.000Z",
            [("ana", f"{m}a2.png"), ("ben", f"{m}b2.png"), ("cid", f"{m}c1.png"), ("dan", f"{m}d2.png")],
        ),
    ]


class AvatarChangesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = pokemon_avatars.load_catalogue()

    def changes(self, **kwargs):
        result = payload(avatar_change_races(), **kwargs)
        names = [player["name"] for player in result["players"]]
        return result, [[names[index], date] for index, date in result["avatar_changes"]]

    def test_changes_are_newest_first_with_eastern_dates(self):
        _result, changes = self.changes()
        # ben and dan changed at 02:00Z on 03-04, which is 21:00 on 03-03 in Eastern Time.
        # They tie, so the lower player index (ben) goes first; ana changed on 03-02.
        self.assertEqual(
            changes, [["ben", "2026-03-03"], ["dan", "2026-03-03"], ["ana", "2026-03-02"]]
        )

    def test_a_racer_with_one_avatar_only_is_absent(self):
        _result, changes = self.changes()
        self.assertNotIn("cid", [name for name, _date in changes])

    def test_indexes_point_at_players(self):
        result, _changes = self.changes()
        for index, date in result["avatar_changes"]:
            self.assertIn(index, range(len(result["players"])))
            self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")

    def test_a_favourite_hides_the_upload_change(self):
        # ana's upload changed on 03-02, but her favourite Pokémon replaces it.
        bios = {"ana": ["My favourite pokemon is: Pikachu", "2026-03-05T00:00:00Z", None, []]}
        result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        self.assertEqual({player["id"]: player.get("a") for player in result["players"]}["ana"], "pokemon-25.png")
        self.assertEqual([name for name, _date in changes], ["ben", "dan"])

    def test_a_random_favourite_hides_the_upload_change(self):
        bios = {"ana": ["fav pokemon: random", "2026-03-05T00:00:00Z", None, []]}
        result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        avatar = {player["id"]: player.get("a") for player in result["players"]}["ana"]
        self.assertIsNotNone(pokemon_avatars.sprite_path(avatar))
        self.assertEqual([name for name, _date in changes], ["ben", "dan"])

    def test_naming_random_is_listed_at_its_eastern_date(self):
        bios = {"ana": ["fav pokemon: random", "2026-03-08T00:00:00Z", "2026-03-07T15:00:00Z", [["hi", None]]]}
        _result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        self.assertEqual(changes[0], ["ana", "2026-03-07"])

    def test_a_favourite_holder_with_a_changed_upload_but_the_same_favourite_is_not_listed(self):
        pikachu = "My favourite pokemon is: Pikachu"
        bios = {"ana": [pikachu + "!", "2026-03-09T00:00:00Z", "2026-03-08T00:00:00Z", [[pikachu, None]]]}
        _result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        self.assertNotIn("ana", [name for name, _date in changes])

    def test_an_added_favourite_is_listed_at_its_eastern_date(self):
        # 03:00Z on 03-07 is 22:00 on 03-06 in Eastern Time (still standard time).
        bios = {"cid": ["My favourite pokemon is: Pikachu", "2026-03-08T00:00:00Z", "2026-03-07T03:00:00Z", [[None, None]]]}
        result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        self.assertEqual(changes[0], ["cid", "2026-03-06"])
        avatars = {player["id"]: player.get("a") for player in result["players"]}
        self.assertEqual(avatars["cid"], "pokemon-25.png")

    def test_a_removed_favourite_is_listed(self):
        bios = {"cid": ["no favourite", "2026-03-08T00:00:00Z", "2026-03-07T15:00:00Z", [["My favourite pokemon is: Pikachu", None]]]}
        _result, changes = self.changes(bios=bios, pokemon=self.catalogue)
        self.assertEqual(changes[0], ["cid", "2026-03-07"])

    def test_a_removed_upload_with_a_random_pokemon_is_listed(self):
        races = avatar_change_races()
        # cid's avatar is removed in the last race, so a random Pokémon takes its place.
        for item in races[-1]["entrants"]:
            if item["user"]["id"] == "cid":
                item["user"]["avatar"] = None
        result = payload(races, pokemon=self.catalogue)
        names = [player["name"] for player in result["players"]]
        changes = [[names[index], date] for index, date in result["avatar_changes"]]
        self.assertIn(["cid", "2026-03-03"], changes)
        avatars = {player["id"]: player.get("a") for player in result["players"]}
        self.assertTrue(avatars["cid"].startswith("pokemon-"))

    def test_a_random_pokemon_alone_is_never_listed(self):
        races = avatar_change_races()
        for item in races:
            for entry in item["entrants"]:
                entry["user"]["avatar"] = None
        result = payload(races, pokemon=self.catalogue)
        self.assertEqual(result["avatar_changes"], [])

    def test_a_leaderboard_sighting_dates_a_change(self):
        url = "https://racetime.gg/media/cid2.png"
        result, changes = self.changes(avatar_observations={"cid": [url, "2026-03-09T05:00:00Z"]})
        self.assertEqual(changes[0], ["cid", "2026-03-09"])

    def test_racers_missing_from_the_players_are_excluded(self):
        # A ghost with a mirrorable racetime avatar and a change time, but no races the
        # payload keeps, must be skipped rather than looked up among the players.
        url = "https://racetime.gg/media/g.png"
        real = avatar_mirror.avatar_change_times

        def with_ghost(envelopes, observations):
            return real(envelopes, observations) | {"ghost": datetime(2026, 3, 9, tzinfo=UTC)}

        with mock.patch.object(participant_stats.avatar_mirror, "avatar_change_times", with_ghost):
            result, changes = self.changes(
                avatar_observations={"ghost": [url, "2026-03-09T00:00:00Z"]}
            )
        self.assertNotIn("ghost", [player["id"] for player in result["players"]])
        self.assertEqual([name for name, _date in changes], ["ben", "dan", "ana"])


class PokemonAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = pokemon_avatars.load_catalogue()

    @staticmethod
    def races():
        """ana has a racetime.gg avatar; ben and cid have none. cid races first."""
        def with_avatar(user_id, place, avatar=None):
            return entrant(
                user_id,
                place=place,
                finish_time=f"PT{10 + place}M",
                user={"id": user_id, "name": user_id, "avatar": avatar},
            )

        return [
            race(
                "early",
                started_at="2026-03-01T20:00:00.000Z",
                ended_at="2026-03-01T22:00:00.000Z",
                entrants=[
                    with_avatar("ana", 1, "https://racetime.gg/media/ana.png"),
                    with_avatar("cid", 2),
                ],
            ),
            race(
                "late",
                started_at="2026-03-02T20:00:00.000Z",
                ended_at="2026-03-02T22:00:00.000Z",
                entrants=[
                    with_avatar("ben", 1),
                    with_avatar("ana", 2, "https://racetime.gg/media/ana.png"),
                ],
            ),
        ]

    def avatars(self, bios=None):
        result = payload(self.races(), bios=bios or {}, pokemon=self.catalogue)
        return {player["id"]: player.get("a") for player in result["players"]}

    def test_racers_without_an_avatar_get_distinct_random_pokemon(self):
        avatars = self.avatars()
        self.assertEqual(
            avatars["ana"], avatar_mirror.mirror_name("https://racetime.gg/media/ana.png")
        )
        for user_id in ("ben", "cid"):
            with self.subTest(user_id=user_id):
                self.assertIsNotNone(pokemon_avatars.sprite_path(avatars[user_id]))
        self.assertNotEqual(avatars["ben"], avatars["cid"])

    def test_a_favourite_replaces_a_racetime_avatar(self):
        bios = {"ana": ["My favourite pokemon is: Pikachu", "2026-03-03T00:00:00Z", None, []]}
        self.assertEqual(self.avatars(bios)["ana"], "pokemon-25.png")

    def test_a_random_favourite_replaces_a_racetime_avatar(self):
        bios = {"ana": ["My favourite pokemon is random", "2026-03-03T00:00:00Z", None, []]}
        expected = pokemon_avatars.assign(["ana", "cid", "ben"], set(), {}, self.catalogue)
        avatars = self.avatars(bios)
        self.assertEqual(avatars, expected)
        self.assertEqual(len(set(avatars.values())), 3)

    def test_racers_are_ordered_by_their_first_race_then_id(self):
        bios = {"ben": ["fav pokemon: unown", "2026-03-03T00:00:00Z", None, []]}
        expected = pokemon_avatars.assign(
            ["ana", "cid", "ben"], {"ana"}, bios, self.catalogue
        )
        avatars = self.avatars(bios)
        self.assertEqual({user_id: avatars[user_id] for user_id in expected}, expected)
        self.assertTrue(avatars["ben"].startswith("pokemon-201"))

    def test_without_a_catalogue_no_pokemon_is_given(self):
        result = payload(self.races())
        avatars = {player["id"]: player.get("a") for player in result["players"]}
        self.assertEqual(
            avatars,
            {
                "ana": avatar_mirror.mirror_name("https://racetime.gg/media/ana.png"),
                "ben": None,
                "cid": None,
            },
        )

    def test_bio_text_is_never_published(self):
        bios = {"cid": ["Secret plans. My favourite pokemon is Mew", "2026-03-03T00:00:00Z", None, []]}
        result = payload(self.races(), bios=bios, pokemon=self.catalogue)
        self.assertNotIn("Secret plans", json.dumps(result))
        self.assertEqual(
            {player["id"]: player.get("a") for player in result["players"]}["cid"],
            "pokemon-151.png",
        )


class GeneratedParticipantDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.participants = json.loads(
            (REPO_ROOT / "site" / "data" / "participants.json").read_text(
                encoding="utf-8"
            )
        )
        cls.stats = json.loads(
            (REPO_ROOT / "site" / "data" / "race-stats.json").read_text(encoding="utf-8")
        )

    def test_reconciles_with_the_dashboard_dataset(self):
        source = self.stats["source"]
        diagnostics = self.participants["diagnostics"]
        self.assertEqual(len(self.participants["races"]), source["included_race_count"])
        self.assertEqual(
            diagnostics["entry_count"] + diagnostics["unidentified_entry_count"],
            source["included_entrant_count"],
        )

    def test_indexes_stay_within_range(self):
        player_count = len(self.participants["players"])
        mode_count = len(self.participants["modes"])
        for race_row in self.participants["races"]:
            self.assertTrue(0 <= race_row["m"] < mode_count)
            self.assertEqual(len(race_row["e"]), len(set(entry[0] for entry in race_row["e"])))
            for entry in race_row["e"]:
                self.assertTrue(0 <= entry[0] < player_count)

    def test_every_race_has_an_eastern_start_hour(self):
        for race_row in self.participants["races"]:
            self.assertIsInstance(race_row["h"], int)
            self.assertTrue(0 <= race_row["h"] <= 23)

    def test_every_mode_has_a_leaderboard(self):
        modes = [board["mode"] for board in self.participants["leaderboards"]]
        self.assertEqual(modes, ["all", *self.participants["modes"]])

    def test_mode_races_sum_to_the_overall_total(self):
        per_mode = {}
        for board in self.participants["leaderboards"]:
            if board["mode"] == "all":
                continue
            for row in board["rows"]:
                per_mode[row["p"]] = per_mode.get(row["p"], 0) + row["races"]
        for row in leaderboard(self.participants, "all"):
            self.assertEqual(per_mode[row["p"]], row["races"])

    def test_schema_version_and_top_level_keys(self):
        self.assertEqual(self.participants["schema_version"], 11)
        for key in ("presets", "hardest_preset", "avatar_changes", "badges", "records", "facts", "rating"):
            self.assertIn(key, self.participants)
        self.assertIn("highlights", self.participants["diagnostics"])

    def test_badge_definitions_carry_image_paths(self):
        for badge in self.participants["badges"]:
            image, still = highlights.badge_image_paths(badge["id"])
            self.assertIsInstance(badge["image"], str)
            self.assertIsInstance(badge["still"], str)
            self.assertEqual(badge["image"], image)
            self.assertEqual(badge["still"], still)

    def test_badge_rules_use_names_that_exist_in_the_data(self):
        for mode in (
            highlights.GLITCHED_MODE,
            highlights.ENTRANCE_MODE,
            highlights.MYSTERY_MODE,
            highlights.SPOILER_MODE,
            highlights.INVERTED_MODE,
            highlights.KEYS_MODE,
            highlights.SIMPLE_MODE,
            highlights.FUN_MODE,
        ):
            self.assertIn(mode, self.participants["modes"])
        preset_names = {preset["n"] for preset in self.participants["presets"]}
        self.assertIn(highlights.CASUAL_BOOTS_PRESET, preset_names)

    def test_avatars_are_mirror_names_or_sprites(self):
        pattern = re.compile(r"^[0-9a-f]{16}\.(png|jpg|jpeg|gif|webp)$")
        for player in self.participants["players"]:
            if "a" in player and pokemon_avatars.sprite_path(player["a"]) is None:
                self.assertRegex(player["a"], pattern)

    def test_no_comment_key_anywhere(self):
        def walk(value):
            if isinstance(value, dict):
                self.assertNotIn("comment", value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(self.participants)

    def test_preset_and_tag_indexes_stay_within_range(self):
        player_count = len(self.participants["players"])
        preset_count = len(self.participants["presets"])
        mode_count = len(self.participants["modes"])
        for preset in self.participants["presets"]:
            self.assertTrue(0 <= preset["m"] < mode_count)
        for race_row in self.participants["races"]:
            self.assertTrue(race_row["p"] is None or 0 <= race_row["p"] < preset_count)
            for tag in race_row.get("t", []):
                self.assertTrue(tag)
                self.assertTrue(0 <= tag[1] < player_count)

    def test_rating_after_is_an_int_on_every_entry(self):
        for race_row in self.participants["races"]:
            for entry in race_row["e"]:
                self.assertEqual(len(entry), 6)
                self.assertIsInstance(entry[4], int)
                self.assertIsInstance(entry[5], int)

    def test_every_player_has_the_rating_fields(self):
        for player in self.participants["players"]:
            for key in ("rating", "mu", "sigma", "finishes", "mode_ratings", "badges",
                        "progress", "pbs", "streaks", "best_preset"):
                self.assertIn(key, player)
            self.assertNotIn("rd", player)
            self.assertNotIn("provisional", player)
            self.assertIsInstance(player["rating"], int)
            self.assertIsInstance(player["mu"], float)
            self.assertIsInstance(player["sigma"], float)
            self.assertGreaterEqual(player["sigma"], self.participants["rating"]["sigma_floor"])

    def test_rating_params_describe_the_model(self):
        params = self.participants["rating"]
        self.assertEqual(params["model"], "bradley-terry")
        self.assertEqual(params["base"], 1000)
        for key in ("scale", "k", "mu0", "beta", "sigma0", "sigma_floor", "kappa",
                    "provisional_finishes"):
            self.assertIn(key, params)
        self.assertNotIn("anchor", params)

    def test_a_players_rating_is_their_last_entry_rating(self):
        last = {}
        for race_row in self.participants["races"]:
            for entry in race_row["e"]:
                last[entry[0]] = entry[4]
        for index, player in enumerate(self.participants["players"]):
            self.assertEqual(player["rating"], last[index])

    def test_every_mode_rating_matches_the_racers_last_entry_in_that_mode(self):
        last = {}
        raced = {}
        for race_row in self.participants["races"]:
            for entry in race_row["e"]:
                last[(entry[0], race_row["m"])] = entry[5]
                raced.setdefault(entry[0], set()).add(race_row["m"])
        for index, player in enumerate(self.participants["players"]):
            mode_ratings = player["mode_ratings"]
            self.assertEqual(list(mode_ratings), [str(m) for m in sorted(raced.get(index, ()))])
            for key, fields in mode_ratings.items():
                self.assertEqual(fields["rating"], last[(index, int(key))])
                self.assertGreaterEqual(
                    fields["sigma"], self.participants["rating"]["sigma_floor"]
                )

    def test_mode_finishes_sum_to_the_overall_finishes(self):
        for player in self.participants["players"]:
            total = sum(fields["finishes"] for fields in player["mode_ratings"].values())
            self.assertEqual(total, player["finishes"])

    def test_contains_no_sensitive_racetime_fields(self):
        serialized = (REPO_ROOT / "site" / "data" / "participants.json").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "full_name",
            "discriminator",
            "pronouns",
            "twitch_name",
            "twitch_channel",
            "score_change",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
