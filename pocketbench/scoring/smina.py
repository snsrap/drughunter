"""smina, and gnina which takes the same flags.

Neither is bundled: both are found on PATH and run as a subprocess, as fpocket
is. smina reads the receptor as PDB and the ligand as SDF directly, so there
is no PDBQT conversion step anywhere in this path -- which is why it comes
first and Vina does not.

gnina is smina with a convolutional network bolted on. It is driven here with
``--cnn_scoring none``, so it computes smina's own scoring function and the
numbers mean the same thing; ``--no_gpu`` keeps a scoring run off a card that
a generation run may be using.

CLI
---
    pocketbench score-poses receptor.pdb poses.sdf [--mode minimize] \
        [--site pockets.spheres.pdb --site-rank 1]
"""

from __future__ import annotations

import os
import shutil

from ..process import Once, first_executable, probe
from . import PoseScorer, pyenv_binaries

__all__ = ["SminaScorer", "GninaScorer"]


class SminaScorer(PoseScorer):
    """Runs the smina binary and reads its scores back from the SDF."""

    name = "smina"

    def __init__(self, binary: str | None = None, timeout: float = 3600.0):
        self.binary = first_executable(binary, self._on_path(), *pyenv_binaries(self.name))
        self.timeout = timeout
        # One pose at a time, when the whole file could not be done at once.
        self.pose_timeout = 300.0
        self._version = Once(self._probe_version)

    def _on_path(self) -> str | None:
        """The tool on PATH, if it is the tool and not a pyenv shim.

        A shim is on PATH whatever environment is active and refuses to run
        from the wrong one, so taking it would make the scorer look installed
        and then fail on the first pose. The real binary is found in the
        environments instead.
        """
        found = shutil.which(self.name)
        shims = os.path.expanduser("~/.pyenv/shims") + os.sep
        return None if found and found.startswith(shims) else found

    def _probe_version(self) -> str:
        """What the tool calls itself, short enough to be a column.

        smina says "smina 2020.12.10"; gnina says "gnina v1.1 master:<sha>
        Built <date>". The name is dropped (it is this scorer's name) and the
        build date with it, leaving the version and, for gnina, the commit --
        which is what identifies a build.
        """
        if not self.binary:
            return ""
        out = probe([self.binary, "--version"], 60) or ""
        words = out.splitlines()[0].split() if out.strip() else []
        if words and words[0].lower() == self.name:
            words = words[1:]
        return " ".join(words[:2])

    @property
    def version(self) -> str:
        return self._version.get()

    def describe(self) -> dict:
        return {
            "available": self.available,
            "binary": self.binary or "",
            "version": self.version if self.available else "",
        }

    def status_line(self) -> str:
        if not self.available:
            return "%s: NOT FOUND - pose scoring disabled (put %s on PATH or set %s_BIN)" % (
                self.name,
                self.name,
                self.name.upper(),
            )
        return "%s: %s  (%s)" % (self.name, self.binary, self.version or "version unknown")


class GninaScorer(SminaScorer):
    """gnina, with its network turned off so it scores like smina."""

    name = "gnina"

    def base_flags(self) -> list[str]:
        return ["--cnn_scoring", "none", "--no_gpu"]
