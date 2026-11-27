"""Ligand datasets on disk: staged until the user says what is in them, then
kept, and reopened by id after the server restarts.

A dataset has two lives. *Staged* is an uploaded CSV, nothing more: it cannot
train and it expires after 24 hours if never resolved. *Ready* is a CSV with
the user's choices applied -- rows, verdicts, a content hash -- and is
immutable: resolving a ready dataset again makes a new one with a new id, so
anything that recorded an id (a split, a model) still means what it did.

    <root>/staged/<id>/source.csv, staged.json
    <root>/ready/<id>/source.csv, dataset.json, rows.json, depictions/...

The rows are written by ligandbench; this module never reads a molecule. It
is I/O: the filesystem and nothing of HTTP, of the domain, or of any tool.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .errors import Conflict, NotFound

__all__ = ["DatasetStore", "STAGED_TTL"]

#: How long an upload may wait to be resolved before it is removed, seconds.
STAGED_TTL = 24 * 3600
_ID = re.compile(r"^ds-[0-9a-f]{12}$")


class DatasetStore:
    """The datasets under one root."""

    def __init__(
        self,
        root: os.PathLike | str,
        *,
        staged_ttl: float = STAGED_TTL,
        clock: Callable[[], float] = time.time,
        cache: int = 4,
    ):
        self.root = Path(os.path.expanduser(str(root)))
        self.staged_ttl = staged_ttl
        self._clock = clock
        self._cache_size = cache
        self._lock = threading.Lock()
        self._meta: dict[str, dict] = {}
        self._rows: OrderedDict[str, list[dict]] = OrderedDict()

    # -- paths ------------------------------------------------------------
    @property
    def _staged(self) -> Path:
        return self.root / "staged"

    @property
    def _ready(self) -> Path:
        return self.root / "ready"

    def _check(self, dataset_id: str) -> str:
        if not isinstance(dataset_id, str) or not _ID.match(dataset_id):
            raise NotFound("no such dataset %r" % (dataset_id,))
        return dataset_id

    # -- staging ----------------------------------------------------------
    def stage(self, name: str, text: str) -> dict:
        """Keep an uploaded CSV until it is resolved or abandoned."""
        self.prune()
        dataset_id = "ds-" + uuid.uuid4().hex[:12]
        path = self._staged / dataset_id
        path.mkdir(parents=True)
        (path / "source.csv").write_text(text, encoding="utf-8", newline="")
        _write_json(path / "staged.json", {"name": name, "created": self._clock()})
        return {"dataset_id": dataset_id, "name": name}

    def staged(self, dataset_id: str) -> dict | None:
        """The staged record, or None when it is gone or never was."""
        self._check(dataset_id)
        self.prune()
        info = _read_json(self._staged / dataset_id / "staged.json")
        return {"dataset_id": dataset_id, **info} if info else None

    def cancel(self, dataset_id: str) -> None:
        """Delete a staged dataset's files. A resolved dataset is not cancelled."""
        self._check(dataset_id)
        if (self._ready / dataset_id).is_dir():
            raise Conflict("dataset %s is already resolved and is kept" % dataset_id)
        path = self._staged / dataset_id
        if not path.is_dir():
            raise NotFound("no staged dataset %s (it may have expired)" % dataset_id)
        shutil.rmtree(path, ignore_errors=True)

    def prune(self) -> int:
        """Remove staged datasets older than the limit; returns how many."""
        if not self._staged.is_dir():
            return 0
        cutoff = self._clock() - self.staged_ttl
        gone = 0
        for path in self._staged.iterdir():
            info = _read_json(path / "staged.json")
            created = info.get("created") if info else None
            if not isinstance(created, (int, float)):
                created = path.stat().st_mtime
            if created < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                gone += 1
        return gone

    # -- resolving ---------------------------------------------------------
    def source(self, dataset_id: str) -> tuple[Path, str, str]:
        """``(source CSV path, name, state)`` of a staged or ready dataset."""
        self._check(dataset_id)
        ready = self._ready / dataset_id
        if ready.is_dir():
            return ready / "source.csv", self.get(dataset_id)["name"], "ready"
        info = self.staged(dataset_id)
        if info is None:
            raise NotFound(
                "no such dataset %s (a staged upload expires after 24 hours)" % dataset_id
            )
        return self._staged / dataset_id / "source.csv", info["name"], "staged"

    def workdir(self) -> Path:
        """An empty directory for a resolution to write into, on the same
        filesystem as the store so it can be moved into place whole.
        """
        tmp = self.root / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="resolve-", dir=tmp))

    def commit(
        self, workdir: Path, *, name: str, source: Path, staged_id: str | None = None
    ) -> dict:
        """Make what a resolution wrote the ready dataset it describes.

        The staged dataset it came from, if any, keeps its id and is removed;
        otherwise (a ready dataset resolved again) the new one gets an id of its
        own and the old one is untouched. Returns the dataset's metadata.
        """
        dataset_id = staged_id or "ds-" + uuid.uuid4().hex[:12]
        meta = _read_json(workdir / "dataset.json")
        if not meta or not (workdir / "rows.json").is_file():
            raise NotFound("the resolution left no dataset behind")
        meta.update(
            dataset_id=dataset_id,
            state="ready",
            name=name,
            created=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self._clock())) + "Z",
        )
        shutil.copyfile(source, workdir / "source.csv")
        _write_json(workdir / "dataset.json", meta)
        self._ready.mkdir(parents=True, exist_ok=True)
        os.replace(workdir, self._ready / dataset_id)
        if staged_id:
            shutil.rmtree(self._staged / staged_id, ignore_errors=True)
        with self._lock:
            self._meta[dataset_id] = meta
        return meta

    # -- reading -----------------------------------------------------------
    def get(self, dataset_id: str) -> dict:
        """A ready dataset's metadata; it is reopened from disk after a restart."""
        self._check(dataset_id)
        with self._lock:
            meta = self._meta.get(dataset_id)
        if meta is not None:
            return meta
        meta = _read_json(self._ready / dataset_id / "dataset.json")
        if not meta:
            raise NotFound("no resolved dataset %s" % dataset_id)
        with self._lock:
            self._meta[dataset_id] = meta
        return meta

    def rows(self, dataset_id: str) -> list[dict]:
        """Every row of a ready dataset, in source order. A dataset never
        changes, so the last few are kept in memory.
        """
        self.get(dataset_id)
        with self._lock:
            rows = self._rows.get(dataset_id)
            if rows is not None:
                self._rows.move_to_end(dataset_id)
                return rows
        loaded = _read_json(self._ready / dataset_id / "rows.json")
        rows = loaded.get("rows") if loaded else None
        if rows is None:
            raise NotFound("dataset %s has lost its rows" % dataset_id)
        with self._lock:
            self._rows[dataset_id] = rows
            while len(self._rows) > self._cache_size:
                self._rows.popitem(last=False)
        return rows

    # -- depictions --------------------------------------------------------
    def _depiction_dir(self, dataset_id: str, version: str) -> Path:
        key = str(self.get(dataset_id).get("content_hash", ""))[:12] or "unhashed"
        return (
            self._ready / dataset_id / "depictions" / ("%s-v%s" % (key, re.sub(r"\W", "", version)))
        )

    def cached_depictions(
        self, dataset_id: str, version: str, row_ids: Iterable[int]
    ) -> dict[int, str]:
        """The drawings already made for these rows, by row id."""
        base = self._depiction_dir(dataset_id, version)
        out = {}
        for rid in row_ids:
            try:
                out[rid] = (base / ("%d.svg" % rid)).read_text(encoding="utf-8")
            except OSError:
                continue
        return out

    def put_depictions(self, dataset_id: str, version: str, svgs: Mapping[int, str]) -> None:
        """Keep drawings, by row id. Written beside and moved into place: a
        request reading at the same moment never sees half of one.
        """
        base = self._depiction_dir(dataset_id, version)
        base.mkdir(parents=True, exist_ok=True)
        for rid, svg in svgs.items():
            tmp = base / ("%d.svg.%s.tmp" % (rid, uuid.uuid4().hex[:6]))
            tmp.write_text(svg, encoding="utf-8")
            os.replace(tmp, base / ("%d.svg" % rid))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    # Written beside and moved into place: a reader never sees half of it.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)
