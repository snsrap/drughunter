"""The experiment registry: a completed training run, kept for good.

A training run lives in a temporary job directory. *Registering* it copies the
run -- its model, feature and target configuration, actual splits, reports,
training scaffolds and an immutable snapshot of the dataset it trained on --
into a registry root that outlives the job, and records what the run says
about itself in SQLite. One experiment is one run: both strategies'
evaluation (or why one was unavailable) and one production model.

    <root>/registry.sqlite3        metadata only: no weights, no pickles, no blobs
    <root>/artifacts/<uuid>/       a registered run, as ``ligandbench train`` wrote it
    <root>/staging/<uuid>/         a run being copied in, or one that could not finish

An experiment is ``pending`` while its copy is being made, ``ready`` once the
directory is in place and verified, ``deleting`` from the moment its deletion
is authorised until the row is gone, and ``failed`` when a registration
stopped half way and is waiting to be recovered or discarded. Only ``ready``
experiments whose files are present are *selectable*.

Registration order (each step leaves a state that is explicit, never a ``ready``
row beside partial files): verify the pinned source against ``run.json``'s
checksums; copy into ``staging/<uuid>/`` and verify the copy; insert the
``pending`` row; rename the directory to ``artifacts/<uuid>/``; mark ``ready``.
A crash or error after the row exists leaves ``pending`` or ``failed``, which
:meth:`ExperimentStore.recover` can finish when the files verify.

Concurrency: mutations are serialised by one process-wide lock per registry
root (the multi-step filesystem sequence needs a single owner; SQLite alone
cannot give it that), and every write inside is a ``BEGIN IMMEDIATE``
transaction on its own connection, so each step is atomic and a second process
waits instead of interleaving. Reads open their own connection and take no
lock. Leases are counted in memory, per process.

This module is I/O: sqlite3 and the filesystem, nothing of HTTP, jobs, models
or any tool. It never loads a model; it moves bytes and keeps metadata.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import BadRequest, Conflict, NotFound, ViewerError

__all__ = [
    "ExperimentStore",
    "StoreError",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "RUN_SCHEMA",
    "REQUIRED_FILES",
]

#: ``run.json``'s ``schema`` value this store understands.
RUN_SCHEMA = "qsar-run-v1"
#: Files every registered run must hold (and ``run.json`` must list).
REQUIRED_FILES = (
    "model.json",
    "features.json",
    "splits.json",
    "validation_trace.json",
    "scaffolds.json",
    "dataset/source.csv",
    "dataset/dataset.json",
    "dataset/rows.json",
)
STATES = ("pending", "ready", "deleting", "failed")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_RUN_JSON = 64 * 1024 * 1024
_MAX_FILES = 100_000
_CHUNK = 1024 * 1024
#: Written by the trainer after ``run.json``, so it cannot be in the checksums
#: and is not part of a run: never copied, and never an error.
_TRANSIENT = ("progress.json",)


class StoreError(ViewerError):
    """The registry itself cannot do what was asked: a database from a newer
    version, a migration that failed, a registration that stopped part way, a
    directory that must not be touched. The message says what state it left.
    """


# -- schema ---------------------------------------------------------------
_IMMUTABLE = (
    "id, source_run_id, model_family, initiator, created_utc, run_created_utc, dataset_id, "
    "dataset_hash, task, target_column, production_strategy, artifact_path, target, splits, "
    "production, repeat_configs, feature_schema, metrics, validation_trace, versions, "
    "checksums, run_json"
)

_V1 = (
    """
    CREATE TABLE experiments (
        id                TEXT PRIMARY KEY CHECK (length(id) = 36),
        source_run_id     TEXT NOT NULL UNIQUE,
        name              TEXT NOT NULL,
        annotations       TEXT NOT NULL DEFAULT '{}',
        state             TEXT NOT NULL
                          CHECK (state IN ('pending', 'ready', 'deleting', 'failed')),
        state_detail      TEXT,
        model_family      TEXT NOT NULL,
        initiator         TEXT NOT NULL,
        created_utc       TEXT NOT NULL,
        updated_utc       TEXT NOT NULL,
        run_created_utc   TEXT,
        dataset_id        TEXT,
        dataset_hash      TEXT NOT NULL,
        task              TEXT NOT NULL,
        target_column     TEXT,
        production_strategy TEXT NOT NULL,
        artifact_path     TEXT NOT NULL,
        target            TEXT NOT NULL,
        splits            TEXT NOT NULL,
        production        TEXT NOT NULL,
        repeat_configs    TEXT NOT NULL,
        feature_schema    TEXT NOT NULL,
        metrics           TEXT NOT NULL,
        validation_trace  TEXT NOT NULL,
        versions          TEXT NOT NULL,
        checksums         TEXT NOT NULL,
        run_json          TEXT NOT NULL
    )
    """,
    "CREATE INDEX experiments_by_state ON experiments (state, created_utc)",
    # What the run says about itself is a fact of the run: only the name and
    # the annotations are edited, so the database refuses the rest even from
    # a bug in this module.
    "CREATE TRIGGER experiments_immutable BEFORE UPDATE OF %s ON experiments "
    "BEGIN SELECT RAISE(ABORT, 'experiments: this field is immutable'); END" % _IMMUTABLE,
)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    for statement in _V1:
        conn.execute(statement)


#: ``MIGRATIONS[n]`` takes a database from schema version ``n`` to ``n + 1``
#: using ``conn.execute`` only (the runner owns the transaction and
#: ``PRAGMA user_version``). Never edit a shipped step: append one.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migrate_v1,)
SCHEMA_VERSION = len(MIGRATIONS)


def _migrate(conn: sqlite3.Connection, migrations: Sequence[Callable]) -> None:
    """Bring an open database to the newest schema, one transaction per step."""
    newest = len(migrations)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > newest:
        raise StoreError(
            "the registry database is schema version %d, newer than this version of "
            "proteinviewer understands (%d); upgrade proteinviewer or use another "
            "registry directory (nothing was changed)" % (version, newest)
        )
    for target in range(version + 1, newest + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have taken this step while this one waited.
            if conn.execute("PRAGMA user_version").fetchone()[0] >= target:
                conn.execute("ROLLBACK")
                continue
            migrations[target - 1](conn)
            conn.execute("PRAGMA user_version = %d" % target)
            conn.execute("COMMIT")
        except Exception as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise StoreError(
                "migrating the registry to schema version %d failed (%s); the database "
                "was rolled back and is unchanged at version %d" % (target, exc, target - 1)
            ) from exc
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise


# -- shared state per registry root ---------------------------------------
class _Shared:
    """What every store object on one root has to agree on, in one process."""

    def __init__(self) -> None:
        self.mutate = threading.Lock()
        self.cond = threading.Condition()
        self.leases: dict[str, int] = {}


_SHARED: dict[str, _Shared] = {}
_SHARED_GUARD = threading.Lock()


def _shared_for(root: Path) -> _Shared:
    key = os.path.realpath(root)
    with _SHARED_GUARD:
        return _SHARED.setdefault(key, _Shared())


def default_initiator() -> str:
    """The server's local OS username, or ``"local"`` when there is none.

    Provenance, not identity: nothing here authenticates anyone.
    """
    try:
        name = getpass.getuser()
    except (KeyError, OSError, ImportError):
        return "local"
    return name.strip() if isinstance(name, str) and name.strip() else "local"


# -- the store ------------------------------------------------------------
class ExperimentStore:
    """The experiments under one registry root."""

    def __init__(
        self,
        root: os.PathLike | str,
        *,
        lease_timeout: float = 30.0,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        migrations: Sequence[Callable[[sqlite3.Connection], None]] | None = None,
    ):
        self.root = Path(os.path.abspath(os.path.expanduser(str(root))))
        self.lease_timeout = lease_timeout
        self._clock = clock
        self._monotonic = monotonic
        self._migrations = tuple(MIGRATIONS if migrations is None else migrations)
        #: The health report taken by :meth:`open`; ``None`` for a bare constructor.
        self.startup_health: dict | None = None
        self.root.mkdir(parents=True, exist_ok=True)
        self._shared = _shared_for(self.root)
        self._layout()
        with self._shared.mutate, contextlib.closing(self._open_db()) as conn:
            _migrate(conn, self._migrations)

    @classmethod
    def open(cls, root: os.PathLike | str, **options: Any) -> ExperimentStore:
        """Open the registry and take its health once. Nothing is repaired or
        removed: the report is on ``startup_health`` for the server to log.
        """
        store = cls(root, **options)
        store.startup_health = store.health()
        return store

    # -- paths ------------------------------------------------------------
    @property
    def database(self) -> Path:
        return self.root / "registry.sqlite3"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    def _layout(self) -> None:
        """Make the two directories, and refuse a layout that points elsewhere."""
        for path in (self.artifacts, self.staging):
            try:
                path.mkdir(exist_ok=True)
                st = os.lstat(path)
            except OSError as exc:
                raise StoreError("cannot use registry directory %s: %s" % (path, exc)) from exc
            if not stat.S_ISDIR(st.st_mode):
                raise StoreError("%s must be a real directory, not a symlink or a file" % path)
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISLNK(os.lstat(self.database).st_mode):
                raise StoreError(
                    "%s is a symlink; the registry database must be a file" % self.database
                )

    def _check_id(self, experiment_id: Any) -> str:
        if not isinstance(experiment_id, str) or not _UUID.match(experiment_id):
            raise NotFound("no such experiment %r" % (experiment_id,))
        return experiment_id

    # -- database ----------------------------------------------------------
    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._open_db()
        try:
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, taken up front so it cannot deadlock on an
        upgrade; rolled back on any exit but a clean one, as a crash would.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise

    def _checkpoint(self, stage: str) -> None:
        """Where a test makes a step fail. Does nothing."""

    def _now(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _row(self, experiment_id: str, conn: sqlite3.Connection | None = None):
        if conn is not None:
            return conn.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        with self._connect() as own:
            return self._row(experiment_id, own)

    def _row_by_run(self, run_id: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM experiments WHERE source_run_id = ?", (run_id,)
            ).fetchone()

    # -- reading -----------------------------------------------------------
    def get(self, experiment_id: str) -> dict:
        """One experiment, in any state, with everything the run recorded."""
        eid = self._check_id(experiment_id)
        row = self._row(eid)
        if row is None:
            raise NotFound("no such experiment %s" % eid)
        return _record(row)

    def list(self, *, selectable_only: bool = True) -> list[dict]:
        """Experiments, newest first.

        By default only the ``ready`` ones whose directory is in place with
        every file at its recorded size: what inference may choose from. A
        full checksum pass is :meth:`health`'s job, not every listing's.
        ``selectable_only=False`` returns every row, whatever its state, for a
        registry page that shows failures.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM experiments ORDER BY created_utc DESC, id"
            ).fetchall()
        out = []
        for row in rows:
            ok = row["state"] == "ready" and self._present(row)
            if selectable_only and not ok:
                continue
            out.append({**_summary(row), "selectable": ok, "headline": _headline(row)})
        return out

    def _present(self, row) -> bool:
        if not _UUID.match(row["id"]):
            return False
        found = _inspect_tree(self.artifacts / row["id"], json.loads(row["checksums"]), deep=False)
        return found.ok

    # -- editing -----------------------------------------------------------
    def rename(self, experiment_id: str, name: str) -> dict:
        name = _clean_text(name, "name", 200)
        return self._edit(experiment_id, "name = ?", (name,))

    def set_annotations(self, experiment_id: str, annotations: Mapping[str, str]) -> dict:
        """Replace the annotations: a few short ``str: str`` notes."""
        return self._edit(
            experiment_id, "annotations = ?", (json.dumps(_clean_annotations(annotations)),)
        )

    def _edit(self, experiment_id: str, assignment: str, params: tuple) -> dict:
        eid = self._check_id(experiment_id)
        with self._shared.mutate, self._write() as conn:
            row = self._row(eid, conn)
            if row is None:
                raise NotFound("no such experiment %s" % eid)
            if row["state"] != "ready":
                raise Conflict("experiment %s is %s and cannot be edited" % (eid, row["state"]))
            conn.execute(
                "UPDATE experiments SET %s, updated_utc = ? WHERE id = ?" % assignment,
                (*params, self._now(), eid),
            )
        return self.get(eid)

    # -- registering -------------------------------------------------------
    def register(
        self, run_dir: os.PathLike | str, run_id: str, name: str, initiator: str | None = None
    ) -> dict:
        """Keep a completed run. ``run_dir`` is already pinned by the caller.

        Idempotent on ``run_id``: a repeat returns the one record (a name
        given the second time does not rename it). A run that has not
        completed, whose checksums do not match, that holds a symlink or whose
        files are not exactly the ones ``run.json`` lists cannot register.
        """
        name = _clean_text(name, "name", 200)
        run_id = _clean_text(run_id, "run id", 128)
        who = default_initiator() if initiator is None else _clean_text(initiator, "initiator", 128)
        with self._shared.mutate:
            existing = self._row_by_run(run_id)
            if existing is not None:
                return self._repeat(existing)
            plan = _inspect_source(Path(run_dir))
            eid = str(uuid.uuid4())
            stage = self.staging / eid
            try:
                self._copy_in(plan, Path(run_dir), stage)
                found = _inspect_tree(stage, plan.checksums, deep=True)
                if not found.ok:
                    raise StoreError("the copy does not match the run: %s" % found.describe())
                self._checkpoint("verify_copy")
                self._insert_pending(eid, run_id, name, who, plan)
            except sqlite3.IntegrityError:
                # Another process registered this run first.
                self._discard_stage(eid)
                existing = self._row_by_run(run_id)
                if existing is None:
                    raise
                return self._repeat(existing)
            except Exception as exc:
                gone = self._discard_stage(eid)
                raise StoreError(
                    "run %s was not registered (%s); %s"
                    % (
                        run_id,
                        exc,
                        "nothing was kept, so it can be registered again"
                        if gone
                        else "its partial copy remains in %s and health reports it" % stage,
                    )
                ) from exc
            self._finish(eid)
            return self.get(eid)

    def _repeat(self, row) -> dict:
        state = row["state"]
        if state == "deleting":
            raise Conflict("the experiment for run %s is being deleted" % row["source_run_id"])
        if state in ("pending", "failed"):
            self._recover_row(row)
        return self.get(row["id"])

    def _copy_in(self, plan: _Plan, source: Path, stage: Path) -> None:
        os.mkdir(stage)
        _write_file(stage / "run.json", plan.run_bytes)
        for rel in plan.files:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            sha, size = _copy_file(source / rel, target)
            expected = plan.files[rel]
            if sha != expected["sha256"] or size != expected["bytes"]:
                raise StoreError("%s changed while it was being copied" % rel)
            self._checkpoint("copy")
        _fsync_tree(stage)

    def _insert_pending(self, eid: str, run_id: str, name: str, who: str, plan: _Plan) -> None:
        facts = plan.facts
        now = self._now()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO experiments (id, source_run_id, name, annotations, state, "
                "model_family, initiator, created_utc, updated_utc, run_created_utc, "
                "dataset_id, dataset_hash, task, target_column, production_strategy, "
                "artifact_path, target, splits, production, repeat_configs, feature_schema, "
                "metrics, validation_trace, versions, checksums, run_json) "
                "VALUES (?, ?, ?, '{}', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    eid,
                    run_id,
                    name,
                    facts["model_family"],
                    who,
                    now,
                    now,
                    facts["run_created_utc"],
                    facts["dataset_id"],
                    facts["dataset_hash"],
                    facts["task"],
                    facts["target_column"],
                    facts["production_strategy"],
                    "artifacts/" + eid,
                    *(
                        json.dumps(facts[k])
                        for k in (
                            "target",
                            "splits",
                            "production",
                            "repeat_configs",
                            "feature_schema",
                            "metrics",
                            "validation_trace",
                            "versions",
                        )
                    ),
                    json.dumps(plan.checksums),
                    json.dumps(plan.run),
                ),
            )
            self._checkpoint("insert_pending")

    def _finish(self, eid: str) -> None:
        """From a pending row with a verified staging directory to ``ready``."""
        try:
            self._checkpoint("rename")
            self._rename_into_place(eid)
        except Exception as exc:
            self._flag(eid, "the rename into artifacts/ failed: %s" % exc)
            raise StoreError(
                "experiment %s is %s: its verified copy is in %s; recover() finishes it"
                % (eid, "failed", self.staging / eid)
            ) from exc
        try:
            self._mark_ready(eid)
        except Exception as exc:
            self._flag(eid, "marking it ready failed: %s" % exc)
            raise StoreError(
                "experiment %s is failed: its files are in place in %s but the record could "
                "not be marked ready (%s); recover() finishes it" % (eid, self.artifacts / eid, exc)
            ) from exc

    def _rename_into_place(self, eid: str) -> None:
        self._layout()
        src, dst = self.staging / eid, self.artifacts / eid
        if os.path.lexists(dst):
            raise FileExistsError("%s already exists" % dst)
        os.rename(src, dst)
        _fsync_dir(self.artifacts)
        _fsync_dir(self.staging)

    def _mark_ready(self, eid: str) -> None:
        with self._write() as conn:
            done = conn.execute(
                "UPDATE experiments SET state = 'ready', state_detail = NULL, updated_utc = ? "
                "WHERE id = ? AND state IN ('pending', 'failed')",
                (self._now(), eid),
            )
            if done.rowcount != 1:
                raise StoreError("experiment %s is not pending" % eid)
            self._checkpoint("mark_ready")

    def _flag(self, eid: str, detail: str) -> None:
        """Say in the row why a registration stopped; best effort, since the
        reason it stopped may be the database.
        """
        with contextlib.suppress(Exception), self._write() as conn:
            conn.execute(
                "UPDATE experiments SET state = 'failed', state_detail = ?, updated_utc = ? "
                "WHERE id = ? AND state IN ('pending', 'failed')",
                (detail, self._now(), eid),
            )

    def _discard_stage(self, eid: str) -> bool:
        """Remove the staging directory this call itself made. True if it is gone."""
        try:
            self._remove_owned(self.staging, eid)
        except Exception:
            return False
        return True

    # -- leases ------------------------------------------------------------
    @contextlib.contextmanager
    def lease(self, experiment_id: str) -> Iterator[Path]:
        """Use a ready experiment's files: yields its artifact directory and
        holds a counted lease so :meth:`delete` waits until it is released.
        """
        eid = self._check_id(experiment_id)
        shared = self._shared
        with shared.cond:
            row = self._row(eid)
            if row is None:
                raise NotFound("no such experiment %s" % eid)
            if row["state"] != "ready":
                raise Conflict("experiment %s is %s, not ready" % (eid, row["state"]))
            found = _inspect_tree(self.artifacts / eid, json.loads(row["checksums"]), deep=False)
            if not found.ok:
                raise Conflict("experiment %s has damaged files: %s" % (eid, found.describe()))
            shared.leases[eid] = shared.leases.get(eid, 0) + 1
        try:
            yield self.artifacts / eid
        finally:
            with shared.cond:
                shared.leases[eid] -= 1
                if shared.leases[eid] <= 0:
                    del shared.leases[eid]
                shared.cond.notify_all()

    def _leases(self, eid: str) -> int:
        with self._shared.cond:
            return self._shared.leases.get(eid, 0)

    # -- deleting ----------------------------------------------------------
    def delete(self, experiment_id: str, *, timeout: float | None = None) -> dict:
        """Remove an experiment's row and its artifact directory for good.

        Marks it ``deleting`` first (so it is no longer selectable and no new
        lease starts), waits up to ``timeout`` seconds (default
        ``lease_timeout``) for leases to end, removes only
        ``artifacts/<id>`` and ``staging/<id>`` -- paths re-derived from the
        id, never read from the database, never through a symlink -- and then
        the row. The report keeps each outcome apart::

            {"id", "complete", "state": "deleted"|"deleting",
             "artifact_dir"|"staging_dir": "removed"|"already_absent"|"failed"|"skipped",
             "row": "removed"|"retained"|"failed", "leases": int, "errors": [...]}

        Anything short of ``complete`` leaves the record ``deleting`` and the
        call can simply be made again.
        """
        eid = self._check_id(experiment_id)
        wait = self.lease_timeout if timeout is None else timeout
        with self._shared.mutate, self._write() as conn:
            row = self._row(eid, conn)
            if row is None:
                raise NotFound("no such experiment %s" % eid)
            if row["state"] != "deleting":
                conn.execute(
                    "UPDATE experiments SET state = 'deleting', state_detail = NULL, "
                    "updated_utc = ? WHERE id = ?",
                    (self._now(), eid),
                )
        if not self._wait_for_leases(eid, wait):
            return {
                "id": eid,
                "complete": False,
                "state": "deleting",
                "artifact_dir": "skipped",
                "staging_dir": "skipped",
                "row": "retained",
                "leases": self._leases(eid),
                "errors": ["experiment is in use; its lease was not released within %gs" % wait],
            }
        with self._shared.mutate:
            return self._finish_deletion(eid)

    def _wait_for_leases(self, eid: str, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout)
        with self._shared.cond:
            while self._shared.leases.get(eid, 0) > 0:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return False
                self._shared.cond.wait(remaining)
        return True

    def _finish_deletion(self, eid: str) -> dict:
        """Steps two and three of a deletion; the mutation lock is held."""
        report: dict[str, Any] = {
            "id": eid,
            "complete": False,
            "state": "deleting",
            "artifact_dir": "skipped",
            "staging_dir": "skipped",
            "row": "retained",
            "leases": self._leases(eid),
            "errors": [],
        }
        with self._connect() as conn:
            row = self._row(eid, conn)
        if row is None:
            # A concurrent delete finished: nothing is left to remove.
            report.update(
                complete=True,
                state="deleted",
                artifact_dir="already_absent",
                staging_dir="already_absent",
                row="already_absent",
            )
            return report
        for key, parent in (("artifact_dir", self.artifacts), ("staging_dir", self.staging)):
            try:
                self._checkpoint("delete_files")
                report[key] = self._remove_owned(parent, eid)
            except Exception as exc:
                report[key] = "failed"
                report["errors"].append("%s: %s" % (key, exc))
        if report["errors"]:
            self._note(eid, "; ".join(report["errors"]))
            return report
        try:
            with self._write() as conn:
                conn.execute("DELETE FROM experiments WHERE id = ?", (eid,))
                self._checkpoint("delete_row")
        except Exception as exc:
            report["row"] = "failed"
            report["errors"].append("row: %s" % exc)
            return report
        report.update(complete=True, state="deleted", row="removed")
        return report

    def _note(self, eid: str, detail: str) -> None:
        with contextlib.suppress(Exception), self._write() as conn:
            conn.execute("UPDATE experiments SET state_detail = ? WHERE id = ?", (detail, eid))

    def _remove_owned(self, parent: Path, eid: str) -> str:
        """Remove ``parent/<eid>`` if it is a plain directory of ours.

        ``eid`` must be a uuid this module generated; the path is composed
        here and checked with ``lstat`` and ``realpath`` so a symlink, a file
        or anything resolving outside ``parent`` is refused, not followed.
        """
        if not _UUID.match(eid):
            raise StoreError("%r is not an experiment id; refusing to remove anything" % (eid,))
        self._layout()
        path = parent / eid
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return "already_absent"
        if stat.S_ISLNK(st.st_mode):
            raise StoreError("%s is a symlink; refusing to follow it" % path)
        if not stat.S_ISDIR(st.st_mode):
            raise StoreError("%s is not a directory; refusing to remove it" % path)
        if os.path.realpath(path) != os.path.join(os.path.realpath(parent), eid):
            raise StoreError("%s resolves outside the registry; refusing to remove it" % path)
        problems: list[str] = []
        shutil.rmtree(path, onerror=lambda _f, p, exc: problems.append("%s: %s" % (p, exc[1])))
        if problems or os.path.lexists(path):
            raise StoreError("could not remove %s (%s)" % (path, "; ".join(problems[:3])))
        _fsync_dir(parent)
        return "removed"

    # -- health and recovery -----------------------------------------------
    def health(self, *, deep: bool = True) -> dict:
        """What is wrong, without changing anything.

        ``pending``/``failed``/``deleting`` list registrations and deletions
        that are not finished; ``missing`` and ``corrupt`` are ``ready`` rows
        whose files are gone or do not match their checksums (``deep=False``
        checks presence and sizes only); ``orphans`` are directories nothing
        refers to. Orphans are only ever reported.
        """
        with self._shared.mutate, self._connect() as conn:
            rows = conn.execute("SELECT * FROM experiments ORDER BY created_utc, id").fetchall()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            try:
                database = conn.execute("PRAGMA quick_check").fetchone()[0]
            except sqlite3.Error as exc:
                database = str(exc)
            report: dict[str, Any] = {
                "schema_version": version,
                "database": database,
                "deep": deep,
                "counts": {s: 0 for s in STATES},
                "pending": [],
                "failed": [],
                "deleting": [],
                "missing": [],
                "corrupt": [],
                "invalid": [],
                "orphans": {"artifacts": [], "staging": []},
            }
            known: dict[str, set[str]] = {"artifacts": set(), "staging": set()}
            for row in rows:
                state = row["state"]
                report["counts"][state] = report["counts"].get(state, 0) + 1
                item = {
                    "id": row["id"],
                    "name": row["name"],
                    "source_run_id": row["source_run_id"],
                    "detail": row["state_detail"],
                }
                if not _UUID.match(row["id"]):
                    report["invalid"].append(
                        {**item, "detail": "the id is not a uuid; not touched"}
                    )
                    continue
                known["artifacts"].add(row["id"])
                if state != "ready":
                    known["staging"].add(row["id"])
                    report[state].append(item)
                    continue
                found = _inspect_tree(
                    self.artifacts / row["id"], json.loads(row["checksums"]), deep=deep
                )
                if found.directory != "ok" or found.missing:
                    report["missing"].append(
                        {**item, "directory": found.directory, "files": found.missing}
                    )
                if (
                    found.corrupt
                    or found.unexpected
                    or found.unsafe
                    or found.directory
                    not in (
                        "ok",
                        "missing",
                    )
                ):
                    report["corrupt"].append(
                        {
                            **item,
                            "files": found.corrupt,
                            "unexpected": found.unexpected,
                            "unsafe": found.unsafe,
                            "directory": found.directory,
                        }
                    )
        for key, parent in (("artifacts", self.artifacts), ("staging", self.staging)):
            with os.scandir(parent) as scan:
                entries = sorted(scan, key=lambda e: e.name)
            for entry in entries:
                if entry.name in known[key] and entry.is_dir(follow_symlinks=False):
                    continue
                kind = (
                    "symlink"
                    if entry.is_symlink()
                    else "directory"
                    if entry.is_dir(follow_symlinks=False)
                    else "file"
                )
                report["orphans"][key].append({"name": entry.name, "kind": kind})
        report["ok"] = not (
            report["pending"]
            or report["failed"]
            or report["deleting"]
            or report["missing"]
            or report["corrupt"]
            or report["invalid"]
            or report["orphans"]["artifacts"]
            or report["orphans"]["staging"]
            or report["database"] != "ok"
        )
        return report

    def recover(self) -> dict:
        """Finish what can be finished safely; keep and flag the rest.

        A ``pending`` or ``failed`` registration whose directory (in staging
        or already renamed) verifies against its recorded checksums is
        completed and marked ``ready``. A ``deleting`` experiment that nothing
        holds a lease on has its deletion finished. Everything else -- files
        that do not verify, both directories present, none present -- is left
        exactly as it is and named under ``retained``. Orphans are never
        touched. Returns ``{"completed": [id], "deleted": [id], "retained":
        [{"id", "state", "reason"}]}``.
        """
        report: dict[str, list] = {"completed": [], "deleted": [], "retained": []}
        with self._shared.mutate:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM experiments WHERE state != 'ready' ORDER BY created_utc, id"
                ).fetchall()
            for row in rows:
                eid = row["id"]
                if not _UUID.match(eid):
                    report["retained"].append(
                        {
                            "id": eid,
                            "state": row["state"],
                            "reason": "the id is not a uuid; not touched",
                        }
                    )
                elif row["state"] == "deleting":
                    if self._leases(eid):
                        report["retained"].append(
                            {"id": eid, "state": "deleting", "reason": "the experiment is in use"}
                        )
                        continue
                    done = self._finish_deletion(eid)
                    if done["complete"]:
                        report["deleted"].append(eid)
                    else:
                        report["retained"].append(
                            {"id": eid, "state": "deleting", "reason": "; ".join(done["errors"])}
                        )
                else:
                    reason = self._recover_row(row)
                    if reason is None:
                        report["completed"].append(eid)
                    else:
                        report["retained"].append(
                            {"id": eid, "state": self._row(eid)["state"], "reason": reason}
                        )
        return report

    def _recover_row(self, row) -> str | None:
        """Try to make a pending/failed row ``ready``. None on success, else why not."""
        eid = row["id"]
        if not _UUID.match(eid):
            return "the id is not a uuid; not touched"
        stage, art = self.staging / eid, self.artifacts / eid
        has_stage, has_art = os.path.lexists(stage), os.path.lexists(art)
        checksums = json.loads(row["checksums"])
        reason = None
        if has_stage and has_art:
            reason = "both staging/%s and artifacts/%s exist; resolve by hand" % (eid, eid)
        elif not has_stage and not has_art:
            reason = "its files are gone: delete this record and register the run again"
        else:
            where = art if has_art else stage
            found = _inspect_tree(where, checksums, deep=True)
            if not found.ok:
                reason = "its files do not verify (%s); retained for inspection" % found.describe()
            elif has_stage:
                try:
                    self._rename_into_place(eid)
                except Exception as exc:
                    reason = "the rename into artifacts/ failed: %s" % exc
            if reason is None:
                try:
                    self._mark_ready(eid)
                except Exception as exc:
                    reason = "marking it ready failed: %s" % exc
        if reason is not None:
            self._flag(eid, reason)
        return reason


# -- records ----------------------------------------------------------------
def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "annotations": _loads(row["annotations"]),
        "state": row["state"],
        "state_detail": row["state_detail"],
        "model_family": row["model_family"],
        "initiator": row["initiator"],
        "created_utc": row["created_utc"],
        "updated_utc": row["updated_utc"],
        "source_run_id": row["source_run_id"],
        "dataset_id": row["dataset_id"],
        "dataset_hash": row["dataset_hash"],
        "task": row["task"],
        "target_column": row["target_column"],
        "production_strategy": row["production_strategy"],
        "artifact_path": row["artifact_path"],
        "target": _loads(row["target"]),
        "versions": _loads(row["versions"]),
    }


_HEADLINE_METRIC = {"regression": "rmse", "classification": "balanced_accuracy"}


def _headline(row) -> dict | None:
    """The one number a registry table shows for an experiment, and what it is of.

    Scaffold test RMSE (regression) or balanced accuracy (classification) when the
    scaffold strategy produced it; otherwise the random strategy's, and ``strategy``
    says which. ``group`` names what the number can be compared with -- the same
    task, target, units, strategy and metric -- so a table never sorts unlike scores
    together. ``status`` is ``ok``, ``undefined`` (no repeat defined it) or
    ``unavailable`` (neither strategy had a valid split); no number is invented.
    """
    task = row["task"]
    metric = _HEADLINE_METRIC.get(task)
    if metric is None:
        return None
    metrics = _loads(row["metrics"]) or {}
    target = _loads(row["target"]) or {}
    for strategy in ("scaffold", "random"):
        report = metrics.get(strategy)
        if not isinstance(report, Mapping) or report.get("status") != "ok":
            continue
        agg = (report.get("aggregate") or {}).get(metric)
        if not isinstance(agg, Mapping):
            continue
        n_defined, n_total = agg.get("n_defined") or 0, agg.get("n_total")
        mean = agg.get("mean")
        defined = n_defined > 0 and isinstance(mean, (int, float))
        units = target.get("units") or ("pKi" if target.get("transform") == "pki" else None)
        return {
            "metric": metric,
            "strategy": strategy,
            "value": mean if defined else None,
            "n_defined": n_defined,
            "n_total": n_total,
            "status": "ok" if defined else "undefined",
            "group": "|".join(
                [task, str(row["target_column"]), str(units or ""), strategy, metric]
            ),
        }
    return {"metric": metric, "strategy": None, "value": None, "n_defined": 0,
            "n_total": None, "status": "unavailable", "group": None}


def _record(row) -> dict:
    out = _summary(row)
    out.update(
        run_created_utc=row["run_created_utc"],
        splits=_loads(row["splits"]),
        production=_loads(row["production"]),
        repeat_configs=_loads(row["repeat_configs"]),
        feature_schema=_loads(row["feature_schema"]),
        metrics=_loads(row["metrics"]),
        validation_trace=_loads(row["validation_trace"]),
        versions=_loads(row["versions"]),
        checksums=_loads(row["checksums"]),
        run=_loads(row["run_json"]),
    )
    return out


def _clean_text(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadRequest("the %s must be some text" % what)
    text = value.strip()
    if len(text) > limit or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise BadRequest("the %s must be at most %d characters on one line" % (what, limit))
    return text


def _clean_annotations(annotations: Any) -> dict[str, str]:
    if not isinstance(annotations, Mapping) or len(annotations) > 32:
        raise BadRequest("annotations are up to 32 notes, each a short text")
    out = {}
    for key, value in annotations.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BadRequest("annotations are text keys with text values")
        if not key.strip() or len(key) > 64 or len(value) > 2000:
            raise BadRequest("an annotation key is 1-64 characters and its value at most 2000")
        out[key] = value
    return out


# -- looking at a run directory ---------------------------------------------
@dataclass
class _Plan:
    """What a verified source run turned out to be."""

    run: dict
    run_bytes: bytes
    files: dict[str, dict]  # as run.json lists them (without run.json)
    checksums: dict[str, dict]  # the same plus run.json itself
    facts: dict[str, Any]


@dataclass
class _Found:
    """How a directory compares with the checksums it should match."""

    directory: str = "ok"  # ok | missing | symlink | not_a_directory
    missing: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.directory == "ok" and not (
            self.missing or self.corrupt or self.unexpected or self.unsafe
        )

    def describe(self) -> str:
        parts = []
        if self.directory != "ok":
            parts.append("directory %s" % self.directory.replace("_", " "))
        for label, items in (
            ("missing", self.missing),
            ("mismatched", self.corrupt),
            ("unexpected", self.unexpected),
            ("not plain files", self.unsafe),
        ):
            if items:
                parts.append(
                    "%s: %s" % (label, ", ".join(items[:5]) + (" ..." if len(items) > 5 else ""))
                )
        return "; ".join(parts) or "ok"


def _walk(base: Path) -> dict[str, str]:
    """Every entry under ``base`` by relative path: ``file``, ``symlink`` or
    ``other``. Symlinks are named, never entered.
    """
    found: dict[str, str] = {}
    stack = [("", base)]
    while stack:
        rel, directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = rel + "/" + entry.name if rel else entry.name
                if entry.is_symlink():
                    found[path] = "symlink"
                elif entry.is_dir(follow_symlinks=False):
                    stack.append((path, Path(entry.path)))
                elif entry.is_file(follow_symlinks=False):
                    found[path] = "file"
                else:
                    found[path] = "other"
                if len(found) > _MAX_FILES:
                    raise StoreError("more than %d files under %s" % (_MAX_FILES, base))
    return found


def _open_regular(path: Path):
    """The file's descriptor, refusing a symlink and anything not a regular file."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("%s is not a regular file" % path)
    return os.fdopen(fd, "rb")


def _digest(path: Path) -> tuple[str, int]:
    sha, size = hashlib.sha256(), 0
    with _open_regular(path) as handle:
        while chunk := handle.read(_CHUNK):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def _copy_file(src: Path, dst: Path) -> tuple[str, int]:
    """Copy bytes, durably, returning the digest of what was read."""
    sha, size = hashlib.sha256(), 0
    with _open_regular(src) as fin, open(dst, "xb") as fout:
        while chunk := fin.read(_CHUNK):
            sha.update(chunk)
            size += len(chunk)
            fout.write(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    return sha.hexdigest(), size


def _write_file(path: Path, data: bytes) -> None:
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _fsync_tree(root: Path) -> None:
    for directory, _, _ in os.walk(root):
        _fsync_dir(Path(directory))


def _inspect_tree(
    base: Path,
    expected: Mapping[str, Mapping[str, Any]],
    *,
    deep: bool,
    ignore: Sequence[str] = (),
) -> _Found:
    """Compare a directory with ``{relative path: {sha256, bytes}}``.

    Symlinks and special files are ``unsafe`` and never followed. ``deep``
    reads every file for its checksum; otherwise presence and size are checked.
    """
    try:
        st = os.lstat(base)
    except FileNotFoundError:
        return _Found(directory="missing")
    except OSError:
        return _Found(directory="not_a_directory")
    if stat.S_ISLNK(st.st_mode):
        return _Found(directory="symlink")
    if not stat.S_ISDIR(st.st_mode):
        return _Found(directory="not_a_directory")
    out = _Found()
    try:
        actual = _walk(base)
    except OSError as exc:
        out.unsafe.append("(unreadable: %s)" % exc)
        return out
    for path, kind in sorted(actual.items()):
        if path in ignore:
            continue
        if kind != "file":
            out.unsafe.append(path)
        elif path not in expected:
            out.unexpected.append(path)
    for path, meta in sorted(expected.items()):
        kind = actual.get(path)
        if kind is None:
            out.missing.append(path)
        elif kind == "file":
            try:
                if deep:
                    sha, size = _digest(base / path)
                    bad = size != meta["bytes"] or sha != meta["sha256"]
                else:
                    bad = os.lstat(base / path).st_size != meta["bytes"]
            except OSError:
                bad = True
            if bad:
                out.corrupt.append(path)
    return out


def _clean_files(files: Any) -> dict[str, dict]:
    if not isinstance(files, dict) or not files:
        raise BadRequest("run.json lists no files")
    out = {}
    for key, meta in files.items():
        parts = key.split("/") if isinstance(key, str) else []
        if (
            not parts
            or len(key) > 512
            or key.startswith("/")
            or "\\" in key
            or "\0" in key
            or any(p in ("", ".", "..") for p in parts)
        ):
            raise BadRequest("run.json lists an unsafe path %r" % (key,))
        if key == "run.json":
            raise BadRequest("run.json cannot list itself")
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("sha256"), str)
            or not _SHA256.match(meta["sha256"])
            or type(meta.get("bytes")) is not int
            or meta["bytes"] < 0
        ):
            raise BadRequest("run.json has no valid sha256 and size for %s" % key)
        out[key] = {"sha256": meta["sha256"], "bytes": meta["bytes"]}
    names = set(out)
    for key in out:
        parts = key.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise BadRequest("run.json lists %s as both a file and a directory" % key)
    missing = [f for f in REQUIRED_FILES if f not in out]
    if missing:
        raise BadRequest("run.json does not list the run's own files: %s" % ", ".join(missing))
    return out


def _inspect_source(run_dir: Path) -> _Plan:
    """Verify a run directory the caller has pinned, and read what it says.

    ``Conflict`` for a run that is not a completed one, ``BadRequest`` for a
    directory that is not what a completed run is: a symlink, a file that
    ``run.json`` does not list (or the reverse), a checksum that differs.
    """
    try:
        st = os.lstat(run_dir)
    except OSError:
        raise BadRequest("the run directory %s does not exist" % run_dir) from None
    if not stat.S_ISDIR(st.st_mode):
        raise BadRequest("the run directory %s is not a directory (or is a symlink)" % run_dir)
    try:
        with _open_regular(run_dir / "run.json") as handle:
            raw = handle.read(_MAX_RUN_JSON + 1)
    except FileNotFoundError:
        raise Conflict("the run has no run.json: it did not complete") from None
    except OSError as exc:
        raise BadRequest("run.json cannot be read: %s" % exc) from exc
    if len(raw) > _MAX_RUN_JSON:
        raise BadRequest("run.json is larger than %d bytes" % _MAX_RUN_JSON)

    def refuse(constant: str):
        raise ValueError("%s is not valid JSON" % constant)

    try:
        run = json.loads(raw.decode("utf-8"), parse_constant=refuse)
    except ValueError as exc:
        raise BadRequest("run.json is not valid JSON: %s" % exc) from exc
    if not isinstance(run, dict) or run.get("schema") != RUN_SCHEMA:
        raise BadRequest("run.json is not a %s document" % RUN_SCHEMA)
    if run.get("status") != "completed":
        raise Conflict("the run's status is %r, not 'completed'" % (run.get("status"),))
    files = _clean_files(run.get("files"))
    facts = _facts(run)
    checksums = {
        "run.json": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
        **files,
    }
    ignore = tuple(t for t in _TRANSIENT if t not in files)
    found = _inspect_tree(run_dir, checksums, deep=True, ignore=ignore)
    if not found.ok:
        raise BadRequest("the run directory does not match run.json: %s" % found.describe())
    return _Plan(run=run, run_bytes=raw, files=files, checksums=checksums, facts=facts)


def _facts(run: dict) -> dict[str, Any]:
    """What the registry denormalises out of ``run.json``."""

    def section(key: str) -> dict:
        value = run.get(key)
        if not isinstance(value, dict):
            raise BadRequest("run.json has no %r section" % key)
        return value

    dataset, target, production = section("dataset"), section("target"), section("production")
    strategies, versions = section("strategies"), section("versions")
    feature_schema = section("feature_schema")
    family = run.get("model_family")
    if not isinstance(family, str) or not family:
        raise BadRequest("run.json names no model_family")
    if not isinstance(dataset.get("content_hash"), str) or not dataset["content_hash"]:
        raise BadRequest("run.json's dataset has no content_hash")
    if target.get("task") not in ("regression", "classification"):
        raise BadRequest("run.json's target task is %r" % (target.get("task"),))
    if not isinstance(production.get("strategy"), str) or not production["strategy"]:
        raise BadRequest("run.json's production section names no strategy")
    splits: dict[str, Any] = {
        "algorithm": versions.get("split_algorithm"),
        "scaffold_policy": versions.get("scaffold_policy"),
        "seed": run.get("seed"),
        "strategies": {},
    }
    repeat_configs: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    trace: dict[str, Any] = {"production": production, "strategies": {}}
    for name, strategy in strategies.items():
        if not isinstance(strategy, dict):
            raise BadRequest("run.json's strategy %r is not an object" % name)
        repeats = [r for r in strategy.get("repeats") or [] if isinstance(r, dict)]
        splits["strategies"][name] = {
            "status": strategy.get("status"),
            "unavailable": strategy.get("unavailable"),
            "seeds": [r.get("seed") for r in repeats],
        }
        repeat_configs[name] = [
            {"repeat": r.get("repeat"), "seed": r.get("seed"), "config": r.get("config")}
            for r in repeats
        ]
        metrics[name] = {
            "status": strategy.get("status"),
            "unavailable": strategy.get("unavailable"),
            "aggregate": strategy.get("aggregate"),
            "repeats": [
                {"repeat": r.get("repeat"), "test": r.get("test"), "undefined": r.get("undefined")}
                for r in repeats
            ],
        }
        trace["strategies"][name] = [
            {"repeat": r.get("repeat"), "validation": r.get("validation")} for r in repeats
        ]
    created = run.get("created_utc")
    return {
        "model_family": family,
        "run_created_utc": created if isinstance(created, str) else None,
        "dataset_id": dataset.get("id") if isinstance(dataset.get("id"), str) else None,
        "dataset_hash": dataset["content_hash"],
        "task": target["task"],
        "target_column": target.get("column") if isinstance(target.get("column"), str) else None,
        "production_strategy": production["strategy"],
        "target": target,
        "splits": splits,
        "production": production,
        "repeat_configs": repeat_configs,
        "feature_schema": feature_schema,
        "metrics": metrics,
        "validation_trace": trace,
        "versions": versions,
    }
