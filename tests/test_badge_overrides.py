import json
import tempfile
import unittest
from pathlib import Path

from scripts import badge_overrides, highlights


def entry(**fields):
    return {"racer": "aaa", "badge": "hat_trick", "date": "2026-11-03"} | fields


def valid(*entries):
    return {"schema_version": 1, "overrides": list(entries) or [entry()]}


class BadgeOverridesTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "badge-overrides.json"

    def write(self, body):
        self.path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        return self.path

    def assertRejected(self, body, pattern=None):
        with self.assertRaisesRegex(badge_overrides.BadgeOverridesError, pattern or "badge-overrides.json"):
            badge_overrides.load(self.write(body))

    def assertEntryRejected(self, pattern, **fields):
        self.assertRejected(valid(entry(**fields)), pattern)


class LoadTests(BadgeOverridesTestCase):
    def test_a_valid_file_loads_in_file_order(self):
        body = valid(
            entry(racer="bbb", badge="hat_trick", date="2026-11-04", note="Raced offline"),
            entry(racer="aaa", badge="hat_trick"),
        )
        self.assertEqual(
            badge_overrides.load(self.write(body)),
            [
                badge_overrides.Override("bbb", "hat_trick", "2026-11-04", "Raced offline"),
                badge_overrides.Override("aaa", "hat_trick", "2026-11-03", None),
            ],
        )

    def test_no_overrides_is_valid(self):
        self.assertEqual(badge_overrides.load(self.write(valid() | {"overrides": []})), [])

    def test_the_committed_file_loads(self):
        self.assertIsInstance(badge_overrides.load(), list)

    def test_a_missing_file_is_rejected(self):
        with self.assertRaisesRegex(badge_overrides.BadgeOverridesError, "badge-overrides.json.*missing"):
            badge_overrides.load(self.path)

    def test_invalid_json_and_non_objects_are_rejected(self):
        self.assertRejected("{nope", "not valid JSON")
        self.assertRejected([], "JSON object")

    def test_top_level_keys_must_be_exact(self):
        self.assertRejected(valid() | {"extra": 1}, "exactly the keys")
        self.assertRejected({"schema_version": 1}, "exactly the keys")

    def test_schema_version_must_be_1(self):
        self.assertRejected(valid() | {"schema_version": 2}, "schema_version must be 1")
        self.assertRejected(valid() | {"schema_version": True}, "schema_version must be 1")

    def test_overrides_must_be_a_list(self):
        self.assertRejected(valid() | {"overrides": {}}, "overrides must be a list")

    def test_entries_must_be_objects(self):
        self.assertRejected(valid(entry(), "hat_trick"), "override 2 must be an object")

    def test_entries_must_have_the_required_keys_and_no_others(self):
        for key in ("racer", "badge", "date"):
            with self.subTest(missing=key):
                body = entry()
                del body[key]
                self.assertRejected(valid(body), "override 1 must have the keys")
        self.assertEntryRejected("override 1 must have the keys", extra=1)

    def test_racer_must_be_a_clean_non_empty_string(self):
        for racer in ("", " aaa", "aaa ", 5, None):
            with self.subTest(racer=racer):
                self.assertEntryRejected("override 1 racer must be a user ID", racer=racer)

    def test_badge_must_be_a_real_id(self):
        self.assertIn("hat_trick", {badge.id for badge in highlights.BADGES})
        self.assertEntryRejected("override 1 badge 'no_such_badge' is not a badge id", badge="no_such_badge")
        self.assertEntryRejected("override 1 badge 5 is not a badge id", badge=5)

    def test_first_blood_is_rejected(self):
        self.assertEntryRejected("override 1 .*first_blood.*can't be given by hand", badge="first_blood")

    def test_dates_must_be_real_iso_dates(self):
        for day in ("2026-2-03", "20261103", 20261103, None):
            with self.subTest(day=day):
                self.assertEntryRejected("override 1 date .* must be written YYYY-MM-DD", date=day)
        self.assertEntryRejected("override 1 date 2026-02-30 is not a real date", date="2026-02-30")
        badge_overrides.load(self.write(valid(entry(date="2026-11-03"))))

    def test_notes_must_be_clean_non_empty_strings(self):
        for note in ("", None, 5):
            with self.subTest(note=note):
                self.assertEntryRejected("override 1 note must be a non-empty string", note=note)
        for note in (" hi", "hi ", "hi\n"):
            with self.subTest(note=note):
                self.assertEntryRejected("override 1 note has leading or trailing whitespace", note=note)
        self.assertEntryRejected("override 1 note must be one line", note="two\nlines")

    def test_notes_are_capped_at_160_characters(self):
        self.assertEntryRejected("override 1 note is longer than 160", note="x" * 161)
        note = "x" * 160
        self.assertEqual(badge_overrides.load(self.write(valid(entry(note=note))))[0].note, note)

    def test_notes_may_hold_non_ascii_and_emoji(self):
        note = "Gewonnen in Köln \N{PARTY POPPER}"
        self.assertEqual(badge_overrides.load(self.write(valid(entry(note=note))))[0].note, note)

    def test_a_racer_cannot_get_the_same_badge_twice(self):
        self.assertRejected(valid(entry(), entry(date="2026-11-04")), "overrides 1 and 2 both give aaa the badge hat_trick")

    def test_other_badges_and_other_racers_are_fine(self):
        other = next(b.id for b in highlights.BADGES if b.id not in ("hat_trick", "first_blood"))
        body = valid(entry(), entry(badge=other), entry(racer="bbb"))
        self.assertEqual(len(badge_overrides.load(self.write(body))), 3)


class ResolveTests(unittest.TestCase):
    players = [{"id": "aaa", "name": "Alice"}, {"id": "bbb", "name": "Bob"}]
    records = [
        {"d": "2026-11-03", "e": [(0, 1, 0, 3600.0), (1, 2, 0, 3700.0)]},
        {"d": "2026-11-03", "e": [(1, 1, 0, 3500.0)]},
        {"d": "2026-11-03", "e": [(0, 2, 0, 3900.0)]},
        {"d": "2026-11-04", "e": [(1, 1, 0, 3400.0)]},
    ]

    def override(self, racer="aaa", badge="hat_trick", date="2026-11-03", note=None):
        return badge_overrides.Override(racer, badge, date, note)

    def test_it_picks_the_last_race_of_the_day_the_racer_ran(self):
        resolved = badge_overrides.resolve([self.override()], self.records, self.players)
        self.assertEqual(resolved.granted, {(0, "hat_trick"): 2})
        resolved = badge_overrides.resolve([self.override(racer="bbb")], self.records, self.players)
        self.assertEqual(resolved.granted, {(1, "hat_trick"): 1})

    def test_it_ignores_later_races_the_racer_wasnt_in(self):
        resolved = badge_overrides.resolve([self.override(date="2026-11-04", racer="bbb")], self.records, self.players)
        self.assertEqual(resolved.granted, {(1, "hat_trick"): 3})

    def test_granted_is_keyed_by_player_index_and_badge(self):
        other = next(b.id for b in highlights.BADGES if b.id not in ("hat_trick", "first_blood"))
        overrides = [self.override(), self.override(badge=other), self.override(racer="bbb")]
        resolved = badge_overrides.resolve(overrides, self.records, self.players)
        self.assertEqual(set(resolved.granted), {(0, "hat_trick"), (0, other), (1, "hat_trick")})

    def test_notes_hold_only_overrides_with_a_note(self):
        overrides = [self.override(note="By hand"), self.override(racer="bbb")]
        resolved = badge_overrides.resolve(overrides, self.records, self.players)
        self.assertEqual(resolved.notes, {0: {"hat_trick": "By hand"}})

    def test_an_unknown_racer_is_rejected(self):
        with self.assertRaisesRegex(
            badge_overrides.BadgeOverridesError, "hat_trick for zzz on 2026-11-03.*no included race"
        ):
            badge_overrides.resolve([self.override(racer="zzz")], self.records, self.players)

    def test_a_date_the_racer_didnt_race_is_rejected(self):
        with self.assertRaisesRegex(
            badge_overrides.BadgeOverridesError, "for aaa on 2026-11-04: Alice entered no included race"
        ):
            badge_overrides.resolve([self.override(date="2026-11-04")], self.records, self.players)

    def test_no_overrides_gives_nothing(self):
        resolved = badge_overrides.resolve([], self.records, self.players)
        self.assertEqual(resolved.granted, {})
        self.assertEqual(resolved.notes, {})


if __name__ == "__main__":
    unittest.main()
