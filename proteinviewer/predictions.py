"""Applying a registered model to a whole dataset, as a background job.

The science is ``ligandbench predict``: it checks that the model can be applied the
way it was trained, then predicts every row of the dataset. It runs as a subprocess
(the server never imports it, nor XGBoost) and can take a while, so a job runs on a
thread and the page polls, as for training.

What this module adds, and what the issue asks it to guarantee:

* **What was asked is what is answered.** When a job starts it captures the dataset's
  id and content hash and the experiment's id and model checksum. The dataset and
  the experiment are immutable, so the result is attached to exactly that dataset, by
  row id, however the page's filter, sort, page or open dataset changes meanwhile.
* **A lease.** The job holds the experiment's lease (``ExperimentStore.lease``) while
  the child reads its files, so a deletion waits for it instead of pulling the model
  out from under it, and a model deleted before the job gets its turn ends the job
  with a message, not a crash.
* **All or nothing.** A set is written only after the child finished, the answer was
  checked (one output per row, in the dataset's order, nothing from another dataset)
  and the model was still the one captured. Anything else -- an error, a cancel, a
  timeout -- writes nothing, so a failed rerun leaves the last good columns alone.
* **Bounded.** At most ``concurrency`` children run and ``queue`` more wait; a start
  past that is refused. A second job for the same dataset and model is refused while
  one is queued or running.

Nothing here knows a molecule: it moves files and states.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import PREDICTION_SCHEMA
from .errors import Conflict, Incompatible, NotFound, ToolFailed, ToolUnavailable, ViewerError
from .training import FINISHED, REAP_TIMEOUT, _kill, _read_json

__all__ = ["Predictions", "set_key", "build_set", "model_checksum"]

#: The job's own error codes, beside ``incompatible`` (a model this build cannot apply).
GONE = "experiment_unavailable"
CHANGED = "dataset_or_model_changed"


def model_checksum(record: Mapping[str, Any]) -> str:
    """The sha256 the registry recorded for the experiment's ``model.json``."""
    try:
        out = record["checksums"]["model.json"]["sha256"]
    except (KeyError, TypeError):
        out = ""
    if not isinstance(out, str) or len(out) < 12:
        raise Incompatible("the experiment records no checksum for its model, so it cannot be used")
    return out


def set_key(experiment_id: str, checksum: str) -> str:
    """The prefix of a set's column keys: the experiment's id and its model's checksum."""
    return "pred_%s_%s" % (experiment_id.replace("-", ""), checksum[:12])


def _units(target: Mapping[str, Any]) -> str:
    """The units a regression model predicts in. The model's target, not the source's:
    pKi stays pKi.
    """
    if str(target.get("transform") or "").lower() == "pki":
        return "pKi"
    return str(target.get("units") or "")


def _label(name: str) -> str:
    return name.strip() or "model"


def build_set(
    *,
    key: str,
    dataset: Mapping[str, Any],
    experiment: Mapping[str, Any],
    result: Mapping[str, Any],
    rows: list[dict],
) -> dict:
    """A kept set from the child's ``predictions.json``: its output columns and, by
    row id, each row's values or the reason it has none.

    ``rows`` is the dataset's rows, in order: the answer is refused unless it has
    exactly one output per row, with the same row ids in the same order, so a number
    can never land on another row.
    """
    answers = result.get("rows")
    if not isinstance(answers, list) or [a.get("row_id") for a in answers] != [
        r["row_id"] for r in rows
    ]:
        raise ToolFailed(
            "ligandbench predict did not answer every row of the dataset, in order", "ligandbench"
        )
    target = result.get("target") or {}
    kind = target.get("kind")
    name = _label(str(experiment["name"]))
    tail = " (%s)" % experiment["id"][:8]
    outputs: list[dict] = []
    if kind == "regression":
        units = _units(experiment.get("target") or {})
        column = (experiment.get("target") or {}).get("column") or "target"
        outputs.append(
            {
                "key": key + "_value",
                "name": "value",
                "type": "number",
                "units": units,
                "label": "%s%s · predicted %s%s" % (name, tail, column, " (%s)" % units if units else ""),
            }
        )
    elif kind in ("binary", "multiclass"):
        classes = [str(c) for c in target.get("classes") or []]
        outputs.append(
            {
                "key": key + "_class",
                "name": "class",
                "type": "text",
                "units": "",
                "label": "%s%s · predicted class" % (name, tail),
            }
        )
        for i, c in enumerate(classes):
            outputs.append(
                {
                    "key": "%s_p%d" % (key, i),
                    "name": "p%d" % i,
                    "type": "number",
                    "units": "",
                    "class": c,
                    "label": "%s%s · P(%s)" % (name, tail, c),
                }
            )
    else:
        raise ToolFailed("ligandbench predict returned a target of kind %r" % (kind,), "ligandbench")

    values: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for a in answers:
        rid = str(a["row_id"])
        if "error" in a:
            errors[rid] = str(a["error"])
        elif kind == "regression":
            values[rid] = {key + "_value": float(a["value"])}
        else:
            probs = a.get("probabilities") or {}
            row = {key + "_class": str(a["predicted_class"])}
            for i, c in enumerate(classes):
                row["%s_p%d" % (key, i)] = float(probs[c])
            values[rid] = row
    return {
        "schema": PREDICTION_SCHEMA,
        "key": key,
        "dataset": {"id": dataset["dataset_id"], "hash": dataset["content_hash"], "n_rows": len(rows)},
        "experiment": {
            "id": experiment["id"],
            "name": experiment["name"],
            "checksum": experiment["checksum"],
            "model_family": experiment.get("model_family"),
            "task": experiment.get("task"),
            "target": experiment.get("target"),
        },
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "outputs": outputs,
        "values": values,
        "errors": errors,
        "n_failed": len(errors),
    }


class Job:
    """One prediction job: what it was asked, where it is, how it ended."""

    def __init__(self, job_id: str, directory: Path, spec: dict):
        self.id = job_id
        self.dir = directory
        self.spec = spec
        self.state = "queued"
        self.error: str | None = None
        self.error_code: str | None = None
        self.message: str | None = None
        self.n_failed = 0
        self.started = time.time()
        self.finished: float | None = None
        self.proc: subprocess.Popen | None = None
        self.proc_done = False
        self.cancel_requested = False
        self._lock = threading.Lock()

    @property
    def done(self) -> bool:
        return self.state in FINISHED

    def finish(self, state: str, *, error=None, code=None, message=None) -> bool:
        """Move to a terminal state; the first ending wins."""
        with self._lock:
            if self.state in FINISHED:
                return False
            self.state, self.error, self.error_code, self.message = state, error, code, message
            self.finished = time.time()
            return True

    def progress(self) -> dict:
        with self._lock:
            state, finished = self.state, self.finished
            error, code, message, failed = self.error, self.error_code, self.message, self.n_failed
        spec = self.spec
        info: dict[str, Any] = {
            "id": self.id,
            "state": state,
            "stage": "",
            "completed": 0,
            "total": spec["n_rows"],
            "elapsed": round((finished or time.time()) - self.started, 1),
            "dataset_id": spec["dataset_id"],
            "dataset_hash": spec["dataset_hash"],
            "experiment_id": spec["experiment_id"],
            "experiment_name": spec["experiment_name"],
            "checksum": spec["checksum"],
            "key": spec["key"],
        }
        if state == "queued":
            info["stage"] = "waiting for a free slot"
        elif state in ("running", "finishing"):
            child = _read_json(self.dir / "out" / "progress.json")
            if isinstance(child.get("stage"), str):
                info["stage"] = child["stage"][:200]
            done = child.get("completed")
            if isinstance(done, int) and not isinstance(done, bool) and 0 <= done <= info["total"]:
                info["completed"] = done
        if state == "done":
            info["completed"], info["n_failed"] = info["total"], failed
        if error:
            info["error"] = error
        if code:
            info["error_code"] = code
        if message:
            info["message"] = message
        return info


class Predictions:
    """Starts prediction jobs, runs them in turn and keeps the most recent ones.

    ``tool`` is the ``Ligandbench`` adapter (``prediction_unavailable``,
    ``model_compatibility``, ``start_prediction``, ``prediction_outcome``);
    ``experiments`` the registry, ``datasets`` the dataset store. Job directories
    live under ``root`` and are removed when a job ends.
    """

    name = "predictions"

    def __init__(
        self,
        tool,
        experiments,
        datasets,
        root: str | os.PathLike,
        *,
        limit: int = 64,
        concurrency: int = 2,
        queue: int = 8,
        timeout: float = 3600.0,
    ):
        self.tool, self.experiments, self.datasets = tool, experiments, datasets
        self.root = Path(root)
        self.limit = max(1, limit)
        self.concurrency = max(1, concurrency)
        self.queue_limit = max(0, queue)
        self.timeout = timeout
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: deque[Job] = deque()
        self._running = 0
        self._threads: list[threading.Thread] = []
        self._closed = False
        self._cv = threading.Condition()

    # -- what this server can do ---------------------------------------------
    def describe(self) -> dict:
        with self._cv:
            running, queued = self._running, len(self._queue)
        reason = self.tool.prediction_unavailable()
        return {
            "available": reason is None,
            "reason": reason or "",
            "running": running,
            "queued": queued,
            "concurrency": self.concurrency,
            "queue": self.queue_limit,
        }

    # -- starting -----------------------------------------------------------
    def start(self, dataset_id: str, experiment_id: str) -> dict:
        """Queue the model on every row of the dataset and return the job's progress.

        Refused, with nothing started: ``ToolUnavailable`` (cannot predict here),
        ``NotFound`` (no such dataset or experiment), ``Conflict`` (the experiment is not
        ready or its files are damaged, a job for the pair is already waiting or running,
        the queue is full), ``Incompatible`` (a model this build cannot apply).
        """
        reason = self.tool.prediction_unavailable()
        if reason:
            raise ToolUnavailable("prediction is not available: %s" % reason, "ligandbench")
        meta = self.datasets.get(dataset_id)
        rows = self.datasets.rows(dataset_id)
        record = self.experiments.get(experiment_id)
        if record["state"] != "ready":
            raise Conflict("experiment %s is %s, not ready" % (experiment_id, record["state"]))
        with self.experiments.lease(experiment_id):  # its files are in place, right now
            pass
        why = self.tool.model_compatibility(record.get("versions"), (record.get("run") or {}).get("schema"))
        if why:
            raise Incompatible("this model cannot be applied here: %s" % why)
        checksum = model_checksum(record)
        spec = {
            "dataset_id": dataset_id,
            "dataset_hash": meta["content_hash"],
            "n_rows": len(rows),
            "experiment_id": record["id"],
            "experiment_name": record["name"],
            "checksum": checksum,
            "key": set_key(record["id"], checksum),
        }
        with self._cv:
            if self._closed:
                raise Conflict("the server is stopping; no new prediction can start")
            for other in self._jobs.values():
                if (
                    not other.done
                    and other.spec["dataset_id"] == dataset_id
                    and other.spec["key"] == spec["key"]
                ):
                    raise Conflict(
                        "this model is already being applied to this dataset (job %s)" % other.id
                    )
            if self._running + len(self._queue) >= self.concurrency + self.queue_limit:
                raise Conflict(
                    "the prediction queue is full (%d running, %d waiting); try again when one "
                    "has finished" % (self._running, len(self._queue))
                )
            job_id = "pred-" + uuid.uuid4().hex[:12]
            directory = self.root / job_id
            try:
                (directory / "out").mkdir(parents=True)
            except OSError as exc:
                raise ToolFailed("cannot make a job directory: %s" % exc, "ligandbench") from exc
            job = Job(job_id, directory, spec)
            thread = threading.Thread(target=self._work, args=(job,), daemon=True, name=job_id)
            self._jobs[job_id] = job
            self._queue.append(job)
            try:
                thread.start()
            except RuntimeError as exc:
                del self._jobs[job_id]
                self._queue.remove(job)
                shutil.rmtree(directory, ignore_errors=True)
                raise ToolFailed("cannot start a prediction thread: %s" % exc, "ligandbench") from exc
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        self._prune()
        return job.progress()

    # -- asking ---------------------------------------------------------------
    def progress(self, job_id: str) -> dict:
        return self._get(job_id).progress()

    def for_dataset(self, dataset_id: str) -> list[dict]:
        """The jobs of a dataset that have not ended, and the ones that ended
        in an error, newest first (a finished one's result is in the dataset's sets).
        """
        with self._cv:
            jobs = [j for j in self._jobs.values() if j.spec["dataset_id"] == dataset_id]
        return [j.progress() for j in reversed(jobs)]

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        with self._cv:
            if job in self._queue:
                self._queue.remove(job)
                job.finish("cancelled", message="cancelled before it started")
                self._cv.notify_all()
                return job.progress()
        self._stop(job, "cancelled")
        return job.progress()

    def shutdown(self, wait: float = 10.0) -> None:
        with self._cv:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in self._queue:
                job.finish("cancelled", message="the server stopped before it started")
            self._queue.clear()
            self._cv.notify_all()
            threads = list(self._threads)
        for job in jobs:
            if not job.done:
                self._stop(job, "the server stopped")
        deadline = time.time() + wait
        for t in threads:
            t.join(max(0.0, deadline - time.time()))

    # -- internals ------------------------------------------------------------
    def _get(self, job_id: str) -> Job:
        with self._cv:
            job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None:
            raise NotFound("no such prediction job %r (it may have been forgotten)" % (job_id,))
        return job

    def _stop(self, job: Job, message: str) -> None:
        with job._lock:
            if job.state in FINISHED or job.state == "finishing":
                return
            job.cancel_requested = True
            job.message = message
            if job.proc is not None and not job.proc_done:
                _kill(job.proc)

    def _prune(self) -> None:
        with self._cv:
            excess = len(self._jobs) - self.limit
            for job in list(self._jobs.values()):
                if excess <= 0:
                    break
                if job.done:
                    del self._jobs[job.id]
                    excess -= 1

    def _work(self, job: Job) -> None:
        slot = False
        try:
            slot = self._wait_turn(job)
            if slot:
                self._execute(job)
        except BaseException as exc:  # a bug here must not leave the job "running"
            with job._lock:
                if job.proc is not None and not job.proc_done:
                    _kill(job.proc)
            job.finish("error", error="prediction failed unexpectedly: %s: %s" % (type(exc).__name__, exc))
        finally:
            if slot:
                with self._cv:
                    self._running -= 1
                    self._cv.notify_all()
            job.finish("error", error="prediction ended without a result")  # no-op when done
            shutil.rmtree(job.dir, ignore_errors=True)

    def _wait_turn(self, job: Job) -> bool:
        with self._cv:
            while True:
                if job.done or self._closed:
                    return False
                if self._queue and self._queue[0] is job and self._running < self.concurrency:
                    self._queue.popleft()
                    self._running += 1
                    with job._lock:
                        if job.state == "queued":
                            job.state = "running"
                    self._cv.notify_all()
                    return True
                self._cv.wait()

    def _execute(self, job: Job) -> None:
        spec = job.spec
        if job.cancel_requested:
            job.finish("cancelled", message=job.message or "cancelled")
            return
        try:
            lease = self.experiments.lease(spec["experiment_id"])
            with lease as run_dir:
                # Under the lease nothing can delete it. Is it still the model that was asked for?
                now = self.experiments.get(spec["experiment_id"])
                if model_checksum(now) != spec["checksum"] or _sha(Path(run_dir) / "model.json") != spec["checksum"]:
                    job.finish(
                        "error",
                        code=CHANGED,
                        error="the experiment's model is not the one this job was started with",
                    )
                    return
                self._run_child(job, Path(run_dir), now)
        except (NotFound, Conflict) as exc:
            job.finish(
                "error",
                code=GONE,
                error="the experiment is no longer available (it may have been deleted): %s" % exc,
            )
        except ViewerError as exc:
            job.finish("error", error=str(exc), code=getattr(exc, "code", None))

    def _run_child(self, job: Job, run_dir: Path, record: Mapping[str, Any]) -> None:
        spec = job.spec
        dataset_dir = self.datasets.directory(spec["dataset_id"])
        meta = self.datasets.get(spec["dataset_id"])
        if meta.get("content_hash") != spec["dataset_hash"]:
            job.finish("error", code=CHANGED, error="the dataset is not the one this job was started with")
            return
        try:
            proc = self.tool.start_prediction(str(run_dir), str(dataset_dir), str(job.dir / "out"))
        except ViewerError as exc:
            job.finish("error", error=str(exc))
            return
        with job._lock:
            job.proc = proc
            cancelled = job.cancel_requested
        if cancelled:
            _kill(proc)
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(proc)
            stdout, stderr = self._reap(proc)
        with job._lock:
            job.proc_done = True
            cancelled = job.cancel_requested
            if not cancelled:
                job.state = "finishing"
        if cancelled:
            job.finish("cancelled", message=job.message or "cancelled")
            return
        if timed_out:
            job.finish("error", error="prediction timed out after %gs and was stopped" % self.timeout)
            return
        try:
            outcome = self.tool.prediction_outcome(proc.returncode, stdout, stderr)
            if outcome.get("status") != "completed":
                raise ToolFailed("ligandbench predict reported %r" % outcome.get("status"), "ligandbench")
            result = _read_json(job.dir / "out" / "predictions.json")
            if not result:
                raise ToolFailed("prediction finished but left no readable predictions.json", "ligandbench")
            experiment = {
                "id": spec["experiment_id"],
                "name": record["name"],
                "checksum": spec["checksum"],
                "model_family": record.get("model_family"),
                "task": record.get("task"),
                "target": record.get("target"),
            }
            kept = build_set(
                key=spec["key"],
                dataset=meta,
                experiment=experiment,
                result=result,
                rows=self.datasets.rows(spec["dataset_id"]),
            )
            if job.cancel_requested:  # cancelled while the answer was being read
                job.finish("cancelled", message=job.message or "cancelled")
                return
            self.datasets.put_prediction(spec["dataset_id"], spec["key"], kept)
        except ViewerError as exc:
            job.finish("error", error=str(exc), code=getattr(exc, "code", None))
            return
        with job._lock:
            job.n_failed = kept["n_failed"]
        job.finish("done")

    def _reap(self, proc: subprocess.Popen) -> tuple[str, str]:
        try:
            return proc.communicate(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            for pipe in (proc.stdout, proc.stderr):
                with contextlib.suppress(OSError, ValueError):
                    if pipe:
                        pipe.close()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                proc.wait(timeout=REAP_TIMEOUT)
            return "", ""


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
