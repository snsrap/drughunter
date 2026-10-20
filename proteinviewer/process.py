"""Subprocess plumbing shared by every external-tool adapter.

Every tool the viewer drives (fpocket, lacuna, quickprep, NEAT-POCKET) lives
in its own environment and is run as a command, so each adapter needs the
same few things: find an executable from a list of candidates, ask it a
question once and remember the answer, and run it with a timeout whose
failure reads the same whichever tool it was.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from .errors import ToolFailed

__all__ = ["first_executable", "run", "probe", "output_tail", "Once"]

T = TypeVar("T")


def first_executable(*candidates: str | os.PathLike | None) -> str | None:
    """The first candidate that is an executable file, ``~`` expanded."""
    for c in candidates:
        if not c:
            continue
        p = os.path.expanduser(os.fspath(c))
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def run(
    cmd: Sequence[str], *, tool: str, timeout: float, cwd: str | os.PathLike | None = None
) -> subprocess.CompletedProcess:
    """Run ``cmd`` to completion, capturing text output.

    A non-zero exit is returned, not raised -- each tool says "failed" in its
    own way (no output directory, no report file, a JSON line), so the adapter
    decides. Not starting at all, or running out of time, is raised.
    """
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        raise ToolFailed("%s timed out after %gs" % (tool, timeout), tool) from exc
    except OSError as exc:
        raise ToolFailed("%s could not be started: %s" % (tool, exc), tool) from exc


def probe(cmd: Sequence[str], timeout: float = 120.0) -> str | None:
    """Stripped stdout of a quick check, or None if it fails in any way."""
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def output_tail(proc: subprocess.CompletedProcess, lines: int = 6, chars: int = 500) -> str:
    """The last few lines a failed run printed, for an error message."""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-lines:]
    return " ".join(tail)[-chars:]


class Once(Generic[T]):
    """A value computed on first use, then remembered.

    Probing a tool means starting its interpreter, and some import OpenMM,
    which takes seconds; this makes sure that happens once even when the
    startup banner and the first status request ask at the same moment.
    """

    def __init__(self, compute: Callable[[], T]):
        self._compute = compute
        self._lock = threading.Lock()
        self._done = False
        self._value: T | None = None

    def get(self) -> T:
        if not self._done:
            with self._lock:
                if not self._done:
                    self._value = self._compute()
                    self._done = True
        return self._value  # type: ignore[return-value]
