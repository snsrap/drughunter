"""Real, tiny models for the prediction tests: ``ligandbench`` resolves a CSV and trains the
CI search on it, as the server will, and the run is registered. A model is only as good as
the files it is made of, so the tests use the real ones.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "ligandbench" / "data"
CDD = DATA / "cdd_export_hiv1_protease.csv"
SYN = DATA / "synthetic_classification.csv"
BAD_SMILES = "this is not a structure"


def lb(*args) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "ligandbench", *map(str, args)],
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert proc.returncode == 0, (args, proc.stdout, proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def csv_with_bad_row(source: Path, out: Path, *, row: int, column: str = "SMILES") -> Path:
    """The CSV with one structure that cannot be read."""
    text = source.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    rows[row + 1][rows[0].index(column)] = BAD_SMILES
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    out.write_text(buf.getvalue(), encoding="utf-8")
    return out


def resolve(csv_path: Path, config: dict, out: Path) -> Path:
    """A resolved dataset directory (rows.json, dataset.json, source.csv)."""
    out.mkdir(parents=True)
    cfg = out.parent / (out.name + ".config.json")
    cfg.write_text(json.dumps(config))
    lb("resolve", csv_path, "--config", cfg, "--out", out)
    shutil.copyfile(csv_path, out / "source.csv")
    return out


def train(dataset_dir: Path, out: Path, *, seed: int = 0, strategy: str = "random") -> Path:
    out.mkdir(parents=True)
    lb(
        "train",
        dataset_dir,
        "--out",
        out,
        "--seed",
        seed,
        "--production-strategy",
        strategy,
        "--profile",
        "tiny",
    )
    return out


REGRESSION = {
    "structure_column": "SMILES",
    "target": {
        "column": "HIV-1 PR FRET (Q7K): Ki (nM)",
        "kind": "regression",
        "qualifier_column": "HIV-1 PR FRET (Q7K): Ki Qualifier",
        "transform": "pki",
        "source_units": "nM",
    },
}
NO_TARGET = {"structure_column": "SMILES", "target": None}


def synthetic(base: Path, kind: str, name: str) -> Path:
    """A resolved dataset of the science tests' own synthetic molecules: ``kind`` is
    ``binary`` (labels 0 and 1, positive 1) or ``multiclass`` (a, b, c).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent / "ligandbench"))
    try:
        from test_train_data import csv_of, dataset, write_dataset
    finally:
        sys.path.pop(0)
    meta, rows = dataset(kind)
    source = csv_of([(r["cells"][0], r["cells"][1]) for r in rows])
    return write_dataset(base / name, meta, rows, source=source)


def commit(store, resolved: Path, name: str = "d", scratch: Path | None = None) -> str:
    """Put a copy of a resolved directory into a DatasetStore as a ready dataset."""
    work = store.workdir()
    shutil.rmtree(work)
    shutil.copytree(resolved, work)
    return store.commit(work, name=name, source=resolved / "source.csv")["dataset_id"]
