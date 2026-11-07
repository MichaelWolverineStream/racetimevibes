---
paths:
  - "scripts/avatar_mirror.py"
  - "scripts/pokemon_avatars.py"
  - "scripts/pokemon_images.py"
  - "assets/pokemon/**"
  - "site/avatars/**"
  - "tests/test_avatar_mirror.py"
  - "tests/test_pokemon_avatars.py"
  - "tests/test_pokemon_images.py"
---

# Avatars and Pokémon

- **Spec:** `docs/avatars.md` (the mirror, Pokémon precedence, phrase and name matching, assignment, sprites, avatar changes). The published file-name rules are part of README › Privacy. Update them in the same change.
- Everything here is generated. Never add, edit, delete or hand-pick a file:
  - `site/avatars/`: `python3 scripts/avatar_mirror.py`, after `build_stats.py`.
  - `assets/pokemon/pokedex.json`: `python3 scripts/pokemon_avatars.py --refresh-pokedex`.
  - `assets/pokemon/*.png`: `python3 scripts/pokemon_images.py` from `_tmp_assets/pokemon/`, then `avatar_mirror.py`.
- Keep the random assignment stable as described under Stability in `docs/avatars.md`: a new racer joins at the end of the order.
