#!/usr/bin/env python3
"""Run NEAT-POCKET generation for selected pockets, as a background job.

NEAT-POCKET needs torch and its own environment, so it cannot be imported here:
``serve.py`` runs on bare Python. It is invoked as a subprocess of
``NEAT-POCKET/scripts/generate_from_pocket.py``, which takes pockets as residue
selections and needs no reference ligand.

Generation is slow enough (tens of seconds per pocket) that a request cannot
wait on it, so jobs run on a thread and the viewer polls. The child keeps
``progress.json`` up to date in the job directory; that file is the source of
truth for progress.

Locations are discovered, and overridable with ``NEAT_PYTHON`` and
``NEAT_POCKET_DIR``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

__all__ = ["Runner", "GenerationError"]

DEFAULT_NEAT_DIRS = ["../NEAT-POCKET", "~/projects/NEAT-POCKET"]
PYENV_VERSIONS = "~/.pyenv/versions"


class GenerationError(RuntimeError):
    """NEAT-POCKET is not reachable, or a job could not be started."""


def _find_neat_dir() -> str | None:
    env = os.environ.get("NEAT_POCKET_DIR")
    cands = [env] if env else []
    here = Path(__file__).resolve().parent
    cands += [str(here / d) for d in DEFAULT_NEAT_DIRS]
    cands += DEFAULT_NEAT_DIRS
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser().resolve()
        if (p / "scripts" / "generate_from_pocket.py").is_file():
            return str(p)
    return None


def _find_python(neat_dir: str | None) -> str | None:
    """The interpreter that has torch and the neat package.

    Not this one: serve.py deliberately runs on the stdlib only.
    """
    env = os.environ.get("NEAT_PYTHON")
    if env and Path(env).expanduser().is_file():
        return str(Path(env).expanduser())

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
    which = shutil.which("python3")
    return which


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

    def progress(self) -> dict:
        """Merge our own state with whatever the child last wrote."""
        info = {"id": self.id, "state": self.state,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "requested": self.spec.get("n"),
                "ranks": [p["rank"] for p in self.spec.get("pockets", [])]}
        child = self.out_dir / "progress.json"
        if child.is_file():
            try:
                info.update({k: v for k, v in json.loads(child.read_text()).items()
                             if k != "state" or self.state == "running"})
            except Exception:
                pass
        if self.state in ("done", "error"):
            info["state"] = self.state
        if self.error:
            info["error"] = self.error
        if self.results:
            info["results"] = self.results
        return info

    def table(self) -> list[dict]:
        """Per-molecule rows (properties and 2D depictions) written by the child."""
        path = self.out_dir / "molecules.json"
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except Exception:
                return []
        return []

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


class Runner:
    def __init__(self):
        self.neat_dir = _find_neat_dir()
        self.python = _find_python(self.neat_dir)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.neat_dir and self.python)

    def describe(self) -> dict:
        return {"available": self.available, "neatDir": self.neat_dir or "",
                "python": self.python or ""}

    def start(self, structure_path: str, pockets: list[dict], out_root: Path,
              n: int = 50, cfg: float = 0.5, batch_size: int = 50,
              max_atoms: int = 100) -> Job:
        if not self.available:
            raise GenerationError(
                "NEAT-POCKET not found. Set NEAT_POCKET_DIR to the repository and "
                "NEAT_PYTHON to an interpreter that can import torch and neat."
            )
        if not pockets:
            raise GenerationError("no pockets selected")

        job_id = uuid.uuid4().hex[:12]
        out_dir = Path(out_root) / ("gen_" + job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = {
            "structure": structure_path,
            "pockets": [{"rank": p["rank"], "residues": p["residues"]} for p in pockets],
            "n": int(n), "batch_size": int(batch_size), "cfg": float(cfg),
            "max_atoms": int(max_atoms), "out_dir": str(out_dir),
        }
        (out_dir / "job.json").write_text(json.dumps(spec, indent=1))

        job = Job(job_id, out_dir, spec)
        with self._lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        job.state = "running"
        cmd = [self.python, str(Path(self.neat_dir) / "scripts" / "generate_from_pocket.py"),
               "--job", str(job.out_dir / "job.json")]
        try:
            proc = subprocess.run(cmd, cwd=self.neat_dir, capture_output=True,
                                  text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            job.state, job.error = "error", "generation timed out after 1 h"
            job.finished = time.time()
            return
        except Exception as exc:
            job.state, job.error = "error", "%s: %s" % (type(exc).__name__, exc)
            job.finished = time.time()
            return

        job.finished = time.time()
        job.stderr_tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        payload = None
        for line in reversed((proc.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    payload = json.loads(line)
                    break
                except Exception:
                    continue
        if payload is None:
            job.state = "error"
            job.error = ("generation produced no result (exit %s). %s"
                         % (proc.returncode, job.stderr_tail[-300:]))
            return
        if not payload.get("ok"):
            job.state = "error"
            job.error = payload.get("error") or "generation failed"
            return
        job.results = payload.get("results", [])
        job.state = "done"

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)
