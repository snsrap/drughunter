"""lacuna: pockets that only a moving protein shows.

fpocket looks at one structure. A cryptic pocket is closed or shallow in the
unbound form and opens on binding, so a detector given one snapshot finds
nothing there. lacuna generates a conformational ensemble from the single
structure it is handed, detects pockets in every conformer, clusters them
across the ensemble and ranks what comes out. Two numbers come back that a
single-structure run cannot produce:

``persistence``
    the fraction of conformers the pocket appears in. Rank alone is
    misleading: measured on TEM-1 (1BTL), the top-ranked pocket over the
    allosteric site appeared in 1 conformer of 21, while a lower-ranked pocket
    covering more of the same site appeared in 19. Persistence is what
    separates those two, so it is reported per pocket rather than folded into
    the ranking.

``crypticity``
    how much the pocket opens relative to the input structure.

Backends
--------
``nma``     elastic-network normal modes. No force field, no solvent, no time
            axis -- an eigendecomposition and a ramp of mode amplitudes. Needs
            no GPU and takes about two seconds for a 260-residue chain.
``openmm``  short implicit-solvent MD. Needs openmm and pdbfixer in lacuna's
            own interpreter, and OpenMM's CUDA platform ships only in the
            conda-forge build -- a pip install runs this on the CPU.

            It is **not reproducible**. The backend seeds its velocities, but
            GPU force summation is not bit-identical between runs and 50 ps of
            chaotic dynamics amplifies the difference. Three runs over the
            same PTP1B input (1SUG) ranked the allosteric site 9th, 7th and
            10th, and recovered the alpha-6/alpha-7 half of it once in three.
            Treat one MD run as a single draw rather than a measurement, and
            reach for nma when the number has to be repeatable -- nma is an
            eigendecomposition and returns the same ensemble every time.

Residue selection
-----------------
Pockets are resolved to residues with ``select_residues``, driven by the
pseudoatoms lacuna writes for each site. lacuna also reports its own lining
residues, and they are deliberately not used: ProteinViewer hands residue
selections to NEAT-POCKET, which was trained on ``cut_pocket`` -- whole
residues within a cutoff of the site. Taking lacuna's list instead would put a
second, slightly different definition of "pocket" into the generation path.

The pocket pseudoatoms stand in for fpocket's alpha-sphere centres, so the
viewer's sphere overlay draws them as it draws fpocket's.

CLI
---
    pocketbench detect lacuna structure.cif [--backend nma|openmm] [--conformers 20]
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any

from ..errors import ToolFailed, ToolUnavailable
from ..pockets import DEFAULT_CUTOFF, DetectionResult, Pocket, select_residues
from ..process import Once, first_executable, output_tail, probe, run
from ..structure import Structure
from . import Option, PocketDetector

__all__ = ["LacunaDetector", "BACKENDS", "DETECTORS"]

BACKENDS = ("nma", "openmm")
# 'alpha' only proposes where there is a concavity, which an apo cryptic site
# often has none of; 'surface-fusion' pools a learned surface model with it.
DETECTORS = ("alpha", "surface", "surface-fusion")

DEFAULT_CONFORMERS = 20
DEFAULT_TOP = 10

# The MD-capable environment first: it carries OpenMM as well, so one binary
# serves both backends.
SEARCH_PATHS = ("~/micromamba/envs/lacuna-gpu/bin/lacuna",
                "~/.pyenv/versions/lacuna/bin/lacuna")


class LacunaDetector(PocketDetector):
    """Runs the lacuna CLI and resolves each pocket to whole residues.

    lacuna needs numpy, scipy and biopython, and its MD backend needs OpenMM,
    so it lives in its own environment and is run as a command rather than
    imported.
    """

    name = "lacuna"
    OPTIONS = (Option("backend", "nma", choices=BACKENDS),
               Option("detector", "alpha", choices=DETECTORS),
               Option("conformers", DEFAULT_CONFORMERS, lo=1, hi=500))

    def __init__(self, binary: str | None = None, timeout: float = 3600.0):
        self.binary = first_executable(binary, *SEARCH_PATHS, shutil.which("lacuna"))
        self.timeout = timeout
        self._version = Once(self._probe_version)
        self._backends = Once(self._probe_backends)
        self._platform = Once(self._probe_platform)

    # -- what this installation can do ------------------------------------
    @property
    def python(self) -> str:
        """The interpreter beside the CLI, used to ask what it can import."""
        return os.path.join(os.path.dirname(self.binary or ""), "python")

    @property
    def version(self) -> str:
        """lacuna's version, or "" if the CLI does not run."""
        return self._version.get()

    @property
    def available(self) -> bool:
        return bool(self.version)

    @property
    def backends(self) -> tuple[str, ...]:
        """Which backends this installation can actually run.

        The MD backend raises at generate time if openmm or pdbfixer is
        missing, which would surface as a failed run minutes in. Asked once,
        up front, so a caller can offer only what works.
        """
        return self._backends.get()

    def platform(self) -> str:
        """The OpenMM platform the MD backend will pick, for the status line.

        OpenMM's CUDA platform ships only in the conda-forge build; a wheel
        install silently runs on CPU, which is worth saying out loud before
        someone waits on a long run.
        """
        return self._platform.get()

    def _probe_version(self) -> str:
        out = probe([self.binary, "--version"]) if self.binary else None
        # "lacuna, version 1.2.0"
        return out.split()[-1] if out else ""

    def _probe_backends(self) -> tuple[str, ...]:
        if self.available and os.access(self.python, os.X_OK):
            if probe([self.python, "-c", "import openmm, pdbfixer"], 300) is not None:
                return ("nma", "openmm")
        return ("nma",)

    def _probe_platform(self) -> str:
        if "openmm" not in self.backends:
            return ""
        return probe([self.python, "-c",
                      "import openmm as mm;"
                      "from lacuna.ensemble.openmm_backend import _select_platform;"
                      "print(_select_platform(mm).getName())"], 300) or "unknown"

    def describe(self) -> dict:
        return {"available": self.available, "version": self.version,
                "binary": self.binary or "", "backends": list(self.backends)}

    def status_line(self) -> str:
        if not self.available:
            return ("lacuna: NOT FOUND - ensemble pocket detection disabled "
                    "(pip install lacuna-pockets in its own env, or set LACUNA_BIN)")
        # The MD backend runs on whatever platform OpenMM finds, and a pip
        # install of OpenMM has no CUDA plugin at all -- worth knowing before
        # waiting on a run that silently went to the CPU.
        platform = self.platform()
        return ("lacuna: %s  (%s)  backends: %s%s"
                % (self.version, self.binary, "/".join(self.backends),
                   "  openmm platform: " + platform if platform else ""))

    # -- running -----------------------------------------------------------
    def run(self, structure: Structure, workdir: str, *,
            cutoff: float = DEFAULT_CUTOFF,
            ligand_resnames: frozenset[str] = frozenset(),
            backend: str = "nma", detector: str = "alpha",
            conformers: int = DEFAULT_CONFORMERS, top: int = DEFAULT_TOP,
            no_sequence: bool = True, **options) -> DetectionResult:
        """Detect pockets over an ensemble and resolve each to whole residues."""
        if not self.available:
            raise ToolUnavailable(
                "lacuna not found. Install it into its own environment "
                "(pip install lacuna-pockets) or set LACUNA_BIN.", self.name)
        if backend not in self.backends:
            raise ToolUnavailable(
                "the %s backend needs openmm and pdbfixer in lacuna's "
                "environment (%s)." % (backend, self.python), self.name)

        receptor, pdb_path = self.write_receptor(structure, workdir,
                                                 "lacuna_receptor.pdb",
                                                 ligand_resnames)
        out_dir = os.path.join(workdir, "lacuna_out")
        # lacuna keeps per-pocket files by index; a stale set from a previous
        # run with more pockets would be read back as this run's.
        shutil.rmtree(out_dir, ignore_errors=True)

        cmd = [self.binary, "discover", pdb_path,
               "--backend", backend, "--detector", detector,
               "--conformers", str(int(conformers)), "--top", str(int(top)),
               "--emit-pocket-pdbs", "--emit-conformers",
               "-o", out_dir, "--quiet"]
        # The surface detectors use ESM-2 unless told not to; without the
        # 'plm' extra installed that is what they do anyway.
        if detector != "alpha" and no_sequence:
            cmd.append("--no-sequence")

        started = time.monotonic()
        proc = run(cmd, tool=self.name, timeout=self.timeout)
        elapsed = time.monotonic() - started

        report_path = os.path.join(out_dir, "pocket_report.json")
        if proc.returncode != 0 or not os.path.exists(report_path):
            raise ToolFailed("lacuna failed (exit %s): %s"
                             % (proc.returncode, output_tail(proc)), self.name)
        with open(report_path) as fh:
            report = json.load(fh)

        pockets: list[Pocket] = []
        for i, entry in enumerate(report.get("pockets") or []):
            # pocket_<i>_site.pdb holds pseudoatoms for pockets[i]; their mean
            # is the centroid in the report, which is how the pairing is
            # checked rather than assumed.
            centers = _read_pseudoatoms(
                os.path.join(out_dir, "pocket_%d_site.pdb" % i))
            if not centers:
                centers = [tuple(entry["centroid"])]
            p = Pocket(
                rank=int(entry.get("rank", i + 1)),
                # lacuna ranks with a fitted model and reports no fpocket-style
                # score; the columns that carry its ranking are below.
                score=float("nan"),
                druggability=_f(entry.get("druggability")),
                volume=_f(entry.get("volume_A3")),
                centers=centers,
                radii=[1.0] * len(centers),
                persistence=_f(entry.get("persistence")),
                crypticity=_f(entry.get("crypticity")),
                cryptic=bool(entry.get("cryptic")),
            )
            p.residues, p.n_atoms = select_residues(structure, centers, cutoff)
            pockets.append(p)

        pockets.sort(key=lambda p: p.rank)
        ensemble = os.path.join(
            out_dir, os.path.splitext(os.path.basename(pdb_path))[0]
            + "_ensemble.pdb")
        ensemble = ensemble if os.path.exists(ensemble) else ""
        return DetectionResult(self.name, pockets, cmd, elapsed, len(receptor),
                               cutoff, extra={
            "backend": backend,
            "detector": detector,
            "conformers": int(report.get("n_conformers") or conformers),
            "nCryptic": int(report.get("n_cryptic_pockets") or 0),
            # A multi-model PDB: model 1 is the input, the rest are generated.
            "ensemble": os.path.basename(ensemble),
            "ensemblePath": ensemble,
        })


def _f(v) -> float:
    """A float, or NaN when the report leaves the field out."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _read_pseudoatoms(path: str) -> list[tuple[float, float, float]]:
    """Coordinates from one of lacuna's pocket pseudoatom PDBs."""
    out: list[tuple[float, float, float]] = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            if line[:6] in ("ATOM  ", "HETATM"):
                try:
                    out.append((float(line[30:38]), float(line[38:46]),
                                float(line[46:54])))
                except ValueError:
                    continue
    return out
