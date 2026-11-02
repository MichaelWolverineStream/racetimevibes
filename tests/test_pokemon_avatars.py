import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import pokemon_avatars, pokemon_images


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = REPO_ROOT / "assets" / "pokemon"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def fake_dir(root, pokemon, species, files):
    """A pokemon directory with a pokédex and tiny sprite files."""
    directory = Path(root)
    pokedex = {
        "pokemon": {str(n): name for n, name in pokemon.items()},
        "species": {str(n): name for n, name in species.items()},
    }
    (directory / pokemon_avatars.POKEDEX_FILE).write_text(json.dumps(pokedex))
    for file in files:
        (directory / file).write_bytes(PNG)
    return directory


class RealCatalogueCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = pokemon_avatars.load_catalogue()
        cls.pokedex = json.loads((REAL_DIR / pokemon_avatars.POKEDEX_FILE).read_text())


class LoadCatalogueTests(unittest.TestCase):
    def test_indexes_sprites_pool_and_name_keys(self):
        with tempfile.TemporaryDirectory() as root:
            directory = fake_dir(
                root,
                {1: "bulbasaur", 122: "mr-mime", 10001: "vulpix-alola", 5: "charmeleon"},
                {1: "bulbasaur", 122: "mr-mime", 5: "charmeleon"},
                ["1.png", "122-b.png", "122.png", "10001.png", "readme.txt", "999.png", "1-X.png"],
            )
            catalogue = pokemon_avatars.load_catalogue(directory)
        self.assertEqual(catalogue.sprites, {1: ("1.png",), 122: ("122-b.png", "122.png"), 10001: ("10001.png",)})
        self.assertEqual(catalogue.pool, (1, 122))
        self.assertEqual(catalogue.compact_ids["mrmime"], 122)
        self.assertEqual(catalogue.sorted_ids["alola-vulpix"], 10001)
        self.assertNotIn("charmeleon", catalogue.compact_ids)

    def test_species_name_leads_to_the_species_id(self):
        with tempfile.TemporaryDirectory() as root:
            directory = fake_dir(root, {386: "deoxys-normal"}, {386: "deoxys"}, ["386.png"])
            catalogue = pokemon_avatars.load_catalogue(directory)
        self.assertEqual(catalogue.compact_ids["deoxys"], 386)
        self.assertEqual(catalogue.compact_ids["deoxysnormal"], 386)

    def test_missing_or_malformed_pokedex_raises(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(pokemon_avatars.PokedexError):
                pokemon_avatars.load_catalogue(Path(root))
            (Path(root) / pokemon_avatars.POKEDEX_FILE).write_text("{not json")
            with self.assertRaises(pokemon_avatars.PokedexError):
                pokemon_avatars.load_catalogue(Path(root))
            (Path(root) / pokemon_avatars.POKEDEX_FILE).write_text('{"pokemon": {"x": "a"}, "species": {}}')
            with self.assertRaises(pokemon_avatars.PokedexError):
                pokemon_avatars.load_catalogue(Path(root))


class NormaliseTests(unittest.TestCase):
    def test_strips_accents_and_spells_out_gender_signs(self):
        self.assertEqual(pokemon_avatars.normalise("Pokémon"), "pokemon")
        self.assertEqual(pokemon_avatars.normalise("Nidoran♀").split(), ["nidoran", "f"])
        self.assertEqual(pokemon_avatars.normalise("Nidoran♂").split(), ["nidoran", "m"])


class FavouritePokemonTests(RealCatalogueCase):
    def check(self, bio, expected):
        with self.subTest(bio=bio):
            self.assertEqual(pokemon_avatars.favourite_pokemon(bio, self.catalogue), expected)

    def test_recognised_phrases(self):
        vulpix = int(next(k for k, v in self.pokedex["pokemon"].items() if v == "vulpix-alola"))
        cases = {
            "My favourite pokemon is: Pikachu": 25,
            "my favorite Pokémon is pikachu because it's cute": 25,
            "Favourite pokemon: Mr. Mime": 122,
            "My favourtie pokmon is Pikachuu": 25,
            "Fave pkmn: Farfetch'd": 83,
            "My favourite Pokémon's Deoxys": 386,
            "favorite pokemon is Nidoran♀": 29,
            "my favourite pokemon is Mega Charizard X": 10034,
            "favourite pokemon: Alolan Vulpix": vulpix,
            "favourite pokemon: Type: Null": 772,
            "fav pokemon: eevee": 133,
        }
        for bio, expected in cases.items():
            self.check(bio, expected)

    def test_unrelated_text_resolves_to_nothing(self):
        for bio in (
            None,
            "",
            "Type: Null",
            "I like pikachu",
            "My favourite pokemon game is Emerald",
            "my favourite pokemon type is fire",
            "My favourite pokemon is:\nPikachu",
            # Ordinary words close to a short name stay below the cutoff.
            "My favourite pokemon starter is mudkip",
            "My favourite pokemon is unknown",
            "favourite pokemon: zard",
        ):
            self.check(bio, None)

    def test_a_later_hit_wins_when_the_first_does_not_resolve(self):
        self.check("my favourite pokemon is zzzzzz\nfavorite pokemon: eevee", 133)
        self.check("favourite pokemon game is emerald, favourite pokemon is bulbasaur", 1)

    def test_exact_match_beats_fuzzy_at_a_shorter_length(self):
        self.check("favourite pokemon: mr mime", 122)

    def test_name_stops_at_the_word_limit(self):
        self.check("favourite pokemon is pikachu " + "word " * 10, 25)


class AssignTests(RealCatalogueCase):
    def bios(self, **texts):
        return {user: [text, "2026-01-01T00:00:00Z"] for user, text in texts.items()}

    def test_favourite_beats_a_racetime_avatar(self):
        result = pokemon_avatars.assign(
            ["a"], {"a"}, self.bios(a="favourite pokemon is eevee"), self.catalogue
        )
        self.assertEqual(result["a"], "pokemon-133.png")

    def test_racetime_avatar_without_favourite_gets_nothing(self):
        result = pokemon_avatars.assign(["a", "b"], {"a"}, {"a": [None, "x"]}, self.catalogue)
        self.assertEqual(set(result), {"b"})

    def test_random_picks_come_from_the_pool_and_are_unused_while_possible(self):
        order = [f"user{n}" for n in range(300)]
        result = pokemon_avatars.assign(order, set(), {}, self.catalogue)
        ids = [int(re.match(r"pokemon-(\d+)", name).group(1)) for name in result.values()]
        self.assertEqual(len(set(ids)), 300)
        self.assertTrue(set(ids) <= set(self.catalogue.pool))
        self.assertTrue(all(number <= 1025 for number in ids))

    def test_random_avoids_pokemon_taken_as_favourites(self):
        order = ["fan"] + [f"user{n}" for n in range(1024)]
        result = pokemon_avatars.assign(
            order, set(), self.bios(fan="favourite pokemon is pikachu"), self.catalogue
        )
        pikachu = [name for name in result.values() if re.fullmatch(r"pokemon-25(-[a-z0-9]+)*\.png", name)]
        self.assertEqual(pikachu, ["pokemon-25.png"])

    def test_favourites_of_one_pokemon_spread_across_variants(self):
        files = self.catalogue.sprites[201]
        self.assertGreater(len(files), 3)
        order = [f"fan{n}" for n in range(len(files))]
        bios = self.bios(**{user: "favourite pokemon: unown" for user in order})
        result = pokemon_avatars.assign(order, set(), bios, self.catalogue)
        self.assertEqual(set(result.values()), {pokemon_avatars.AVATAR_PREFIX + f for f in files})

    def test_is_deterministic(self):
        order = [f"user{n}" for n in range(50)]
        first = pokemon_avatars.assign(order, set(), {}, self.catalogue)
        self.assertEqual(first, pokemon_avatars.assign(order, set(), {}, self.catalogue))

    def test_appending_a_racer_changes_nobody_else(self):
        order = [f"user{n}" for n in range(60)]
        bios = self.bios(user3="favourite pokemon is unown", user4="favourite pokemon is unown")
        before = pokemon_avatars.assign(order, {"user7"}, bios, self.catalogue)
        after = pokemon_avatars.assign(order + ["newcomer"], {"user7"}, bios, self.catalogue)
        self.assertEqual({k: v for k, v in after.items() if k != "newcomer"}, before)
        self.assertIn("newcomer", after)

    def test_exhausted_pool_falls_back_to_least_used(self):
        with tempfile.TemporaryDirectory() as root:
            directory = fake_dir(
                root, {1: "a", 2: "b", 3: "c"}, {1: "a", 2: "b", 3: "c"}, ["1.png", "2.png", "3.png"]
            )
            catalogue = pokemon_avatars.load_catalogue(directory)
        order = [f"user{n}" for n in range(7)]
        result = pokemon_avatars.assign(order, set(), {}, catalogue)
        counts = sorted(list(result.values()).count(name) for name in set(result.values()))
        self.assertEqual(counts, [2, 2, 3])

    def test_empty_pool_assigns_no_random_avatars(self):
        with tempfile.TemporaryDirectory() as root:
            catalogue = pokemon_avatars.load_catalogue(fake_dir(root, {1: "a"}, {1: "a"}, []))
        self.assertEqual(pokemon_avatars.assign(["a"], set(), {}, catalogue), {})


class SpritePathTests(unittest.TestCase):
    def test_resolves_only_prefixed_regular_sprite_files(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "201-b.png").write_bytes(PNG)
            (directory / "25.png").write_bytes(PNG)
            (directory / "26.png").symlink_to(directory / "25.png")
            (directory / "x.png").write_bytes(PNG)
            resolve = lambda name: pokemon_avatars.sprite_path(name, directory)
            self.assertEqual(resolve("pokemon-201-b.png"), directory / "201-b.png")
            self.assertEqual(resolve("pokemon-25.png"), directory / "25.png")
            for name in (
                "pokemon-26.png",
                "pokemon-27.png",
                "pokemon-../x.png",
                "pokemon-25.PNG",
                "pokemon-x.png",
                "pokemon-0.png",
                "25.png",
                "pokemon-",
                None,
                25,
                b"pokemon-25.png",
            ):
                with self.subTest(name=name):
                    self.assertIsNone(resolve(name))


class CommittedAssetTests(RealCatalogueCase):
    def test_every_file_is_a_png_sprite_of_a_known_pokemon(self):
        files = [p for p in REAL_DIR.iterdir() if p.name != pokemon_avatars.POKEDEX_FILE]
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=path.name):
                self.assertRegex(path.name, r"^\d+(-[a-z0-9-]+)?\.png$")
                self.assertEqual(path.read_bytes()[:8], PNG[:8])
                number = path.name.split(".")[0].split("-")[0]
                self.assertIn(number, self.pokedex["pokemon"])

    def test_every_species_has_a_sprite(self):
        for number in range(1, 1026):
            with self.subTest(species=number):
                self.assertIn(str(number), self.pokedex["species"])
                self.assertIn(number, self.catalogue.sprites)
        self.assertEqual(self.catalogue.pool, tuple(range(1, 1026)))

    def test_there_is_no_zero_sprite(self):
        self.assertFalse((REAL_DIR / "0.png").exists())

    def test_every_sprite_is_a_square_of_at_least_the_minimum_side(self):
        for path in sorted(REAL_DIR.glob("*.png")):
            with self.subTest(file=path.name):
                width, height, _ = pokemon_images.png_size(path)
                self.assertEqual(width, height)
                self.assertGreaterEqual(width, pokemon_images.MIN_SIDE)

    def test_every_sprite_is_a_palette_png(self):
        for path in sorted(REAL_DIR.glob("*.png")):
            with self.subTest(file=path.name):
                self.assertEqual(pokemon_images.png_size(path)[2], 3)


class RefreshPokedexTests(unittest.TestCase):
    ORIGIN = pokemon_avatars.POKEAPI_ORIGIN

    def setUp(self):
        sleeper = patch.object(pokemon_avatars.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def page(self, kind, entries, count, next_url=None):
        return {
            "count": count,
            "next": next_url,
            "results": [
                {"name": name, "url": f"{self.ORIGIN}{kind}/{number}/"}
                for number, name in entries
            ],
        }

    def loader(self, pages):
        requested = []

        def load(url, _label):
            requested.append(url)
            return pages[url]

        return load, requested

    def test_follows_next_links_and_writes_both_lists_by_id(self):
        second = f"{self.ORIGIN}pokemon/?offset=2&limit=2"
        pages = {
            pokemon_avatars.POKEAPI_LISTS["pokemon"]: self.page(
                "pokemon", [(10001, "deoxys-attack"), (1, "bulbasaur")], 3, second
            ),
            second: self.page("pokemon", [(386, "deoxys-normal")], 3),
            pokemon_avatars.POKEAPI_LISTS["species"]: self.page(
                "pokemon-species", [(386, "deoxys"), (1, "bulbasaur")], 2
            ),
        }
        load, requested = self.loader(pages)
        with tempfile.TemporaryDirectory() as root:
            pokedex = pokemon_avatars.refresh_pokedex(Path(root), loader=load)
            written = (Path(root) / pokemon_avatars.POKEDEX_FILE).read_text(encoding="utf-8")

        self.assertEqual(
            pokedex,
            {
                "pokemon": {"1": "bulbasaur", "386": "deoxys-normal", "10001": "deoxys-attack"},
                "species": {"1": "bulbasaur", "386": "deoxys"},
            },
        )
        self.assertEqual(json.loads(written), pokedex)
        self.assertNotIn(" ", written)
        self.assertEqual(len(requested), 3)
        self.assertEqual(self.sleep.call_count, 1)

    def test_rejects_lists_that_cannot_become_the_pokedex(self):
        first = pokemon_avatars.POKEAPI_LISTS["pokemon"]
        cases = {
            "not a list": {"count": 1, "results": None, "next": None},
            "bad url": {
                "count": 1,
                "next": None,
                "results": [{"name": "mew", "url": "https://example.test/pokemon/151/"}],
            },
            "bad name": self.page("pokemon", [(151, "Mew!")], 1),
            "short count": self.page("pokemon", [(151, "mew")], 2),
            "off-origin next": self.page(
                "pokemon", [(151, "mew")], 2, "https://example.test/pokemon/?offset=1"
            ),
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                load, _requested = self.loader({first: payload})
                with tempfile.TemporaryDirectory() as root:
                    with self.assertRaises(pokemon_avatars.PokedexError):
                        pokemon_avatars.refresh_pokedex(Path(root), loader=load)
                    self.assertFalse((Path(root) / pokemon_avatars.POKEDEX_FILE).exists())


if __name__ == "__main__":
    unittest.main()
