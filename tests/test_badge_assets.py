import unittest
from pathlib import Path

from scripts import badge_images, highlights


SITE_DIR = Path(__file__).resolve().parents[1] / "site"
BADGE_DIR = SITE_DIR / "assets" / "badges"


class BadgeAssetTests(unittest.TestCase):
    def test_every_badge_has_standard_image_and_still(self):
        for badge in highlights.BADGES:
            image, still = (SITE_DIR / p for p in highlights.badge_image_paths(badge.id))
            for path in (image, still):
                with self.subTest(badge=badge.id, file=path.name):
                    self.assertTrue(path.is_file())
                    width, height, _, _ = badge_images.read_webp(path)
                    self.assertEqual((width, height), (badge_images.SIZE, badge_images.SIZE))
            with self.subTest(badge=badge.id, check="still"):
                self.assertFalse(badge_images.read_webp(still)[2])
            with self.subTest(badge=badge.id, check="loop"):
                _, _, animated, loop = badge_images.read_webp(image)
                if animated:
                    self.assertEqual(loop, 0)

    def test_folder_holds_only_badge_images(self):
        expected = {
            Path(p).name
            for badge in highlights.BADGES
            for p in highlights.badge_image_paths(badge.id)
        }
        self.assertEqual({p.name for p in BADGE_DIR.iterdir() if not p.name.startswith(".")}, expected)


if __name__ == "__main__":
    unittest.main()
