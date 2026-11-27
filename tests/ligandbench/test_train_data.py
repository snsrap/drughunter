"""Synthetic datasets for the training tests: many scaffold groups, so five 80/10/10
repeats are feasible for both strategies. Nothing here is a test.

A molecule is ``CORES[c]`` with ``k`` carbons in front of it: one scaffold per core, one
structure per ``(c, k)``. The labels are functions of ``(c, k)``, so a model can learn them.
"""

import json

import pytest

pytest.importorskip("rdkit")

from ligandbench.resolve import resolve  # noqa: E402

CORES = (
    "c1ccccc1",
    "c1ccncc1",
    "C1CCCCC1",
    "c1ccc2ccccc2c1",
    "c1ccsc1",
    "c1ccoc1",
    "C1CCNCC1",
    "c1ccc2[nH]ccc2c1",
    "c1ccnc2ccccc12",
    "C1CCOC1",
    "c1cnccn1",
    "C1CCCC1",
    "C1CC1",
    "C1CCC1",
    "C1CCCCCC1",
    "c1cncnc1",
    "c1ccnnc1",
    "c1cc[nH]c1",
    "c1c[nH]cn1",
    "c1cocn1",
    "c1cscn1",
    "C1COCCN1",
    "C1CNCCN1",
    "C1CCOCC1",
)


def mol(core, k):
    return "C" * k + (CORES[core] if core is not None else "O")


def csv_of(pairs):
    return ("smiles,y\n" + "\n".join("%s,%s" % p for p in pairs) + "\n").encode()


def config(kind, positive="1"):
    if kind == "regression":
        target = {
            "column": "y",
            "kind": "regression",
            "qualifier_column": None,
            "transform": "identity",
            "source_units": "pKi",
        }
    else:
        target = {
            "column": "y",
            "kind": "classification",
            "qualifier_column": None,
            "positive_class": positive,
        }
    return {"structure_column": "smiles", "target": target}


def pairs_for(kind, groups=20, per=5):
    out = []
    for c in range(groups):
        for k in range(1, per + 1):
            if kind == "regression":
                y = round(0.3 * c + k + 0.05 * ((c * k) % 7), 3)
            elif kind == "binary":
                y = (c + k) % 2
            else:
                y = "abc"[(c + k) % 3]
            out.append((mol(c, k), y))
    return out


def dataset(kind="regression", groups=20, per=5, extra=()):
    """``(meta, rows)`` as ``ligandbench resolve`` makes them. ``kind`` is
    ``regression``, ``binary`` (labels 0 and 1, positive 1) or ``multiclass`` (a, b, c).
    ``extra`` pairs are appended (a bad structure, a blank label).
    """
    task = "regression" if kind == "regression" else "classification"
    pairs = pairs_for(kind, groups, per) + list(extra)
    positive = None if kind == "multiclass" else "1"
    return resolve(csv_of(pairs), config(task, positive))


def write_dataset(directory, meta, rows, source=None):
    """The files ``resolve`` and the dataset store leave in a ready dataset's directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dataset.json").write_text(json.dumps(meta), encoding="utf-8")
    (directory / "rows.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    if source is not None:
        (directory / "source.csv").write_bytes(source)
    return directory
