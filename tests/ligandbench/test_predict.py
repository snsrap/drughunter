"""``ligandbench predict``: a registered run's model applied to a dataset, and the checks
that come before any inference.

A model is applied only the way it was trained, so each way an artifact can be wrong has
its own test: a damaged file, an unknown schema, another RDKit or XGBoost, a feature
schema or width this build does not implement. Every one is refused before a number is
made, with a code the server reads.
"""

import hashlib
import json
import shutil

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("xgboost")

from test_train_cli import make_dataset, run_cli, train  # noqa: E402

from ligandbench import predict as P  # noqa: E402
from ligandbench.model import load_model  # noqa: E402


@pytest.fixture(scope="module")
def regression(tmp_path_factory):
    base = tmp_path_factory.mktemp("reg")
    directory, meta, rows = make_dataset(base)
    run, code, out, err = train(base, directory)
    assert code == 0, err
    return base, directory, run, rows


@pytest.fixture
def copy(regression, tmp_path):
    """A private copy of the run, to damage."""
    _, directory, run, rows = regression
    mine = tmp_path / "run"
    shutil.copytree(run, mine)
    return mine, directory, rows


def predict(run, directory, tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return run_cli("predict", run, directory, "--out", out), out


def rewrite(run, **changes):
    doc = json.loads((run / "run.json").read_text())
    doc.update(changes)
    (run / "run.json").write_text(json.dumps(doc))


def test_every_row_gets_an_output_in_the_datasets_order(regression, tmp_path):
    _, directory, run, rows = regression
    (code, out, err), where = predict(run, directory, tmp_path)
    assert code == 0, err
    assert out["n_rows"] == len(rows)
    got = json.loads((where / "predictions.json").read_text())
    assert got["schema"] == P.SCHEMA
    assert [r["row_id"] for r in got["rows"]] == [r["row_id"] for r in rows]
    assert all("value" in r for r in got["rows"])
    assert got["target"]["kind"] == "regression"


def test_the_targets_of_the_prediction_dataset_play_no_part(regression, tmp_path):
    _, directory, run, rows = regression
    other = tmp_path / "other"
    shutil.copytree(directory, other)
    doc = json.loads((other / "rows.json").read_text())
    for r in doc["rows"]:
        r["target"], r["training_eligible"] = None, False
        r["cells"] = r["cells"][:1] + [""]
    (other / "rows.json").write_text(json.dumps(doc))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (c1, _, _), w1 = predict(run, directory, tmp_path / "a")
    (c2, _, _), w2 = predict(run, other, tmp_path / "b")
    assert c1 == c2 == 0
    assert (w1 / "predictions.json").read_text() == (w2 / "predictions.json").read_text()


def test_a_row_without_a_structure_keeps_its_place_and_says_why(regression, tmp_path):
    _, directory, run, rows = regression
    other = tmp_path / "other"
    shutil.copytree(directory, other)
    doc = json.loads((other / "rows.json").read_text())
    doc["rows"][1].update(smiles=None, structure_error="the structure did not parse")
    (other / "rows.json").write_text(json.dumps(doc))
    (code, out, err), where = predict(run, other, tmp_path)
    assert code == 0, err
    got = json.loads((where / "predictions.json").read_text())["rows"]
    assert [r["row_id"] for r in got] == [r["row_id"] for r in doc["rows"]]
    assert got[1] == {"row_id": doc["rows"][1]["row_id"], "error": "the structure did not parse"}
    assert "value" in got[0] and "value" in got[2]
    (tmp_path / "x").mkdir()
    whole = predict(run, directory, tmp_path / "x")[1]
    full = json.loads((whole / "predictions.json").read_text())["rows"]
    assert got[2] == full[2]  # a neighbour's number did not move


def refused(result, where, needle):
    code, out, err = result
    assert code == 2, (code, out, err)
    assert out["code"] == "incompatible" and needle in out["error"], out
    assert not (where / "predictions.json").exists()


def test_a_damaged_model_is_refused_before_inference(copy, tmp_path):
    run, directory, _ = copy
    (run / "model.json").write_bytes((run / "model.json").read_bytes() + b" ")
    result, where = predict(run, directory, tmp_path)
    refused(result, where, "model.json")


def test_a_missing_model_is_refused(copy, tmp_path):
    run, directory, _ = copy
    (run / "model.json").unlink()
    result, where = predict(run, directory, tmp_path)
    refused(result, where, "model.json")


def test_an_edited_feature_record_is_refused(copy, tmp_path):
    run, directory, _ = copy
    feats = json.loads((run / "features.json").read_text())
    feats["feature_schema"]["columns"] = list(reversed(feats["feature_schema"]["columns"]))
    text = json.dumps(feats)
    (run / "features.json").write_text(text)
    doc = json.loads((run / "run.json").read_text())
    doc["files"]["features.json"] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text)}
    (run / "run.json").write_text(json.dumps(doc))
    result, where = predict(run, directory, tmp_path)
    refused(result, where, "feature schema")


@pytest.mark.parametrize("library", ["rdkit", "xgboost"])
def test_a_different_rdkit_or_xgboost_is_refused_with_both_versions(copy, tmp_path, library):
    run, directory, _ = copy
    versions = json.loads((run / "run.json").read_text())["versions"]
    versions[library] = "0.0.1"
    rewrite(run, versions=versions)
    result, where = predict(run, directory, tmp_path)
    refused(result, where, library)
    assert "0.0.1" in result[1]["error"]


def test_an_unknown_run_schema_is_refused(copy, tmp_path):
    run, directory, _ = copy
    rewrite(run, schema="qsar-run-v9")
    result, where = predict(run, directory, tmp_path)
    refused(result, where, "qsar-run-v9")


def test_a_run_that_did_not_complete_is_refused(copy, tmp_path):
    run, directory, _ = copy
    rewrite(run, status="running")
    result, where = predict(run, directory, tmp_path)
    refused(result, where, "not a completed")


def test_the_check_loads_no_model_and_names_what_it_found(regression):
    _, _, run, _ = regression
    assert P.check(run)["schema"] == "qsar-run-v1"
    assert load_model(run).booster.num_features() == 2055
