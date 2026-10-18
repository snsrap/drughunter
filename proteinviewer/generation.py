"""NEAT-POCKET generation for selected pockets, as background jobs.

NEAT-POCKET needs torch and its own environment, so it cannot be imported
here: the server runs on bare Python. It is invoked as a subprocess of
``NEAT-POCKET/scripts/generate_from_pocket.py``, which takes pockets as residue
selections and needs no reference ligand.

Generation is slow enough (tens of seconds per pocket) that a request cannot
wait on it, so jobs run on a thread and the viewer polls. The child keeps
``progress.json`` up to date in the job directory; that file is the source of
truth for progress -- but not for completion, see :meth:`Job.progress`.

Each job owns its directory, with its own copy of the structure, so nothing
else (the structure buffer evicting an entry, say) can pull files out from
under a running job.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from .errors import ToolFailed, ToolUnavailable
from .process import run

__all__ = ["NeatPocket", "Job"]

DEFAULT_NEAT_DIRS = ["../NEAT-POCKET", "~/projects/NEAT-POCKET"]
PYENV_VERSIONS = "~/.pyenv/versions"
SCRIPT = Path("scripts") / "generate_from_pocket.py"
FINISHED = ("done", "error")


def find_neat_dir(override: str | None = None) -> str | None:
    cands = [override] if override else []
    here = Path(__file__).resolve().parent.parent
    cands += [str(here / d) for d in DEFAULT_NEAT_DIRS]
    cands += DEFAULT_NEAT_DIRS
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser().resolve()
        if (p / SCRIPT).is_file():
            return str(p)
    return None


def find_python(neat_dir: str | None, override: str | None = None) -> str | None:
    """The interpreter that has torch and the neat package.

    Not this one: the server deliberately runs on the stdlib only.
    """
    if override and Path(override).expanduser().is_file():
        return str(Path(override).expanduser())

    cands: list[Path] = []
    if neat_dir:
        ver = Path(neat_dir) / ".python-version"
        if ver.is_file():
            for name in ver.read_text().split():
                cands.append(Path(PYENV_VERSIONS).expanduser() / name / "bin" / "python")
    root = Path(PYENV_VERSIONS).expanduser()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if "neat" in d.name.lower():
                cands.append(d / "bin" / "python")
    for c in cands:
        if c.is_file():
            return str(c)
    return shutil.which("python3")


class Job:
    def __init__(self, job_id: str, out_dir: Path, spec: dict):
        self.id = job_id
        self.out_dir = out_dir
        self.spec = spec
        self.state = "queued"
        self.error: str | None = None
        self.results: list[dict] = []
        self.started = time.time()
        self.finished: float | None = None
        self.stderr_tail = ""
        self._lock = threading.Lock()

    @property
    def done(self) -> bool:
        return self.state in FINISHED

    def _finish(self, state: str, *, error: str | None = None,
                results: list[dict] | None = None) -> None:
        with self._lock:
            self.results = results or []
            self.error = error
            self.finished = time.time()
            self.state = state

    def progress(self) -> dict:
        """Our own state, merged with whatever the child last wrote.

        The child's own stages ("loading model", "generating") say more than
        "running" does, so they are passed on -- except "done". The child
        writes that before it exits, and the results it prints are only read
        once it has; passing it on would send the viewer for molecules that
        are not there yet. Until then the job is "finishing".
        """
        with self._lock:
            state, error, results = self.state, self.error, list(self.results)
            finished = self.finished
        info = {"id": self.id, "state": state,
                "elapsed": round((finished or time.time()) - self.started, 1),
                "requested": self.spec.get("n"),
                "ranks": [p["rank"] for p in self.spec.get("pockets", [])]}
        for k, v in _read_json(self.out_dir / "progress.json", {}).items():
            if k != "state":
                info[k] = v
            elif state == "running":
                info["state"] = "finishing" if v == "done" else v
        if error:
            info["error"] = error
        if results:
            info["results"] = results
        return info

    def table(self) -> list[dict]:
        """Per-molecule rows (properties and 2D depictions) written by the child."""
        return _read_json(self.out_dir / "molecules.json", [])

    def molecules(self) -> dict[str, str]:
        """SDF text per pocket rank, for whatever has been written so far."""
        out: dict[str, str] = {}
        for r in self.results:
            if "sdf" not in r:
                continue
            path = self.out_dir / r["sdf"]
            if path.is_file():
                out[str(r["rank"])] = path.read_text()
        return out


def _read_json(path: Path, default):
    """The file's JSON, or ``default`` if it is missing or half-written."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class NeatPocket:
    """Starts generation jobs and keeps the most recent ones."""

    name = "NEAT-POCKET"

    def __init__(self, jobs_root: str | os.PathLike, *,
                 neat_dir: str | None = None, python: str | None = None,
                 limit: int = 32, timeout: float = 3600.0):
        self.neat_dir = find_neat_dir(neat_dir)
        self.python = find_python(self.neat_dir, python)
        self.jobs_root = Path(jobs_root)
        self.limit = limit
        self.timeout = timeout
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.neat_dir and self.python)

    def describe(self) -> dict:
        return {"available": self.available, "neatDir": self.neat_dir or "",
                "python": self.python or ""}

    def status_line(self) -> str:
        return "NEAT-POCKET: %s" % (self.neat_dir + "  (" + self.python + ")"
                                    if self.available else
                                    "NOT FOUND - generation disabled "
                                    "(set NEAT_POCKET_DIR and NEAT_PYTHON)")

    def start(self, structure_path: str, pockets: list[dict], *,
              n: int = 50, cfg: float = 0.5, batch_size: int = 50,
              max_atoms: int = 100) -> Job:
        """Start a job for ``pockets`` (``[{"rank", "residues"}]``) and return
        at once; poll :meth:`get` for progress."""
        if not self.available:
            raise ToolUnavailable(
                "NEAT-POCKET not found. Set NEAT_POCKET_DIR to the repository and "
                "NEAT_PYTHON to an interpreter that can import torch and neat.",
                self.name)

        job_id = uuid.uuid4().hex[:12]
        out_dir = self.jobs_root / ("gen_" + job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        source = out_dir / ("source" + Path(structure_path).suffix)
        shutil.copyfile(structure_path, source)
        spec = {
            "structure": str(source),
            "pockets": [{"rank": p["rank"], "residues": p["residues"]} for p in pockets],
            "n": int(n), "batch_size": int(batch_size), "cfg": float(cfg),
            "max_atoms": int(max_atoms), "out_dir": str(out_dir),
        }
        (out_dir / "job.json").write_text(json.dumps(spec, indent=1))

        job = Job(job_id, out_dir, spec)
        with self._lock:
            self._jobs[job_id] = job
            self._prune()
        threading.Thread(target=self._run, args=(job,), daemon=True,
                         name="neat-" + job_id).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune(self) -> None:
        """Forget the oldest finished jobs past the limit, and their files.
        A running job is never dropped."""
        finished = [j for j in self._jobs.values() if j.done]
        for job in finished[:max(0, len(self._jobs) - self.limit)]:
            del self._jobs[job.id]
            shutil.rmtree(job.out_dir, ignore_errors=True)

    def _run(self, job: Job) -> None:
        job.state = "running"
        cmd = [self.python, str(Path(self.neat_dir) / SCRIPT),
               "--job", str(job.out_dir / "job.json")]
        try:
            proc = run(cmd, tool="generation", timeout=self.timeout, cwd=self.neat_dir)
        except ToolFailed as exc:
            job._finish("error", error=str(exc))
            return
        except Exception as exc:  # a bug here must not leave the job "running"
            job._finish("error", error="%s: %s" % (type(exc).__name__, exc))
            return

        job.stderr_tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        payload = _last_json_line(proc.stdout or "")
        if payload is None:
            job._finish("error", error="generation produced no result (exit %s). %s"
                        % (proc.returncode, job.stderr_tail[-300:]))
        elif not payload.get("ok"):
            job._finish("error", error=payload.get("error") or "generation failed")
        else:
            job._finish("done", results=payload.get("results", []))


def _last_json_line(stdout: str) -> dict | None:
    """The child's result: the last line of its output that is a JSON object."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None
