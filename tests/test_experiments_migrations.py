"""Schema versions and migrations, and where the registry lives."""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from proteinviewer.config import Settings, default_experiment_root
from proteinviewer.experiments import MIGRATIONS, SCHEMA_VERSION, ExperimentStore, StoreError
from tests.experiments_helpers import make_run


def version(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def tables(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def columns(path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(experiments)")]


def v2_add_label(conn):
    conn.execute("ALTER TABLE experiments ADD COLUMN label TEXT")


def v3_add_table(conn):
    conn.execute("CREATE TABLE tags (experiment_id TEXT, tag TEXT)")


# -- a fresh registry ------------------------------
def test_a_new_registry_is_created_at_the_newest_schema(tmp_path):
    store = ExperimentStore(tmp_path / "registry")
    assert version(store.database) == SCHEMA_VERSION == len(MIGRATIONS) == 1
    assert "experiments" in tables(store.database)
    assert store.health()["schema_version"] == 1
    assert (store.root / "artifacts").is_dir() and (store.root / "staging").is_dir()


# -- the previous layout ------------------------------
def build_v0_layout(path):
    """A database as an earlier, unversioned prototype left it: a table of the
    same name with another shape, and ``user_version`` still 0.
    """
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE experiments (id TEXT, name TEXT)")
        conn.execute("INSERT INTO experiments VALUES ('old-1', 'prototype run')")


def test_a_previous_layout_that_cannot_be_migrated_is_reported_and_left_untouched(tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    build_v0_layout(root / "registry.sqlite3")
    with pytest.raises(StoreError, match=r"version 1 failed.*unchanged at version 0"):
        ExperimentStore(root)
    assert version(root / "registry.sqlite3") == 0
    with sqlite3.connect(root / "registry.sqlite3") as conn:
        assert conn.execute("SELECT id, name FROM experiments").fetchall() == [
            ("old-1", "prototype run")
        ]
    assert columns(root / "registry.sqlite3") == ["id", "name"]


def test_an_empty_unversioned_database_file_is_migrated(tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    sqlite3.connect(root / "registry.sqlite3").close()
    assert version(root / "registry.sqlite3") == 0
    assert ExperimentStore(root).health()["ok"] and version(root / "registry.sqlite3") == 1


# -- a previous schema carrying data ------------------------------
def test_data_written_under_schema_1_survives_a_later_migration(tmp_path):
    root = tmp_path / "registry"
    old = ExperimentStore(root)
    rec = old.register(make_run(tmp_path / "jobs", "a"), "a", "kept")
    new = ExperimentStore(root, migrations=[*MIGRATIONS, v2_add_label])
    assert version(new.database) == 2 and "label" in columns(new.database)
    got = new.get(rec["id"])
    assert got["name"] == "kept" and got["run"] == rec["run"]
    with new.lease(rec["id"]) as path:
        assert (path / "model.json").is_file()
    assert new.health()["ok"]


def test_steps_apply_in_order_exactly_once_and_a_second_open_changes_nothing(tmp_path):
    root = tmp_path / "registry"
    calls = []

    def step(label, inner):
        def run(conn):
            calls.append(label)
            inner(conn)

        return run

    steps = [
        step("v1", MIGRATIONS[0]),
        step("v2", v2_add_label),
        step("v3", v3_add_table),
    ]
    ExperimentStore(root, migrations=steps)
    assert calls == ["v1", "v2", "v3"] and version(root / "registry.sqlite3") == 3
    ExperimentStore(root, migrations=steps)
    assert calls == ["v1", "v2", "v3"], "idempotent"
    # a store that has only seen the first step catches up, not from the start
    older = tmp_path / "older"
    ExperimentStore(older, migrations=steps[:1])
    calls.clear()
    ExperimentStore(older, migrations=steps)
    assert calls == ["v2", "v3"]


def test_a_failing_step_rolls_back_and_keeps_the_steps_before_it(tmp_path):
    root = tmp_path / "registry"
    ExperimentStore(root)

    def half_done(conn):
        conn.execute("ALTER TABLE experiments ADD COLUMN label TEXT")
        conn.execute("CREATE TABLE tags (x TEXT)")
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(
        StoreError, match=r"version 2 failed.*disk I/O error.*unchanged at version 1"
    ):
        ExperimentStore(root, migrations=[*MIGRATIONS, half_done])
    path = root / "registry.sqlite3"
    assert version(path) == 1 and "tags" not in tables(path) and "label" not in columns(path)
    assert ExperimentStore(root).health()["ok"], "the registry still opens at the old version"


def test_a_failure_after_a_good_step_keeps_the_good_step(tmp_path):
    root = tmp_path / "registry"

    def boom(conn):
        raise sqlite3.OperationalError("nope")

    with pytest.raises(StoreError, match="version 3"):
        ExperimentStore(root, migrations=[*MIGRATIONS, v2_add_label, boom])
    assert version(root / "registry.sqlite3") == 2 and "label" in columns(root / "registry.sqlite3")


def test_a_database_from_a_newer_version_is_refused_and_not_touched(tmp_path):
    root = tmp_path / "registry"
    store = ExperimentStore(root, migrations=[*MIGRATIONS, v2_add_label])
    before = store.database.read_bytes()
    with pytest.raises(
        StoreError, match=r"schema version 2, newer than.*\(1\).*nothing was changed"
    ):
        ExperimentStore(root)
    assert store.database.read_bytes() == before and version(store.database) == 2


# -- where it lives ------------------------------
def test_the_default_root_is_under_the_data_directory_and_never_the_temp_root(tmp_path):
    assert default_experiment_root({"XDG_DATA_HOME": str(tmp_path / "data")}) == (
        tmp_path / "data" / "proteinviewer" / "experiments"
    )
    home = default_experiment_root({})
    assert home == Path(os.path.expanduser("~/.local/share")) / "proteinviewer" / "experiments"
    assert default_experiment_root({"XDG_DATA_HOME": ""}) == home
    temp = Path(tempfile.gettempdir()).resolve()
    assert temp not in home.resolve().parents
    assert Settings().experiment_root is None, (
        "unset means default_experiment_root(), not work_root"
    )
    assert Settings().work_root is None


def test_the_environment_overrides_the_root_and_it_is_used(tmp_path):
    mine = tmp_path / "mine"
    settings = Settings.from_env({"PV_EXPERIMENT_DIR": str(mine)})
    assert settings.experiment_root == mine
    assert Settings.from_env({}).experiment_root is None
    assert Settings.from_env({"PV_EXPERIMENT_DIR": ""}).experiment_root is None
    store = ExperimentStore(settings.experiment_root)
    assert store.root == mine and (mine / "registry.sqlite3").is_file()
    assert Settings.from_env({"PV_EXPERIMENT_DIR": "~/x"}).experiment_root == Path(
        os.path.expanduser("~/x")
    )


def test_the_registry_root_is_created_with_its_parents_and_is_not_a_temp_directory(tmp_path):
    store = ExperimentStore(tmp_path / "a" / "b" / "registry")
    assert store.database.is_file()
