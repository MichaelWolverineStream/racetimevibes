import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts import build_stats, participant_stats


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


def payload(races):
    return participant_stats.build_participant_payload(
        build_stats.included_race_contexts(races),
        source_url=SOURCE_URL,
        timezone_label="Eastern Time",
        timezone_name=build_stats.SCHEDULE_TIMEZONE_NAME,
        generated_at=GENERATED_AT,
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
        self.assertIn({"id": "abc123", "name": "abc123"}, result["players"])

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
        self.assertEqual(racer["average_seconds"], 1200)
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
        self.assertIsNone(quitter["average_seconds"])
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

    def test_filtering_matches_the_dashboard_aggregate(self):
        races = [
            placed_race("keep-1", ["ana", "ben"]),
            placed_race("keep-2", ["ana", "ben"], mode="keys"),
            placed_race("ladder-test", ["ana", "ben"], mode="ladder test"),
            placed_race("too-old", ["ana", "ben"], started_at="2025-12-01T20:00:00.000Z"),
            race("cancelled", entrants=[entrant("ana")], status="cancelled"),
        ]
        stats = build_stats.aggregate_races(races, 5, 1, generated_at=GENERATED_AT)
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
            "avatar",
            "pronouns",
            "flair",
            "twitch_name",
            "twitch_channel",
            "comment",
            "score",
            "Visible#1234",
            "2025-",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)
        self.assertIn({"id": "abc123", "name": "Visible"}, result["players"])

    def test_writes_compact_json_atomically(self):
        result = payload([placed_race("solo", ["ana", "ben"])])
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "nested" / "participants.json"
            participant_stats.write_compact_json(result, output_path)
            text = output_path.read_text(encoding="utf-8")
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)
        self.assertEqual(json.loads(text)["schema_version"], 1)
        self.assertTrue(text.endswith("\n"))


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
        self.assertEqual(
            diagnostics["player_count"],
            self.stats["unique_players"]["identified_player_count"],
        )

    def test_indexes_stay_within_range(self):
        player_count = len(self.participants["players"])
        mode_count = len(self.participants["modes"])
        for race_row in self.participants["races"]:
            self.assertTrue(0 <= race_row["m"] < mode_count)
            self.assertEqual(len(race_row["e"]), len(set(entry[0] for entry in race_row["e"])))
            for entry in race_row["e"]:
                self.assertTrue(0 <= entry[0] < player_count)

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

    def test_contains_no_sensitive_racetime_fields(self):
        serialized = (REPO_ROOT / "site" / "data" / "participants.json").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "full_name",
            "discriminator",
            "avatar",
            "pronouns",
            "twitch_name",
            "twitch_channel",
            "score_change",
            "2025-",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
