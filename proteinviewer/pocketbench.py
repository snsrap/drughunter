"""Pocket detection and superposition, by the pocketbench package that ships
beside this one in drughunter.

pocketbench drives fpocket and lacuna, turns what they find into residues, and
superposes structures. It is run as a command, ``python -m pocketbench``, and
never imported: in the server's own interpreter, unless POCKETBENCH_PYTHON
names another. This module only knows which interpreter that is, how to call
it, and how to read its answers back -- including
whose problem a failure was, which pocketbench says with its exit code (2 a
bad input, 3 a tool not installed, 4 a tool that ran and failed).

What each detector can do and which options it takes are asked once, up
front (``pocketbench detectors --json``), so a request is checked here
against the options pocketbench publishes: a bad value is answered at once,
and a run's cache key holds every option, defaults included.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from . import validate
from .errors import BadRequest, ToolFailed, ToolUnavailable
from .process import Once, first_executable, output_tail, probe, run

__all__ = ["Pocketbench", "residue_spec"]


class Pocketbench:
    """Runs the pocketbench package in the interpreter it is installed in."""

    name = "pocketbench"

    def __init__(
        self,
        python: str | None = None,
        *,
        fpocket_bin: str | None = None,
        lacuna_bin: str | None = None,
        timeout: float = 3700.0,
    ):
        # drughunter installs pocketbench beside the server, so the
        # interpreter running the server is the one that has it.
        self.python = first_executable(python, sys.executable)
        # A binary the viewer's own settings name goes through to pocketbench,
        # so FPOCKET_BIN and LACUNA_BIN mean what they always meant.
        self.tool_flags = (["--fpocket-bin", fpocket_bin] if fpocket_bin else []) + (
            ["--lacuna-bin", lacuna_bin] if lacuna_bin else []
        )
        self.timeout = timeout  # lacuna's MD backend is allowed an hour
        self._info = Once(self._probe)

    def _cmd(self, *args: str) -> list[str]:
        return [self.python or "python", "-m", "pocketbench", *args]

    def _probe(self) -> dict:
        """What pocketbench and its detectors can do. Probing lacuna imports
        OpenMM in its environment, which takes seconds, so this is asked once.
        """
        if not self.python:
            return {}
        out = probe(self._cmd("detectors", "--json", *self.tool_flags), 600)
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

    @property
    def version(self) -> str:
        return self.info.get("version", "")

    def describe(self) -> dict:
        return {"available": self.available, "version": self.version, "python": self.python or ""}

    def detectors(self) -> dict[str, dict]:
        """Each detector's status, by name: what ``/api/status`` lists."""
        return self.info.get("detectors", {})

    def additives(self) -> list[str]:
        """Residues every detector drops on its own, so the page does not
        offer to delete them.
        """
        return self.info.get("additives", [])

    def status_lines(self) -> list[str]:
        if not self.available:
            return [
                "pocketbench: NOT FOUND - pocket detection and superposition disabled "
                "(%s cannot run it: start the server from the environment drughunter "
                "is installed in, or set POCKETBENCH_PYTHON)" % (self.python or "no python")
            ]
        return ["pocketbench: %s  (%s)" % (self.version, self.python)] + [
            d.get("status", name) for name, d in self.detectors().items()
        ]

    # -- detection ---------------------------------------------------------
    def options(self, method: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        """``method``'s own options from a request, checked against what
        pocketbench says it takes, every default filled in.
        """
        self._require()
        spec = self.detectors().get(method)
        if spec is None:
            raise BadRequest(
                "unknown method %r (want %s)" % (method, " or ".join(self.detectors()))
            )
        out = {}
        for name, o in spec.get("options", {}).items():
            if "choices" in o:
                out[name] = validate.choice(raw, name, o["choices"], o["default"])
            else:
                out[name] = validate.integer(
                    raw, name, o["default"], lo=o.get("min"), hi=o.get("max")
                )
        return out

    def detect(
        self,
        method: str,
        path: str,
        workdir: str,
        *,
        cutoff: float,
        ligands: Iterable[str] = (),
        options: Mapping[str, Any] | None = None,
    ) -> dict:
        """Detect pockets in the file at ``path``, the tool's files kept in
        ``workdir``. Returns ``{tool, pockets, meta}``, the pockets in the JSON
        form the page draws.
        """
        cmd = self._cmd(
            "detect",
            method,
            path,
            "--workdir",
            workdir,
            "--cutoff",
            "%g" % cutoff,
            "--json",
            *self.tool_flags,
        )
        if ligands:
            cmd += ["--ligand", ",".join(sorted(ligands))]
        for k, v in sorted((options or {}).items()):
            cmd += ["--" + k, str(v)]
        return self._call(cmd)

    # -- superposition -----------------------------------------------------
    def superpose(
        self,
        mobile: str,
        reference: str,
        out: str,
        *,
        on: Iterable[tuple[str, int, str]] = (),
        mobile_on: Iterable[tuple[str, int, str]] = (),
    ) -> dict:
        """Superpose the file at ``mobile`` onto the one at ``reference`` and
        write the moved text to ``out``. ``on`` and ``mobile_on`` restrict the
        fit to those residues, (chain, number, insertion code), of either
        side. Returns the fit.
        """
        cmd = self._cmd("superpose", mobile, reference, "-o", out, "--json")
        for flag, keys in (("--on", on), ("--mobile-on", mobile_on)):
            spec = residue_spec(keys)
            if spec:
                cmd += [flag, spec]
        return self._call(cmd)

    # -- plumbing ----------------------------------------------------------
    def _require(self) -> None:
        if not self.available:
            raise ToolUnavailable(
                "pocketbench does not run in %s. Start the server from the environment "
                "drughunter is installed in, or set POCKETBENCH_PYTHON."
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
                "pocketbench failed (exit %s): %s" % (proc.returncode, output_tail(proc)), self.name
            ) from None
        if proc.returncode == 0:
            return out
        message = out.get("error") or output_tail(proc)
        if proc.returncode == 2:
            raise BadRequest(message)
        if proc.returncode == 3:
            raise ToolUnavailable(message, self.name)
        raise ToolFailed(message, self.name)


def residue_spec(keys: Iterable[tuple[str, int, str]]) -> str:
    """Residues as pocketbench takes them: ``A:1-29,A:31,A:52A``.

    Runs of consecutive numbers go as ranges, which keeps a whole chain short
    on a command line. pocketbench reads a range as including any inserted
    residues inside it (52A in 50-55) -- harmless for a fit restriction.
    """
    by_chain: dict[str, set[int]] = {}
    inserted: list[str] = []
    for chain, resi, icode in keys:
        if icode:
            inserted.append("%s:%d%s" % (chain, int(resi), icode))
        else:
            by_chain.setdefault(chain, set()).add(int(resi))
    parts = []
    for chain, nums in sorted(by_chain.items()):
        nums_sorted, i = sorted(nums), 0
        while i < len(nums_sorted):
            j = i
            while j + 1 < len(nums_sorted) and nums_sorted[j + 1] == nums_sorted[j] + 1:
                j += 1
            parts.append(
                "%s:%d" % (chain, nums_sorted[i])
                if i == j
                else "%s:%d-%d" % (chain, nums_sorted[i], nums_sorted[j])
            )
            i = j + 1
    return ",".join(parts + sorted(inserted))
