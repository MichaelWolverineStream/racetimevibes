import json
import tempfile
import unittest
from pathlib import Path

from scripts import version_assets


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "site"

INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
  <head>
    <meta name="app-version" content="">
    <link rel="stylesheet" href="styles.css">
  </head>
  <body>
    <script src="app.js" defer></script>
    <script src="leaderboard.js" defer></script>
    <script src="version-check.js" defer></script>
  </body>
</html>
"""


def build_site(directory, **overrides):
    site = Path(directory)
    (site / version_assets.INDEX_NAME).write_text(
        overrides.get("index", INDEX_TEMPLATE), encoding="utf-8"
    )
    for asset in version_assets.VERSIONED_ASSETS:
        (site / asset).write_text(overrides.get(asset, f"/* {asset} */\n"), encoding="utf-8")
    return site


class StampTests(unittest.TestCase):
    def test_stamps_every_asset_reference_and_the_meta_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            version = version_assets.stamp(site)
            html = (site / version_assets.INDEX_NAME).read_text(encoding="utf-8")

        self.assertEqual(len(version), version_assets.VERSION_LENGTH)
        self.assertIn(f'<meta name="app-version" content="{version}">', html)
        for asset in version_assets.VERSIONED_ASSETS:
            with self.subTest(asset=asset):
                self.assertIn(f'"{asset}?v={version}"', html)

    def test_manifest_matches_the_stamped_version(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            version = version_assets.stamp(site)
            manifest = (site / version_assets.VERSION_NAME).read_text(encoding="utf-8")

        self.assertEqual(json.loads(manifest), {"version": version})
        self.assertTrue(manifest.endswith("\n"))
        self.assertNotIn(", ", manifest)

    def test_restamping_unchanged_files_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            first = version_assets.stamp(site)
            first_html = (site / version_assets.INDEX_NAME).read_text(encoding="utf-8")
            second = version_assets.stamp(site)
            second_html = (site / version_assets.INDEX_NAME).read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(first_html, second_html)

    def test_version_changes_when_an_asset_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            before = version_assets.stamp(site)
            (site / "app.js").write_text("/* changed */\n", encoding="utf-8")
            after = version_assets.stamp(site)

        self.assertNotEqual(before, after)

    def test_version_changes_when_the_markup_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            before = version_assets.stamp(site)
            index_path = site / version_assets.INDEX_NAME
            index_path.write_text(
                index_path.read_text(encoding="utf-8").replace(
                    "<body>", "<body>\n    <p>new copy</p>"
                ),
                encoding="utf-8",
            )
            after = version_assets.stamp(site)

        self.assertNotEqual(before, after)

    def test_is_current_tracks_asset_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            version_assets.stamp(site)
            self.assertTrue(version_assets.is_current(site))
            (site / "styles.css").write_text("body { color: red; }\n", encoding="utf-8")
            self.assertFalse(version_assets.is_current(site))

    def test_missing_asset_reference_is_rejected(self):
        index = INDEX_TEMPLATE.replace('<script src="leaderboard.js" defer></script>', "")
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory, index=index)
            with self.assertRaisesRegex(version_assets.VersionError, "leaderboard.js"):
                version_assets.stamp(site)

    def test_missing_meta_tag_is_rejected(self):
        index = INDEX_TEMPLATE.replace('<meta name="app-version" content="">', "")
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory, index=index)
            with self.assertRaisesRegex(version_assets.VersionError, "app-version"):
                version_assets.stamp(site)

    def test_missing_asset_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            site = build_site(directory)
            (site / "app.js").unlink()
            with self.assertRaisesRegex(version_assets.VersionError, "app.js"):
                version_assets.stamp(site)


class CommittedSiteTests(unittest.TestCase):
    def test_committed_site_carries_its_current_version(self):
        self.assertTrue(
            version_assets.is_current(SITE_DIR),
            "site/index.html or site/version.json is stale; "
            "run python3 scripts/version_assets.py and commit the result",
        )

    def test_every_versioned_asset_is_referenced_with_the_published_version(self):
        version = json.loads(
            (SITE_DIR / version_assets.VERSION_NAME).read_text(encoding="utf-8")
        )["version"]
        html = (SITE_DIR / version_assets.INDEX_NAME).read_text(encoding="utf-8")
        self.assertIn(f'<meta name="app-version" content="{version}">', html)
        for asset in version_assets.VERSIONED_ASSETS:
            with self.subTest(asset=asset):
                self.assertIn(f'"{asset}?v={version}"', html)


if __name__ == "__main__":
    unittest.main()
