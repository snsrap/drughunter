"""What the viewer can do, independent of how it is asked.

Each public method is one use case -- buffer a structure, find pockets,
superpose, prepare, generate, report a job -- taking a parsed request from
``schemas.py`` and returning a JSON-ready dict. Nothing here knows about HTTP,
and nothing here knows which concrete tools are installed: those arrive
through the constructor (see ``server.build_service``), typed by the small
protocols below, so tests hand in fakes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from . import rowquery
from .buffer import BufferedStructure, PocketRun, StructureBuffer
from .dataset import DatasetStore
from .errors import BadRequest, Conflict, NotFound, ToolFailed, ToolUnavailable, ViewerError
from .experiments import STATES as EXPERIMENT_STATES
from .experiments import ExperimentStore, StoreError
from .pockets import residue_list, spheres_pdb
from .project import Project, ProjectStore, csv_text, resolve_destination, safe_name
from .schemas import (
    BufferRequest,
    DatasetIdRequest,
    DatasetResolveRequest,
    DatasetRowsRequest,
    DatasetSplitRequest,
    DatasetSummaryRequest,
    DatasetUploadRequest,
    ExperimentHealthQuery,
    ExperimentIdRequest,
    ExperimentQuery,
    ExperimentRegisterRequest,
    ExperimentUpdateRequest,
    PredictionQuery,
    PredictionSetRequest,
    PredictionStartRequest,
    GenerateRequest,
    JobQuery,
    PocketRequest,
    PrepRequest,
    ProjectRequest,
    ProjectSaveRequest,
    ScoreQuery,
    ScoreRequest,
    SuperposeRequest,
    TrainingQuery,
    TrainingStartRequest,
)

__all__ = [
    "ViewerService",
    "API_VERSION",
    "PocketTools",
    "Preparer",
    "Generator",
    "GenerationJob",
    "PoseScoring",
    "ScoringJob",
    "LigandTools",
    "Trainer",
    "DeleteIncomplete",
    "DeleteFailed",
]

log = logging.getLogger("proteinviewer")

#: Bumped when a response changes shape, so the page can tell a server that
#: predates it (and needs a restart) from one that is merely missing a tool.
#: 4: /api/superpose; status lists additives and superposition; buffering no
#: longer counts atoms (the server parses no structures).
#: 5: /api/score-poses and /api/score-job; status says what can score a pose.
#: 6: /api/dataset/* (upload, resolve, cancel, rows); status says whether ligand
#: datasets can be read.
API_VERSION = 7

#: The molecule-table columns written to a project's CSV, in table order. The
#: depictions in the same rows are left out; they are in molecules.json.
MOLECULE_COLUMNS = (
    "rank",
    "i",
    "name",
    "formula",
    "mw",
    "heavy",
    "logp",
    "tpsa",
    "qed",
    "rings",
    "rotb",
    # Filled in once the poses have been scored; an unscored run leaves them
    # empty, which is what a missing value has always looked like here.
    "score",
    "le",
    "rmsd_min",
    "scorer",
    "smiles",
)


#: What a page of rows carries: the row as stored, less nothing the page needs
#: to show it whole (every source cell, why it cannot train, what it was made into).
ROW_FIELDS = (
    "row_id",
    "record",
    "cells",
    "smiles",
    "structure_error",
    "target",
    "target_source",
    "qualifier",
    "censored",
    "prediction_eligible",
    "training_eligible",
    "exclusions",
)


def _row_view(row: Mapping[str, Any], id_index: int | None) -> dict:
    """A stored row as the page gets it, with the name it is listed by: the
    first non-structure text column, or the record number when that is blank.
    """
    out = {k: row[k] for k in ROW_FIELDS}
    name = row["cells"][id_index].strip() if id_index is not None else ""
    out["label"] = name or str(row["record"])
    return out


def _attach_predictions(rows: list[dict], sets: list[dict]) -> None:
    """Each row of a page with its predictions (by output key) and, for a set that could not
    predict it, the reason: joined by row id, so a number is on the row it was made for.
    """
    if not sets:
        return
    for row in rows:
        rid = str(row["row_id"])
        found: dict[str, Any] = {}
        why: dict[str, str] = {}
        for kept in sets:
            if rid in kept["values"]:
                found.update(kept["values"][rid])
            elif rid in kept["errors"]:
                why[kept["key"]] = kept["errors"][rid]
        row["predictions"] = found
        row["prediction_errors"] = why


def merge_scores(
    rows: list[dict], scores: Mapping[str, list[dict]], scorers: Mapping[str, dict]
) -> list[dict]:
    """The molecule rows with each pose's score joined onto it.

    A row is joined by ``(rank, i)``: ``i`` is the molecule's place in its
    pocket's SDF, which is the order the scorer was handed and the order it
    answered in. A row whose pose was not scored is left as it was -- an empty
    column, not a zero.
    """
    out = []
    for row in rows:
        rank = str(row.get("rank"))
        records = scores.get(rank) or []
        i = row.get("i")
        record = records[i] if isinstance(i, int) and 0 <= i < len(records) else None
        if record is None:
            out.append(dict(row))
            continue
        joined = dict(row)
        for key in ("score", "le", "rmsd_min"):
            joined[key] = record.get(key)
        joined["scorer"] = (scorers.get(rank) or {}).get("scorer", "")
        if record.get("error"):
            joined["scoreError"] = record["error"]
        out.append(joined)
    return out


class PocketTools(Protocol):
    """Pocket detection and superposition: pocketbench (pocketbench.py)."""

    def describe(self) -> dict: ...

    def detectors(self) -> dict[str, dict]: ...

    def additives(self) -> list[str]: ...

    def scorers(self) -> dict[str, dict]: ...

    def score_options(self, scorer: str, raw: Mapping[str, Any]) -> dict[str, Any]: ...

    def options(self, method: str, raw: Mapping[str, Any]) -> dict[str, Any]: ...

    def detect(
        self,
        method: str,
        path: str,
        workdir: str,
        *,
        cutoff: float,
        ligands: Iterable[str] = (),
        options: Mapping[str, Any] | None = None,
    ) -> dict: ...

    def superpose(
        self,
        mobile: str,
        reference: str,
        out: str,
        *,
        on: Iterable[tuple[str, int, str]] = (),
        mobile_on: Iterable[tuple[str, int, str]] = (),
    ) -> dict: ...


class Preparer(Protocol):
    def describe(self) -> dict: ...

    def run(
        self,
        path: str,
        workdir: str,
        ph: float = 7.0,
        name: str | None = None,
        no_rebuild: Mapping[str, Iterable[int]] | None = None,
    ) -> dict: ...


class GenerationJob(Protocol):
    id: str
    state: str

    def progress(self) -> dict: ...

    def molecules(self) -> dict[str, str]: ...

    def table(self) -> list[dict]: ...


class ScoringJob(Protocol):
    id: str
    state: str

    def progress(self) -> dict: ...

    def payload(self) -> dict: ...


class PoseScoring(Protocol):
    def describe(self) -> dict: ...

    def start(
        self,
        receptor: str,
        poses: dict[int, str],
        spheres: str,
        *,
        mode: str,
        padding: int,
        scorer: str = "",
        ligands: tuple[str, ...] = (),
        parent: str = "",
    ) -> ScoringJob: ...

    def get(self, job_id: str) -> ScoringJob | None: ...


class Generator(Protocol):
    def describe(self) -> dict: ...

    def start(
        self, structure_path: str, pockets: list[dict], *, n: int, cfg: float, batch_size: int
    ) -> GenerationJob: ...

    def get(self, job_id: str) -> GenerationJob | None: ...


class LigandTools(Protocol):
    """Reading ligand datasets: ligandbench (ligandbench.py)."""

    def describe(self) -> dict: ...

    def inspect(self, csv_path: str) -> dict: ...

    def resolve(self, csv_path: str, config: Mapping[str, Any], out_dir: str) -> dict: ...

    def depict(self, smiles: list[str]) -> dict: ...

    def split(self, dataset_dir: str, strategy: str, seed: int) -> dict: ...
    def summarize(self, dataset_dir: str) -> dict: ...

    def training_unavailable(self) -> str | None: ...

    def start_training(
        self, dataset_dir: str, run_dir: str, seed: int, strategy: str, profile: str
    ) -> Any: ...

    def training_outcome(self, returncode: int | None, stdout: str, stderr: str) -> dict: ...

    def prediction_unavailable(self) -> str | None: ...

    def model_compatibility(self, versions: Mapping[str, Any] | None, schema: str | None) -> str | None: ...

    def start_prediction(self, run_dir: str, dataset_dir: str, out_dir: str) -> Any: ...

    def prediction_outcome(self, returncode: int | None, stdout: str, stderr: str) -> dict: ...


class Trainer(Protocol):
    """Training jobs on ready datasets: the registry in training.py."""

    def describe(self) -> dict: ...

    def start(
        self,
        dataset_id: str,
        dataset_dir: str,
        seed: int,
        production_strategy: str,
        profile: str = "full",
    ) -> dict: ...

    def progress(self, run_id: str) -> dict: ...

    def run(self, run_id: str) -> dict: ...

    def cancel(self, run_id: str) -> dict: ...

    def pin(self, run_id: str) -> contextlib.AbstractContextManager[Path]: ...


class DeleteIncomplete(Conflict):
    """A deletion that is waiting on something in use. ``report`` says, part by
    part, what was and was not removed; the record is left ``deleting`` and the
    request can be made again.
    """

    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


class DeleteFailed(ViewerError):
    """A deletion that could not remove something it owns (the server's problem,
    not the client's). Reported like :class:`DeleteIncomplete`; retryable.
    """

    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


class ViewerService:
    def __init__(
        self,
        buffer: StructureBuffer,
        pockets: PocketTools,
        prep: Preparer,
        generator: Generator,
        scoring: PoseScoring | None = None,
        projects: ProjectStore | None = None,
        work_root: os.PathLike | str | None = None,
        version: str = "",
        ligands: LigandTools | None = None,
        datasets: DatasetStore | None = None,
        training: Trainer | None = None,
        experiments: ExperimentStore | None = None,
        experiments_error: str = "",
        predictions: Any = None,
    ):
        self.version = version
        self.training = training
        # Background jobs applying a registered model to a dataset (predictions.py); None
        # when there is no registry or no dataset directory to work with.
        self.predictions = predictions
        # None when the registry could not be opened; then the experiment routes
        # answer 503 with ``experiments_error`` and nothing else is affected.
        self.experiments = experiments
        self.experiments_error = experiments_error
        self.ligands = ligands
        self.datasets = datasets
        self.buffer = buffer
        self.pockets = pockets
        self.prep = prep
        self.generator = generator
        self.scoring = scoring
        self.projects = projects
        self.work_root = Path(work_root) if work_root else None
        # Which project a generation job writes into, and the jobs already
        # written: a page polls a finished job more than once.
        self._job_projects: dict[str, str] = {}
        self._job_written: set[str] = set()
        # A scoring job's project, the generation job it scored, and the ones
        # whose scores have been written down already.
        self._score_jobs: dict[str, tuple[str, str]] = {}
        self._scores_written: set[str] = set()
        self._lock = threading.Lock()
        # One summary at a time per dataset: the first request computes it and the
        # ones that arrived meanwhile read what it kept.
        self._summary_locks: dict[str, threading.Lock] = {}

    def status(self) -> dict:
        """Which tools this server can run. Probing some of them starts an
        interpreter, so the first call can take a few seconds.
        """
        return {
            "api": API_VERSION,
            "version": self.version,
            "detectors": self.pockets.detectors(),
            # What every detector drops on its own: the page does not
            # offer to delete these before a run.
            "additives": self.pockets.additives(),
            "superpose": self.pockets.describe(),
            "prep": self.prep.describe(),
            "neat": self.generator.describe(),
            "scoring": self.scoring.describe() if self.scoring else {"available": False},
            "projects": {
                "available": self.projects is not None,
                "root": str(self.projects.root) if self.projects else "",
            },
            "datasets": self._datasets_status(),
            "training": self.training.describe() if self.training else {"available": False},
            "experiments": self._experiments_status(),
            "predictions": self.predictions.describe() if self.predictions else {"available": False},
        }

    def _datasets_status(self) -> dict:
        info = self.ligands.describe() if self.ligands else {}
        return {
            **{k: info.get(k, "") for k in ("version", "rdkit", "depiction")},
            "available": bool(info.get("available")) and self.datasets is not None,
            "root": str(self.datasets.root) if self.datasets else "",
        }

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
        key = (
            req.method,
            round(req.cutoff, 3),
            tuple(sorted(req.ligands)),
            tuple(sorted(options.items())),
        )

        cached = True
        with entry.lock("pockets"):
            last = entry.pockets
            if last is not None and last.key == key:
                run = last
            else:
                out = self.pockets.detect(
                    req.method,
                    entry.path,
                    entry.workdir,
                    cutoff=req.cutoff,
                    ligands=req.ligands,
                    options=options,
                )
                meta = dict(out.get("meta") or {})
                meta.update(ligandRemoved=sorted(req.ligands), method=req.method)
                entry.pockets = run = PocketRun(key, list(out.get("pockets") or []), meta)
                cached = False

        if not cached:
            log.info(
                "%s %s: %d pockets in %ss",
                req.method,
                entry.token,
                len(run.pockets),
                run.meta.get("elapsed"),
            )
        # Written for a cache hit too: the project a page names holds the
        # results of every run it asked for, however they were answered.
        if project is not None:
            self._write_pockets(project, entry, req.method, run)
        return {"token": entry.token, "cached": cached, "pockets": run.pockets, "meta": run.meta}

    def superpose(self, req: SuperposeRequest) -> dict:
        """Superpose one buffered structure onto another; returns the moved
        text, written in the structure's own format, and the fit it rests on.
        Nothing is kept: the page owns the moved structure from here, and
        buffers it again under its own name when a run needs it.
        """
        mobile = self.buffer.require(req.token)
        reference = self.buffer.require(req.reference)
        out = os.path.join(mobile.workdir, "superposed." + mobile.fmt)
        with mobile.lock("superpose"):
            fit = self.pockets.superpose(
                mobile.path, reference.path, out, on=req.on, mobile_on=req.mobile_on
            )
            with open(out) as fh:
                data = fh.read()
        log.info(
            "superposed %s onto %s: %.2f A over %s CA",
            mobile.token,
            reference.token,
            fit.get("rmsd", 0),
            fit.get("n"),
        )
        keep = ("rmsd", "n", "aligned", "paired", "ident", "related", "chains")
        return {"data": data, "fit": {k: fit.get(k) for k in keep}}

    def prepare(self, req: PrepRequest) -> dict:
        """Prepare a buffered structure; returns the prepared PDB text, the
        report and the summary quickprep printed.
        """
        entry = self.buffer.require(req.token)
        project = self._project(req.project)
        with entry.lock("prep"):
            out = self.prep.run(
                entry.path, entry.workdir, req.ph, name=entry.token, no_rebuild=req.no_rebuild
            )
        log.info("quickprep %s: %ss", entry.token, out["report"].get("seconds"))
        if project is not None:
            self._write_prepared(project, entry, req.ph, out)
        return out

    def start_generation(self, req: GenerateRequest) -> dict:
        """Start NEAT-POCKET on the requested pockets of the structure's last
        pocket run. Returns at once; the viewer polls :meth:`job_status`.
        """
        entry = self.buffer.require(req.token)
        project = self._project(req.project)
        run = entry.pockets
        if run is None:
            raise Conflict("run pocket detection on this structure first")
        chosen = [
            {
                "rank": p["rank"],
                "residues": [[g["chain"], int(r)] for g in p["sel"] for r in g["resi"]],
            }
            for p in run.pockets
            if p["rank"] in req.ranks
        ]
        if not chosen:
            raise Conflict("selected pockets are not in the current results")

        job = self.generator.start(
            entry.path, chosen, n=req.n, cfg=req.cfg, batch_size=req.batch_size
        )
        log.info("neat-pocket job %s: pockets %s, %d each", job.id, sorted(req.ranks), req.n)
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

    # -- pose scoring --------------------------------------------------------
    def start_scoring(self, req: ScoreRequest) -> dict:
        """Score a finished generation job's poses in the pockets they were
        generated for. Returns at once; the viewer polls :meth:`score_status`.

        Scoring is its own run, asked for after the molecules are in the
        table -- never part of generation, which must hand over its molecules
        whether or not anything can score them.
        """
        if self.scoring is None:
            raise Conflict("this server has no pose scorer")
        job = self.generator.get(req.job)
        if job is None:
            raise NotFound("no such job")
        if job.state != "done":
            raise Conflict("the generation job has not finished")
        entry = self.buffer.require(req.token)
        run = entry.pockets
        if run is None:
            raise Conflict(
                "the pocket run these poses were generated for is gone: "
                "run pocket detection again, or score the files with "
                "`pocketbench score-poses`"
            )
        scorer = req.scorer or next(
            (n for n, s in self.pockets.scorers().items() if s.get("available")), ""
        )
        options = self.pockets.score_options(scorer, req.raw)

        poses = {
            int(rank): text
            for rank, text in job.molecules().items()
            if not req.ranks or int(rank) in req.ranks
        }
        if not poses:
            raise Conflict("none of the selected pockets have poses in this job")
        project = self._project(req.project)
        sjob = self.scoring.start(
            entry.path,
            poses,
            spheres_pdb(run.pockets, run.meta.get("method", "")),
            mode=options["mode"],
            padding=options["padding"],
            scorer=scorer,
            ligands=tuple(sorted(run.meta.get("ligandRemoved") or ())),
            parent=job.id,
        )
        log.info(
            "scoring job %s: %s %s on pockets %s of job %s",
            sjob.id,
            scorer or "scorer",
            options["mode"],
            sorted(poses),
            job.id,
        )
        with self._lock:
            self._score_jobs[sjob.id] = (project.id if project else "", job.id)
        return {"job": sjob.id, "ranks": sorted(poses), "state": sjob.state, **options}

    def score_status(self, q: ScoreQuery) -> dict[str, Any]:
        """A scoring job's progress, and its scores once it has finished."""
        if self.scoring is None:
            raise Conflict("this server has no pose scorer")
        sjob = self.scoring.get(q.job_id)
        if sjob is None:
            raise NotFound("no such job")
        out = sjob.progress()
        if sjob.state == "done":
            out.update(sjob.payload())
            self._write_scores(sjob)
        return out

    # -- ligand datasets ------------------------------------------------------
    def _store(self) -> DatasetStore:
        if self.datasets is None or self.ligands is None:
            raise Conflict("this server has no dataset directory")
        return self.datasets

    def upload_dataset(self, req: DatasetUploadRequest) -> dict:
        """Keep an uploaded CSV, staged, and say what is in it: its columns,
        and which of them might hold structures. It cannot train until the
        user has resolved it; abandoned, it expires after 24 hours.
        """
        store = self._store()
        staged = store.stage(req.name, req.data)
        dataset_id = staged["dataset_id"]
        try:
            found = self.ligands.inspect(str(store.source(dataset_id)[0]))
        except Exception:
            store.cancel(dataset_id)  # a file that cannot be read is not kept
            raise
        view = {
            "n_rows": found.get("n_rows"),
            "columns": found.get("columns", []),
            "structure_candidates": found.get("structure_candidates", []),
        }
        store.annotate(dataset_id, **view)
        log.info("dataset %s staged: %s, %s rows", dataset_id, req.name, view["n_rows"])
        return {"dataset_id": dataset_id, "state": "staged", "name": req.name, **view}

    def resolve_dataset(self, req: DatasetResolveRequest) -> dict:
        """Apply the user's choices. A staged dataset becomes ready under its
        own id; a ready one resolved again becomes a new dataset, so the old
        id keeps meaning what it meant.
        """
        store = self._store()
        source, name, state = store.source(req.dataset_id)
        work = store.workdir()
        try:
            self.ligands.resolve(str(source), req.config, str(work))
            meta = store.commit(
                work,
                name=name,
                source=source,
                staged_id=req.dataset_id if state == "staged" else None,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        log.info(
            "dataset %s ready: %s rows, %s trainable",
            meta["dataset_id"],
            meta["n_rows"],
            meta["n_training_eligible"],
        )
        return self._dataset_view(meta)

    def cancel_dataset(self, req: DatasetIdRequest) -> dict:
        self._store().cancel(req.dataset_id)
        return {"dataset_id": req.dataset_id, "state": "cancelled"}

    def dataset_status(self, req: DatasetIdRequest) -> dict:
        """A dataset by id: a ready one is reopened from disk after a restart, and a
        staged one comes back with its columns, still waiting to be resolved.
        """
        store = self._store()
        try:
            return self._dataset_view(store.get(req.dataset_id))
        except NotFound:
            staged = store.staged(req.dataset_id)
            if staged is None:
                raise
            return {
                "dataset_id": req.dataset_id,
                "state": "staged",
                "name": staged["name"],
                "n_rows": staged.get("n_rows"),
                "columns": staged.get("columns", []),
                "structure_candidates": staged.get("structure_candidates", []),
            }

    def dataset_rows(self, req: DatasetRowsRequest) -> dict:
        """One page of a ready dataset. Sorting and filtering happen before the
        page is cut, and only the rows in it are drawn.
        """
        store = self._store()
        meta = store.get(req.dataset_id)
        extra, sets = self._prediction_columns(req.dataset_id)
        columns = rowquery.schema(meta, extra)
        page, total = rowquery.run(
            columns,
            store.rows(req.dataset_id),
            filters=rowquery.parse_filters(columns, req.filters),
            sort=rowquery.parse_sort(columns, req.sort),
            offset=req.offset,
            limit=req.limit,
        )
        id_index = next(
            (c["index"] for c in meta["columns"] if c["name"] == meta.get("id_column")), None
        )
        rows = [_row_view(r, id_index) for r in page]
        _attach_predictions(rows, sets)
        if req.depict:
            self._depict(meta, rows)
        return {
            "dataset_id": req.dataset_id,
            "total": total,
            "offset": req.offset,
            "limit": req.limit,
            "rows": rows,
        }

    def dataset_splits(self, req: DatasetSplitRequest) -> dict:
        """Five repeated holdouts of a ready dataset, by one strategy and seed.

        Only a resolved dataset with a target can be split: a staged one has to
        be resolved first, and one without a target is not for training. The
        split is made by ligandbench from the dataset's stored rows; what comes
        back is its result -- ``ok`` with the repeats, or ``split_unavailable``
        with why, which is an answer and not an error -- with the dataset's id
        added. Nothing is stored: the caller keeps the result it is given.
        """
        store = self._store()
        try:
            meta = store.get(req.dataset_id)
        except NotFound:
            if store.staged(req.dataset_id) is not None:
                raise Conflict(
                    "dataset %s is staged, not resolved: choose its structure column and "
                    "target (resolve it) before splitting it" % req.dataset_id
                ) from None
            raise
        if not meta.get("trainable"):
            raise BadRequest(
                "dataset %s has no target, so it cannot be split for training; resolve it "
                "again with a target column" % req.dataset_id
            )
        # A ready dataset is a directory: source.csv beside the rows.json and
        # dataset.json that ligandbench reads.
        directory = store.source(req.dataset_id)[0].parent
        out = self.ligands.split(str(directory), req.strategy, req.seed)
        log.info(
            "dataset %s: %s split, seed %d: %s",
            req.dataset_id,
            req.strategy,
            req.seed,
            out.get("status"),
        )
        return {"dataset_id": req.dataset_id, **out}

    def dataset_summary(self, req: DatasetSummaryRequest) -> dict:
        """The dashboard's statistics for a ready dataset, in one answer: counts
        with their populations, duplicates, the target's histogram or class
        counts, scaffold concentration and missing cells. No row is in it.

        A ready dataset never changes, so what ligandbench computes is kept
        beside it, under the summary version ligandbench reports, and every
        later request -- from any page, after a restart -- reads that. A new
        version of the summary computes again and leaves the old file alone.
        """
        store = self._store()
        store.get(req.dataset_id)  # a staged or unknown id is NotFound here, as for its rows
        version = str(self.ligands.describe().get("summary", ""))
        with self._summary_locks.setdefault(req.dataset_id, threading.Lock()):
            summary = store.cached_summary(req.dataset_id, version)
            if summary is None:
                summary = self.ligands.summarize(str(store.directory(req.dataset_id)))
                store.put_summary(req.dataset_id, version, summary)
        return {"dataset_id": req.dataset_id, **summary}

    # -- training ------------------------------------------------------------
    def _trainer(self) -> Trainer:
        if self.training is None:
            raise ToolUnavailable("this server cannot train models", "ligandbench")
        return self.training

    def start_training(self, req: TrainingStartRequest) -> dict:
        """Queue a training run on a ready dataset; returns its progress, with the
        run's ``id``. Only a resolved dataset with a target can train: a staged one
        has to be resolved first (409), one without a target is refused (400), and
        both are refused before any process starts. A missing ligandbench or XGBoost
        is a 503, a full queue a 409.
        """
        trainer = self._trainer()
        store = self._store()
        try:
            meta = store.get(req.dataset_id)
        except NotFound:
            if store.staged(req.dataset_id) is not None:
                raise Conflict(
                    "dataset %s is staged, not resolved: choose its structure column and "
                    "target (resolve it) before training on it" % req.dataset_id
                ) from None
            raise
        if not meta.get("trainable"):
            raise BadRequest(
                "dataset %s has no target, so it cannot be trained on; resolve it "
                "again with a target column" % req.dataset_id
            )
        started = trainer.start(
            req.dataset_id,
            str(store.directory(req.dataset_id)),
            req.seed,
            req.production_strategy,
            req.profile,
        )
        log.info(
            "training %s: dataset %s, seed %d, %s production strategy, %s profile",
            started.get("id"),
            req.dataset_id,
            req.seed,
            req.production_strategy,
            req.profile,
        )
        return started

    def training_status(self, q: TrainingQuery) -> dict:
        return self._trainer().progress(q.run_id)

    def training_run(self, q: TrainingQuery) -> dict:
        """A completed run's ``run.json``: metadata only, never the model."""
        return self._trainer().run(q.run_id)

    def cancel_training(self, q: TrainingQuery) -> dict:
        return self._trainer().cancel(q.run_id)

    # -- experiments -----------------------------------------------------------
    def _registry(self) -> ExperimentStore:
        if self.experiments is None:
            raise ToolUnavailable(
                "the experiment registry is not available: %s"
                % (self.experiments_error or "it was not configured"),
                "experiments",
            )
        return self.experiments

    def _clean(self, value: Any, store: ExperimentStore, *paths: Any) -> Any:
        """``value`` with the server's directories taken out of every string in it: the
        registry root and the run directory a message may name. What is sent over HTTP
        shows an experiment's own relative path and nothing absolute.
        """
        roots = {str(store.root), os.path.realpath(store.root)}
        hidden = [(r, "<registry>") for r in roots] + [(str(p), "<run>") for p in paths]
        hidden = sorted((h for h in hidden if len(h[0]) > 1), key=lambda h: -len(h[0]))

        def scrub(v: Any) -> Any:
            if isinstance(v, str):
                for path, label in hidden:
                    v = v.replace(path, label)
                return v
            if isinstance(v, dict):
                return {k: scrub(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [scrub(x) for x in v]
            return v

        return scrub(value)

    @contextlib.contextmanager
    def _guarded(self, store: ExperimentStore, *paths: Any) -> Iterator[None]:
        """Registry failures as a client sees them: the error's own meaning and
        message (a 404, 409 or 400 from the store keep theirs), no server path in
        it, and the registry's own trouble -- an unreadable or corrupt database, a
        full disk -- as a ``StoreError`` with a message, never a stack trace.
        """
        try:
            yield
        except ViewerError as exc:
            exc.args = tuple(self._clean(a, store, *paths) for a in exc.args)
            raise
        except (sqlite3.Error, OSError) as exc:
            log.exception("experiment registry")
            raise StoreError(
                "the experiment registry could not be used (%s); check it with "
                "/api/experiments/health"
                % self._clean("%s: %s" % (type(exc).__name__, exc), store, *paths)
            ) from exc

    def _experiments_status(self) -> dict:
        store = self.experiments
        if store is None:
            return {"available": False, "reason": self.experiments_error or "not configured"}
        try:
            rows = store.list(selectable_only=False)
        except (sqlite3.Error, OSError, ViewerError) as exc:
            return {
                "available": False,
                "root": str(store.root),
                "reason": self._clean("%s: %s" % (type(exc).__name__, exc), store),
            }
        counts = {state: 0 for state in EXPERIMENT_STATES}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        selectable = sum(1 for row in rows if row["selectable"])
        problems = sum(1 for row in rows if row["state"] == "ready" and not row["selectable"])
        started = store.startup_health or {}
        orphans = started.get("orphans") or {}
        return {
            "available": True,
            "reason": "",
            "root": str(store.root),
            "counts": counts,
            "selectable": selectable,
            "problems": problems,
            # Cheap and lock-free: the rows and their files' presence. The full
            # check (checksums, orphans) is GET /api/experiments/health.
            "healthy": problems == 0
            and not (counts["pending"] or counts["failed"] or counts["deleting"]),
            "startup": {
                "ok": bool(started.get("ok", True)),
                "orphans": len(orphans.get("artifacts", [])) + len(orphans.get("staging", [])),
            },
        }

    def register_experiment(self, req: ExperimentRegisterRequest) -> dict:
        """Keep a completed training run: pin it against eviction, copy it into the
        registry, release the pin (on success and on failure alike). Idempotent on the
        run id: a repeat returns the same record, also once the job itself has been
        evicted. A run that is not completed is a 409 (running, failed, cancelled), an
        unknown or evicted one a 404.
        """
        store = self._registry()
        trainer = self._trainer()
        with self._guarded(store):
            try:
                with trainer.pin(req.run_id) as run_dir:
                    record = self._copy_run(store, run_dir, req)
            except NotFound:
                record = self._registered_run(store, req.run_id)
                if record is None:
                    raise
            log.info("experiment %s: run %s registered as %r", record["id"], req.run_id, req.name)
            return self._clean(record, store)

    def _copy_run(
        self, store: ExperimentStore, run_dir: Path, req: ExperimentRegisterRequest
    ) -> dict:
        """The copy, with the pin held by the caller. What the store refuses of the run
        itself (a bad ``run.json``, files that do not match it) is the run not being
        registrable (409): the request was checked at the edge.
        """
        with self._guarded(store, run_dir):
            try:
                return store.register(run_dir, req.run_id, req.name)
            except BadRequest as exc:
                raise Conflict("run %s cannot be registered: %s" % (req.run_id, exc)) from exc

    def _registered_run(self, store: ExperimentStore, run_id: str) -> dict | None:
        """The ready experiment already made from ``run_id``, if there is one."""
        for row in store.list(selectable_only=False):
            if row["source_run_id"] == run_id and row["state"] == "ready":
                return store.get(row["id"])
        return None

    def experiments_view(self, q: ExperimentQuery) -> dict:
        """One experiment (``id``), in any state, with both strategies' reports and the
        production model's facts; or the list of the selectable ones (``all`` lists
        every record, whatever its state).
        """
        store = self._registry()
        with self._guarded(store):
            if q.experiment_id:
                return self._clean(store.get(q.experiment_id), store)
            rows = store.list(selectable_only=not q.everything)
            for row in rows:
                row["usable"] = self._usable(row)
            return {"experiments": self._clean(rows, store)}

    def _usable(self, row: dict) -> dict:
        """Whether a model can be applied here, and why not: what the selector shows beside
        each experiment. A first look at what the registry recorded; starting a job checks
        the files themselves again.
        """
        if self.predictions is None:
            return {"ok": False, "reason": "predictions are not available on this server"}
        if not row.get("selectable"):
            return {"ok": False, "reason": "its files are missing or it is not ready"}
        why = self.ligands.model_compatibility(row.get("versions"), None)
        return {"ok": why is None, "reason": why or ""}

    def update_experiment(self, req: ExperimentUpdateRequest) -> dict:
        """Change an experiment's name and/or annotations (the whole set is replaced).
        Everything else is a fact of the run and was refused at the edge.
        """
        store = self._registry()
        with self._guarded(store):
            if req.name is not None:
                store.rename(req.experiment_id, req.name)
            if req.annotations is not None:
                store.set_annotations(req.experiment_id, dict(req.annotations))
            return self._clean(store.get(req.experiment_id), store)

    def delete_experiment(self, req: ExperimentIdRequest) -> dict:
        """Remove an experiment for good, and report each part apart. One that is in
        use past the wait is a 409 and one whose files or row could not be removed a
        500; both leave it ``deleting`` and the call can simply be made again.
        """
        store = self._registry()
        with self._guarded(store):
            report = self._clean(store.delete(req.experiment_id), store)
        log.info("experiment %s: delete %s", req.experiment_id, report["state"])
        if report["complete"]:
            return report
        if report["leases"] and report["row"] == "retained" and report["artifact_dir"] == "skipped":
            raise DeleteIncomplete(
                "experiment %s is in use: %s; it stays 'deleting', so try again when "
                "the use has ended" % (req.experiment_id, "; ".join(report["errors"])),
                report,
            )
        raise DeleteFailed(
            "experiment %s was not completely deleted (%s); it stays 'deleting' and the "
            "request can be repeated" % (req.experiment_id, "; ".join(report["errors"])),
            report,
        )

    def experiments_health(self, q: ExperimentHealthQuery) -> dict:
        """What is wrong with the registry, changing nothing (``deep=false`` skips
        re-hashing every file).
        """
        store = self._registry()
        with self._guarded(store):
            return self._clean(store.health(deep=q.deep), store)

    def recover_experiments(self) -> dict:
        """Finish what can be finished provably: a verified half-done registration, a
        deletion nothing is using. Everything else is kept and named; orphans are
        never touched. Only ever called by a request, never on its own.
        """
        store = self._registry()
        with self._guarded(store):
            return self._clean(store.recover(), store)

    def _dataset_view(self, meta: dict) -> dict:
        extra, sets = self._prediction_columns(meta["dataset_id"])
        return {
            **meta,
            "schema": [c.describe() for c in rowquery.schema(meta, extra)],
            "operators": {k: list(v) for k, v in rowquery.OPERATORS.items()},
            "predictions": [s["view"] for s in sets],
        }

    # -- applying a registered model to a dataset ---------------------------------
    def _predictor(self):
        if self.predictions is None:
            raise Conflict(
                "predictions are not available: %s"
                % (self.experiments_error or "this server has no experiment registry or no dataset directory")
            )
        return self.predictions

    def _prediction_columns(self, dataset_id: str) -> tuple[list[rowquery.Column], list[dict]]:
        """The kept sets of predictions of a dataset as query columns, and the sets.

        Each output is a column that sorts and filters like any other, over every row,
        before a page is cut. A row the model could not predict is missing in it, and
        its reason travels with the row. Sets whose experiment was deleted stay, labelled
        as such: they are what that model said at the time.
        """
        store = self._store()
        columns: list[rowquery.Column] = []
        sets: list[dict] = []
        for key in store.prediction_keys(dataset_id):
            kept = store.prediction(dataset_id, key)
            if kept is None:
                continue
            exp = kept["experiment"]
            deleted = self._experiment_deleted(exp["id"], exp["checksum"])
            values = kept["values"]
            outputs = []
            for out in kept["outputs"]:
                label = out["label"] + (" — source model deleted" if deleted else "")
                meta = {
                    "set": key,
                    "output": out["name"],
                    "units": out.get("units", ""),
                    "experiment_id": exp["id"],
                    "experiment_name": exp["name"],
                    "source_deleted": deleted,
                }
                if "class" in out:
                    meta["class"] = out["class"]
                outputs.append({**out, "label": label})
                columns.append(
                    rowquery.Column(
                        out["key"],
                        label,
                        out["type"],
                        "prediction",
                        lambda r, v=values, k=out["key"]: (v.get(str(r["row_id"])) or {}).get(k),
                        meta,
                    )
                )
            sets.append(
                {
                    "key": key,
                    "values": values,
                    "errors": kept["errors"],
                    "view": {
                        "key": key,
                        "experiment": {**exp, "deleted": deleted},
                        "created_utc": kept["created_utc"],
                        "n_rows": kept["dataset"]["n_rows"],
                        "n_failed": kept["n_failed"],
                        "source_deleted": deleted,
                        "outputs": outputs,
                    },
                }
            )
        return columns, sets

    def _experiment_deleted(self, experiment_id: str, checksum: str) -> bool:
        """True when the registry has no such experiment any more. An unreadable registry
        says nothing, so it is not "deleted".
        """
        if self.experiments is None:
            return False
        try:
            self.experiments.get(experiment_id)
        except NotFound:
            return True
        except Exception:
            return False
        return False

    def start_prediction(self, req: PredictionStartRequest) -> dict:
        """Apply a registered model to every row of a ready dataset, as a job. The job
        captures the dataset's hash and the model's checksum now; polling it, or the
        dataset's predictions, is how the result is read.
        """
        self._store().get(req.dataset_id)  # NotFound before anything is started
        with self._guarded(self._registry()):
            out = self._predictor().start(req.dataset_id, req.experiment_id)
        log.info("prediction %s: experiment %s on dataset %s", out["id"], req.experiment_id, req.dataset_id)
        return out

    def prediction_status(self, q: PredictionQuery) -> dict:
        return self._predictor().progress(q.job_id)

    def cancel_prediction(self, q: PredictionQuery) -> dict:
        return self._predictor().cancel(q.job_id)

    def dataset_predictions(self, req: DatasetIdRequest) -> dict:
        """A dataset's columns (with the predictions' in them), the sets kept for it and
        its prediction jobs, newest first: what the page reads after a job ends.
        """
        store = self._store()
        meta = store.get(req.dataset_id)
        extra, sets = self._prediction_columns(req.dataset_id)
        return {
            "dataset_id": req.dataset_id,
            "schema": [c.describe() for c in rowquery.schema(meta, extra)],
            "predictions": [s["view"] for s in sets],
            "jobs": self.predictions.for_dataset(req.dataset_id) if self.predictions else [],
        }

    def delete_predictions(self, req: PredictionSetRequest) -> dict:
        """Remove one kept set of predictions from a dataset (not the model, not the dataset)."""
        removed = self._store().delete_prediction(req.dataset_id, req.key)
        if not removed:
            raise NotFound("no such set of predictions %s on dataset %s" % (req.key, req.dataset_id))
        return {"dataset_id": req.dataset_id, "key": req.key, "deleted": True}

    def _depict(self, meta: dict, rows: list[dict]) -> None:
        """Draw the rows on this page, from the cache where they were drawn
        before. Never more than a page: a dataset is not drawn whole.
        """
        dataset_id, version = meta["dataset_id"], str(self.ligands.describe().get("depiction", ""))
        wanted = [r for r in rows if r["smiles"]]
        have = self.datasets.cached_depictions(dataset_id, version, [r["row_id"] for r in wanted])
        todo = [r for r in wanted if r["row_id"] not in have]
        if todo:
            drawn = self.ligands.depict([r["smiles"] for r in todo]).get("svgs") or []
            if len(drawn) != len(todo):
                raise ToolFailed(
                    "ligandbench drew %d of %d" % (len(drawn), len(todo)), "ligandbench"
                )
            made = {r["row_id"]: svg for r, svg in zip(todo, drawn) if svg}
            self.datasets.put_depictions(dataset_id, version, made)
            have.update(made)
        for r in rows:
            r["svg"] = have.get(r["row_id"], "")

    # -- the session's output directory -------------------------------------
    def create_project(self, req: ProjectRequest) -> dict:
        """Start an output directory for this session. The page asks once,
        before the first run that produces a file, and names it after
        whatever is loaded.
        """
        if self.projects is None:
            raise Conflict("this server has no project directory")
        project = self.projects.create(req.name)
        log.info("project %s: %s", project.id, project.path)
        return project.describe()

    def save_project(self, req: ProjectSaveRequest) -> dict:
        """Copy the project to a path the user gave, with the page's own
        state written into it first.
        """
        project = self._project(req.project)
        if project is None:  # parse() refuses an empty id
            raise NotFound("no such project")
        if req.session is not None:
            project.put_text(
                "session.json",
                json.dumps(dict(req.session), indent=1),
                kind="session",
                tool="viewer",
                note="the viewer's own state; open it in the viewer",
            )
        forbid = [
            p
            for p in (self.work_root, self.projects.root if self.projects else None)
            if p is not None
        ]
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
        project, since the store reopens the directory.
        """
        if not project_id:
            return None
        if self.projects is None:
            raise NotFound("this server has no project directory")
        return self.projects.require(project_id)

    def _write_structure(self, project: Project, entry: BufferedStructure) -> str:
        """The exact text a run ran on. Everything else in the project is
        derived from one of these, so they go in beside the results.
        """
        rel = "structures/%s.%s" % (safe_name(entry.token), entry.fmt)
        project.put_file(rel, entry.path, kind="structure", structure=entry.token)
        return rel

    def _write_pockets(
        self, project: Project, entry: BufferedStructure, tool: str, run: PocketRun
    ) -> None:
        self._write_structure(project, entry)
        base = "pockets/%s.%s" % (safe_name(entry.token), safe_name(tool))
        common = {
            "structure": entry.token,
            "tool": tool,
            "command": run.meta.get("command"),
            "pockets": len(run.pockets),
        }
        project.put_text(
            base + ".json",
            json.dumps(
                {"structure": entry.token, "meta": run.meta, "pockets": run.pockets}, indent=1
            ),
            kind="pockets",
            **common,
        )
        project.put_text(
            base + ".residues.txt", residue_list(run.pockets, tool), kind="pockets", **common
        )
        project.put_text(
            base + ".spheres.pdb", spheres_pdb(run.pockets, tool), kind="pockets", **common
        )

    def _write_prepared(
        self, project: Project, entry: BufferedStructure, ph: float, out: dict
    ) -> None:
        self._write_structure(project, entry)
        base = "structures/%s-prep" % safe_name(entry.token)
        report = out.get("report") or {}
        common = {
            "structure": entry.token,
            "tool": "quickprep",
            "ph": ph,
            "seconds": report.get("seconds"),
        }
        project.put_text(base + ".pdb", out.get("data") or "", kind="prepared", **common)
        project.put_text(
            base + ".report.json", json.dumps(report, indent=1), kind="report", **common
        )
        project.put_text(base + ".report.txt", out.get("summary") or "", kind="report", **common)

    def _write_scores(self, sjob: ScoringJob) -> None:
        """A finished scoring job's numbers, once, beside the poses they
        belong to: the scores themselves, the relaxed poses, and the molecule
        table with the scores joined onto it.
        """
        with self._lock:
            known = self._score_jobs.get(sjob.id)
            if known is None or sjob.id in self._scores_written:
                return
            project_id, parent = known
            if not project_id:
                return
            self._scores_written.add(sjob.id)
        try:
            project = self.projects.require(project_id) if self.projects else None
            if project is None:
                return
            payload = sjob.payload()
            base = "molecules/%s" % safe_name(parent)
            scorers = payload.get("scorers") or {}
            tool = next(iter(scorers.values()), {}).get("scorer", "")
            common = {"kind": "scores", "tool": tool or "pose scorer"}
            project.put_text(
                base + "/scores.json",
                json.dumps(payload, indent=1),
                note="a scoring function's value in kcal/mol, not a measured affinity",
                **common,
            )
            for rank, meta in sorted(scorers.items()):
                minimized = meta.get("minimized") or ""
                if minimized and os.path.isfile(minimized):
                    project.put_file(
                        "%s/pocket-%s.min.sdf" % (base, safe_name(rank)),
                        minimized,
                        command=meta.get("command"),
                        pocket=rank,
                        mode=meta.get("mode"),
                        note="the poses after minimization; the generated poses are untouched",
                        **common,
                    )
            # The join the whole feature is for: the same rows, now scored.
            gen = self.generator.get(parent)
            rows = merge_scores(gen.table() if gen else [], payload.get("scores") or {}, scorers)
            if rows:
                project.put_text(
                    base + "/molecules.json",
                    json.dumps(rows, indent=1),
                    kind="properties",
                    tool="NEAT-POCKET",
                    note="properties and 2D depictions, RDKit's; scores from %s"
                    % (tool or "the pose scorer"),
                )
                project.put_text(
                    base + "/molecules.csv",
                    csv_text(rows, MOLECULE_COLUMNS),
                    kind="properties",
                    tool="NEAT-POCKET",
                )
        except Exception:
            with self._lock:
                self._scores_written.discard(sjob.id)
            log.exception("could not write scoring job %s into project %s", sjob.id, project_id)

    def _write_molecules(self, job: GenerationJob) -> None:
        """A finished job's molecules, once. The page polls a done job again
        whenever it reloads its results, and the files do not change.
        """
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
                project.put_text(
                    "%s/pocket-%s.sdf" % (base, safe_name(rank)),
                    sdf,
                    kind="molecules",
                    tool="NEAT-POCKET",
                    pocket=rank,
                    molecules=sdf.count("$$$$"),
                )
            rows = job.table()
            if rows:
                project.put_text(
                    base + "/molecules.json",
                    json.dumps(rows, indent=1),
                    kind="properties",
                    tool="NEAT-POCKET",
                    note="properties and 2D depictions, RDKit's",
                )
                project.put_text(
                    base + "/molecules.csv",
                    csv_text(rows, MOLECULE_COLUMNS),
                    kind="properties",
                    tool="NEAT-POCKET",
                )
        except Exception:
            # Artifacts are a side effect: a job's results still go to the
            # page even if writing them down fails (a full disk, say).
            with self._lock:
                self._job_written.discard(job.id)
            log.exception("could not write job %s into project %s", job.id, project_id)
