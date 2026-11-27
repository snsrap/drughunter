"""Saving a trained model and reading it back: the same predictions, in another process."""

import json
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("rdkit")
pytest.importorskip("xgboost")

from test_train_data import dataset  # noqa: E402

from ligandbench import model as M  # noqa: E402
from ligandbench import train as T  # noqa: E402
from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.features import Featuriser  # noqa: E402

KINDS = ("regression", "binary", "multiclass")
CHILD = """
import json, sys
from ligandbench.model import load_model
m = load_model(sys.argv[1])
pairs = [tuple(p) for p in json.load(open(sys.argv[2]))]
print(json.dumps(m.predict(pairs), allow_nan=False))
"""


def trained(kind, tmp_path):
    meta, rows = dataset(kind)
    run = T.train(meta, rows, seed=1, production_strategy="scaffold", profile="tiny")
    M.save(tmp_path, run.booster, run.features)
    pairs = [(r["row_id"], r["smiles"]) for r in rows]
    return meta, run, pairs


@pytest.mark.parametrize("kind", KINDS)
def test_a_saved_model_predicts_the_same_in_a_new_process(tmp_path, kind):
    meta, run, pairs = trained(kind, tmp_path)
    request = tmp_path / "pairs.json"
    request.write_text(json.dumps(pairs))
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(tmp_path), str(request)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout)
    assert [p["row_id"] for p in loaded] == [i for i, _ in pairs]

    target = T.make_target(meta)
    expected = T.predict(target, run.booster, Featuriser().featurise(pairs).matrix)
    if kind == "regression":
        got = np.array([p["value"] for p in loaded])
    else:
        got = np.array([[p["probabilities"][c] for c in target.classes] for p in loaded])
        if kind == "binary":
            got = got[:, target.classes.index(target.positive_class)]
    assert np.allclose(got, expected, rtol=1e-6, atol=1e-7)
    assert np.array_equal(got, expected)  # in practice, bit for bit


def test_files_are_native_xgboost_json_and_the_target_config(tmp_path):
    meta, run, _ = trained("binary", tmp_path)
    model = json.loads((tmp_path / "model.json").read_text())
    assert "learner" in model  # XGBoost's own JSON, not a pickle
    features = json.loads((tmp_path / "features.json").read_text())
    assert features["feature_schema"]["version"] == "morgan2048-desc7-v1"
    assert features["target"]["classes"] == meta["classes"]
    assert features["target"]["positive_class"] == "1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["features.json", "model.json"]


def test_classification_rows_carry_probabilities_and_the_decided_class(tmp_path):
    _, run, pairs = trained("binary", tmp_path)
    loaded = M.load_model(tmp_path)
    for row in loaded.predict(pairs[:20]):
        p = row["probabilities"]
        assert list(p) == ["1", "0"] and abs(sum(p.values()) - 1.0) < 1e-12
        assert row["predicted_class"] == ("1" if p["1"] >= 0.5 else "0")
    _, run, pairs = trained("multiclass", tmp_path)
    for row in M.load_model(tmp_path).predict(pairs[:20]):
        p = row["probabilities"]
        assert abs(sum(p.values()) - 1.0) < 1e-5
        assert row["predicted_class"] == max(p, key=p.get)


def test_a_row_that_cannot_be_featurised_is_a_failure_in_its_place_not_an_exception(tmp_path):
    trained("regression", tmp_path)
    out = M.load_model(tmp_path).predict(
        [(7, "CCO"), ("x", "not-smiles"), (9, ""), (2, "c1ccccc1")]
    )
    assert [r["row_id"] for r in out] == [7, "x", 9, 2]
    assert "value" in out[0] and "value" in out[3]
    assert out[1] == {"row_id": "x", "error": "not a valid SMILES"}
    assert out[2]["error"] == "no structure"
    assert M.load_model(tmp_path).predict([]) == []
    assert "error" in M.load_model(tmp_path).predict([(1, "bad")])[0]


def rewrite(tmp_path, change):
    path = tmp_path / "features.json"
    features = json.loads(path.read_text())
    change(features)
    path.write_text(json.dumps(features))


def test_a_featuriser_this_build_does_not_implement_is_refused(tmp_path):
    trained("regression", tmp_path)
    rewrite(tmp_path, lambda f: f["feature_schema"].update(version="morgan4096-v2"))
    with pytest.raises(BadInput, match="morgan4096-v2"):
        M.load_model(tmp_path)


def test_a_changed_generator_option_is_refused_too(tmp_path):
    trained("regression", tmp_path)
    rewrite(tmp_path, lambda f: f["feature_schema"]["fingerprint"].update(radius=3))
    with pytest.raises(BadInput, match="fingerprint"):
        M.load_model(tmp_path)


def test_a_different_dependency_version_loads_and_is_reported(tmp_path):
    trained("regression", tmp_path)
    assert M.load_model(tmp_path).drift == {}
    rewrite(tmp_path, lambda f: f["feature_schema"]["dependencies"].update(rdkit="2001.03.1"))
    drift = M.load_model(tmp_path).drift
    assert drift["rdkit"]["recorded"] == "2001.03.1" and "current" in drift["rdkit"]


def test_missing_or_damaged_files_are_bad_input_not_a_crash(tmp_path):
    with pytest.raises(BadInput, match="features.json"):
        M.load_model(tmp_path)
    trained("regression", tmp_path)
    (tmp_path / "model.json").write_text("{not a model")
    with pytest.raises(BadInput, match="model.json"):
        M.load_model(tmp_path)
    (tmp_path / "features.json").write_text("[]")
    with pytest.raises(BadInput, match="features.json"):
        M.load_model(tmp_path)
    rewrite_ok = tmp_path / "features.json"
    rewrite_ok.write_text(json.dumps({"feature_schema": {}, "target": {"kind": "ranking"}}))
    with pytest.raises(BadInput):
        M.load_model(tmp_path)
