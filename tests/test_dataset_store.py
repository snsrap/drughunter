"""Datasets on disk: staged, resolved, expired, reopened after a restart."""

import json

import pytest

from proteinviewer.dataset import STAGED_TTL, DatasetStore
from proteinviewer.errors import Conflict, NotFound


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def resolved_into(store, name="a.csv", rows=3):
    """A workdir as ligandbench leaves one."""
    work = store.workdir()
    meta = {"n_rows": rows, "content_hash": "ab" * 32, "columns": []}
    (work / "dataset.json").write_text(json.dumps(meta))
    (work / "rows.json").write_text(json.dumps({"rows": [{"row_id": i} for i in range(rows)]}))
    return work


def test_a_staged_upload_keeps_the_text_exactly(tmp_path):
    store = DatasetStore(tmp_path)
    text = 'a,b\r\n"x\ny",1\r\n'
    staged = store.stage("a.csv", text)
    path, name, state = store.source(staged["dataset_id"])
    assert (name, state) == ("a.csv", "staged")
    assert path.read_bytes() == text.encode(), "no newline translation"


def test_ids_that_are_not_ours_are_not_found_and_never_touch_the_disk(tmp_path):
    store = DatasetStore(tmp_path)
    for bad in ("../x", "ds-../../etc", "", "ds-123", "DS-aaaaaaaaaaaa", None, 7):
        with pytest.raises(NotFound):
            store.get(bad)
        with pytest.raises(NotFound):
            store.cancel(bad)


def test_cancel_deletes_the_staged_files(tmp_path):
    store = DatasetStore(tmp_path)
    dataset_id = store.stage("a.csv", "a\n1\n")["dataset_id"]
    store.cancel(dataset_id)
    assert not (tmp_path / "staged" / dataset_id).exists()
    with pytest.raises(NotFound):
        store.source(dataset_id)
    with pytest.raises(NotFound):
        store.cancel(dataset_id)


def test_what_was_found_in_a_staged_upload_is_kept_with_it(tmp_path):
    store = DatasetStore(tmp_path)
    dataset_id = store.stage("a.csv", "a\n1\n")["dataset_id"]
    store.annotate(dataset_id, n_rows=1, columns=[{"name": "a"}])
    again = DatasetStore(tmp_path).staged(dataset_id)
    assert again["name"] == "a.csv" and again["n_rows"] == 1 and again["columns"] == [{"name": "a"}]
    store.cancel(dataset_id)
    with pytest.raises(NotFound):
        store.annotate(dataset_id, n_rows=2)


def test_a_staged_upload_expires_after_a_day(tmp_path):
    clock = Clock()
    store = DatasetStore(tmp_path, clock=clock)
    old = store.stage("old.csv", "a\n1\n")["dataset_id"]
    clock.now += STAGED_TTL - 60
    young = store.stage("young.csv", "a\n1\n")["dataset_id"]
    assert store.staged(old) is not None, "not yet a day"
    clock.now += 120
    assert store.staged(old) is None
    assert not (tmp_path / "staged" / old).exists()
    assert store.staged(young) is not None
    with pytest.raises(NotFound, match="expires after 24 hours"):
        store.source(old)


def test_resolving_a_staged_dataset_keeps_its_id_and_removes_the_staging(tmp_path):
    store = DatasetStore(tmp_path)
    staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = store.source(staged)
    meta = store.commit(resolved_into(store), name=name, source=source, staged_id=staged)
    assert meta["dataset_id"] == staged and meta["state"] == "ready" and meta["name"] == "a.csv"
    assert not (tmp_path / "staged" / staged).exists()
    assert store.source(staged)[2] == "ready"
    assert (tmp_path / "ready" / staged / "source.csv").read_text() == "a\n1\n"
    assert not list((tmp_path / "tmp").iterdir()), "the workdir was moved, not copied"


def test_a_ready_dataset_reopens_after_a_restart_unchanged(tmp_path):
    first = DatasetStore(tmp_path)
    staged = first.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = first.source(staged)
    meta = first.commit(resolved_into(first, rows=5), name=name, source=source, staged_id=staged)

    second = DatasetStore(tmp_path)  # a new process, the same directory
    assert second.get(staged) == meta
    assert [r["row_id"] for r in second.rows(staged)] == [0, 1, 2, 3, 4]
    assert second.source(staged)[0].read_text() == "a\n1\n"


def test_resolving_a_ready_dataset_again_makes_a_new_one(tmp_path):
    store = DatasetStore(tmp_path)
    staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = store.source(staged)
    first = store.commit(resolved_into(store), name=name, source=source, staged_id=staged)
    source, name, state = store.source(first["dataset_id"])
    assert state == "ready"
    second = store.commit(resolved_into(store, rows=1), name=name, source=source)
    assert second["dataset_id"] != first["dataset_id"]
    assert store.get(first["dataset_id"])["n_rows"] == 3, "the first is untouched"
    assert store.get(second["dataset_id"])["n_rows"] == 1


def test_a_ready_dataset_is_not_cancelled(tmp_path):
    store = DatasetStore(tmp_path)
    staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = store.source(staged)
    store.commit(resolved_into(store), name=name, source=source, staged_id=staged)
    with pytest.raises(Conflict, match="already resolved"):
        store.cancel(staged)


def test_a_resolution_that_wrote_nothing_is_not_committed(tmp_path):
    store = DatasetStore(tmp_path)
    staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = store.source(staged)
    with pytest.raises(NotFound, match="left no dataset"):
        store.commit(store.workdir(), name=name, source=source, staged_id=staged)
    assert store.staged(staged) is not None


def test_only_the_most_recent_datasets_stay_in_memory(tmp_path):
    store = DatasetStore(tmp_path, cache=2)
    ids = []
    for _ in range(3):
        staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
        source, name, _ = store.source(staged)
        store.commit(resolved_into(store), name=name, source=source, staged_id=staged)
        ids.append(staged)
        store.rows(staged)
    assert list(store._rows) == ids[1:]
    assert len(store.rows(ids[0])) == 3, "an evicted dataset is simply read again"


def test_depictions_are_cached_by_hash_row_and_version(tmp_path):
    store = DatasetStore(tmp_path)
    staged = store.stage("a.csv", "a\n1\n")["dataset_id"]
    source, name, _ = store.source(staged)
    store.commit(resolved_into(store), name=name, source=source, staged_id=staged)
    assert store.cached_depictions(staged, "1", [0, 1]) == {}
    store.put_depictions(staged, "1", {0: "<svg>zero</svg>"})
    assert store.cached_depictions(staged, "1", [0, 1]) == {0: "<svg>zero</svg>"}
    assert store.cached_depictions(staged, "2", [0]) == {}, "a new depiction version starts clean"
    assert DatasetStore(tmp_path).cached_depictions(staged, "1", [0]) == {0: "<svg>zero</svg>"}
    assert not list((tmp_path / "ready" / staged).rglob("*.tmp"))
