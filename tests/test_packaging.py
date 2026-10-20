"""drughunter is one distribution holding three packages: one thing on the
outside, three on the inside. One version for all of them, and science code
that never reaches for the viewer.
"""

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = ("proteinviewer", "pocketbench", "quickprep")


def top_level_imports(path):
    tops = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            tops |= {a.name.partition(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            tops.add(node.module.partition(".")[0])
    return tops


class PackagingTest(unittest.TestCase):
    def test_the_three_packages_carry_one_version(self):
        # All three read the installed distribution's version (the git tag).
        versions = {p: importlib.import_module(p).__version__ for p in PARTS}
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_science_never_imports_the_viewer(self):
        # The viewer runs pocketbench and quickprep as commands and imports
        # neither (test_architecture holds it to the standard library); this
        # is the other direction.
        for package in ("pocketbench", "quickprep"):
            for path in sorted((ROOT / package).rglob("*.py")):
                self.assertNotIn(
                    "proteinviewer", top_level_imports(path), str(path.relative_to(ROOT))
                )


if __name__ == "__main__":
    unittest.main()
