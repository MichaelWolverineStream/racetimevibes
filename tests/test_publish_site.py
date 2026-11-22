import tempfile
import unittest
from pathlib import Path

from scripts.publish_site import PublishError, publish, strip_css_comments


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "site"


class StripCssCommentsTests(unittest.TestCase):
    def test_removes_every_comment_line_and_keeps_the_rest_byte_for_byte(self):
        cases = [
            ("one line", "/* Section */\n.a {\n  color: red;\n}\n", ".a {\n  color: red;\n}\n"),
            ("several lines", ".a {}\n/* One\n   two. */\n.b {}\n", ".a {}\n.b {}\n"),
            ("indented in a block", ".a {\n  /* Why */\n  color: red;\n}\n", ".a {\n  color: red;\n}\n"),
            (
                "indented in @media",
                "@media (max-width: 620px) {\n  /* Phones */\n  .a { color: red; }\n}\n",
                "@media (max-width: 620px) {\n  .a { color: red; }\n}\n",
            ),
            ("between blank lines", ".a {}\n\n/* Next */\n\n.b {}\n", ".a {}\n\n\n.b {}\n"),
            ("padded, with CRLF", ".a {}\r\n\t/* x */  \r\n.b {}\r\n", ".a {}\r\n.b {}\r\n"),
            ("last line, no newline", ".a {}\n/* End */", ".a {}\n"),
            ("whole file", "/* Only */\n", ""),
            ("no comment", ".a {\n\n  color: red;   \n}", ".a {\n\n  color: red;   \n}"),
        ]
        for name, css, expected in cases:
            with self.subTest(name):
                self.assertEqual(strip_css_comments(css), expected)

    def test_keeps_escaped_and_quoted_comment_markers(self):
        kept = (
            '.a::before {\n  content: "/* not */";\n}\n'
            '.b {\n  background: url("a/*b*/c.png");\n}\n'
            ".c::after {\n  content: 'it\\'s /* not */';\n}\n"
            '.d::after {\n  content: "\\"/*";\n}\n'
            '.e::after {\n  content: "one \\\n/* two */";\n}\n'
            '.f::after {\n  content: "one \\\r\n/* two */";\n}\n'
            ".g\\/*h {\n  color: red;\n}\n"
        )
        self.assertEqual(strip_css_comments(kept + "/* Real */\n"), kept)

    def test_rejects_a_comment_that_shares_a_line_with_code(self):
        cases = [
            ("code before", ".a {}\n.b { color: red; } /* Why */\n", 2),
            ("code after", "/* Why */ .a {}\n", 1),
            ("between tokens", ".x {}\n\n.a/**/.b {}\n", 3),
            ("ends on a line with code", "/* One\n   two */ .a {}\n", 1),
            ("two on one line", "/* One */ /* two */\n", 1),
        ]
        for name, css, line in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(PublishError, rf"^line {line}: a comment must have its lines to itself$"):
                    strip_css_comments(css)

    def test_rejects_an_unterminated_comment_or_string(self):
        cases = [
            ("comment", ".a {}\n/* Open\n.b {}\n", 2, "comment"),
            ("string across a line", '.a::before {\n  content: "open;\n}\n', 2, "string"),
            ("string across a bare CR", '.a::before {\n  content: "open\r/* c */";\n}\n', 2, "string"),
            ("string across a form feed", '.a::before {\n  content: "open\f/* c */";\n}\n', 2, "string"),
            ("string at the end", '.a::before { content: "open', 1, "string"),
            ("escape at the end", '.a::before { content: "open\\', 1, "string"),
        ]
        for name, css, line, kind in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(PublishError, rf"^line {line}: unterminated {kind}$"):
                    strip_css_comments(css)


class PublishTests(unittest.TestCase):
    def make_site(self, files: dict[str, str | bytes]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        site = Path(temp.name) / "site"
        for name, content in files.items():
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return site

    def files_under(self, root: Path) -> dict[Path, bytes]:
        return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def test_publish_strips_every_stylesheet_and_copies_everything_else_unchanged(self):
        site = self.make_site(
            {
                "index.html": "<!-- Kept -->\n<style>\n/* Kept */\n</style>\n",
                "a.css": "/* Gone */\n.a {}\n",
                "assets/b.css": ".b {}\n  /* Gone */\n",
                "a.js": "/* Kept */\nconst a = 1;\n",
                "data/x.json": '{"a":"/* kept */"}\n',
            }
        )
        out = site.parent / "out"

        saved = publish(site, out)

        source, published = self.files_under(site), self.files_under(out)
        self.assertEqual(published.keys(), source.keys())
        stylesheets = {name for name in source if name.suffix == ".css"}
        for name, data in source.items():
            with self.subTest(str(name)):
                expected = strip_css_comments(data.decode()).encode() if name in stylesheets else data
                self.assertEqual(published[name], expected)
        self.assertEqual(saved, sum(len(source[name]) - len(published[name]) for name in stylesheets))
        self.assertGreater(saved, 0)

    def test_publish_refuses_an_output_directory_that_exists_or_sits_inside_the_site(self):
        site = self.make_site({"a.css": ".a {}\n"})
        existing = site.parent / "existing"
        existing.mkdir()
        for name, out in [("exists", existing), ("inside the site", site / "out")]:
            with self.subTest(name):
                with self.assertRaises(PublishError):
                    publish(site, out)
        self.assertEqual(list(existing.iterdir()), [])
        self.assertFalse((site / "out").exists())

    def test_publish_names_the_stylesheet_that_fails_and_writes_nothing(self):
        cases = [
            ("comment beside code", b".b {} /* Why */\n", r"line 1: a comment must have its lines to itself"),
            ("not UTF-8", b".b::after { content: '\xff'; }\n", r"not UTF-8 \(invalid start byte\)"),
        ]
        for name, bad, message in cases:
            with self.subTest(name):
                site = self.make_site({"a.css": ".a {}\n", "assets/bad.css": bad})
                out = site.parent / "out"

                with self.assertRaisesRegex(PublishError, rf"^assets/bad\.css: {message}$"):
                    publish(site, out)
                self.assertFalse(out.exists())


class SiteStylesheetTests(unittest.TestCase):
    def test_every_site_stylesheet_publishes_without_comments(self):
        stylesheets = sorted(SITE_DIR.rglob("*.css"))
        self.assertTrue(stylesheets, f"no stylesheets under {SITE_DIR}")
        for path in stylesheets:
            with self.subTest(path.name):
                stripped = strip_css_comments(path.read_text(encoding="utf-8"))
                # A second pass finding nothing means no comment survived the first.
                self.assertEqual(strip_css_comments(stripped), stripped)


if __name__ == "__main__":
    unittest.main()
