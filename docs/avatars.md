# Avatars

- **Purpose:** how each racer's published avatar is chosen, Pokémon included, how `site/avatars/` mirrors it, and how an avatar change is dated.
- **Read before:** changing `avatar_mirror.py`, `pokemon_avatars.py` or `pokemon_images.py`, or anything under `assets/pokemon/` or `site/avatars/`. Never edit `site/avatars/` by hand. The rules for the published file names are part of [Privacy](../README.md#privacy).
- **Code:** `scripts/avatar_mirror.py`, `scripts/pokemon_avatars.py`, `scripts/pokemon_images.py`.

## Avatar mirror

`avatar_mirror.py` makes `site/avatars/` hold exactly the files `participants.json` names, so the page serves every avatar from its own origin.

- For each racer it works out the URL behind the published file name from `archive/`.
- It downloads only the files the directory is missing, pausing 0.2 seconds between requests. A normal run makes no requests; the first run makes one per racer with an avatar.
- It checks each download before keeping it: the first bytes must match a PNG, JPEG, GIF or WebP image that agrees with the file extension, and the file must be at most 2,000,000 bytes.
- It deletes every file or symlink in `site/avatars/` that `participants.json` does not name, so a racer's old picture goes when a new one arrives.
- It copies a Pokémon avatar from `assets/pokemon/` instead, with no request (see [Pokémon avatars](#pokémon-avatars)). The sprite `pokemon-201-b.png` comes from `201-b.png`. A file is copied only if the directory is missing it or holds a copy that differs from the sprite, and only if the sprite is a PNG image. A local read is cheap, so every run compares the copies, and a replaced sprite reaches `site/avatars/` on the next run.
- A download or check that fails prints a `Warning:` line and the run continues; the file is tried again on the next run, and meanwhile the page shows the racer's monogram. A sprite that cannot be read or is not a PNG fails the same way.
- The script ends with `Avatars: N downloaded, C copied, M removed, K failed.` It exits with 1 only if the archive cannot be read, or if `participants.json` is unreadable or names a file the archive does not explain, in which case it asks you to run `build_stats.py` first. A `pokemon-` name with no sprite in `assets/pokemon/` counts as such a file, so it is treated like a stale `participants.json`.

## Pokémon avatars

A racer's avatar can be a Pokémon. The Pokémon is the racer's favourite, if their bio names one, or else a random one. A racer whose bio names `random` as their favourite gets a random one too. `scripts/pokemon_avatars.py` makes both choices, and `build_stats.py` publishes the result as the racer's avatar file name in `participants.json`.

### Precedence

For each racer, in this order:

1. A favourite Pokémon named in their bio, even when they have a racetime.gg avatar. If the favourite is `random`, a random Pokémon, again even over a racetime.gg avatar.
2. Their racetime.gg avatar, when they have one that can be mirrored (see [Privacy](../README.md#privacy)).
3. A random Pokémon.

Nobody is left without an avatar when `assets/pokemon/` has sprites.

### The phrase

The bio is read from `archive/racer-bios.json` (see [Bios](archive.md#bios)). The text is lowercased, accents are removed, and `♀` and `♂` become `f` and `m`. Each line is cut into words of letters and digits, so punctuation, such as the colon in "favourite pokemon: Mew", is ignored.

- A phrase is two words next to each other on one line. The first is favourite-like and the second Pokémon-like.
- A word is favourite-like if it is `fav` or `fave`, or at least 0.8 similar to `favourite` or `favorite`. It is Pokémon-like if it is `pkmn`, or at least 0.8 similar to `pokemon`. Similarity is the ratio of Python's `difflib.SequenceMatcher`, set as `PHRASE_WORD_CUTOFF`.
- "My" is not required, and neither is anything before the phrase.
- The words after the phrase are the name. One linking word, `is`, `s` (as in "pokemon's"), `are` or `was`, is skipped first.
- If the first word of the name is random-like, at least 0.8 similar to `random` (`RANDOM_WORDS`), the favourite is `random` and the rest of the name is ignored. So "favourite pokemon: Random!" and "fav pokemon is rando" both ask for a random Pokémon, but "my favourite pokemon changes at random" and "favourite pokemon randomizer" do not. No word of a Pokémon name is random-like, so this is checked before the name and never hides a Pokémon.
- The name is on the same line as the phrase. The name never continues onto the next line.
- Hits are tried in text order, line by line. The first hit whose name resolves, or is `random`, wins. A hit whose name resolves to nothing is skipped, and a later hit can still win.

### The name

At most 4 words (`NAME_MAX_WORDS`) after the phrase are used, so "pikachu is the best" still resolves. The name is tried as the first 4 words, then the first 3, 2 and 1, longest first. Names come from the pokédex's two lists: Pokémon, including alternate forms such as `charizard-mega-x` (ids from 10001), and species, such as `deoxys`. A name only counts if its id has a sprite.

1. **Exact:** a candidate matches if its words joined without spaces equal a name with its hyphens removed, or if its words, in any order, equal a name's hyphen-separated parts. This is tried at every length before any fuzzy match, so an exact short name beats a fuzzy longer one. If both keys match, the lower id wins.
2. **Fuzzy:** otherwise, the candidate is compared, words sorted, with every name, again with its parts sorted. A name needs a similarity of at least 0.85 (`NAME_CUTOFF`), which keeps ordinary words such as "starter", "unknown" or "zard" from fuzzing to Stantler, Unown or Zarude. The best similarity wins and a tie goes to the lower id. The longest candidate with any name at or above the cutoff wins.

So `Mr. Mime` and `mrmime` give Mr. Mime, `Mega Charizard X` gives `charizard-mega-x`, `Pikachuu` gives Pikachu, and `Deoxys` gives Deoxys.

### Assignment

`assign` takes the racers in a fixed order: by the position of their first included race, then by user ID. It makes two passes.

1. **Favourites:** each racer whose bio names a Pokémon gets it.
2. **Random:** each racer whose favourite is `random`, and each racer with neither a favourite nor a racetime.gg avatar, gets a random base Pokémon, in their place in the order. A favourite of `random` therefore gives a racer what they would get with no racetime.gg avatar at all. The Pokémon is picked from the 1,025 species, that is, ids 1 to 1025 and never an alternate form. It is one of those the fewest racers already use. Racers who take a favourite count as users of its exact id, and so do those earlier in this pass. A favourite alternate form, such as `charizard-mega-x`, therefore does not take its base species out of the random pass. Ties are broken by a ranking from the SHA-256 of `<user id>/<pokédex number>`, lowest first, so the pick is fixed per racer.

A Pokémon can have several sprites, such as the Unown letters. The racer gets the sprite file, favourite or random, that the fewest racers use, with the same tie-break from the SHA-256 of `<user id>/<file name>`. A published name is `pokemon-` plus the file name, for example `pokemon-201-b.png`. There is no limit on how many racers share a Pokémon: once every one is in use, the least used are picked. If `assets/pokemon/` has no species, nobody gets a random Pokémon.

### Stability

The order depends only on racers' first races, so a new racer joins at the end and their random Pokémon changes nobody else's, unless a race reaches the archive after a later-starting one. A random pick changes only when something before the racer's turn changes: a favourite is added, changed or removed, or an earlier racer enters or leaves the random pass by gaining or losing an upload or a favourite, or, while they have an upload, by naming or dropping `random`. A favourite Pokémon changes only with the bio, but its sprite can change when an earlier racer starts or stops favouring the same Pokémon.

### The pokédex

`assets/pokemon/` is committed and never deployed; `site/avatars/` gets copies of the sprites in use. It holds the sprites, named `<pokédex number>.png` or `<number>-<variant>.png`, and `pokedex.json`, which maps ids to names for `pokemon` and `species`. `python3 scripts/pokemon_avatars.py --refresh-pokedex` rewrites `pokedex.json` from PokeAPI's `/pokemon/` and `/pokemon-species/` lists, following their `next` links, and writes nothing if a list is incomplete, changes while it is read, or links outside PokeAPI. CI never runs it. A new generation also needs its sprites added.

### The sprites

`python3 scripts/pokemon_images.py` generates the committed sprites from 96 × 96 originals in `_tmp_assets/pokemon/`, which are not committed. It skips the `0.png` placeholder and never touches `pokedex.json`; run `avatar_mirror.py` afterwards. Each sprite's transparency is cut at 50 %, as the palette PNG stores it, so a pixel less than half opaque is margin. The sprite is trimmed of its transparent margin and centred in a square whose side is its longest side, so that side runs edge to edge with no margin. It is scaled up with nearest-neighbour sampling by the smallest whole factor that reaches 256 pixels (`MIN_SIDE`, four times the 64-pixel racer-page avatar, so every avatar scales it down), and written as a palette PNG. A sprite whose written content is not exactly its trimmed content scaled up fails and is not replaced. Rerunning it on its own output changes nothing.

## Avatar changes

`avatar_changes` in `participants.json` lists the racers whose avatar changed, newest first, each with the Eastern Time date the change was first seen. The dashboard's **Latest avatars** shows them (see [Latest results](dashboard.md#latest-results)). A racer's avatar follows the [precedence](#precedence) above, so two kinds of change count, and the later one dates the racer:

- **Upload:** the racer's mirrorable racetime.gg upload changed, appeared or went. `avatar_mirror.py` sorts a racer's avatar observations as it does to pick the current avatar (see [Privacy](../README.md#privacy)), and compares their mirrored file names, so an upload that cannot be mirrored counts as none. A change needs an earlier observation with a different value; its time is the first observation of the current value after the last different one. A racer whose upload went shows the random Pokémon that replaced it. An upload change does not count while a favourite Pokémon, or a favourite of `random`, hides the upload.
- **Favourite:** the favourite Pokémon named in the racer's bio was added, switched or removed. `pokemon_avatars.py` reads the favourite of each bio in the racer's [bio history](archive.md#bios), oldest first; the change is the last bio whose favourite differs from the one before it, at that bio's `since`. An edit that keeps the same favourite is not a change. `random` is a favourite of its own, so naming or dropping it is a change. A racer without an upload who names or drops `random` is listed too, although their random Pokémon stays the same.
- **Not counted:** a racer's first avatar, so a new racer is not listed, and a random Pokémon that moves because other racers changed (see [Stability](#stability)).
- **When:** the archive can only date what it saw, so a change is dated by the sync that first saw it, and nothing from before the archive tracked it is known. The leaderboards are synced every two hours. A bio is only checked after its racer races, so a favourite picked between races is dated by the check after their next race; bio history began on 2026-11-05. The leaderboard file keeps only each racer's latest upload, so a switch back to an earlier picture before any race file recorded the one in between leaves no trace. Changes on the same day are ordered by the exact time first seen.
