"""Shared test fixtures: data files, and fake tools that stand in for the real
external programs so the adapters can be tested without them."""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def read(name: str) -> str:
    return (DATA / name).read_text()


def pdb_line(serial, name, res, chain, seq, x, y, z, element, het=False, alt=" "):
    """One fixed-column PDB atom record."""
    rec = "HETATM" if het else "ATOM  "
    return ("%-6s%5d %-4s%1s%-3s %1s%4d    %8.3f%8.3f%8.3f  1.00  0.00          %2s"
            % (rec, serial, (" " + name) if len(name) < 4 else name, alt, res,
               chain, seq, x, y, z, element))


def fake_executable(directory: str | os.PathLike, name: str, body: str) -> str:
    """A Python script, made executable, run by this interpreter."""
    path = Path(directory) / name
    path.write_text("#!%s\n%s" % (sys.executable, textwrap.dedent(body)))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


# A stand-in for fpocket: writes N pockets (N from $FAKE_FPOCKET_N) the way
# fpocket lays them out, each a few alpha spheres around a point on the x axis.
FAKE_FPOCKET = r"""
import os, sys
pdb = sys.argv[sys.argv.index("-f") + 1]
n = int(os.environ.get("FAKE_FPOCKET_N", "2"))
stem = os.path.splitext(pdb)[0]
out = stem + "_out"
os.makedirs(os.path.join(out, "pockets"), exist_ok=True)
with open(os.path.join(out, os.path.basename(stem) + "_info.txt"), "w") as fh:
    for i in range(1, n + 1):
        fh.write("Pocket %d :\n\tScore : \t%.3f\n\tDruggability Score : \t0.5\n"
                 "\tVolume : \t%.1f\n\n" % (i, 1.0 / i, 100.0 * i))
for i in range(1, n + 1):
    with open(os.path.join(out, "pockets", "pocket%d_vert.pqr" % i), "w") as fh:
        for k in range(3):
            fh.write("ATOM  %5d    C STP  %4d    %8.3f%8.3f%8.3f  0.00  3.50\n"
                     % (k + 1, i, 10.0 * i + k, 0.0, 0.0))
"""
