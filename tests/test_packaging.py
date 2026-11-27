"""drughunter is one distribution holding four packages: one thing on the
outside, four on the inside. One version for all of them, and science code
that never reaches for the viewer.
"""

import ast
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = ("proteinviewer", "pocketbench", "ligandbench", "quickprep")


def top_level_imports(path):
    tops = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            tops |= {a.name.partition(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            tops.add(node.module.partition(".")[0])
    return tops


class PackagingTest(unittest.TestCase):
    def test_the_packages_carry_one_version(self):
        # All of them read the installed distribution's version (the git tag).
        versions = {p: importlib.import_module(p).__version__ for p in PARTS}
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_science_never_imports_the_viewer(self):
        # The viewer runs pocketbench, ligandbench and quickprep as commands and
        # imports none of them (test_architecture holds it to the standard
        # library); this is the other direction.
        for package in ("pocketbench", "ligandbench", "quickprep"):
            for path in sorted((ROOT / package).rglob("*.py")):
                self.assertNotIn(
                    "proteinviewer", top_level_imports(path), str(path.relative_to(ROOT))
                )

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib")
    def test_every_package_is_discovered_and_ligandbench_dependencies_are_declared(self):
        # ligandbench is run by the server, not imported, so nothing else would
        # notice a package that was left out of the distribution or its dependency.
        import tomllib

        cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
        include = cfg["tool"]["setuptools"]["packages"]["find"]["include"]
        for part in (*PARTS, "pocketgen"):
            self.assertIn(part + "*", include)
        declared = [d.split(">")[0].split("=")[0].strip() for d in cfg["project"]["dependencies"]]
        self.assertIn("rdkit", declared)
        self.assertIn("xgboost", declared)
        self.assertIn("xgboost>=2.1", cfg["project"]["dependencies"])
        self.assertIn("ligandbench", cfg["project"]["scripts"])


if __name__ == "__main__":
    unittest.main()
