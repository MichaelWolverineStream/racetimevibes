import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from scripts import featured


DAY_ZERO = date(2026, 3, 1)
MODES = ["keys", "open", "unknown"]
PARTICIPANTS_PATH = Path(__file__).resolve().parents[1] / "site" / "data" / "participants.json"


def iso(day):
    return (DAY_ZERO + timedelta(days=day)).isoformat()


def entry(player, rating=1000, season_rating=None):
    """A published entry: player, place, status, seconds, then the four ratings after it."""
    return [player, 1, 0, 3600, rating, rating, season_rating, season_rating]


def race(day, entries, *, mode=0, season=None, tags=()):
    row = {"r": f"race-{day}", "m": mode, "d": iso(day), "u": 0, "n": len(entries), "p": None, "e": list(entries)}
    if season is not None:
        row["s"] = season
    if tags:
        row["t"] = [list(tag) for tag in tags]
    return row


def leaders(day=0, *, mode=0, season=None, first=0):
    """Three racers rated far above everyone else, so nobody else is ever in a top 3."""
    return race(
        day,
        [entry(first + offset, 3000 - offset, 3000 - offset if season else None) for offset in range(3)],
        mode=mode,
        season=season,
    )


def ids(count):
    return [f"user{index:03d}" for index in range(count)]


def pick_days(picks, player=None):
    return [(pick.day - DAY_ZERO).days for pick in picks if player is None or pick.player == player]


class BioTextTests(unittest.TestCase):
    def test_none_or_blank_is_none(self):
        self.assertIsNone(featured.bio_text(None))
        self.assertIsNone(featured.bio_text(""))
        self.assertIsNone(featured.bio_text(" \n\t "))

    def test_whitespace_collapses(self):
        self.assertEqual(featured.bio_text("  Keys\n\nenjoyer\t and   more \n"), "Keys enjoyer and more")

    def test_a_short_bio_is_kept_whole(self):
        text = "x" * featured.BIO_MAX_LENGTH
        self.assertEqual(featured.bio_text(text), text)

    def test_a_long_bio_is_cut_at_a_word_break(self):
        words = " ".join(["routing"] * 40)
        text = featured.bio_text(words)
        self.assertLessEqual(len(text), featured.BIO_MAX_LENGTH)
        self.assertTrue(text.endswith("routing…"))
        self.assertTrue(words.startswith(text[:-1]))

    def test_the_cap_counts_code_points_as_the_page_does(self):
        # leaderboard.js checks the cap with Array.from, which counts code points like len().
        text = featured.bio_text("🏁" * 300)
        self.assertEqual(len(text), featured.BIO_MAX_LENGTH)

    def test_a_long_single_word_is_cut_hard(self):
        text = featured.bio_text("a " + "y" * 300)
        self.assertEqual(len(text), featured.BIO_MAX_LENGTH)
        self.assertTrue(text.startswith("a yyy"))
        self.assertTrue(text.endswith("y…"))


class ReplayTests(unittest.TestCase):
    def test_no_tags_no_picks(self):
        self.assertEqual(featured.replay([leaders(), race(5, [entry(3)])], ids(4), MODES), [])

    def test_the_window_runs_from_fifteen_to_two_days_back(self):
        races = [leaders(), race(20, [entry(3)], tags=[["pb", 3, 3000, 3100]]), leaders(40)]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = featured.replay(races, ids(4), MODES)
        self.assertEqual(pick_days(picks), list(range(22, 36)))
        self.assertTrue(all(pick.player == 3 and pick.race == 1 and pick.tag == 0 for pick in picks))

    def test_nobody_is_featured_twice_within_the_cooldown(self):
        races = [leaders()]
        for day in range(1, 90):
            races.append(race(day, [entry(3), entry(4), entry(5)], tags=[["pb", p, 1, 2] for p in (3, 4, 5)]))
        picks = featured.replay(races, ids(6), MODES)
        for player in (3, 4, 5):
            days = pick_days(picks, player)
            self.assertGreater(len(days), 1)
            self.assertEqual({later - earlier for earlier, later in zip(days, days[1:])}, {featured.COOLDOWN_DAYS})

    def test_a_top_three_racer_is_never_featured(self):
        # Ranks: 4 at 1600 leads, then 0, 1 and 2 tie at 1300 for 2nd; 3 is 5th.
        races = [
            race(0, [entry(4, 1600), entry(0, 1300), entry(1, 1300), entry(2, 1300), entry(3, 1200)]),
            race(5, [entry(3, 1200), entry(4, 1600), entry(2, 1300)], tags=[["pb", p, 1, 2] for p in (3, 4, 2)]),
        ]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = featured.replay(races, ids(5), MODES)
        self.assertEqual({pick.player for pick in picks}, {3})

    def test_a_mode_leader_is_excluded_but_not_an_unknown_mode_leader(self):
        races = [
            leaders(),
            # 3 is rated only in open, so they lead it; 4 leads only the unknown mode.
            race(1, [entry(3, 900)], mode=1, tags=[["pb", 3, 1, 2]]),
            race(1, [entry(4, 900)], mode=2, tags=[["pb", 4, 1, 2]]),
        ]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = featured.replay(races, ids(5), MODES)
        self.assertEqual({pick.player for pick in picks}, {4})

    def test_the_season_top_three_follows_the_newest_season_by_the_cutoff(self):
        # 3 is 4th all time but leads season 1, alone. Season 2 starts on day 10.
        races = [
            race(0, [entry(0, 3000), entry(1, 2900), entry(2, 2800), entry(3, 1000, 1500)], season=1, tags=[["pb", 3, 1, 2]]),
            race(10, [entry(0, 3000, 1100), entry(1, 2900, 1050), entry(2, 2800, 1000)], season=2),
        ]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            days = pick_days(featured.replay(races, ids(4), MODES), 3)
        # Excluded while season 1 is the newest by the cutoff (days 2 to 11), then free.
        self.assertEqual(days, [12, 13, 14, 15])

    def test_a_pb_beats_a_common_badge_from_the_same_race(self):
        # 30 racers unlock "keys" on day 0, so it is common by the time 3 unlocks it.
        races = [
            leaders(),
            race(0, [entry(p) for p in range(10, 40)], tags=[["badge", p, "keys"] for p in range(10, 40)]),
            race(20, [entry(3)], tags=[["pb", 3, 1, 2], ["badge", 3, "keys"]]),
        ]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = [pick for pick in featured.replay(races, ids(40), MODES) if pick.player == 3]
        self.assertEqual(pick_days(picks), list(range(22, 36)))
        self.assertEqual({(pick.race, pick.tag) for pick in picks}, {(2, 0)})

    def test_a_record_outranks_a_later_pb_and_a_later_record_replaces_it(self):
        races = [
            leaders(),
            race(20, [entry(3)], tags=[["record", 3, 1, 2]]),
            race(21, [entry(3)], tags=[["pb", 3, 1, 2]]),
            race(22, [entry(3)], tags=[["record", 3, 1, 2]]),
        ]
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = featured.replay(races, ids(4), MODES)
        best = {(pick.day - DAY_ZERO).days: pick.race for pick in picks}
        self.assertEqual(best, {22: 1, 23: 1, **{day: 3 for day in range(24, 38)}})

    def test_badge_rarity_is_measured_at_the_cutoff(self):
        # 50 racers by day 10, when 3 alone unlocks "keys" (2%: legendary); 30 more unlock it on day 30.
        races = [leaders(), race(1, [entry(p) for p in range(3, 50)])]
        races.append(race(10, [entry(3)], tags=[["pb", 3, 1, 2], ["badge", 3, "keys"]]))
        races.append(race(30, [entry(p) for p in range(10, 40)], tags=[["badge", p, "keys"] for p in range(10, 40)]))
        with mock.patch.object(featured, "COOLDOWN_DAYS", 1):
            picks = featured.replay(races, ids(50), MODES)
        mine = {(pick.day - DAY_ZERO).days: pick.tag for pick in picks if pick.player == 3}
        # The legendary badge outranks the PB on every day of the window, though by
        # day 30 the badge is common.
        self.assertEqual(set(mine), set(range(12, 26)))
        self.assertEqual(set(mine.values()), {1})

    def test_picks_follow_user_ids_not_indexes(self):
        races = [leaders()]
        for day in range(1, 60):
            races.append(race(day, [entry(p) for p in range(3, 9)], tags=[["pb", p, 1, 2] for p in range(3, 9)]))
        user_ids = ids(9)
        picks = featured.replay(races, user_ids, MODES)
        # Renumber everyone in reverse; the same users must be picked on the same days.
        order = list(reversed(range(9)))
        renumbered = [
            {**row, "e": [[order[e[0]], *e[1:]] for e in row["e"]], **({"t": [[t[0], order[t[1]], *t[2:]] for t in row["t"]]} if "t" in row else {})}
            for row in races
        ]
        new_ids = [None] * 9
        for old, new in enumerate(order):
            new_ids[new] = user_ids[old]
        repicks = featured.replay(renumbered, new_ids, MODES)
        self.assertEqual(
            [(pick.day, user_ids[pick.player]) for pick in picks],
            [(pick.day, new_ids[pick.player]) for pick in repicks],
        )

    def test_races_after_the_cutoff_never_change_a_pick(self):
        races = [leaders()]
        for day in range(1, 50):
            # Ratings swing above the leaders' now and then, so the exclusions change too.
            ratings = [entry(p, 1000 + (day * p) % 13 * 200) for p in range(3, 12)]
            races.append(race(day, ratings, tags=[["pb", p, 1, 2] for p in range(3, 12) if (p + day) % 3 == 0]))
        full = {pick.day: pick for pick in featured.replay(races, ids(12), MODES)}
        for day in range(5, 52):
            known = [row for row in races if row["d"] <= iso(day - featured.LAG_DAYS)]
            partial = {pick.day: pick for pick in featured.replay(known, ids(12), MODES)}
            target = DAY_ZERO + timedelta(days=day)
            self.assertEqual(partial.get(target), full.get(target), f"day {day}")

    def test_the_schedule_starts_at_the_newest_race(self):
        races = [leaders()]
        for day in range(1, 30):
            races.append(race(day, [entry(p) for p in range(3, 40)], tags=[["pb", p, 1, 2] for p in range(3, 40)]))
        picks = featured.schedule(races, ids(40), MODES)
        self.assertEqual(picks[0].day, DAY_ZERO + timedelta(days=29))
        self.assertLessEqual(picks[-1].day, DAY_ZERO + timedelta(days=29 + featured.WINDOW_DAYS + featured.LAG_DAYS - 1))

    def test_published_carries_only_the_current_bio_of_featured_racers(self):
        races = [leaders(), race(5, [entry(3), entry(4)], tags=[["pb", 3, 1, 2]])]
        user_ids = ids(5)
        bios = {
            user_ids[3]: ["Keys\nenjoyer", "2026-03-05T00:00:00Z", "2026-03-04T00:00:00Z", [["old secret", None]]],
            user_ids[4]: ["not featured", "2026-03-05T00:00:00Z", None, []],
        }
        rows = featured.published(races, user_ids, MODES, bios)
        self.assertTrue(rows)
        self.assertTrue(all(row[1] == 3 and row[4] == "Keys enjoyer" for row in rows))
        text = json.dumps(rows)
        self.assertNotIn("old secret", text)
        self.assertNotIn("not featured", text)
        self.assertNotIn("2026-03-04", text)

    def test_a_racer_without_a_bio_gets_none(self):
        races = [leaders(), race(5, [entry(3)], tags=[["pb", 3, 1, 2]])]
        rows = featured.published(races, ids(4), MODES, {})
        self.assertTrue(rows)
        self.assertTrue(all(row[4] is None for row in rows))


class CommittedFeaturedTests(unittest.TestCase):
    """The whole archive's replay, from the committed dataset the rebuild test keeps fresh."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(PARTICIPANTS_PATH.read_text(encoding="utf-8"))
        cls.races = cls.data["races"]
        cls.user_ids = [player["id"] for player in cls.data["players"]]
        cls.picks = featured.replay(cls.races, cls.user_ids, cls.data["modes"])

    def test_there_are_picks(self):
        self.assertGreater(len(self.picks), 300)

    def test_nobody_is_featured_twice_within_the_cooldown(self):
        last = {}
        for pick in self.picks:
            user_id = self.user_ids[pick.player]
            if user_id in last:
                self.assertGreaterEqual((pick.day - last[user_id]).days, featured.COOLDOWN_DAYS, user_id)
            last[user_id] = pick.day

    def test_every_pick_is_named_by_a_tag_in_its_window(self):
        for pick in self.picks:
            race_row = self.races[pick.race]
            self.assertEqual(race_row["t"][pick.tag][1], pick.player)
            self.assertLessEqual(race_row["d"], (pick.day - timedelta(days=featured.LAG_DAYS)).isoformat())
            self.assertGreaterEqual(
                race_row["d"], (pick.day - timedelta(days=featured.WINDOW_DAYS + featured.LAG_DAYS - 1)).isoformat()
            )

    def test_no_pick_is_in_a_top_three_at_its_cutoff(self):
        categories = {None} | {index for index, mode in enumerate(self.data["modes"]) if mode != "unknown"}
        for pick in self.picks[::20]:
            cutoff = (pick.day - timedelta(days=featured.LAG_DAYS)).isoformat()
            known = [row for row in self.races if row["d"] <= cutoff]
            season = max((row["s"] for row in known if "s" in row), default=None)
            tables = {}
            for row in known:
                scopes = [(None, None, 4), (None, row["m"], 5)]
                if season is not None and row.get("s") == season:
                    scopes += [(season, None, 6), (season, row["m"], 7)]
                for scope_season, mode, index in scopes:
                    for e in row["e"]:
                        if isinstance(e[index], int):
                            tables.setdefault((scope_season, mode), {})[e[0]] = e[index]
            for (scope_season, mode), table in tables.items():
                if mode in categories and pick.player in table:
                    rank = 1 + sum(1 for value in table.values() if value > table[pick.player])
                    self.assertGreater(rank, featured.TOP_RANKS, f"{pick.day} season {scope_season} mode {mode}")

    def test_the_published_field_matches_the_replay(self):
        newest = self.races[-1]["d"]
        expected = [
            [pick.day.isoformat(), pick.player, pick.race, pick.tag]
            for pick in self.picks
            if pick.day.isoformat() >= newest
        ]
        self.assertEqual([row[:4] for row in self.data["featured"]], expected)
        for row in self.data["featured"]:
            self.assertTrue(row[4] is None or (isinstance(row[4], str) and len(row[4]) <= featured.BIO_MAX_LENGTH))


if __name__ == "__main__":
    unittest.main()
