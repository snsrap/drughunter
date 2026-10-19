"""What the viewer can do, independent of how it is asked.

Each public method is one use case -- buffer a structure, find pockets,
superpose, prepare, generate, report a job -- taking a parsed request from
``schemas.py`` and returning a JSON-ready dict. Nothing here knows about HTTP,
and nothing here knows which concrete tools are installed: those arrive
through the constructor (see ``server.build_service``), typed by the small
protocols below, so tests hand in fakes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from .buffer import BufferedStructure, PocketRun, StructureBuffer
from .errors import BadRequest, Conflict, NotFound
from .pockets import residue_list, spheres_pdb
from .project import (Project, ProjectStore, csv_text, resolve_destination,
                      safe_name)
from .schemas import (BufferRequest, GenerateRequest, JobQuery, PocketRequest,
                      PrepRequest, ProjectRequest, ProjectSaveRequest, SuperposeRequest)

__all__ = ["ViewerService", "API_VERSION", "PocketTools", "Preparer", "Generator",
           "GenerationJob"]

log = logging.getLogger("proteinviewer")

#: Bumped when a response changes shape, so the page can tell a server that
#: predates it (and needs a restart) from one that is merely missing a tool.
#: 4: /api/superpose; status lists additives and superposition; buffering no
#: longer counts atoms (the server parses no structures).
API_VERSION = 4

#: The molecule-table columns written to a project's CSV, in table order. The
#: depictions in the same rows are left out; they are in molecules.json.
MOLECULE_COLUMNS = ("rank", "i", "name", "formula", "mw", "heavy", "logp",
                    "tpsa", "qed", "rings", "rotb", "smiles")


class PocketTools(Protocol):
    """Pocket detection and superposition: pocketbench (pocketbench.py)."""

    def describe(self) -> dict: ...

    def detectors(self) -> dict[str, dict]: ...

    def additives(self) -> list[str]: ...

    def options(self, method: str, raw: Mapping[str, Any]) -> dict[str, Any]: ...

    def detect(self, method: str, path: str, workdir: str, *, cutoff: float,
               ligands: Iterable[str] = (), options: Mapping[str, Any] | None = None) -> dict: ...

    def superpose(self, mobile: str, reference: str, out: str, *,
                  on: Iterable[tuple[str, int, str]] = (),
                  mobile_on: Iterable[tuple[str, int, str]] = ()) -> dict: ...


class Preparer(Protocol):
    def describe(self) -> dict: ...

    def run(self, path: str, workdir: str, ph: float = 7.0, name: str | None = None,
            no_rebuild: Mapping[str, Iterable[int]] | None = None) -> dict: ...


class GenerationJob(Protocol):
    id: str
    state: str

    def progress(self) -> dict: ...

    def molecules(self) -> dict[str, str]: ...

    def table(self) -> list[dict]: ...


class Generator(Protocol):
    def describe(self) -> dict: ...

    def start(self, structure_path: str, pockets: list[dict], *, n: int,
              cfg: float, batch_size: int) -> GenerationJob: ...

    def get(self, job_id: str) -> GenerationJob | None: ...


class ViewerService:
    def __init__(self, buffer: StructureBuffer, pockets: PocketTools,
                 prep: Preparer, generator: Generator,
                 projects: ProjectStore | None = None,
                 work_root: os.PathLike | str | None = None):
        self.buffer = buffer
        self.pockets = pockets
        self.prep = prep
        self.generator = generator
        self.projects = projects
        self.work_root = Path(work_root) if work_root else None
        # Which project a generation job writes into, and the jobs already
        # written: a page polls a finished job more than once.
        self._job_projects: dict[str, str] = {}
        self._job_written: set[str] = set()
        self._lock = threading.Lock()

    def status(self) -> dict:
        """Which tools this server can run. Probing some of them starts an
        interpreter, so the first call can take a few seconds."""
        return {"api": API_VERSION,
                "detectors": self.pockets.detectors(),
                # What every detector drops on its own: the page does not
                # offer to delete these before a run.
                "additives": self.pockets.additives(),
                "superpose": self.pockets.describe(),
                "prep": self.prep.describe(),
                "neat": self.generator.describe(),
                "projects": {"available": self.projects is not None,
                             "root": str(self.projects.root) if self.projects else ""}}

    def buffer_structure(self, req: BufferRequest) -> dict:
        try:
            entry = self.buffer.put(req.token, req.data, req.fmt)
        except ValueError as exc:  # no atoms in it
            raise BadRequest(str(exc)) from None
        return {"token": entry.token, "fmt": entry.fmt, "path": entry.path}

    def find_pockets(self, req: PocketRequest) -> dict:
        """Run the chosen detector against the buffered copy, never against
        posted text. A repeat of the last run on a structure is answered from
        its cache.

        Runs on one structure take turns (they share its scratch directory);
        runs on different structures, and every other request, do not wait --
        an MD-backed lacuna run takes a minute.
        """
        entry = self.buffer.require(req.token)
        project = self._project(req.project)
        options = self.pockets.options(req.method, req.raw)
        # The options are part of the identity of a result: the same
        # structure with another backend is a different run, not a cache hit.
        key = (req.method, round(req.cutoff, 3), tuple(sorted(req.ligands)),
               tuple(sorted(options.items())))

        cached = True
        with entry.lock("pockets"):
            last = entry.pockets
            if last is not None and last.key == key:
                run = last
            else:
                out = self.pockets.detect(req.method, entry.path, entry.workdir,
                                          cutoff=req.cutoff, ligands=req.ligands,
                                          options=options)
                meta = dict(out.get("meta") or {})
                meta.update(ligandRemoved=sorted(req.ligands), method=req.method)
                entry.pockets = run = PocketRun(key, list(out.get("pockets") or []), meta)
                cached = False

        if not cached:
            log.info("%s %s: %d pockets in %ss", req.method, entry.token,
                     len(run.pockets), run.meta.get("elapsed"))
        # Written for a cache hit too: the project a page names holds the
        # results of every run it asked for, however they were answered.
        if project is not None:
            self._write_pockets(project, entry, req.method, run)
        return {"token": entry.token, "cached": cached,
                "pockets": run.pockets, "meta": run.meta}

    def superpose(self, req: SuperposeRequest) -> dict:
        """Superpose one buffered structure onto another; returns the moved
        text, written in the structure's own format, and the fit it rests on.
        Nothing is kept: the page owns the moved structure from here, and
        buffers it again under its own name when a run needs it."""
        mobile = self.buffer.require(req.token)
        reference = self.buffer.require(req.reference)
        out = os.path.join(mobile.workdir, "superposed." + mobile.fmt)
        with mobile.lock("superpose"):
            fit = self.pockets.superpose(mobile.path, reference.path, out,
                                         on=req.on, mobile_on=req.mobile_on)
            with open(out) as fh:
                data = fh.read()
        log.info("superposed %s onto %s: %.2f A over %s CA", mobile.token,
                 reference.token, fit.get("rmsd", 0), fit.get("n"))
        keep = ("rmsd", "n", "aligned", "paired", "ident", "related", "chains")
        return {"data": data, "fit": {k: fit.get(k) for k in keep}}

    def prepare(self, req: PrepRequest) -> dict:
        """Prepare a buffered structure; returns the prepared PDB text, the
        report and the summary quickprep printed."""
        entry = self.buffer.require(req.token)
        project = self._project(req.project)
        with entry.lock("prep"):
            out = self.prep.run(entry.path, entry.workdir, req.ph,
                                name=entry.token, no_rebuild=req.no_rebuild)
        log.info("quickprep %s: %ss", entry.token, out["report"].get("seconds"))
        if project is not None:
            self._write_prepared(project, entry, req.ph, out)
        return out

    def start_generation(self, req: GenerateRequest) -> dict:
        """Start NEAT-POCKET on the requested pockets of the structure's last
        pocket run. Returns at once; the viewer polls :meth:`job_status`."""
        entry = self.buffer.require(req.token)
        project = self._project(req.project)
        run = entry.pockets
        if run is None:
            raise Conflict("run pocket detection on this structure first")
        chosen = [{"rank": p["rank"],
                   "residues": [[g["chain"], int(r)] for g in p["sel"] for r in g["resi"]]}
                  for p in run.pockets if p["rank"] in req.ranks]
        if not chosen:
            raise Conflict("selected pockets are not in the current results")

        job = self.generator.start(entry.path, chosen, n=req.n, cfg=req.cfg,
                                   batch_size=req.batch_size)
        log.info("neat-pocket job %s: pockets %s, %d each",
                 job.id, sorted(req.ranks), req.n)
        if project is not None:
            # The job writes its molecules whenever it finishes, so the
            # project it belongs to is remembered now and used then.
            with self._lock:
                self._job_projects[job.id] = project.id
        return {"job": job.id, "ranks": sorted(req.ranks), "state": job.state}

    def job_status(self, q: JobQuery) -> dict[str, Any]:
        job = self.generator.get(q.job_id)
        if job is None:
            raise NotFound("no such job")
        out = job.progress()
        # Molecules are only sent once they exist, and only when asked for.
        if q.molecules:
            out["molecules"] = job.molecules()
        if q.table:
            out["table"] = job.table()
        if job.state == "done":
            self._write_molecules(job)
        return out

    # -- the session's output directory -------------------------------------
    def create_project(self, req: ProjectRequest) -> dict:
        """Start an output directory for this session. The page asks once,
        before the first run that produces a file, and names it after
        whatever is loaded."""
        if self.projects is None:
            raise Conflict("this server has no project directory")
        project = self.projects.create(req.name)
        log.info("project %s: %s", project.id, project.path)
        return project.describe()

    def save_project(self, req: ProjectSaveRequest) -> dict:
        """Copy the project to a path the user gave, with the page's own
        state written into it first."""
        project = self._project(req.project)
        if project is None:                       # parse() refuses an empty id
            raise NotFound("no such project")
        if req.session is not None:
            project.put_text("session.json", json.dumps(dict(req.session), indent=1),
                             kind="session", tool="viewer",
                             note="the viewer's own state; open it in the viewer")
        forbid = [p for p in (self.work_root, self.projects.root if self.projects else None)
                  if p is not None]
        dest = resolve_destination(req.path, forbid)
        out = project.copy_to(dest, overwrite=req.overwrite)
        log.info("project %s -> %s (%d files)", project.id, out["path"], len(out["files"]))
        return {"project": project.id, **out}

    def project_status(self, project_id: str) -> dict:
        project = self._project(project_id)
        if project is None:
            raise NotFound("no such project")
        return project.describe()

    def _project(self, project_id: str) -> Project | None:
        """The project a request names, or None when it names none. A page
        that comes back with an id from an earlier server run keeps that
        project, since the store reopens the directory."""
        if not project_id:
            return None
        if self.projects is None:
            raise NotFound("this server has no project directory")
        return self.projects.require(project_id)

    def _write_structure(self, project: Project, entry: BufferedStructure) -> str:
        """The exact text a run ran on. Everything else in the project is
        derived from one of these, so they go in beside the results."""
        rel = "structures/%s.%s" % (safe_name(entry.token), entry.fmt)
        project.put_file(rel, entry.path, kind="structure", structure=entry.token)
        return rel

    def _write_pockets(self, project: Project, entry: BufferedStructure,
                       tool: str, run: PocketRun) -> None:
        self._write_structure(project, entry)
        base = "pockets/%s.%s" % (safe_name(entry.token), safe_name(tool))
        common = {"structure": entry.token, "tool": tool,
                  "command": run.meta.get("command"), "pockets": len(run.pockets)}
        project.put_text(base + ".json",
                         json.dumps({"structure": entry.token, "meta": run.meta,
                                     "pockets": run.pockets}, indent=1),
                         kind="pockets", **common)
        project.put_text(base + ".residues.txt", residue_list(run.pockets, tool),
                         kind="pockets", **common)
        project.put_text(base + ".spheres.pdb", spheres_pdb(run.pockets, tool),
                         kind="pockets", **common)

    def _write_prepared(self, project: Project, entry: BufferedStructure,
                        ph: float, out: dict) -> None:
        self._write_structure(project, entry)
        base = "structures/%s-prep" % safe_name(entry.token)
        report = out.get("report") or {}
        common = {"structure": entry.token, "tool": "quickprep",
                  "ph": ph, "seconds": report.get("seconds")}
        project.put_text(base + ".pdb", out.get("data") or "", kind="prepared", **common)
        project.put_text(base + ".report.json", json.dumps(report, indent=1),
                         kind="report", **common)
        project.put_text(base + ".report.txt", out.get("summary") or "",
                         kind="report", **common)

    def _write_molecules(self, job: GenerationJob) -> None:
        """A finished job's molecules, once. The page polls a done job again
        whenever it reloads its results, and the files do not change."""
        with self._lock:
            project_id = self._job_projects.get(job.id)
            if project_id is None or job.id in self._job_written:
                return
            self._job_written.add(job.id)
        try:
            project = self.projects.require(project_id) if self.projects else None
            if project is None:
                return
            base = "molecules/%s" % safe_name(job.id)
            for rank, sdf in sorted(job.molecules().items()):
                project.put_text("%s/pocket-%s.sdf" % (base, safe_name(rank)), sdf,
                                 kind="molecules", tool="NEAT-POCKET", pocket=rank,
                                 molecules=sdf.count("$$$$"))
            rows = job.table()
            if rows:
                project.put_text(base + "/molecules.json", json.dumps(rows, indent=1),
                                 kind="properties", tool="NEAT-POCKET",
                                 note="properties and 2D depictions, RDKit's")
                project.put_text(base + "/molecules.csv",
                                 csv_text(rows, MOLECULE_COLUMNS),
                                 kind="properties", tool="NEAT-POCKET")
        except Exception:
            # Artifacts are a side effect: a job's results still go to the
            # page even if writing them down fails (a full disk, say).
            with self._lock:
                self._job_written.discard(job.id)
            log.exception("could not write job %s into project %s", job.id, project_id)
