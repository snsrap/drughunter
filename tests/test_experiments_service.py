"""Registering and managing experiments through the service and its routes, over a
real socket: the pin on the training run, the statuses, the records that leave
the server, a registry that cannot open, and a restart on the same root.

The training job is the stand-in (``tests/training_standin.py``) behind the real
``Training``; once a job is done its directory is swapped for a run built the
way ``ligandbench train`` lays one out (``tests/experiments_helpers.py``), since
the stand-in does not write the whole contract.
"""

import functools
import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from proteinviewer import server
from proteinviewer.config import Settings
from proteinviewer.dataset import DatasetStore
from proteinviewer.errors import NotFound
from proteinviewer.experiments import ExperimentStore
from proteinviewer.service import ViewerService
from proteinviewer.training import Training
from proteinviewer.web import Api, ApiRequest, Handler

from .experiments_helpers import make_run
from .training_helpers import dataset_dir, standin_tool, wait_for, wait_state


class Described:
    """Every other tool, as far as ``status`` asks anything of it."""

    def __getattr__(self, name):
        return lambda *a, **k: {} if name != "additives" else []


class Rig:
    """A service over a real stand-in training registry and a real store, served
    on a socket of its own. ``restart()`` builds a new store, service and server
    on the same directories, as a restarted process would.
    """

    def __init__(self, tmp_path, *, limit=32, lease_timeout=30.0, store=True, error=""):
        self.tmp = tmp_path
        (tmp_path / "bin").mkdir()
        self.tool = standin_tool(tmp_path / "bin")
        self.limit = limit
        self.lease_timeout = lease_timeout
        self.registry = tmp_path / "registry"
        self.training = Training(self.tool, tmp_path / "work" / "training", limit=limit)
        self.n = 0
        self.httpd = None
        self.start(store=store, error=error)

    def start(self, *, store=True, error=""):
        self.store = (
            ExperimentStore.open(self.registry, lease_timeout=self.lease_timeout) if store else None
        )
        self.service = ViewerService(
            None,
            Described(),
            Described(),
            Described(),
            ligands=self.tool,
            datasets=DatasetStore(self.tmp / "datasets"),
            training=self.training,
            experiments=self.store,
            experiments_error=error,
        )
        app = self.tmp / "app"
        app.mkdir(exist_ok=True)
        handler = functools.partial(
            Handler, directory=str(app), api=Api(self.service), max_body=100_000
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def restart(self, **kw):
        self.stop_http()
        self.start(**kw)

    def stop_http(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def close(self):
        self.stop_http()
        self.training.shutdown(wait=5)

    # -- HTTP
    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def api(self, method, path, body=None):
        code, raw = self.call(method, path, body)
        return code, json.loads(raw)

    def register(self, run_id, name="A model"):
        return self.api("POST", "/api/experiments", {"run_id": run_id, "name": name})

    # -- training runs
    def finish_run(self, behavior=None) -> str:
        """A completed run: a stand-in job that is done, its directory then holding a full run."""
        self.n += 1
        data = dataset_dir(self.tmp / "ds", "ds-%012d" % self.n, **(behavior or {}))
        run_id = self.training.start(data.name, data, self.n, "random", "tiny")["id"]
        wait_state(self.training, run_id, "done", "error", "cancelled")
        if self.training.progress(run_id)["state"] == "done":
            job_dir = self.training.root / run_id
            shutil.rmtree(job_dir)
            shutil.copytree(make_run(self.tmp / "built", run_id), job_dir)
        return run_id

    def held_run(self):
        """A run that is still running, held by the stand-in until ``release``."""
        self.n += 1
        data = dataset_dir(self.tmp / "ds", "ds-%012d" % self.n, gate=True)
        run_id = self.training.start(data.name, data, 1, "random", "tiny")["id"]
        wait_for(lambda: self.training.progress(run_id)["completed"] >= 3, what="the gate")
        return run_id, data


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def absolute_paths(rig, text):
    """Any of the server's own directories named in a response."""
    roots = [rig.tmp, rig.registry, rig.training.root, rig.tmp / "built"]
    return [str(p) for p in roots if str(p) in text]


# -- registering ------------------------------------------------------------------------


def test_a_completed_run_registers_and_both_strategies_come_back(rig):
    run_id = rig.finish_run()
    code, record = rig.register(run_id, "  First model  ")
    assert code == 200
    assert (record["state"], record["name"], record["source_run_id"]) == (
        "ready",
        "First model",
        run_id,
    )
    assert record["artifact_path"] == "artifacts/" + record["id"]
    assert record["production_strategy"] == "scaffold"
    assert set(record["metrics"]) == {"random", "scaffold"}
    assert set(record["run"]["strategies"]) == {"random", "scaffold"}
    assert (rig.registry / "artifacts" / record["id"] / "model.json").is_file()
    code, fetched = rig.api("GET", "/api/experiments?id=" + record["id"])
    assert (code, fetched) == (200, record)


def test_registering_twice_returns_the_one_experiment(rig):
    run_id = rig.finish_run()
    _, first = rig.register(run_id, "one")
    code, second = rig.register(run_id, "two")
    assert (code, second) == (200, first), "a repeat is a 200 with the same record, not a rename"
    assert len(rig.api("GET", "/api/experiments")[1]["experiments"]) == 1


def test_the_pin_is_held_while_the_run_is_copied_and_released_after(tmp_path):
    rig = Rig(tmp_path, limit=1)
    try:
        run_id = rig.finish_run()
        seen = {}
        real = rig.store._checkpoint

        def during(stage):
            real(stage)
            if stage == "copy" and "pinned" not in seen:
                # Another job crowds the limit: an unpinned finished run would be evicted.
                rig.n += 1
                data = dataset_dir(rig.tmp / "ds", "ds-%012d" % rig.n)
                seen["other"] = rig.training.start(data.name, data, 1, "random", "tiny")["id"]
                seen["pinned"] = (rig.training.root / run_id / "run.json").is_file()
                seen["known"] = rig.training.progress(run_id)["state"]

        rig.store._checkpoint = during
        assert rig.register(run_id)[0] == 200
        assert seen["pinned"] and seen["known"] == "done"
        wait_state(rig.training, seen["other"], "done")
        # Released: the next job in finds nothing holding it, and the run goes.
        rig.n += 1
        data = dataset_dir(rig.tmp / "ds", "ds-%012d" % rig.n)
        rig.training.start(data.name, data, 1, "random", "tiny")
        with pytest.raises(NotFound):
            rig.training.progress(run_id)
    finally:
        rig.close()


def test_a_failed_copy_releases_the_pin_and_leaves_the_run_registrable(rig, monkeypatch):
    run_id = rig.finish_run()

    def boom(stage):
        if stage == "copy":
            raise OSError("disk full")

    monkeypatch.setattr(rig.store, "_checkpoint", boom)
    code, body = rig.register(run_id)
    assert code == 500
    assert "disk full" in body["error"] and "was not registered" in body["error"]
    assert "Traceback" not in json.dumps(body)
    assert absolute_paths(rig, json.dumps(body)) == []
    with rig.training.pin(run_id) as run_dir:  # not left pinned, and not lost
        assert (run_dir / "run.json").is_file()
    assert rig.api("GET", "/api/experiments")[1] == {"experiments": []}
    monkeypatch.undo()
    assert rig.register(run_id)[0] == 200, "nothing was kept, so it registers again"


def test_the_pin_is_counted_so_a_second_holder_keeps_the_run(tmp_path):
    rig = Rig(tmp_path, limit=1)
    try:
        run_id = rig.finish_run()
        with rig.training.pin(run_id):
            assert rig.register(run_id)[0] == 200
            rig.n += 1
            data = dataset_dir(rig.tmp / "ds", "ds-%012d" % rig.n)
            rig.training.start(data.name, data, 1, "random", "tiny")
            assert rig.training.progress(run_id)["state"] == "done", "the outer pin still holds"
    finally:
        rig.close()


def test_runs_that_are_not_completed_cannot_be_registered(rig):
    held, data = rig.held_run()
    code, body = rig.register(held)
    assert code == 409 and "not completed" in body["error"]
    rig.training.cancel(held)
    wait_state(rig.training, held, "cancelled")
    assert rig.register(held)[0] == 409

    failed = rig.finish_run({"mode": "fail"})
    assert rig.training.progress(failed)["state"] == "error"
    code, body = rig.register(failed)
    assert code == 409 and "not completed" in body["error"]
    assert rig.api("GET", "/api/experiments?all=true")[1] == {"experiments": []}


def test_an_unknown_run_is_a_404(rig):
    code, body = rig.register("run-0123456789ab")
    assert code == 404 and "no such training run" in body["error"]


def test_an_evicted_run_is_a_404_until_it_was_registered_and_then_it_is_the_same_record(tmp_path):
    rig = Rig(tmp_path, limit=1)
    try:
        lost = rig.finish_run()
        kept = rig.finish_run()  # the second start pruned the first
        assert rig.register(lost)[0] == 404
        _, record = rig.register(kept)
        rig.n += 1
        data = dataset_dir(rig.tmp / "ds", "ds-%012d" % rig.n)
        rig.training.start(data.name, data, 1, "random", "tiny")
        with pytest.raises(NotFound):
            rig.training.progress(kept)
        code, again = rig.register(kept)
        assert (code, again) == (200, record), "a retry after eviction finds what it registered"
    finally:
        rig.close()


def test_a_run_whose_files_do_not_match_is_refused_as_not_registrable(rig):
    run_id = rig.finish_run()
    (rig.training.root / run_id / "model.json").write_bytes(b"tampered")
    code, body = rig.register(run_id)
    assert code == 409 and "does not match run.json" in body["error"]
    assert absolute_paths(rig, body["error"]) == []
    assert rig.api("GET", "/api/experiments?all=true")[1] == {"experiments": []}


@pytest.mark.parametrize(
    "body, message",
    [
        ({"name": "x"}, "name the training run"),
        ({"run_id": "../etc", "name": "x"}, "not a training run id"),
        ({"run_id": "run-1"}, "must be some text"),
        ({"run_id": "run-1", "name": "   "}, "must be some text"),
        ({"run_id": "run-1", "name": "a\nb"}, "one line"),
        ({"run_id": "run-1", "name": "a\x00b"}, "one line"),
        ({"run_id": "run-1", "name": "x" * 201}, "at most 200"),
        ({"run_id": "run-1", "name": 7}, "must be a string"),
    ],
)
def test_a_bad_registration_request_is_a_400(rig, body, message):
    code, answer = rig.api("POST", "/api/experiments", body)
    assert code == 400 and message in answer["error"]


# -- reading, editing, deleting -----------------------------------------------------------


def test_list_get_rename_annotate_and_delete_work_across_a_restart(rig):
    a, b = rig.finish_run(), rig.finish_run()
    ida = rig.register(a, "alpha")[1]["id"]
    idb = rig.register(b, "beta")[1]["id"]

    rig.restart()  # a new store, service and server on the same directories
    listing = rig.api("GET", "/api/experiments")[1]["experiments"]
    assert {e["id"] for e in listing} == {idb, ida}  # (the order is the store's, tested there)
    assert all(e["selectable"] and "run" not in e for e in listing)
    assert rig.api("GET", "/api/experiments?id=" + ida)[1]["name"] == "alpha"

    code, updated = rig.api("POST", "/api/experiments/update", {"id": ida, "name": "renamed"})
    assert (code, updated["name"]) == (200, "renamed")
    code, updated = rig.api(
        "POST", "/api/experiments/update", {"id": ida, "annotations": {"note": "good one"}}
    )
    assert (updated["name"], updated["annotations"]) == ("renamed", {"note": "good one"})
    code, updated = rig.api(
        "POST", "/api/experiments/update", {"id": ida, "name": "both", "annotations": {}}
    )
    assert (updated["name"], updated["annotations"]) == ("both", {})

    rig.restart()
    assert rig.api("GET", "/api/experiments?id=" + ida)[1]["name"] == "both"
    code, report = rig.api("POST", "/api/experiments/delete", {"id": ida})
    assert code == 200 and report["complete"] is True
    assert (report["row"], report["artifact_dir"]) == ("removed", "removed")
    assert rig.api("GET", "/api/experiments?id=" + ida)[0] == 404
    assert [e["id"] for e in rig.api("GET", "/api/experiments")[1]["experiments"]] == [idb]
    assert not (rig.registry / "artifacts" / ida).exists()
    assert rig.api("POST", "/api/experiments/delete", {"id": ida})[0] == 404


def test_a_record_does_not_depend_on_the_training_job_directory(rig):
    run_id = rig.finish_run()
    experiment = rig.register(run_id)[1]
    shutil.rmtree(rig.training.root / run_id)
    code, fetched = rig.api("GET", "/api/experiments?id=" + experiment["id"])
    assert (code, fetched) == (200, experiment)
    assert (rig.registry / "artifacts" / experiment["id"] / "dataset" / "source.csv").is_file()


def test_the_facts_of_a_run_cannot_be_edited(rig):
    experiment = rig.register(rig.finish_run())[1]
    for field, value in (
        ("model_family", "random-forest"),
        ("target", {"column": "x"}),
        ("splits", {}),
        ("metrics", {}),
        ("production_strategy", "random"),
        ("dataset_hash", "0" * 64),
    ):
        code, body = rig.api(
            "POST", "/api/experiments/update", {"id": experiment["id"], field: value}
        )
        assert code == 400 and "cannot be changed" in body["error"], field
    code, body = rig.api(
        "POST", "/api/experiments/update", {"id": experiment["id"], "name": "ok", "task": "x"}
    )
    assert code == 400, "a name beside a fact is refused whole, nothing is half applied"
    assert rig.api("GET", "/api/experiments?id=" + experiment["id"])[1] == experiment


@pytest.mark.parametrize(
    "body, message",
    [
        ({"name": "x"}, "name the experiment"),
        ({"id": "nope", "name": "x"}, "is a uuid"),
        ({"id": str(uuid.uuid4())}, "say what to change"),
        ({"id": str(uuid.uuid4()), "colour": "red"}, "unknown field"),
        ({"id": str(uuid.uuid4()), "name": "  "}, "must be some text"),
        ({"id": str(uuid.uuid4()), "name": "a\tb"}, "one line"),
        ({"id": str(uuid.uuid4()), "annotations": "text"}, "must be an object"),
        ({"id": str(uuid.uuid4()), "annotations": {"k": 5}}, "text keys with text values"),
        ({"id": str(uuid.uuid4()), "annotations": {"": "v"}}, "annotation key"),
        ({"id": str(uuid.uuid4()), "annotations": {"k": "v" * 2001}}, "annotation key"),
        ({"id": str(uuid.uuid4()), "annotations": {str(i): "v" for i in range(33)}}, "at most 32"),
    ],
)
def test_a_bad_update_is_a_400(rig, body, message):
    code, answer = rig.api("POST", "/api/experiments/update", body)
    assert code == 400 and message in answer["error"]


def test_an_unknown_experiment_is_a_404_whatever_you_ask_of_it(rig):
    missing = str(uuid.uuid4())
    assert rig.api("GET", "/api/experiments?id=" + missing)[0] == 404
    assert rig.api("POST", "/api/experiments/update", {"id": missing, "name": "x"})[0] == 404
    assert rig.api("POST", "/api/experiments/delete", {"id": missing})[0] == 404
    assert rig.api("GET", "/api/experiments?id=not-a-uuid")[0] == 400
    assert rig.api("POST", "/api/experiments/delete", {})[0] == 400


def test_deleting_while_a_lease_is_held_is_a_409_and_the_record_stays_deleting(tmp_path):
    rig = Rig(tmp_path, lease_timeout=0.2)
    try:
        eid = rig.register(rig.finish_run())[1]["id"]
        with rig.store.lease(eid):
            code, body = rig.api("POST", "/api/experiments/delete", {"id": eid})
            assert code == 409
            assert "in use" in body["error"]
            report = body["report"]
            assert (report["complete"], report["state"], report["row"]) == (
                False,
                "deleting",
                "retained",
            )
            assert report["leases"] == 1
            code, record = rig.api("GET", "/api/experiments?id=" + eid)
            assert (code, record["state"]) == (200, "deleting")
            assert (rig.registry / "artifacts" / eid).is_dir()
            assert rig.api("GET", "/api/experiments")[1] == {"experiments": []}, "not selectable"
            code, body = rig.api("POST", "/api/experiments/update", {"id": eid, "name": "x"})
            assert code == 409, "a deleting experiment is not editable"
        code, report = rig.api("POST", "/api/experiments/delete", {"id": eid})
        assert (code, report["complete"]) == (200, True)
        assert rig.api("GET", "/api/experiments?id=" + eid)[0] == 404
    finally:
        rig.close()


def test_a_deletion_that_cannot_remove_files_is_a_500_with_the_parts_apart(rig, monkeypatch):
    eid = rig.register(rig.finish_run())[1]["id"]
    real = rig.store._checkpoint

    def boom(stage):
        real(stage)
        if stage == "delete_files":
            raise OSError("read-only file system")

    monkeypatch.setattr(rig.store, "_checkpoint", boom)
    code, body = rig.api("POST", "/api/experiments/delete", {"id": eid})
    assert code == 500
    report = body["report"]
    assert (report["artifact_dir"], report["row"], report["complete"]) == (
        "failed",
        "retained",
        False,
    )
    assert "read-only" in json.dumps(report)
    assert absolute_paths(rig, json.dumps(body)) == []
    assert rig.api("GET", "/api/experiments?id=" + eid)[1]["state"] == "deleting"
    monkeypatch.undo()
    assert rig.api("POST", "/api/experiments/delete", {"id": eid})[1]["complete"] is True


# -- health and recovery ---------------------------------------------------------------------


def test_health_reports_a_deleted_artifact_directory_and_an_orphan(rig):
    kept = rig.register(rig.finish_run(), "kept")[1]["id"]
    lost = rig.register(rig.finish_run(), "lost")[1]["id"]
    code, health = rig.api("GET", "/api/experiments/health")
    assert (code, health["ok"]) == (200, True)

    shutil.rmtree(rig.registry / "artifacts" / lost)
    orphan = str(uuid.uuid4())
    (rig.registry / "artifacts" / orphan).mkdir()
    (rig.registry / "staging" / "leftover").mkdir()
    code, health = rig.api("GET", "/api/experiments/health")
    assert code == 200 and health["ok"] is False
    assert [m["id"] for m in health["missing"]] == [lost]
    assert health["orphans"]["artifacts"] == [{"name": orphan, "kind": "directory"}]
    assert health["orphans"]["staging"] == [{"name": "leftover", "kind": "directory"}]
    assert absolute_paths(rig, json.dumps(health)) == []
    # Only the healthy one is selectable; the damaged one is still listed on request.
    assert [e["id"] for e in rig.api("GET", "/api/experiments")[1]["experiments"]] == [kept]
    everything = rig.api("GET", "/api/experiments?all=true")[1]["experiments"]
    assert {e["id"]: e["selectable"] for e in everything} == {kept: True, lost: False}
    # Orphans are reported, never removed.
    assert (rig.registry / "artifacts" / orphan).is_dir()


def test_health_catches_a_corrupted_file_unless_asked_for_the_shallow_check(rig):
    eid = rig.register(rig.finish_run())[1]["id"]
    path = rig.registry / "artifacts" / eid / "model.json"
    path.write_bytes(b"x" * path.stat().st_size)  # same size, other bytes
    assert [c["id"] for c in rig.api("GET", "/api/experiments/health")[1]["corrupt"]] == [eid]
    assert rig.api("GET", "/api/experiments/health?deep=false")[1]["corrupt"] == []
    assert rig.api("GET", "/api/experiments/health?deep=maybe")[0] == 400


def test_recover_finishes_a_verified_failed_registration_and_nothing_else(rig, monkeypatch):
    run_id = rig.finish_run()

    def boom(stage):
        if stage == "mark_ready":
            raise OSError("commit failed")

    monkeypatch.setattr(rig.store, "_checkpoint", boom)
    code, body = rig.register(run_id)
    assert code == 500 and "recover" in body["error"]
    monkeypatch.undo()
    (rig.registry / "artifacts" / str(uuid.uuid4())).mkdir()
    assert rig.api("GET", "/api/experiments")[1] == {"experiments": []}
    assert rig.api("GET", "/api/experiments/health")[1]["counts"]["failed"] == 1

    assert rig.api("GET", "/api/experiments/recover")[0] == 404, "recovery is a POST"
    code, report = rig.api("POST", "/api/experiments/recover", {})
    assert code == 200 and len(report["completed"]) == 1 and report["retained"] == []
    assert len(rig.api("GET", "/api/experiments")[1]["experiments"]) == 1
    assert len(list((rig.registry / "artifacts").iterdir())) == 2, "the orphan is kept"


# -- a store that did not open, one that breaks, and what leaves the server ---------------------


def test_without_a_registry_the_experiment_routes_say_503_and_everything_else_works(tmp_path):
    rig = Rig(tmp_path, store=False, error="the registry database is schema version 99")
    try:
        for method, path, body in (
            ("GET", "/api/experiments", None),
            ("GET", "/api/experiments?id=" + str(uuid.uuid4()), None),
            ("POST", "/api/experiments", {"run_id": "run-1", "name": "x"}),
            ("POST", "/api/experiments/update", {"id": str(uuid.uuid4()), "name": "x"}),
            ("POST", "/api/experiments/delete", {"id": str(uuid.uuid4())}),
            ("GET", "/api/experiments/health", None),
            ("POST", "/api/experiments/recover", {}),
        ):
            code, answer = rig.api(method, path, body)
            assert code == 503, (method, path)
            assert "schema version 99" in answer["error"]
        code, status = rig.api("GET", "/api/status")
        assert code == 200
        assert status["experiments"]["available"] is False
        assert "schema version 99" in status["experiments"]["reason"]
        assert status["training"]["available"] is True
        run_id = rig.finish_run()
        assert rig.api("GET", "/api/training?id=" + run_id)[0] == 200
    finally:
        rig.close()


def test_the_server_opens_the_registry_and_survives_one_it_cannot_open(tmp_path):
    root = tmp_path / "newer"
    root.mkdir()
    with sqlite3.connect(root / "registry.sqlite3") as conn:
        conn.execute("PRAGMA user_version = 99")
    store, reason = server.open_experiments(Settings(experiment_root=root))
    assert store is None and "schema version 99" in reason
    assert not (root / "artifacts").exists() or list((root / "artifacts").iterdir()) == []

    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    store, reason = server.open_experiments(Settings(experiment_root=blocked / "registry"))
    assert store is None and reason

    store, reason = server.open_experiments(Settings(experiment_root=tmp_path / "fine"))
    assert reason == "" and store.root == tmp_path / "fine"
    assert (tmp_path / "fine" / "registry.sqlite3").is_file()


def test_the_default_root_is_the_durable_one_and_never_the_work_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "work"
    service = server.build_service(Settings(), work)
    assert service.experiments.root == tmp_path / "xdg" / "proteinviewer" / "experiments"
    assert work not in service.experiments.root.parents


def test_the_startup_line_counts_what_needs_attention_and_deletes_nothing(tmp_path):
    root = tmp_path / "registry"
    store = ExperimentStore.open(root)
    (root / "artifacts" / str(uuid.uuid4())).mkdir()
    (root / "staging" / "half").mkdir()
    store = ExperimentStore.open(root)
    line = server.experiments_status_line(store, "")
    assert str(root) in line
    assert "2 orphan" in line and "0 pending" in line and "0 deleting" in line
    assert (
        len(list((root / "artifacts").iterdir())) == 1
        and len(list((root / "staging").iterdir())) == 1
    )
    assert "NOT AVAILABLE" in server.experiments_status_line(None, "no room")
    assert "no room" in server.experiments_status_line(None, "no room")


def test_status_summarises_the_registry(rig):
    status = rig.api("GET", "/api/status")[1]["experiments"]
    assert status["available"] is True and status["healthy"] is True
    assert status["counts"] == {"pending": 0, "ready": 0, "deleting": 0, "failed": 0}
    eid = rig.register(rig.finish_run())[1]["id"]
    status = rig.api("GET", "/api/status")[1]["experiments"]
    assert (status["counts"]["ready"], status["selectable"]) == (1, 1)
    shutil.rmtree(rig.registry / "artifacts" / eid)
    status = rig.api("GET", "/api/status")[1]["experiments"]
    assert status["healthy"] is False and status["problems"] == 1


def test_a_registry_that_breaks_underneath_is_a_500_with_a_message_not_a_trace(rig):
    eid = rig.register(rig.finish_run())[1]["id"]
    rig.store.database.write_bytes(b"this is not a database" * 100)
    for method, path, body in (
        ("GET", "/api/experiments", None),
        ("GET", "/api/experiments?id=" + eid, None),
        ("POST", "/api/experiments/update", {"id": eid, "name": "x"}),
        ("GET", "/api/experiments/health", None),
    ):
        code, answer = rig.api(method, path, body)
        assert code == 500, (method, path)
        assert "registry" in answer["error"] and "Traceback" not in answer["error"]
        assert absolute_paths(rig, answer["error"]) == []
    assert rig.api("GET", "/api/status")[0] == 200


def test_no_response_names_a_directory_of_the_server(rig):
    run_id = rig.finish_run()
    eid = rig.register(run_id)[1]["id"]
    seen = [
        rig.call("POST", "/api/experiments", {"run_id": run_id, "name": "again"}),
        rig.call("GET", "/api/experiments"),
        rig.call("GET", "/api/experiments?all=true"),
        rig.call("GET", "/api/experiments?id=" + eid),
        rig.call("GET", "/api/experiments/health"),
        rig.call("POST", "/api/experiments/update", {"id": eid, "name": "n"}),
        rig.call("POST", "/api/experiments/recover", {}),
        rig.call("POST", "/api/experiments/delete", {"id": eid}),
    ]
    for code, text in seen:
        assert code == 200, text
        assert absolute_paths(rig, text) == [], text
        assert "/home/" not in text and "/tmp/" not in text, text


def test_a_state_detail_with_a_path_is_scrubbed(rig):
    run_id = rig.finish_run()
    eid = rig.register(run_id)[1]["id"]
    with sqlite3.connect(rig.store.database) as conn:
        conn.execute(
            "UPDATE experiments SET state_detail = ? WHERE id = ?",
            ("could not rename %s/staging/x" % rig.registry, eid),
        )
    text = rig.call("GET", "/api/experiments?id=" + eid)[1]
    assert str(rig.registry) not in text and "staging/x" in text


# -- the real trainer ----------------------------------------------------------------------------


def _real_train_exists() -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ligandbench", "train", "--help"],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@pytest.mark.skipif(not _real_train_exists(), reason="needs ligandbench train")
def test_a_run_from_the_real_trainer_registers(tmp_path):
    """Runs for real once ``ligandbench train`` exists: a tiny training on a synthetic
    dataset, registered through the routes, and the model bytes kept identical.
    """
    pytest.importorskip("rdkit")
    from proteinviewer.ligandbench import Ligandbench
    from proteinviewer.schemas import DatasetResolveRequest, DatasetUploadRequest

    tool = Ligandbench()
    datasets = DatasetStore(tmp_path / "datasets")
    training = Training(tool, tmp_path / "work" / "training")
    store = ExperimentStore.open(tmp_path / "registry")
    service = ViewerService(
        None,
        Described(),
        Described(),
        Described(),
        ligands=tool,
        datasets=datasets,
        training=training,
        experiments=store,
    )
    ring_smiles = ("c1ccccc1", "c1ccncc1", "C1CCCCC1", "c1ccc2ccccc2c1", "c1ccsc1", "c1ccoc1")
    lines = ["Name,SMILES,Y"]
    for c, ring in enumerate(ring_smiles * 4):
        for k in range(1, 8):
            lines.append("m%d-%d,%s%s,%s" % (c, k, "C" * k, ring, 0.5 * ((c * 7 + k * 3) % 11)))
    up = service.upload_dataset(
        DatasetUploadRequest.parse({"name": "r.csv", "data": "\n".join(lines)})
    )
    dataset_id = service.resolve_dataset(
        DatasetResolveRequest.parse(
            {
                "dataset_id": up["dataset_id"],
                "structure_column": "SMILES",
                "target_column": "Y",
                "target_kind": "regression",
                "qualifier_column": None,
                "source_units": "",
                "transform": "identity",
            }
        )
    )["dataset_id"]
    try:
        run_id = training.start(
            dataset_id, str(datasets.directory(dataset_id)), 0, "random", "tiny"
        )["id"]
        assert wait_state(training, run_id, "done", "error", timeout=600)["state"] == "done"
        api = Api(service)
        status, record = api.handle(
            "POST",
            "/api/experiments",
            ApiRequest({}, {"run_id": run_id, "name": "real"}),
        )
        assert status == 200 and record["state"] == "ready"
        with training.pin(run_id) as run_dir:
            source = (Path(run_dir) / "model.json").read_bytes()
        assert (store.artifacts / record["id"] / "model.json").read_bytes() == source
    finally:
        training.shutdown()
