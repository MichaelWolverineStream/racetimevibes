import re
import unittest
from pathlib import Path


SITE_DIR = Path(__file__).resolve().parents[1] / "site"

ROOT_BLOCK = re.compile(r"^:root\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
DARK_BLOCK = re.compile(r'^:root\[data-theme="dark"\]\s*\{(.*?)^\}', re.MULTILINE | re.DOTALL)
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
DECLARATION = re.compile(r"^\s*(--[\w-]+)\s*:\s*(.+?)\s*;\s*$", re.MULTILINE)
# A colour the dark theme can't inherit: hex, a colour function, a named colour, or a bare `transparent`.
# A literal inside a mix counts too, but `transparent` inside one is the same in both themes.
LITERAL_COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\("
    r"|\b(?:white|black)\b"
    r"|^transparent$"
)
HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
ALIAS = re.compile(r"^var\((--[\w-]+)\)$")

THEMES = ("light", "dark")

# Contrast pairs as (foreground, background) token names, per WCAG 2.
# Data hues (--mode-*, --tag-*) and colour-blind separation are deliberately not asserted here:
# some light data hues sit below 3:1 today, and colour-blind separation needs simulation
# that is checked outside the suite.
TEXT_PAIRS = (
    [(fg, bg) for fg in ("--ink", "--muted") for bg in ("--panel", "--paper", "--row-hover")]
    + [("--accent", "--panel"), ("--accent", "--paper")]
    + [
        ("--on-fill", bg)
        for bg in (
            "--accent", "--accent-strong", "--teal", "--teal-deep",
            "--gold-ink", "--bronze-ink", "--silver-ink",
        )
    ]
    + [("--on-brand", "--brand-fill"), ("--on-brand", "--brand-fill-strong")]
    + [("--on-medal", bg) for bg in ("--gold", "--silver", "--bronze", "--diamond")]
    + [("--tooltip-ink", "--tooltip-bg")]
    + [
        ("--band-prime-ink", "--band-prime"),
        ("--band-normal-ink", "--band-normal"),
        ("--band-quiet-ink", "--band-quiet"),
    ]
    + [
        (fg, "--panel")
        for fg in (
            "--gold-ink", "--silver-ink", "--bronze-ink", "--diamond-ink",
            "--positive", "--negative", "--retro-link", "--retro-link-visited",
        )
    ]
)
# Gold on the nav band is 3.28:1 in light by existing design, so it is held to 3:1.
NON_TEXT_PAIRS = [
    ("--focus", "--panel"),
    ("--focus", "--paper"),
    ("--rule", "--panel"),
    ("--gold", "--brand-fill"),
    ("--gold", "--brand-fill-strong"),
]
TEXT_RATIO = 4.5
NON_TEXT_RATIO = 3.0


def parse_tokens(css):
    """Return (root_tokens, dark_tokens, dark_block_text) from a stylesheet."""
    css = COMMENT.sub("", css)
    root = ROOT_BLOCK.search(css)
    dark = DARK_BLOCK.search(css)
    if root is None or dark is None:
        raise ValueError("stylesheet needs a :root block and a :root[data-theme=\"dark\"] block")
    return (
        dict(DECLARATION.findall(root.group(1))),
        dict(DECLARATION.findall(dark.group(1))),
        dark.group(1),
    )


def theme_tokens(root, dark, theme):
    """The tokens in effect for a theme: dark overrides :root, everything else is inherited."""
    return {**root, **dark} if theme == "dark" else dict(root)


def resolve(name, tokens):
    """Follow var() aliases within one theme's tokens to a final value."""
    seen = []
    while True:
        if name in seen:
            raise ValueError("cycle: " + " -> ".join(seen + [name]))
        seen.append(name)
        if name not in tokens:
            raise KeyError(f"unknown token {name} (via {' -> '.join(seen)})")
        value = tokens[name]
        alias = ALIAS.match(value)
        if alias is None:
            return value
        name = alias.group(1)


def resolve_hex(name, tokens):
    value = resolve(name, tokens)
    if not HEX_COLOUR.match(value):
        raise ValueError(f"{name} resolves to {value!r}, not a plain hex colour")
    return value


def missing_from_dark(root, dark):
    """Tokens in :root holding a literal colour that the dark block does not redeclare."""
    return sorted(name for name, value in root.items() if LITERAL_COLOUR.search(value) and name not in dark)


def luminance(hex_colour):
    digits = hex_colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    channels = []
    for i in (0, 2, 4):
        c = int(digits[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(fg_hex, bg_hex):
    lighter, darker = sorted((luminance(fg_hex), luminance(bg_hex)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class SiteThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        css = (SITE_DIR / "styles.css").read_text(encoding="utf-8")
        cls.root, cls.dark, cls.dark_block = parse_tokens(css)

    def test_dark_block_redeclares_every_literal_colour(self):
        self.assertEqual(missing_from_dark(self.root, self.dark), [], "colour tokens missing from the dark block")

    def test_dark_block_has_no_stray_tokens(self):
        strays = sorted(set(self.dark) - set(self.root))
        self.assertEqual(strays, [], "dark tokens that do not exist in :root")

    def test_dark_block_sets_color_scheme(self):
        self.assertRegex(self.dark_block, r"color-scheme:\s*dark\s*;")

    def check_pairs(self, pairs, minimum):
        for theme in THEMES:
            tokens = theme_tokens(self.root, self.dark, theme)
            for fg, bg in pairs:
                ratio = contrast(resolve_hex(fg, tokens), resolve_hex(bg, tokens))
                with self.subTest(theme=theme, fg=fg, bg=bg, ratio=round(ratio, 2)):
                    self.assertGreaterEqual(ratio, minimum, f"{theme}: {fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1")

    def test_text_contrast(self):
        self.check_pairs(TEXT_PAIRS, TEXT_RATIO)

    def test_non_text_contrast(self):
        self.check_pairs(NON_TEXT_PAIRS, NON_TEXT_RATIO)

    def search(self, pattern, path):
        match = re.search(pattern, (SITE_DIR / path).read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIsNotNone(match, f"{path} has no match for {pattern}")
        return match

    def test_theme_key_uses_the_store_prefix(self):
        # theme.js runs before store.js, so it spells out the key Store would build.
        prefix = self.search(r'^const STORAGE_PREFIX = "([^"]+)";', "store.js").group(1)
        key = self.search(r'const STORAGE_KEY = "([^"]+)";', "theme.js").group(1)
        self.assertEqual(key, prefix + "theme")

    def test_browser_chrome_matches_the_brand_fill(self):
        # theme.js tints the browser toolbar before the stylesheets load, so it can't read --brand-fill.
        chrome = self.search(
            r'const BROWSER_CHROME = \{ light: "(#[0-9a-fA-F]{6})", dark: "(#[0-9a-fA-F]{6})" \};', "theme.js"
        )
        meta = self.search(r'<meta name="theme-color" content="(#[0-9a-fA-F]{6})">', "index.html").group(1)
        for theme, colour in zip(THEMES, chrome.groups()):
            with self.subTest(theme=theme):
                brand = resolve_hex("--brand-fill", theme_tokens(self.root, self.dark, theme))
                self.assertEqual(colour.lower(), brand.lower())
        self.assertEqual(meta.lower(), chrome.group(1).lower(), "the page's initial theme-color is the light one")


SAMPLE_CSS = """
/* comment with --fake: #000; */
:root {
  --a: #111111;
  --b: var(--a);
  --c: var(--b);
  --edge: transparent;
  --shade: rgb(255 255 255 / 0.2);
  --size: 1rem;
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --a: #eeeeee;
  --edge: #333333;
  --shade: rgba(0, 0, 0, 0.5);
}
"""


class ThemeCheckerTests(unittest.TestCase):
    def test_completeness_reports_a_removed_token(self):
        root, dark, _ = parse_tokens(SAMPLE_CSS)
        self.assertEqual(missing_from_dark(root, dark), [])
        del dark["--shade"]
        self.assertEqual(missing_from_dark(root, dark), ["--shade"])

    def test_completeness_counts_named_colours_modern_functions_and_mixes(self):
        root = {
            "--named": "white",
            "--mixed": "color-mix(in srgb, var(--gold) 14%, #fff)",
            "--modern": "oklch(0.5 0.1 160)",
            "--faded": "color-mix(in srgb, var(--gold) 20%, transparent)",
            "--alias": "var(--gold)",
            "--font": '"Barlow", "Trebuchet MS", sans-serif',
        }
        self.assertEqual(missing_from_dark(root, {}), ["--mixed", "--modern", "--named"])

    def test_parsing_ignores_comments(self):
        root, _, _ = parse_tokens(SAMPLE_CSS)
        self.assertNotIn("--fake", root)

    def test_var_chains_resolve_per_theme(self):
        root, dark, _ = parse_tokens(SAMPLE_CSS)
        self.assertEqual(resolve("--c", theme_tokens(root, dark, "light")), "#111111")
        self.assertEqual(resolve("--c", theme_tokens(root, dark, "dark")), "#eeeeee")

    def test_cycle_raises(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            resolve("--x", {"--x": "var(--y)", "--y": "var(--x)"})

    def test_unknown_token_raises(self):
        with self.assertRaisesRegex(KeyError, "unknown token --nope"):
            resolve("--x", {"--x": "var(--nope)"})

    def test_non_hex_contrast_token_raises(self):
        with self.assertRaisesRegex(ValueError, "not a plain hex colour"):
            resolve_hex("--shade", {"--shade": "rgba(0, 0, 0, 0.5)"})

    def test_contrast_of_black_on_white_is_21(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21.0)


if __name__ == "__main__":
    unittest.main()
