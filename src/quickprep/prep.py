"""Open-source stand-in for MOE's QuickPrep.

QuickPrep chains four MOE tools. Each has an open-source counterpart here:

  MOE                              here
  -------------------------------  ---------------------------------------------
  Structure Preparation            gemmi (first model, altlocs, hydrogens off)
                                   + PDBFixer (missing atoms, short loops, caps,
                                   modified residues -> parent amino acid)
  delete distant solvent           waters > 4.5 A from every non-water atom go
  Protonate3D                      PDB2PQR + PROPKA: pKa-based ionisation at the
                                   given pH, His tautomers, Asn/Gln/His flips,
                                   hydrogen-bond network optimisation
  tethers + refinement             OpenMM, Amber14/TIP3P, flat-bottom tethers on
                                   heavy atoms, L-BFGS minimisation

Things it does that PROPKA does not: residues bound to a metal are put in the
state that coordination implies -- the coordinating His nitrogen loses its
hydrogen, Cys becomes thiolate, Asp/Glu carboxylate.

Beyond QuickPrep (after the GPTomics bioSkills structure-preparation skill):

* The input is assessed first -- method, resolution, experimental or
  predicted -- and anything that makes the result shakier is a warning.
* Predicted models (AlphaFold, ESMFold, ...): --plddt-trim removes residues
  below a pLDDT cutoff before anything is built, and the trimmed regions are
  capped, never rebuilt. Breaks with nothing on record (no SEQRES) are capped.
* --no-rebuild A:5-8 marks residues the caller removed or left out on purpose:
  a gap they fall in is capped, never rebuilt from SEQRES.
* --state A:57=HIE sets a protonation state by hand, over PROPKA and the metal
  rules -- for residues whose pKa sits near the pH, or sites PROPKA is weak at.
  Groups within 1 unit of the pH and groups shifted 2+ units are reported.
* --drop-additives removes crystallisation additives and buffer components;
  metals and cofactors are kept.
* --pqr writes charges and radii for APBS from the same Amber14 force field
  the refinement used.
* Provenance travels with the file: REMARK 99 lines record the pH, the
  protonation model and everything built, trimmed or capped; deposited atoms
  keep their occupancy and B-factor (pLDDT, for a predicted model); built atoms
  have occupancy 0.00.

What it does not do, and QuickPrep does:

* Ligands and cofactors are not protonated or parametrised. During refinement
  they are rigid, uncharged steric walls that only stop the protein moving into
  them; single-atom ions keep their formal charge. Residues that coordinate a
  metal are held fixed.
* Missing loops are built by PDBFixer, which is cruder than MOE's homology
  modelling. Only gaps up to --max-loop residues are built; longer ones are
  capped with ACE/NME. Missing terminal residues are left off by default
  (--max-tail 0; MOE builds up to 5). PDBFixer's builds are not reproducible run to run
  (it iterates over sets of objects hashed by address), so rebuilt residues --
  which have no experimental coordinates anyway -- can differ between runs.
  They also come out D now and then; those side chains are mirrored to L, and
  bent backbone is straightened before the main refinement.

Needs openmm, pdbfixer, pdb2pqr, propka, gemmi and numpy.

CLI
---
    quickprep 1HSG.pdb -o 1HSG_prep.pdb [--ph 7.0] [--report prep.json]
    quickprep AF-P04637-F1.pdb --plddt-trim 70 --pqr p53.pqr
    quickprep 3PTB.pdb --drop-additives --state A:57=HIE
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import gemmi
import numpy as np
import openmm as mm
from openmm import app, unit
from pdbfixer import PDBFixer

from . import __version__

AMINO = frozenset(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP "
    "TYR VAL HID HIE HIP ASH GLH LYN CYX CYM".split()
)
NUCLEIC = frozenset("A C G U I DA DC DG DT DI".split())
CAPS = frozenset({"ACE", "NME"})
WATER = frozenset({"HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"})
METALS = frozenset(
    "Li Na K Rb Cs Mg Ca Sr Ba Mn Fe Co Ni Cu Zn Cd Hg Pt Pd Ag Au Al Ga Mo W "
    "V Cr Yb Gd Tb Eu Sm La".split()
)
# Formal charge for a lone metal ion. Fe and Cu are ambiguous; +2 is the
# commoner state in protein sites.
ION_CHARGE = {"Li": 1, "Na": 1, "K": 1, "Rb": 1, "Cs": 1, "Ag": 1,
              "Al": 3, "Ga": 3, "Yb": 3, "Gd": 3, "Tb": 3, "Eu": 3, "Sm": 3,
              "La": 3}
ANION_CHARGE = {"F": -1, "Cl": -1, "Br": -1, "I": -1}

# Crystallisation additives and buffer components (--drop-additives). Opt-in:
# a sulfate or phosphate can sit in a real anion site.
ADDITIVES = frozenset(
    "EDO GOL SO4 PO4 ACT DMS MES TRS PEG PG4 PGE 1PE P6G IPA MPD FMT NO3 AZI "
    "CAC BME EPE IMD MLI FLC CIT TLA BTB DIO PE4 CL BR IOD".split()
)
# Header text that marks a predicted model (pLDDT in the B-factor column).
PREDICTED_MARKERS = ("alphafold", "esmfold", "colabfold", "openfold",
                     "rosettafold", "_ma_qa_metric")

COVALENT_CUTOFF = 0.245   # nm; mobile atom this close to a hetero atom is bonded to it
METAL_CUTOFF = 0.28       # nm; coordination distance to a metal
FLIP_MATCH = 0.03         # nm; an atom landing on its partner's old site is a flip

KCAL = 4.184  # kJ per kcal

# A flip swaps these two atoms' positions.
FLIP_PAIRS = {"ASN": ("OD1", "ND2"), "GLN": ("OE1", "NE2"), "HIS": ("ND1", "CD2")}
# PDB2PQR residue names that are OpenMM hydrogen variants.
VARIANTS = {"HIS": ("HID", "HIE", "HIP"), "ASP": ("ASH", "ASP"),
            "GLU": ("GLH", "GLU"), "LYS": ("LYN", "LYS"),
            "CYS": ("CYS", "CYX", "CYM")}
RING_NEIGHBOURS = {"ND1": ("CG", "CE1"), "CD2": ("CG", "NE2"),
                   "CE1": ("ND1", "NE2"), "NE2": ("CD2", "CE1")}


class PrepError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Step 1: clean-up on the deposited structure (gemmi)
# ---------------------------------------------------------------------------
def clean(path: str, water_cutoff: float | None, drop: frozenset[str],
          report: dict, plddt_trim: float | None = None,
          predicted: bool | None = None):
    """Returns the cleaned structure, the deposited occupancy/B-factor of every
    atom that survives (for provenance in the output), and the residues
    trimmed for low pLDDT (so they are not rebuilt)."""
    st = gemmi.read_structure(path)
    st.setup_entities()
    n_models = len(st)
    while len(st) > 1:
        del st[len(st) - 1]
    model = st[0]

    # Assess before adding inference on top: method, resolution, and whether
    # this is a predicted model, whose B-factor column holds pLDDT.
    head = _head(path)
    method = ", ".join(e.method for e in getattr(st.meta, "experiments", [])
                       if e.method) or _expdta(head)
    if predicted is None:
        predicted = any(m in head for m in PREDICTED_MARKERS)
    resolution = st.resolution or None
    warnings = []
    if resolution and resolution > 3.0:
        warnings.append("resolution %.2f A: side-chain placement, flips and waters "
                        "are poorly determined" % resolution)
    plddt = None
    if predicted:
        cas = [r.find_atom("CA", "*") for c in model for r in c]
        b = np.array([a.b_iso for a in cas if a is not None])
        if len(b):
            plddt = {"mean": round(float(b.mean()), 1),
                     "below70": round(float((b < 70).mean()), 3),
                     "below50": round(float((b < 50).mean()), 3)}
        warnings.append("predicted model: pocket side-chain rotamers are the least "
                        "reliable atoms even where pLDDT is high; check the site "
                        "before docking")
        if plddt_trim is None:
            warnings.append("predicted model kept whole: low-pLDDT regions were not "
                            "trimmed (--plddt-trim 70)")

    # Alternate locations: keep the conformer with the highest mean occupancy,
    # per residue. Microheterogeneity (two residue types at one position) is
    # resolved the same way.
    altlocs, microhet = [], []
    for chain in model:
        groups: dict[tuple, list[int]] = {}
        for i, res in enumerate(chain):
            groups.setdefault((res.seqid.num, res.seqid.icode), []).append(i)
        doomed = []
        for idx in groups.values():
            if len(idx) < 2:
                continue
            occ = [np.mean([a.occ for a in chain[i]]) for i in idx]
            keep = idx[int(np.argmax(occ))]
            doomed += [i for i in idx if i != keep]
            microhet.append("%s %s%s -> %s" % (chain.name, chain[keep].seqid.num,
                                               chain[keep].seqid.icode.strip(),
                                               chain[keep].name))
        for i in sorted(doomed, reverse=True):
            del chain[i]

        for res in chain:
            alts = sorted({a.altloc for a in res if a.altloc != "\0"})
            if not alts:
                continue
            occ = {k: np.mean([a.occ for a in res if a.altloc == k]) for k in alts}
            best = max(alts, key=lambda k: occ[k])
            for j in range(len(res) - 1, -1, -1):
                if res[j].altloc not in ("\0", best):
                    del res[j]
            for a in res:
                a.altloc = "\0"
            altlocs.append("%s %s%s %s:%s" % (chain.name, res.seqid.num,
                                              res.seqid.icode.strip(), res.name,
                                              best))

    st.remove_hydrogens()

    # Low-confidence regions of a predicted model come off before anything is
    # built or protonated; fix() makes sure they are not rebuilt.
    trimmed: dict[str, set[int]] = {}
    if plddt_trim is not None:
        for chain in model:
            for i in range(len(chain) - 1, -1, -1):
                res = chain[i]
                info = gemmi.find_tabulated_residue(res.name)
                if info is None or not info.is_amino_acid():
                    continue
                ca = res.find_atom("CA", "*")
                score = ca.b_iso if ca else np.mean([a.b_iso for a in res])
                if score < plddt_trim:
                    trimmed.setdefault(chain.name, set()).add(res.seqid.num)
                    del chain[i]

    dropped = []
    if drop:
        for chain in model:
            for i in range(len(chain) - 1, -1, -1):
                if chain[i].name in drop:
                    dropped.append("%s %s %s" % (chain.name, chain[i].name,
                                                 chain[i].seqid.num))
                    del chain[i]

    # Delete distant solvent: waters with no non-water atom within the cutoff.
    # No unit cell, so crystal mates do not count as neighbours.
    n_water = n_water_del = 0
    if water_cutoff is not None:
        ns = gemmi.NeighborSearch(model, gemmi.UnitCell(), 6).populate()
        for chain in model:
            far = []
            for i, res in enumerate(chain):
                if not res.is_water():
                    continue
                n_water += 1
                near = False
                for a in res:
                    for m in ns.find_atoms(a.pos, "\0", radius=water_cutoff):
                        if not m.to_cra(model).residue.is_water():
                            near = True
                            break
                    if near:
                        break
                if not near:
                    far.append(i)
            for i in reversed(far):
                del chain[i]
            n_water_del += len(far)
    st.remove_empty_chains()
    deposited = {(c.name, str(r.seqid.num), r.seqid.icode.strip(), a.name):
                 (a.occ, a.b_iso) for c in model for r in c for a in r}

    report["input"] = {"path": str(path), "models": n_models,
                       "chains": [c.name for c in model], "method": method or None,
                       "resolution": resolution, "predicted": bool(predicted),
                       "plddt": plddt}
    report["warnings"] = warnings
    report["trimmed"] = {"cutoff": plddt_trim,
                         "ranges": _ranges(trimmed)} if plddt_trim is not None else None
    report["altlocs"] = altlocs
    report["microheterogeneity"] = microhet
    report["dropped"] = dropped
    report["water"] = {"found": n_water, "deleted": n_water_del,
                       "kept": n_water - n_water_del, "cutoff": water_cutoff}
    return st, deposited, trimmed


# ---------------------------------------------------------------------------
# Step 2: missing atoms, short loops, caps (PDBFixer)
# ---------------------------------------------------------------------------
def fix(st: gemmi.Structure, max_loop: int, max_tail: int,
        replace_nonstandard: bool, report: dict,
        trimmed: dict[str, set[int]] | None = None,
        no_rebuild: dict[str, set[int]] | None = None):
    opts = gemmi.PdbWriteOptions()
    # Reference is single-threaded and, for the small systems PDBFixer
    # minimises, faster than CPU.
    fixer = PDBFixer(pdbfile=io.StringIO(st.make_pdb_string(opts)),
                     platform=mm.Platform.getPlatformByName("Reference"))
    original = {_key(a) for a in fixer.topology.atoms()}

    fixer.findMissingResidues()
    chains = list(fixer.topology.chains())
    built, capped, skipped = [], [], []
    for (ci, idx), names in sorted(fixer.missingResidues.items()):
        chain = chains[ci]
        residues = list(chain.residues())
        terminal = idx == 0 or idx == len(residues)
        where = ("N-term" if idx == 0 else "C-term" if terminal else
                 "after %s%s" % (residues[idx - 1].id,
                                 residues[idx - 1].insertionCode.strip()))
        label = "%s %s: %d (%s)" % (chain.id, where, len(names),
                                    "".join(_one(n) for n in names))
        # A gap we trimmed ourselves (low pLDDT) is never rebuilt, and nor is
        # one the caller left out on purpose (--no-rebuild): SEQRES still
        # lists those residues, but nobody wants them back.
        nums = _gap_numbers(residues, idx, len(names))
        cut = bool(nums & (trimmed or {}).get(chain.id, set()))
        left = not cut and bool(nums & (no_rebuild or {}).get(chain.id, set()))
        if cut:
            label += " [pLDDT-trimmed]"
        elif left:
            label += " [left out]"
        if not (cut or left) and len(names) <= (max_tail if terminal else max_loop):
            built.append(label)
        elif terminal:
            del fixer.missingResidues[(ci, idx)]
            skipped.append(label)
        else:
            fixer.missingResidues[(ci, idx)] = ["NME", "ACE"]
            capped.append(label)

    # Breaks with nothing on record (no SEQRES, as in ESMFold output, or a
    # trimmed predicted model): OpenMM would bond straight across them, so cap.
    xyz = _nm(fixer.positions)
    for ci, chain in enumerate(chains):
        residues = list(chain.residues())
        for i in range(1, len(residues)):
            a, b = residues[i - 1], residues[i]
            if (ci, i) in fixer.missingResidues or a.name not in AMINO or b.name not in AMINO:
                continue
            c = next((x for x in a.atoms() if x.name == "C"), None)
            n = next((x for x in b.atoms() if x.name == "N"), None)
            if c is not None and n is not None and np.linalg.norm(
                    xyz[c.index] - xyz[n.index]) > 0.2:
                fixer.missingResidues[(ci, i)] = ["NME", "ACE"]
                capped.append("%s break after %s%s: not in the sequence record"
                              % (chain.id, a.id, a.insertionCode.strip()))

    nonstandard = []
    if replace_nonstandard:
        fixer.findNonstandardResidues()
        nonstandard = ["%s %s%s %s->%s" % (r.chain.id, r.id, r.insertionCode.strip(),
                                           r.name, p)
                       for r, p in fixer.nonstandardResidues]
        fixer.replaceNonstandardResidues()

    fixer.findMissingAtoms()
    missing_atoms = ["%s %s%s %s: %s" % (r.chain.id, r.id, r.insertionCode.strip(),
                                         r.name, " ".join(a.name for a in atoms))
                     for r, atoms in fixer.missingAtoms.items()]
    terminals = ["%s %s%s %s: %s" % (r.chain.id, r.id, r.insertionCode.strip(),
                                     r.name, " ".join(names))
                 for r, names in fixer.missingTerminals.items()]
    fixer.addMissingAtoms(seed=0)
    pos = _nm(fixer.positions)
    _number_caps(fixer.topology)

    # PDBFixer sometimes builds a side chain (or a whole residue) as its D
    # enantiomer, and no amount of minimisation undoes that. Mirror rebuilt
    # side chains through the N-CA-C plane: same backbone, L configuration.
    # A rebuilt CB means everything beyond it was rebuilt too.
    mirrored = []
    for res in fixer.topology.residues():
        at = {a.name: a for a in res.atoms()}
        if (res.name not in AMINO or not all(n in at for n in ("N", "CA", "C", "CB"))
                or _key(at["CB"]) in original):
            continue
        n, ca, c = (pos[at[k].index] for k in ("N", "CA", "C"))
        if _chirality(n, ca, c, pos[at["CB"].index]) > 0:
            continue
        normal = np.cross(n - ca, c - ca)
        normal /= np.linalg.norm(normal)
        for a in res.atoms():
            if a.name not in ("N", "CA", "C", "O", "OXT"):
                pos[a.index] -= 2.0 * np.dot(pos[a.index] - ca, normal) * normal
        mirrored.append(_label(res))

    report["loops"] = {"built": built, "capped": capped, "notBuilt": skipped,
                       "maxLoop": max_loop, "maxTail": max_tail,
                       "mirroredToL": mirrored}
    report["nonstandard"] = nonstandard
    report["missingAtoms"] = missing_atoms
    report["terminalAtoms"] = terminals
    return fixer.topology, pos, original


# ---------------------------------------------------------------------------
# Step 3: protonation (PDB2PQR + PROPKA), then metal sites
# ---------------------------------------------------------------------------
def protonate(top, pos, ph: float, work: Path, report: dict,
              overrides: dict | None = None):
    pos = _nm(pos)
    before = {_key(a): pos[a.index] for a in top.atoms()}

    # PDB2PQR has no hydrogen rules for ACE/NME and gives up on them. It is
    # handed the chain without caps; across the gap it keeps both flanking
    # residues as chain interior (no OXT, backbone N-H), which is the capped
    # state. The caps are put back afterwards.
    src = work / "fixed.pdb"
    bare = app.Modeller(top, pos * unit.nanometer)
    bare.delete([r for r in top.residues() if r.name in CAPS])
    with open(src, "w") as fh:
        app.PDBFile.writeFile(bare.topology, bare.positions, fh, keepIds=True)

    out_pdb, out_pqr = work / "protonated.pdb", work / "protonated.pqr"
    cmd = [_pdb2pqr(), "--ff", "AMBER", "--ffout", "AMBER",
           "--titration-state-method", "propka", "--with-ph", "%g" % ph,
           "--keep-chain", "--pdb-output", str(out_pdb), str(src), str(out_pqr)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if proc.returncode != 0 or not out_pdb.exists():
        raise PrepError("pdb2pqr failed (exit %s): %s" % (
            proc.returncode, (proc.stderr or proc.stdout).strip()[-600:]))

    # PDB2PQR names water atoms OW/HW/HW; the duplicate name makes OpenMM drop
    # a hydrogen, so rename them O/H1/H2. Residue names are read here because
    # OpenMM folds HID/HIE/HIP, ASH, ... back to the standard names.
    names, lines, seen = {}, [], {}
    for line in out_pdb.read_text().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            rk = (line[21], line[22:26].strip(), line[26].strip())
            names[rk] = line[17:20].strip()
            if names[rk] in WATER:
                atom = line[12:16].strip()
                if atom == "OW":
                    line = line[:12] + " O  " + line[16:]
                elif atom == "HW":
                    seen[rk] = seen.get(rk, 0) + 1
                    line = line[:12] + " H%d " % seen[rk] + line[16:]
        lines.append(line)
    out_pdb.write_text("\n".join(lines) + "\n")
    pdb = app.PDBFile(str(out_pdb))
    top, pos = _merge(top, pos, pdb.topology,
                      pdb.getPositions(asNumpy=True).value_in_unit(unit.nanometer))
    if any(r.name in CAPS for r in top.residues()):
        top, pos = _protonate_caps(top, pos, names)

    flips = []
    for res in top.residues():
        pair = FLIP_PAIRS.get(res.name)
        if not pair:
            continue
        atoms = {a.name: a for a in res.atoms()}
        old = before.get(_rkey(res) + (pair[1],))
        if pair[0] in atoms and old is not None and np.linalg.norm(
                pos[atoms[pair[0]].index] - old) < FLIP_MATCH:
            flips.append(res.index)

    # PROPKA decides charge states; PDB2PQR's hydrogen-bond network pass picks
    # the neutral His tautomer and the Asn/Gln/His flips.
    report["protonation"] = {
        "method": "PDB2PQR %s + PROPKA %s, pH %g" % (_version("pdb2pqr"),
                                                    _version("propka"), ph),
        "ph": ph,
        "flipped": [_label(r) for r in top.residues() if r.index in flips],
    }
    log = work / "protonated.log"     # PROPKA's summary lands in PDB2PQR's log
    if log.exists():
        near, shifted = _pka_flags(log.read_text(), ph)
        report["protonation"]["nearPH"] = near
        report["protonation"]["shifted"] = shifted
    top, pos = metal_sites(top, pos, before, set(flips), report)
    if overrides:
        top, pos = apply_states(top, pos, overrides, report)
    report["protonation"].update(_final_states(top))
    return top, pos


def apply_states(top, pos, overrides: dict, report: dict):
    """Protonation states set by hand (--state). They win over PROPKA and the
    metal-site rules: for a residue whose pKa sits near the pH, or a site the
    predictor is known to be weak at, test the other state this way."""
    by_key = {_rkey(r): r for r in top.residues()}
    for key, v in overrides.items():
        r = by_key.get(key)
        if r is None:
            raise PrepError("--state: no residue %s %s%s" % key)
        if v not in VARIANTS.get(r.name, ()):
            raise PrepError("--state: %s is not a state of %s (choose from %s)" % (
                v, _label(r), "/".join(VARIANTS.get(r.name, ())) or "nothing"))
    variants, done = [], []
    for r in top.residues():
        cur = _variant(r)
        v = overrides.get(_rkey(r), cur)
        if _rkey(r) in overrides and v != cur:
            done.append("%s: %s -> %s" % (_label(r), cur, v))
        variants.append(v if r.name in VARIANTS else None)
    m = app.Modeller(top, pos * unit.nanometer)
    m.addHydrogens(variants=variants)
    report["protonation"]["overrides"] = done
    return m.topology, _nm(m.positions)


def _variant(res) -> str | None:
    """Protonation state of a titratable residue, read off its hydrogens."""
    h = {a.name for a in res.atoms() if a.element == app.element.hydrogen}
    if res.name == "HIS":
        return {(True, True): "HIP", (True, False): "HID",
                (False, True): "HIE"}.get(("HD1" in h, "HE2" in h))
    if res.name == "ASP":
        return "ASH" if h & {"HD1", "HD2"} else "ASP"
    if res.name == "GLU":
        return "GLH" if h & {"HE1", "HE2"} else "GLU"
    if res.name == "LYS":
        return "LYS" if sum(1 for x in h if x.startswith("HZ")) >= 3 else "LYN"
    if res.name == "CYS":
        if "HG" in h:
            return "CYS"
        sg = next((a for a in res.atoms() if a.name == "SG"), None)
        bridged = sg is not None and any(
            b.name == "SG" and b.residue is not res for _, b in _bonds_of(sg))
        return "CYX" if bridged else "CYM"
    return None


def _final_states(top) -> dict:
    """What the structure ends up with, after PROPKA, metal sites and any
    overrides -- read from the hydrogens rather than from PDB2PQR's names."""
    states: dict[str, list[str]] = {}
    his = {"HID": 0, "HIE": 0, "HIP": 0}
    cyx = 0
    for r in top.residues():
        v = _variant(r)
        if v in his:
            his[v] += 1
        if v == "CYX":
            cyx += 1
        if v in ("ASH", "GLH", "LYN", "CYM", "HIP"):
            states.setdefault(v, []).append("%s %s%s" % _rkey(r))
    return {"nonDefault": states, "histidine": his, "disulfides": cyx // 2}


def metal_sites(top, pos, before: dict, flipped: set[int], report: dict):
    """Put metal-coordinating side chains in the state coordination implies.

    PDB2PQR does not see metals, so it may flip a His ring that coordinates
    one and leave a carbon pointing at the ion. Flips on metal ligands are
    undone first, from the deposited coordinates.
    """
    metals = [a for a in top.atoms()
              if a.element is not None and a.element.symbol in METALS
              and a.residue.name not in AMINO]
    report["protonation"]["metalSites"] = changes = []
    if not metals:
        return top, pos
    pos = pos.copy()
    mpos = pos[[a.index for a in metals]]
    near = lambda xyz: np.min(np.linalg.norm(mpos - xyz, axis=1)) < METAL_CUTOFF
    drop: set[int] = set()
    add: dict[int, list] = {}

    for res in top.residues():
        if res.name not in AMINO:
            continue
        atoms = {a.name: a for a in res.atoms()}
        hyd = {a.name: a for a in res.atoms() if a.element == app.element.hydrogen}
        heavy = [a for a in res.atoms() if a.element != app.element.hydrogen]
        label = _label(res)

        if res.index in flipped and any(
                near(before.get(_key(a), pos[a.index])) for a in heavy):
            for a in heavy:
                if _key(a) in before:
                    pos[a.index] = before[_key(a)]
            _replace_hydrogens(res, atoms, pos)
            changes.append("%s flip undone (metal ligand)" % label)

        bound = lambda name: name in atoms and near(pos[atoms[name].index])
        if "ND1" in atoms and "NE2" in atoms:   # histidine
            nd1, ne2 = bound("ND1"), bound("NE2")
            if nd1 == ne2:
                continue
            lig, free = ("ND1", "NE2") if nd1 else ("NE2", "ND1")
            lig_h, free_h = ("HD1", "HE2") if nd1 else ("HE2", "HD1")
            changed = lig_h in hyd
            if changed:
                drop.add(hyd[lig_h].index)
            if free_h not in hyd:
                add.setdefault(res.index, []).append(
                    (free_h, atoms[free].index, _ring_h(atoms, free, pos)))
                changed = True
            if changed:
                changes.append("%s HIS -> %s (%s on metal)" % (
                    label, "HIE" if nd1 else "HID", lig))
        elif "SG" in atoms and bound("SG") and "HG" in hyd:
            drop.add(hyd["HG"].index)
            changes.append("%s CYS -> CYM (SG on metal)" % label)
        elif any(bound(o) for o in ("OD1", "OD2", "OE1", "OE2")):
            for h in ("HD1", "HD2", "HE1", "HE2"):
                if h in hyd and res.name in ("ASP", "GLU"):
                    drop.add(hyd[h].index)
                    changes.append("%s %s deprotonated (carboxylate on metal)"
                                   % (label, res.name))
    if drop or add:
        top, pos = _edit(top, pos, drop, add)
    return top, pos


def _merge(fixed_top, fixed_pos, prot_top, prot_pos):
    """The fixed structure in its own residue order, with each residue taken
    from PDB2PQR's output where it has one (all but the caps)."""
    prot = {_rkey(r): r for r in prot_top.residues()}
    new, xyz = app.Topology(), []
    for chain in fixed_top.chains():
        c = new.addChain(chain.id)
        for res in chain.residues():
            src = None if res.name in CAPS else prot.get(_rkey(res))
            atoms, coords = ((list(src.atoms()), prot_pos) if src is not None
                             else (list(res.atoms()), fixed_pos))
            r = new.addResidue((src or res).name, c, res.id, res.insertionCode)
            for a in atoms:
                new.addAtom(a.name, a.element, r)
                xyz.append(coords[a.index])
    xyz = np.asarray(xyz)
    new.createStandardBonds()
    new.createDisulfideBonds(xyz * unit.nanometer)
    return new, xyz


def _protonate_caps(top, pos, names: dict):
    """Add the cap hydrogens with OpenMM. Every other residue's protonation
    state is pinned to what PDB2PQR chose, so nothing else changes."""
    variants = []
    for r in top.residues():
        v = names.get(_rkey(r))
        variants.append(v if v in VARIANTS.get(r.name, ()) else None)
    m = app.Modeller(top, pos * unit.nanometer)
    m.addHydrogens(variants=variants)
    return m.topology, _nm(m.positions)


def _ring_h(atoms: dict, name: str, pos) -> np.ndarray:
    """Hydrogen on an sp2 ring atom, along the outward bisector."""
    a, b = RING_NEIGHBOURS[name]
    x = pos[atoms[name].index]
    v = (x - pos[atoms[a].index]) + (x - pos[atoms[b].index])
    return x + (0.101 if name[0] == "N" else 0.108) * v / np.linalg.norm(v)


def _replace_hydrogens(res, atoms: dict, pos) -> None:
    """Re-seat the hydrogens of a His ring or an Asn/Gln amide after its heavy
    atoms were moved."""
    for h in res.atoms():
        if h.element != app.element.hydrogen:
            continue
        parent = next((b for a, b in _bonds_of(h) if b is not h), None)
        if parent is None:
            continue
        if res.name == "HIS" and parent.name in RING_NEIGHBOURS:
            pos[h.index] = _ring_h(atoms, parent.name, pos)
        elif parent.name in ("ND2", "NE2") and res.name in ("ASN", "GLN"):
            c = atoms["CG" if res.name == "ASN" else "CD"]
            o = atoms["OD1" if res.name == "ASN" else "OE1"]
            n = pos[parent.index]
            u = n - pos[c.index]
            u /= np.linalg.norm(u)
            w = pos[o.index] - pos[c.index]
            w -= u * np.dot(w, u)
            w /= np.linalg.norm(w)
            sign = 1.0 if h.name.endswith("1") else -1.0
            pos[h.index] = n + 0.101 * (0.5 * u + sign * 0.866 * w)


def _bonds_of(atom):
    for a, b in atom.residue.bonds():
        if a is atom:
            yield a, b
        elif b is atom:
            yield b, a


# ---------------------------------------------------------------------------
# Step 4: tethered refinement (OpenMM)
# ---------------------------------------------------------------------------
def refine(top, pos, original: set, tether_kcal: float, buffer_a: float,
           tolerance_kcal: float, platform: str | None, report: dict):
    ff = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")

    # Everything that is not polymer or water is held rigid, and so is any
    # residue Amber14 has no template for.
    het = [r for r in top.residues() if r.name not in AMINO | NUCLEIC | CAPS | WATER]
    probe = app.Modeller(top, pos * unit.nanometer)
    probe.delete(het)
    rkey = lambda r: (r.chain.id, r.id, r.insertionCode, r.name)
    unmatched = {rkey(r) for r in ff.getUnmatchedResidues(probe.topology)}
    het += [r for r in top.residues() if rkey(r) in unmatched]
    het_ids = {r.index for r in het}

    sub = app.Modeller(top, pos * unit.nanometer)
    sub.delete([r for r in top.residues() if r.index in het_ids])
    mobile = [a for a in top.atoms() if a.residue.index not in het_ids]
    frozen = [a for a in top.atoms() if a.residue.index in het_ids]
    sys_index = {a.index: i for i, a in enumerate(mobile)}
    sys_index.update({a.index: len(mobile) + i for i, a in enumerate(frozen)})

    system = ff.createSystem(sub.topology, nonbondedMethod=app.CutoffNonPeriodic,
                             nonbondedCutoff=1.0 * unit.nanometer,
                             constraints=None, rigidWater=False)
    nb = next(f for f in system.getForces() if isinstance(f, mm.NonbondedForce))

    # Hetero atoms join as massless particles: fixed in place, felt only as a
    # steric wall (small sigma so that ordinary contacts are not pushed apart).
    for a in frozen:
        sym = a.element.symbol if a.element is not None else "C"
        lone = len(list(a.residue.atoms())) == 1
        q = 0.0
        if lone and sym in METALS:
            q = ION_CHARGE.get(sym, 2)
        elif lone and sym in ANION_CHARGE:
            q = ANION_CHARGE[sym]
        system.addParticle(0.0)
        nb.addParticle(q, 0.20 if sym in METALS else 0.25, 0.4)

    # No interactions between nearby hetero atoms: they are fixed anyway, and
    # bonded neighbours would otherwise add a large constant to the energy.
    fpos = pos[[a.index for a in frozen]] if frozen else np.zeros((0, 3))
    fidx = [sys_index[a.index] for a in frozen]
    for i in range(len(frozen)):
        d = np.linalg.norm(fpos[i + 1:] - fpos[i], axis=1)
        for j in np.nonzero(d < 0.6)[0] + i + 1:
            nb.addException(fidx[i], fidx[j], 0.0, 1.0, 0.0)

    # Covalent links and metal coordination: no nonbonded terms between the
    # two residues; residues on a metal are held fixed.
    links, held = set(), set()
    if frozen:
        for res in top.residues():
            if res.index in het_ids:
                continue
            for a in res.atoms():
                if a.element is None or a.element == app.element.hydrogen:
                    continue
                d = np.linalg.norm(fpos - pos[a.index], axis=1)
                for k in np.nonzero(d < METAL_CUTOFF)[0]:
                    h = frozen[k]
                    metal = h.element is not None and h.element.symbol in METALS
                    if d[k] < COVALENT_CUTOFF or metal:
                        links.add((res.index, h.residue.index))
                        if metal:
                            held.add(res.index)
    by_res: dict[int, list[int]] = {}
    for a in top.atoms():
        by_res.setdefault(a.residue.index, []).append(sys_index[a.index])
    existing = {tuple(sorted(nb.getExceptionParameters(i)[:2]))
                for i in range(nb.getNumExceptions())}
    for r, h in links:
        for i in by_res[r]:
            for j in by_res[h]:
                if tuple(sorted((i, j))) not in existing:
                    nb.addException(i, j, 0.0, 1.0, 0.0)
                    existing.add(tuple(sorted((i, j))))
    for a in mobile:
        if a.residue.index in held and a.element != app.element.hydrogen:
            system.setParticleMass(sys_index[a.index], 0.0)

    # Flat-bottom tethers on heavy atoms that were in the deposited structure.
    k = tether_kcal * KCAL * 100.0          # kcal/mol/A^2 -> kJ/mol/nm^2
    tether = mm.CustomExternalForce(
        # The epsilon keeps the gradient finite at d = 0, where every atom starts.
        "0.5*k*step(d-b)*(d-b)^2; d=sqrt((x-x0)^2+(y-y0)^2+(z-z0)^2+1e-12)")
    tether.addGlobalParameter("k", k)
    tether.addGlobalParameter("b", buffer_a / 10.0)
    for p in ("x0", "y0", "z0"):
        tether.addPerParticleParameter(p)
    tethered = []
    for a in mobile:
        if (a.element == app.element.hydrogen or a.residue.index in held
                or _key(a) not in original):
            continue
        i = sys_index[a.index]
        tether.addParticle(i, list(pos[a.index]))
        tethered.append(a.index)
    if tether_kcal > 0:
        system.addForce(tether)

    # Stage 1: PDBFixer's rebuilt atoms can come out with badly bent backbone
    # (a carbonyl folded onto the next nitrogen) that a full minimisation
    # cannot escape. Straighten them with bonded terms and a soft, bounded
    # repulsion -- enough to stop atoms passing through rings, not enough to
    # lock a bad start in place -- with everything else held.
    built_res = {a.residue.index for a in mobile
                 if a.element != app.element.hydrogen and _key(a) not in original}
    movers = [i for i, a in enumerate(mobile) if a.residue.index in built_res
              and (a.element == app.element.hydrogen or _key(a) not in original)]
    mpos = pos[[a.index for a in mobile]]
    if movers:
        s1 = ff.createSystem(sub.topology, nonbondedMethod=app.NoCutoff,
                             constraints=None, rigidWater=False)
        for i in reversed(range(s1.getNumForces())):
            if isinstance(s1.getForce(i), (mm.NonbondedForce, mm.CMMotionRemover)):
                s1.removeForce(i)
        soft = mm.CustomNonbondedForce("100*step(0.3-r)*(1-r/0.3)^2")
        soft.setNonbondedMethod(mm.CustomNonbondedForce.CutoffNonPeriodic)
        soft.setCutoffDistance(0.3)
        for _ in range(s1.getNumParticles()):
            soft.addParticle([])
        soft.createExclusionsFromBonds(
            [(a.index, b.index) for a, b in sub.topology.bonds()], 3)
        heavy_sub = [i for i, a in enumerate(sub.topology.atoms())
                     if a.element != app.element.hydrogen]
        soft.addInteractionGroup(set(movers) & set(heavy_sub), set(heavy_sub))
        s1.addForce(soft)
        keep = set(movers)
        for i in range(s1.getNumParticles()):
            if i not in keep:
                s1.setParticleMass(i, 0.0)
        c1 = mm.Context(s1, mm.VerletIntegrator(0.001),
                        mm.Platform.getPlatformByName("CPU"))
        c1.setPositions(mpos * unit.nanometer)
        mm.LocalEnergyMinimizer.minimize(c1, 1.0, 2000)
        mpos = c1.getState(getPositions=True).getPositions(
            asNumpy=True).value_in_unit(unit.nanometer)
        del c1

    xyz = np.vstack([mpos, fpos])
    ctx = None
    for name in ([platform] if platform else [None, "CPU"]):
        try:
            integrator = mm.VerletIntegrator(0.001)
            ctx = (mm.Context(system, integrator,
                              mm.Platform.getPlatformByName(name)) if name
                   else mm.Context(system, integrator))
            ctx.setPositions(xyz * unit.nanometer)
            e0 = ctx.getState(getEnergy=True).getPotentialEnergy().value_in_unit(
                unit.kilocalories_per_mole)
            break
        except Exception as exc:   # e.g. a GPU platform that cannot compile
            ctx, err = None, exc
    if ctx is None:
        raise PrepError("could not create an OpenMM context: %s" % err)
    t0 = time.monotonic()
    mm.LocalEnergyMinimizer.minimize(ctx, tolerance_kcal * KCAL * 10.0, 0)
    state = ctx.getState(getEnergy=True, getPositions=True)
    e1 = state.getPotentialEnergy().value_in_unit(unit.kilocalories_per_mole)
    new = state.getPositions(asNumpy=True).value_in_unit(unit.nanometer)

    out = pos.copy()
    for a in top.atoms():
        out[a.index] = new[sys_index[a.index]]
    shift = np.linalg.norm(out[tethered] - pos[tethered], axis=1) * 10.0

    # Rebuilt residues that still came out as D (checked again after fix()).
    d_res = []
    for res in top.residues():
        if res.index not in built_res or res.name in CAPS:
            continue
        at = {a.name: out[a.index] for a in res.atoms()}
        if all(n in at for n in ("N", "CA", "C", "CB")) and _chirality(
                at["N"], at["CA"], at["C"], at["CB"]) < 0:
            d_res.append(_label(res))
    report["refinement"] = {
        "forcefield": "amber14-all + TIP3P, cutoff 10 A, reaction field",
        "platform": ctx.getPlatform().getName(),
        "tether": {"kcalPerA2": tether_kcal, "bufferA": buffer_a,
                   "atoms": len(tethered)},
        "fixedResidues": sorted("%s %s%s %s" % (r.chain.id, r.id,
                                                r.insertionCode.strip(), r.name)
                                for r in top.residues() if r.index in held),
        "heteroGroups": sorted({"%s %s" % (r.name, r.id) for r in het}),
        "rebuiltResidues": len(built_res),
        "rebuiltD": d_res,
        "energyKcal": [round(e0, 1), round(e1, 1)],
        "rmsdA": round(float(np.sqrt(np.mean(shift ** 2))), 3) if len(shift) else 0.0,
        "maxShiftA": round(float(shift.max()), 3) if len(shift) else 0.0,
        "seconds": round(time.monotonic() - t0, 1),
    }
    return out


# ---------------------------------------------------------------------------
def prepare(path: str, out: str, ph: float = 7.0, water_cutoff: float | None = 4.5,
            drop: frozenset[str] = frozenset(), max_loop: int = 10,
            max_tail: int = 0, replace_nonstandard: bool = True,
            minimize: bool = True, tether: float = 10.0, buffer: float = 0.25,
            tolerance: float = 0.1, platform: str | None = None,
            plddt_trim: float | None = None, predicted: bool | None = None,
            overrides: dict | None = None, pqr: str | None = None,
            pqr_water: bool = False, name: str | None = None,
            no_rebuild: dict[str, set[int]] | None = None) -> dict:
    report: dict = {"tool": "quickprep %s" % __version__,
                    "name": name or Path(path).name}
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="quickprep-") as tmp:
        work = Path(tmp)
        steps = report["stepSeconds"] = {}
        t = time.monotonic()
        st, deposited, trimmed = clean(path, water_cutoff, drop, report,
                                       plddt_trim, predicted)
        steps["clean"], t = round(time.monotonic() - t, 1), time.monotonic()
        report["noRebuild"] = _ranges(no_rebuild) if no_rebuild else None
        top, pos, original = fix(st, max_loop, max_tail, replace_nonstandard,
                                 report, trimmed, no_rebuild)
        steps["fix"], t = round(time.monotonic() - t, 1), time.monotonic()
        top, pos = protonate(top, pos, ph, work, report, overrides)
        steps["protonate"], t = round(time.monotonic() - t, 1), time.monotonic()
        if minimize:
            pos = refine(top, pos, original, tether, buffer, tolerance,
                         platform, report)
            steps["refine"] = round(time.monotonic() - t, 1)

    if pqr:
        write_pqr(top, pos, pqr, pqr_water, report)
    report["seconds"] = round(time.monotonic() - t0, 1)
    write_output(top, pos, out, deposited, report)
    return report


def write_output(top, pos, out: str, deposited: dict, report: dict) -> None:
    """The prepared structure, carrying its own provenance: REMARK 99 lines
    state the pH, protonation model and everything built; deposited atoms keep
    their occupancy and B-factor (pLDDT, for a predicted model); atoms that were
    built have occupancy 0.00 -- modelled, not observed."""
    occb, built = [], 0
    for a in top.atoms():
        k = _key(a)
        if k in deposited:
            occb.append(deposited[k])
            continue
        if a.element == app.element.hydrogen:
            parent = next((b for _, b in _bonds_of(a)), None)
            if parent is not None and _key(parent) in deposited:
                occb.append(deposited[_key(parent)])
                continue
        else:
            built += 1
        occb.append((0.0, 0.0))
    report["output"] = {"path": out, "atoms": top.getNumAtoms(),
                        "hydrogens": sum(1 for a in top.atoms()
                                         if a.element == app.element.hydrogen),
                        "builtHeavyAtoms": built}

    buf = io.StringIO()
    app.PDBFile.writeFile(top, pos * unit.nanometer, buf, keepIds=True)
    lines, i = _remarks(report), 0
    for line in buf.getvalue().splitlines():
        if line.startswith(("ATOM", "HETATM")):
            o, b = occb[i]
            i += 1
            line = line[:54] + "%6.2f%6.2f" % (o, min(b, 999.99)) + line[66:]
        elif line.startswith("REMARK"):
            continue
        lines.append(line)
    text = "\n".join(lines) + "\n"
    if out.lower().endswith((".cif", ".mmcif")):
        st = gemmi.read_pdb_string(text)
        st.setup_entities()
        st.make_mmcif_document().write_file(out)
    else:
        Path(out).write_text(text)


def write_pqr(top, pos, path: str, keep_water: bool, report: dict) -> None:
    """Charges and radii for Poisson-Boltzmann electrostatics (APBS), from the
    same Amber14 force field the refinement used: ff14SB charges, Rmin/2 radii.
    Groups with no parameters (ligands, cofactors) are left out and named."""
    ff = app.ForceField("amber14-all.xml", "amber14/tip3p.xml")
    rkey = lambda r: (r.chain.id, r.id, r.insertionCode, r.name)
    keep = [r for r in top.residues()
            if r.name in AMINO | NUCLEIC | CAPS or (keep_water and r.name in WATER)]
    first = {r.index for r in keep}
    probe = app.Modeller(top, pos * unit.nanometer)
    probe.delete([r for r in top.residues() if r.index not in first])
    bad = {rkey(r) for r in ff.getUnmatchedResidues(probe.topology)}
    keep = [r for r in keep if rkey(r) not in bad]
    keep_ids = {r.index for r in keep}
    sub = app.Modeller(top, pos * unit.nanometer)
    sub.delete([r for r in top.residues() if r.index not in keep_ids])
    # Keep the System referenced: its forces are freed along with it.
    system = ff.createSystem(sub.topology, nonbondedMethod=app.NoCutoff)
    nb = next(f for f in system.getForces() if isinstance(f, mm.NonbondedForce))

    def qr(i):
        q, s, e = nb.getParticleParameters(i)
        r = (s.value_in_unit(unit.angstrom) * 2 ** (1 / 6) / 2
             if e.value_in_unit(unit.kilojoule_per_mole) > 0 else 0.0)
        return q.value_in_unit(unit.elementary_charge), r

    params, j = {}, 0
    for a in top.atoms():
        if a.residue.index in keep_ids:
            params[a.index] = qr(j)
            j += 1
    # Lone ions take Amber14's ion parameters, matched by residue name first.
    skipped = set()
    for r in top.residues():
        if r.index in keep_ids or r.name in WATER:
            continue
        atoms = list(r.atoms())
        if len(atoms) == 1 and atoms[0].element is not None:
            t = app.Topology()
            rr = t.addResidue(r.name, t.addChain())
            t.addAtom(atoms[0].name, atoms[0].element, rr)
            for kw in ({"residueTemplates": {rr: r.name}}, {}):
                try:
                    s = ff.createSystem(t, nonbondedMethod=app.NoCutoff, **kw)
                    q, sg, e = next(f for f in s.getForces() if isinstance(
                        f, mm.NonbondedForce)).getParticleParameters(0)
                    params[atoms[0].index] = (
                        q.value_in_unit(unit.elementary_charge),
                        sg.value_in_unit(unit.angstrom) * 2 ** (1 / 6) / 2)
                    break
                except Exception:
                    continue
        if not any(a.index in params for a in atoms):
            skipped.add("%s %s" % (r.name, r.id))

    xyz = pos * 10.0
    out = ["REMARK   1 PQR written by quickprep %s: Amber14 (ff14SB) charges, " % __version__ +
           "Amber Rmin/2 radii",
           "REMARK   1 pH %g; same charges as the refinement" % report["protonation"]["ph"],
           "REMARK   1 waters %s" % ("kept" if keep_water else "left out")]
    for s in _wrap("REMARK   1 no parameters, left out: ", sorted(skipped)):
        out.append(s)
    n, total = 0, 0.0
    for a in top.atoms():
        if a.index not in params:
            continue
        q, rad = params[a.index]
        r = a.residue
        name = a.name if len(a.name) >= 4 else " " + a.name.ljust(3)
        n += 1
        total += q
        out.append("ATOM  %5d %-4s %-3s %1s%4s%1s   %8.3f%8.3f%8.3f%8.4f%7.4f" % (
            n % 100000, name[:4], (_variant(r) or r.name)[:3], r.chain.id[:1],
            r.id[-4:], (r.insertionCode or " ")[:1], *xyz[a.index], q, rad))
    out.append("END")
    Path(path).write_text("\n".join(out) + "\n")
    report["pqr"] = {"path": path, "atoms": n, "netCharge": round(total, 3),
                     "water": keep_water, "leftOut": sorted(skipped),
                     "parameters": "Amber14 ff14SB charges, Rmin/2 radii"}


def summary(r: dict) -> str:
    p, w, lp, inp = r["protonation"], r["water"], r["loops"], r["input"]
    kind = ("predicted model" + (", mean pLDDT %.0f" % inp["plddt"]["mean"]
                                 if inp.get("plddt") else "")
            if inp.get("predicted") else
            ", ".join(x for x in (inp.get("method") or "",
                                  "%.2f A" % inp["resolution"] if inp.get("resolution")
                                  else "") if x) or "method not stated")
    lines = [
        "input      %s  (%s; %d model%s, chains %s)" % (
            r.get("name") or inp["path"], kind, inp["models"],
            "" if inp["models"] == 1 else "s, kept the first",
            ",".join(inp["chains"])),
        "altlocs    %d residues resolved by occupancy" % len(r["altlocs"]),
    ]
    for wn in r.get("warnings", []):
        lines.append("WARNING    %s" % wn)
    if r.get("trimmed"):
        lines.append("trimmed    pLDDT < %g: %s" % (
            r["trimmed"]["cutoff"], ", ".join(r["trimmed"]["ranges"]) or "nothing"))
    if r.get("noRebuild"):
        lines.append("left out   %s (gaps there are capped, not rebuilt)"
                     % ", ".join(r["noRebuild"]))
    if r.get("dropped"):
        lines.append("dropped    %s" % ", ".join(r["dropped"]))
    lines += [
        "water      %d of %d deleted (> %s A from protein/ligand), %d kept" % (
            w["deleted"], w["found"], w["cutoff"], w["kept"]),
        "loops      built %d, capped %d, not built %d" % (
            len(lp["built"]), len(lp["capped"]), len(lp["notBuilt"])),
    ]
    for tag in ("built", "capped", "notBuilt"):
        lines += ["             %-8s %s" % (tag, s) for s in lp[tag]]
    lines += [
        "residues   %d with missing atoms rebuilt, %d modified -> standard" % (
            len(r["missingAtoms"]), len(r["nonstandard"])),
        "protonate  %s" % p["method"],
        "             His %s; %d disulfide%s" % (
            ", ".join("%s %d" % kv for kv in p["histidine"].items()),
            p.get("disulfides", 0), "" if p.get("disulfides", 0) == 1 else "s"),
    ]
    for k, v in p["nonDefault"].items():
        lines.append("             %s: %s" % (k, ", ".join(v)))
    if p["flipped"]:
        lines.append("             flipped: %s" % ", ".join(p["flipped"]))
    for m in p.get("metalSites", []):
        lines.append("             metal: %s" % m)
    for o in p.get("overrides", []):
        lines.append("             set by --state: %s" % o)
    if p.get("nearPH"):
        lines.append("             pKa within 1 of pH (state is a judgement call): %s"
                     % ", ".join(p["nearPH"]))
    if p.get("shifted"):
        lines.append("             pKa shifted >= 2 from model: %s" % ", ".join(p["shifted"]))
    if "refinement" in r:
        f = r["refinement"]
        lines += [
            "refine     %s, tethers %g kcal/mol/A^2 beyond %g A on %d atoms" % (
                f["forcefield"], f["tether"]["kcalPerA2"], f["tether"]["bufferA"],
                f["tether"]["atoms"]),
            "             energy %.0f -> %.0f kcal/mol, RMSD %.2f A, max %.2f A, %ss on %s"
            % (f["energyKcal"][0], f["energyKcal"][1], f["rmsdA"], f["maxShiftA"],
               f["seconds"], f["platform"]),
        ]
        if f["heteroGroups"]:
            lines.append("             held rigid: %s" % ", ".join(f["heteroGroups"]))
        if f["fixedResidues"]:
            lines.append("             fixed (metal site): %s" % ", ".join(f["fixedResidues"]))
        if f["rebuiltD"]:
            lines.append("             WARNING rebuilt residues with D chirality: %s"
                         % ", ".join(f["rebuiltD"]))
    if r.get("pqr"):
        q = r["pqr"]
        lines.append("pqr        %s  (%d atoms, net charge %+.2f, %s%s)" % (
            q["path"], q["atoms"], q["netCharge"], q["parameters"],
            "; left out: " + ", ".join(q["leftOut"]) if q["leftOut"] else ""))
    nb = r["output"]["builtHeavyAtoms"]
    lines.append("output     %s  (%d atoms, %d H; %d built heavy atom%s at occupancy "
                 "0.00)  in %ss" % (r["output"]["path"], r["output"]["atoms"],
                                    r["output"]["hydrogens"], nb,
                                    "" if nb == 1 else "s", r["seconds"]))
    return "\n".join(lines)


def _remarks(r: dict) -> list[str]:
    """REMARK 99 header: the assumptions travel with the file."""
    p, lp, inp = r["protonation"], r["loops"], r["input"]
    rows = [("QUICKPREP", ["%s, open-source stand-in for MOE QuickPrep" % r["tool"]]),
            ("SOURCE", [" ".join(str(x) for x in (
                r.get("name") or Path(inp["path"]).name,
                "predicted model" if inp.get("predicted") else inp.get("method") or "",
                "%.2f A" % inp["resolution"] if inp.get("resolution") else "") if x)]),
            ("PH", ["%g" % p["ph"]]),
            ("STATES", [p["method"] + "; flips and His tautomers by PDB2PQR "
                        "H-bond network"]),
            ("HIS", ["%s %d" % kv for kv in p["histidine"].items()]),
            ("NONDEFAULT", ["%s %s" % (k, x) for k, v in p["nonDefault"].items() for x in v]),
            ("FLIPPED", p["flipped"]),
            ("METAL", p.get("metalSites", [])),
            ("SET", p.get("overrides", [])),
            ("TRIMMED", ["pLDDT < %g: %s" % (r["trimmed"]["cutoff"],
                                             ", ".join(r["trimmed"]["ranges"]))]
             if r.get("trimmed") and r["trimmed"]["ranges"] else []),
            ("BUILT", lp["built"]), ("CAPPED", lp["capped"]),
            ("NOT BUILT", lp["notBuilt"]),
            ("COMPLETED", ["%d side chains" % len(r["missingAtoms"])]
             if r["missingAtoms"] else []),
            ("REFINED", ["%s; tethers %g kcal/mol/A^2 beyond %g A; RMSD %.2f A" % (
                r["refinement"]["forcefield"], r["refinement"]["tether"]["kcalPerA2"],
                r["refinement"]["tether"]["bufferA"], r["refinement"]["rmsdA"])]
             if r.get("refinement") else ["not minimised"]),
            ("MODELLED", ["atoms at occupancy 0.00 were built, not observed"])]
    out = []
    for tag, items in rows:
        if items:
            out += _wrap("REMARK  99 %-10s " % tag, items, sep="; ")
    return out


def _wrap(prefix: str, items, sep: str = ", ", width: int = 80) -> list[str]:
    """Items joined and wrapped to ``width`` columns, every line prefixed."""
    import textwrap
    text = sep.join(items)
    return [prefix + line for line in textwrap.wrap(
        text, width=width - len(prefix), break_on_hyphens=False)] if text else []


# ---------------------------------------------------------------------------
def _key(atom) -> tuple:
    r = atom.residue
    return (r.chain.id, r.id, r.insertionCode.strip(), atom.name)


def _rkey(res) -> tuple:
    return (res.chain.id, res.id, res.insertionCode.strip())


def _label(res) -> str:
    return "%s %s%s %s" % (res.chain.id, res.id, res.insertionCode.strip(), res.name)


def _chirality(n, ca, c, cb) -> float:
    """(N-CA) . ((C-CA) x (CB-CA)): positive for an L amino acid."""
    return float(np.dot(n - ca, np.cross(c - ca, cb - ca)))


def _nm(pos) -> np.ndarray:
    if hasattr(pos, "value_in_unit"):
        pos = pos.value_in_unit(unit.nanometer)
    return np.array([[p[0], p[1], p[2]] for p in pos], dtype=float)


def _edit(top, pos, drop: set[int], add: dict[int, list]):
    """Copy of ``top`` without the atoms in ``drop`` and with hydrogens added:
    ``add`` maps residue index -> [(name, parent atom index, xyz nm)]."""
    new, xyz, amap = app.Topology(), [], {}
    for chain in top.chains():
        c = new.addChain(chain.id)
        for res in chain.residues():
            r = new.addResidue(res.name, c, res.id, res.insertionCode)
            for a in res.atoms():
                if a.index in drop:
                    continue
                amap[a.index] = new.addAtom(a.name, a.element, r, a.id)
                xyz.append(pos[a.index])
            for name, parent, p in add.get(res.index, ()):
                h = new.addAtom(name, app.element.hydrogen, r)
                xyz.append(p)
                new.addBond(amap[parent], h)
    for a, b in top.bonds():
        if a.index in amap and b.index in amap:
            new.addBond(amap[a.index], amap[b.index])
    return new, np.asarray(xyz)


def _pka_flags(text: str, ph: float) -> tuple[list[str], list[str]]:
    """From PROPKA's summary: groups whose pKa is within 1 unit of the pH (the
    assigned state is a judgement call) and groups shifted 2 or more units
    from their model pKa (buried, salt-bridged or metal-adjacent)."""
    near, shifted, on = [], [], False
    for line in text.splitlines():
        if line.startswith("SUMMARY OF THIS PREDICTION"):
            on = True
            continue
        if on and line.startswith("-----"):
            break
        f = line.split()
        if on and len(f) >= 4 and f[0] in ("ASP", "GLU", "HIS", "CYS", "TYR",
                                           "LYS", "ARG", "N+", "C-"):
            try:
                pka, model = float(f[3]), float(f[4])
            except (ValueError, IndexError):
                continue
            label = "%s %s %s %.1f" % (f[2], f[0], f[1], pka)
            if abs(pka - ph) < 1.0:
                near.append(label)
            if abs(pka - model) >= 2.0:
                shifted.append("%s (model %.1f)" % (label, model))
    return near, shifted


def _number_caps(top) -> None:
    """PDBFixer numbers inserted residues back from the next residue, so a cap
    pair in a one-residue gap takes the previous residue's number. Give NME and
    ACE numbers of their own: free numbers in the gap where there are two,
    insertion codes on the residue before the gap where there are not."""
    for chain in top.chains():
        res = list(chain.residues())
        for i in range(1, len(res) - 2):
            if res[i].name != "NME" or res[i + 1].name != "ACE":
                continue
            prev, nxt = res[i - 1], res[i + 2]
            try:
                p, q = int(prev.id), int(nxt.id)
            except ValueError:
                continue
            free = list(range(p + 1, q))
            if len(free) >= 2:
                ids = [(free[0], ""), (free[-1], "")]
            elif free:
                ids = [(free[0], ""), (free[0], "A")]
            else:
                start = prev.insertionCode.strip()
                k = ord(start) - ord("A") + 1 if start else 0
                ids = [(p, chr(ord("A") + k)), (p, chr(ord("A") + k + 1))]
            for r, (num, code) in zip((res[i], res[i + 1]), ids):
                r.id, r.insertionCode = str(num), code or " "


def _gap_numbers(residues, idx: int, n: int) -> set[int]:
    """Residue numbers a gap of ``n`` before ``residues[idx]`` would take."""
    try:
        if idx > 0:
            p = int(residues[idx - 1].id)
            return set(range(p + 1, p + 1 + n))
        if residues:
            q = int(residues[0].id)
            return set(range(q - n, q))
    except ValueError:
        pass
    return set()


def _ranges(by_chain: dict[str, set[int]]) -> list[str]:
    out = []
    for chain, nums in sorted(by_chain.items()):
        nums = sorted(nums)
        start = prev = nums[0]
        for x in nums[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            out.append("%s %d" % (chain, start) if start == prev
                       else "%s %d-%d" % (chain, start, prev))
            if x is not None:
                start = prev = x
    return out


def _head(path: str, size: int = 200_000) -> str:
    """The start of the file, lower-cased, for header markers."""
    import gzip
    opener = gzip.open if str(path).endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as fh:
            return fh.read(size).lower()
    except OSError:
        return ""


def _expdta(head: str) -> str:
    for line in head.splitlines():
        if line.startswith("expdta"):
            return line[10:].strip().upper()
    return ""


def _pdb2pqr() -> str:
    exe = Path(sys.executable).with_name("pdb2pqr")
    return str(exe) if exe.exists() else "pdb2pqr"


def _version(pkg: str) -> str:
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return "?"


_ONE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
        "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
        "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
        "TYR": "Y", "VAL": "V"}


def _one(name: str) -> str:
    return _ONE.get(name, "X")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="quickprep",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version",
                    version="quickprep %s" % __version__)
    ap.add_argument("structure", help="PDB or mmCIF file")
    ap.add_argument("-o", "--out", help="output .pdb or .cif "
                    "(default: <input>_prep.pdb)")
    ap.add_argument("--ph", type=float, default=7.0)
    ap.add_argument("--water-cutoff", type=float, default=4.5,
                    help="delete waters farther than this (A) from any "
                         "non-water atom (default 4.5)")
    ap.add_argument("--keep-water", action="store_true",
                    help="keep every crystallographic water")
    ap.add_argument("--drop-water", action="store_true", help="delete all waters")
    ap.add_argument("--drop", default="",
                    help="comma-separated residue names to delete first, e.g. a "
                         "bound ligand or additives")
    ap.add_argument("--drop-additives", action="store_true",
                    help="delete common crystallisation additives and buffer "
                         "components (glycerol, sulfate, PEG, ...); metals and "
                         "cofactors stay")
    ap.add_argument("--max-loop", type=int, default=10,
                    help="build internal gaps up to this many residues; longer "
                         "ones are capped with ACE/NME (default 10)")
    ap.add_argument("--max-tail", type=int, default=0,
                    help="build missing terminal residues up to this many "
                         "(default 0: PDBFixer's terminal tails are guesses with "
                         "nothing to anchor them; MOE's default is 5)")
    ap.add_argument("--keep-nonstandard", action="store_true",
                    help="do not convert modified residues (MSE, ...) to their "
                         "parent amino acid")
    ap.add_argument("--state", action="append", default=[], metavar="A:57=HIE",
                    help="force a protonation state (HID/HIE/HIP, ASH/ASP, GLH/GLU, "
                         "LYN/LYS, CYS/CYM/CYX); repeatable. Use it to test the "
                         "other state of a residue whose pKa sits near the pH")
    ap.add_argument("--no-rebuild", action="append", default=[], metavar="A:5-8,12",
                    help="residues removed or left out on purpose; a gap they "
                         "fall in is capped, never rebuilt from SEQRES. Repeatable, "
                         "one chain each")
    ap.add_argument("--plddt-trim", type=float, metavar="CUTOFF",
                    help="predicted model: delete residues whose CA pLDDT (the "
                         "B-factor column) is below this, e.g. 70, before anything "
                         "is built; trimmed regions are capped, never rebuilt")
    ap.add_argument("--predicted", action="store_true",
                    help="treat the input as a predicted model even if its header "
                         "does not say so")
    ap.add_argument("--pqr", metavar="FILE",
                    help="also write a PQR (Amber14 charges, Rmin/2 radii) for "
                         "APBS electrostatics")
    ap.add_argument("--pqr-water", action="store_true",
                    help="keep the retained waters in the PQR")
    ap.add_argument("--name", help="name to record for the input (default: file name)")
    ap.add_argument("--no-minimize", action="store_true")
    ap.add_argument("--tether", type=float, default=10.0,
                    help="tether strength, kcal/mol/A^2 (default 10; 0 = none)")
    ap.add_argument("--buffer", type=float, default=0.25,
                    help="free movement before a tether acts, A (default 0.25)")
    ap.add_argument("--tolerance", type=float, default=0.1,
                    help="stop minimising at this RMS gradient, kcal/mol/A")
    ap.add_argument("--platform", help="OpenMM platform (default: fastest)")
    ap.add_argument("--report", help="write the full report as JSON here")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON instead of the summary")
    args = ap.parse_args(argv)

    out = args.out or str(Path(args.structure).with_suffix("")) + "_prep.pdb"
    overrides = {}
    for s in args.state:
        m = re.fullmatch(r"\s*([^:\s]+):(-?\d+)([A-Za-z]?)\s*=\s*([A-Za-z]{3})\s*", s)
        if not m:
            ap.error("--state %r: expected CHAIN:NUMBER=STATE, e.g. A:57=HIE" % s)
        overrides[(m.group(1), m.group(2), m.group(3))] = m.group(4).upper()
    no_rebuild: dict[str, set[int]] = {}
    for s in args.no_rebuild:
        chain, sep, body = s.strip().partition(":")
        parts = [re.fullmatch(r"\s*(-?\d+)(?:-(-?\d+))?\s*", x) for x in body.split(",")]
        if not sep or not all(parts):
            ap.error("--no-rebuild %r: expected CHAIN:RANGES, e.g. A:5-8,12" % s)
        for m in parts:
            lo = int(m.group(1))
            no_rebuild.setdefault(chain, set()).update(
                range(lo, int(m.group(2) or lo) + 1))
    drop = frozenset(x.strip().upper() for x in args.drop.split(",") if x.strip())
    if args.drop_additives:
        drop |= ADDITIVES
    cutoff = None if args.keep_water else (0.0 if args.drop_water
                                           else args.water_cutoff)
    try:
        report = prepare(
            args.structure, out, ph=args.ph, water_cutoff=cutoff,
            drop=drop,
            max_loop=args.max_loop, max_tail=args.max_tail,
            replace_nonstandard=not args.keep_nonstandard,
            minimize=not args.no_minimize, tether=args.tether,
            buffer=args.buffer, tolerance=args.tolerance, platform=args.platform,
            plddt_trim=args.plddt_trim, predicted=True if args.predicted else None,
            overrides=overrides, pqr=args.pqr, pqr_water=args.pqr_water,
            name=args.name, no_rebuild=no_rebuild or None)
    except PrepError as exc:
        print("quickprep: %s" % exc, file=sys.stderr)
        return 1
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    print(json.dumps(report) if args.json else summary(report))
    return 0


def cli() -> None:
    """Console-script entry point."""
    sys.exit(main(sys.argv[1:]))
