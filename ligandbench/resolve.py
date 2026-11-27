"""From a CSV and the user's choices to a dataset: rows with identity, a target,
and a verdict on what each row may be used for.

The choices are made in a dialog, never guessed here: which column holds the
structures, which (if any) the target, whether the target is regression or
classification, which column qualifies it (``<``, ``>``), and what its units
are and what transform to apply. A column's numbers do not say which task is
meant, and a column's name does not say what its units are.

Every source row is kept, in source order, with an immutable zero-based
``row_id``. *Prediction* needs only a parsed structure. *Training* needs a
parsed structure and a valid, present, exact (uncensored) target; each row
carries the reasons it fails, which may overlap.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from . import __version__
from .chem import IDENTITY_POLICY, canonical_smiles
from .columns import CENSORING, QUALIFIERS, describe_columns, split_qualified
from .errors import BadInput
from .table import Table, parse_csv

__all__ = ["resolve", "FORMAT", "KINDS", "TRANSFORMS", "REASONS"]

#: The layout of dataset.json and rows.json; bump when either changes shape.
FORMAT = 1
KINDS = ("regression", "classification")
TRANSFORMS = ("identity", "pki")
#: Why a row cannot train, and what each means to the person reading the table.
REASONS = {
    "no_target": "the dataset has no target",
    "invalid_structure": "the structure did not parse",
    "missing_target": "the target is blank",
    "invalid_target": "the target is not a number",
    "censored": "the target is a bound (<, >), not a measurement",
    "unknown_qualifier": "the qualifier is not one of =, <, >, <=, >=",
    "conflicting_qualifiers": "the qualifier column and the value disagree",
    "nonpositive_for_pki": "a pKi needs a value above zero",
}
EXAMPLES = 5
PKI_UNITS = "nM"


def resolve(data: bytes, config: dict[str, Any]) -> tuple[dict, list[dict]]:
    """``(metadata, rows)`` for the CSV ``data`` under the choices in ``config``.

    ``config`` is ``{"structure_column": name, "target": None | {...}}``; see
    :func:`_check_config` for the target's fields. A :class:`BadInput` says
    what to change.
    """
    table = parse_csv(data)
    cfg = _check_config(config, table.headers)
    columns = describe_columns(table)
    s_idx = table.headers.index(cfg["structure_column"])
    target = cfg["target"]

    rows = []
    for rid, cells in enumerate(table.records):
        smiles, why = canonical_smiles(cells[s_idx])
        rows.append(
            {
                "row_id": rid,
                "record": rid + 1,
                "cells": cells,
                "smiles": smiles,
                "structure_error": why,
                "target": None,
                "target_source": None,
                "qualifier": None,
                "censored": False,
                "prediction_eligible": smiles is not None,
                "training_eligible": False,
                "exclusions": [],
            }
        )

    failures = [r for r in rows if r["smiles"] is None]
    if len(failures) == len(rows):
        raise BadInput(_nothing_parses(cfg["structure_column"], table, s_idx, rows))

    classes: list[str] = []
    if target is not None:
        _apply_target(table, rows, target)
        if target["kind"] == "classification":
            classes = _classes(rows, target)
    else:
        for r in rows:
            r["exclusions"] = ["no_target"] + (["invalid_structure"] if r["smiles"] is None else [])

    for r in rows:
        if target is not None:
            r["training_eligible"] = (
                r["prediction_eligible"] and r["target"] is not None and not r["exclusions"]
            )
    if target is not None and not any(r["training_eligible"] for r in rows):
        raise BadInput(_nothing_trains(target, rows))

    exclusions: dict[str, int] = {}
    for r in rows:
        for code in r["exclusions"]:
            exclusions[code] = exclusions.get(code, 0) + 1

    meta = {
        "format": FORMAT,
        "ligandbench": __version__,
        "identity": IDENTITY_POLICY,
        "n_rows": len(rows),
        "columns": [
            {k: c[k] for k in ("index", "key", "name", "type", "n_filled")} for c in columns
        ],
        "structure_column": cfg["structure_column"],
        "id_column": next(
            (
                c["name"]
                for c in columns
                if c["index"] != s_idx and c["type"] == "text" and c["n_filled"]
            ),
            None,
        ),
        "target": target,
        "target_column": target["column"] if target else None,
        "target_kind": target["kind"] if target else None,
        "classes": classes,
        "trainable": target is not None,
        "n_parse_failures": len(failures),
        "parse_failures": [
            {"record": r["record"], "value": r["cells"][s_idx], "error": r["structure_error"]}
            for r in failures[:EXAMPLES]
        ],
        "n_prediction_eligible": sum(r["prediction_eligible"] for r in rows),
        "n_training_eligible": sum(r["training_eligible"] for r in rows),
        "exclusions": exclusions,
        "duplicates": _duplicates(rows, target),
        "content_hash": content_hash(data, cfg),
    }
    return meta, rows


def content_hash(data: bytes, cfg: dict) -> str:
    """Of the source bytes and the resolution, so the same file read two ways
    is two datasets and the same read twice is one.
    """
    h = hashlib.sha256(data)
    h.update(b"\0")
    h.update(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()


# -- the choices --------------------------------------------------------------


def _check_config(config: dict[str, Any], headers: list[str]) -> dict:
    """The choices, checked against the file and written out in full."""
    listing = "; ".join(headers)
    s = config.get("structure_column")
    if s not in headers:
        raise BadInput(
            "the structure column %r is not in the file; its columns are: %s" % (s, listing)
        )
    t = config.get("target")
    if t is None:
        return {"structure_column": s, "target": None}

    col, kind = t.get("column"), t.get("kind")
    if col not in headers:
        raise BadInput(
            "the target column %r is not in the file; its columns are: %s" % (col, listing)
        )
    if col == s:
        raise BadInput("the target cannot be the structure column")
    if kind not in KINDS:
        raise BadInput("say whether %r is %s" % (col, " or ".join(KINDS)))
    if "qualifier_column" not in t:
        raise BadInput("confirm the qualifier column for %r, or choose none" % col)
    q = t["qualifier_column"]
    if q is not None:
        if q not in headers:
            raise BadInput("the qualifier column %r is not in the file" % q)
        if q in (s, col):
            raise BadInput("the qualifier column must be neither the structure nor the target")
        if kind == "classification":
            raise BadInput("a qualifier applies to a measured number, not to a class label")

    out = {
        "column": col,
        "kind": kind,
        "positive_class": None,
        "qualifier_column": q,
        "transform": None,
        "source_units": None,
        "output_units": None,
    }
    if kind == "regression":
        transform = t.get("transform") or "identity"
        if transform not in TRANSFORMS:
            raise BadInput("unknown transform %r (want %s)" % (transform, " or ".join(TRANSFORMS)))
        if not isinstance(t.get("source_units"), str):
            raise BadInput("confirm the units of %r (or say it has none)" % col)
        units = t["source_units"].strip()
        if transform == "pki":
            if units != PKI_UNITS:
                raise BadInput(
                    "pKi = 9 - log10(Ki) is defined for nM: confirm that %r is in nM, "
                    "or keep the values as they are" % col
                )
            out["output_units"] = "pKi"
        else:
            out["output_units"] = units
        out.update(transform=transform, source_units=units)
    else:
        pc = t.get("positive_class")
        out["positive_class"] = None if pc in (None, "") else str(pc)
    return {"structure_column": s, "target": out}


# -- targets -----------------------------------------------------------------


def _apply_target(table: Table, rows: list[dict], target: dict) -> None:
    t_idx = table.headers.index(target["column"])
    q_idx = table.headers.index(target["qualifier_column"]) if target["qualifier_column"] else None
    for r in rows:
        why = [] if r["smiles"] is not None else ["invalid_structure"]
        raw = r["cells"][t_idx].strip()
        r["target_source"] = raw or None
        if not raw:
            why.append("missing_target")
        elif target["kind"] == "classification":
            r["target"] = raw
        else:
            qual = r["cells"][q_idx].strip() if q_idx is not None else ""
            value, q, problem = _measurement(raw, qual, target["transform"])
            r["target"], r["qualifier"] = value, q
            r["censored"] = q in CENSORING
            why += problem
        r["exclusions"] = why


def _measurement(
    raw: str, qualifier_cell: str, transform: str
) -> tuple[float | None, str | None, list[str]]:
    """``(value, qualifier, reasons)`` for one regression cell.

    A bound is never made an exact value: its qualifier is kept and its value
    is not, however it was written (``<0.5`` in the cell, or ``<`` beside it).
    """
    inline, number = split_qualified(raw)
    if number is None:
        return None, None, ["invalid_target"]
    if qualifier_cell and qualifier_cell not in QUALIFIERS:
        return None, None, ["unknown_qualifier"]
    stated = {q for q in (inline, qualifier_cell or None) if q is not None}
    if len(stated) > 1:
        return None, None, ["conflicting_qualifiers"]
    q = stated.pop() if stated else "="
    if q in CENSORING:
        return None, q, ["censored"]
    if transform == "pki":
        if number <= 0:
            return None, q, ["nonpositive_for_pki"]
        number = 9.0 - math.log10(number)
    return number, q, []


def _classes(rows: list[dict], target: dict) -> list[str]:
    """The labels among rows with a structure, in first-seen order. Training
    needs two; a binary target needs to be told which is the positive one.
    """
    seen: dict[str, None] = {}
    for r in rows:
        if r["prediction_eligible"] and r["target"] is not None:
            seen.setdefault(r["target"])
    classes = list(seen)
    if len(classes) < 2:
        raise BadInput(
            "%r has %s among the rows that parse; classification needs at least two classes. "
            "Choose another column, or no target to use this dataset only for prediction"
            % (target["column"], "one class (%r)" % classes[0] if classes else "no labels")
        )
    pc = target["positive_class"]
    if len(classes) == 2 and pc is None:
        raise BadInput(
            "choose the positive class of %r: %s"
            % (target["column"], " or ".join(map(repr, classes)))
        )
    if pc is not None and pc not in classes:
        raise BadInput(
            "the positive class %r is not one of the classes: %s" % (pc, ", ".join(classes))
        )
    if len(classes) > 2:
        target["positive_class"] = None  # one-vs-rest has no single positive
    return classes


# -- the verdicts ---------------------------------------------------------------


def _nothing_parses(column: str, table: Table, s_idx: int, rows: list[dict]) -> str:
    filled = [r for r in rows if r["cells"][s_idx].strip()]
    if not filled:
        return "column %r is empty, so it holds no structures" % column
    eg = "; ".join("record %d: %r" % (r["record"], r["cells"][s_idx][:40]) for r in filled[:3])
    return (
        "none of the %d values in column %r parse as SMILES (%s). Choose the column that holds "
        "SMILES; its columns are: %s" % (len(filled), column, eg, "; ".join(table.headers))
    )


def _nothing_trains(target: dict, rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        for code in r["exclusions"]:
            counts[code] = counts.get(code, 0) + 1
    why = ", ".join(
        "%d %s" % (n, REASONS[c]) for c, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return "no row of %d can be trained on with target %r (%s)" % (len(rows), target["column"], why)


def _duplicates(rows: list[dict], target: dict | None) -> dict:
    """Rows that are the same molecule. They stay separate observations but
    share an identity, so a split keeps them together. For a classification
    target, the ones whose labels disagree are listed.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["smiles"] is not None:
            groups.setdefault(r["smiles"], []).append(r)
    dup = {s: g for s, g in groups.items() if len(g) > 1}
    out: dict[str, Any] = {
        "n_structures": len(dup),
        "n_rows": sum(len(g) for g in dup.values()),
        "n_conflicting": None,
        "examples": [],
    }
    if target is not None and target["kind"] == "classification":
        conflicts = []
        for smiles, g in dup.items():
            labels = sorted({r["target"] for r in g if r["target"] is not None})
            if len(labels) > 1:
                conflicts.append(
                    {"smiles": smiles, "records": [r["record"] for r in g], "labels": labels}
                )
        out["n_conflicting"] = len(conflicts)
        out["examples"] = conflicts[:EXAMPLES]
    return out
