"""What a resolved dataset looks like, in numbers: the dashboard's statistics.

One call, :func:`summarize`, takes a dataset's metadata and rows (what
``resolve`` wrote) and returns everything the dashboard shows as plain JSON:
counts with the population each is a count *of*, duplicates, the target's
distribution, scaffold concentration, and missing cells per column. Nothing in
the answer grows with the number of rows -- a histogram is at most
:data:`MAX_BINS` bins, a classification is one count per class -- so the page
draws from it without ever being handed a row.

Every count that the dataset's own metadata also holds is computed from the
rows' verdicts and equals it; the tests assert so. The statistics do not decide
anything: what counts as a valid structure, a duplicate or a trainable row is
``resolve``'s, and a scaffold is ``scaffold.murcko_scaffold``'s.

Populations, named in the answer so that no percentage is of an unnamed whole:

* ``source_rows`` -- every row of the file.
* ``valid_structures`` -- rows whose structure parsed (prediction-eligible).
* ``training_eligible`` -- rows that parsed and have a valid, exact target.

Bump :data:`SUMMARY_VERSION` when this module would answer differently for the
same dataset, so a stored summary says which definition it used.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections import Counter
from typing import Any

from .errors import BadInput
from .resolve import REASONS
from .scaffold import SCAFFOLD_POLICY, murcko_scaffold

__all__ = ["SUMMARY_VERSION", "MAX_BINS", "BIN_RULE", "bin_count", "histogram", "summarize"]

#: Stored beside a summary and part of what a cached one is keyed by.
SUMMARY_VERSION = "1"

#: A histogram never has more bins than this, however many rows there are.
MAX_BINS = 50
BIN_RULE = (
    "Rice rule: ceil(2 * n^(1/3)) equal-width bins between the smallest and the largest "
    "value, at most %d; every bin includes its lower edge and the last one its upper edge; "
    "one distinct value is a single bin whose two edges are that value" % MAX_BINS
)

#: How many of the largest scaffold groups the concentration share adds up.
TOP_GROUPS = 10

SOURCE, VALID, TRAINING = "source_rows", "valid_structures", "training_eligible"


def summarize(meta: dict, rows: list[dict]) -> dict:
    """The dashboard's statistics for the dataset ``meta`` describes, whose
    rows are ``rows`` (the contents of ``dataset.json`` and ``rows.json``).
    """
    valid = [r for r in rows if r["smiles"] is not None]
    eligible = [r for r in rows if r["training_eligible"]]
    target = meta.get("target")
    return {
        "version": SUMMARY_VERSION,
        "identity": meta.get("identity"),
        "populations": {
            SOURCE: {"label": "source rows", "n": len(rows), "of": None},
            VALID: {"label": "valid structures", "n": len(valid), "of": SOURCE},
            TRAINING: {"label": "training-eligible rows", "n": len(eligible), "of": SOURCE},
        },
        "parse_failures": {"population": SOURCE, "n": len(rows) - len(valid)},
        "exclusions": _exclusions(rows),
        "duplicates": _duplicates(valid, target),
        "target": _target(meta, eligible),
        "scaffolds": _scaffolds(valid),
        "missing": _missing(meta, rows),
    }


# -- exclusions -------------------------------------------------------------------


def _exclusions(rows: list[dict]) -> dict:
    """Why rows cannot train. A row can have several reasons, so the counts per
    reason add up to more than the rows left out; ``n_rows`` is the rows.
    """
    counts: Counter[str] = Counter()
    for r in rows:
        counts.update(r["exclusions"])
    return {
        "population": SOURCE,
        "overlap": True,
        "n_rows": sum(1 for r in rows if r["exclusions"]),
        "by_reason": [
            {"code": code, "label": REASONS.get(code, code), "n": n}
            for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


# -- duplicates ---------------------------------------------------------------------


def _duplicates(valid: list[dict], target: dict | None) -> dict:
    """Rows that are one molecule under the dataset's identity policy.

    ``n_duplicate_rows`` is valid rows minus distinct identities. A group has
    conflicting targets when, for a classification target, the labels present
    among its rows are not all the same -- ``resolve``'s own rule, so the count
    is its ``duplicates.n_conflicting``. No such rule exists for a measured
    number without inventing a tolerance, so for regression, and with no target,
    it is None and says why.
    """
    groups: dict[str, list[dict]] = {}
    for r in valid:
        groups.setdefault(r["smiles"], []).append(r)
    dup = [g for g in groups.values() if len(g) > 1]
    if target is None:
        conflicting, reason = None, "the dataset has no target"
    elif target["kind"] != "classification":
        conflicting, reason = None, "not defined for %s" % target["kind"]
    else:
        conflicting = sum(len({r["target"] for r in g if r["target"] is not None}) > 1 for g in dup)
        reason = None
    return {
        "population": VALID,
        "n_distinct": len(groups),
        "n_duplicate_rows": len(valid) - len(groups),
        "n_groups": len(dup),
        "n_rows_in_groups": sum(len(g) for g in dup),
        "n_conflicting": conflicting,
        "conflicting_reason": reason,
    }


# -- the target -----------------------------------------------------------------------


def _target(meta: dict, eligible: list[dict]) -> dict:
    """The target's distribution over the training-eligible rows, in one of four
    shapes (``state``): ``no_target``, ``empty``, ``regression``, ``classification``.
    """
    t = meta.get("target")
    if t is None:
        return {"state": "no_target", "population": TRAINING, "n": 0}
    out: dict[str, Any] = {
        "population": TRAINING,
        "n": len(eligible),
        "column": t["column"],
        "kind": t["kind"],
    }
    if t["kind"] == "classification":
        if not eligible:
            return {"state": "empty", **out}
        counts = Counter(r["target"] for r in eligible)
        labels = list(meta.get("classes") or [])
        labels += [label for label in counts if label not in labels]  # never lose a row
        positive = t.get("positive_class")
        return {
            "state": "classification",
            **out,
            "positive_class": positive,
            "classes": [
                {"label": c, "count": counts.get(c, 0), "positive": c == positive} for c in labels
            ],
        }
    values = [
        r["target"]
        for r in eligible
        if isinstance(r["target"], (int, float)) and math.isfinite(r["target"])
    ]
    if not values:
        return {"state": "empty", **out}
    return {
        "state": "regression",
        **out,
        "transform": t.get("transform"),
        "source_units": t.get("source_units"),
        "units": t.get("output_units"),
        "n_binned": len(values),
        "n_not_binned": len(eligible) - len(values),
        "min": min(values),
        "max": max(values),
        "mean": math.fsum(values) / len(values),
        "median": statistics.median(values),
        "bins": histogram(values),
    }


def bin_count(n: int) -> int:
    """How many bins ``n`` values get: ``ceil(2 * n^(1/3))``, between 1 and
    :data:`MAX_BINS`. Integer arithmetic, so a perfect cube is not at the mercy
    of a float cube root.
    """
    if n <= MAX_BINS**3 // 8:
        need = 8 * max(n, 1)
        k = round(need ** (1 / 3))
        while k**3 < need:
            k += 1
        while k > 1 and (k - 1) ** 3 >= need:
            k -= 1
        return max(1, k)
    return MAX_BINS


def histogram(values: list[float]) -> dict:
    """``{rule, n_bins, edges, counts}`` for finite ``values``, at least one.

    ``edges`` has one more entry than ``counts``. A value is counted in the bin
    the published edges put it in, not by dividing, so no value can sit on one
    side of an edge in the picture and the other in the numbers.
    """
    lo, hi = min(values), max(values)
    if lo == hi:
        return {"rule": BIN_RULE, "n_bins": 1, "edges": [lo, hi], "counts": [len(values)]}
    k = bin_count(len(values))
    width = (hi - lo) / k
    edges = [lo + i * width for i in range(k)] + [hi]
    counts = [0] * k
    for v in values:
        counts[min(bisect_right(edges, v) - 1, k - 1)] += 1
    return {"rule": BIN_RULE, "n_bins": k, "edges": edges, "counts": counts}


# -- scaffolds ----------------------------------------------------------------------------


def _scaffolds(valid: list[dict]) -> dict:
    """Scaffold concentration over the valid structures.

    A scaffold is a group of rows. The empty scaffold (no ring) is one group
    like any other: counted once in ``n_distinct``, a candidate for the largest
    group and the top ten, and its rows are also given on their own as
    ``acyclic_rows``. A structure whose scaffold RDKit cannot make is in no group
    and is counted in ``n_unscaffolded``; the share's denominator stays every
    valid structure, so the two numbers cannot hide it.
    """
    per_identity = Counter(r["smiles"] for r in valid)
    sizes: Counter[str] = Counter()
    unscaffolded = 0
    for smiles, n in per_identity.items():  # a scaffold per molecule, not per row
        try:
            sizes[murcko_scaffold(smiles)] += n
        except BadInput:
            unscaffolded += n
    largest_first = sorted(sizes.values(), reverse=True)
    top = largest_first[:TOP_GROUPS]
    return {
        "population": VALID,
        "policy": SCAFFOLD_POLICY,
        "n_structures": len(valid),
        "n_distinct": len(sizes),
        "largest_group_size": largest_first[0] if largest_first else 0,
        "top10_groups": len(top),
        "top10_rows": sum(top),
        "top10_share": sum(top) / len(valid) if valid else None,
        "acyclic_rows": sizes.get("", 0),
        "n_unscaffolded": unscaffolded,
    }


# -- missing cells ------------------------------------------------------------------------


def _missing(meta: dict, rows: list[dict]) -> dict:
    """Blank and whitespace-only cells, per source column -- the same cells
    ``columns.describe_columns`` does not count as filled. A value that is
    present but wrong for its column (``abc`` in a number column) is filled:
    it is reported where it is judged, as an exclusion, not here.
    """
    return {
        "population": SOURCE,
        "columns": [
            {
                "index": c["index"],
                "name": c["name"],
                "n_missing": sum(1 for r in rows if not r["cells"][c["index"]].strip()),
            }
            for c in meta["columns"]
        ],
    }
