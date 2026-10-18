"""fpocket: pockets in the structure as it stands.

Only ``pocketN_vert.pqr`` is read back from fpocket, and that file holds
nothing but coordinates -- residue identity is resolved against the structure
parsed here. fpocket never has to agree with us about chain IDs or numbering.

Two preparation steps are not optional, and :meth:`PocketDetector.write_receptor`
does both: waters and crystallisation additives are dropped, and every
remaining hetero atom is written as ``ATOM``. fpocket silently ignores
``HETATM`` records, so a cofactor left as hetero is invisible to it and its
volume is detected as empty cavity, merging the cofactor site into the pocket.

CLI
---
    python -m proteinviewer.detectors.fpocket structure.cif [--cutoff 6.0] [--top 5] [--ligand IZP]
"""

from __future__ import annotations

import os
import re
import shutil
import time

from ..errors import ToolFailed, ToolUnavailable
from ..pockets import DEFAULT_CUTOFF, DetectionResult, Pocket, select_residues
from ..process import first_executable, run
from ..structure import Structure
from . import PocketDetector

__all__ = ["FpocketDetector"]


class FpocketDetector(PocketDetector):
    """Runs the fpocket binary and resolves each pocket to whole residues."""

    name = "fpocket"

    def __init__(self, binary: str | None = None, timeout: float = 300.0):
        self.binary = first_executable(binary, shutil.which("fpocket"))
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self.binary is not None

    def describe(self) -> dict:
        return {"available": self.available, "binary": self.binary or ""}

    def status_line(self) -> str:
        return "fpocket: %s" % (self.binary if self.available else
                                "NOT FOUND - pocket detection disabled "
                                "(put fpocket on PATH or set FPOCKET_BIN)")

    def run(self, structure: Structure, workdir: str, *,
            cutoff: float = DEFAULT_CUTOFF,
            ligand_resnames: frozenset[str] = frozenset(),
            **options) -> DetectionResult:
        if not self.available:
            raise ToolUnavailable(
                "fpocket binary not found. Install it and put it on PATH, or "
                "set FPOCKET_BIN to its location.", self.name)
        receptor, pdb_path = self.write_receptor(structure, workdir,
                                                 "receptor.pdb", ligand_resnames)
        out_dir = os.path.join(workdir, "receptor_out")
        # fpocket writes into an existing output directory without clearing
        # it: rerun on the same structure (another cutoff, a ligand removed)
        # and an earlier run's pocketN files outlive it and are read back as
        # this run's pockets -- even when this run found none.
        shutil.rmtree(out_dir, ignore_errors=True)

        cmd = [self.binary, "-f", pdb_path]
        started = time.monotonic()
        proc = run(cmd, tool=self.name, timeout=self.timeout)
        elapsed = time.monotonic() - started
        if not os.path.isdir(out_dir):
            raise ToolFailed(
                "fpocket produced no output (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[:400]),
                self.name)

        info = _parse_info(out_dir)
        pockets: list[Pocket] = []
        pocket_dir = os.path.join(out_dir, "pockets")
        for fn in (os.listdir(pocket_dir) if os.path.isdir(pocket_dir) else ()):
            m = re.match(r"pocket(\d+)_vert\.pqr$", fn)
            if not m:
                continue
            rank = int(m.group(1))
            centers, radii = _parse_vert(os.path.join(pocket_dir, fn))
            if not centers:
                continue
            meta = info.get(rank, {})
            p = Pocket(rank=rank,
                       score=meta.get("Score", float("nan")),
                       druggability=meta.get("Druggability Score", float("nan")),
                       volume=meta.get("Volume", float("nan")),
                       centers=centers, radii=radii)
            p.residues, p.n_atoms = select_residues(structure, centers, cutoff)
            pockets.append(p)

        pockets.sort(key=lambda p: p.rank)
        return DetectionResult(self.name, pockets, cmd, elapsed,
                               len(receptor), cutoff)


def _parse_vert(path: str) -> tuple[list[tuple[float, float, float]], list[float]]:
    centers, radii = [], []
    with open(path) as fh:
        for line in fh:
            if not line.startswith(("ATOM", "HETATM")) or len(line) < 54:
                continue
            try:
                centers.append((float(line[30:38]), float(line[38:46]),
                                float(line[46:54])))
            except ValueError:
                continue
            tail = line[54:].split()
            radii.append(float(tail[-1]) if tail else 0.0)
    return centers, radii


def _parse_info(out_dir: str) -> dict[int, dict[str, float]]:
    path = next((os.path.join(out_dir, fn) for fn in os.listdir(out_dir)
                 if fn.endswith("_info.txt")), None)
    if not path:
        return {}
    info: dict[int, dict[str, float]] = {}
    cur = None
    with open(path) as fh:
        for line in fh:
            m = re.match(r"^Pocket (\d+)\s*:", line)
            if m:
                cur = int(m.group(1))
                info[cur] = {}
            elif cur is not None and ":" in line:
                k, _, v = line.partition(":")
                try:
                    info[cur][k.strip()] = float(v.strip())
                except ValueError:
                    pass
    return info


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description="Detect pockets with fpocket.")
    ap.add_argument("path")
    ap.add_argument("--cutoff", type=float, default=DEFAULT_CUTOFF)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--ligand", default="",
                    help="comma-separated residue names to delete first")
    args = ap.parse_args(argv)

    with open(args.path) as fh:
        structure = Structure.from_text(fh.read())
    lig = frozenset(x.strip().upper() for x in args.ligand.split(",") if x.strip())
    detector = FpocketDetector(os.environ.get("FPOCKET_BIN"))

    with tempfile.TemporaryDirectory(prefix="fpocket-") as work:
        pockets = detector.run(structure, work, cutoff=args.cutoff,
                               ligand_resnames=lig)

    print("%d atoms, chains %s -> %d pockets in %.2fs"
          % (len(structure), ",".join(structure.chains()), len(pockets),
             pockets.elapsed))
    print("$ %s" % " ".join(pockets.command))
    print("%4s %8s %7s %9s %8s %6s %7s  %s"
          % ("rank", "score", "drug", "volume", "spheres", "resid", "atoms",
             "centroid"))
    for p in pockets[:args.top]:
        print("%4d %8.3f %7.3f %9.1f %8d %6d %7d  %s"
              % (p.rank, p.score, p.druggability, p.volume, p.n_spheres,
                 len(p.residues), p.n_atoms,
                 " ".join("%.2f" % v for v in p.centroid)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
