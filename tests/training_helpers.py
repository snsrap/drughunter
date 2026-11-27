"""Shared by the training tests: ligandbench's ``train`` as a stand-in, behind the
real adapter, and helpers to wait on a job without sleeping for a fixed time.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from proteinviewer.ligandbench import Ligandbench

from .helpers import fake_executable

STANDIN = Path(__file__).resolve().parent / "training_standin.py"


def standin_python(directory: Path, info: dict | None = None, *, working: bool = True) -> str:
    """An executable that runs the stand-in wherever ``python -m ligandbench ...``
    would run. ``info`` is what ``ligandbench info`` adds to its answer (the
    ``xgboost`` key, say); ``working=False`` is an installation that cannot run.
    """
    if not working:
        return fake_executable(directory, "broken-python", "import sys\nsys.exit(1)\n")
    return fake_executable(
        directory,
        "standin-python",
        "import runpy\nrunpy.run_path(%r, run_name='__main__', init_globals={'INFO': %r})\n"
        % (str(STANDIN), info or {"xgboost": "3.2.0"}),
    )


def standin_tool(directory: Path, info: dict | None = None, **kw) -> Ligandbench:
    return Ligandbench(standin_python(directory, info, **kw))


def dataset_dir(root: Path, name: str, **behavior) -> Path:
    """A directory standing in for a ready dataset, with the behaviour the
    stand-in is to show for it.
    """
    path = root / name
    path.mkdir(parents=True)
    (path / "behavior.json").write_text(json.dumps(behavior))
    return path


def release(path: Path) -> None:
    """Let a gated stand-in go on."""
    (path / "release").write_text("")


def wait_for(predicate, timeout: float = 30.0, what: str = "the condition"):
    """Poll until ``predicate()`` is truthy; returns its value. Fails with ``what``."""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() > deadline:
            raise AssertionError("timed out waiting for %s" % what)
        time.sleep(0.02)


def wait_state(training, run_id: str, *states: str, timeout: float = 30.0) -> dict:
    return wait_for(
        lambda: (p := training.progress(run_id))["state"] in states and p,
        timeout,
        "run %s to be %s" % (run_id, " or ".join(states)),
    )


def alive(pid: int) -> bool:
    """Whether a process is still running (a zombie does not count)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        stat = Path("/proc/%d/stat" % pid).read_text()
    except OSError:
        return True
    return stat.rsplit(")", 1)[-1].split()[0] != "Z"
