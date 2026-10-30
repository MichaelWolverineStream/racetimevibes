import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts import comment_stats, participant_stats

DATA_DIR = Path(__file__).resolve().parents[1] / "site" / "data"
GENERATED_AT = datetime(2026, 9, 18, tzinfo=UTC)


def context(name, comments, *, started_at="2026-09-17T20:00:00Z"):
    """Build a race context whose entrants carry (user id, comment) pairs.

    A pair may add a status value and an ISO 8601 finish time as a third and
    fourth item; without them the entrant has neither.
    """
    entrants = []
    for user_id, comment, *finish in comments:
        entrant = {"user": {"id": user_id} if user_id else None, "comment": comment}
        if finish:
            status, finish_time = finish
            entrant["status"] = {"value": status}
            entrant["finish_time"] = finish_time
        entrants.append(entrant)
    return {
        "race": {"name": name, "entrants": entrants},
        "started_at": datetime.fromisoformat(started_at.replace("Z", "+00:00")),
    }


# (comment, finish time, expected chest count), one per documented example.
PARSE_CASES = [
    ("164/216 - I rate this seed a JUST GO TO EP/10", "PT2H11M37S", 164),
    ("143 cr as an overworld enjoyer", "PT1H23M52S", 143),
    ("174cr", "PT1H57M21S", 174),
    ("176", "PT1H47M37S", 176),
    ("CR198 - MMBK for go", "PT2H29M9S", 198),
    ("CR: 189 -- 140 checks in, flippers for go .... 50 checks", "PT2H15M49S", 189),
    ("~133/216 (ish) - Go mode minus skull north", "PT1H41M9S", 133),
    ("182 - Fun clown routing the start", "PT1H58M23S", 182),
    (">174, 184, and 194. Just meme tile room", "PT2H4M47S", 184),
    ("155, bathroom break cost sub 100, 5/10 bosses", "PT1H40M34S", 155),
    ("CR 151, CPH at GT entrance was 85", "PT1H35M54S", 151),
    ("hook go, 128 CR, 100cph on the nose !chuu", "PT1H16M38S", 128),
    ("167 that felt like 200 lmao", "PT1H42M27S", 167),
    ("I should have fluffed my 200 check 199/216", "PT2H2M26S", 200),
    ("197- welp, I got the mirror shield", "PT2H15M14S", 197),
    ("threw away 10+ minutes in fire rod go mode -180", "PT1H51M23S", 180),
    ("Yikes 56/216", "PT1H18M5S", 56),
    ("Forgot where boots were\u2026 202/216 CR o.O", "PT4H36M18S", 202),
    ("Derp 46/216", "PT58M26S", 46),
    ("91cph, go mode swamp small", "PT1H40M21S", None),
    ("more than 174. Nice flute.", "PT2H8M25S", None),
    ("<174", "PT1H55M24S", None),
    ("so what happened at 92m", "PT1H40M42S", None),
    ("100% random", "PT1H24M12S", None),
    ("1:43 boots -> only to isolate the icepalace", "PT2H14S", None),
    ("814cr", "PT1H41M7S", None),
    ("43 bonks :P", "PT2H8M22S", None),
    ("good to see we all lost the 50/50", "PT1H36M27S", 50),
    ("19.75 hearts", "PT2H17M7S", 75),
    ("nothing like 100 percent to start the day", "PT2H28M22S", 100),
]


class ParseChestCountTests(unittest.TestCase):
    def test_documented_examples(self):
        for text, finish_time, expected in PARSE_CASES:
            with self.subTest(text=text):
                seconds = participant_stats.parse_finish_seconds(finish_time)
                chests = comment_stats.parse_chest_count(text, seconds)
                self.assertEqual(chests, expected)

    def test_chest_count_bounds_are_inclusive(self):
        # The finish times keep the pace inside 30-175 for the accepted counts.
        for text, seconds, expected in [
            ("30", 3600, 30),
            ("29", 3600, None),
            ("300", 7200, 300),
            ("301", 7200, None),
        ]:
            with self.subTest(text=text):
                chests = comment_stats.parse_chest_count(text, seconds)
                self.assertEqual(chests, expected)

    def test_chests_per_hour_bounds_are_inclusive(self):
        for text, seconds, expected in [
            ("100", 12000, 100),
            ("100", 12000.1, None),
            ("175", 3600, 175),
            ("175", 3599.9, None),
        ]:
            with self.subTest(text=text, seconds=seconds):
                chests = comment_stats.parse_chest_count(text, seconds)
                self.assertEqual(chests, expected)

    def test_without_a_finish_time_there_is_no_count(self):
        self.assertIsNone(comment_stats.parse_chest_count("164/216", None))


class BuildCommentsPayloadTests(unittest.TestCase):
    def build(self, contexts):
        return comment_stats.build_comments_payload(contexts, generated_at=GENERATED_AT)

    def test_payload_shape_and_timestamp(self):
        payload = self.build([context("r/one", [("a", "hi")])])
        self.assertEqual(
            payload,
            {
                "schema_version": 2,
                "generated_at": "2026-09-18T00:00:00Z",
                "races": {"r/one": [["a", "hi", None]]},
            },
        )

    def test_strips_text_and_drops_empty_comments(self):
        payload = self.build(
            [context("r/one", [("a", "  gg  "), ("b", "   "), ("c", ""), ("d", None)])]
        )
        self.assertEqual(payload["races"], {"r/one": [["a", "gg", None]]})

    def test_caps_text_at_500_characters(self):
        payload = self.build([context("r/one", [("a", "x" * 900)])])
        self.assertEqual(payload["races"]["r/one"][0][1], "x" * 500)

    def test_skips_anonymous_entrants(self):
        payload = self.build([context("r/one", [(None, "ghost"), ("a", "hi")])])
        self.assertEqual(payload["races"], {"r/one": [["a", "hi", None]]})

    def test_omits_races_without_comments(self):
        payload = self.build([context("r/one", [("a", None)])])
        self.assertEqual(payload["races"], {})

    def test_keeps_entrant_order_and_orders_races_chronologically(self):
        payload = self.build(
            [
                context("r/late", [("z", "last")], started_at="2026-09-18T20:00:00Z"),
                context("r/early", [("b", "two"), ("a", "one")]),
            ]
        )
        self.assertEqual(list(payload["races"]), ["r/early", "r/late"])
        self.assertEqual(
            payload["races"]["r/early"], [["b", "two", None], ["a", "one", None]]
        )

    def test_parses_chest_counts_for_finishers_only(self):
        payload = self.build(
            [
                context(
                    "r/one",
                    [
                        ("a", "164 CR", "done", "PT2H11M37S"),
                        ("b", "164 CR", "dnf", None),
                        ("c", "164 CR"),
                    ],
                )
            ]
        )
        self.assertEqual(
            payload["races"]["r/one"],
            [["a", "164 CR", 164], ["b", "164 CR", None], ["c", "164 CR", None]],
        )

    def test_rejects_a_race_without_entrants(self):
        with self.assertRaises(comment_stats.CommentDataError):
            self.build([{"race": {"name": "r/bad"}, "started_at": GENERATED_AT}])


class CommittedCommentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.comments = json.loads(
            (DATA_DIR / "comments.json").read_text(encoding="utf-8")
        )
        cls.participants = json.loads(
            (DATA_DIR / "participants.json").read_text(encoding="utf-8")
        )

    def test_schema_version(self):
        self.assertEqual(self.comments["schema_version"], 2)

    def test_races_and_users_exist_in_participants(self):
        race_names = {race["r"] for race in self.participants["races"]}
        player_ids = {player["id"] for player in self.participants["players"]}
        for race_name, entries in self.comments["races"].items():
            with self.subTest(race=race_name):
                self.assertIn(race_name, race_names)
                self.assertTrue(entries)
                for user_id, text, chests in entries:
                    self.assertIn(user_id, player_ids)
                    self.assertTrue(text)
                    self.assertLessEqual(len(text), 500)
                    if chests is not None:
                        self.assertIsInstance(chests, int)
                        low, high = comment_stats.CHEST_COUNT_RANGE
                        self.assertTrue(low <= chests <= high)


if __name__ == "__main__":
    unittest.main()
