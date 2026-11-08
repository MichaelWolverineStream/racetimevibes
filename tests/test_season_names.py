import json
import tempfile
import unittest
from pathlib import Path

from scripts import season_names


def valid(**seasons):
    return {"schema_version": 1, "seasons": seasons or {"11": {"animal": "Panda"}}}


class SeasonNamesTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "season-names.json"

    def write(self, body):
        self.path.write_text(body if isinstance(body, str) else json.dumps(body))
        return self.path

    def assertRejected(self, body, pattern=None):
        with self.assertRaisesRegex(season_names.SeasonNamesError, pattern or "season-names.json"):
            season_names.load(self.write(body))


class LoadTests(SeasonNamesTestCase):
    def test_a_valid_file_loads(self):
        body = valid(**{"11": {"animal": "Panda"}, "12": {"animal": "Red Fox"}})
        self.assertEqual(season_names.load(self.write(body)), {11: "Panda", 12: "Red Fox"})

    def test_no_seasons_is_valid(self):
        self.assertEqual(season_names.load(self.write(valid() | {"seasons": {}})), {})

    def test_the_committed_file_names_season_11_panda(self):
        self.assertEqual(season_names.load()[11], "Panda")

    def test_a_missing_file_is_rejected(self):
        with self.assertRaisesRegex(season_names.SeasonNamesError, "season-names.json.*missing"):
            season_names.load(self.path)

    def test_invalid_json_and_non_objects_are_rejected(self):
        self.assertRejected("{nope", "not valid JSON")
        self.assertRejected([], "JSON object")

    def test_top_level_keys_must_be_exact(self):
        self.assertRejected(valid() | {"extra": 1}, "exactly the keys")
        self.assertRejected({"schema_version": 1}, "exactly the keys")

    def test_schema_version_must_be_1(self):
        self.assertRejected(valid() | {"schema_version": 2}, "schema_version")
        self.assertRejected(valid() | {"schema_version": True}, "schema_version")

    def test_seasons_must_be_an_object(self):
        self.assertRejected(valid() | {"seasons": []}, "seasons must be an object")

    def test_keys_must_be_plain_whole_numbers_from_season_11(self):
        for key in ("011", "+11", " 11", "11 ", "1.5", "eleven", "", "-11", "0", "١١"):
            with self.subTest(key=key):
                self.assertRejected(valid(**{key: {"animal": "Panda"}}), "whole number")
        self.assertRejected(valid(**{"10": {"animal": "Panda"}}), "from season 11")

    def test_entries_must_be_objects_with_exactly_animal(self):
        for entry in ("Panda", None, {}, {"animal": "Panda", "x": 1}, {"name": "Panda"}):
            with self.subTest(entry=entry):
                self.assertRejected(valid(**{"11": entry}), "exactly the key animal")

    def test_animals_must_be_clean_strings(self):
        for animal in (5, None, "", " Panda", "Panda ", "x" * (season_names.MAX_ANIMAL_LENGTH + 1)):
            with self.subTest(animal=animal):
                self.assertRejected(valid(**{"11": {"animal": animal}}), "animal")
        season_names.load(self.write(valid(**{"11": {"animal": "x" * season_names.MAX_ANIMAL_LENGTH}})))

    def test_animals_must_be_unique_ignoring_case(self):
        body = valid(**{"11": {"animal": "Panda"}, "12": {"animal": "pANDA"}})
        self.assertRejected(body, "share the animal")

    def test_animals_must_not_be_retro_names(self):
        for animal in ("EP", "ice palace", "TOH", "thieves' town"):
            with self.subTest(animal=animal):
                self.assertRejected(valid(**{"11": {"animal": animal}}), "retro")


class PublishedNamesTests(unittest.TestCase):
    def test_retro_names_for_1_to_10_and_champions_after(self):
        names = season_names.published_names({11: "Panda", 12: "Fox"})
        self.assertEqual(list(names), [str(season) for season in range(1, 13)])
        self.assertEqual(names["1"], {"kind": "retro", "text": "EP", "full": "Eastern Palace"})
        self.assertEqual(names["10"], {"kind": "retro", "text": "TR", "full": "Turtle Rock"})
        self.assertEqual(names["11"], {"kind": "champion", "text": "Panda"})
        self.assertEqual(names["12"], {"kind": "champion", "text": "Fox"})

    def test_without_animals_only_the_retro_seasons_are_named(self):
        self.assertEqual(list(season_names.published_names({})), [str(n) for n in range(1, 11)])

    def test_keys_are_in_numeric_order(self):
        names = season_names.published_names({12: "Fox", 11: "Panda"})
        self.assertEqual(list(names)[-2:], ["11", "12"])


if __name__ == "__main__":
    unittest.main()
