"""The package's layering, checked rather than hoped for.

Each module may import only from the layers its entry allows. Reading the
imports with ``ast`` catches a violation the moment it is written, before it
grows into a dependency something else relies on. ``typing.TYPE_CHECKING``
blocks are skipped: an annotation is not a runtime dependency.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "proteinviewer"

DOMAIN = {"structure", "pockets"}
SHARED = {"errors", "validate", "config"}
ADAPTERS = {"process", "detectors", "detectors.fpocket", "detectors.lacuna",
            "prep", "generation"}
# Files on disk: not the domain, but no knowledge of a tool or of HTTP either.
STORES = {"buffer", "project"}

# module -> the package modules it may import
ALLOWED = {
    "structure": set(),
    "pockets": {"structure"},
    "errors": set(),
    "validate": {"errors"},
    "config": set(),
    "process": {"errors"},
    "detectors": DOMAIN | {"errors"},
    "detectors.fpocket": DOMAIN | {"errors", "process", "detectors"},
    "detectors.lacuna": DOMAIN | {"errors", "process", "detectors", "validate"},
    "prep": {"errors", "process"},
    "generation": {"errors", "process"},
    "buffer": DOMAIN | {"errors"},
    "project": {"errors"},
    "schemas": DOMAIN | {"errors", "validate"},
    # The service sees tools only through the registry and its own protocols.
    "service": DOMAIN | STORES | {"errors", "schemas", "detectors"},
    "web": {"errors", "schemas", "service"},
    "server": DOMAIN | SHARED | ADAPTERS | STORES | {"service", "web"},
    "__main__": {"server"},
    "__init__": set(),
}

# The domain is the part that could leave this repository as its own package:
# nothing outside the standard library, and no I/O.
DOMAIN_STDLIB = {"__future__", "dataclasses", "re", "math", "collections",
                 "typing"}


def module_name(path: Path) -> str:
    rel = path.relative_to(PKG).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def imports(path: Path) -> tuple[set[str], set[str]]:
    """(package modules, top-level external modules) imported at runtime."""
    tree = ast.parse(path.read_text(), str(path))
    # The package this file's relative imports start from, as module parts.
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    package = [p for p in package if p != "__init__"]
    internal, external = set(), set()

    def name(parts):
        return ".".join(parts) or "__init__"

    def visit(node):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            return
        if isinstance(node, ast.Import):
            for a in node.names:
                top, _, rest = a.name.partition(".")
                if top == "proteinviewer":
                    internal.add(rest or "__init__")
                else:
                    external.add(top)
        elif isinstance(node, ast.ImportFrom) and node.level:
            base = package[:len(package) - (node.level - 1)]
            if node.module:
                internal.add(name(base + node.module.split(".")))
            else:
                # "from . import x": a submodule if there is one by that
                # name, otherwise a name defined in the package itself.
                for a in node.names:
                    sub = name(base + [a.name])
                    internal.add(sub if sub in ALLOWED else name(base))
        elif isinstance(node, ast.ImportFrom):
            top, _, rest = (node.module or "").partition(".")
            if top == "proteinviewer":
                internal.add(rest or "__init__")
            else:
                external.add(top)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return internal, external


class LayeringTest(unittest.TestCase):
    def test_every_module_has_a_rule(self):
        found = {module_name(p) for p in PKG.rglob("*.py")}
        self.assertEqual(found - set(ALLOWED), set(), "add new modules to ALLOWED")

    def test_imports_respect_the_layers(self):
        for path in sorted(PKG.rglob("*.py")):
            name = module_name(path)
            internal, _ = imports(path)
            illegal = internal - ALLOWED[name] - {name}
            self.assertEqual(illegal, set(), "%s imports %s" % (name, sorted(illegal)))

    def test_domain_is_standard_library_only(self):
        for name in DOMAIN:
            _, external = imports(PKG / (name + ".py"))
            self.assertLessEqual(external, DOMAIN_STDLIB, name)

    def test_the_server_runs_on_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        for path in PKG.rglob("*.py"):
            _, external = imports(path)
            self.assertLessEqual(external - {""}, stdlib, module_name(path))


class PageMirrorsServerTest(unittest.TestCase):
    """Two lists of the same thing, one per language, kept equal.

    The page offers to delete the bound ligands before a run, and names them.
    It can only name the ones a detector would otherwise see, so it has to
    know what the server drops on its own -- and if the two lists drift, the
    page offers to delete something that was never there.
    """

    def test_the_pages_additive_list_matches_the_servers(self):
        from proteinviewer.structure import ADDITIVES

        source = (ROOT / "app" / "js" / "model" / "tree.js").read_text()
        m = re.search(r"export const ADDITIVES = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(m, "app/js/model/tree.js no longer exports ADDITIVES")
        page = set(re.findall(r'"([A-Z0-9]+)"', m.group(1)))
        self.assertEqual(page, set(ADDITIVES))


if __name__ == "__main__":
    unittest.main()
