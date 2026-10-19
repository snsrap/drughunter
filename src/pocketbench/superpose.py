"""Superposition: one structure moved onto another by Cα atoms paired up
through a sequence alignment -- what PyMOL's ``align`` does.

- Pairing. Each structure's protein chains are read as sequences; every chain
  being moved is aligned to every reference chain (align.py), and chains are
  paired best alignment first, whatever their ids or order. The aligned
  residues give the Cα pairs, and the residue mapping between the two
  structures -- which is how a site defined in one is found in the other when
  their numbering differs.
- Fitting. The rotation with the least RMSD over the pairs (Horn's quaternion
  method: always a rotation, never a mirror image), refined by dropping the
  pairs more than twice the RMSD apart and fitting again, up to five times, so
  a loop that moved or a domain that hinged does not drag the core with it.

The same method as the viewer's Superpose panel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .align import align, self_align
from .structure import Atom, Key

__all__ = ["Transform", "Superposition", "Residue", "Chain", "protein_chains",
           "fit", "superpose", "related"]

# The standard residues, the modified ones a deposited chain carries, and the
# protonation-state names a prepared structure may be written with.
ONE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
       "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
       "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
       "MSE": "M", "SEP": "S", "TPO": "T", "PTR": "Y", "CSO": "C", "HYP": "P", "MLY": "K",
       "KCX": "K", "HID": "H", "HIE": "H", "HIP": "H", "HSD": "H", "HSE": "H", "HSP": "H",
       "CYX": "C", "CYM": "C", "ASH": "D", "GLH": "E", "LYN": "K"}

CYCLES, CUTOFF = 5, 2.0
# Past this many alignment cells (a large complex against another) only chains
# with the same id are aligned, so a run does not stall in pure Python.
MAX_CELLS = 5_000_000

Point = tuple[float, float, float]


@dataclass(frozen=True)
class Transform:
    """x' = R x + t, R row-major."""
    R: tuple[float, ...]
    t: Point

    def apply(self, p) -> Point:
        R, t = self.R, self.t
        return (R[0] * p[0] + R[1] * p[1] + R[2] * p[2] + t[0],
                R[3] * p[0] + R[4] * p[1] + R[5] * p[2] + t[1],
                R[6] * p[0] + R[7] * p[1] + R[8] * p[2] + t[2])

    def as_dict(self) -> dict:
        return {"R": list(self.R), "t": list(self.t)}


@dataclass
class Residue:
    key: Key
    aa: str
    ca: Point
    fit: bool


@dataclass
class Chain:
    chain: str
    residues: list[Residue]

    @property
    def seq(self) -> str:
        return "".join(r.aa for r in self.residues)


@dataclass
class Superposition:
    """The transform taking the mobile structure onto the reference, and what
    it rests on: ``rmsd`` over the ``n`` pairs kept of the ``aligned`` pairs
    fitted on; ``ident``, the fraction of identical residues over the chains'
    whole alignment (``paired`` residues), and whether that shows the two are
    ``related`` at all; ``mapping`` from mobile residues to reference ones."""
    transform: Transform
    rmsd: float
    n: int
    aligned: int
    paired: int
    ident: float
    related: bool
    chains: list[tuple[str, str]]
    mapping: dict[Key, Key] = field(repr=False)

    def as_dict(self) -> dict:
        return {"transform": self.transform.as_dict(), "rmsd": self.rmsd, "n": self.n,
                "aligned": self.aligned, "paired": self.paired, "ident": self.ident,
                "related": self.related, "chains": [list(c) for c in self.chains]}


def d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


# ---------------------------------------------------------------- residues
def protein_chains(atoms: list[Atom], fit_on: set[Key] | None = None) -> list[Chain]:
    """The protein chains of a structure, in file order. A residue counts if it
    has a Cα and is an amino acid -- by name, or by its N-CA-C backbone, which
    keeps a calcium ion (atom CA of residue CA) out. ``fit_on``, if given, is
    the residues the fit is restricted to: the others are still aligned, so
    the pairing sees the whole chain, but are not fitted on; a chain with none
    of them is left out."""
    seen: dict[Key, dict] = {}
    chains: dict[str, list[dict]] = {}
    for a in atoms:
        r = seen.get(a.key)
        if r is None:
            r = seen[a.key] = {"key": a.key, "resn": a.resn, "names": set(), "ca": None}
            chains.setdefault(a.chain, []).append(r)
        r["names"].add(a.name)
        if a.name == "CA" and r["ca"] is None:
            r["ca"] = a.xyz
    out = []
    for chain, rs in chains.items():
        res = [Residue(r["key"], ONE.get(r["resn"], "X"), r["ca"], fit_on is None or r["key"] in fit_on)
               for r in rs
               if r["ca"] is not None and (r["resn"] in ONE or {"N", "C"} <= r["names"])]
        if res and any(r.fit for r in res):
            out.append(Chain(chain, res))
    return out


def _pair_chains(M: list[Chain], T: list[Chain]):
    """Chains being moved paired with reference chains: every pair aligned,
    then taken best first, each chain used once. A later pair must score per
    residue at least half what the first did, so a leftover chain is not
    forced onto an unrelated one. Returns the residue pairs and chain pairs."""
    cells = sum(len(m.residues) * len(t.residues) for m in M for t in T if m.seq != t.seq)
    cand = []
    for i, m in enumerate(M):
        for j, t in enumerate(T):
            same = m.chain == t.chain
            if m.seq != t.seq and cells > MAX_CELLS and not same:
                continue
            score, pairs = self_align(m.seq) if m.seq == t.seq else align(m.seq, t.seq)
            if pairs:
                cand.append((score, same, i, j, score / len(pairs), pairs))
    cand.sort(key=lambda c: (-c[0], not c[1], c[2], c[3]))
    used_m, used_t, out, chains = set(), set(), [], []
    first = None
    for score, _, i, j, per, pairs in cand:
        if i in used_m or j in used_t or score <= 0:
            continue
        if first is not None and per < first / 2:
            continue
        first = per if first is None else first
        used_m.add(i)
        used_t.add(j)
        chains.append((M[i].chain, T[j].chain))
        out.extend((M[i].residues[a], T[j].residues[b]) for a, b in pairs)
    return out, chains


# ---------------------------------------------------------------- fitting
def _jacobi(A: list[list[float]]):
    """Eigen-decomposition of a small symmetric matrix by cyclic Jacobi
    rotations: (values, vectors as columns)."""
    n = len(A)
    a = [row[:] for row in A]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(64):
        off = sum(a[p][q] ** 2 for p in range(n) for q in range(p + 1, n))
        if off < 1e-30:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if a[p][q] == 0:
                    continue
                th = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = (1 if th >= 0 else -1) / (abs(th) + math.sqrt(th * th + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    kp, kq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * kp - s * kq, s * kp + c * kq
                for k in range(n):
                    pk, qk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * pk - s * qk, s * pk + c * qk
                for k in range(n):
                    kp, kq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * kp - s * kq, s * kp + c * kq
    return [a[i][i] for i in range(n)], v


def fit(P: list[Point], Q: list[Point]) -> tuple[Transform, float]:
    """The rigid transform taking points P onto points Q with the least RMSD,
    and that RMSD. Horn's closed form: the best rotation is the quaternion of
    the largest eigenvalue of a 4x4 matrix built from the covariance of P and
    Q, so it is always a proper rotation."""
    n = len(P)
    cp = [sum(p[k] for p in P) / n for k in range(3)]
    cq = [sum(q[k] for q in Q) / n for k in range(3)]
    S = [[0.0] * 3 for _ in range(3)]
    for p, q in zip(P, Q):
        pc = (p[0] - cp[0], p[1] - cp[1], p[2] - cp[2])
        qc = (q[0] - cq[0], q[1] - cq[1], q[2] - cq[2])
        for i in range(3):
            for j in range(3):
                S[i][j] += pc[i] * qc[j]
    (xx, xy, xz), (yx, yy, yz), (zx, zy, zz) = S
    values, vectors = _jacobi([
        [xx + yy + zz, yz - zy, zx - xz, xy - yx],
        [yz - zy, xx - yy - zz, xy + yx, zx + xz],
        [zx - xz, xy + yx, -xx + yy - zz, yz + zy],
        [xy - yx, zx + xz, yz + zy, -xx - yy + zz]])
    k = max(range(4), key=lambda i: values[i])
    w, x, y, z = (vectors[r][k] for r in range(4))
    R = (w * w + x * x - y * y - z * z, 2 * (x * y - w * z), 2 * (x * z + w * y),
         2 * (x * y + w * z), w * w - x * x + y * y - z * z, 2 * (y * z - w * x),
         2 * (x * z - w * y), 2 * (y * z + w * x), w * w - x * x - y * y + z * z)
    rc = Transform(R, (0.0, 0.0, 0.0)).apply(cp)
    T = Transform(R, (cq[0] - rc[0], cq[1] - rc[1], cq[2] - rc[2]))
    rmsd = math.sqrt(sum(d2(T.apply(p), q) for p, q in zip(P, Q)) / n)
    return T, rmsd


def related(ident: float, n: int) -> bool:
    """Whether ``ident`` identity over ``n`` aligned residues shows two
    sequences are related at all: Rost's twilight-zone curve (Protein Eng
    12:85, 1999), below which an alignment is no better than chance. About
    47 % over 40 residues, 29 % over 100, 20 % from 500 on (where the curve
    flattens; it is read there for longer alignments)."""
    L = min(n, 500)
    return n > 0 and ident * 100 >= 480 * L ** (-0.32 * (1 + math.exp(-L / 1000)))


def superpose(mobile: list[Atom], reference: list[Atom], *,
              mobile_on: set[Key] | None = None,
              reference_on: set[Key] | None = None) -> Superposition:
    """Superpose ``mobile`` onto ``reference``. ``*_on`` restrict the fit to
    those residues of either side. Raises ``ValueError`` saying why it
    cannot."""
    M, T = protein_chains(mobile, mobile_on), protein_chains(reference, reference_on)
    if not M:
        raise ValueError("the part selected has no protein Cα atoms" if mobile_on
                         else "the structure being moved has no protein Cα atoms")
    if not T:
        raise ValueError("the part of the reference selected has no protein Cα atoms" if reference_on
                         else "the reference has no protein Cα atoms")
    every, chains = _pair_chains(M, T)
    pairs = [(m, t) for m, t in every if m.fit and t.fit]
    if len(pairs) < 3:
        raise ValueError("%s residues pair up with the reference — at least 3 are needed"
                         % ("only %d" % len(pairs) if pairs else "no"))
    ident = sum(1 for m, t in every if m.aa == t.aa != "X") / len(every)

    use = pairs
    X, rmsd = fit([m.ca for m, _ in use], [t.ca for _, t in use])
    for _ in range(CYCLES):
        if rmsd <= 1e-6:
            break
        lim = (CUTOFF * rmsd) ** 2
        keep = [(m, t) for m, t in use if d2(X.apply(m.ca), t.ca) <= lim]
        if len(keep) == len(use) or len(keep) < 3:
            break
        use = keep
        X, rmsd = fit([m.ca for m, _ in use], [t.ca for _, t in use])
    return Superposition(X, rmsd, len(use), len(pairs), len(every), ident,
                         related(ident, len(every)), chains,
                         {m.key: t.key for m, t in every})
