"""Command line: ``ligandbench <command>`` or ``python -m ligandbench``.

    info                                what this installation can do
    inspect CSV                         columns, types, structure candidates
    resolve CSV --config F --out DIR    rows and metadata, written into DIR
    depict  REQUEST                     SVGs for the SMILES a JSON file lists
    split   DIR --strategy S --seed N   five repeated holdouts of a resolved dataset
    summarize DIR                       the dashboard's numbers for a resolved dataset in DIR
    train DIR --out RUN --seed N --production-strategy S
                                        tune, evaluate and refit an XGBoost model into RUN
    predict RUN DIR --out OUT           apply a registered run's model to every row of a dataset

Every command prints one JSON object. The exit code says whose problem a
failure is -- 2 a file or choice that cannot be used, 3 RDKit or XGBoost missing -- and the
error is printed as ``{"error", "kind"}``, which is how ProteinViewer's server
reads it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .columns import describe_columns, structure_candidates
from .errors import BadInput, LigandbenchError, ToolUnavailable
from .table import parse_csv


def _out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise BadInput("cannot read %s: %s" % (path, exc.strerror or exc)) from None


def _rdkit():
    try:
        from . import chem
    except ImportError as exc:
        raise ToolUnavailable("RDKit cannot be imported here: %s" % exc) from None
    return chem


def cmd_info(a) -> int:
    chem = _rdkit()
    from .depict import DEPICTION_VERSION
    from .features import FEATURISER_VERSION
    from .predict import RUN_SCHEMAS
    from .summary import SUMMARY_VERSION

    try:
        import xgboost

        boost = {"xgboost": xgboost.__version__}
    except ImportError as exc:  # not fatal to the other commands: said here, and train exits 3
        boost = {"xgboost": None, "xgboost_error": str(exc)}
    _out(
        {
            "version": __version__,
            "rdkit": chem.require_rdkit(),
            **boost,
            "depiction": DEPICTION_VERSION,
            "summary": SUMMARY_VERSION,
            "featuriser": FEATURISER_VERSION,
            "predicts": list(RUN_SCHEMAS),
        }
    )
    return 0


def cmd_inspect(a) -> int:
    table = parse_csv(_read(a.csv))
    _out(
        {
            "n_rows": len(table.records),
            "headers": table.headers,
            "columns": describe_columns(table),
            "structure_candidates": structure_candidates(table.headers),
        }
    )
    return 0


def cmd_resolve(a) -> int:
    _rdkit()
    from .resolve import resolve

    try:
        config = json.loads(_read(a.config))
    except ValueError as exc:
        raise BadInput("the config is not JSON: %s" % exc) from None
    if not isinstance(config, dict):
        raise BadInput("the config must be a JSON object")
    meta, rows = resolve(_read(a.csv), config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "rows.json", {"rows": rows})
    _write(out / "dataset.json", meta)
    _out(meta)
    return 0


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def cmd_depict(a) -> int:
    _rdkit()
    from .depict import DEPICTION_VERSION, depict

    try:
        request = json.loads(_read(a.request))
        smiles = request["smiles"]
        if not isinstance(smiles, list) or not all(isinstance(s, str) for s in smiles):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise BadInput('the request must be {"smiles": [text, ...]}') from None
    _out({"version": DEPICTION_VERSION, "svgs": [depict(s) for s in smiles]})
    return 0


def _json_object(path: Path, what: str) -> dict:
    data = _read(str(path))  # its own error: "cannot read ..." is not "is not JSON"
    try:
        found = json.loads(data)
    except ValueError as exc:
        raise BadInput("%s is not JSON: %s" % (path, exc)) from None
    if not isinstance(found, dict):
        raise BadInput("%s must hold a JSON object (%s)" % (path, what))
    return found


def cmd_split(a) -> int:
    """Five repeated holdouts of the dataset ``resolve`` wrote into DIR.

    Prints the result in canonical form (sorted keys, no spaces, ASCII), so the
    same dataset, strategy, seed and versions print the same bytes every time.
    An unavailable split is an answer (``status``), not an error.
    """
    _rdkit()
    from .split import canonical_json, split

    if a.strategy is None:
        raise BadInput("say which strategy: random or scaffold")
    try:
        seed = int(a.seed)
    except ValueError:
        raise BadInput("the seed must be a whole number, not %r" % a.seed) from None
    directory = Path(a.dataset)
    if not directory.is_dir():
        raise BadInput(
            "cannot read the dataset directory %s: it is not a directory (it should hold the "
            "rows.json and dataset.json that resolve wrote)" % directory
        )
    meta = _json_object(directory / "dataset.json", "the dataset's metadata")
    rows = _json_object(directory / "rows.json", "the dataset's rows").get("rows")
    if not isinstance(rows, list):
        raise BadInput("%s has no list of rows" % (directory / "rows.json"))
    print(canonical_json(split(meta, rows, strategy=a.strategy, seed=seed)))
    return 0


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(_read(str(path)))
    except ValueError as exc:
        raise BadInput("%s is not JSON: %s" % (path, exc)) from None
    if not isinstance(payload, dict):
        raise BadInput("%s is not a ligandbench dataset file" % path)
    return payload


def cmd_summarize(a) -> int:
    _rdkit()
    from .summary import summarize

    base = Path(a.directory)
    meta, rows = _read_json(base / "dataset.json"), _read_json(base / "rows.json").get("rows")
    try:
        summary = summarize(meta, rows)
    except (KeyError, TypeError, AttributeError):
        raise BadInput(
            "%s is not a ligandbench dataset (dataset.json and rows.json)" % base
        ) from None
    _out(summary)
    return 0


#: What a ready dataset directory keeps, and so what a run's ``dataset/`` snapshot copies.
#: (``DatasetStore`` also caches depictions and summaries there; those are not the dataset.)
DATASET_FILES = ("source.csv", "dataset.json", "rows.json")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _progress_writer(out: Path):
    """``note(state, stage, completed, total)`` rewrites ``progress.json``. A call
    without counts (``None``) repeats the last ones, so ``writing`` and ``done`` still
    say how many fits there were.
    """
    last = [0, 0]

    def note(state: str, stage: str, completed: int | None = None, total: int | None = None):
        if completed is not None:
            last[:] = [completed, total or 0]
        payload = {"state": state, "stage": stage, "completed": last[0], "total": last[1]}
        _write_atomic(out / "progress.json", json.dumps(payload))

    return note


def _file_map(out: Path) -> dict:
    """``{relative path: {"sha256", "bytes"}}`` for every file under ``out`` except
    ``run.json`` (written after this) and the transient ``progress.json``.
    """
    found = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        rel = path.relative_to(out).as_posix()
        if rel in ("run.json", "progress.json"):
            continue
        data = path.read_bytes()
        found[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return found


def cmd_train(a) -> int:
    """Tune, evaluate and refit an XGBoost model on the dataset in DIR, into the
    empty directory ``--out``. ``run.json`` is written last: no ``run.json``, no run.
    An unavailable production strategy is an answer (exit 0), not an error.
    """
    _rdkit()
    from . import model
    from .split import canonical_json
    from .train import RUN_SCHEMA, require_xgboost, train

    require_xgboost()
    if a.production_strategy is None:
        raise BadInput("say which production strategy: random or scaffold")
    try:
        seed = int(a.seed)
    except ValueError:
        raise BadInput("the seed must be a whole number, not %r" % a.seed) from None
    out = Path(a.out)
    if not out.is_dir() or any(out.iterdir()):
        raise BadInput("--out %s must be an existing, empty directory" % out)
    directory = Path(a.dataset)
    if not directory.is_dir():
        raise BadInput(
            "cannot read the dataset directory %s: it is not a directory (it should hold the "
            "source.csv, dataset.json and rows.json of a ready dataset)" % directory
        )
    meta = _json_object(directory / "dataset.json", "the dataset's metadata")
    rows = _json_object(directory / "rows.json", "the dataset's rows").get("rows")
    if not isinstance(rows, list):
        raise BadInput("%s has no list of rows" % (directory / "rows.json"))
    for name in DATASET_FILES:
        if not (directory / name).is_file():
            raise BadInput("the dataset directory %s has no %s" % (directory, name))

    note = _progress_writer(out)
    run = train(
        meta,
        rows,
        seed=seed,
        production_strategy=a.production_strategy,
        profile=a.profile,
        dataset_id=directory.resolve().name,
        progress=note,
    )
    if run.unavailable is not None:
        note("done", "the production strategy is unavailable")
        _out(
            {
                "status": "production_strategy_unavailable",
                "strategy": run.production_strategy,
                "unavailable": run.unavailable,
            }
        )
        return 0

    note("writing", "writing the run directory")
    model.save(out, run.booster, run.features)
    _write_atomic(out / "splits.json", canonical_json(run.splits))
    _write_atomic(out / "validation_trace.json", canonical_json(run.validation_trace))
    _write_atomic(out / "scaffolds.json", canonical_json(run.scaffolds))
    (out / "dataset").mkdir()
    for name in DATASET_FILES:
        shutil.copyfile(directory / name, out / "dataset" / name)
    record = {
        "schema": RUN_SCHEMA,
        "status": "completed",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **run.report,
        "files": _file_map(out),
    }
    _write_atomic(out / "run.json", canonical_json(record))
    note("done", "done")
    _out({"status": "completed", "run": "run.json"})
    return 0


def cmd_predict(a) -> int:
    """Apply the model in the run directory RUN to every row of the dataset in DIR, writing
    ``predictions.json`` into the existing directory ``--out`` (``progress.json`` beside it
    while it works). Nothing is written when the model cannot be applied: that is exit 2
    with ``"code": "incompatible"``.
    """
    _rdkit()
    from .predict import predict_rows
    from .train import require_xgboost

    require_xgboost()
    out = Path(a.out)
    if not out.is_dir():
        raise BadInput("--out %s must be an existing directory" % out)
    rows = _json_object(Path(a.dataset) / "rows.json", "the dataset's rows").get("rows")
    if not isinstance(rows, list):
        raise BadInput("%s has no list of rows" % (Path(a.dataset) / "rows.json"))

    def note(done: int, total: int) -> None:
        _write_atomic(
            out / "progress.json",
            json.dumps({"state": "running", "stage": "predicting", "completed": done, "total": total}),
        )

    result = predict_rows(a.run_dir, rows, progress=note)
    from .split import canonical_json

    _write_atomic(out / "predictions.json", canonical_json(result))
    _out({"status": "completed", "n_rows": result["n_rows"], "n_failed": result["n_failed"]})
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ligandbench", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("info", help="what this installation can do")
    p.set_defaults(run=cmd_info)
    p = sub.add_parser("inspect", help="columns, types and structure candidates of a CSV")
    p.add_argument("csv")
    p.set_defaults(run=cmd_inspect)
    p = sub.add_parser("resolve", help="turn a CSV and a set of choices into a dataset")
    p.add_argument("csv")
    p.add_argument("--config", required=True, help="a JSON file of the choices")
    p.add_argument("--out", required=True, help="the directory to write rows.json and dataset.json")
    p.set_defaults(run=cmd_resolve)
    p = sub.add_parser("depict", help="SVG depictions of the SMILES in a JSON file")
    p.add_argument("request")
    p.set_defaults(run=cmd_depict)
    p = sub.add_parser("split", help="five repeated holdouts of a resolved dataset")
    p.add_argument("dataset", help="the directory resolve wrote rows.json and dataset.json into")
    # Not argparse's own choices or type: a bad value is a JSON error with exit 2, as every
    # other refusal is, not a usage message.
    p.add_argument("--strategy", help="random or scaffold")
    p.add_argument("--seed", default="0", help="a whole number; the same seed, the same split")
    p.set_defaults(run=cmd_split)
    p = sub.add_parser("summarize", help="the dashboard's statistics for a resolved dataset")
    p.add_argument("directory", help="the directory resolve wrote dataset.json and rows.json into")
    p.set_defaults(run=cmd_summarize)
    p = sub.add_parser("train", help="tune, evaluate and refit an XGBoost QSAR model")
    p.add_argument(
        "dataset", help="a ready dataset's directory (source.csv, dataset.json, rows.json)"
    )
    p.add_argument("--out", required=True, help="an existing, empty directory for the run")
    p.add_argument("--seed", default="0", help="a whole number; the same seed, the same run")
    p.add_argument(
        "--production-strategy", help="random or scaffold: which split chooses the model"
    )
    p.add_argument("--profile", default="full", help="full (12 candidates) or tiny (CI)")
    p.set_defaults(run=cmd_train)
    p = sub.add_parser("predict", help="apply a registered run's model to every row of a dataset")
    p.add_argument("run_dir", help="the run directory (run.json, model.json, features.json)")
    p.add_argument("dataset", help="a ready dataset's directory (rows.json)")
    p.add_argument("--out", required=True, help="an existing directory for predictions.json")
    p.set_defaults(run=cmd_predict)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except ToolUnavailable as err:
        _out({"error": str(err), "kind": "unavailable"})
        return 3
    except (LigandbenchError, ValueError, OSError) as err:
        payload = {"error": str(err), "kind": "bad-input"}
        if getattr(err, "code", None):
            payload["code"] = err.code
        _out(payload)
        return 2


if __name__ == "__main__":
    sys.exit(main())
