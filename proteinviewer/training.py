"""Training QSAR models on ready datasets, as background jobs.

The science is ``ligandbench train``: tuning, evaluation and the production
refit, written into a run directory. It is run as a subprocess by the
``Ligandbench`` adapter (the server never imports it, nor XGBoost), and takes
minutes, so a request cannot wait on it: a job runs on a thread and the page
polls. The child keeps ``progress.json`` in the run directory up to date, and
writes ``run.json`` last, atomically; a run is complete when that file says so.

What is different from generation (``generation.py``, whose conventions this
follows):

* Training is heavy and single-threaded by contract, so at most ``concurrency``
  children run at once (one by default) and at most ``queue`` more wait. A start
  past that is refused, not buffered without bound.
* A completed run is the input of registration, which has to copy it while it
  exists, so a run can be *pinned*: eviction skips it until the pin is released.
  Without a pin a finished run is temporary: it is forgotten, and its directory
  removed, once more jobs than ``limit`` are kept (or when the server stops).
* Every way a job can end is a terminal state with a message: the child crashing,
  exiting non-zero, being killed, timing out, printing nonsense, leaving no
  ``run.json`` or an unreadable one, a bug on the job's own thread, the server
  stopping, and being cancelled -- queued or running. Cancelling kills the child's
  whole process group, not only the child.

No model crosses HTTP: what is returned is progress and the run's metadata.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import Conflict, NotFound, ToolFailed, ToolUnavailable, ViewerError

__all__ = ["Training", "Job", "FINISHED"]

FINISHED = ("done", "error", "cancelled")
#: ``run.json``'s ``schema`` starts with this; anything else is not a run we know.
RUN_SCHEMA = "qsar-run-"
#: The error code of a job whose production strategy could not be split.
UNAVAILABLE = "production_strategy_unavailable"
#: How long to wait for a killed child's output before giving up on it, seconds.
REAP_TIMEOUT = 10.0


class Job:
    """One training run: its request, its state, and what it produced."""

    def __init__(self, run_id: str, run_dir: Path, spec: dict):
        self.id = run_id
        self.dir = run_dir
        self.spec = spec
        self.state = "queued"
        self.error: str | None = None
        self.error_code: str | None = None
        self.unavailable: dict | None = None
        self.message: str | None = None  # why it was cancelled
        self.result: dict | None = None  # run.json, once read
        self.started = time.time()
        self.finished: float | None = None
        # Guarded by the registry's lock, not this one.
        self.pins = 0
        # Guarded by this one.
        self.proc: subprocess.Popen | None = None
        self.proc_done = False
        self.cancel_requested = False
        self._lock = threading.Lock()

    @property
    def done(self) -> bool:
        return self.state in FINISHED

    def _begin(self) -> None:
        with self._lock:
            if self.state == "queued":
                self.state = "running"

    def _finish(
        self,
        state: str,
        *,
        error: str | None = None,
        message: str | None = None,
        error_code: str | None = None,
        unavailable: dict | None = None,
        result: dict | None = None,
    ) -> bool:
        """Move to a terminal state. The first ending wins, so a cancelled job
        is not made an error by the child dying of it. Returns whether this call
        was the one that ended the job.
        """
        with self._lock:
            if self.state in FINISHED:
                return False
            self.error, self.message = error, message
            self.error_code, self.unavailable = error_code, unavailable
            self.result = result
            self.finished = time.time()
            self.state = state
            return True

    def progress(self) -> dict:
        """Our own state, with the child's stage and counters while there is one.

        The child writes ``done`` before it exits, and its ``run.json`` is only
        read once it has: reporting that as done would send a caller for a run
        that is not there yet. Until then the job is "finishing".
        """
        with self._lock:
            state, finished = self.state, self.finished
            error, message = self.error, self.message
            code, unavailable = self.error_code, self.unavailable
        info: dict[str, Any] = {
            "id": self.id,
            "state": state,
            "stage": "",
            "completed": 0,
            "total": 0,
            "elapsed": round((finished or time.time()) - self.started, 1),
            **{k: self.spec[k] for k in ("dataset_id", "seed", "production_strategy", "profile")},
        }
        if state != "queued":
            child = _read_json(self.dir / "progress.json")
            if isinstance(child.get("stage"), str):
                info["stage"] = child["stage"][:200]
            for key in ("completed", "total"):
                v = child.get(key)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    info[key] = v
            if state == "running" and child.get("state") == "done":
                info["state"] = "finishing"
        if error:
            info["error"] = error
        if message:
            info["message"] = message
        if code:
            info["error_code"] = code
        if unavailable is not None:
            info["unavailable"] = unavailable
        return info


def _read_json(path: Path) -> dict:
    """The file's JSON object, or ``{}`` if it is missing, half-written or not one."""
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _kill(proc: subprocess.Popen) -> None:
    """End the child and everything it started: it leads its own process group."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except AttributeError:  # no process groups here
        with contextlib.suppress(OSError):
            proc.kill()


class Training:
    """Starts training jobs, runs them in turn, and keeps the most recent ones.

    ``tool`` is what runs ligandbench (the ``Ligandbench`` adapter): it says whether
    training can run (``training_unavailable``), starts a child (``start_training``)
    and reads what it printed (``training_outcome``). Run directories live under
    ``root`` as ``<root>/<run id>/``; the server passes ``<work_root>/training``.
    """

    name = "training"

    def __init__(
        self,
        tool,
        root: str | os.PathLike,
        *,
        limit: int = 32,
        concurrency: int = 1,
        queue: int = 8,
        timeout: float = 6 * 3600.0,
    ):
        self.tool = tool
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
        # Guards the registry, the queue, the running count and every job's pins.
        # Taken before a job's own lock, never after.
        self._cv = threading.Condition()

    # -- what this server can do ---------------------------------------------
    @property
    def available(self) -> bool:
        return self.tool.training_unavailable() is None

    def describe(self) -> dict:
        with self._cv:
            running, queued = self._running, len(self._queue)
        return {
            "available": self.available,
            "reason": self.tool.training_unavailable() or "",
            "running": running,
            "queued": queued,
            "concurrency": self.concurrency,
            "queue": self.queue_limit,
            "kept": self.limit,
        }

    def status_line(self) -> str:
        reason = self.tool.training_unavailable()
        if reason:
            return "training: NOT AVAILABLE - %s" % reason
        return "training: ligandbench train  (%d at a time, %d may wait)" % (
            self.concurrency,
            self.queue_limit,
        )

    # -- the jobs ------------------------------------------------------------
    def start(
        self,
        dataset_id: str,
        dataset_dir: str | os.PathLike,
        seed: int,
        production_strategy: str,
        profile: str = "full",
    ) -> dict:
        """Queue a run on the ready dataset in ``dataset_dir`` and return its
        progress at once; poll :meth:`progress` with the ``id`` in it.

        ``ToolUnavailable`` when training cannot run here, ``Conflict`` when the
        queue is full or the server is stopping. Nothing is started for either.
        """
        reason = self.tool.training_unavailable()
        if reason:
            raise ToolUnavailable("training is not available: %s" % reason, "ligandbench")
        spec = {
            "dataset_id": dataset_id,
            "dataset_dir": str(dataset_dir),
            "seed": int(seed),
            "production_strategy": production_strategy,
            "profile": profile,
        }
        with self._cv:
            if self._closed:
                raise Conflict("the server is stopping; no new training can start")
            if self._running + len(self._queue) >= self.concurrency + self.queue_limit:
                raise Conflict(
                    "the training queue is full (%d running, %d waiting); try again when a run "
                    "has finished, or cancel one" % (self._running, len(self._queue))
                )
            run_id = "run-" + uuid.uuid4().hex[:12]
            run_dir = self.root / run_id
            try:
                run_dir.mkdir(parents=True)
            except OSError as exc:
                raise ToolFailed("cannot make a run directory: %s" % exc, "ligandbench") from exc
            job = Job(run_id, run_dir, spec)
            thread = threading.Thread(
                target=self._work, args=(job,), daemon=True, name="train-" + run_id
            )
            self._jobs[run_id] = job
            self._queue.append(job)
            try:
                thread.start()
            except RuntimeError as exc:  # no thread to be had: nothing was started
                del self._jobs[run_id]
                self._queue.remove(job)
                shutil.rmtree(run_dir, ignore_errors=True)
                raise ToolFailed("cannot start a training thread: %s" % exc, "ligandbench") from exc
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        self._prune()
        return job.progress()

    def progress(self, run_id: str) -> dict:
        return self._get(run_id).progress()

    def run(self, run_id: str) -> dict:
        """The completed run's ``run.json``. ``Conflict`` unless it is done."""
        job = self._get(run_id)
        with job._lock:
            state, result = job.state, job.result
        if state != "done" or result is None:
            raise Conflict("training run %s is %s, not completed" % (run_id, state))
        return copy.deepcopy(result)

    def cancel(self, run_id: str) -> dict:
        """Stop a queued or running job. A job that has ended (or is reading its
        result) is not changed: cancelling it answers with how it ended.
        """
        job = self._get(run_id)
        with self._cv:
            if job in self._queue:
                self._queue.remove(job)
                job._finish("cancelled", message="cancelled before it started")
                self._cv.notify_all()
                return job.progress()
        self._stop(job, "cancelled")
        return job.progress()

    @contextlib.contextmanager
    def pin(self, run_id: str) -> Iterator[Path]:
        """Hold a completed run in place while it is copied: yields its directory.

        ``NotFound`` for a run that is unknown, evicted or gone with a restart,
        ``Conflict`` for one that is not completed (running, failed, cancelled).
        Pins are counted, so two holders each keep it; eviction skips a run while
        any hold it, and the pin is released on leaving the block, also by an error.
        """
        with self._cv:
            job = self._jobs.get(run_id)
            if job is None:
                raise NotFound("no such training run %r (it may have been evicted)" % (run_id,))
            if job.state != "done":
                raise Conflict("training run %s is %s, not completed" % (run_id, job.state))
            if not (job.dir / "run.json").is_file():
                raise NotFound("training run %s has lost its files" % run_id)
            job.pins += 1
        try:
            yield job.dir
        finally:
            with self._cv:
                job.pins -= 1
            self._prune()

    def shutdown(self, wait: float = 10.0) -> None:
        """The server is stopping: refuse new runs, drop the queue and kill what
        is running, so no child outlives the server. Every job ends cancelled.
        """
        with self._cv:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in self._queue:
                job._finish("cancelled", message="the server stopped before it started")
            self._queue.clear()
            self._cv.notify_all()
            threads = list(self._threads)
        for job in jobs:
            if not job.done:
                self._stop(job, "the server stopped")
        deadline = time.time() + wait
        for t in threads:
            t.join(max(0.0, deadline - time.time()))

    # -- internals -----------------------------------------------------------
    def _get(self, run_id: str) -> Job:
        with self._cv:
            job = self._jobs.get(run_id) if isinstance(run_id, str) else None
        if job is None:
            raise NotFound("no such training run %r (it may have been evicted)" % (run_id,))
        return job

    def _stop(self, job: Job, message: str) -> None:
        """Ask a started job to end: kill its child if it has one, and say why."""
        with job._lock:
            if job.state in FINISHED or job.state == "finishing":
                return
            job.cancel_requested = True
            job.message = message
            if job.proc is not None and not job.proc_done:
                _kill(job.proc)

    def _prune(self) -> None:
        """Forget the oldest finished jobs past the limit, and their directories.
        A job that is waiting or running is never dropped, nor is a pinned one.
        """
        with self._cv:
            excess = len(self._jobs) - self.limit
            doomed = []
            for job in self._jobs.values():
                if excess <= 0:
                    break
                if job.done and not job.pins:
                    doomed.append(job)
                    excess -= 1
            for job in doomed:
                del self._jobs[job.id]
        # Out of the registry, so nothing can pin them any more.
        for job in doomed:
            shutil.rmtree(job.dir, ignore_errors=True)

    def _work(self, job: Job) -> None:
        """The job's thread: wait for a turn, run, and always end terminal."""
        slot = False
        try:
            slot = self._wait_turn(job)
            if slot:
                self._execute(job)
        except BaseException as exc:  # a bug here must not leave the job "running"
            with job._lock:
                if job.proc is not None and not job.proc_done:
                    _kill(job.proc)  # nothing is left running for a job that has ended
            job._finish(
                "error", error="training failed unexpectedly: %s: %s" % (type(exc).__name__, exc)
            )
        finally:
            if slot:
                with self._cv:
                    self._running -= 1
                    self._cv.notify_all()
            job._finish("error", error="training ended without a result")  # no-op when done

    def _wait_turn(self, job: Job) -> bool:
        """Block until this job is first in the queue and there is a free slot.
        False when it was cancelled (or the server stopped) while it waited.
        """
        with self._cv:
            while True:
                if job.done or self._closed:
                    return False
                if self._queue and self._queue[0] is job and self._running < self.concurrency:
                    self._queue.popleft()
                    self._running += 1
                    job._begin()
                    self._cv.notify_all()
                    return True
                self._cv.wait()

    def _execute(self, job: Job) -> None:
        spec = job.spec
        if job.cancel_requested:
            return self._cancelled(job)
        try:
            proc = self.tool.start_training(
                spec["dataset_dir"],
                str(job.dir),
                spec["seed"],
                spec["production_strategy"],
                spec["profile"],
            )
        except ViewerError as exc:
            job._finish("error", error=str(exc))
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
            return self._cancelled(job)
        if timed_out:
            job._finish(
                "error", error="training timed out after %gs and was stopped" % self.timeout
            )
            return
        self._conclude(job, proc.returncode, stdout, stderr)

    def _reap(self, proc: subprocess.Popen) -> tuple[str, str]:
        """What a killed child left on its pipes; empty if it cannot be had (a
        grandchild that left the process group may hold them open).
        """
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

    def _cancelled(self, job: Job) -> None:
        job._finish("cancelled", message=job.message or "cancelled")

    def _conclude(self, job: Job, returncode: int | None, stdout: str, stderr: str) -> None:
        """Read what the child said and what it left, and end the job."""
        try:
            outcome = self.tool.training_outcome(returncode, stdout, stderr)
        except ViewerError as exc:  # its exit code said whose problem it was
            job._finish("error", error=str(exc))
            return
        status = outcome.get("status")
        if status == UNAVAILABLE:
            strategy = outcome.get("strategy") or job.spec["production_strategy"]
            job._finish(
                "error",
                error="the %s split is not available for this dataset, so nothing was trained; "
                "start a new run with another production strategy" % strategy,
                error_code=UNAVAILABLE,
                unavailable=outcome.get("unavailable"),
            )
            return
        if status != "completed":
            job._finish(
                "error", error="ligandbench train reported %r, not a completed run" % status
            )
            return
        run = _read_json(job.dir / "run.json")
        if not run:
            job._finish(
                "error",
                error="training finished but left no readable run.json in its run directory",
            )
        elif run.get("status") != "completed" or not str(run.get("schema", "")).startswith(
            RUN_SCHEMA
        ):
            job._finish(
                "error",
                error="training finished but run.json is not a completed run (schema %r, status %r)"
                % (run.get("schema"), run.get("status")),
            )
        else:
            job._finish("done", result=run)
