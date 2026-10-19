"""Synthetic structures: a Cα trace with N and C per residue, written as PDB
text, so a test controls exactly what moved and by how much."""

import math
import random

from pocketbench.structure import Structure
from pocketbench.superpose import Transform

THREE = {"A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN", "E": "GLU",
         "G": "GLY", "H": "HIS", "I": "ILE", "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE",
         "P": "PRO", "S": "SER", "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL"}
SEQ = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQ"


def rotation(axis, angle):
    l = math.sqrt(sum(v * v for v in axis))
    x, y, z = (v / l for v in axis)
    c, s = math.cos(angle), math.sin(angle)
    C = 1 - c
    return (c + x * x * C, x * y * C - z * s, x * z * C + y * s,
            y * x * C + z * s, c + y * y * C, y * z * C - x * s,
            z * x * C - y * s, z * y * C + x * s, c + z * z * C)


T0 = Transform(rotation((1, 2, 3), 1.1), (12.0, -7.0, 30.0))


def trace(n, seed):
    """A random walk of 3.8 Å steps."""
    r = random.Random(seed)
    out = [(0.0, 0.0, 0.0)]
    while len(out) < n:
        v = [r.random() - 0.5 for _ in range(3)]
        l = math.sqrt(sum(x * x for x in v))
        p = out[-1]
        out.append(tuple(p[k] + 3.8 * v[k] / l for k in range(3)))
    return out


def atom_line(serial, name, resn, chain, resi, xyz, het=False):
    rec = "HETATM" if het else "ATOM  "
    nm = name if len(name) >= 4 else " " + name.ljust(3)
    return "%s%5d %-4s %-3s %1s%4d    %8.3f%8.3f%8.3f  1.00 10.00          %2s" % (
        rec, serial, nm, resn, chain, resi, xyz[0], xyz[1], xyz[2], name[0])


def chain_lines(chain, seq, cas, first=1, T=None):
    out = []
    for i, (aa, ca) in enumerate(zip(seq, cas)):
        for name, dx in (("N", -1.0), ("CA", 0.0), ("C", 1.0)):
            p = (ca[0] + dx, ca[1], ca[2])
            out.append(atom_line(len(out) + 1, name, THREE[aa], chain, first + i, T.apply(p) if T else p))
    return out


def ligand_lines(resn, chain, resi, points, T=None):
    return [atom_line(9000 + k, "C%d" % (k + 1), resn, chain, resi, T.apply(p) if T else p, het=True)
            for k, p in enumerate(points)]


def pdb(*parts):
    return Structure.from_text("\n".join(sum(parts, [])) + "\nEND\n", "pdb")


# ---------------------------------------------------------------- ported from ProteinViewer
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


def fake_executable(directory, name: str, body: str) -> str:
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
