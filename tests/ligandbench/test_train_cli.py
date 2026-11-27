"""``ligandbench train``, run as the server will run it: a subprocess, a run directory out.

The run directory is the contract issue 06 reads, so these tests check it from the outside:
what files there are, that ``files`` describes them byte for byte, that ``run.json`` is
strict JSON and is written last, and that two processes make the same run.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("xgboost")

from test_train_data import config, csv_of, dataset, write_dataset  # noqa: E402

from ligandbench import cli  # noqa: E402
from ligandbench import model as M  # noqa: E402
from ligandbench.resolve import resolve  # noqa: E402

DATA = Path(__file__).parent / "data"
CDD = DATA / "cdd_export_hiv1_protease.csv"
SNAPSHOT = ("source.csv", "dataset.json", "rows.json")
TOP = {
    "model.json",
    "features.json",
    "splits.json",
    "validation_trace.json",
    "scaffolds.json",
    "run.json",
    "progress.json",
    "dataset",
}


def make_dataset(tmp_path, kind="regression", name="ds-0123456789ab", **kw):
    meta, rows = dataset(kind, **kw)
    source = csv_of([(r["cells"][0], r["cells"][1]) for r in rows])
    return write_dataset(tmp_path / name, meta, rows, source=source), meta, rows


def run_cli(*args, env=None, code=None):
    proc = subprocess.run(
        [sys.executable, *(code or ["-m", "ligandbench"]), *map(str, args)],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc.stderr


def train(tmp_path, directory, *extra, out="run", strategy="scaffold", seed=4, **kw):
    target = tmp_path / out
    target.mkdir(exist_ok=True)
    result = run_cli(
        "train", directory, "--out", target, "--seed", seed, "--production-strategy", strategy,
        "--profile", "tiny", *extra, **kw,
    )  # fmt: skip
    return target, *result


def strict(text):
    def refuse(token):
        raise ValueError("not strict JSON: " + token)

    return json.loads(text, parse_constant=refuse)


# -- the run directory ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["regression", "binary", "multiclass"])
def test_a_run_is_written_whole_and_described_byte_for_byte(tmp_path, kind):
    directory, meta, rows = make_dataset(tmp_path, kind)
    out, code, answer, err = train(tmp_path, directory)
    assert code == 0 and answer == {"status": "completed", "run": "run.json"}, err
    assert {p.name for p in out.iterdir()} == TOP
    run = strict((out / "run.json").read_text())

    for key in ("schema", "status", "model_family", "created_utc", "seed", "profile", "grid"):
        assert key in run
    assert run["schema"] == "qsar-run-v1" and run["status"] == "completed"
    assert run["model_family"] == "xgboost" and run["seed"] == 4 and run["profile"] == "tiny"
    assert run["created_utc"].endswith("Z") and len(run["created_utc"]) == 20
    assert run["dataset"] == {
        "id": "ds-0123456789ab",
        "content_hash": meta["content_hash"],
        "n_rows": 100,
        "n_eligible": 100,
    }
    task = "regression" if kind == "regression" else "classification"
    assert run["target"]["task"] == task and run["target"]["column"] == "y"
    assert run["target"]["classes"] == (meta["classes"] or None)
    assert run["production"]["strategy"] == "scaffold" and run["production"]["n_refit_rows"] == 100
    assert set(run["strategies"]) == {"random", "scaffold"}
    for s in run["strategies"].values():
        assert s["status"] == "ok" and s["unavailable"] is None and len(s["repeats"]) == 5
        assert {"repeat", "seed", "config", "validation", "test", "undefined"} <= set(
            s["repeats"][0]
        )
        assert all(
            {"mean", "sd", "n_defined", "n_total"} == set(a) for a in s["aggregate"].values()
        )
    assert set(run["versions"]) >= {
        "ligandbench", "rdkit", "xgboost", "numpy", "python",
        "scaffold_policy", "split_algorithm", "featuriser",
    }  # fmt: skip
    assert (
        run["feature_schema"] == json.loads((out / "features.json").read_text())["feature_schema"]
    )

    files = run["files"]
    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} - {
        "run.json",
        "progress.json",
    }
    assert set(files) == on_disk
    assert {"model.json", "features.json", "splits.json", "validation_trace.json",
            "scaffolds.json", "dataset/source.csv", "dataset/dataset.json",
            "dataset/rows.json"} <= set(files)  # fmt: skip
    for rel, info in files.items():
        data = (out / rel).read_bytes()
        assert info == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}, rel
    for name in SNAPSHOT:  # the snapshot is the dataset's own bytes
        assert (out / "dataset" / name).read_bytes() == (directory / name).read_bytes()
    assert not list(out.rglob("*.tmp")) and not list(out.rglob("*.tmp.json"))


def test_each_file_holds_what_the_contract_says(tmp_path):
    directory, meta, rows = make_dataset(tmp_path)
    out, code, *_ = train(tmp_path, directory)
    assert code == 0
    splits = strict((out / "splits.json").read_text())
    assert set(splits) == {"random", "scaffold"} and splits["scaffold"]["status"] == "ok"
    assert len(splits["random"]["repeats"]) == 5
    trace = strict((out / "validation_trace.json").read_text())
    assert trace["loss"] == "rmse" and len(trace["grid"]) == 2
    repeat = trace["strategies"]["scaffold"]["repeats"][0]
    assert [c["candidate"] for c in repeat["candidates"]] == [0, 1] and "selected" in repeat
    production = trace["production"]
    assert production["strategy"] == "scaffold" and len(production["candidates"]) == 2
    run = json.loads((out / "run.json").read_text())
    assert run["production"]["config"] == trace["grid"][production["selected"]]
    scaffolds = strict((out / "scaffolds.json").read_text())
    assert scaffolds["scaffold_policy"] == "murcko-v1" and len(scaffolds["scaffolds"]) == 20
    assert scaffolds["scaffolds"] == sorted(set(scaffolds["scaffolds"]))
    features = strict((out / "features.json").read_text())
    assert features["target"]["kind"] == "regression"
    assert M.load_model(out).predict([(0, "CCO")])[0]["value"] is not None


def test_progress_ends_done_having_counted_every_fit(tmp_path):
    directory, *_ = make_dataset(tmp_path)
    out, code, *_ = train(tmp_path, directory)
    progress = json.loads((out / "progress.json").read_text())
    assert code == 0 and progress["state"] == "done"
    assert progress["completed"] == progress["total"] == 2 * 5 * 2 + 1


def test_progress_is_rewritten_atomically_through_the_stages(tmp_path, monkeypatch, capsys):
    directory, *_ = make_dataset(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    seen, real = [], cli._write_atomic

    def spy(path, text):
        real(path, text)
        if path.name == "progress.json":
            seen.append(json.loads(path.read_text()))

    monkeypatch.setattr(cli, "_write_atomic", spy)
    code = cli.main(["train", str(directory), "--out", str(out), "--production-strategy",
                     "random", "--profile", "tiny"])  # fmt: skip
    assert code == 0
    states = [s["state"] for s in seen]
    assert states[0] == "splitting" and states[-1] == "done"
    assert states.index("featurizing") < states.index("tuning") < states.index("refitting")
    assert states.index("refitting") < states.index("writing") < states.index("done")
    assert all(set(s) == {"state", "stage", "completed", "total"} for s in seen)
    assert all(a["completed"] <= b["completed"] for a, b in zip(seen, seen[1:]))


def test_run_json_is_the_last_thing_written(tmp_path, monkeypatch):
    directory, *_ = make_dataset(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    order, real = [], cli._write_atomic

    def spy(path, text):
        real(path, text)
        order.append(path.name)

    monkeypatch.setattr(cli, "_write_atomic", spy)
    cli.main(["train", str(directory), "--out", str(out), "--production-strategy", "random",
              "--profile", "tiny"])  # fmt: skip
    written = [n for n in order if n != "progress.json"]
    assert written[-1] == "run.json" and written.count("run.json") == 1
    assert order[-1] == "progress.json"  # `done` comes after it, and is not in `files`


def test_a_run_that_fails_while_writing_leaves_no_run_json(tmp_path, monkeypatch):
    directory, *_ = make_dataset(tmp_path)
    out = tmp_path / "run"
    out.mkdir()

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(M, "save", broken)
    with pytest.raises(RuntimeError):
        cli.main(["train", str(directory), "--out", str(out), "--production-strategy", "random",
                  "--profile", "tiny"])  # fmt: skip
    assert not (out / "run.json").exists()


# -- determinism, across processes --------------------------------------------------------------


@pytest.mark.parametrize("kind", ["regression", "binary"])
def test_two_processes_make_the_same_run_whatever_their_hash_seed(tmp_path, kind):
    directory, meta, rows = make_dataset(tmp_path, kind)
    a, code_a, *_ = train(tmp_path, directory, out="a", env={"PYTHONHASHSEED": "1"})
    b, code_b, *_ = train(tmp_path, directory, out="b", env={"PYTHONHASHSEED": "999"})
    assert code_a == code_b == 0
    ra, rb = (json.loads((d / "run.json").read_text()) for d in (a, b))
    created_a, created_b = ra.pop("created_utc"), rb.pop("created_utc")
    assert ra == rb and created_a and created_b
    for name in ("model.json", "features.json", "splits.json", "validation_trace.json",
                 "scaffolds.json"):  # fmt: skip
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    pairs = [(r["row_id"], r["smiles"]) for r in rows]
    assert M.load_model(a).predict(pairs) == M.load_model(b).predict(pairs)


# -- what is refused, and what is answered -------------------------------------------------------


def test_an_unavailable_production_strategy_is_an_answer_and_writes_no_run_json(tmp_path):
    pairs = [("C" * k + "O", 0.5 * k) for k in range(2, 62)]
    meta, rows = resolve(csv_of(pairs), config("regression"))
    directory = write_dataset(tmp_path / "ds-aaaaaaaaaaaa", meta, rows, source=csv_of(pairs))
    out, code, answer, _ = train(tmp_path, directory, strategy="scaffold")
    assert code == 0 and answer["status"] == "production_strategy_unavailable"
    assert answer["strategy"] == "scaffold" and answer["unavailable"]["failed_constraints"]
    assert not (out / "run.json").exists() and not (out / "model.json").exists()
    assert json.loads((out / "progress.json").read_text())["state"] == "done"
    # the other strategy is available on it: asked for, the run completes and reports scaffold
    out, code, answer, _ = train(tmp_path, directory, out="random", strategy="random")
    assert code == 0 and answer["status"] == "completed"
    run = json.loads((out / "run.json").read_text())
    assert run["strategies"]["scaffold"]["status"] == "split_unavailable"
    assert run["strategies"]["scaffold"]["unavailable"]["failed_constraints"]
    assert run["strategies"]["scaffold"]["repeats"] == []


def test_input_that_cannot_be_used_exits_two_with_a_reason(tmp_path):
    directory, *_ = make_dataset(tmp_path)

    def refused(*extra, out=None, ds=directory, strategy="scaffold"):
        target = out or tmp_path / ("ok%d" % len(list(tmp_path.glob("ok*"))))
        if out is None:
            target.mkdir()
        args = ["train", ds, "--out", target, "--profile", "tiny", *extra]
        if strategy:
            args += ["--production-strategy", strategy]
        code, answer, _ = run_cli(*args)
        assert code == 2 and answer["kind"] == "bad-input", answer
        return answer["error"]

    assert "empty directory" in refused(out=tmp_path / "missing")
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    assert "empty directory" in refused(out=tmp_path / "full")
    assert "strategy" in refused(strategy=None)
    assert "unknown production strategy" in refused(strategy="cluster")
    assert "seed" in refused("--seed", "abc")
    assert "seed" in refused("--seed", "-1")
    assert "not a directory" in refused(ds=tmp_path / "nothing")
    (tmp_path / "profile").mkdir()
    code, answer, _ = run_cli("train", directory, "--out", tmp_path / "profile", "--production-strategy",
                              "random", "--profile", "enormous")  # fmt: skip
    assert code == 2 and "unknown profile" in answer["error"]
    (directory / "source.csv").unlink()
    assert "no source.csv" in refused()


def test_a_target_free_dataset_exits_two(tmp_path):
    meta, rows = resolve(
        csv_of([("CCO", 1), ("CCN", 2)]), {"structure_column": "smiles", "target": None}
    )
    directory = write_dataset(tmp_path / "ds-bbbbbbbbbbbb", meta, rows, source=b"smiles,y\n")
    out, code, answer, _ = train(tmp_path, directory)
    assert code == 2 and "no target" in answer["error"] and not (out / "run.json").exists()


def test_descriptors_that_are_not_finite_stop_the_run_naming_the_rows(
    tmp_path, monkeypatch, capsys
):
    from ligandbench.features import FeatureResult, Featuriser

    directory, *_ = make_dataset(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    real = Featuriser.featurise

    def featurise(self, pairs):
        good = real(self, [p for p in pairs if p[0] not in (3, 8)])
        bad = [{"row_id": i, "reason": "descriptor mol_wt is not finite"} for i in (3, 8)]
        return FeatureResult(good.matrix, good.row_ids, bad)

    monkeypatch.setattr(Featuriser, "featurise", featurise)
    code = cli.main(
        ["train", str(directory), "--out", str(out), "--production-strategy", "random",
         "--profile", "tiny"]
    )  # fmt: skip
    answer = json.loads(capsys.readouterr().out)
    assert code == 2 and "row ids: 3, 8" in answer["error"]
    assert not (out / "run.json").exists() and not (out / "model.json").exists()


# -- the dependency, missing --------------------------------------------------------------------

BLOCKED = (
    "import sys; sys.modules['xgboost'] = None; from ligandbench.cli import main; "
    "sys.exit(main(sys.argv[1:]))"
)


def test_a_missing_xgboost_is_exit_three_from_train_and_reported_by_info(tmp_path):
    directory, *_ = make_dataset(tmp_path)
    (tmp_path / "run").mkdir()
    code, answer, _ = run_cli(
        "train", directory, "--out", tmp_path / "run", "--production-strategy", "random",
        code=["-c", BLOCKED],
    )  # fmt: skip
    assert code == 3 and answer["kind"] == "unavailable" and "XGBoost" in answer["error"]
    assert not list((tmp_path / "run").iterdir())
    code, info, _ = run_cli("info", code=["-c", BLOCKED])
    assert code == 0 and info["xgboost"] is None and "xgboost_error" in info
    assert info["rdkit"]  # the other commands still work


def test_info_reports_xgboost_beside_rdkit():
    import xgboost

    code, info, _ = run_cli("info")
    assert code == 0 and info["xgboost"] == xgboost.__version__ and info["rdkit"]


# -- the CDD export: real parsing, no expensive search ---------------------------------------------


def test_the_cdd_export_trains_on_the_tiny_profile_through_the_command(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "structure_column": "SMILES",
        "target": {
            "column": "HIV-1 PR FRET (Q7K): Ki (nM)",
            "kind": "regression",
            "qualifier_column": "HIV-1 PR FRET (Q7K): Ki Qualifier",
            "transform": "pki",
            "source_units": "nM",
        },
    }))  # fmt: skip
    ds = tmp_path / "ds-cccccccccccc"
    code, meta, err = run_cli("resolve", CDD, "--config", cfg, "--out", ds)
    assert code == 0, err
    (ds / "source.csv").write_bytes(CDD.read_bytes())
    # Real parsing and the split diagnostics are what this exercises: its censored rows stay
    # out, and both strategies are feasible, so the (tiny) run completes.
    out, code, answer, err = train(tmp_path, ds, strategy="scaffold")
    assert code == 0 and answer["status"] == "completed", err
    run = json.loads((out / "run.json").read_text())
    assert run["dataset"]["n_rows"] == 106 and run["dataset"]["n_eligible"] == 102
    assert run["production"]["n_refit_rows"] == 102 and run["target"]["units"] == "pKi"
    assert [
        e["row_id"] for e in json.loads((out / "splits.json").read_text())["scaffold"]["excluded"]
    ] == [102, 103, 104, 105]
