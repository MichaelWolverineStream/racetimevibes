#!/usr/bin/env python3
"""Convert badge art to the site standard: 128x128 transparent WebP plus a still."""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
from pathlib import Path


DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "site" / "assets" / "badges"
SIZE = 128
SVG_DENSITY = 1152
SMOOTH_QUALITY = 90
ANIMATION_FLAG = 0x02


class BadgeImageError(RuntimeError):
    """Raised when a badge image cannot be read or converted."""


def read_webp(path: Path) -> tuple[int, int, bool, int | None]:
    """Return (width, height, animated, loop count) from a WebP's headers."""
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise BadgeImageError(f"{path.name} is not a WebP")
    kind = data[12:16]
    if kind == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF, False, None
    if kind == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, False, None
    if kind != b"VP8X":
        raise BadgeImageError(f"{path.name} has unknown chunk {kind!r}")
    width = int.from_bytes(data[24:27], "little") + 1
    height = int.from_bytes(data[27:30], "little") + 1
    if not data[20] & ANIMATION_FLAG:
        return width, height, False, None
    anim = data.index(b"ANIM")
    return width, height, True, struct.unpack("<H", data[anim + 12 : anim + 14])[0]


def is_standard_webp(path: Path) -> bool:
    """True for a WebP on a 128x128 canvas that is static or loops forever."""
    width, height, animated, loop = read_webp(path)
    return (width, height) == (SIZE, SIZE) and (not animated or loop == 0)


def encode_options(source: Path, pixel_art: bool) -> list[str]:
    """Return the resize and encoder options for the chosen treatment."""
    if source.suffix.lower() == ".svg":
        return ["-filter", "Lanczos", "-resize", f"{SIZE}x{SIZE}", "-define", "webp:lossless=true"]
    if pixel_art:
        data = subprocess.run(
            ["magick", "identify", "-format", "%w %h\n", f"{source}[0]"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        factor = -(-SIZE // max(int(data[0]), int(data[1])))
        return [
            "-filter", "point", "-resize", f"{factor * 100}%",
            "-filter", "Lanczos", "-resize", f"{SIZE}x{SIZE}",
            "-define", "webp:lossless=true",
        ]
    return ["-filter", "Lanczos", "-resize", f"{SIZE}x{SIZE}", "-quality", str(SMOOTH_QUALITY)]


def convert(source: Path, target: Path, options: list[str], frame: int | None) -> None:
    """Write target from source; frame keeps only that frame, None keeps them all."""
    command = ["magick"]
    if source.suffix.lower() == ".svg":
        command += ["-background", "none", "-density", str(SVG_DENSITY)]
    command += [str(source), "-coalesce"]
    if frame is not None:
        if frame > 0:
            command += ["-delete", f"0-{frame - 1}"]
        command += ["-delete", "1--1"]
    command += options
    command += ["-background", "none", "-gravity", "center", "-extent", f"{SIZE}x{SIZE}", "-loop", "0", str(target)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise BadgeImageError(f"magick failed for {source}: {result.stderr.strip()}")


def convert_badge(source: Path, badge_id: str, out_dir: Path, pixel_art: bool, still_frame: int) -> None:
    main_path = out_dir / f"{badge_id}.webp"
    still_path = out_dir / f"{badge_id}-still.webp"
    out_dir.mkdir(parents=True, exist_ok=True)
    options = encode_options(source, pixel_art)
    if source.suffix.lower() == ".webp" and is_standard_webp(source):
        shutil.copyfile(source, main_path)
    else:
        convert(source, main_path, options, None)
    if read_webp(main_path)[2]:
        convert(main_path, still_path, ["-define", "webp:lossless=true"], still_frame)
    else:
        shutil.copyfile(main_path, still_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("badge_id")
    parser.add_argument("--pixel-art", action="store_true", help="upscale by whole factors first")
    parser.add_argument("--still-frame", type=int, default=0, help="frame used for the still")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    if shutil.which("magick") is None:
        print("error: ImageMagick 7 (`magick`) is not on PATH", file=sys.stderr)
        return 1
    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 1
    try:
        convert_badge(args.source, args.badge_id, args.out_dir, args.pixel_art, args.still_frame)
    except (BadgeImageError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out_dir / args.badge_id}.webp and -still.webp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
