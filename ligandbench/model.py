"""A trained model on disk, and the way back from it to predictions.

A run directory holds the production booster as ``model.json`` (XGBoost's own
JSON) and ``features.json``: the featuriser's schema and the target and class
configuration, i.e. everything needed to build inference features the way
training did and to read what the model returns. ``save`` writes the pair and
``load_model`` reads them back into a ``Model``.

``load_model`` refuses a ``features.json`` whose featuriser this build does not
implement (``Featuriser.from_schema``): a model is never fed a vector other than
the one it was trained on. A different RDKit or numpy version is not a refusal;
it is reported in ``Model.drift``, since the vector may then differ in the last
digit or in a descriptor's definition and the caller should say so.

``Model.predict`` never raises for a row: a structure that does not parse or
whose descriptors are not finite comes back as a failure beside the rows that
worked, in the order they were given.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .errors import BadInput
from .features import Featuriser
from .metrics import THRESHOLD
from .train import Target, predict, require_xgboost

__all__ = ["MODEL_FILE", "FEATURES_FILE", "Model", "load_model", "save"]

MODEL_FILE = "model.json"
FEATURES_FILE = "features.json"
_KINDS = ("regression", "binary", "multiclass")


def _replace_json(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save(directory: os.PathLike | str, booster, features: Mapping[str, Any]) -> None:
    """Write ``model.json`` and ``features.json`` into ``directory`` (each moved
    into place whole, so a reader never sees half of one).
    """
    out = Path(directory)
    tmp = out / "model.tmp.json"  # XGBoost picks its format from the extension
    booster.save_model(str(tmp))
    os.replace(tmp, out / MODEL_FILE)
    _replace_json(
        out / FEATURES_FILE,
        json.dumps(
            features, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ),
    )


class Model:
    """A booster with the featuriser and target configuration it was trained with."""

    def __init__(self, booster, featuriser: Featuriser, target: Target, features: dict) -> None:
        self.booster = booster
        self.featuriser = featuriser
        self.target = target
        self.features = features
        self.drift = Featuriser.dependency_drift(features["feature_schema"])

    def predict(self, pairs: Sequence[tuple]) -> list[dict]:
        """One result per ``(row_id, smiles)`` pair, in order.

        Regression: ``{"row_id", "value"}``. Classification: ``{"row_id",
        "probabilities": {class: p, ...}, "predicted_class"}``, the class chosen
        at probability ``>= 0.5`` (binary) or by argmax (multiclass). A row that
        cannot be featurised is ``{"row_id", "error": reason}``.
        """
        result = self.featuriser.featurise(list(pairs))
        failed = {f["row_id"]: f["reason"] for f in result.failures}
        found: dict = {}
        if result.row_ids:
            output = predict(self.target, self.booster, result.matrix)
            for i, row_id in enumerate(result.row_ids):
                found[row_id] = self._row(row_id, output[i])
        return [
            found[row_id] if row_id in found else {"row_id": row_id, "error": failed[row_id]}
            for row_id, _ in pairs
        ]

    def _row(self, row_id, out) -> dict:
        t = self.target
        if t.kind == "regression":
            return {"row_id": row_id, "value": float(out)}
        if t.kind == "binary":
            p = float(out)
            other = t.classes[0] if t.classes[1] == t.positive_class else t.classes[1]
            probabilities = {t.positive_class: p, other: 1.0 - p}
            predicted = t.positive_class if p >= THRESHOLD else other
        else:
            probabilities = {c: float(out[i]) for i, c in enumerate(t.classes)}
            predicted = t.classes[int(np.argmax(out))]
        ordered = {c: probabilities[c] for c in t.classes}
        return {"row_id": row_id, "probabilities": ordered, "predicted_class": predicted}


def load_model(directory: os.PathLike | str) -> Model:
    """The model in a run directory. ``BadInput`` for files that are missing or
    unreadable, or a featuriser this build does not implement.
    """
    base = Path(directory)
    try:
        features = json.loads((base / FEATURES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput("cannot read %s in %s: %s" % (FEATURES_FILE, base, exc)) from None
    if not isinstance(features, dict) or not isinstance(features.get("target"), dict):
        raise BadInput(
            "%s in %s is not a model's feature and target record" % (FEATURES_FILE, base)
        )
    featuriser = Featuriser.from_schema(features.get("feature_schema"))
    spec = features["target"]
    kind = spec.get("kind")
    classes = spec.get("classes") or []
    if kind not in _KINDS or (kind != "regression" and len(classes) < 2):
        raise BadInput("%s describes a target this build cannot read" % FEATURES_FILE)
    target = Target(kind, tuple(classes), spec.get("positive_class"))
    if kind == "binary" and target.positive_class not in target.classes:
        raise BadInput("%s names a positive class that is not a class" % FEATURES_FILE)
    lib = require_xgboost()
    booster = lib.Booster()
    try:
        booster.load_model(str(base / MODEL_FILE))
    except (lib.core.XGBoostError, OSError) as exc:
        raise BadInput("cannot read %s in %s: %s" % (MODEL_FILE, base, exc)) from None
    return Model(booster, featuriser, target, features)
