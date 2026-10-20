"""Pockets as a detector finds them, and the residues each one is.

Selection method
----------------
A detector's site points (fpocket's alpha-sphere centres, lacuna's pocket
pseudoatoms) stand in for a ligand, and the pocket is every whole standard
amino-acid residue with an atom within ``cutoff`` of any point. This is the
criterion NEAT-POCKET was trained on (``cut_pocket``), driven by site points
instead of a bound ligand. Every detector goes through
:func:`select_residues`, so "pocket" means the same thing whichever one ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .structure import STANDARD_AA, Key, Structure

__all__ = ["DEFAULT_CUTOFF", "Pocket", "DetectionResult", "select_residues"]

DEFAULT_CUTOFF = 6.0

Point = tuple[float, float, float]


def _num(v: float, digits: int = 3):
    """A JSON-safe number. ``json.dumps`` writes NaN as a bare ``NaN``, which
    ``JSON.parse`` rejects, so a field the detector did not report goes out as
    null and a reader can show it as absent rather than as zero.
    """
    return None if v != v else round(v, digits)


@dataclass
class Pocket:
    rank: int
    score: float
    druggability: float
    volume: float
    centers: list[Point]
    radii: list[float]
    residues: list[Key] = field(default_factory=list)
    n_atoms: int = 0
    # Ensemble detectors only -- see detectors/lacuna.py. A single-structure
    # run leaves these alone, and they go out as null.
    persistence: float = float("nan")
    crypticity: float = float("nan")
    cryptic: bool = False

    @property
    def n_spheres(self) -> int:
        return len(self.centers)

    @property
    def centroid(self) -> Point:
        """Stand-in for the ligand centroid. NEAT-POCKET centres its input
        frame on the ligand's centre of mass, so this is the value that plays
        that role when there is no ligand.
        """
        n = len(self.centers) or 1
        return tuple(sum(c[i] for c in self.centers) / n for i in range(3))

    def selection(self) -> list[dict]:
        """Residue numbers grouped by chain, as a viewer selects them.

        Grouped rather than flattened on purpose: a pocket can span chains, and
        a bare list of residue numbers would light up that number on every one.
        """
        by_chain: dict[str, set[int]] = {}
        for chain, resi, _ in self.residues:
            by_chain.setdefault(chain, set()).add(resi)
        return [{"chain": c, "resi": sorted(v)} for c, v in sorted(by_chain.items())]

    def as_dict(self, max_spheres: int = 400) -> dict:
        """JSON form: what ProteinViewer shows and keeps. ``spheres`` is
        included so a viewer can draw what the detector actually found, rather
        than only the residues derived from it.
        """
        return {
            "rank": self.rank,
            "score": _num(self.score),
            "drug": _num(self.druggability),
            "volume": _num(self.volume, 1),
            "persistence": _num(self.persistence),
            "crypticity": _num(self.crypticity),
            "cryptic": self.cryptic,
            "nSpheres": self.n_spheres,
            "nResidues": len(self.residues),
            "nAtoms": self.n_atoms,
            "centroid": [round(v, 3) for v in self.centroid],
            "sel": self.selection(),
            "spheres": [
                [round(c[0], 2), round(c[1], 2), round(c[2], 2), round(r, 2)]
                for c, r in zip(self.centers[:max_spheres], self.radii[:max_spheres])
            ],
        }


@dataclass
class DetectionResult:
    """Pockets plus a record of how they were produced.

    Behaves like the list of pockets, so callers that only want the pockets can
    iterate, index or slice it directly. ``extra`` carries what only one
    detector reports (lacuna's backend and ensemble, say), already in its JSON
    form, so a new detector adds fields without a new result class.
    """

    tool: str
    pockets: list[Pocket]
    command: list[str]
    elapsed: float
    receptor_atoms: int
    cutoff: float
    extra: dict = field(default_factory=dict)

    def __iter__(self):
        return iter(self.pockets)

    def __len__(self) -> int:
        return len(self.pockets)

    def __getitem__(self, i):
        return self.pockets[i]

    def meta(self) -> dict:
        out = {
            "tool": self.tool,
            "command": " ".join(self.command),
            "elapsed": round(self.elapsed, 2),
            "receptorAtoms": self.receptor_atoms,
            "cutoff": self.cutoff,
            "nPockets": len(self.pockets),
        }
        out.update(self.extra)
        return out


def select_residues(
    structure: Structure,
    centers: list[Point],
    cutoff: float = DEFAULT_CUTOFF,
    standard_only: bool = True,
) -> tuple[list[Key], int]:
    """Whole residues with any atom within ``cutoff`` of any site point.

    Returns (residue keys, atom count). Uses a cell grid so the cost stays
    linear -- a plain all-pairs loop is ~10^6 distances on a mid-sized entry,
    which is slow in pure Python.
    """
    if not centers:
        return [], 0
    cell = max(cutoff, 1e-6)
    grid: dict[tuple[int, int, int], list[Point]] = {}
    for c in centers:
        grid.setdefault((int(c[0] // cell), int(c[1] // cell), int(c[2] // cell)), []).append(c)

    cut2 = cutoff * cutoff
    hit: set[Key] = set()
    for a in structure.atoms:
        if a.key not in hit and _near(a.x, a.y, a.z, grid, cell, cut2):
            hit.add(a.key)
    if standard_only:
        hit &= {a.key for a in structure.atoms if a.resn in STANDARD_AA}
    return sorted(hit), sum(1 for a in structure.atoms if a.key in hit)


def _near(
    x: float,
    y: float,
    z: float,
    grid: dict[tuple[int, int, int], list[Point]],
    cell: float,
    cut2: float,
) -> bool:
    """Whether any point in the 27 cells around (x, y, z) is within reach."""
    gx, gy, gz = int(x // cell), int(y // cell), int(z // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c in grid.get((gx + dx, gy + dy, gz + dz), ()):
                    if (x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 <= cut2:
                        return True
    return False
