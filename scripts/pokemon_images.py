#!/usr/bin/env python3
"""Fit Pokémon sprites to the avatar frame: trimmed, square, 7/8 filled, upscaled 1:N."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# Supports both `python3 scripts/pokemon_images.py` and `from scripts import pokemon_images`.
try:
    from scripts import pokemon_avatars
except ImportError:
    import pokemon_avatars


DEFAULT_SOURCE_DIR = Path(__file__).resolve().parents[1] / "_tmp_assets" / "pokemon"
DEFAULT_OUT_DIR = pokemon_avatars.POKEMON_DIR
FILL_RATIO = 0.875
# Four times the 64 px racer-page avatar, so every display, up to 3x screens and the
# 72 px share card, scales the sprite down.
MIN_SIDE = 256
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PALETTE_COLOUR_TYPE = 3
# Fully transparent pixels become one colour; the transparent border makes -trim start
# from a transparent corner even when a sprite touches the edge of its frame.
NORMALISE = [
    "-background", "none", "-alpha", "background",
    "-bordercolor", "none", "-border", "1", "-trim", "+repage",
]


class PokemonImageError(RuntimeError):
    """Raised when a sprite cannot be measured or converted."""


def frame(content_width: int, content_height: int) -> tuple[int, int]:
    """Return (square side, whole upscale factor) for trimmed content of this size.

    The content's longest side fills FILL_RATIO of the square side; the factor is the
    smallest whole number that makes side * factor at least MIN_SIDE.
    """
    if content_width <= 0 or content_height <= 0:
        raise PokemonImageError(
            f"content size must be positive: {content_width}x{content_height}"
        )
    side = math.ceil(max(content_width, content_height) / FILL_RATIO)
    factor = max(1, math.ceil(MIN_SIDE / side))
    return side, factor


def png_size(path: Path) -> tuple[int, int, int]:
    """Return (width, height, colour type) from a PNG's IHDR chunk."""
    with path.open("rb") as handle:
        header = handle.read(26)
    if len(header) < 26 or header[:8] != PNG_SIGNATURE:
        raise PokemonImageError(f"{path.name} is not a PNG")
    if header[12:16] != b"IHDR":
        raise PokemonImageError(f"{path.name} has no IHDR chunk first")
    width, height = struct.unpack(">II", header[16:24])
    return width, height, header[25]


def run_magick(arguments: list[str], source: Path) -> str:
    """Run magick and return stdout; failures raise with stderr in the message."""
    result = subprocess.run(["magick", *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        raise PokemonImageError(f"magick failed for {source.name}: {result.stderr.strip()}")
    return result.stdout


def content_size(source: Path) -> tuple[int, int]:
    """Return the (width, height) of a sprite's opaque content after trimming."""
    output = run_magick([str(source), *NORMALISE, "-format", "%w %h", "info:"], source)
    parts = output.split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise PokemonImageError(f"cannot read the size of {source.name}: {output!r}")
    return int(parts[0]), int(parts[1])


def convert_sprite(source: Path, target: Path) -> int:
    """Write the fitted sprite to target and return its square side in pixels.

    The output goes to a temporary file beside target and replaces it only once its
    geometry and colour type are verified.
    """
    side, factor = frame(*content_size(source))
    temporary = target.with_name(target.name + ".tmp")
    try:
        run_magick(
            [
                str(source), *NORMALISE,
                "-gravity", "center", "-extent", f"{side}x{side}",
                "-filter", "point", "-resize", f"{factor * 100}%",
                "-define", "png:compression-level=9", f"PNG8:{temporary}",
            ],
            source,
        )
        width, height, colour_type = png_size(temporary)
        if width != height or width != side * factor or width < MIN_SIDE:
            raise PokemonImageError(
                f"{source.name} came out {width}x{height}, expected {side * factor} square"
            )
        if colour_type != PALETTE_COLOUR_TYPE:
            raise PokemonImageError(
                f"{source.name} came out with colour type {colour_type}, not palette"
            )
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return width


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    if shutil.which("magick") is None:
        print("error: ImageMagick 7 (`magick`) is not on PATH", file=sys.stderr)
        return 1
    if not args.source.is_dir():
        print(f"error: source directory not found: {args.source}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sprites = [
        path for path in sorted(args.source.glob("*.png"))
        if pokemon_avatars.SPRITE_NAME.fullmatch(path.name)
    ]
    converted = 0
    failed = 0
    for path in sprites:
        try:
            convert_sprite(path, args.out_dir / path.name)
        except PokemonImageError as error:
            print(f"error: {error}", file=sys.stderr)
            failed += 1
        else:
            converted += 1
    suffix = f", {failed} failed" if failed else ""
    print(f"Sprites: {converted} converted{suffix}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
