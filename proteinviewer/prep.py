"""Structure preparation, by the separate quickprep package (../quickprep).

quickprep needs OpenMM, PDBFixer and PDB2PQR, so it is installed in its own
environment and run there as ``python -m quickprep``; this module only knows
how to find that interpreter and how to call it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping

from .errors import ToolFailed, ToolUnavailable
from .process import Once, first_executable, output_tail, probe, run

__all__ = ["QuickPrep", "residue_ranges"]

DEFAULT_PYTHON = "~/.pyenv/versions/quickprep/bin/python"


class QuickPrep:
    """Runs the quickprep package in the interpreter it is installed in."""

    name = "quickprep"

    def __init__(self, python: str | None = None, timeout: float = 1800.0):
        self.python = first_executable(python, DEFAULT_PYTHON)
        self.timeout = timeout
        self._version = Once(self._probe_version)

    def _probe_version(self) -> str:
        if not self.python:
            return ""
        return probe([self.python, "-c",
                      "import quickprep; print(quickprep.__version__)"]) or ""

    @property
    def version(self) -> str:
        """quickprep's version in that interpreter, or "" if it does not
        import. Asked once; importing OpenMM takes a moment."""
        return self._version.get()

    @property
    def available(self) -> bool:
        return bool(self.version)

    def describe(self) -> dict:
        return {"available": self.available, "version": self.version,
                "python": self.python or ""}

    def status_line(self) -> str:
        return "quickprep: %s" % ("%s  (%s)" % (self.version, self.python)
                                  if self.available else
                                  "NOT FOUND - Prepare disabled (install "
                                  "../quickprep into its env or set QUICKPREP_PYTHON)")

    def run(self, path: str, workdir: str, ph: float = 7.0,
            name: str | None = None,
            no_rebuild: Mapping[str, Iterable[int]] | None = None) -> dict:
        """Prepare the structure at ``path``; returns the prepared PDB text,
        quickprep's JSON report and its printed summary."""
        if not self.available:
            raise ToolUnavailable(
                "the quickprep package is not installed. Install it into its "
                "environment: ~/.pyenv/versions/quickprep/bin/pip install -e "
                "../quickprep (or set QUICKPREP_PYTHON).", self.name)
        out = os.path.join(workdir, "prepared.pdb")
        report = os.path.join(workdir, "prepared.json")
        # Terminal tails are not rebuilt from the GUI: PDBFixer's tails are
        # the one part of the build that comes out distorted at times.
        cmd = [self.python, "-m", "quickprep", path, "-o", out, "--report", report,
               "--ph", "%g" % ph, "--max-tail", "0", "--name", name or "structure"]
        # Residues the viewer removed or left out of the selection: SEQRES
        # still lists them, and they must not come back as built loops.
        for chain, nums in sorted((no_rebuild or {}).items()):
            cmd += ["--no-rebuild", "%s:%s" % (chain, residue_ranges(nums))]
        proc = run(cmd, tool=self.name, timeout=self.timeout)
        if proc.returncode != 0 or not os.path.exists(out):
            raise ToolFailed("preparation failed: " + output_tail(proc), self.name)
        with open(out) as fh, open(report) as rh:
            return {"data": fh.read(), "report": json.load(rh),
                    "summary": proc.stdout}


def residue_ranges(nums: Iterable[int]) -> str:
    """[5, 6, 7, 8, 12] -> "5-8,12"."""
    nums = sorted({int(n) for n in nums})
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else "%d-%d" % (nums[i], nums[j]))
        i = j + 1
    return ",".join(out)
