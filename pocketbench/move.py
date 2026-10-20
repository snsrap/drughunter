"""Applying a transform to a structure's own text.

Only coordinates (and anisotropic displacement tensors) change; every other
record is kept as it was, so CRYST1, SCALE and the assembly records still
describe the deposited frame -- as in a file PyMOL saves after ``align``.
Every atom moves, whatever its model or alternate location.
"""

from __future__ import annotations

import re

from .structure import ATOM_SITE, cif_loops, cif_set_tokens, cif_tokens, detect_format
from .superpose import Transform

__all__ = ["move_text"]

_ATOM = re.compile(r"^(ATOM|HETATM)")


def move_text(text: str, transform: Transform, fmt: str | None = None) -> str:
    """The text with every atom moved by ``transform``."""
    if detect_format(text, fmt) == "cif":
        return _move_cif(text, transform)
    return _move_pdb(text, transform)


def _rotate_u(R, u):
    """An anisotropic tensor turns with its atom: U' = R U Rᵀ.
    [U11, U22, U33, U12, U13, U23] in, the same out.
    """
    U = ((u[0], u[3], u[4]), (u[3], u[1], u[5]), (u[4], u[5], u[2]))
    r = [R[0:3], R[3:6], R[6:9]]

    def e(i, j):
        return sum(r[i][k] * U[k][l] * r[j][l] for k in range(3) for l in range(3))

    return [e(0, 0), e(1, 1), e(2, 2), e(0, 1), e(0, 2), e(1, 2)]


def _pdb_coord(v: float) -> str:
    s = "%.3f" % v
    if len(s) > 8:
        raise ValueError("a moved coordinate (%s) does not fit a PDB file's columns" % s)
    return s.rjust(8)


def _move_pdb(text: str, T: Transform) -> str:
    out = []
    for line in text.split("\n"):
        if _ATOM.match(line):
            try:
                p = [float(line[c : c + 8]) for c in (30, 38, 46)]
            except ValueError:
                out.append(line)
                continue
            line = line[:30] + "".join(_pdb_coord(v) for v in T.apply(p)) + line[54:]
        elif line.startswith("ANISOU"):
            try:
                u = [int(line[c : c + 7]) for c in (28, 35, 42, 49, 56, 63)]
            except ValueError:
                out.append(line)
                continue
            line = (
                line[:28]
                + "".join(("%d" % round(v)).rjust(7) for v in _rotate_u(T.R, u))
                + line[70:]
            )
        out.append(line)
    return "\n".join(out)


def _like(old: str, v: float, least: int) -> str:
    """The new value to as many decimals as the old one had (at least
    ``least``), so a file keeps its own precision.
    """
    d = len(old.split(".", 1)[1]) if "." in old else 0
    return "%.*f" % (max(least, d), v)


_ANISO = ("[1][1]", "[2][2]", "[3][3]", "[1][2]", "[1][3]", "[2][3]")


def _move_cif(text: str, T: Transform) -> str:
    lines = text.split("\n")
    for L in cif_loops(lines, ATOM_SITE):
        if L.cat == "_atom_site.":
            cols = [L.col(c) for c in ("Cartn_x", "Cartn_y", "Cartn_z")]
            if min(cols) < 0:
                continue
            for j in L.rows:
                tok = cif_tokens(lines[j])
                try:
                    p = [float(tok[c]) for c in cols]
                except (ValueError, IndexError):
                    continue
                q = T.apply(p)
                lines[j] = cif_set_tokens(
                    lines[j], {c: _like(tok[c], q[k], 3) for k, c in enumerate(cols)}
                )
            continue
        # _atom_site_anisotrop: U, or B, which is U scaled and turns the same.
        for pre in ("U", "B"):
            cols = [L.col(pre + c) for c in _ANISO]
            if min(cols) < 0:
                continue
            for j in L.rows:
                tok = cif_tokens(lines[j])
                try:
                    u = [float(tok[c]) for c in cols]
                except (ValueError, IndexError):
                    continue
                r = _rotate_u(T.R, u)
                lines[j] = cif_set_tokens(
                    lines[j], {c: _like(tok[c], r[k], 4) for k, c in enumerate(cols)}
                )
    return "\n".join(lines)
