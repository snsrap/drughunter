"""The training registry: jobs through their states, every way they end, the
bounds on the queue, pinning against eviction. ``ligandbench train`` is the
stand-in in ``training_standin.py``, run by the real adapter, so what is
tested is the server's side of the CLI contract.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from proteinviewer.errors import BadRequest, Conflict, NotFound, ToolUnavailable
from proteinviewer.training import Job, Training

from .training_helpers import (
    alive,
    dataset_dir,
    release,
    standin_tool,
    wait_for,
    wait_state,
)


@pytest.fixture
def world(tmp_path):
    class World:
        root = tmp_path

        def __init__(self):
            self.made = []
            self.n = 0
            self.tool = self.fresh_tool()

        def fresh_tool(self, info=None, **kw):
            self.tools = getattr(self, "tools", 0) + 1
            where = tmp_path / ("bin-%d" % self.tools)
            where.mkdir()
            return standin_tool(where, info, **kw)

        def training(self, **kw):
            t = Training(self.tool, tmp_path / "training", **kw)
            self.made.append(t)
            return t

        def data(self, **behavior):
            self.n += 1
            return dataset_dir(tmp_path / "datasets", "ds-%012d" % self.n, **behavior)

        def start(self, training, data, strategy="scaffold", seed=0, profile="tiny"):
            return training.start(data.name, str(data), seed, strategy, profile)

    w = World()
    yield w
    for t in w.made:
        t.shutdown(wait=5)


# -- the lifecycle ------------------------------------------------------------------


def test_a_run_goes_from_queued_to_running_to_done(world):
    t = world.training()
    data = world.data(gate=True)
    started = world.start(t, data, strategy="random", seed=7)
    assert started["id"].startswith("run-")
    assert started["state"] in ("queued", "running")
    assert (started["dataset_id"], started["seed"]) == (data.name, 7)
    assert (started["production_strategy"], started["profile"]) == ("random", "tiny")

    running = wait_for(
        lambda: (p := t.progress(started["id"]))["stage"] == "tuning candidates" and p,
        what="the child's stage",
    )
    assert (running["state"], running["completed"], running["total"]) == ("running", 3, 10)
    with pytest.raises(Conflict):
        t.run(started["id"])

    release(data)
    done = wait_state(t, started["id"], "done")
    assert (done["completed"], done["total"]) == (10, 10)
    assert "error" not in done
    run = t.run(started["id"])
    assert (run["schema"], run["status"], run["seed"]) == ("qsar-run-v1", "completed", 7)
    assert run["production"]["strategy"] == "random"
    assert "model.json" in run["files"]
    # Status and metadata only: JSON, with no model in it.
    assert "standin" not in json.dumps(run)
    assert (t.root / started["id"] / "model.json").is_file()


def test_the_child_is_asked_the_way_the_contract_says(world):
    t = world.training()
    data = world.data()
    run_id = world.start(t, data, strategy="random", seed=42, profile="full")["id"]
    wait_state(t, run_id, "done")
    called = json.loads((data / "argv.json").read_text())["argv"]
    assert called[:4] == ["-m", "ligandbench", "train", str(data)]
    assert called[4:] == [
        "--out",
        str(t.root / run_id),
        "--seed",
        "42",
        "--production-strategy",
        "random",
        "--profile",
        "full",
    ]


def test_the_run_directory_is_the_runs_id_under_the_root(world):
    t = world.training()
    run_id = world.start(t, world.data())["id"]
    assert (t.root / run_id).is_dir()
    wait_state(t, run_id, "done")


def test_finishing_is_what_the_child_saying_done_means_until_its_result_is_read(tmp_path):
    # The child writes "done" before it exits; the run.json is read after.
    job = Job(
        "run-1", tmp_path, dict.fromkeys(("dataset_id", "seed", "production_strategy", "profile"))
    )
    (tmp_path / "progress.json").write_text(
        json.dumps({"state": "done", "stage": "done", "completed": 10, "total": 10})
    )
    assert job.progress()["state"] == "queued"
    assert job.progress()["stage"] == ""
    job._begin()
    p = job.progress()
    assert (p["state"], p["completed"], p["total"]) == ("finishing", 10, 10)
    job._finish("done", result={})
    assert job.progress()["state"] == "done"


def test_a_half_written_or_odd_progress_file_is_ignored(tmp_path):
    spec = dict.fromkeys(("dataset_id", "seed", "production_strategy", "profile"))
    job = Job("run-1", tmp_path, spec)
    job._begin()
    (tmp_path / "progress.json").write_text('{"state": "tun')
    assert (job.progress()["stage"], job.progress()["completed"]) == ("", 0)
    (tmp_path / "progress.json").write_text(
        json.dumps({"stage": ["x"], "completed": True, "total": -3})
    )
    p = job.progress()
    assert (p["stage"], p["completed"], p["total"], p["state"]) == ("", 0, 0, "running")


# -- every way a run can end ----------------------------------------------------------

FAILURES = [
    ("fail", "exit 1"),
    ("bad_input", "the target has one class"),
    ("missing", "XGBoost cannot be imported here"),
    ("crash", "killed by signal 9"),
    ("garbage", "printed no result"),
    ("no_run_json", "no readable run.json"),
    ("bad_run_json", "no readable run.json"),
    ("wrong_status", "not a completed run"),
]


@pytest.mark.parametrize("mode, message", FAILURES)
def test_every_failure_ends_in_error_with_a_message(world, mode, message):
    t = world.training()
    run_id = world.start(t, world.data(mode=mode))["id"]
    ended = wait_state(t, run_id, "done", "error", "cancelled")
    assert ended["state"] == "error"
    assert message in ended["error"]
    assert "error_code" not in ended
    with pytest.raises(Conflict):
        t.run(run_id)
    with pytest.raises(Conflict), t.pin(run_id):
        pass


def test_a_nonzero_exit_says_why(world):
    t = world.training()
    ended = wait_state(t, world.start(t, world.data(mode="fail"))["id"], "error")
    assert "exit 1" in ended["error"] and "boom in the tuner" in ended["error"]


def test_a_child_that_writes_a_lot_does_not_stall_and_still_completes(world):
    t = world.training()
    run_id = world.start(t, world.data(mode="noisy"))["id"]
    assert wait_state(t, run_id, "done", "error")["state"] == "done"


def test_a_run_that_takes_too_long_is_stopped(world):
    t = world.training(timeout=0.5)
    data = world.data(mode="hang")
    run_id = world.start(t, data)["id"]
    ended = wait_state(t, run_id, "error")
    assert "timed out" in ended["error"]
    # The whole group goes, not only the child.
    pid = int((data / "grandchild.pid").read_text())
    wait_for(lambda: not alive(pid), what="the grandchild to be killed")


def test_a_bug_on_the_job_thread_still_ends_the_job(world):
    class Broken(type(world.tool)):
        def training_outcome(self, *a):
            raise RuntimeError("the reader has a bug")

    t = Training(Broken(world.tool.python), world.root / "training")
    world.made.append(t)
    run_id = world.start(t, world.data())["id"]
    ended = wait_state(t, run_id, "error")
    assert "RuntimeError" in ended["error"] and "the reader has a bug" in ended["error"]
    # ... and the slot it held is free again.
    assert t.describe()["running"] == 0


def test_a_child_that_cannot_be_started_ends_the_job(world):
    t = world.training()
    assert t.available  # probed while the interpreter was there
    world.tool.python = str(world.root / "no-such-python")
    ended = wait_state(t, world.start(t, world.data())["id"], "error")
    assert "could not be started" in ended["error"]


def test_the_production_strategy_being_unavailable_is_an_error_with_its_reason(world):
    t = world.training()
    data = world.data(mode="unavailable")
    run_id = world.start(t, data, strategy="scaffold")["id"]
    ended = wait_state(t, run_id, "error")
    assert ended["error_code"] == "production_strategy_unavailable"
    assert ended["unavailable"] == {"reason": "too few scaffolds", "strategy": "scaffold"}
    assert "scaffold" in ended["error"] and "another production strategy" in ended["error"]
    assert not (t.root / run_id / "run.json").exists()


# -- cancelling ---------------------------------------------------------------------


def test_cancelling_a_running_run_kills_its_whole_process_group(world):
    t = world.training()
    data = world.data(mode="hang")
    run_id = world.start(t, data)["id"]
    wait_for(lambda: (data / "grandchild.pid").exists(), what="the child to start its own child")
    child = t._jobs[run_id].proc.pid
    grandchild = int((data / "grandchild.pid").read_text())
    assert alive(child) and alive(grandchild)

    got = t.cancel(run_id)
    assert got["state"] in ("running", "cancelled")
    ended = wait_state(t, run_id, "cancelled")
    assert ended["message"]
    wait_for(lambda: not alive(child) and not alive(grandchild), what="both to be dead")
    with pytest.raises(Conflict):
        t.run(run_id)
    assert t.describe()["running"] == 0


def test_cancelling_a_queued_run_removes_it_from_the_queue(world):
    t = world.training(concurrency=1, queue=2)
    first = world.data(gate=True)
    a = world.start(t, first)["id"]
    wait_state(t, a, "running")
    b = world.start(t, world.data())["id"]
    assert t.progress(b)["state"] == "queued"
    got = t.cancel(b)
    assert got["state"] == "cancelled" and got["message"]
    assert t.describe()["queued"] == 0
    release(first)
    wait_state(t, a, "done")
    assert t.progress(b)["state"] == "cancelled"  # it never ran
    assert not (world.root / "datasets").joinpath("ds-%012d" % 2, "argv.json").exists()


def test_cancelling_a_finished_run_changes_nothing(world):
    t = world.training()
    run_id = world.start(t, world.data())["id"]
    wait_state(t, run_id, "done")
    assert t.cancel(run_id)["state"] == "done"
    assert t.run(run_id)["status"] == "completed"
    failed = world.start(t, world.data(mode="fail"))["id"]
    wait_state(t, failed, "error")
    assert t.cancel(failed)["state"] == "error"


def test_cancelling_an_unknown_run_is_not_found(world):
    with pytest.raises(NotFound):
        world.training().cancel("run-nope")


# -- the bounds ---------------------------------------------------------------------


def test_the_queue_is_bounded_and_runs_go_in_order(world):
    t = world.training(concurrency=1, queue=2)
    gates = [world.data(gate=True) for _ in range(4)]
    a, b, c = (world.start(t, g)["id"] for g in gates[:3])
    wait_state(t, a, "running")
    assert [t.progress(i)["state"] for i in (b, c)] == ["queued", "queued"]
    with pytest.raises(Conflict, match="queue is full"):
        world.start(t, gates[3])
    assert len(list(t.root.iterdir())) == 3  # the refused one left nothing behind
    assert not (gates[3] / "argv.json").exists()

    release(gates[0])
    wait_state(t, a, "done")
    wait_state(t, b, "running")
    assert t.progress(c)["state"] == "queued"  # in order
    # A place has opened.
    d = world.start(t, gates[3])["id"]
    for g in gates[1:]:
        release(g)
    for i in (b, c, d):
        wait_state(t, i, "done")


def test_concurrent_starts_respect_the_bounds(world):
    t = world.training(concurrency=2, queue=3)
    gates = [world.data(gate=True) for _ in range(16)]
    results, barrier = [], threading.Barrier(len(gates))

    def go(g):
        barrier.wait()
        try:
            results.append(world.start(t, g)["id"])
        except Conflict as exc:
            results.append(exc)

    threads = [threading.Thread(target=go, args=(g,)) for g in gates]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    accepted = [r for r in results if isinstance(r, str)]
    refused = [r for r in results if isinstance(r, Conflict)]
    assert (len(accepted), len(refused)) == (5, 11)
    wait_for(lambda: t.describe()["running"] == 2, what="two to run")
    assert t.describe()["queued"] == 3
    # Never more than two at once, however long we look.
    time.sleep(0.2)
    assert sum(t.progress(i)["state"] == "running" for i in accepted) == 2
    for g in gates:
        release(g)
    for i in accepted:
        wait_state(t, i, "done")


def test_the_server_stopping_ends_every_job_and_every_child(world):
    t = world.training(concurrency=1, queue=2)
    hung = world.data(mode="hang")
    a = world.start(t, hung)["id"]
    wait_for(lambda: (hung / "grandchild.pid").exists(), what="the child to start")
    b = world.start(t, world.data())["id"]
    child = t._jobs[a].proc.pid
    t.shutdown(wait=10)
    assert t.progress(a)["state"] == "cancelled" and t.progress(b)["state"] == "cancelled"
    assert t.progress(a)["message"] and t.progress(b)["message"]
    assert not alive(child)
    with pytest.raises(Conflict, match="stopping"):
        world.start(t, world.data())


# -- eviction and pinning ------------------------------------------------------------


def finish(world, t, **kw):
    run_id = world.start(t, world.data(**kw))["id"]
    wait_state(t, run_id, "done", "error")
    return run_id


def test_an_old_run_is_forgotten_with_its_directory(world):
    t = world.training(limit=2)
    first, second = finish(world, t), finish(world, t)
    assert t.progress(first)["state"] == "done"
    third = finish(world, t)
    with pytest.raises(NotFound):
        t.progress(first)
    with pytest.raises(NotFound):
        t.run(first)
    with pytest.raises(NotFound), t.pin(first):
        pass
    assert not (t.root / first).exists()
    assert (t.root / second / "run.json").is_file() and (t.root / third / "run.json").is_file()


def test_eviction_never_drops_a_waiting_or_running_run(world):
    t = world.training(limit=1, concurrency=1, queue=3)
    gates = [world.data(gate=True) for _ in range(3)]
    ids = [world.start(t, g)["id"] for g in gates]
    assert [t.progress(i)["state"] in ("queued", "running") for i in ids] == [True] * 3
    for g in gates:
        release(g)
    for i in ids:
        wait_state(t, i, "done")


def test_a_pinned_run_survives_eviction_and_limit_pressure(world):
    t = world.training(limit=1)
    pinned = finish(world, t)
    with t.pin(pinned) as path:
        assert path == t.root / pinned
        for _ in range(3):
            finish(world, t)
        # Over the limit by every measure, and still here, files and all.
        assert t.progress(pinned)["state"] == "done"
        assert (path / "run.json").is_file() and (path / "model.json").is_file()
    # Released, it is just an old run again.
    with pytest.raises(NotFound):
        t.progress(pinned)
    assert not path.exists()


def test_pins_are_counted(world):
    t = world.training(limit=1)
    run_id = finish(world, t)
    with t.pin(run_id) as first:
        with t.pin(run_id) as second:
            assert first == second
            assert t._jobs[run_id].pins == 2
        assert t._jobs[run_id].pins == 1
        finish(world, t)
        assert t.progress(run_id)["state"] == "done"  # one holder is still enough
    with pytest.raises(NotFound):  # released: the limit forgets it now
        t.progress(run_id)


def test_a_pin_is_released_when_the_block_raises(world):
    t = world.training(limit=1)
    run_id = finish(world, t)
    with pytest.raises(RuntimeError, match="copy failed"), t.pin(run_id):
        raise RuntimeError("copy failed")
    assert t._jobs[run_id].pins == 0
    finish(world, t)
    with pytest.raises(NotFound):
        t.progress(run_id)


def test_only_a_completed_known_run_can_be_pinned(world):
    t = world.training()
    with pytest.raises(NotFound), t.pin("run-nope"):
        pass
    data = world.data(gate=True)
    running = world.start(t, data)["id"]
    wait_state(t, running, "running")
    with pytest.raises(Conflict, match="running"), t.pin(running):
        pass
    queued = world.start(t, world.data())["id"]
    cancelled = t.cancel(queued)["id"]
    with pytest.raises(Conflict, match="cancelled"), t.pin(cancelled):
        pass
    release(data)
    wait_state(t, running, "done")
    errored = finish(world, t, mode="fail")
    with pytest.raises(Conflict, match="error"), t.pin(errored):
        pass


def test_a_run_whose_files_are_gone_is_not_found(world):
    t = world.training()
    run_id = finish(world, t)
    (t.root / run_id / "run.json").unlink()
    with pytest.raises(NotFound, match="lost its files"), t.pin(run_id):
        pass


def test_a_run_that_is_restarted_away_is_not_found(world):
    first = world.training()
    run_id = finish(world, first)
    second = Training(world.tool, world.root / "training")  # a new server: an empty registry
    world.made.append(second)
    with pytest.raises(NotFound):
        second.progress(run_id)
    with pytest.raises(NotFound), second.pin(run_id):
        pass


# -- whether this server can train ---------------------------------------------------


def test_training_needs_ligandbench_and_xgboost(world):
    cases = [
        ({"xgboost": ""}, False, "XGBoost is not installed"),
        ({"xgboost": "3.2.0"}, True, ""),
        # An older ligandbench says nothing about it: unknown, not missing.
        ({"other": 1}, True, ""),
    ]
    for info, ok, why in cases:
        t = Training(world.fresh_tool(info), world.root / "training")
        assert t.available is ok
        assert why in t.describe()["reason"]
        if not ok:
            with pytest.raises(ToolUnavailable, match="XGBoost"):
                world.start(t, world.data())
            assert not (world.root / "training").exists()


def test_training_is_unavailable_where_ligandbench_does_not_run(world):
    t = Training(world.fresh_tool(working=False), world.root / "training")
    assert not t.available
    assert "does not run" in t.describe()["reason"]
    assert "NOT AVAILABLE" in t.status_line()
    with pytest.raises(ToolUnavailable):
        world.start(t, world.data())


def test_describe_reports_the_limits_and_the_load(world):
    t = world.training(concurrency=1, queue=4, limit=9)
    d = t.describe()
    assert (d["available"], d["running"], d["queued"]) == (True, 0, 0)
    assert (d["concurrency"], d["queue"], d["kept"]) == (1, 4, 9)


# -- the real thing ---------------------------------------------------------------------


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
def test_the_real_ligandbench_trains_a_tiny_run_end_to_end(tmp_path):
    """Runs for real the moment ``ligandbench train`` exists: a resolved dataset,
    the real adapter, the tiny search, then the run directory the contract describes.
    """
    pytest.importorskip("rdkit")
    from proteinviewer.dataset import DatasetStore
    from proteinviewer.ligandbench import Ligandbench
    from proteinviewer.schemas import DatasetResolveRequest, DatasetUploadRequest
    from proteinviewer.service import ViewerService

    tool = Ligandbench()
    store = DatasetStore(tmp_path / "datasets")
    svc = ViewerService(None, None, None, None, ligands=tool, datasets=store)
    ring_smiles = ("c1ccccc1", "c1ccncc1", "C1CCCCC1", "c1ccc2ccccc2c1", "c1ccsc1", "c1ccoc1")
    lines = ["Name,SMILES,Y"]
    for c, ring in enumerate(ring_smiles * 4):
        for k in range(1, 8):
            lines.append("m%d-%d,%s%s,%s" % (c, k, "C" * k, ring, 0.5 * ((c * 7 + k * 3) % 11)))
    up = svc.upload_dataset(DatasetUploadRequest.parse({"name": "r.csv", "data": "\n".join(lines)}))
    dataset_id = svc.resolve_dataset(
        DatasetResolveRequest.parse(
            {
                "dataset_id": up["dataset_id"],
                "structure_column": "SMILES",
                "target_column": "Y",
                "target_kind": "regression",
                "source_units": "",
                "transform": "identity",
            }
        )
    )["dataset_id"]

    t = Training(tool, tmp_path / "training")
    try:
        run_id = t.start(dataset_id, str(store.directory(dataset_id)), 0, "random", "tiny")["id"]
        ended = wait_state(t, run_id, "done", "error", timeout=600)
        assert ended["state"] == "done", ended
        run = t.run(run_id)
        assert run["status"] == "completed" and run["profile"] == "tiny"
        with t.pin(run_id) as path:
            for name in run["files"]:
                assert (Path(path) / name).is_file(), name
    finally:
        t.shutdown()


def test_a_bad_request_for_a_child_is_the_users_problem(world):
    # The adapter's reading of exit 2, on its own: BadRequest, not a tool failure.
    with pytest.raises(BadRequest, match="no target"):
        world.tool.training_outcome(2, json.dumps({"error": "no target"}), "")
