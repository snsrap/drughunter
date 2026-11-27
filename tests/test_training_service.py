"""Training through the service and its routes: the requests, the dataset checks
that refuse before any process starts, the four endpoints and their statuses.
ligandbench's training is the stand-in; the datasets are directories laid out as a
ready dataset is, so none of this needs RDKit.
"""

import json
from pathlib import Path

import pytest

from proteinviewer.dataset import DatasetStore
from proteinviewer.errors import BadRequest
from proteinviewer.schemas import TrainingQuery, TrainingStartRequest
from proteinviewer.service import ViewerService
from proteinviewer.training import Training
from proteinviewer.web import Api, ApiRequest

from .training_helpers import release, standin_tool, wait_for, wait_state

READY = "ds-0123456789ab"
NO_TARGET = "ds-0123456789ac"
STAGED = "ds-0123456789ad"


def ready_dataset(root: Path, dataset_id: str, *, trainable: bool, **behavior) -> Path:
    """A ready dataset on disk as DatasetStore keeps it, and how the stand-in is to behave."""
    path = root / "ready" / dataset_id
    path.mkdir(parents=True)
    (path / "dataset.json").write_text(
        json.dumps(
            {"dataset_id": dataset_id, "state": "ready", "name": "d", "trainable": trainable}
        )
    )
    (path / "rows.json").write_text('{"rows": []}')
    (path / "source.csv").write_text("a\n")
    (path / "behavior.json").write_text(json.dumps(behavior))
    return path


class Described:
    """Every other tool, as far as ``status`` asks anything of it."""

    def __getattr__(self, name):
        return lambda *a, **k: {} if name != "additives" else []


class Rig:
    def __init__(self, tmp_path, *, info=None, working=True, **limits):
        (tmp_path / "bin").mkdir()
        self.tool = standin_tool(tmp_path / "bin", info, working=working)
        self.store = DatasetStore(tmp_path / "datasets")
        self.training = Training(self.tool, tmp_path / "work" / "training", **limits)
        self.service = ViewerService(
            None,
            Described(),
            Described(),
            Described(),
            ligands=self.tool,
            datasets=self.store,
            training=self.training,
        )
        self.api = Api(self.service)
        self.dir = ready_dataset(self.store.root, READY, trainable=True)
        ready_dataset(self.store.root, NO_TARGET, trainable=False)
        self.store.stage("up.csv", "a\n1\n")  # a staged one, under an id of its own
        self.staged = next((self.store.root / "staged").iterdir()).name

    def call(self, method, path, body=None):
        query = {}
        if method == "GET" and body:
            query, body = body, None
        return self.api.handle(method, path, ApiRequest(query, body or {}))

    def start(self, **body):
        return self.call("POST", "/api/training", {"dataset_id": READY, "profile": "tiny", **body})


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.training.shutdown(wait=5)


# -- the request ----------------------------------------------------------------------


def test_a_start_request_has_defaults():
    r = TrainingStartRequest.parse({"dataset_id": "ds-1"})
    assert (r.dataset_id, r.seed, r.production_strategy, r.profile) == (
        "ds-1",
        0,
        "scaffold",
        "full",
    )
    r = TrainingStartRequest.parse(
        {"dataset_id": "ds-1", "seed": "7", "production_strategy": "Random", "profile": "TINY"}
    )
    assert (r.seed, r.production_strategy, r.profile) == (7, "random", "tiny")
    assert TrainingStartRequest.parse({"dataset_id": "d", "seed": 2**32 - 1}).seed == 2**32 - 1


@pytest.mark.parametrize(
    "body, message",
    [
        ({}, "name the dataset"),
        ({"dataset_id": 5}, "dataset_id must be a string"),
        ({"dataset_id": "d", "seed": -1}, "at least 0"),
        ({"dataset_id": "d", "seed": 1.5}, "whole number"),
        ({"dataset_id": "d", "seed": "x"}, "must be a number"),
        ({"dataset_id": "d", "seed": True}, "must be a number"),
        ({"dataset_id": "d", "seed": 2**32}, "at most 4294967295"),
        ({"dataset_id": "d", "seed": 10**400}, "finite number"),
        ({"dataset_id": "d", "seed": float("inf")}, "finite number"),
        ({"dataset_id": "d", "seed": "1e400"}, "finite number"),
        ({"dataset_id": "d", "production_strategy": "best"}, "random or scaffold"),
        ({"dataset_id": "d", "profile": "huge"}, "full or tiny"),
    ],
)
def test_a_bad_start_request_is_a_bad_request(body, message):
    with pytest.raises(BadRequest, match=message):
        TrainingStartRequest.parse(body)


def test_a_query_names_a_run():
    assert TrainingQuery.parse({"id": "run-1"}).run_id == "run-1"
    assert TrainingQuery.parse({"run_id": "run-2"}).run_id == "run-2"
    with pytest.raises(BadRequest):
        TrainingQuery.parse({})


def test_a_huge_number_is_a_bad_request_not_a_crash():
    # validate.integer used to raise OverflowError for these: a 500.
    from proteinviewer import validate

    for huge in (10**400, 10**5000, -(10**400), "9" * 400):
        with pytest.raises(BadRequest, match="finite number"):
            validate.integer({"n": huge}, "n", 0)
        with pytest.raises(BadRequest, match="finite number"):
            validate.number({"n": huge}, "n", 0)
    assert validate.integer({"n": 10**15}, "n", 0) == 10**15


# -- starting -------------------------------------------------------------------------


def test_starting_a_run_answers_with_its_progress(rig):
    code, body = rig.start(seed=3, production_strategy="random")
    assert code == 200
    assert body["id"].startswith("run-") and body["state"] in ("queued", "running")
    assert (body["dataset_id"], body["seed"], body["production_strategy"]) == (READY, 3, "random")
    wait_state(rig.training, body["id"], "done")
    # The child was given the dataset's own directory.
    assert json.loads((rig.dir / "argv.json").read_text())["argv"][3] == str(rig.dir)


def test_the_defaults_are_seed_zero_scaffold_full(rig):
    code, body = rig.call("POST", "/api/training", {"dataset_id": READY})
    assert code == 200
    assert (body["seed"], body["production_strategy"], body["profile"]) == (0, "scaffold", "full")
    wait_state(rig.training, body["id"], "done")


def test_an_unknown_dataset_is_404(rig):
    code, body = rig.call("POST", "/api/training", {"dataset_id": "ds-ffffffffffff"})
    assert code == 404 and "dataset" in body["error"]
    assert rig.call("POST", "/api/training", {"dataset_id": "nonsense"})[0] == 404


def test_a_staged_dataset_is_409_and_starts_nothing(rig):
    code, body = rig.call("POST", "/api/training", {"dataset_id": rig.staged})
    assert code == 409 and "resolve" in body["error"]
    assert not (rig.training.root).exists()


def test_a_dataset_without_a_target_is_400_before_any_process_starts(rig):
    code, body = rig.call("POST", "/api/training", {"dataset_id": NO_TARGET})
    assert code == 400 and "no target" in body["error"]
    assert not (rig.training.root).exists()
    assert not (rig.store.root / "ready" / NO_TARGET / "argv.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"seed": -1},
        {"seed": 2.5},
        {"seed": 10**400},
        {"seed": 2**32},
        {"production_strategy": "best"},
        {"profile": "xl"},
        {"dataset_id": ""},
    ],
)
def test_a_bad_request_is_400(rig, body):
    code, out = rig.call("POST", "/api/training", {"dataset_id": READY, **body})
    assert code == 400, out
    assert not rig.training.root.exists()


def test_a_full_queue_is_409(tmp_path):
    rig = Rig(tmp_path, concurrency=1, queue=0)
    try:
        rig.dir.joinpath("behavior.json").write_text(json.dumps({"gate": True}))
        code, first = rig.start()
        assert code == 200
        code, body = rig.start()
        assert code == 409 and "queue is full" in body["error"]
        release(rig.dir)
        wait_state(rig.training, first["id"], "done")
    finally:
        rig.training.shutdown(wait=5)


def test_ligandbench_or_xgboost_missing_is_503(tmp_path):
    for sub, kw in (("a", {"info": {"xgboost": ""}}), ("b", {"working": False})):
        (tmp_path / sub).mkdir()
        rig = Rig(tmp_path / sub, **kw)
        code, body = rig.start()
        assert code == 503, kw
        assert "training is not available" in body["error"]
        assert not rig.training.root.exists()


def test_a_server_without_training_says_so(tmp_path):
    rig = Rig(tmp_path)
    rig.service.training = None
    assert rig.start()[0] == 503
    assert rig.call("GET", "/api/training", {"id": "run-1"})[0] == 503
    assert rig.service.status()["training"] == {"available": False}


# -- polling, the result, cancelling ----------------------------------------------------


def test_progress_and_run_by_id(rig):
    rig.dir.joinpath("behavior.json").write_text(json.dumps({"gate": True}))
    run_id = rig.start()[1]["id"]
    code, body = wait_for(
        lambda: (
            (r := rig.call("GET", "/api/training", {"id": run_id}))[1].get("stage")
            == "tuning candidates"
            and r
        ),
        what="the stage",
    )
    assert code == 200
    assert (body["state"], body["completed"], body["total"]) == ("running", 3, 10)
    assert isinstance(body["elapsed"], float)
    # Not completed: the run is not there yet.
    code, out = rig.call("GET", "/api/training/run", {"id": run_id})
    assert code == 409 and "not completed" in out["error"]

    release(rig.dir)
    wait_state(rig.training, run_id, "done")
    code, run = rig.call("GET", "/api/training/run", {"id": run_id})
    assert code == 200 and run["schema"] == "qsar-run-v1" and run["status"] == "completed"
    assert set(run["files"]) >= {"model.json", "features.json"}


def test_unknown_and_missing_ids(rig):
    for method, path in (
        ("GET", "/api/training"),
        ("GET", "/api/training/run"),
    ):
        assert rig.call(method, path, {"id": "run-nope"})[0] == 404, path
        assert rig.call(method, path)[0] == 400, path
    assert rig.call("POST", "/api/training/cancel", {"id": "run-nope"})[0] == 404
    assert rig.call("POST", "/api/training/cancel", {})[0] == 400


def test_a_failed_run_is_reported_and_has_no_result(rig):
    rig.dir.joinpath("behavior.json").write_text(json.dumps({"mode": "bad_input"}))
    run_id = rig.start()[1]["id"]
    wait_state(rig.training, run_id, "error")
    code, body = rig.call("GET", "/api/training", {"id": run_id})
    assert code == 200 and body["state"] == "error" and "one class" in body["error"]
    assert rig.call("GET", "/api/training/run", {"id": run_id})[0] == 409


def test_an_unavailable_production_strategy_is_an_error_with_its_reason(rig):
    rig.dir.joinpath("behavior.json").write_text(json.dumps({"mode": "unavailable"}))
    run_id = rig.start()[1]["id"]
    wait_state(rig.training, run_id, "error")
    _, body = rig.call("GET", "/api/training", {"id": run_id})
    assert body["state"] == "error"
    assert body["error_code"] == "production_strategy_unavailable"
    assert body["unavailable"]["reason"] == "too few scaffolds"
    assert "another production strategy" in body["error"]


def test_cancelling_over_the_route(rig):
    rig.dir.joinpath("behavior.json").write_text(json.dumps({"mode": "hang"}))
    run_id = rig.start()[1]["id"]
    wait_for(lambda: (rig.dir / "grandchild.pid").exists(), what="the child to start")
    code, body = rig.call("POST", "/api/training/cancel", {"id": run_id})
    assert code == 200 and body["id"] == run_id
    assert wait_state(rig.training, run_id, "cancelled")["state"] == "cancelled"


def test_status_reports_training(rig):
    st = rig.service.status()["training"]
    assert st["available"] is True and st["reason"] == ""
    assert (st["running"], st["queued"]) == (0, 0)
    assert rig.service.status()["datasets"]["version"] == "0.0-standin"


def test_the_service_hands_the_registry_to_registration(rig):
    run_id = rig.start()[1]["id"]
    wait_state(rig.training, run_id, "done")
    with rig.service.training.pin(run_id) as path:
        assert (path / "run.json").is_file()
