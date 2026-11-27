"""Ligand datasets, by the ligandbench package that ships beside this one in
drughunter.

ligandbench reads a CSV of molecules with RDKit: it lists the columns, resolves
the user's choices into rows with a verdict on each, and draws structures. It
is run as a command, ``python -m ligandbench``, and never imported: in the
server's own interpreter, unless LIGANDBENCH_PYTHON names another. This module
only knows which interpreter that is, how to call it, and how to read its
answers back -- including whose problem a failure was, which ligandbench says
with its exit code (2 a file or choice that cannot be used, 3 RDKit missing).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import BadRequest, Incompatible, ToolFailed, ToolUnavailable
from .process import Once, first_executable, output_tail, probe, run

__all__ = ["Ligandbench"]


class Ligandbench:
    """Runs the ligandbench package in the interpreter it is installed in."""

    name = "ligandbench"

    def __init__(self, python: str | None = None, *, timeout: float = 600.0):
        # drughunter installs ligandbench beside the server, so the interpreter
        # running the server is the one that has it.
        self.python = first_executable(python, sys.executable)
        self.timeout = timeout
        self._info = Once(self._probe)

    def _cmd(self, *args: str) -> list[str]:
        return [self.python or "python", "-m", "ligandbench", *args]

    def _probe(self) -> dict:
        """What this installation can do. Importing RDKit takes a second, so
        this is asked once.
        """
        if not self.python:
            return {}
        out = probe(self._cmd("info"), 120)
        try:
            return json.loads(out) if out else {}
        except ValueError:
            return {}

    # -- what this installation can do -------------------------------------
    @property
    def info(self) -> dict:
        return self._info.get()

    @property
    def available(self) -> bool:
        return bool(self.info)

    def describe(self) -> dict:
        return {
            "available": self.available,
            "version": self.info.get("version", ""),
            "rdkit": self.info.get("rdkit", ""),
            "depiction": self.info.get("depiction", ""),
            "summary": self.info.get("summary", ""),
            "xgboost": self.info.get("xgboost", ""),
            "python": self.python or "",
        }

    def status_line(self) -> str:
        if not self.available:
            return (
                "ligandbench: NOT FOUND - ligand datasets disabled (%s cannot run it with "
                "RDKit: start the server from the environment drughunter is installed in, "
                "or set LIGANDBENCH_PYTHON)" % (self.python or "no python")
            )
        return "ligandbench: %s  (RDKit %s, %s)" % (
            self.info.get("version"),
            self.info.get("rdkit"),
            self.python,
        )

    # -- datasets ----------------------------------------------------------
    def inspect(self, csv_path: str) -> dict:
        """The CSV's columns, their types and its structure candidates."""
        return self._call(self._cmd("inspect", csv_path))

    def resolve(self, csv_path: str, config: Mapping[str, Any], out_dir: str) -> dict:
        """Resolve the CSV under the user's choices, writing ``rows.json`` and
        ``dataset.json`` into ``out_dir``. Returns the metadata.
        """
        with tempfile.TemporaryDirectory(prefix="ligandbench-") as tmp:
            cfg = os.path.join(tmp, "config.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump(config, fh, default=dict)  # a request's choices are read-only mappings
            return self._call(self._cmd("resolve", csv_path, "--config", cfg, "--out", out_dir))

    def split(self, dataset_dir: str, strategy: str, seed: int) -> dict:
        """Five repeated holdouts of the ready dataset ``resolve`` wrote into
        ``dataset_dir`` (its ``rows.json`` and ``dataset.json``), by ``random`` or
        ``scaffold`` groups. Returns ligandbench's result: ``status`` is ``ok``
        with the five repeats, or ``split_unavailable`` with why -- both answers.
        """
        return self._call(
            self._cmd("split", dataset_dir, "--strategy", strategy, "--seed", str(seed))
        )

    def depict(self, smiles: Sequence[str]) -> dict:
        """``{version, svgs}``: a drawing per SMILES, in order; an empty string
        for one that will not draw.
        """
        with tempfile.TemporaryDirectory(prefix="ligandbench-") as tmp:
            req = os.path.join(tmp, "request.json")
            with open(req, "w", encoding="utf-8") as fh:
                json.dump({"smiles": list(smiles)}, fh)
            return self._call(self._cmd("depict", req))

    def summarize(self, dataset_dir: str) -> dict:
        """The dashboard's statistics for the resolved dataset whose
        ``dataset.json`` and ``rows.json`` are in ``dataset_dir``: one JSON
        object, no per-row data.
        """
        return self._call(self._cmd("summarize", dataset_dir))

    # -- training ----------------------------------------------------------
    def training_unavailable(self) -> str | None:
        """Why this installation cannot train, or None when it can.

        ``ligandbench info`` reports the XGBoost it found beside RDKit's version;
        an older ligandbench says nothing about it, which is "unknown" and not
        "missing" -- so only a key that is there and empty counts against it.
        Tighten this one function once every ligandbench reports it.
        """
        if not self.available:
            return "ligandbench does not run in %s (it needs RDKit)" % (
                self.python or "this Python"
            )
        if "xgboost" in self.info and not self.info["xgboost"]:
            return "XGBoost is not installed in %s" % self.python
        return None

    def start_training(
        self, dataset_dir: str, run_dir: str, seed: int, strategy: str, profile: str
    ) -> subprocess.Popen:
        """Start ``ligandbench train`` and return the running process; the caller
        waits for it (``communicate``) and hands what it printed to
        :meth:`training_outcome`.

        The child leads its own process group, so cancelling it can end whatever it
        started too, and it is held to one thread: the search is seeded and
        single-threaded by contract, and BLAS pools would only add load beyond
        what the server's limits count.
        """
        self._require()
        env = dict(os.environ)
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[var] = "1"
        cmd = self._cmd(
            "train",
            dataset_dir,
            "--out",
            run_dir,
            "--seed",
            str(seed),
            "--production-strategy",
            strategy,
            "--profile",
            profile,
        )
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolFailed("ligandbench could not be started: %s" % exc, self.name) from exc

    def training_outcome(self, returncode: int | None, stdout: str, stderr: str) -> dict:
        """What a finished ``ligandbench train`` said: its JSON line, as a dict
        whose ``status`` is ``completed`` or ``production_strategy_unavailable``.

        Anything else raises, in the words of ``_call``: exit 2 is the user's
        problem (:class:`BadRequest`), 3 a missing tool (:class:`ToolUnavailable`),
        and a crash, a signal or output that is not JSON a failure of the tool's.
        """
        out = _last_json_object(stdout)
        tail = output_tail(subprocess.CompletedProcess([], returncode or 0, stdout, stderr))
        if returncode is not None and returncode < 0:
            raise ToolFailed(
                "ligandbench train was killed by signal %d. %s" % (-returncode, tail), self.name
            )
        if out is None:
            raise ToolFailed(
                "ligandbench train printed no result (exit %s): %s" % (returncode, tail), self.name
            )
        if returncode == 0:
            return out
        message = out.get("error") or tail
        if returncode == 2:
            raise BadRequest(message)
        if returncode == 3:
            raise ToolUnavailable(message, self.name)
        raise ToolFailed(message, self.name)

    # -- prediction --------------------------------------------------------
    def prediction_unavailable(self) -> str | None:
        """Why this installation cannot apply a saved model, or None when it can."""
        if not self.available:
            return "ligandbench does not run in %s (it needs RDKit)" % (
                self.python or "this Python"
            )
        if not self.info.get("xgboost"):
            return "XGBoost is not installed in %s" % self.python
        if "predicts" not in self.info:
            return "this ligandbench cannot apply a saved model (it predates prediction)"
        return None

    def model_compatibility(self, versions: Mapping[str, Any] | None, schema: str | None) -> str | None:
        """Why a model trained with ``versions`` cannot be applied by this installation,
        or None. A first look from what the registry recorded, for the selector: the
        command itself checks again, against the files, before it predicts.
        """
        reason = self.prediction_unavailable()
        if reason:
            return reason
        if schema is not None and schema not in (self.info.get("predicts") or []):
            return "its run is %r, which this build cannot apply" % (schema,)
        versions = versions or {}
        for name in ("rdkit", "xgboost"):
            if versions.get(name) != self.info.get(name):
                return "trained with %s %s; this server runs %s %s" % (
                    name, versions.get(name) or "(unrecorded)", name, self.info.get(name)
                )
        if versions.get("featuriser") != self.info.get("featuriser"):
            return "its features come from featuriser %s; this build implements %s" % (
                versions.get("featuriser") or "(unrecorded)", self.info.get("featuriser")
            )
        return None

    def start_prediction(self, run_dir: str, dataset_dir: str, out_dir: str) -> subprocess.Popen:
        """Start ``ligandbench predict`` (the saved model in ``run_dir`` on every row of the
        dataset in ``dataset_dir``, into the existing ``out_dir``) and return the process;
        the caller waits for it and hands what it printed to :meth:`prediction_outcome`.
        Held to one thread and a group of its own, like training.
        """
        self._require()
        env = dict(os.environ)
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[var] = "1"
        cmd = self._cmd("predict", run_dir, dataset_dir, "--out", out_dir)
        try:
            return subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, start_new_session=True,
            )
        except OSError as exc:
            raise ToolFailed("ligandbench could not be started: %s" % exc, self.name) from exc

    def prediction_outcome(self, returncode: int | None, stdout: str, stderr: str) -> dict:
        """What a finished ``ligandbench predict`` said: its JSON line (``status``
        ``completed``). A model that cannot be applied is :class:`Incompatible`; anything
        else raises as :meth:`training_outcome` does.
        """
        out = _last_json_object(stdout)
        tail = output_tail(subprocess.CompletedProcess([], returncode or 0, stdout, stderr))
        if returncode is not None and returncode < 0:
            raise ToolFailed(
                "ligandbench predict was killed by signal %d. %s" % (-returncode, tail), self.name
            )
        if out is None:
            raise ToolFailed(
                "ligandbench predict printed no result (exit %s): %s" % (returncode, tail), self.name
            )
        if returncode == 0:
            return out
        message = out.get("error") or tail
        if returncode == 2:
            raise (Incompatible if out.get("code") == "incompatible" else BadRequest)(message)
        if returncode == 3:
            raise ToolUnavailable(message, self.name)
        raise ToolFailed(message, self.name)

    # -- plumbing ----------------------------------------------------------
    def _require(self) -> None:
        if not self.available:
            raise ToolUnavailable(
                "ligandbench does not run in %s (it needs RDKit). Start the server from the "
                "environment drughunter is installed in, or set LIGANDBENCH_PYTHON."
                % (self.python or "this Python"),
                self.name,
            )

    def _call(self, cmd: list[str]) -> dict:
        self._require()
        proc = run(cmd, tool=self.name, timeout=self.timeout)
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            raise ToolFailed(
                "ligandbench failed (exit %s): %s" % (proc.returncode, output_tail(proc)), self.name
            ) from None
        if proc.returncode == 0:
            return out
        message = out.get("error") or output_tail(proc)
        if proc.returncode == 2:
            raise BadRequest(message)
        if proc.returncode == 3:
            raise ToolUnavailable(message, self.name)
        raise ToolFailed(message, self.name)


def _last_json_object(stdout: str) -> dict | None:
    """The last line of ``stdout`` that is a JSON object: what a command prints
    last is its answer, whatever a library wrote before it.
    """
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                out = json.loads(line)
            except ValueError:
                continue
            if isinstance(out, dict):
                return out
    return None
