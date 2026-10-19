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
