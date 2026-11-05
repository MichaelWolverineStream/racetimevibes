#!/usr/bin/env python3
"""Pick Pokémon avatars: a racer's favourite from their bio, or a random one."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

# Supports both `python3 scripts/pokemon_avatars.py` and `from scripts import pokemon_avatars`.
try:
    from scripts import race_archive
except ImportError:
    import race_archive


POKEMON_DIR = Path(__file__).resolve().parents[1] / "assets" / "pokemon"
POKEDEX_FILE = "pokedex.json"
POKEAPI_ORIGIN = "https://pokeapi.co/api/v2/"
POKEAPI_LISTS = {
    "pokemon": f"{POKEAPI_ORIGIN}pokemon/?limit=500",
    "species": f"{POKEAPI_ORIGIN}pokemon-species/?limit=500",
}
POKEAPI_ENTRY_URL = re.compile(
    re.escape(POKEAPI_ORIGIN) + r"(?:pokemon|pokemon-species)/([1-9][0-9]*)/"
)
POKEAPI_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

AVATAR_PREFIX = "pokemon-"
SPRITE_NAME = re.compile(r"([1-9][0-9]*)(?:-[a-z0-9]+)*\.png")
PHRASE_WORD_CUTOFF = 0.8
NAME_CUTOFF = 0.85
NAME_MAX_WORDS = 4
FAVOURITE_WORDS = ("favourite", "favorite")
FAVOURITE_SHORT_FORMS = frozenset({"fav", "fave"})
POKEMON_WORDS = ("pokemon",)
POKEMON_SHORT_FORMS = frozenset({"pkmn"})
LINKING_WORDS = frozenset({"is", "s", "are", "was"})
TOKEN = re.compile(r"[a-z0-9]+")

JsonObject = dict[str, Any]
JsonLoader = Callable[[str, str], JsonObject]


class PokedexError(RuntimeError):
    """Raised when PokeAPI returns a list that cannot become the pokédex."""


@dataclass(frozen=True)
class Catalogue:
    """The sprites on disk and the name keys that lead to them."""

    sprites: dict[int, tuple[str, ...]]
    pool: tuple[int, ...]
    compact_ids: dict[str, int]
    sorted_ids: dict[str, int]


def compact_key(name: str) -> str:
    """`mr-mime` -> `mrmime`."""
    return name.replace("-", "")


def sorted_key(name: str) -> str:
    """`vulpix-alola` -> `alola-vulpix`."""
    return "-".join(sorted(name.split("-")))


def load_catalogue(pokemon_dir: Path = POKEMON_DIR) -> Catalogue:
    """Read the pokédex and the sprite files; ids without a sprite are dropped."""
    try:
        pokedex = json.loads((pokemon_dir / POKEDEX_FILE).read_text(encoding="utf-8"))
        names = {
            key: {int(number): name for number, name in pokedex[key].items()}
            for key in ("pokemon", "species")
        }
        if not all(
            isinstance(name, str) for table in names.values() for name in table.values()
        ):
            raise TypeError("a name is not a string")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise PokedexError(
            f"{pokemon_dir / POKEDEX_FILE} is missing or malformed: {error}"
        ) from error
    files: dict[int, list[str]] = {}
    for path in pokemon_dir.glob("*.png"):
        match = SPRITE_NAME.fullmatch(path.name)
        if match and int(match.group(1)) in names["pokemon"]:
            files.setdefault(int(match.group(1)), []).append(path.name)
    sprites = {number: tuple(sorted(found)) for number, found in sorted(files.items())}
    compact_ids: dict[str, int] = {}
    sorted_ids: dict[str, int] = {}
    entries = sorted(item for table in names.values() for item in table.items())
    for number, name in entries:
        if number in sprites:
            compact_ids.setdefault(compact_key(name), number)
            sorted_ids.setdefault(sorted_key(name), number)
    pool = tuple(number for number in names["species"] if number in sprites)
    return Catalogue(sprites, tuple(sorted(pool)), compact_ids, sorted_ids)


def normalise(text: str) -> str:
    """Casefold, strip accents, and spell out the nidoran gender signs."""
    text = text.replace("\u2640", " f ").replace("\u2642", " m ")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _similar(token: str, word: str, cutoff: float) -> bool:
    matcher = difflib.SequenceMatcher(None, token, word)
    return (
        matcher.real_quick_ratio() >= cutoff
        and matcher.quick_ratio() >= cutoff
        and matcher.ratio() >= cutoff
    )


@lru_cache(maxsize=4096)
def _like(token: str, short_forms: frozenset[str], words: tuple[str, ...]) -> bool:
    return token in short_forms or any(
        _similar(token, word, PHRASE_WORD_CUTOFF) for word in words
    )


def _resolve_name(tokens: Sequence[str], catalogue: Catalogue) -> int | None:
    """Resolve the words after a phrase: exact at any length first, then fuzzy."""
    prefixes = [tokens[:count] for count in range(min(len(tokens), NAME_MAX_WORDS), 0, -1)]
    for prefix in prefixes:
        found = catalogue.compact_ids.get("".join(prefix))
        found_sorted = catalogue.sorted_ids.get(sorted_key("-".join(prefix)))
        candidates = [number for number in (found, found_sorted) if number is not None]
        if candidates:
            return min(candidates)
    matcher = difflib.SequenceMatcher(None)
    for prefix in prefixes:
        matcher.set_seq2(sorted_key("-".join(prefix)))
        # (ratio, -id), so that a tie goes to the lower id.
        best: tuple[float, int] | None = None
        for key, number in catalogue.sorted_ids.items():
            matcher.set_seq1(key)
            if (
                matcher.real_quick_ratio() < NAME_CUTOFF
                or matcher.quick_ratio() < NAME_CUTOFF
            ):
                continue
            ratio = matcher.ratio()
            if ratio >= NAME_CUTOFF and (best is None or (ratio, -number) > best):
                best = (ratio, -number)
        if best:
            return -best[1]
    return None


def favourite_pokemon(bio: str | None, catalogue: Catalogue) -> int | None:
    """The Pokémon id a bio names as favourite, tolerating typos, or None."""
    for line in normalise(bio or "").splitlines():
        tokens = TOKEN.findall(line)
        for k in range(len(tokens) - 1):
            if not (
                _like(tokens[k], FAVOURITE_SHORT_FORMS, FAVOURITE_WORDS)
                and _like(tokens[k + 1], POKEMON_SHORT_FORMS, POKEMON_WORDS)
            ):
                continue
            rest = tokens[k + 2 :]
            if rest and rest[0] in LINKING_WORDS:
                rest = rest[1:]
            found = _resolve_name(rest, catalogue)
            if found is not None:
                return found
    return None


def favourite_change_times(
    bios: Mapping[str, Sequence], catalogue: Catalogue
) -> dict[str, datetime]:
    """Return when each racer last added, switched or dropped their favourite Pokémon.

    A bio entry is `[bio, checked_at, since, earlier]` (see `racer_bios`). The bios of
    `earlier` plus the current one form a timeline, and the change is dated at the
    `since` of the latest bio whose favourite differs from the one before it. Edits that
    leave the favourite alone never move the date. A racer who never named a favourite,
    kept the same one throughout or has no history has no entry.
    """
    changes: dict[str, datetime] = {}
    for user_id, entry in bios.items():
        timeline = [*entry[3], [entry[0], entry[2]]]
        favourites = [favourite_pokemon(text, catalogue) for text, _since in timeline]
        for index in range(len(timeline) - 1, 0, -1):
            if favourites[index] != favourites[index - 1]:
                changes[user_id] = race_archive.parse_timestamp(
                    timeline[index][1], "bio since time"
                )
                break
    return changes


def _digest(user_id: str, item: int | str) -> bytes:
    return hashlib.sha256(f"{user_id}/{item}".encode()).digest()


def _pick_file(
    user_id: str, number: int, file_uses: Counter, catalogue: Catalogue
) -> str:
    return min(
        catalogue.sprites[number],
        key=lambda file: (file_uses[file], _digest(user_id, file)),
    )


def assign(
    order: Sequence[str],
    racetime_avatars: Collection[str],
    bios: Mapping[str, Sequence],
    catalogue: Catalogue,
) -> dict[str, str]:
    """Return {user_id: avatar name}: favourites first, then random for the avatarless."""
    pokemon_uses: Counter = Counter()
    file_uses: Counter = Counter()
    avatars: dict[str, str] = {}

    def give(user_id: str, number: int) -> None:
        file = _pick_file(user_id, number, file_uses, catalogue)
        pokemon_uses[number] += 1
        file_uses[file] += 1
        avatars[user_id] = AVATAR_PREFIX + file

    favourites = {}
    for user_id in order:
        bio = bios.get(user_id)
        number = favourite_pokemon(bio[0] if bio else None, catalogue)
        if number is not None:
            favourites[user_id] = number
            give(user_id, number)
    for user_id in order:
        if user_id in favourites or user_id in racetime_avatars or not catalogue.pool:
            continue
        fewest = min(pokemon_uses[number] for number in catalogue.pool)
        unused = [number for number in catalogue.pool if pokemon_uses[number] == fewest]
        give(user_id, min(unused, key=lambda number: _digest(user_id, number)))
    return avatars


def sprite_path(name: Any, pokemon_dir: Path = POKEMON_DIR) -> Path | None:
    """The sprite file a published avatar name points at, or None if it is not one."""
    if not isinstance(name, str) or not name.startswith(AVATAR_PREFIX):
        return None
    file = name[len(AVATAR_PREFIX) :]
    if not SPRITE_NAME.fullmatch(file):
        return None
    path = pokemon_dir / file
    return path if path.is_file() and not path.is_symlink() else None


def fetch_names(url: str, label: str, loader: JsonLoader) -> dict[int, str]:
    """Return {id: name} for every entry of a paginated PokeAPI list, following `next`."""
    names: dict[int, str] = {}
    expected = None
    page = 1
    while url:
        payload = loader(url, f"{label} page {page}")
        count, results = payload.get("count"), payload.get("results")
        url = payload.get("next")
        if not isinstance(count, int) or not isinstance(results, list):
            raise PokedexError(f"{label} page {page} is not a PokeAPI list")
        if expected not in (None, count):
            raise PokedexError(f"{label} changed its count while it was being read")
        expected = count
        for entry in results:
            entry_url = entry.get("url") if isinstance(entry, dict) else None
            name = entry.get("name") if isinstance(entry, dict) else None
            match = isinstance(entry_url, str) and POKEAPI_ENTRY_URL.fullmatch(entry_url)
            if not match or not isinstance(name, str) or not POKEAPI_NAME.fullmatch(name):
                raise PokedexError(
                    f"{label} page {page} has an entry without a valid name and URL"
                )
            names[int(match.group(1))] = name
        if url is not None and not (
            isinstance(url, str) and url.startswith(POKEAPI_ORIGIN)
        ):
            raise PokedexError(f"{label} page {page} links to {url!r}, outside PokeAPI")
        if url:
            page += 1
            time.sleep(race_archive.REQUEST_DELAY_SECONDS)
    if len(names) != expected:
        raise PokedexError(f"{label} lists {expected} entries but {len(names)} were read")
    return names


def refresh_pokedex(
    pokemon_dir: Path = POKEMON_DIR, *, loader: JsonLoader | None = None
) -> JsonObject:
    """Rewrite pokedex.json from PokeAPI's pokemon and species lists, ordered by id."""
    load = loader or race_archive._get_json
    pokedex = {}
    for key, url in POKEAPI_LISTS.items():
        names = fetch_names(url, key, load)
        pokedex[key] = {str(number): names[number] for number in sorted(names)}
    race_archive.write_json_atomic(pokedex, pokemon_dir / POKEDEX_FILE)
    return pokedex


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-pokedex",
        action="store_true",
        help=f"rewrite {POKEDEX_FILE} from PokeAPI (network)",
    )
    parser.add_argument(
        "--pokemon",
        type=Path,
        default=POKEMON_DIR,
        help=f"sprite and pokédex directory (default: {POKEMON_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.refresh_pokedex:
        print(
            "Nothing to do; pass --refresh-pokedex to rewrite the pokédex.",
            file=sys.stderr,
        )
        return 2
    try:
        pokedex = refresh_pokedex(args.pokemon)
    except (PokedexError, race_archive.ArchiveError) as error:
        print(f"Pokédex refresh failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Pokédex: {len(pokedex['pokemon'])} pokemon and "
        f"{len(pokedex['species'])} species."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
