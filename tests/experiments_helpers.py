"""Run directories as ``ligandbench train`` leaves them, built by the tests.

The real writer is a different piece of work; this builds the layout the run
contract describes: any bytes for ``model.json``, real JSON for the rest, and
a ``files`` map with the real sha256 of every file except ``run.json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_BYTES = b'{"learner": "xgboost-model-bytes-9d1f3a7c"}'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strategy(seed: int, *, status: str = "ok") -> dict:
    repeats = [
        {
            "repeat": i,
            "seed": seed + i,
            "config": {"max_depth": 3, "eta": 0.1},
            "validation": [{"config": {"max_depth": 3}, "loss": 0.5 + i}],
            "test": {"rmse": 0.7, "r2": 0.4},
            "undefined": {},
        }
        for i in range(2)
    ]
    return {
        "status": status,
        "unavailable": None,
        "repeats": repeats,
        "aggregate": {"rmse": {"mean": 0.7, "sd": 0.01, "n_defined": 2, "n_total": 2}},
    }


def make_run(base: Path, name: str = "run", *, doc: dict | None = None, extra=None) -> Path:
    """Write a completed run into ``base/name`` and return it.

    ``doc`` is merged over ``run.json`` (before ``files`` is added; give
    ``files`` to replace it). ``extra`` maps relative paths to bytes written
    *after* the checksums were taken, as the trainer's ``progress.json`` is.
    """
    run = base / name
    contents = {
        "model.json": MODEL_BYTES,
        "features.json": json.dumps({"spec": "morgan", "bits": 64}).encode(),
        "splits.json": json.dumps(
            {"random": {"repeats": []}, "scaffold": {"repeats": []}}
        ).encode(),
        "validation_trace.json": json.dumps({"random": [], "scaffold": []}).encode(),
        "scaffolds.json": json.dumps(
            {"scaffold_policy": "murcko-v1", "scaffolds": ["", "c1ccccc1"]}
        ).encode(),
        "dataset/source.csv": b"smiles,pKi\r\nCCO,5.1\r\nCCN,6.2\r\n",
        "dataset/dataset.json": json.dumps(
            {"dataset_id": "ds-abcdef012345", "n_rows": 40}
        ).encode(),
        "dataset/rows.json": json.dumps({"rows": [{"row_id": 0}, {"row_id": 1}]}).encode(),
    }
    for rel, data in contents.items():
        path = run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    document = {
        "schema": "qsar-run-v1",
        "status": "completed",
        "model_family": "xgboost",
        "created_utc": "2026-11-01T10:00:00Z",
        "seed": 7,
        "profile": "tiny",
        "grid": [{"max_depth": 3}],
        "dataset": {
            "id": "ds-abcdef012345",
            "content_hash": "ab" * 32,
            "n_rows": 40,
            "n_eligible": 38,
        },
        "target": {
            "column": "pKi",
            "task": "regression",
            "units": "nM",
            "transform": "pKi",
            "classes": None,
            "positive_class": None,
        },
        "production": {
            "strategy": "scaffold",
            "config": {"max_depth": 3, "eta": 0.1},
            "mean_validation_loss": 0.5,
            "n_refit_rows": 38,
            "tie_break": ["max_depth", "eta"],
        },
        "strategies": {"random": strategy(100), "scaffold": strategy(200)},
        "feature_schema": {"version": 1, "radius": 2, "n_bits": 64},
        "versions": {
            "ligandbench": "0.1",
            "rdkit": "2025.03.1",
            "xgboost": "3.2.0",
            "numpy": "2.0.0",
            "python": "3.11.14",
            "scaffold_policy": "murcko-v1",
            "split_algorithm": "grouped-holdout-v1",
            "featuriser": "morgan-v1",
        },
        "files": {rel: {"sha256": sha(data), "bytes": len(data)} for rel, data in contents.items()},
    }
    document.update(doc or {})
    (run / "run.json").write_text(json.dumps(document))
    for rel, data in (extra or {}).items():
        path = run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return run


def rewrite_run_json(run: Path, **changes) -> None:
    """Change ``run.json`` after the fact (to make a source inconsistent)."""
    document = json.loads((run / "run.json").read_text())
    document.update(changes)
    (run / "run.json").write_text(json.dumps(document))
