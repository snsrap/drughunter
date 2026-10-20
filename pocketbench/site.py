"""The ground truth a pocket is scored against: where a ligand sits in the
holo structure, carried into the apo structure's frame and numbering.

The holo structure is superposed onto the apo one (superpose.py), so the
ligand's atoms land where the pocket should open in the apo frame. Its residue
shell -- the protein residues within a cutoff of it in the holo structure --
is carried over through the alignment's residue mapping rather than by
matching residue numbers, so the two entries need not share numbering, chain
ids or even the exact sequence. A shell residue the apo structure lacks (a
loop it leaves disordered, say) stays in the shell as missing: no pocket can
cover it, and counting it is what keeps coverage honest.
"""

from __future__ import annotations

from dataclasses import dataclass

from .structure import STANDARD_AA, Atom, Key, Structure
from .superpose import Point, Superposition, d2, superpose

__all__ = ["Site", "shell", "sites"]


@dataclass
class Site:
    ligand: str  # residue name, e.g. "CBT"
    copy: Key  # which copy, in the holo structure's numbering
    atoms: list[Point]  # its heavy atoms, in the apo frame
    shell: frozenset[Key]  # the residues lining it, in apo numbering
    missing: frozenset[Key]  # shell residues the apo structure lacks (holo numbering)
    holo_shell: frozenset[Key]

    @property
    def centroid(self) -> Point:
        n = len(self.atoms)
        return tuple(sum(p[k] for p in self.atoms) / n for k in range(3))

    @property
    def size(self) -> int:
        """Residues a pocket would have to cover: those there and those not."""
        return len(self.shell) + len(self.missing)

    def as_dict(self) -> dict:
        return {
            "ligand": self.ligand,
            "copy": list(self.copy),
            "atoms": len(self.atoms),
            "centroid": [round(v, 3) for v in self.centroid],
            "shell": [list(k) for k in sorted(self.shell)],
            "missing": [list(k) for k in sorted(self.missing)],
        }


def shell(structure: Structure, ligand: list[Atom], cutoff: float) -> set[Key]:
    """Standard amino-acid residues with a heavy atom within ``cutoff`` of any
    ligand atom.
    """
    c2, out = cutoff * cutoff, set()
    pts = [a.xyz for a in ligand]
    for a in structure.atoms:
        if a.hetero or a.resn not in STANDARD_AA or a.element == "H" or a.key in out:
            continue
        if any(d2(a.xyz, p) <= c2 for p in pts):
            out.add(a.key)
    return out


def sites(
    apo: Structure,
    holo: Structure,
    ligand: str,
    *,
    cutoff: float = 5.0,
    apo_on: set[Key] | None = None,
) -> tuple[Superposition, list[Site]]:
    """Every copy of ``ligand`` in ``holo``, as a site in ``apo``'s frame and
    numbering, with the superposition that put it there. ``apo_on`` restricts
    the fit to those apo residues.
    """
    copies = holo.residue(ligand)
    if not copies:
        raise ValueError("the holo structure has no residue named %s" % ligand)
    sup = superpose(holo.atoms, apo.atoms, reference_on=apo_on)
    out = []
    for key, atoms in sorted(copies.items()):
        heavy = [a for a in atoms if a.element != "H"] or atoms
        hs = shell(holo, heavy, cutoff)
        out.append(
            Site(
                ligand,
                key,
                [sup.transform.apply(a.xyz) for a in heavy],
                frozenset(sup.mapping[k] for k in hs if k in sup.mapping),
                frozenset(k for k in hs if k not in sup.mapping),
                frozenset(hs),
            )
        )
    return sup, out
