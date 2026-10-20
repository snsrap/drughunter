"""The package's layering, checked rather than hoped for.

Each module may import only from the layers its entry allows. Reading the
imports with ``ast`` catches a violation the moment it is written, before it
grows into a dependency something else relies on. ``typing.TYPE_CHECKING``
blocks are skipped: an annotation is not a runtime dependency.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "proteinviewer"

SHARED = {"errors", "validate", "config"}
ADAPTERS = {"process", "pocketbench", "prep", "generation"}
# Files on disk: no knowledge of a tool or of HTTP.
STORES = {"buffer", "project"}

# module -> the package modules it may import. There is no domain layer: what
# was one -- parsing structures, the pocket model, the detectors -- is the
# pocketbench package now, which the server runs and never imports.
ALLOWED = {
    "pockets": set(),
    "errors": set(),
    "validate": {"errors"},
    "config": set(),
    "process": {"errors"},
    "pocketbench": {"errors", "process", "validate"},
    "prep": {"errors", "process"},
    "generation": {"errors", "process"},
    "buffer": {"errors"},
    "project": {"errors"},
    "schemas": {"errors", "validate"},
    # The service sees tools only through its own protocols.
    "service": STORES | {"errors", "schemas", "pockets"},
    "web": {"errors", "schemas", "service"},
    "server": SHARED | ADAPTERS | STORES | {"service", "web", "__init__"},
    "__main__": {"server"},
    "__init__": set(),
}


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

    def test_the_server_runs_on_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        for path in PKG.rglob("*.py"):
            _, external = imports(path)
            self.assertLessEqual(external - {""}, stdlib, module_name(path))


if __name__ == "__main__":
    unittest.main()
