"""The session's output directory: every artifact its runs produced, on disk.

A page asks for a project the first time it does something that produces a
file. From then on each run writes its own artifacts into it -- the structure
it ran on, the prepared PDB and its report, the pockets with their alpha
spheres, the generated molecules -- so results outlive the job directories
(pruned to the last ``job_limit``) and the temp tree (removed when the server
stops). :meth:`Project.copy_to` then puts the whole project wherever the user
asks for it.

The project is a plain directory of files other programs can read, plus a
``manifest.json`` saying what produced each one. Nothing reads a project back
in: it is output, and the viewer's own reopenable state is ``session.json``,
which the page writes into it when saving.

I/O layer: this module knows the filesystem and nothing of HTTP, of the
domain, or of any tool.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .errors import BadRequest, NotFound

__all__ = [
    "Project",
    "ProjectStore",
    "safe_name",
    "safe_relpath",
    "resolve_destination",
    "free_dir",
    "csv_text",
]

_ALLOWED = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(name: str, fallback: str = "session") -> str:
    """One path component, from text that may be anything.

    Leading dots go too: ``..`` is all allowed characters, and as a directory
    name it is the parent.
    """
    out = re.sub(r"-{2,}", "-", _ALLOWED.sub("-", str(name)))
    return out.strip("-.")[:48].strip("-.") or fallback


def safe_relpath(relpath: str) -> Path:
    """A path inside a project, from a relative path this package composed.

    Artifact paths are built here, not by a request, but they carry structure
    tokens and pocket method names that came from one -- so every component is
    checked rather than trusted.
    """
    text = str(relpath).replace("\\", "/")
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or text.startswith("/") or any(p == ".." or p != safe_name(p, "") for p in parts):
        raise BadRequest("bad artifact path %r" % relpath)
    return Path(*parts)


def csv_text(rows: Iterable[Mapping[str, Any]], columns: Iterable[str]) -> str:
    """Rows of a table as CSV, for a reader that is a spreadsheet or pandas.

    A missing value is an empty field rather than the word ``None``, which is
    what a numeric column needs if it is to be read back as numbers.
    """
    cols = list(columns)
    out = [",".join(cols)]
    for row in rows:
        out.append(",".join(_csv_value(row.get(c)) for c in cols))
    return "\n".join(out) + "\n"


def _csv_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return '"%s"' % text.replace('"', '""') if any(c in text for c in ',"\n\r') else text


def free_dir(dest: Path) -> Path:
    """``dest``, or ``dest-2``, ``-3`` ... when it is there with something in
    it. A directory someone else's work is in is never written into.
    """
    out, n = dest, 1
    while out.is_dir() and any(out.iterdir()):
        n += 1
        out = dest.with_name(dest.name + "-" + str(n))
    return out


def resolve_destination(path: str, forbid: Iterable[os.PathLike] = ()) -> Path:
    """Where the user asked for a project to be written.

    It is their own machine and their own server, so any path is allowed --
    but the parent has to exist (a typo should not create a tree of
    directories), a file is not a directory, and the server's own working
    tree is refused, since copying a project into the place it is copied from
    would race with the runs still writing there.
    """
    text = str(path or "").strip()
    if not text:
        raise BadRequest("where should the project be saved? Give a directory path")
    dest = Path(os.path.normpath(os.path.expanduser(text)))
    if not dest.is_absolute():
        raise BadRequest("give an absolute path, or one starting with ~, not %r" % text)
    parent = Path(os.path.realpath(dest.parent))
    if not parent.is_dir():
        raise BadRequest("there is no directory %s to save into" % dest.parent)
    if dest.exists() and not dest.is_dir():
        raise BadRequest("%s is a file, not a directory" % dest)
    real = parent / dest.name
    for root in forbid:
        r = Path(os.path.realpath(root))
        if r == real or r in real.parents:
            raise BadRequest(
                "%s is inside the server's own working tree -- pick somewhere else" % dest
            )
    return dest


class Project:
    """One session's artifacts, in one directory."""

    def __init__(self, project_id: str, path: Path, api: int = 0):
        self.id = project_id
        self.path = path
        self._lock = threading.Lock()
        self.path.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.is_file():
            self._write_manifest(
                {"project": self.id, "api": api, "created": _now(), "artifacts": []}
            )

    def __repr__(self) -> str:
        return "Project(%r, %r)" % (self.id, str(self.path))

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    # -- writing ------------------------------------------------------------
    def put_text(self, relpath: str, text: str, **note) -> Path:
        """Write one artifact, and record what produced it."""
        out = self.path / safe_relpath(relpath)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        self.note(relpath, **note)
        return out

    def put_file(self, relpath: str, source: os.PathLike | str, **note) -> Path:
        """Copy a file a tool wrote into the project."""
        out = self.path / safe_relpath(relpath)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out)
        self.note(relpath, **note)
        return out

    def note(self, relpath: str, **fields) -> None:
        """Add one artifact to the manifest. Requests run in parallel, so the
        manifest is read, extended and rewritten under the project's lock.
        """
        entry = {"path": str(safe_relpath(relpath)).replace(os.sep, "/"), "when": _now()}
        entry.update({k: v for k, v in fields.items() if v is not None})
        with self._lock:
            manifest = self.manifest()
            artifacts = [a for a in manifest.get("artifacts", []) if a.get("path") != entry["path"]]
            artifacts.append(entry)
            manifest["artifacts"] = artifacts
            self._write_manifest(manifest)

    def manifest(self) -> dict:
        try:
            data = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("project", self.id)
        data.setdefault("artifacts", [])
        return data

    def _write_manifest(self, manifest: dict) -> None:
        # Written beside and moved into place: a reader never sees half of it.
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=1) + "\n")
        os.replace(tmp, self.manifest_path)

    # -- reading and copying ------------------------------------------------
    def files(self) -> list[Path]:
        """Every artifact, relative to the project, in path order."""
        return sorted(
            p.relative_to(self.path)
            for p in self.path.rglob("*")
            if p.is_file() and p.suffix != ".tmp"
        )

    def describe(self) -> dict:
        files = self.files()
        return {
            "id": self.id,
            "path": str(self.path),
            "files": len(files),
            "bytes": sum((self.path / f).stat().st_size for f in files),
        }

    def copy_to(self, dest: Path, overwrite: bool = False) -> dict:
        """Copy the whole project to ``dest``, or to ``dest-2`` if something
        is there already and ``overwrite`` is not set. Nothing is deleted:
        with ``overwrite`` the files are written over whatever is in the way,
        and the rest of that directory is left alone.
        """
        with self._lock:
            target = dest if overwrite else free_dir(dest)
            target.mkdir(parents=True, exist_ok=True)
            written, total = [], 0
            for rel in self.files():
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.path / rel, out)
                written.append(str(rel).replace(os.sep, "/"))
                total += out.stat().st_size
            return {"path": str(target), "files": written, "bytes": total}


class ProjectStore:
    """The projects this server has made, under one root.

    Ids are the directory names, so a project is still there after a restart:
    a page that comes back with the id it had -- a restored session, say --
    goes on writing into the same directory rather than starting a second one
    beside it.
    """

    def __init__(self, root: os.PathLike | str, api: int = 0):
        self.root = Path(os.path.expanduser(str(root)))
        self.api = api
        self._open: dict[str, Project] = {}
        self._lock = threading.Lock()

    def create(self, name: str | None = None) -> Project:
        """A new project, named for the day and for whatever the page called
        it: ``2026-10-18-1HSG``.
        """
        stem = time.strftime("%Y-%m-%d") + "-" + safe_name(name or "session")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            project_id, n = stem, 1
            while (self.root / project_id).exists():
                n += 1
                project_id = "%s-%d" % (stem, n)
            project = Project(project_id, self.root / project_id, self.api)
            self._open[project_id] = project
            return project

    def get(self, project_id: str) -> Project | None:
        """The project, reopening one left on disk by an earlier run."""
        name = safe_name(project_id, "")
        if not name or name != str(project_id):
            return None
        with self._lock:
            project = self._open.get(name)
            if project is not None:
                return project
            if (self.root / name).is_dir():
                project = self._open[name] = Project(name, self.root / name, self.api)
                return project
            return None

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise NotFound("no such project %r" % project_id)
        return project


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
