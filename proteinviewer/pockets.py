"""Pocket results as the files a project keeps.

Finding pockets is pocketbench's (see pocketbench.py); these only write what a
run returned, in its JSON form, as text other programs read: the site points
as a PDB of pseudo-atoms, and the residues as a list.
"""

from __future__ import annotations

__all__ = ["spheres_pdb", "residue_list"]


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
