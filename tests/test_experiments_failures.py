"""The registry under failure: every step that can break, then a restart, then
``health`` and ``recover``; deletion, leases, and nothing escaping the root.
"""

import errno
import json
import os
import shutil
import sqlite3
import threading
import time

import pytest

from proteinviewer import experiments
from proteinviewer.errors import Conflict, NotFound
from proteinviewer.experiments import ExperimentStore, StoreError
from tests.experiments_helpers import MODEL_BYTES, make_run


class Crash(BaseException):
    """The process dying: nothing in the store is allowed to catch it."""


def fail_at(store, stage, error, nth=1):
    """Make the ``nth`` time the store reaches ``stage`` raise ``error``."""
    seen = {"n": 0}

    def hook(name):
        if name == stage:
            seen["n"] += 1
            if seen["n"] == nth:
                raise error

    store._checkpoint = hook


def rows(store):
    with sqlite3.connect(store.database) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT id, state, state_detail FROM experiments")]


def no_ready_row_lacks_files(store):
    """The invariant: a row that says ``ready`` has every file it lists."""
    with sqlite3.connect(store.database) as conn:
        found = conn.execute("SELECT id, state, checksums FROM experiments").fetchall()
    for eid, state, checksums in found:
        if state == "ready":
            tree = experiments._inspect_tree(
                store.artifacts / eid, json.loads(checksums), deep=True
            )
            assert tree.ok, tree.describe()


@pytest.fixture
def store(tmp_path):
    return ExperimentStore(tmp_path / "registry")


@pytest.fixture
def run(tmp_path):
    return make_run(tmp_path / "jobs", "run-1")


# -- before the row exists: copy, verify, insert -------------------------------------------
@pytest.mark.parametrize(
    ("stage", "nth"), [("copy", 1), ("copy", 3), ("verify_copy", 1), ("insert_pending", 1)]
)
def test_an_error_before_the_row_leaves_nothing_and_the_run_can_register_again(
    store, run, stage, nth
):
    fail_at(store, stage, OSError(errno.ENOSPC, "No space left on device"), nth)
    with pytest.raises(StoreError, match="nothing was kept"):
        store.register(run, "run-1", "m")
    assert rows(store) == [] and list(store.staging.iterdir()) == []
    assert list(store.artifacts.iterdir()) == []
    restarted = ExperimentStore(store.root)
    assert restarted.health()["ok"]
    assert restarted.register(run, "run-1", "m")["state"] == "ready"


@pytest.mark.parametrize(
    ("stage", "nth"), [("copy", 1), ("copy", 3), ("verify_copy", 1), ("insert_pending", 1)]
)
def test_a_crash_before_the_row_leaves_a_staging_orphan_that_is_reported_not_deleted(
    store, run, stage, nth
):
    fail_at(store, stage, Crash(), nth)
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    assert rows(store) == []
    restarted = ExperimentStore(store.root)
    health = restarted.health()
    assert [o["kind"] for o in health["orphans"]["staging"]] == ["directory"] and not health["ok"]
    assert restarted.recover() == {"completed": [], "deleted": [], "retained": []}
    assert len(list(restarted.staging.iterdir())) == 1, "an orphan is never removed"
    assert restarted.list() == []
    again = restarted.register(run, "run-1", "m")
    assert again["state"] == "ready"
    assert len(restarted.health()["orphans"]["staging"]) == 1


def test_a_real_copy_failure_is_reported_and_cleaned_up(store, run, monkeypatch):
    real, calls = experiments._copy_file, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError(errno.EIO, "I/O error")
        return real(src, dst)

    monkeypatch.setattr(experiments, "_copy_file", flaky)
    with pytest.raises(StoreError, match="I/O error"):
        store.register(run, "run-1", "m")
    assert rows(store) == [] and list(store.staging.iterdir()) == []


def test_a_copy_that_cannot_clean_up_after_itself_says_where_it_is(store, run, monkeypatch):
    fail_at(store, "copy", OSError("disk"))
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(StoreError, match="partial copy remains"):
        store.register(run, "run-1", "m")
    assert len(store.health()["orphans"]["staging"]) == 1


# -- after the row exists: rename, mark ready -----------------------------------------------
def test_a_rename_error_leaves_a_failed_record_that_recover_finishes(store, run):
    fail_at(store, "rename", OSError(errno.EXDEV, "cross-device"))
    with pytest.raises(StoreError, match="recover"):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    assert row["state"] == "failed" and "rename" in row["state_detail"]
    assert store.list() == [] and len(list(store.staging.iterdir())) == 1
    no_ready_row_lacks_files(store)
    restarted = ExperimentStore(store.root)
    assert [r["id"] for r in restarted.health()["failed"]] == [row["id"]]
    report = restarted.recover()
    assert report["completed"] == [row["id"]] and report["retained"] == []
    assert restarted.get(row["id"])["state"] == "ready" and restarted.health()["ok"]
    no_ready_row_lacks_files(restarted)


def test_a_real_os_rename_failure_is_the_same(store, run, monkeypatch):
    monkeypatch.setattr(os, "rename", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(StoreError):
        store.register(run, "run-1", "m")
    monkeypatch.undo()
    assert [r["state"] for r in rows(store)] == ["failed"]
    assert ExperimentStore(store.root).recover()["completed"]


def test_a_crash_before_the_rename_leaves_a_pending_record_recover_finishes(store, run):
    fail_at(store, "rename", Crash())
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    assert row["state"] == "pending" and store.list() == []
    restarted = ExperimentStore(store.root)
    assert [r["id"] for r in restarted.health()["pending"]] == [row["id"]]
    assert restarted.recover()["completed"] == [row["id"]]
    assert restarted.get(row["id"])["state"] == "ready"
    assert restarted.health()["ok"]


@pytest.mark.parametrize(
    ("error", "state"), [(OSError("disk full"), "failed"), (Crash(), "pending")]
)
def test_a_failed_commit_after_the_rename_leaves_the_files_in_place_and_no_ready_row(
    store, run, error, state
):
    fail_at(store, "mark_ready", error)
    with pytest.raises((StoreError, Crash)):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    assert row["state"] == state
    assert (store.artifacts / row["id"]).is_dir() and list(store.staging.iterdir()) == []
    assert store.list() == []
    restarted = ExperimentStore(store.root)
    assert restarted.recover()["completed"] == [row["id"]]
    assert restarted.list()[0]["id"] == row["id"] and restarted.health()["ok"]


def test_registering_again_after_a_failure_completes_it(store, run):
    fail_at(store, "rename", OSError("denied"))
    with pytest.raises(StoreError):
        store.register(run, "run-1", "m")
    store._checkpoint = lambda stage: None
    rec = ExperimentStore(store.root).register(run, "run-1", "other name")
    assert rec["state"] == "ready" and rec["name"] == "m"
    assert len(rows(store)) == 1


def test_a_pending_record_whose_files_do_not_verify_is_kept_and_flagged(store, run):
    fail_at(store, "rename", Crash())
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    stage = store.staging / row["id"]
    (stage / "model.json").write_bytes(MODEL_BYTES.replace(b"9", b"8"))
    restarted = ExperimentStore(store.root)
    report = restarted.recover()
    assert report["completed"] == []
    assert (
        report["retained"][0]["id"] == row["id"]
        and "do not verify" in report["retained"][0]["reason"]
    )
    assert (stage / "model.json").is_file(), "retained, not removed"
    (row,) = rows(restarted)
    assert row["state"] == "failed" and "do not verify" in row["state_detail"]
    assert restarted.list() == []
    # the way out is explicit: discard it, then register the run again
    assert restarted.delete(row["id"])["complete"]
    assert not stage.exists()
    assert restarted.register(run, "run-1", "m")["state"] == "ready"


def test_a_pending_record_with_no_files_or_with_both_directories_is_flagged(store, run):
    fail_at(store, "rename", Crash())
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    stage = store.staging / row["id"]
    shutil.copytree(stage, store.artifacts / row["id"])
    reason = ExperimentStore(store.root).recover()["retained"][0]["reason"]
    assert "both" in reason and stage.is_dir() and (store.artifacts / row["id"]).is_dir()
    shutil.rmtree(stage)
    shutil.rmtree(store.artifacts / row["id"])
    reason = ExperimentStore(store.root).recover()["retained"][0]["reason"]
    assert "gone" in reason and rows(store)[0]["state"] == "failed"


# -- damage to a ready experiment ------------------------------
def test_health_reports_missing_corrupt_and_orphaned_things_and_changes_none_of_them(
    store, tmp_path
):
    ids = {
        n: store.register(make_run(tmp_path / "jobs", n), n, n)["id"]
        for n in ("good", "flipped", "truncated", "lost-file", "lost-dir", "extra", "linked")
    }
    art = store.artifacts
    data = (art / ids["flipped"] / "model.json").read_bytes()
    (art / ids["flipped"] / "model.json").write_bytes(data.replace(b"9", b"8"))
    (art / ids["truncated"] / "features.json").write_bytes(b"{")
    os.unlink(art / ids["lost-file"] / "dataset" / "rows.json")
    shutil.rmtree(art / ids["lost-dir"])
    (art / ids["extra"] / "notes.txt").write_text("hand edited")
    os.symlink(art / ids["good"] / "model.json", art / ids["linked"] / "model.json.link")
    (art / "orphan-dir").mkdir()
    (art / "orphan-dir" / "x").write_text("x")
    (art / "stray.txt").write_text("x")
    (store.staging / "left-behind").mkdir()
    before = sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob("*"))

    restarted = ExperimentStore(store.root)
    health = restarted.health()
    by = lambda key: {e["id"]: e for e in health[key]}  # noqa: E731
    assert health["ok"] is False
    assert set(by("corrupt")) == {ids["flipped"], ids["truncated"], ids["extra"], ids["linked"]}
    assert by("corrupt")[ids["flipped"]]["files"] == ["model.json"]
    assert by("corrupt")[ids["extra"]]["unexpected"] == ["notes.txt"]
    assert by("corrupt")[ids["linked"]]["unsafe"] == ["model.json.link"]
    assert set(by("missing")) == {ids["lost-file"], ids["lost-dir"]}
    assert by("missing")[ids["lost-file"]]["files"] == ["dataset/rows.json"]
    assert by("missing")[ids["lost-dir"]]["directory"] == "missing"
    assert {(o["name"], o["kind"]) for o in health["orphans"]["artifacts"]} == {
        ("orphan-dir", "directory"),
        ("stray.txt", "file"),
    }
    assert [o["name"] for o in health["orphans"]["staging"]] == ["left-behind"]
    assert health["counts"]["ready"] == 7 and health["database"] == "ok"

    restarted.recover()
    assert sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob("*")) == before
    # a missing or resized file is also kept out of the selectable list without a checksum pass
    assert ids["lost-file"] not in {e["id"] for e in restarted.list()}
    assert ids["lost-dir"] not in {e["id"] for e in restarted.list()}
    with pytest.raises(Conflict):
        restarted.lease(ids["lost-dir"]).__enter__()


def test_a_clean_registry_is_healthy(store, run):
    store.register(run, "run-1", "m")
    health = store.health()
    assert health["ok"] and health["counts"] == {
        "pending": 0,
        "ready": 1,
        "deleting": 0,
        "failed": 0,
    }


# -- deleting ------------------------------
def test_delete_removes_the_row_and_only_that_experiments_directory(store, tmp_path):
    a = store.register(make_run(tmp_path / "jobs", "a"), "a", "A")
    b = store.register(make_run(tmp_path / "jobs", "b"), "b", "B")
    report = store.delete(a["id"])
    assert report == {
        "id": a["id"],
        "complete": True,
        "state": "deleted",
        "artifact_dir": "removed",
        "staging_dir": "already_absent",
        "row": "removed",
        "leases": 0,
        "errors": [],
    }
    assert not (store.artifacts / a["id"]).exists() and (store.artifacts / b["id"]).is_dir()
    assert [e["id"] for e in store.list()] == [b["id"]]
    with pytest.raises(NotFound):
        store.delete(a["id"])


def test_a_delete_that_cannot_remove_files_stays_deleting_and_can_be_retried(
    store, run, monkeypatch
):
    rec = store.register(run, "run-1", "m")
    monkeypatch.setattr(
        shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied"))
    )
    report = store.delete(rec["id"])
    assert (report["complete"], report["state"], report["row"]) == (False, "deleting", "retained")
    assert report["artifact_dir"] == "failed" and "denied" in report["errors"][0]
    assert store.get(rec["id"])["state"] == "deleting" and store.list() == []
    monkeypatch.undo()
    restarted = ExperimentStore(store.root)
    assert [d["id"] for d in restarted.health()["deleting"]] == [rec["id"]]
    again = restarted.delete(rec["id"])
    assert again["complete"] and again["artifact_dir"] == "removed" and again["row"] == "removed"


def test_a_delete_whose_row_removal_fails_reports_the_files_and_the_row_apart(store, run):
    rec = store.register(run, "run-1", "m")
    fail_at(store, "delete_row", sqlite3.OperationalError("database is locked"))
    report = store.delete(rec["id"])
    assert (report["artifact_dir"], report["row"], report["complete"]) == (
        "removed",
        "failed",
        False,
    )
    assert not (store.artifacts / rec["id"]).exists()
    assert store.get(rec["id"])["state"] == "deleting"
    store._checkpoint = lambda stage: None
    again = ExperimentStore(store.root).delete(rec["id"])
    assert (again["artifact_dir"], again["row"], again["complete"]) == (
        "already_absent",
        "removed",
        True,
    )


@pytest.mark.parametrize("stage", ["delete_files", "delete_row"])
def test_a_crash_during_deletion_is_finished_by_recover(store, run, stage):
    rec = store.register(run, "run-1", "m")
    fail_at(store, stage, Crash())
    with pytest.raises(Crash):
        store.delete(rec["id"])
    restarted = ExperimentStore(store.root)
    assert restarted.get(rec["id"])["state"] == "deleting" and restarted.list() == []
    assert restarted.recover()["deleted"] == [rec["id"]]
    assert rows(restarted) == [] and list(restarted.artifacts.iterdir()) == []
    assert restarted.health()["ok"]


def test_delete_waits_for_a_held_lease_and_then_completes(store, run):
    rec = store.register(run, "run-1", "m")
    result = {}
    with store.lease(rec["id"]) as path:
        thread = threading.Thread(target=lambda: result.update(store.delete(rec["id"], timeout=30)))
        thread.start()
        deadline = time.monotonic() + 5
        while store.get(rec["id"])["state"] != "deleting" and time.monotonic() < deadline:
            time.sleep(0.01)
        thread.join(0.2)
        assert thread.is_alive(), "the delete is waiting"
        assert store.get(rec["id"])["state"] == "deleting"
        assert (path / "model.json").read_bytes() == MODEL_BYTES, "files stay while leased"
        assert store.list() == []
        with pytest.raises(Conflict):
            store.lease(rec["id"]).__enter__()
    thread.join(10)
    assert not thread.is_alive() and result["complete"] and not path.exists()


def test_a_delete_that_times_out_leaves_a_retryable_deleting_record(tmp_path, run):
    store = ExperimentStore(tmp_path / "registry", lease_timeout=0.05)
    rec = store.register(run, "run-1", "m")
    with store.lease(rec["id"]) as first, store.lease(rec["id"]):
        report = store.delete(rec["id"])
        assert report["complete"] is False and report["leases"] == 2
        assert (report["state"], report["row"], report["artifact_dir"]) == (
            "deleting",
            "retained",
            "skipped",
        )
        assert first.is_dir()
    assert store.get(rec["id"])["state"] == "deleting"
    assert store.recover()["deleted"] == [rec["id"]]


def test_recover_does_not_finish_a_deletion_that_is_still_leased(store, run):
    rec = store.register(run, "run-1", "m")
    with store.lease(rec["id"]):
        store.delete(rec["id"], timeout=0)
        report = store.recover()
        assert report["deleted"] == [] and "in use" in report["retained"][0]["reason"]
        assert (store.artifacts / rec["id"]).is_dir()


def test_a_lease_is_released_when_its_block_raises_and_is_seen_by_every_store_object(store, run):
    rec = store.register(run, "run-1", "m")
    other = ExperimentStore(store.root)
    with pytest.raises(RuntimeError), store.lease(rec["id"]):
        assert other.delete(rec["id"], timeout=0)["complete"] is False
        raise RuntimeError("inference blew up")
    assert other.delete(rec["id"])["complete"]


def test_nothing_can_be_leased_unless_it_is_ready(store, run):
    fail_at(store, "rename", Crash())
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    with pytest.raises(Conflict):
        store.lease(row["id"]).__enter__()


# -- nothing escapes the root ------------------------------
def test_a_stored_path_is_never_used_to_delete(store, run, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious").write_text("keep")
    rec = store.register(run, "run-1", "m")
    with sqlite3.connect(store.database) as conn:
        conn.execute("DROP TRIGGER experiments_immutable")
        conn.execute(
            "UPDATE experiments SET artifact_path = ? WHERE id = ?", (str(victim), rec["id"])
        )
    assert store.delete(rec["id"])["complete"]
    assert (victim / "precious").read_text() == "keep"
    assert not (store.artifacts / rec["id"]).exists()


def test_a_row_whose_id_is_a_path_is_never_touched(store, run, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious").write_text("keep")
    rec = store.register(run, "run-1", "m")
    with sqlite3.connect(store.database) as conn:
        conn.execute("DROP TRIGGER experiments_immutable")
        conn.execute("PRAGMA ignore_check_constraints = 1")
        conn.execute(
            "UPDATE experiments SET id = '../../victim', state = 'deleting' WHERE id = ?",
            (rec["id"],),
        )
    restarted = ExperimentStore(store.root)
    report = restarted.recover()
    assert report["deleted"] == [] and "not a uuid" in report["retained"][0]["reason"]
    assert restarted.health()["invalid"][0]["id"] == "../../victim"
    assert (victim / "precious").read_text() == "keep"


def test_an_artifact_directory_swapped_for_a_symlink_is_refused_not_followed(store, run, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    rec = store.register(run, "run-1", "m")
    shutil.rmtree(store.artifacts / rec["id"])
    os.symlink(outside, store.artifacts / rec["id"])
    assert store.list() == []
    with pytest.raises(Conflict):
        store.lease(rec["id"]).__enter__()
    assert store.health()["corrupt"][0]["directory"] == "symlink"
    report = store.delete(rec["id"])
    assert report["artifact_dir"] == "failed" and "symlink" in report["errors"][0]
    assert report["complete"] is False and report["row"] == "retained"
    assert (outside / "precious").read_text() == "keep"
    assert os.path.islink(store.artifacts / rec["id"]), "the link is left for a human"
    assert store.get(rec["id"])["state"] == "deleting"


def test_a_registry_directory_turned_into_a_symlink_after_start_is_refused(store, run, tmp_path):
    rec = store.register(run, "run-1", "m")
    outside = tmp_path / "outside"
    shutil.move(store.artifacts, outside)
    os.symlink(outside, store.artifacts)
    report = store.delete(rec["id"])
    assert report["complete"] is False and (outside / rec["id"]).is_dir()


def test_the_removal_primitive_refuses_anything_that_is_not_one_of_our_uuid_directories(
    store, run, tmp_path
):
    victim = tmp_path / "victim"
    victim.mkdir()
    for bad in ("../../victim", str(victim), "..", "", "not-a-uuid"):
        with pytest.raises(StoreError, match="not an experiment id"):
            store._remove_owned(store.artifacts, bad)
    assert victim.is_dir()
    stray = "11111111-1111-4111-8111-111111111111"
    (store.artifacts / stray).write_text("a file, not a directory")
    with pytest.raises(StoreError, match="not a directory"):
        store._remove_owned(store.artifacts, stray)
    assert store._remove_owned(store.artifacts, "22222222-2222-4222-8222-222222222222") == (
        "already_absent"
    )


def test_the_rename_never_replaces_a_directory_that_is_already_there(store, run):
    fail_at(store, "rename", Crash())
    with pytest.raises(Crash):
        store.register(run, "run-1", "m")
    (row,) = rows(store)
    (store.artifacts / row["id"]).mkdir()
    with pytest.raises(FileExistsError):
        store._rename_into_place(row["id"])
    assert (store.staging / row["id"] / "model.json").is_file()
