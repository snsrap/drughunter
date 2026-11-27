"""The experiment registry: register, read, edit, restart, refuse."""

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from proteinviewer import experiments
from proteinviewer.errors import BadRequest, Conflict, NotFound
from proteinviewer.experiments import ExperimentStore
from tests.experiments_helpers import MODEL_BYTES, make_run, rewrite_run_json


@pytest.fixture
def store(tmp_path):
    return ExperimentStore(tmp_path / "registry")


@pytest.fixture
def run(tmp_path):
    return make_run(tmp_path / "jobs", "run-1")


def files_under(base: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()
    }


def nothing_kept(store):
    """No row, no artifact directory and no staging directory."""
    with sqlite3.connect(store.database) as conn:
        assert conn.execute("SELECT count(*) FROM experiments").fetchone()[0] == 0
    assert list(store.artifacts.iterdir()) == []
    assert list(store.staging.iterdir()) == []


# -- registering and reading ------------------------------
def test_a_registered_run_is_ready_and_its_directory_is_a_byte_for_byte_copy(store, run):
    record = store.register(run, "run-1", "First model", initiator="chemist")
    assert record["state"] == "ready"
    assert (record["name"], record["initiator"], record["source_run_id"]) == (
        "First model",
        "chemist",
        "run-1",
    )
    assert store.get(record["id"])["state"] == "ready"
    assert record["artifact_path"] == "artifacts/" + record["id"]
    assert files_under(store.artifacts / record["id"]) == files_under(run)
    assert (store.artifacts / record["id"] / "dataset" / "source.csv").read_bytes() == (
        b"smiles,pKi\r\nCCO,5.1\r\nCCN,6.2\r\n"
    ), "no newline translation"
    assert list(store.staging.iterdir()) == []


def test_what_the_run_says_about_itself_is_pulled_out_and_the_document_is_kept(store, run):
    rec = store.register(run, "run-1", "m")
    assert rec["model_family"] == "xgboost"
    assert (rec["dataset_id"], rec["dataset_hash"]) == ("ds-abcdef012345", "ab" * 32)
    assert (rec["task"], rec["target_column"], rec["production_strategy"]) == (
        "regression",
        "pKi",
        "scaffold",
    )
    assert rec["target"]["units"] == "nM" and rec["target"]["transform"] == "pKi"
    assert rec["production"]["config"] == {"max_depth": 3, "eta": 0.1}
    assert rec["splits"]["algorithm"] == "grouped-holdout-v1"
    assert rec["splits"]["strategies"]["random"]["seeds"] == [100, 101]
    assert rec["splits"]["strategies"]["scaffold"]["seeds"] == [200, 201]
    assert [r["seed"] for r in rec["repeat_configs"]["scaffold"]] == [200, 201]
    assert rec["metrics"]["random"]["aggregate"]["rmse"]["mean"] == 0.7
    assert rec["validation_trace"]["strategies"]["scaffold"][1]["validation"][0]["loss"] == 1.5
    assert rec["feature_schema"]["n_bits"] == 64
    assert rec["versions"]["xgboost"] == "3.2.0"
    assert rec["run"] == json.loads((run / "run.json").read_text()), "lossless"
    assert set(rec["checksums"]) == {"run.json", *rec["run"]["files"]}
    assert rec["created_utc"].endswith("Z")


def test_the_database_holds_metadata_and_never_the_model(store, run):
    store.register(run, "run-1", "m")
    assert MODEL_BYTES not in store.database.read_bytes()
    with sqlite3.connect(store.database) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(experiments)")]
        types = {r[2] for r in conn.execute("PRAGMA table_info(experiments)")}
    assert "BLOB" not in types and "model" not in columns


def test_everything_is_there_after_a_restart(store, run, tmp_path):
    rec = store.register(run, "run-1", "m")
    store.rename(rec["id"], "renamed")
    store.set_annotations(rec["id"], {"note": "good one"})
    again = ExperimentStore(store.root)
    got = again.get(rec["id"])
    assert (got["name"], got["annotations"], got["state"]) == (
        "renamed",
        {"note": "good one"},
        "ready",
    )
    assert [e["id"] for e in again.list()] == [rec["id"]]
    assert again.register(run, "run-1", "other")["id"] == rec["id"]
    with again.lease(rec["id"]) as path:
        assert (path / "model.json").read_bytes() == MODEL_BYTES
    assert again.delete(rec["id"])["complete"]
    assert ExperimentStore(store.root).list() == []
    with pytest.raises(NotFound):
        ExperimentStore(store.root).get(rec["id"])


def test_the_copy_outlives_the_job_directory(store, run):
    rec = store.register(run, "run-1", "m")
    shutil.rmtree(run)
    with ExperimentStore(store.root).lease(rec["id"]) as path:
        assert (path / "dataset" / "rows.json").is_file()
        assert (path / "splits.json").is_file()
        assert json.loads((path / "run.json").read_text())["versions"]["rdkit"] == "2025.03.1"
        assert (path / "dataset" / "source.csv").read_bytes().startswith(b"smiles,pKi")
    assert store.health()["ok"]


def test_list_is_newest_first_and_shows_only_what_can_be_selected(store, tmp_path):
    ticks = iter(range(1000, 2000))
    store._clock = lambda: next(ticks)
    ids = [
        store.register(make_run(tmp_path / "jobs", "r%d" % i), "r%d" % i, "m%d" % i)["id"]
        for i in range(3)
    ]
    assert [e["id"] for e in store.list()] == ids[::-1]
    assert all(e["selectable"] for e in store.list())
    os.unlink(store.artifacts / ids[1] / "model.json")
    assert [e["id"] for e in store.list()] == [ids[2], ids[0]]
    everything = store.list(selectable_only=False)
    assert [e["id"] for e in everything] == ids[::-1]
    assert [e["selectable"] for e in everything] == [True, False, True]


# -- idempotency ------------------------------
def test_a_repeated_registration_returns_the_same_record_and_keeps_its_name(store, run):
    first = store.register(run, "run-1", "First")
    second = store.register(run, "run-1", "Second", initiator="someone-else")
    assert second["id"] == first["id"]
    assert (second["name"], second["initiator"]) == (first["name"], first["initiator"])
    assert len(store.list()) == 1 and len(list(store.artifacts.iterdir())) == 1


def test_two_threads_registering_one_run_get_one_experiment(store, run):
    barrier, results, errors = threading.Barrier(2), [], []

    def go(name):
        try:
            barrier.wait(5)
            results.append(store.register(run, "run-1", name))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=("name-%d" % i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert results[0]["id"] == results[1]["id"] and results[0]["name"] == results[1]["name"]
    assert len(store.list()) == 1 and len(list(store.artifacts.iterdir())) == 1
    assert list(store.staging.iterdir()) == []


def test_different_runs_are_different_experiments(store, tmp_path):
    a = store.register(make_run(tmp_path / "jobs", "a"), "a", "A")
    b = store.register(make_run(tmp_path / "jobs", "b"), "b", "B")
    assert a["id"] != b["id"] and len(store.list()) == 2


# -- a source that cannot register ------------------------------
def make_bad(tmp_path, kind):
    jobs = tmp_path / "jobs"
    run = make_run(jobs, "bad")
    if kind == "error-status":
        rewrite_run_json(run, status="error")
    elif kind == "running-status":
        rewrite_run_json(run, status="running")
    elif kind == "no-run-json":
        (run / "run.json").unlink()
    elif kind == "wrong-schema":
        rewrite_run_json(run, schema="qsar-run-v0")
    elif kind == "not-json":
        (run / "run.json").write_text("{not json")
    elif kind == "nan":
        (run / "run.json").write_text('{"schema": "qsar-run-v1", "x": NaN}')
    elif kind == "changed-bytes":
        (run / "model.json").write_bytes(MODEL_BYTES.replace(b"9", b"8"))
    elif kind == "truncated":
        (run / "model.json").write_bytes(MODEL_BYTES[:-1])
    elif kind == "missing-file":
        (run / "scaffolds.json").unlink()
    elif kind == "unlisted-file":
        (run / "extra.bin").write_bytes(b"x")
    elif kind == "unlisted-nested-file":
        (run / "dataset" / "depictions").mkdir()
        (run / "dataset" / "depictions" / "1.svg").write_bytes(b"<svg/>")
    elif kind == "symlink-file":
        os.unlink(run / "features.json")
        os.symlink(run / "splits.json", run / "features.json")
    elif kind == "symlink-dir":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "rows.json").write_bytes(b'{"rows": []}')
        shutil.rmtree(run / "dataset")
        os.symlink(outside, run / "dataset")
    elif kind == "unlisted-symlink":
        os.symlink("/etc/hostname", run / "link")
    elif kind == "parent-path":
        document = json.loads((run / "run.json").read_text())
        document["files"]["../evil"] = document["files"]["model.json"]
        (run / "run.json").write_text(json.dumps(document))
    elif kind == "absolute-path":
        document = json.loads((run / "run.json").read_text())
        document["files"]["/etc/passwd"] = document["files"]["model.json"]
        (run / "run.json").write_text(json.dumps(document))
    elif kind == "lists-itself":
        document = json.loads((run / "run.json").read_text())
        document["files"]["run.json"] = document["files"]["model.json"]
        (run / "run.json").write_text(json.dumps(document))
    elif kind == "bad-checksum-shape":
        document = json.loads((run / "run.json").read_text())
        document["files"]["model.json"]["sha256"] = "XYZ"
        (run / "run.json").write_text(json.dumps(document))
    elif kind == "no-dataset-snapshot":
        document = json.loads((run / "run.json").read_text())
        del document["files"]["dataset/rows.json"]
        (run / "run.json").write_text(json.dumps(document))
        (run / "dataset" / "rows.json").unlink()
    elif kind == "no-target-task":
        rewrite_run_json(run, target={"column": "pKi"})
    return run


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("error-status", Conflict),
        ("running-status", Conflict),
        ("no-run-json", Conflict),
        ("wrong-schema", BadRequest),
        ("not-json", BadRequest),
        ("nan", BadRequest),
        ("changed-bytes", BadRequest),
        ("truncated", BadRequest),
        ("missing-file", BadRequest),
        ("unlisted-file", BadRequest),
        ("unlisted-nested-file", BadRequest),
        ("symlink-file", BadRequest),
        ("symlink-dir", BadRequest),
        ("unlisted-symlink", BadRequest),
        ("parent-path", BadRequest),
        ("absolute-path", BadRequest),
        ("lists-itself", BadRequest),
        ("bad-checksum-shape", BadRequest),
        ("no-dataset-snapshot", BadRequest),
        ("no-target-task", BadRequest),
    ],
)
def test_a_source_that_is_not_a_verified_completed_run_registers_nothing(
    store, tmp_path, kind, error
):
    run = make_bad(tmp_path, kind)
    with pytest.raises(error):
        store.register(run, "bad", "m")
    nothing_kept(store)


def test_a_run_directory_that_is_missing_or_a_symlink_is_refused(store, run, tmp_path):
    with pytest.raises(BadRequest):
        store.register(tmp_path / "nowhere", "x", "m")
    link = tmp_path / "link"
    os.symlink(run, link)
    with pytest.raises(BadRequest):
        store.register(link, "x", "m")
    nothing_kept(store)


def test_the_trainers_transient_progress_file_is_neither_an_error_nor_copied(store, tmp_path):
    run = make_run(tmp_path / "jobs", "r", extra={"progress.json": b'{"state": "done"}'})
    rec = store.register(run, "r", "m")
    assert not (store.artifacts / rec["id"] / "progress.json").exists()
    assert store.health()["ok"]


def test_a_source_changed_after_checking_is_caught_by_the_copy(store, run, monkeypatch):
    real = experiments._copy_file

    def tamper(src, dst):
        if src.name == "model.json":
            src.write_bytes(b"swapped after the check")
        return real(src, dst)

    monkeypatch.setattr(experiments, "_copy_file", tamper)
    with pytest.raises(experiments.StoreError, match="changed while"):
        store.register(run, "run-1", "m")
    nothing_kept(store)


def test_a_failed_run_is_not_idempotently_remembered(store, tmp_path):
    run = make_run(tmp_path / "jobs", "r")
    rewrite_run_json(run, status="error")
    with pytest.raises(Conflict):
        store.register(run, "r", "m")
    rewrite_run_json(run, status="completed")
    assert store.register(run, "r", "m")["state"] == "ready"


# -- editing ------------------------------
def test_only_the_name_and_annotations_can_be_edited(store, run):
    rec = store.register(run, "run-1", "m")
    renamed = store.rename(rec["id"], "  Better name ")
    assert renamed["name"] == "Better name" and renamed["updated_utc"] >= rec["updated_utc"]
    notes = store.set_annotations(rec["id"], {"owner": "me", "why": "baseline"})
    assert notes["annotations"] == {"owner": "me", "why": "baseline"}
    assert store.set_annotations(rec["id"], {})["annotations"] == {}
    after = store.get(rec["id"])
    for key in ("target", "splits", "metrics", "production", "versions", "checksums", "run"):
        assert after[key] == rec[key]


def test_the_database_itself_refuses_to_change_a_fact_of_the_run(store, run):
    rec = store.register(run, "run-1", "m")
    with sqlite3.connect(store.database) as conn:
        for column in ("target", "metrics", "dataset_hash", "source_run_id", "run_json", "id"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute("UPDATE experiments SET %s = 'x' WHERE id = ?" % column, (rec["id"],))
        conn.execute("UPDATE experiments SET name = 'fine' WHERE id = ?", (rec["id"],))


@pytest.mark.parametrize("name", ["", "   ", None, 7, "x" * 201, "two\nlines"])
def test_a_bad_name_is_refused(store, run, name):
    with pytest.raises(BadRequest):
        store.register(run, "run-1", name)
    rec = store.register(run, "run-1", "ok")
    with pytest.raises(BadRequest):
        store.rename(rec["id"], name)
    assert store.get(rec["id"])["name"] == "ok"


@pytest.mark.parametrize(
    "notes",
    [
        None,
        ["a"],
        {"a": 1},
        {1: "a"},
        {"": "x"},
        {"k" * 65: "x"},
        {"k": "v" * 2001},
        {str(i): "x" for i in range(33)},
    ],
)
def test_bad_annotations_are_refused(store, run, notes):
    rec = store.register(run, "run-1", "m")
    with pytest.raises(BadRequest):
        store.set_annotations(rec["id"], notes)


def test_a_record_that_is_not_ready_cannot_be_edited(store, run):
    rec = store.register(run, "run-1", "m")
    with sqlite3.connect(store.database) as conn:
        conn.execute("UPDATE experiments SET state = 'failed' WHERE id = ?", (rec["id"],))
    with pytest.raises(Conflict):
        store.rename(rec["id"], "x")
    with pytest.raises(Conflict):
        store.set_annotations(rec["id"], {})


# -- initiator ------------------------------
def test_the_initiator_is_the_local_user_or_local(store, tmp_path, monkeypatch):
    monkeypatch.setattr(experiments.getpass, "getuser", lambda: "alice")
    assert store.register(make_run(tmp_path / "j", "a"), "a", "m")["initiator"] == "alice"

    def unavailable():
        raise KeyError("getpwuid(): uid not found: 4242")

    monkeypatch.setattr(experiments.getpass, "getuser", unavailable)
    assert store.register(make_run(tmp_path / "j", "b"), "b", "m")["initiator"] == "local"
    monkeypatch.setattr(experiments.getpass, "getuser", lambda: "")
    assert store.register(make_run(tmp_path / "j", "c"), "c", "m")["initiator"] == "local"
    assert experiments.default_initiator() == "local"
    with pytest.raises(BadRequest):
        store.register(make_run(tmp_path / "j", "d"), "d", "m", initiator="  ")


# -- ids never reach the filesystem ------------------------------
@pytest.mark.parametrize(
    "bad",
    ["../x", "/etc", "", "..", "a/b", "A" * 36, "00000000-0000-0000-0000-000000000000", None, 7],
)
def test_an_id_that_is_not_a_uuid_we_made_is_not_found(store, run, bad):
    store.register(run, "run-1", "m")
    for call in (
        store.get,
        store.delete,
        lambda i: store.rename(i, "x"),
        lambda i: store.set_annotations(i, {}),
        lambda i: store.lease(i).__enter__(),
    ):
        with pytest.raises(NotFound):
            call(bad)


def test_the_registry_directories_must_be_real_directories(tmp_path):
    root = tmp_path / "registry"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.mkdir()
    os.symlink(elsewhere, root / "artifacts")
    with pytest.raises(experiments.StoreError, match="real directory"):
        ExperimentStore(root)


def test_open_takes_health_once_and_removes_nothing(tmp_path, run):
    root = tmp_path / "registry"
    (root / "artifacts" / "stray").mkdir(parents=True)
    (root / "staging").mkdir()
    (root / "staging" / "half-copied").mkdir()
    store = ExperimentStore.open(root)
    report = store.startup_health
    assert report["ok"] is False
    assert [o["name"] for o in report["orphans"]["artifacts"]] == ["stray"]
    assert [o["name"] for o in report["orphans"]["staging"]] == ["half-copied"]
    assert (root / "artifacts" / "stray").is_dir() and (root / "staging" / "half-copied").is_dir()
    assert ExperimentStore(root).startup_health is None
