#!/usr/bin/env python3
"""Score a Lacuna pocket_report.json against the holo ligand's residue shell.

Usage:
    score.py REPORT.json APO.pdb HOLO.pdb LIGCODE [cutoff]

Reports, per ground-truth ligand copy, every Lacuna pocket's residue overlap and
centroid distance, and the best rank that qualifies as a recovery.

The two entries are superposed on their common CA atoms (Kabsch) before centroid
distances are compared, so the numbers hold even though the entries are deposited
in different frames. Residue overlap needs no superposition: both are TEM-1
chain A in author numbering.
"""

import json
import re
import sys
from collections import defaultdict

import numpy as np

STANDARD_AA = frozenset(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE "
    "LEU LYS MET PHE PRO SER THR TRP TYR VAL".split()
)
# Recovery criteria, both reported.
CENTROID_HIT_A = 5.0   # pocket centroid within this of the ligand centroid
COVERAGE_HIT = 0.50    # fraction of ligand-shell residues the pocket contains


def parse(path):
    atoms = []
    with open(path) as fh:
        for line in fh:
            if line[:6] not in ("ATOM  ", "HETATM"):
                continue
            if line[16] not in (" ", "A"):
                continue
            if line[76:78].strip() == "H":
                continue
            atoms.append({
                "het": line[:6] == "HETATM",
                "name": line[12:16].strip(),
                "resn": line[17:20].strip(),
                "chain": line[21],
                "resi": int(line[22:26]),
                "xyz": np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]),
            })
    return atoms


def kabsch(P, Q):
    """Rotation+translation taking P onto Q."""
    pc, qc = P.mean(0), Q.mean(0)
    H = (P - pc).T @ (Q - qc)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T
    rmsd = np.sqrt((((R @ (P - pc).T).T + qc - Q) ** 2).sum(1).mean())
    return R, pc, qc, rmsd


def main():
    report_p, apo_p, holo_p, ligcode = sys.argv[1:5]
    cutoff = float(sys.argv[5]) if len(sys.argv) > 5 else 5.0

    apo, holo = parse(apo_p), parse(holo_p)

    # superpose holo -> apo on common CA
    apo_ca = {(a["chain"], a["resi"]): a["xyz"] for a in apo if a["name"] == "CA"}
    holo_ca = {(a["chain"], a["resi"]): a["xyz"] for a in holo
               if a["name"] == "CA" and not a["het"]}
    common = sorted(set(apo_ca) & set(holo_ca))
    P = np.array([holo_ca[k] for k in common])
    Q = np.array([apo_ca[k] for k in common])
    R, pc, qc, rmsd = kabsch(P, Q)
    to_apo = lambda x: (R @ (x - pc)) + qc
    print(f"superposition: {len(common)} CA atoms, backbone RMSD {rmsd:.2f} A "
          f"(apo {apo_p.split('/')[-1]} <- holo {holo_p.split('/')[-1]})")

    # ground truth per ligand copy
    prot = [a for a in holo if not a["het"] and a["resn"] in STANDARD_AA]
    prot_xyz = np.array([a["xyz"] for a in prot])
    copies = defaultdict(list)
    for a in holo:
        if a["resn"] == ligcode:
            copies[(a["chain"], a["resi"])].append(a)

    truth = {}
    for key, lig in sorted(copies.items()):
        lig_xyz = np.array([a["xyz"] for a in lig])
        d = np.linalg.norm(prot_xyz[:, None, :] - lig_xyz[None, :, :], axis=2).min(axis=1)
        resi = {f"{prot[i]['resn']}{prot[i]['resi']}:{prot[i]['chain']}"
                for i in np.where(d <= cutoff)[0]}
        truth[key] = {"res": resi, "centroid": to_apo(lig_xyz.mean(axis=0))}

    # lacuna pockets
    rep = json.load(open(report_p))
    pockets = rep["pockets"]
    print(f"report       : {rep['n_pockets_found']} pockets found, "
          f"{rep['n_cryptic_pockets']} cryptic, {rep['n_conformers']} conformers, "
          f"ranked by {rep['ranked_by']}")

    for key, gt in truth.items():
        print(f"\n{'='*78}\nground truth {ligcode} {key[0]}{key[1]}: "
              f"{len(gt['res'])} residues within {cutoff} A")
        print(f"{'rank':>4} {'ovlp':>5} {'cover':>6} {'jacc':>5} {'dcent':>6} "
              f"{'persist':>8} {'crypt':>6} {'drug':>5}  verdict")
        best = None
        for p in pockets:
            res = set(p["lining_residues"])
            inter = res & gt["res"]
            cover = len(inter) / len(gt["res"])
            jacc = len(inter) / len(res | gt["res"])
            dc = float(np.linalg.norm(np.array(p["centroid"]) - gt["centroid"]))
            hit = (dc <= CENTROID_HIT_A) or (cover >= COVERAGE_HIT)
            if hit and best is None:
                best = p["rank"]
            flag = ""
            if dc <= CENTROID_HIT_A:
                flag += " centroid"
            if cover >= COVERAGE_HIT:
                flag += " coverage"
            print(f"{p['rank']:>4} {len(inter):>5} {cover:>6.0%} {jacc:>5.2f} "
                  f"{dc:>6.1f} {p['persistence']:>8.0%} {p['crypticity']:>6.2f} "
                  f"{p['druggability']:>5.2f} {'HIT' + flag if hit else ''}")
        verdict = str(best) if best else f"NONE in top {len(pockets)}"
        print(f"  -> best qualifying rank: {verdict}")


if __name__ == "__main__":
    main()
