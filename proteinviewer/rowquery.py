"""Sorting, filtering and paging a dataset's rows, before any of it is sent.

The page asks for fifty rows of ten thousand, sorted and filtered the way the
user chose; the answer is those fifty and how many matched. Nothing here knows
a molecule -- only columns with a stable key and a type -- so a column added
later (a model's predictions) sorts and filters the same way.

Rules, all deterministic:

- A *number* column compares the number in the cell; a leading qualifier
  (``<0.5``) is not part of the number, and the cell text is still what the
  page shows. A *text* column ``contains`` is case-insensitive; ``eq`` is exact.
- Every column answers ``missing``. A blank cell is missing.
- Filters combine with AND. An unknown column or operator is refused, not ignored.
- Missing values sort last, ascending or descending. Ties go to the lower
  ``row_id``, always, so a page boundary never moves between requests.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import BadRequest

__all__ = ["Column", "Filter", "OPERATORS", "schema", "parse_filters", "parse_sort", "run"]

#: The operators a column of each type answers.
OPERATORS = {
    "number": ("eq", "lt", "lte", "gt", "gte", "missing"),
    "text": ("contains", "eq", "missing"),
}
MAX_FILTERS = 20

_QUALIFIED = re.compile(r"^(?:<=|>=|<|>|=)?\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)$")


@dataclass(frozen=True)
class Column:
    """One queryable column. ``get(row)`` is its value in a row: a float for a
    number column, a string for a text column, None when missing.
    """

    key: str
    label: str
    type: str
    kind: str  # "source" (a CSV column), "derived" (computed from the row)
    get: Callable[[dict], Any]

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "type": self.type, "kind": self.kind}


@dataclass(frozen=True)
class Filter:
    key: str
    op: str
    value: Any = None


def _number(cell: str) -> float | None:
    m = _QUALIFIED.match(cell.strip())
    if not m:
        return None
    v = float(m.group(1))
    return v if math.isfinite(v) else None


def _source(index: int, numeric: bool) -> Callable[[dict], Any]:
    def get(row: dict) -> Any:
        cell = row["cells"][index]
        if not cell.strip():
            return None
        return _number(cell) if numeric else cell

    return get


def schema(meta: Mapping[str, Any]) -> list[Column]:
    """The columns of a ready dataset: the derived ones, then the CSV's own,
    each with the key the page sends back.
    """
    regression = meta.get("target_kind") == "regression"
    out = [
        Column("record", "Record", "number", "derived", lambda r: r["record"]),
        Column("row_id", "Row id", "number", "derived", lambda r: r["row_id"]),
        Column("smiles", "Canonical SMILES", "text", "derived", lambda r: r["smiles"]),
        Column(
            "structure",
            "Structure",
            "text",
            "derived",
            lambda r: "parsed" if r["smiles"] is not None else "failed",
        ),
    ]
    if meta.get("target_column"):
        out.append(
            Column(
                "target",
                "Target (model)",
                "number" if regression else "text",
                "derived",
                lambda r: r["target"],
            )
        )
    out.append(
        Column(
            "eligible",
            "Training eligible",
            "text",
            "derived",
            lambda r: "yes" if r["training_eligible"] else "no",
        )
    )
    for c in meta.get("columns", []):
        out.append(
            Column(
                c["key"], c["name"], c["type"], "source", _source(c["index"], c["type"] == "number")
            )
        )
    return out


def _lookup(columns: Sequence[Column], key: Any) -> Column:
    for c in columns:
        if c.key == key:
            return c
    raise BadRequest("unknown column %r" % (key,))


def parse_filters(columns: Sequence[Column], raw: Sequence[Mapping[str, Any]]) -> list[Filter]:
    """The request's filters, each checked against the column it names."""
    if len(raw) > MAX_FILTERS:
        raise BadRequest("at most %d filters" % MAX_FILTERS)
    out = []
    for f in raw:
        col = _lookup(columns, f.get("key"))
        op = f.get("op")
        if op not in OPERATORS[col.type]:
            raise BadRequest(
                "%r cannot be filtered with %r (want %s)"
                % (col.label, op, " or ".join(OPERATORS[col.type]))
            )
        value = f.get("value")
        if op == "missing":
            value = True if value is None else value
            if not isinstance(value, bool):
                raise BadRequest("missing takes true or false")
        elif col.type == "number":
            if isinstance(value, bool) or value is None or value == "":
                raise BadRequest("%r needs a number to compare with" % col.label)
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise BadRequest("%r needs a number, not %r" % (col.label, value)) from None
            if not math.isfinite(value):
                raise BadRequest("%r needs a finite number" % col.label)
        else:
            if not isinstance(value, str):
                raise BadRequest("%r needs text to compare with" % col.label)
        out.append(Filter(col.key, op, value))
    return out


def parse_sort(columns: Sequence[Column], raw: Mapping[str, Any] | None) -> tuple[Column, bool]:
    """``(column, descending)``; row order unless the request says otherwise."""
    raw = raw or {}
    col = _lookup(columns, raw.get("key") or "row_id")
    direction = raw.get("dir") or "asc"
    if direction not in ("asc", "desc"):
        raise BadRequest("sort direction must be asc or desc, not %r" % (direction,))
    return col, direction == "desc"


def _matches(f: Filter, value: Any) -> bool:
    if f.op == "missing":
        return (value is None) == f.value
    if value is None:
        return False
    if f.op == "contains":
        return f.value.casefold() in value.casefold()
    if f.op == "eq":
        return value == f.value
    if f.op == "lt":
        return value < f.value
    if f.op == "lte":
        return value <= f.value
    if f.op == "gt":
        return value > f.value
    return value >= f.value  # gte


def run(
    columns: Sequence[Column],
    rows: Sequence[dict],
    *,
    filters: Sequence[Filter] = (),
    sort: tuple[Column, bool] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """``(the page of rows, how many rows matched)``.

    ``rows`` are in ``row_id`` order, which is what makes the tie-break free: a
    stable sort leaves equal rows as they were, and ``reverse`` keeps that.
    """
    by_key = {c.key: c for c in columns}
    kept = [r for r in rows if all(_matches(f, by_key[f.key].get(r)) for f in filters)]
    if sort is not None:
        col, desc = sort
        valued = [(col.get(r), r) for r in kept]
        present = [(v, r) for v, r in valued if v is not None]
        absent = [r for v, r in valued if v is None]
        if col.type == "text":
            present.sort(key=lambda p: p[0].casefold(), reverse=desc)
        else:
            present.sort(key=lambda p: p[0], reverse=desc)
        kept = [r for _, r in present] + absent
    return kept[offset : offset + limit], len(kept)
