"""The command line, run as the server runs it: a subprocess, JSON out, an exit
code that says whose problem a failure is.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

DATA = Path(__file__).parent / "data"
CDD = DATA / "cdd_export_hiv1_protease.csv"


def run(*args):
    proc = subprocess.run(
        [sys.executable, "-m", "ligandbench", *map(str, args)], capture_output=True, text=True
    )
    return proc.returncode, json.loads(proc.stdout), proc.stderr


def test_info_names_rdkit_and_the_depiction_version():
    code, out, _ = run("info")
    assert code == 0 and out["rdkit"] and out["depiction"] and out["version"]


def test_inspect_lists_columns_and_structure_candidates():
    code, out, err = run("inspect", CDD)
    assert code == 0 and err == ""
    assert out["n_rows"] == 106 and out["structure_candidates"] == ["SMILES"]
    assert out["columns"][3]["name"] == "SMILES"


def test_resolve_writes_rows_and_metadata_and_prints_the_metadata(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"structure_column": "SMILES", "target": None}))
    out_dir = tmp_path / "out"
    code, meta, err = run("resolve", CDD, "--config", cfg, "--out", out_dir)
    assert code == 0 and err == ""
    assert json.loads((out_dir / "dataset.json").read_text()) == meta
    rows = json.loads((out_dir / "rows.json").read_text())["rows"]
    assert len(rows) == 106 and rows[0]["row_id"] == 0
    assert not list(out_dir.glob("*.tmp"))


def test_a_choice_that_cannot_be_used_exits_two_with_the_reason(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"structure_column": "Project", "target": None}))
    code, out, _ = run("resolve", CDD, "--config", cfg, "--out", tmp_path / "o")
    assert code == 2 and out["kind"] == "bad-input" and "none of the 106 values" in out["error"]
    assert not (tmp_path / "o" / "dataset.json").exists()


def test_a_file_that_is_not_there_exits_two():
    code, out, _ = run("inspect", "/no/such/file.csv")
    assert code == 2 and "cannot read" in out["error"]


def test_depict_returns_one_svg_per_smiles_and_an_empty_one_for_a_bad_smiles(tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"smiles": ["CCO", "not smiles", "c1ccccc1"]}))
    code, out, _ = run("depict", req)
    assert code == 0 and out["version"]
    ok, bad, ring = out["svgs"]
    assert ok.startswith("<svg") and ring.startswith("<svg") and bad == ""
    assert "currentColor" in ok and "<?xml" not in ok


def test_a_depict_request_of_the_wrong_shape_exits_two(tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"smiles": "CCO"}))
    code, out, _ = run("depict", req)
    assert code == 2 and "smiles" in out["error"]
