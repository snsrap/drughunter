# Contributing

## Something does not work

Run this first, from the environment drughunter is installed in, and paste the
whole output into the bug report:

```sh
python -m pytest -m health
```

It imports every dependency, finds fpocket, lacuna, smina/gnina and NEAT-POCKET
the way drughunter does, checks NEAT-POCKET's weights, and runs one operation on
the GPU from NEAT-POCKET's interpreter. The block headed `drughunter
environment` at the end is the part that matters. A missing tool is a skip, not
a failure. To make it a failure (the setup script does this to check what it
installed), name the tools or pass `1` for all of them:

```sh
DRUGHUNTER_REQUIRE_TOOLS=1 python -m pytest -m health
DRUGHUNTER_REQUIRE_TOOLS=fpocket,scorer,gpu python -m pytest -m health
```

## Making a change

```sh
pip install -e '.[test,dev]'
pre-commit install
python -m pytest     # everything; tests/e2e needs npm, see docs/proteinviewer.md
npm test             # the page's pure modules, Node 18+
```

Ruff lints and formats; CI runs `ruff check` and `ruff format --check`. Science
code goes in `pocketbench` or `quickprep` and never imports `proteinviewer`
(`tests/test_architecture.py` checks the layering).
