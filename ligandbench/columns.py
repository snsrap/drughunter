"""What each column holds, for the dialog that asks what to make of it.

Standard library only. A column is ``number`` when every filled cell is a
number, perhaps with a leading qualifier (``<0.5``): that is what a sort or a
numeric filter can use. The type says nothing about the learning task --
0 and 1 are numbers and may be classes -- so the user is asked, not told.
"""

from __future__ import annotations

import math
import re

from .table import Table

__all__ = [
    "QUALIFIERS",
    "CENSORING",
    "ALIASES",
    "parse_number",
    "split_qualified",
    "describe_columns",
    "structure_candidates",
]

#: What a qualifier column may say. Blank means the same as ``=``.
QUALIFIERS = ("=", "<", ">", "<=", ">=")
#: The ones that make a value a bound, not a measurement.
CENSORING = frozenset({"<", ">", "<=", ">="})

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_PLAIN = re.compile(r"^%s$" % _NUM)
_QUALIFIED = re.compile(r"^(<=|>=|<|>|=)?\s*(%s)$" % _NUM)

#: Headers, once lower-cased with spaces made underscores, that name a structure.
ALIASES = frozenset({"smiles", "canonical_smiles", "isomeric_smiles", "mol", "molecule", "romol"})
#: How many distinct values a column may have and still list them, for choosing classes.
MAX_LISTED_VALUES = 20


def parse_number(text: str) -> float | None:
    """A plain finite number, or None. Not Python's ``float``, which also
    reads ``nan``, ``inf`` and ``1_000``.
    """
    s = text.strip()
    if not _PLAIN.match(s):
        return None
    v = float(s)
    return v if math.isfinite(v) else None


def split_qualified(text: str) -> tuple[str | None, float | None]:
    """``(qualifier, number)`` of ``<0.5`` or ``12``; the qualifier is None
    when there is none written, and the number None when it is not a number.
    """
    m = _QUALIFIED.match(text.strip())
    if not m:
        return None, None
    v = float(m.group(2))
    return (m.group(1), v) if math.isfinite(v) else (None, None)


def _normal(header: str) -> str:
    return re.sub(r"\s+", "_", header.strip().lower())


def structure_candidates(headers: list[str]) -> list[str]:
    """The headers that name a structure: an alias exactly, or ``*_smiles``.
    ``Molecule Name`` is a name, not a structure, and is not here.
    """
    out = []
    for h in headers:
        n = _normal(h)
        if n in ALIASES or n.endswith("_smiles"):
            out.append(h)
    return out


def describe_columns(table: Table) -> list[dict]:
    """One entry per column: its stable key, type, how many cells are filled,
    a few examples, and -- when there are few enough -- every distinct value
    with its count.
    """
    out = []
    for i, name in enumerate(table.headers):
        filled = [r[i].strip() for r in table.records if r[i].strip()]
        counts: dict[str, int] = {}
        for v in filled:
            counts[v] = counts.get(v, 0) + 1
        numeric = bool(filled) and all(split_qualified(v)[1] is not None for v in filled)
        out.append(
            {
                "index": i,
                "key": "c%d" % i,
                "name": name,
                "type": "number" if numeric else "text",
                "n_filled": len(filled),
                "examples": list(counts)[:3],
                "values": [[v, n] for v, n in counts.items()]
                if len(counts) <= MAX_LISTED_VALUES
                else None,
                "qualifier_like": bool(filled) and all(v in QUALIFIERS for v in counts),
            }
        )
    return out
