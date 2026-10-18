"""What the viewer can do, independent of how it is asked.

Each public method is one use case -- buffer a structure, find pockets,
prepare, generate, report a job -- taking a parsed request from
``schemas.py`` and returning a JSON-ready dict. Nothing here knows about HTTP,
and nothing here knows which concrete tools are installed: those arrive
through the constructor (see ``server.build_service``), typed by the small
protocols below, so tests hand in fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from .buffer import PocketRun, StructureBuffer
from .detectors import DetectorRegistry
from .errors import BadRequest, Conflict, NotFound
from .schemas import (BufferRequest, GenerateRequest, JobQuery, PocketRequest,
                      PrepRequest)

__all__ = ["ViewerService", "API_VERSION", "Preparer", "Generator", "GenerationJob"]

log = logging.getLogger("proteinviewer")

#: Bumped when a response changes shape, so the page can tell a server that
#: predates it (and needs a restart) from one that is merely missing a tool.
API_VERSION = 2


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
    def __init__(self, buffer: StructureBuffer, detectors: DetectorRegistry,
                 prep: Preparer, generator: Generator):
        self.buffer = buffer
        self.detectors = detectors
        self.prep = prep
        self.generator = generator

    def status(self) -> dict:
        """Which tools this server can run. Probing some of them starts an
        interpreter, so the first call can take a few seconds."""
        return {"api": API_VERSION,
                "detectors": self.detectors.describe(),
                "prep": self.prep.describe(),
                "neat": self.generator.describe()}

    def buffer_structure(self, req: BufferRequest) -> dict:
        try:
            entry = self.buffer.put(req.token, req.data, req.fmt)
        except ValueError as exc:  # no atoms in it
            raise BadRequest(str(exc)) from None
        return {"token": entry.token, "nAtoms": len(entry.structure),
                "chains": entry.structure.chains(), "path": entry.path}

    def find_pockets(self, req: PocketRequest) -> dict:
        """Run the chosen detector against the buffered copy, never against
        posted text. A repeat of the last run on a structure is answered from
        its cache.

        Runs on one structure take turns (they share its scratch directory);
        runs on different structures, and every other request, do not wait --
        an MD-backed lacuna run takes a minute.
        """
        entry = self.buffer.require(req.token)
        detector = self.detectors.get(req.method)
        options = detector.options(req.raw)
        # The options are part of the identity of a result: the same
        # structure with another backend is a different run, not a cache hit.
        key = (detector.name, round(req.cutoff, 3), tuple(sorted(req.ligands)),
               tuple(sorted(options.items())))

        with entry.lock("pockets"):
            last = entry.pockets
            if last is not None and last.key == key:
                return {"token": entry.token, "cached": True,
                        "pockets": last.pockets, "meta": last.meta}
            result = detector.run(entry.structure, entry.workdir,
                                  cutoff=req.cutoff, ligand_resnames=req.ligands,
                                  **options)
            meta = result.meta()
            meta.update(ligandRemoved=sorted(req.ligands),
                        totalAtoms=len(entry.structure), method=detector.name)
            entry.pockets = run = PocketRun(key, [p.as_dict() for p in result], meta)

        log.info("%s %s: %d pockets in %.2fs", detector.name, entry.token,
                 len(run.pockets), result.elapsed)
        return {"token": entry.token, "cached": False,
                "pockets": run.pockets, "meta": run.meta}

    def prepare(self, req: PrepRequest) -> dict:
        """Prepare a buffered structure; returns the prepared PDB text, the
        report and the summary quickprep printed."""
        entry = self.buffer.require(req.token)
        with entry.lock("prep"):
            out = self.prep.run(entry.path, entry.workdir, req.ph,
                                name=entry.token, no_rebuild=req.no_rebuild)
        log.info("quickprep %s: %ss", entry.token, out["report"].get("seconds"))
        return out

    def start_generation(self, req: GenerateRequest) -> dict:
        """Start NEAT-POCKET on the requested pockets of the structure's last
        pocket run. Returns at once; the viewer polls :meth:`job_status`."""
        entry = self.buffer.require(req.token)
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
        return out
