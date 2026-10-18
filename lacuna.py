#!/usr/bin/env python3
"""A wrapper around lacuna, for pockets that only a moving protein shows.

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
Pockets are resolved to residues here, with ``select_residues`` from
pockets.py, driven by the pseudoatoms lacuna writes for each site. lacuna also
reports its own lining residues, and they are deliberately not used: the
viewer hands residue selections to NEAT-POCKET, which was trained on
``cut_pocket`` -- whole residues within a cutoff of the site. Taking lacuna's
list instead would put a second, slightly different definition of "pocket"
into the generation path.

The pocket pseudoatoms stand in for fpocket's alpha-sphere centres, so the
viewer's existing sphere overlay draws them with no extra work.

CLI
---
    ./lacuna.py structure.cif [--backend nma|openmm] [--conformers 20]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from pockets import (DEFAULT_CUTOFF, FpocketResult, Pocket, Structure,
                     select_residues)

__all__ = ["Lacuna", "LacunaError", "LacunaResult", "BACKENDS", "DETECTORS"]

BACKENDS = ("nma", "openmm")
# 'alpha' only proposes where there is a concavity, which an apo cryptic site
# often has none of; 'surface-fusion' pools a learned surface model with it.
DETECTORS = ("alpha", "surface", "surface-fusion")

DEFAULT_CONFORMERS = 20
DEFAULT_TOP = 10


class LacunaError(RuntimeError):
    """lacuna is not reachable, or it failed."""


class LacunaResult(FpocketResult):
    """Pockets plus how the ensemble that produced them was made."""

    def __init__(self, pockets, command, elapsed, receptor_atoms, cutoff, *,
                 backend: str, detector: str, conformers: int,
                 n_cryptic: int = 0, ensemble: str = ""):
        super().__init__(pockets, command, elapsed, receptor_atoms, cutoff)
        self.backend = backend
        self.detector = detector
        self.conformers = conformers
        self.n_cryptic = n_cryptic
        self.ensemble = ensemble

    def meta(self) -> dict:
        m = super().meta()
        m.update({
            "tool": "lacuna",
            "backend": self.backend,
            "detector": self.detector,
            "conformers": self.conformers,
            "nCryptic": self.n_cryptic,
            # A multi-model PDB: model 1 is the input, the rest are generated.
            "ensemble": os.path.basename(self.ensemble) if self.ensemble else "",
            "ensemblePath": self.ensemble,
        })
        return m


class Lacuna:
    """Runs the lacuna CLI and returns :class:`Pocket` objects.

    lacuna needs numpy, scipy and biopython, and its MD backend needs OpenMM,
    so it lives in its own environment and is run as a command rather than
    imported. ``LACUNA_BIN`` overrides the search.
    """

    def __init__(self, binary: str | None = None):
        cands = [
            binary,
            os.environ.get("LACUNA_BIN"),
            # The MD-capable environment first: it carries OpenMM as well, so
            # one binary serves both backends.
            os.path.expanduser("~/micromamba/envs/lacuna-gpu/bin/lacuna"),
            os.path.expanduser("~/.pyenv/versions/lacuna/bin/lacuna"),
            shutil.which("lacuna"),
        ]
        self.binary = next((p for p in cands if p and os.access(p, os.X_OK)), None)
        self._version: str | None = None
        self._backends: tuple[str, ...] | None = None

    @property
    def python(self) -> str:
        """The interpreter beside the CLI, used to ask what it can import."""
        return os.path.join(os.path.dirname(self.binary or ""), "python")

    @property
    def version(self) -> str:
        """lacuna's version, or "" if the CLI does not run. Asked once."""
        if self._version is None:
            self._version = ""
            if self.binary:
                try:
                    proc = subprocess.run([self.binary, "--version"],
                                          capture_output=True, text=True,
                                          timeout=120)
                    if proc.returncode == 0:
                        # "lacuna, version 1.2.0"
                        self._version = proc.stdout.strip().split()[-1]
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return self._version

    @property
    def available(self) -> bool:
        return bool(self.version)

    @property
    def backends(self) -> tuple[str, ...]:
        """Which backends this installation can actually run.

        The MD backend raises at generate time if openmm or pdbfixer is
        missing, which would surface as a failed run minutes in. Asked once,
        up front, so the viewer can offer only what works.
        """
        if self._backends is None:
            self._backends = ("nma",)
            if self.available and os.access(self.python, os.X_OK):
                try:
                    proc = subprocess.run(
                        [self.python, "-c", "import openmm, pdbfixer"],
                        capture_output=True, text=True, timeout=300)
                    if proc.returncode == 0:
                        self._backends = ("nma", "openmm")
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return self._backends

    def platform(self) -> str:
        """The OpenMM platform the MD backend will pick, for the status line.

        OpenMM's CUDA platform ships only in the conda-forge build; a wheel
        install silently runs on CPU, which is worth saying out loud before
        someone waits on a long run.
        """
        if "openmm" not in self.backends:
            return ""
        try:
            proc = subprocess.run(
                [self.python, "-c",
                 "import openmm as mm;"
                 "from lacuna.ensemble.openmm_backend import _select_platform;"
                 "print(_select_platform(mm).getName())"],
                capture_output=True, text=True, timeout=300)
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "unknown"

    def run(self, structure: Structure, workdir: str,
            cutoff: float = DEFAULT_CUTOFF,
            ligand_resnames: frozenset[str] = frozenset(),
            backend: str = "nma", detector: str = "alpha",
            conformers: int = DEFAULT_CONFORMERS, top: int = DEFAULT_TOP,
            no_sequence: bool = True,
            timeout: float = 3600.0) -> LacunaResult:
        """Detect pockets over an ensemble and resolve each to whole residues.

        ``structure`` is the full entry as displayed. The receptor is derived
        from it here -- same solvent drop and HETATM promotion as the fpocket
        path -- so callers cannot forget it.
        """
        if not self.available:
            raise LacunaError(
                "lacuna not found. Install it into its own environment "
                "(pip install lacuna-pockets) or set LACUNA_BIN.")
        if backend not in BACKENDS:
            raise LacunaError("unknown backend %r (want %s)"
                              % (backend, "/".join(BACKENDS)))
        if backend not in self.backends:
            raise LacunaError(
                "the %s backend needs openmm and pdbfixer in lacuna's "
                "environment (%s)." % (backend, self.python))
        if detector not in DETECTORS:
            raise LacunaError("unknown detector %r (want %s)"
                              % (detector, "/".join(DETECTORS)))

        receptor = structure.receptor(drop_extra=ligand_resnames)
        if len(receptor) == 0:
            raise LacunaError("nothing left to search after removing solvent")

        os.makedirs(workdir, exist_ok=True)
        pdb_path = os.path.join(workdir, "lacuna_receptor.pdb")
        with open(pdb_path, "w") as fh:
            fh.write(receptor.to_pdb())
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
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise LacunaError("lacuna timed out after %gs" % timeout) from exc
        elapsed = time.monotonic() - started

        report_path = os.path.join(out_dir, "pocket_report.json")
        if proc.returncode != 0 or not os.path.exists(report_path):
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            raise LacunaError("lacuna failed (exit %s): %s"
                              % (proc.returncode, " ".join(tail)[-500:]))
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
        return LacunaResult(
            pockets, cmd, elapsed, len(receptor), cutoff,
            backend=backend, detector=detector,
            conformers=int(report.get("n_conformers") or conformers),
            n_cryptic=int(report.get("n_cryptic_pockets") or 0),
            ensemble=ensemble if os.path.exists(ensemble) else "")


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


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("structure")
    ap.add_argument("--backend", default="nma", choices=BACKENDS)
    ap.add_argument("--detector", default="alpha", choices=DETECTORS)
    ap.add_argument("--conformers", type=int, default=DEFAULT_CONFORMERS)
    ap.add_argument("--cutoff", type=float, default=DEFAULT_CUTOFF)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    args = ap.parse_args(argv[1:])

    tool = Lacuna()
    if not tool.available:
        print("lacuna not found; set LACUNA_BIN")
        return 2
    print("lacuna %s (%s)  backends: %s"
          % (tool.version, tool.binary, "/".join(tool.backends)))
    with open(args.structure) as fh:
        structure = Structure.from_text(fh.read())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = tool.run(structure, tmp, cutoff=args.cutoff,
                          backend=args.backend, detector=args.detector,
                          conformers=args.conformers, top=args.top)
    print("%d pockets (%d cryptic) in %.2fs over %d conformers"
          % (len(result), result.n_cryptic, result.elapsed, result.conformers))
    print("%4s %6s %6s %8s %7s %6s %s"
          % ("rank", "drug", "volume", "persist", "crypt", "resid", "chains"))
    for p in result:
        print("%4d %6.2f %6.1f %7.0f%% %7.2f %6d %s"
              % (p.rank, p.druggability, p.volume, p.persistence * 100,
                 p.crypticity, len(p.residues),
                 ",".join(g["chain"] for g in p.selection())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
