#!/usr/bin/env python3
"""Build the apo input for Lacuna and the ground-truth pocket from the holo entry.

Usage:
    prep_and_truth.py APO.pdb HOLO.pdb LIGCODE OUTDIR [cutoff]

Writes OUTDIR/<apo_stem>_protein.pdb (chain A protein only) and prints, per copy
of LIGCODE in the holo entry, the protein residues within `cutoff` of it.

Residue numbering is author numbering and is shared between the two entries
(both TEM-1, chain A, 26-290), so the residue-level comparison needs no
superposition. Centroids are reported in each entry's own frame.
"""

import sys
from collections import defaultdict

import numpy as np

STANDARD_AA = frozenset(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE "
    "LEU LYS MET PHE PRO SER THR TRP TYR VAL".split()
)


def parse(path):
    """Return (atoms, lines) where atoms is a list of dicts."""
    atoms, lines = [], []
    with open(path) as fh:
        for line in fh:
            rec = line[:6]
            if rec not in ("ATOM  ", "HETATM"):
                continue
            # skip altloc B and beyond
            altloc = line[16]
            if altloc not in (" ", "A"):
                continue
            atoms.append({
                "het": rec == "HETATM",
                "name": line[12:16].strip(),
                "resn": line[17:20].strip(),
                "chain": line[21],
                "resi": int(line[22:26]),
                "xyz": np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]),
                "elem": line[76:78].strip(),
                "line": line,
            })
            lines.append(line)
    return atoms


def main():
    apo_p, holo_p, ligcode, outdir = sys.argv[1:5]
    cutoff = float(sys.argv[5]) if len(sys.argv) > 5 else 5.0

    # --- apo input: chain A, standard amino acids only, hydrogens dropped ---
    apo = parse(apo_p)
    keep = [a for a in apo
            if a["chain"] == "A" and a["resn"] in STANDARD_AA and a["elem"] != "H"]
    stem = apo_p.split("/")[-1].replace(".pdb", "")
    out = f"{outdir}/{stem}_protein.pdb"
    with open(out, "w") as fh:
        for a in keep:
            fh.write(a["line"])
        fh.write("END\n")
    nres = len({(a["chain"], a["resi"]) for a in keep})
    print(f"apo input : {out}")
    print(f"            {len(keep)} heavy atoms, {nres} residues, chain A")
    dropped = sorted({a["resn"] for a in apo if a["resn"] not in STANDARD_AA})
    print(f"            dropped hetero/solvent: {' '.join(dropped) or 'none'}")

    # --- ground truth from the holo entry ---
    holo = parse(holo_p)
    prot = [a for a in holo
            if not a["het"] and a["resn"] in STANDARD_AA and a["elem"] != "H"]
    prot_xyz = np.array([a["xyz"] for a in prot])

    copies = defaultdict(list)
    for a in holo:
        if a["resn"] == ligcode:
            copies[(a["chain"], a["resi"])].append(a)

    print(f"\nholo      : {holo_p}")
    print(f"            {len(copies)} cop{'y' if len(copies)==1 else 'ies'} of {ligcode}")

    for (chain, resi), lig in sorted(copies.items()):
        lig_xyz = np.array([a["xyz"] for a in lig if a["elem"] != "H"])
        centroid = lig_xyz.mean(axis=0)
        d = np.linalg.norm(prot_xyz[:, None, :] - lig_xyz[None, :, :], axis=2).min(axis=1)
        near = sorted({(prot[i]["chain"], prot[i]["resi"]) for i in np.where(d <= cutoff)[0]})
        print(f"\n  {ligcode} {chain}{resi}: {len(lig_xyz)} heavy atoms, "
              f"centroid {centroid[0]:.2f} {centroid[1]:.2f} {centroid[2]:.2f}")
        print(f"    {len(near)} residues within {cutoff} A:")
        print("    " + " ".join(f"{c}{r}" for c, r in near))
        # catalytic Ser70 reference for the allosteric-vs-active-site call
        ser70 = [a["xyz"] for a in prot if a["resi"] == 70 and a["name"] == "OG"]
        if ser70:
            print(f"    distance from catalytic Ser70 OG: "
                  f"{np.linalg.norm(centroid - ser70[0]):.1f} A")


if __name__ == "__main__":
    main()
