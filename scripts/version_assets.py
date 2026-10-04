#!/usr/bin/env python3
"""Stamp the static site with a content version so browsers refetch after a deploy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


SITE_DIR = Path(__file__).resolve().parents[1] / "site"
INDEX_NAME = "index.html"
VERSION_NAME = "version.json"
VERSIONED_ASSETS = ("styles.css", "app.js", "leaderboard.js", "version-check.js")
VERSION_LENGTH = 12
VERSION_META_PATTERN = re.compile(r'(<meta name="app-version" content=")([^"]*)(">)')


class VersionError(RuntimeError):
    """Raised when the site cannot be stamped with a content version."""


def _reference_pattern(asset: str) -> re.Pattern[str]:
    return re.compile(r'((?:href|src)=")' + re.escape(asset) + r'(?:\?v=[^"]*)?(")')


def strip_version(html: str) -> str:
    """Return index.html with any existing version stamp removed."""
    for asset in VERSIONED_ASSETS:
        html = _reference_pattern(asset).sub(rf"\g<1>{asset}\g<2>", html)
    return VERSION_META_PATTERN.sub(r"\g<1>\g<3>", html)


def compute_version(stripped_html: str, assets: dict[str, bytes]) -> str:
    """Hash the unstamped markup and every versioned asset into one short token."""
    digest = hashlib.sha256()
    digest.update(stripped_html.encode("utf-8"))
    for asset in VERSIONED_ASSETS:
        digest.update(asset.encode("utf-8"))
        digest.update(assets[asset])
    return digest.hexdigest()[:VERSION_LENGTH]


def apply_version(stripped_html: str, version: str) -> str:
    """Return index.html with every asset reference and the meta tag stamped."""
    html = stripped_html
    for asset in VERSIONED_ASSETS:
        html, count = _reference_pattern(asset).subn(
            rf"\g<1>{asset}?v={version}\g<2>", html
        )
        if count == 0:
            raise VersionError(f"{INDEX_NAME} does not reference {asset}")
    html, count = VERSION_META_PATTERN.subn(rf"\g<1>{version}\g<3>", html)
    if count != 1:
        raise VersionError(f"{INDEX_NAME} needs exactly one app-version meta tag")
    return html


def render(site_dir: Path = SITE_DIR) -> tuple[str, str, str]:
    """Return the content version, the stamped index.html, and the version manifest."""
    index_path = site_dir / INDEX_NAME
    if not index_path.is_file():
        raise VersionError(f"{index_path} does not exist")

    assets: dict[str, bytes] = {}
    for asset in VERSIONED_ASSETS:
        asset_path = site_dir / asset
        if not asset_path.is_file():
            raise VersionError(f"{asset_path} does not exist")
        assets[asset] = asset_path.read_bytes()

    stripped = strip_version(index_path.read_text(encoding="utf-8"))
    version = compute_version(stripped, assets)
    manifest = json.dumps({"version": version}, separators=(",", ":")) + "\n"
    return version, apply_version(stripped, version), manifest


def is_current(site_dir: Path = SITE_DIR) -> bool:
    """Report whether the committed site already carries its current content version."""
    _version, index_html, manifest = render(site_dir)
    index_path = site_dir / INDEX_NAME
    version_path = site_dir / VERSION_NAME
    if not version_path.is_file():
        return False
    return (
        index_path.read_text(encoding="utf-8") == index_html
        and version_path.read_text(encoding="utf-8") == manifest
    )


def stamp(site_dir: Path = SITE_DIR) -> str:
    """Write the stamped index.html and version manifest, returning the version."""
    version, index_html, manifest = render(site_dir)
    (site_dir / INDEX_NAME).write_text(index_html, encoding="utf-8")
    (site_dir / VERSION_NAME).write_text(manifest, encoding="utf-8")
    return version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site",
        type=Path,
        default=SITE_DIR,
        help=f"site directory to stamp (default: {SITE_DIR})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the site needs restamping instead of writing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.check:
            if is_current(args.site):
                print("Site version stamp is current.")
                return 0
            print(
                "Site version stamp is stale; run python3 scripts/version_assets.py",
                file=sys.stderr,
            )
            return 1
        version = stamp(args.site)
    except VersionError as error:
        print(f"Version stamp failed: {error}", file=sys.stderr)
        return 1

    print(f"Stamped {args.site} with version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
