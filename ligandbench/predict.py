"""Applying a registered model to a dataset: the checks that come first, then the rows.

A model is only ever applied the way it was trained. ``check`` reads a run
directory (``run.json``, ``model.json``, ``features.json``) and refuses -- with
``Incompatible``, before any inference -- anything that could feed the model a
vector other than the one it learned from:

* a ``run.json`` or ``features.json`` this build does not know the schema of;
* ``model.json`` or ``features.json`` whose bytes are not the ones ``run.json``
  recorded (a damaged or edited artifact);
* a different RDKit or XGBoost than the one that trained it (v1 does not
  guess that a different build computes the same numbers);
* a featuriser version, parameter, column order or column count that this build
  does not implement exactly, or a booster whose width is not the schema's.

There is no fallback: a model is never run with default features. The target of
the *prediction* dataset plays no part -- features come from the structure only.

``predict_rows`` returns one output per source row, keyed by ``row_id`` and in
the dataset's order. A row with no parsed structure, or whose features cannot be
built, gets an ``error`` and no number, so nothing can shift onto its neighbour.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import BadInput

__all__ = ["SCHEMA", "RUN_SCHEMAS", "Incompatible", "check", "predict_rows"]

#: What ``predict_rows`` returns says it is this.
SCHEMA = "qsar-predictions-v1"
#: The run schemas whose artifacts this build can apply.
RUN_SCHEMAS = ("qsar-run-v1",)
BATCH = 500


class Incompatible(BadInput):
    """A model that cannot be applied here, and why."""

    code = "incompatible"


def _read(path: Path, what: str) -> dict:
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Incompatible("cannot read %s: %s" % (what, exc)) from None
    if not isinstance(out, dict):
        raise Incompatible("%s is not a JSON object" % what)
    return out


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def check(run_dir: Path | str) -> dict:
    """The run's ``run.json`` if its model can be applied by this build, else
    ``Incompatible``. Loads no model.
    """
    base = Path(run_dir)
    run = _read(base / "run.json", "run.json")
    schema = run.get("schema")
    if schema not in RUN_SCHEMAS:
        raise Incompatible(
            "this model's run is %r; this build applies %s" % (schema, ", ".join(RUN_SCHEMAS))
        )
    if run.get("status") != "completed":
        raise Incompatible("this model's run is not a completed one")
    files = run.get("files") or {}
    for name in ("model.json", "features.json"):
        recorded = (files.get(name) or {}).get("sha256")
        if not recorded:
            raise Incompatible("run.json records no checksum for %s" % name)
        if _sha256(base / name) != recorded:
            raise Incompatible(
                "%s is missing or is not the file that was registered (its checksum differs); "
                "the artifact is damaged" % name
            )
    features = _read(base / "features.json", "features.json")
    versions = run.get("versions") or {}
    from .train import require_xgboost

    lib = require_xgboost()
    from rdkit import rdBase

    now = {"rdkit": rdBase.rdkitVersion, "xgboost": lib.__version__}
    for lib_name, current in now.items():
        recorded = versions.get(lib_name)
        if recorded != current:
            raise Incompatible(
                "this model was trained with %s %s and this server runs %s %s; version 1 applies "
                "a model only with the versions it was trained with"
                % (lib_name, recorded or "(unrecorded)", lib_name, current)
            )
    from .features import Featuriser

    try:
        Featuriser.from_schema(features.get("feature_schema"))
    except BadInput as exc:
        raise Incompatible("the feature schema cannot be reproduced: %s" % exc) from None
    return run


def predict_rows(
    run_dir: Path | str,
    rows: Sequence[dict],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Check the run, then one output per row of ``rows`` (dicts with ``row_id``,
    ``smiles`` and, for a structure that did not parse, ``structure_error``).
    """
    run = check(run_dir)
    from .model import load_model

    model = load_model(run_dir)
    n_features = len(model.featuriser.schema.get("columns") or []) or None
    width = model.booster.num_features()
    if n_features is not None and n_features != width:
        raise Incompatible(
            "the model expects %d features but its schema lists %d" % (width, n_features)
        )
    if len({r["row_id"] for r in rows}) != len(rows):
        raise BadInput("the dataset repeats a row id")
    out: list[dict] = []
    total = len(rows)
    for start in range(0, total, BATCH):
        chunk = rows[start : start + BATCH]
        pairs = [(r["row_id"], r["smiles"]) for r in chunk if r.get("smiles")]
        found = {p["row_id"]: p for p in model.predict(pairs)} if pairs else {}
        for r in chunk:
            rid = r["row_id"]
            if rid in found:
                out.append(found[rid])
            else:
                why = r.get("structure_error") or "the structure did not parse"
                out.append({"row_id": rid, "error": why})
        if progress:
            progress(len(out), total)
    if [o["row_id"] for o in out] != [r["row_id"] for r in rows]:  # an invariant, not an input
        raise BadInput("the predictions are not in the dataset's row order")
    t = model.target
    return {
        "schema": SCHEMA,
        "target": {
            "kind": t.kind,
            "classes": list(t.classes),
            "positive_class": t.positive_class,
        },
        "task": run.get("target", {}).get("task"),
        "n_rows": total,
        "n_failed": sum("error" in o for o in out),
        "rows": out,
    }
