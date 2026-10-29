import re
import unittest
from collections import defaultdict
from pathlib import Path

from scripts import version_assets


SITE_DIR = Path(__file__).resolve().parents[1] / "site"
# Classic scripts share one global scope, so a name declared at the top level of
# two files is a SyntaxError that stops the second file from running at all.
TOP_LEVEL_DECLARATION = re.compile(
    r"^(?:async\s+)?(?:const|let|var|function\*?|class)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


class SiteGlobalsTests(unittest.TestCase):
    def test_scripts_do_not_redeclare_each_others_globals(self):
        owners = defaultdict(list)
        for asset in version_assets.VERSIONED_ASSETS:
            if not asset.endswith(".js"):
                continue
            source = (SITE_DIR / asset).read_text(encoding="utf-8")
            for name in TOP_LEVEL_DECLARATION.findall(source):
                owners[name].append(asset)

        duplicates = {name: files for name, files in owners.items() if len(files) > 1}
        self.assertEqual(duplicates, {}, "top-level names declared in more than one script")


if __name__ == "__main__":
    unittest.main()
