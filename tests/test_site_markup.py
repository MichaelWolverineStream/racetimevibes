import unittest
from html.parser import HTMLParser
from pathlib import Path


SITE_DIR = Path(__file__).resolve().parents[1] / "site"
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class MainChildren(HTMLParser):
    """The elements directly inside <main>, in document order, as (tag, attributes)."""

    def __init__(self):
        super().__init__()
        self.depth = None
        self.children = []

    def handle_starttag(self, tag, attrs):
        if self.depth is None:
            if tag == "main":
                self.depth = 0
            return
        if self.depth == 0:
            self.children.append((tag, dict(attrs)))
        if tag not in VOID_ELEMENTS:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.depth is None:
            return
        if self.depth == 0:
            self.depth = None
            return
        self.depth -= 1


def main_children():
    parser = MainChildren()
    parser.feed((SITE_DIR / "index.html").read_text(encoding="utf-8"))
    parser.close()
    return parser.children


# A section that shows before its data renders is pushed down when the sections above it appear, which
# Lighthouse counts as Cumulative Layout Shift (docs/dashboard.md, Page layout and navigation).
class SiteMarkupTests(unittest.TestCase):
    def test_every_section_in_main_starts_hidden(self):
        sections = [attrs for tag, attrs in main_children() if tag == "section"]
        self.assertIn("leaderboard", [attrs.get("id") for attrs in sections])

        shown = [attrs.get("id") for attrs in sections if "hidden" not in attrs]
        self.assertEqual(shown, [], "sections visible before their data renders")

    def test_racer_load_status_reserves_the_screen_before_the_sections(self):
        children = main_children()
        status = [attrs.get("id") for _, attrs in children].index("racer-load-status")
        first_section = next(index for index, (tag, _) in enumerate(children) if tag == "section")

        self.assertLess(status, first_section)
        self.assertIn("load-status--page", children[status][1]["class"].split())


if __name__ == "__main__":
    unittest.main()
