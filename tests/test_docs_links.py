import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

DOC_GLOBS = ("README.md", "CLAUDE.md", "docs/*.md", ".claude/rules/*.md", ".claude/skills/*/SKILL.md")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
LINK = re.compile(r"\[(?:[^\]\[]|\[[^\]]*\])*\]\(\s*<?([^)\s>]*)>?(?:\s+\"[^\"]*\")?\s*\)")
EXTERNAL = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")


def doc_files():
    files = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    return files


def lines_outside_code(text):
    """Yield (line number, line) for every line outside a fenced block."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = FENCE.match(line)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif fence == marker.group(1):
                fence = None
            continue
        if fence is None:
            yield number, line


def slug(heading):
    """GitHub's anchor for a heading: lowercase, keep letters, digits, spaces, hyphens, underscores."""
    text = INLINE_CODE.sub(lambda match: match.group(2), heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.lower()
    text = "".join(c for c in text if c.isalnum() or c in " -_")
    return text.replace(" ", "-")


def anchors(path):
    seen = {}
    found = set()
    for _, line in lines_outside_code(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def links(path):
    """Yield (line number, target) for each relative markdown link outside code."""
    for number, line in lines_outside_code(path.read_text(encoding="utf-8")):
        stripped = INLINE_CODE.sub("", line)
        for match in LINK.finditer(stripped):
            target = match.group(1)
            if target and not EXTERNAL.match(target):
                yield number, target


def broken_links(files, root):
    """Return (file, line, target, reason) for each link that does not resolve."""
    problems = []
    anchor_cache = {}
    for path in files:
        for number, target in links(path):
            file_part, _, anchor = target.partition("#")
            destination = path if not file_part else (path.parent / file_part).resolve()
            where = (path.relative_to(root), number, target)
            if not destination.exists():
                problems.append((*where, "no such file"))
            elif anchor and destination.suffix == ".md":
                if destination not in anchor_cache:
                    anchor_cache[destination] = anchors(destination)
                if anchor not in anchor_cache[destination]:
                    problems.append((*where, "no heading with that anchor"))
    return problems


class DocsLinkTests(unittest.TestCase):
    def test_every_relative_markdown_link_resolves_to_a_file_and_heading(self):
        files = doc_files()
        self.assertTrue(files, "no documentation files found")
        for file, number, target, reason in broken_links(files, ROOT):
            with self.subTest(file=str(file), line=number, target=target):
                self.fail(f"{file}:{number} links {target}: {reason}")


class LinkCheckerTests(unittest.TestCase):
    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def test_slug_follows_the_github_rule(self):
        self.assertEqual(slug("Pokémon avatars"), "pokémon-avatars")
        self.assertEqual(slug("You card"), "you-card")
        self.assertEqual(slug("`build_stats.py`: a, b (c)"), "build_statspy-a-b-c")

    def test_duplicate_headings_get_numbered_suffixes(self):
        page = self.write("a.md", "# Same\n\n## Same\n\n## Same\n")
        self.assertEqual(anchors(page), {"same", "same-1", "same-2"})

    def test_a_missing_file_and_a_missing_anchor_are_both_reported(self):
        self.write("b.md", "# Real heading\n")
        page = self.write("a.md", "[ok](b.md#real-heading) [file](gone.md) [anchor](b.md#nope) [self](#top)\n# Top\n")
        problems = broken_links([page], self.root)
        self.assertEqual([(p[2], p[3]) for p in problems], [("gone.md", "no such file"), ("b.md#nope", "no heading with that anchor")])

    def test_links_in_code_and_external_links_are_ignored(self):
        page = self.write(
            "a.md",
            "```\n[x](gone.md)\n```\n`[y](gone.md)` [web](https://example.com/x) [mail](mailto:a@b.c)\n",
        )
        self.assertEqual(broken_links([page], self.root), [])


if __name__ == "__main__":
    unittest.main()
