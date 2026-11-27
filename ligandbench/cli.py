"""Command line: ``ligandbench <command>`` or ``python -m ligandbench``.

    info                                what this installation can do
    inspect CSV                         columns, types, structure candidates
    resolve CSV --config F --out DIR    rows and metadata, written into DIR
    depict  REQUEST                     SVGs for the SMILES a JSON file lists

Every command prints one JSON object. The exit code says whose problem a
failure is -- 2 a file or choice that cannot be used, 3 RDKit missing -- and the
error is printed as ``{"error", "kind"}``, which is how ProteinViewer's server
reads it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

    _out({"version": __version__, "rdkit": chem.require_rdkit(), "depiction": DEPICTION_VERSION})
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
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except ToolUnavailable as err:
        _out({"error": str(err), "kind": "unavailable"})
        return 3
    except (LigandbenchError, ValueError, OSError) as err:
        _out({"error": str(err), "kind": "bad-input"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
