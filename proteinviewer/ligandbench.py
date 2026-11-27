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
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import BadRequest, ToolFailed, ToolUnavailable
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
