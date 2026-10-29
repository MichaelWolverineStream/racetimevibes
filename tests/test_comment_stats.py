import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts import comment_stats

DATA_DIR = Path(__file__).resolve().parents[1] / "site" / "data"
GENERATED_AT = datetime(2026, 9, 18, tzinfo=UTC)


def context(name, comments, *, started_at="2026-09-17T20:00:00Z"):
    """Build a race context whose entrants carry (user id, comment) pairs."""
    entrants = [
        {"user": {"id": user_id} if user_id else None, "comment": comment}
        for user_id, comment in comments
    ]
    return {
        "race": {"name": name, "entrants": entrants},
        "started_at": datetime.fromisoformat(started_at.replace("Z", "+00:00")),
    }


class BuildCommentsPayloadTests(unittest.TestCase):
    def build(self, contexts):
        return comment_stats.build_comments_payload(contexts, generated_at=GENERATED_AT)

    def test_payload_shape_and_timestamp(self):
        payload = self.build([context("r/one", [("a", "hi")])])
        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "generated_at": "2026-09-18T00:00:00Z",
                "races": {"r/one": [["a", "hi"]]},
            },
        )

    def test_strips_text_and_drops_empty_comments(self):
        payload = self.build(
            [context("r/one", [("a", "  gg  "), ("b", "   "), ("c", ""), ("d", None)])]
        )
        self.assertEqual(payload["races"], {"r/one": [["a", "gg"]]})

    def test_caps_text_at_500_characters(self):
        payload = self.build([context("r/one", [("a", "x" * 900)])])
        self.assertEqual(payload["races"]["r/one"][0][1], "x" * 500)

    def test_skips_anonymous_entrants(self):
        payload = self.build([context("r/one", [(None, "ghost"), ("a", "hi")])])
        self.assertEqual(payload["races"], {"r/one": [["a", "hi"]]})

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
        self.assertEqual(payload["races"]["r/early"], [["b", "two"], ["a", "one"]])

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
        self.assertEqual(self.comments["schema_version"], 1)

    def test_races_and_users_exist_in_participants(self):
        race_names = {race["r"] for race in self.participants["races"]}
        player_ids = {player["id"] for player in self.participants["players"]}
        for race_name, entries in self.comments["races"].items():
            with self.subTest(race=race_name):
                self.assertIn(race_name, race_names)
                self.assertTrue(entries)
                for user_id, text in entries:
                    self.assertIn(user_id, player_ids)
                    self.assertTrue(text)
                    self.assertLessEqual(len(text), 500)


if __name__ == "__main__":
    unittest.main()
