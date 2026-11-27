"""Applying a registered model to a whole dataset: the job, what it keeps, and every way it
must not go wrong -- a model this build cannot apply, a damaged one, a dataset that is not
the one asked for, a model deleted while a job holds it, a rerun that fails.

The models are real (tiny ones, trained by ligandbench in the fixtures below), behind the
real adapter, the real registry and the real dataset store.
"""

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("xgboost")

from proteinviewer import rowquery
from proteinviewer.dataset import DatasetStore
from proteinviewer.errors import Conflict, Incompatible, NotFound, ToolFailed
from proteinviewer.experiments import ExperimentStore
from proteinviewer.ligandbench import Ligandbench
from proteinviewer.predictions import Predictions, set_key
from proteinviewer.schemas import DatasetRowsRequest
from proteinviewer.service import ViewerService

from . import predictions_helpers as H

BAD_ROW = 3


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """Resolved datasets and trained runs, made once: a regression (one row whose structure
    cannot be read), the same file without a target, a second regression model and a
    classification.
    """
    base = tmp_path_factory.mktemp("made")
    bad_csv = H.csv_with_bad_row(H.CDD, base / "bad.csv", row=BAD_ROW)
    reg = H.resolve(bad_csv, H.REGRESSION, base / "reg")
    return {
        "reg": reg,
        "notarget": H.resolve(bad_csv, H.NO_TARGET, base / "notarget"),
        "run_a": H.train(reg, base / "run_a", seed=0),
        "run_b": H.train(reg, base / "run_b", seed=1),
        "binary": (binary := H.synthetic(base, "binary", "binary")),
        "binary_run": H.train(binary, base / "binary_run", seed=0),
        "multi": (multi := H.synthetic(base, "multiclass", "multi")),
        "multi_run": H.train(multi, base / "multi_run", seed=0),
    }


class Rig:
    def __init__(self, tmp_path, made, tool=None, **limits):
        self.tool = tool or Ligandbench()
        self.datasets = DatasetStore(tmp_path / "datasets")
        self.registry = ExperimentStore(tmp_path / "registry", lease_timeout=0.5)
        self.predictions = Predictions(
            self.tool, self.registry, self.datasets, tmp_path / "jobs", timeout=120, **limits
        )
        self.made, self.tmp = made, tmp_path
        self.service = ViewerService(
            None, None, None, None, ligands=self.tool, datasets=self.datasets,
            experiments=self.registry, predictions=self.predictions,
        )

    def dataset(self, name="reg"):
        return H.commit(self.datasets, self.made[name])

    def register(self, run: Path, name: str, run_id: str | None = None) -> str:
        copy = self.tmp / "regsrc" / (run_id or name)
        shutil.copytree(run, copy)
        return self.registry.register(copy, run_id or name, name)["id"]

    def wait(self, job_id: str, timeout=120.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            p = self.predictions.progress(job_id)
            if p["state"] in ("done", "error", "cancelled"):
                return p
            time.sleep(0.05)
        raise AssertionError("the job did not end: %r" % self.predictions.progress(job_id))

    def run(self, dataset_id: str, experiment_id: str) -> dict:
        return self.wait(self.predictions.start(dataset_id, experiment_id)["id"])

    def rows(self, dataset_id, **kw):
        args = dict(dataset_id=dataset_id, offset=0, limit=200, sort=None, filters=(), depict=False)
        args.update(kw)
        return self.service.dataset_rows(DatasetRowsRequest(**args))


@pytest.fixture
def rig(tmp_path, made):
    r = Rig(tmp_path, made)
    yield r
    r.predictions.shutdown()


# ---- what a job keeps -----------------------------------------------------------------------------
def test_a_regression_model_predicts_every_row_in_the_models_own_units(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "model A")
    done = rig.run(ds, exp)
    assert done["state"] == "done" and done["total"] == done["completed"]
    kept = rig.datasets.prediction(ds, done["key"])
    n = len(rig.datasets.rows(ds))
    assert kept["dataset"]["n_rows"] == n
    assert set(kept["values"]) | set(kept["errors"]) == {str(r["row_id"]) for r in rig.datasets.rows(ds)}
    (out,) = kept["outputs"]
    assert out["type"] == "number" and out["units"] == "pKi"   # pKi stays pKi: no inverse transform
    assert "pKi" in out["label"] and "model A" in out["label"] and exp[:8] in out["label"]
    assert out["key"].startswith(set_key(exp, kept["experiment"]["checksum"]))


def test_a_row_whose_structure_cannot_be_read_has_no_number_and_a_reason(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    done = rig.run(ds, exp)
    assert done["n_failed"] == 1
    kept = rig.datasets.prediction(ds, done["key"])
    bad = next(r for r in rig.datasets.rows(ds) if r["smiles"] is None)
    assert str(bad["row_id"]) in kept["errors"] and str(bad["row_id"]) not in kept["values"]
    page = rig.rows(ds)
    row = next(r for r in page["rows"] if r["row_id"] == bad["row_id"])
    assert row["predictions"] == {} and kept["errors"][str(bad["row_id"])] in row["prediction_errors"][done["key"]]
    # every other row has its own number
    assert all(r["predictions"] for r in page["rows"] if r["row_id"] != bad["row_id"])


def test_a_dataset_without_a_target_can_use_a_model(rig, made):
    with_target, bare = rig.dataset("reg"), rig.dataset("notarget")
    exp = rig.register(made["run_a"], "m")
    a, b = rig.run(with_target, exp), rig.run(bare, exp)
    assert b["state"] == "done"
    va = rig.datasets.prediction(with_target, a["key"])["values"]
    vb = rig.datasets.prediction(bare, b["key"])["values"]
    assert va == vb   # the target plays no part in the features


def test_the_whole_dataset_is_predicted_whatever_the_page_is_filtered_to(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    done = rig.run(ds, exp)
    key = rig.datasets.prediction(ds, done["key"])["outputs"][0]["key"]
    n_all = len(rig.datasets.rows(ds))
    top = rig.rows(ds, sort={"key": key, "dir": "desc"}, limit=5)
    assert top["total"] == n_all and len(top["rows"]) == 5
    values = [r["predictions"][key] for r in top["rows"]]
    assert values == sorted(values, reverse=True)
    everything = rig.rows(ds)
    best = max(r["predictions"][key] for r in everything["rows"] if key in r["predictions"])
    assert values[0] == best                       # sorted over all rows, then paged
    cutoff = sorted(values)[0]
    kept = rig.rows(ds, filters=({"key": key, "op": "gte", "value": cutoff},))
    assert kept["total"] == sum(1 for r in everything["rows"] if r["predictions"].get(key, -1e9) >= cutoff)
    missing = rig.rows(ds, filters=({"key": key, "op": "missing", "value": True},))
    assert missing["total"] == 1                    # the unreadable row


def test_two_models_have_columns_that_can_be_told_apart(rig, made):
    ds = rig.dataset()
    a, b = rig.register(made["run_a"], "alpha"), rig.register(made["run_b"], "beta")
    ka, kb = rig.run(ds, a)["key"], rig.run(ds, b)["key"]
    assert ka != kb
    shown = rig.service.dataset_status(type("R", (), {"dataset_id": ds})())
    labels = [c["label"] for c in shown["schema"] if c["kind"] == "prediction"]
    assert len(labels) == 2 and any("alpha" in l for l in labels) and any("beta" in l for l in labels)
    assert len({c["key"] for c in shown["schema"]}) == len(shown["schema"])


@pytest.mark.parametrize("kind", ["binary", "multi"])
def test_a_classification_model_gives_a_label_and_a_column_per_class_in_the_stored_order(rig, made, kind):
    ds, exp = rig.dataset(kind), rig.register(made[kind + "_run"], "classifier")
    done = rig.run(ds, exp)
    assert done["state"] == "done", done
    kept = rig.datasets.prediction(ds, done["key"])
    classes = json.loads((rig.registry.artifacts / exp / "features.json").read_text())["target"]["classes"]
    names = [o["name"] for o in kept["outputs"]]
    assert names == ["class"] + ["p%d" % i for i in range(len(classes))]
    assert [o["class"] for o in kept["outputs"][1:]] == [str(c) for c in classes]   # the stored order
    assert kept["outputs"][0]["type"] == "text" and all(o["type"] == "number" for o in kept["outputs"][1:])
    prob_keys = [o["key"] for o in kept["outputs"][1:]]
    for values in kept["values"].values():
        probs = [values[k] for k in prob_keys]
        assert abs(sum(probs) - 1.0) < 1e-5
        assert values[kept["outputs"][0]["key"]] in [str(c) for c in classes]
    # a class's probability is its own numeric column: it sorts and filters as a number
    page = rig.rows(ds, sort={"key": prob_keys[0], "dir": "desc"}, limit=3)
    got = [r["predictions"][prob_keys[0]] for r in page["rows"]]
    assert got == sorted(got, reverse=True)
    label_key = kept["outputs"][0]["key"]
    only = rig.rows(ds, filters=({"key": label_key, "op": "eq", "value": str(classes[0])},))
    assert 0 <= only["total"] <= page["total"]


def test_a_rerun_replaces_its_own_columns_and_nothing_else(rig, made):
    ds = rig.dataset()
    a, b = rig.register(made["run_a"], "a"), rig.register(made["run_b"], "b")
    first, other = rig.run(ds, a), rig.run(ds, b)
    before = rig.datasets.prediction(ds, other["key"])
    again = rig.run(ds, a)
    assert again["key"] == first["key"]
    assert rig.datasets.prediction_keys(ds) == sorted([first["key"], other["key"]])
    assert rig.datasets.prediction(ds, other["key"]) == before


class FailingOutcome(Ligandbench):
    def prediction_outcome(self, *a):
        raise ToolFailed("boom", "ligandbench")


def test_a_failed_rerun_leaves_the_last_good_columns_alone(tmp_path, made):
    good = Rig(tmp_path, made)
    ds, exp = good.dataset(), good.register(made["run_a"], "m")
    key = good.run(ds, exp)["key"]
    before = (good.datasets.root / "ready" / ds / "predictions" / (key + ".json")).read_bytes()
    bad = Predictions(FailingOutcome(), good.registry, good.datasets, tmp_path / "jobs2", timeout=60)
    job = bad.start(ds, exp)
    end = time.time() + 60
    while bad.progress(job["id"])["state"] not in ("done", "error", "cancelled") and time.time() < end:
        time.sleep(0.05)
    assert bad.progress(job["id"])["state"] == "error" and "boom" in bad.progress(job["id"])["error"]
    assert (good.datasets.root / "ready" / ds / "predictions" / (key + ".json")).read_bytes() == before
    assert not list((good.datasets.root / "ready" / ds / "predictions").glob("*.tmp"))
    good.predictions.shutdown(); bad.shutdown()


# ---- what must be refused ----------------------------------------------------------------------------
def test_a_model_trained_with_other_versions_is_refused_before_a_job_exists(tmp_path, made):
    class OtherRdkit(Ligandbench):
        def model_compatibility(self, versions, schema):
            return super().model_compatibility({**(versions or {}), "rdkit": "1999.01.1"}, schema)

    rig = Rig(tmp_path, made, tool=OtherRdkit())
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    with pytest.raises(Incompatible, match="rdkit"):
        rig.predictions.start(ds, exp)
    assert rig.predictions.for_dataset(ds) == [] and rig.datasets.prediction_keys(ds) == []
    listed = {e["id"]: e for e in rig.service.experiments_view(type("Q", (), {"experiment_id": "", "everything": False})())["experiments"]}
    assert listed[exp]["usable"]["ok"] is False and "rdkit" in listed[exp]["usable"]["reason"]


def test_the_listing_says_which_models_are_usable(rig, made):
    exp = rig.register(made["run_a"], "m")
    listed = rig.service.experiments_view(type("Q", (), {"experiment_id": "", "everything": False})())["experiments"]
    assert [e["usable"] for e in listed] == [{"ok": True, "reason": ""}]
    assert listed[0]["id"] == exp


def test_a_damaged_model_fails_before_inference_and_keeps_nothing(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    model = rig.registry.artifacts / exp / "model.json"
    data = model.read_bytes()
    model.write_bytes(bytes([data[0] ^ 1]) + data[1:])      # the same size: only the checksum knows
    done = rig.run(ds, exp)
    assert done["state"] == "error" and "model" in done["error"].lower()
    assert rig.datasets.prediction_keys(ds) == []


def test_a_missing_model_file_is_refused_at_the_start(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    (rig.registry.artifacts / exp / "model.json").unlink()
    with pytest.raises(Conflict, match="damaged"):
        rig.predictions.start(ds, exp)


def test_unknown_datasets_and_experiments_are_not_found(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    with pytest.raises(NotFound):
        rig.predictions.start("ds-000000000000", exp)
    with pytest.raises(NotFound):
        rig.predictions.start(ds, "123e4567-e89b-42d3-a456-426614174000")


# ---- jobs that are held open ---------------------------------------------------------------------------
class Gated(Ligandbench):
    """The real command, started only once ``gate`` exists."""

    gate: Path

    def start_prediction(self, run_dir, dataset_dir, out_dir):
        script = 'while [ ! -e "$G" ]; do sleep 0.05; done; shift; exec "$@"'
        cmd = ["sh", "-c", script, "sh", str(self.gate), *self._cmd("predict", run_dir, dataset_dir, "--out", out_dir)]
        return subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**__import__("os").environ, "G": str(self.gate)}, start_new_session=True,
        )


def gated(tmp_path, made, **limits):
    tool = Gated()
    tool.gate = tmp_path / "go"
    return Rig(tmp_path, made, tool=tool, **limits)


def wait_state(rig, job_id, state, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if rig.predictions.progress(job_id)["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(rig.predictions.progress(job_id))


def test_a_model_cannot_be_deleted_while_a_job_holds_it_and_the_job_still_finishes(tmp_path, made):
    rig = gated(tmp_path, made)
    ds, exp = rig.dataset(), rig.register(made["run_a"], "doomed")
    job = rig.predictions.start(ds, exp)
    wait_state(rig, job["id"], "running")
    time.sleep(0.2)
    report = rig.registry.delete(exp, timeout=0.3)
    assert report["complete"] is False and report["row"] == "retained"       # waits on the lease
    assert (rig.registry.artifacts / exp / "model.json").exists()
    rig.tool.gate.write_text("")
    done = rig.wait(job["id"])
    assert done["state"] == "done"
    assert rig.registry.delete(exp)["complete"] is True                       # and now it can go
    # the predictions stay with the dataset, labelled as from a deleted model
    shown = rig.service.dataset_predictions(type("R", (), {"dataset_id": ds})())
    (kept,) = shown["predictions"]
    assert kept["source_deleted"] is True
    assert all("source model deleted" in o["label"] for o in kept["outputs"])
    assert all("source model deleted" in c["label"] for c in shown["schema"] if c["kind"] == "prediction")
    # and they still sort and filter
    key = kept["outputs"][0]["key"]
    assert rig.rows(ds, sort={"key": key, "dir": "asc"}, limit=3)["total"] > 0
    rig.predictions.shutdown()


def test_a_job_waiting_for_a_turn_for_a_model_that_was_deleted_ends_with_a_message(tmp_path, made):
    rig = gated(tmp_path, made, concurrency=1)
    ds = rig.dataset()
    a, b = rig.register(made["run_a"], "a"), rig.register(made["run_b"], "b")
    first = rig.predictions.start(ds, a)
    wait_state(rig, first["id"], "running")
    second = rig.predictions.start(ds, b)
    assert rig.predictions.progress(second["id"])["state"] == "queued"
    assert rig.registry.delete(b)["complete"] is True                       # nobody holds b yet
    rig.tool.gate.write_text("")
    ended = rig.wait(second["id"])
    assert ended["state"] == "error" and ended["error_code"] == "experiment_unavailable"
    assert "deleted" in ended["error"]
    assert rig.wait(first["id"])["state"] == "done"
    assert rig.datasets.prediction_keys(ds) == [first["key"]]                # only the good one
    rig.predictions.shutdown()


def test_a_job_is_bound_to_the_dataset_it_started_on(tmp_path, made):
    rig = gated(tmp_path, made, concurrency=2)
    one, two = rig.dataset("reg"), rig.dataset("notarget")
    exp = rig.register(made["run_a"], "m")
    job = rig.predictions.start(one, exp)
    other = rig.predictions.start(two, exp)
    rig.tool.gate.write_text("")
    assert rig.wait(job["id"])["dataset_id"] == one
    rig.wait(other["id"])
    assert rig.datasets.prediction_keys(one) and rig.datasets.prediction_keys(two)
    assert rig.predictions.progress(job["id"])["dataset_hash"] == rig.datasets.get(one)["content_hash"]
    assert {j["id"] for j in rig.predictions.for_dataset(one)} == {job["id"]}
    rig.predictions.shutdown()


def test_the_same_model_on_the_same_dataset_is_not_started_twice_and_the_queue_is_bounded(tmp_path, made):
    rig = gated(tmp_path, made, concurrency=1, queue=1)
    ds = rig.dataset()
    a, b, c = (rig.register(made["run_a"], "a"), rig.register(made["run_b"], "b"), None)
    first = rig.predictions.start(ds, a)
    with pytest.raises(Conflict, match="already being applied"):
        rig.predictions.start(ds, a)
    rig.predictions.start(ds, b)
    other = rig.dataset("notarget")
    with pytest.raises(Conflict, match="queue is full"):
        rig.predictions.start(other, a)
    rig.tool.gate.write_text("")
    rig.wait(first["id"])
    rig.predictions.shutdown()


def test_cancelling_a_running_job_writes_nothing(tmp_path, made):
    rig = gated(tmp_path, made)
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    job = rig.predictions.start(ds, exp)
    wait_state(rig, job["id"], "running")
    rig.predictions.cancel(job["id"])
    assert rig.wait(job["id"])["state"] == "cancelled"
    assert rig.datasets.prediction_keys(ds) == []
    assert rig.registry.delete(exp)["complete"] is True      # the lease was let go
    rig.predictions.shutdown()


def test_progress_is_reported_while_it_runs(tmp_path, made):
    rig = gated(tmp_path, made)
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    job = rig.predictions.start(ds, exp)
    p = rig.predictions.progress(job["id"])
    assert p["total"] == len(rig.datasets.rows(ds)) and p["experiment_id"] == exp and p["experiment_name"] == "m"
    rig.tool.gate.write_text("")
    done = rig.wait(job["id"])
    assert done["completed"] == done["total"]
    rig.predictions.shutdown()


# ---- the sets themselves --------------------------------------------------------------------------------
def test_a_set_can_be_removed_and_the_columns_go_with_it(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    key = rig.run(ds, exp)["key"]
    from proteinviewer.schemas import PredictionSetRequest

    rig.service.delete_predictions(PredictionSetRequest(ds, key))
    assert rig.datasets.prediction_keys(ds) == []
    assert not [c for c in rig.service.dataset_status(type("R", (), {"dataset_id": ds})())["schema"] if c["kind"] == "prediction"]
    with pytest.raises(NotFound):
        rig.service.delete_predictions(PredictionSetRequest(ds, key))


def test_a_damaged_set_file_is_no_set(rig, made):
    ds, exp = rig.dataset(), rig.register(made["run_a"], "m")
    key = rig.run(ds, exp)["key"]
    (rig.datasets.root / "ready" / ds / "predictions" / (key + ".json")).write_text("{not json")
    assert rig.datasets.prediction(ds, key) is None
    assert rig.rows(ds)["total"] == len(rig.datasets.rows(ds))       # the grid still loads


def test_the_sets_survive_a_restart(tmp_path, made):
    first = Rig(tmp_path, made)
    ds, exp = first.dataset(), first.register(made["run_a"], "m")
    key = first.run(ds, exp)["key"]
    first.predictions.shutdown()
    reopened = DatasetStore(tmp_path / "datasets")
    assert reopened.prediction_keys(ds) == [key] and reopened.prediction(ds, key)["dataset"]["id"] == ds


def test_build_set_refuses_an_answer_that_is_not_one_output_per_row_in_order():
    from proteinviewer.predictions import build_set

    meta = {"dataset_id": "ds-0123456789ab", "content_hash": "x"}
    exp = {"id": "123e4567-e89b-42d3-a456-426614174000", "name": "m", "checksum": "c" * 64, "target": {"column": "y", "transform": "pki"}}
    rows = [{"row_id": 0}, {"row_id": 1}]
    good = {"target": {"kind": "regression", "classes": []}, "rows": [{"row_id": 0, "value": 1.0}, {"row_id": 1, "error": "bad"}]}
    kept = build_set(key="pred_k", dataset=meta, experiment=exp, result=good, rows=rows)
    assert kept["values"] == {"0": {"pred_k_value": 1.0}} and kept["errors"] == {"1": "bad"}
    swapped = {**good, "rows": list(reversed(good["rows"]))}
    with pytest.raises(ToolFailed, match="in order"):
        build_set(key="k", dataset=meta, experiment=exp, result=swapped, rows=rows)
    short = {**good, "rows": good["rows"][:1]}
    with pytest.raises(ToolFailed):
        build_set(key="k", dataset=meta, experiment=exp, result=short, rows=rows)
