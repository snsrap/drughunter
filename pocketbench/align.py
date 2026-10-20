"""Protein sequence alignment: BLOSUM62, affine gaps, end gaps free.

Gotoh's algorithm with free leading and trailing gaps on both sequences, so a
longer construct or a missing tail costs nothing -- what pairing the chains of
two structures of one protein needs. Gap costs are PyMOL's ``align`` defaults.
"""

from __future__ import annotations

__all__ = ["BLOSUM62", "AA", "align", "self_align"]

AA = "ARNDCQEGHILKMFPSTWYVX"
BLOSUM62 = [
    [int(v) for v in row.split()]
    for row in """
 4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0  0
-1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3 -1
-2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3 -1
-2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3 -1
 0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1 -2
-1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2 -1
-1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2 -1
 0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3 -1
-2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3 -1
-1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3 -1
-1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1 -1
-1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2 -1
-1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1 -1
-2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1 -1
-1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2 -2
 1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2  0
 0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0  0
-3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3 -2
-2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1 -1
 0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4 -1
 0 -1 -1 -1 -2 -1 -1 -1 -1 -1 -1 -1 -1 -1 -2  0  0 -2 -1 -1 -1
""".strip().splitlines()
]

OPEN, EXTEND = 10.0, 0.5
_NEG = -1e9


def _codes(s: str) -> list[int]:
    x = len(AA) - 1
    return [AA.find(c) if c in AA else x for c in s]


def align(a: str, b: str) -> tuple[float, list[tuple[int, int]]]:
    """Global alignment with free end gaps. Returns (score, pairs): pairs are
    (i, j), the positions in ``a`` and ``b`` that align, in order.
    """
    n, m = len(a), len(b)
    if not n or not m:
        return 0.0, []
    ca, cb = _codes(a), _codes(b)
    W = m + 1
    # The traceback cell holds where H came from (bits 0-1: 0 match, 1 a gap
    # in a, 2 a gap in b) and whether those gaps extended one (bits 2, 3).
    tb = bytearray((n + 1) * W)
    Hp, Fp = [0.0] * W, [_NEG] * W
    best, bi, bj = float("-inf"), 0, 0
    for i in range(1, n + 1):
        row, base = BLOSUM62[ca[i - 1]], i * W
        Hc, Fc = [0.0] * W, [_NEG] * W
        E = _NEG
        for j in range(1, W):
            bits = 0
            eo, ee = Hc[j - 1] - OPEN, E - EXTEND
            if ee > eo:
                E, bits = ee, 4
            else:
                E = eo
            fo, fe = Hp[j] - OPEN, Fp[j] - EXTEND
            if fe > fo:
                F, bits = fe, bits | 8
            else:
                F = fo
            Fc[j] = F
            h, src = Hp[j - 1] + row[cb[j - 1]], 0
            if h < E:
                h, src = E, 1
            if h < F:
                h, src = F, 2
            Hc[j] = h
            tb[base + j] = bits | src
        # A trailing stretch of b left over is free: the end can be any row ...
        if Hc[m] > best:
            best, bi, bj = Hc[m], i, m
        Hp, Fp = Hc, Fc
    # ... and so is one of a: or any column of the last row.
    for j in range(1, W):
        if Hp[j] > best:
            best, bi, bj = Hp[j], n, j

    pairs: list[tuple[int, int]] = []
    i, j, st = bi, bj, 0
    while i > 0 and j > 0:
        c = tb[i * W + j]
        if st == 0:
            src = c & 3
            if src == 0:
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            else:
                st = src
        elif st == 1:
            st = 1 if c & 4 else 0
            j -= 1
        else:
            st = 2 if c & 8 else 0
            i -= 1
    pairs.reverse()
    return best, pairs


def self_align(s: str) -> tuple[float, list[tuple[int, int]]]:
    """An identical sequence aligns with itself residue for residue and need
    not be aligned at all: the usual case of apo and holo forms of one
    protein, or a structure and its own prepared copy.
    """
    c = _codes(s)
    return float(sum(BLOSUM62[k][k] for k in c)), [(i, i) for i in range(len(s))]
