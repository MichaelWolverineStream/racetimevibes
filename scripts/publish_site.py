#!/usr/bin/env python3
"""Copy the site for deployment, with every comment stripped from its stylesheets.

The source keeps its comments; only the deployed copy loses them. Whitespace stays, so the
served CSS is still readable. Each comment must have its lines to itself: a comment that shares
a line with code fails the publish instead of being guessed at, so removing one can never join
two tokens (`.a/**/.b` would become `.a.b`).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


SITE_DIR = Path(__file__).resolve().parents[1] / "site"
LINE_PADDING = " \t\r"
CSS_NEWLINES = "\n\r\f"  # CSS reads each of these, and CRLF, as a newline, which ends a string.


class PublishError(RuntimeError):
    """Raised when the site cannot be published."""


def _line_number(css: str, index: int) -> int:
    return css.count("\n", 0, index) + 1


def _comment_spans(css: str) -> list[tuple[int, int]]:
    """Return the (start, end) of every comment, skipping escaped and quoted comment markers."""
    spans: list[tuple[int, int]] = []
    quote = ""
    quote_start = 0
    index = 0
    while index < len(css):
        char = css[index]
        if char == "\\":
            # An escape takes the next character literally, in a string or out (`.a\/*b`).
            # In a string, a backslash before a newline continues the string, and CRLF is one newline.
            index += 3 if css.startswith("\r\n", index + 1) else 2
            continue
        if quote:
            if char in CSS_NEWLINES:
                raise PublishError(f"line {_line_number(css, quote_start)}: unterminated string")
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            quote_start = index
        elif css.startswith("/*", index):
            end = css.find("*/", index + 2)
            if end == -1:
                raise PublishError(f"line {_line_number(css, index)}: unterminated comment")
            spans.append((index, end + 2))
            index = end + 2
            continue
        index += 1
    if quote:
        raise PublishError(f"line {_line_number(css, quote_start)}: unterminated string")
    return spans


def strip_css_comments(css: str) -> str:
    """Return the stylesheet without the lines its comments occupy; every other line is unchanged."""
    pieces: list[str] = []
    kept_from = 0
    for start, end in _comment_spans(css):
        line_start = css.rfind("\n", 0, start) + 1
        line_end = css.find("\n", end)
        line_end = len(css) if line_end == -1 else line_end
        if css[line_start:start].strip(LINE_PADDING) or css[end:line_end].strip(LINE_PADDING):
            raise PublishError(
                f"line {_line_number(css, start)}: a comment must have its lines to itself"
            )
        pieces.append(css[kept_from:line_start])
        kept_from = min(line_end + 1, len(css))
    pieces.append(css[kept_from:])
    return "".join(pieces)


def publish(site_dir: Path, out_dir: Path) -> int:
    """Copy site_dir to a new out_dir with CSS comments stripped, returning the bytes saved."""
    if not site_dir.is_dir():
        raise PublishError(f"{site_dir} is not a directory")
    if out_dir.exists():
        raise PublishError(f"{out_dir} already exists")
    if out_dir.resolve().is_relative_to(site_dir.resolve()):
        raise PublishError(f"{out_dir} is inside {site_dir}")

    # Strip every stylesheet before copying anything, so a bad one leaves no partial output.
    stripped: dict[Path, bytes] = {}
    saved = 0
    for path in sorted(site_dir.rglob("*.css")):
        source = path.read_bytes()
        try:
            result = strip_css_comments(source.decode("utf-8")).encode("utf-8")
        except UnicodeDecodeError as error:
            raise PublishError(f"{path.relative_to(site_dir)}: not UTF-8 ({error.reason})") from None
        except PublishError as error:
            raise PublishError(f"{path.relative_to(site_dir)}: {error}") from None
        stripped[path.relative_to(site_dir)] = result
        saved += len(source) - len(result)

    shutil.copytree(site_dir, out_dir)
    for relative, result in stripped.items():
        (out_dir / relative).write_bytes(result)
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="directory to create with the deployable copy")
    parser.add_argument(
        "--site",
        type=Path,
        default=SITE_DIR,
        help=f"site directory to copy (default: {SITE_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        saved = publish(args.site, args.out)
    except PublishError as error:
        print(f"Publish failed: {error}", file=sys.stderr)
        return 1

    print(f"Published {args.site} to {args.out}; stripping CSS comments saved {saved:,} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
