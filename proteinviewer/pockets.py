"""Pockets, and the pocket-atom selection the viewer highlights.

Domain layer: standard library only, no I/O, no knowledge of any tool.

Selection method
----------------
A detector's site points (fpocket's alpha-sphere centres, lacuna's pocket
pseudoatoms) are used as a stand-in for a ligand, and the pocket is every
whole standard amino-acid residue with an atom within ``cutoff`` of any point.
This is the criterion NEAT-POCKET was trained on (``cut_pocket``), just driven
by site points instead of a bound ligand. Every detector goes through
:func:`select_residues`, so "pocket" means the same thing whichever one ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .structure import STANDARD_AA, Structure

__all__ = ["DEFAULT_CUTOFF", "Pocket", "DetectionResult", "select_residues",
           "spheres_pdb", "residue_list"]

DEFAULT_CUTOFF = 6.0

Point = tuple[float, float, float]


def _num(v: float, digits: int = 3):
    """A JSON-safe number. ``json.dumps`` writes NaN as a bare ``NaN``, which
    ``JSON.parse`` rejects, so a field the detector did not report is sent as
    null and the panel can show it as absent rather than as zero."""
    return None if v != v else round(v, digits)


@dataclass
class Pocket:
    rank: int
    score: float
    druggability: float
    volume: float
    centers: list[Point]
    radii: list[float]
    residues: list[tuple[str, int]] = field(default_factory=list)
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
        that role when there is no ligand."""
        n = len(self.centers) or 1
        return tuple(sum(c[i] for c in self.centers) / n for i in range(3))

    def selection(self) -> list[dict]:
        """Residues grouped by chain, for a 3Dmol ``{or:[...]}`` selection.

        Grouped rather than flattened on purpose: a pocket can span chains, and
        a bare list of residue numbers would light up that number on every one.
        """
        by_chain: dict[str, list[int]] = {}
        for chain, seq in self.residues:
            by_chain.setdefault(chain, []).append(seq)
        return [{"chain": c, "resi": sorted(v)} for c, v in sorted(by_chain.items())]

    def as_dict(self, max_spheres: int = 400) -> dict:
        """JSON form. ``spheres`` is included so the viewer can draw what
        the detector actually found, rather than only the residues derived
        from it."""
        return {
            "rank": self.rank, "score": _num(self.score),
            "drug": _num(self.druggability), "volume": _num(self.volume, 1),
            "persistence": _num(self.persistence),
            "crypticity": _num(self.crypticity), "cryptic": self.cryptic,
            "nSpheres": self.n_spheres, "nResidues": len(self.residues),
            "nAtoms": self.n_atoms,
            "centroid": [round(v, 3) for v in self.centroid],
            "sel": self.selection(),
            "spheres": [[round(c[0], 2), round(c[1], 2), round(c[2], 2),
                         round(r, 2)]
                        for c, r in zip(self.centers[:max_spheres],
                                        self.radii[:max_spheres])],
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
        out = {"tool": self.tool, "command": " ".join(self.command),
               "elapsed": round(self.elapsed, 2),
               "receptorAtoms": self.receptor_atoms, "cutoff": self.cutoff,
               "nPockets": len(self.pockets)}
        out.update(self.extra)
        return out


def spheres_pdb(pockets: list[dict], tool: str = "") -> str:
    """The detectors' site points as a PDB of pseudo-atoms: one residue per
    pocket, numbered by rank, with each sphere's radius in the B-factor
    column.

    Takes the JSON form (:meth:`Pocket.as_dict`), which is what a run keeps.
    Written as ``STP`` residues -- fpocket's own name for its alpha spheres --
    so the file loads beside the structure in any viewer and shows *where* a
    pocket is, which a list of residues does not.
    """
    out = ["REMARK   1 pocket site points%s" % ((" from " + tool) if tool else ""),
           "REMARK   1 one residue per pocket, numbered by rank; "
           "B-factor is the sphere radius"]
    for p in pockets:
        out.append("REMARK   2 pocket %-4s %3s residues  volume %-8s score %s"
                   % (p.get("rank"), p.get("nResidues"), p.get("volume"), p.get("score")))
    serial = 0
    for p in pockets:
        rank = int(p.get("rank") or 0)
        for sphere in p.get("spheres") or ():
            x, y, z = (float(v) for v in sphere[:3])
            radius = float(sphere[3]) if len(sphere) > 3 else 0.0
            serial += 1
            out.append("HETATM%5d  O   STP P%4d    %8.3f%8.3f%8.3f  1.00%6.2f           O"
                       % (serial % 100000, rank % 10000, x, y, z, radius))
    out.append("END")
    return "\n".join(out) + "\n"


def residue_list(pockets: list[dict], tool: str = "") -> str:
    """The pockets as text: rank, numbers, and the residues chain by chain.

    The plain-text form of what the viewer highlights, for a notebook or a
    grep. Takes the same JSON form as :func:`spheres_pdb`.
    """
    out = ["# pockets%s" % ((" from " + tool) if tool else ""),
           "# residues are whole residues within the cutoff of the site points"]
    for p in pockets:
        out.append("")
        out.append("pocket %-4s score %-8s volume %-10s %s residues, %s spheres"
                   % (p.get("rank"), p.get("score"), p.get("volume"),
                      p.get("nResidues"), p.get("nSpheres")))
        for group in p.get("sel") or ():
            resi = ",".join(str(r) for r in group.get("resi") or ())
            out.append("  chain %-2s %s" % (group.get("chain") or "?", resi))
    return "\n".join(out) + "\n"


def select_residues(structure: Structure, centers: list[Point],
                    cutoff: float = DEFAULT_CUTOFF,
                    standard_only: bool = True) -> tuple[list[tuple[str, int]], int]:
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
        k = (int(c[0] // cell), int(c[1] // cell), int(c[2] // cell))
        grid.setdefault(k, []).append(c)

    cut2 = cutoff * cutoff
    hit: set[tuple[str, int]] = set()
    for a in structure.atoms:
        if a.key not in hit and _near(a.x, a.y, a.z, grid, cell, cut2):
            hit.add(a.key)

    if standard_only:
        hit &= {a.key for a in structure.atoms if a.res_name in STANDARD_AA}
    n_atoms = sum(1 for a in structure.atoms if a.key in hit)
    return sorted(hit), n_atoms


def _near(x: float, y: float, z: float,
          grid: dict[tuple[int, int, int], list[Point]],
          cell: float, cut2: float) -> bool:
    """Whether any point in the 27 cells around (x, y, z) is within reach."""
    gx, gy, gz = int(x // cell), int(y // cell), int(z // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for c in grid.get((gx + dx, gy + dy, gz + dz), ()):
                    if (x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 <= cut2:
                        return True
    return False
