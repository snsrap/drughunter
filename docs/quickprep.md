# quickprep

Protein structure preparation in the spirit of MOE's QuickPrep, built from open
tools. It takes a deposited or predicted structure and makes it ready for pocket
finding, docking or electrostatics, and records everything it changed.

## Install

```sh
pyenv virtualenv 3.11.14 quickprep
~/.pyenv/versions/quickprep/bin/pip install -e /home/sinas/projects/quickprep
```

That pulls in OpenMM, PDBFixer, PDB2PQR, PROPKA, gemmi and numpy.

## Use

```sh
quickprep 1HSG.pdb -o 1HSG_prep.pdb                 # or: python -m quickprep ...
quickprep AF-P04637-F1.pdb --plddt-trim 70 --pqr p53.pqr
quickprep 3PTB.pdb --drop-additives --state A:57=HIE --report prep.json
```

```python
from quickprep import prepare, summary

report = prepare("1HSG.pdb", "1HSG_prep.pdb", ph=7.0)
print(summary(report))
```

`prepare()` writes the prepared structure and returns the report as a dict;
`summary()` renders it as the text the command prints.

## What it does

An open-source stand-in for MOE's QuickPrep. It runs the same four stages with
open tools:

| QuickPrep stage | here |
| --- | --- |
| Structure Preparation | gemmi: first model, highest-occupancy altloc, hydrogens off. PDBFixer: missing atoms, gaps ≤ 10 residues built, longer gaps capped with ACE/NME, missing chain ends left off (`--max-tail` to build them), modified residues (MSE, …) → parent |
| delete distant solvent | waters > 4.5 Å from every non-water atom removed |
| Protonate3D | PDB2PQR + PROPKA at the given pH: ionisation, His tautomers, Asn/Gln/His flips, H-bond network. Metal ligands then corrected (see below) |
| tethers + refinement | OpenMM, Amber14/TIP3P: flat-bottom tethers on deposited heavy atoms, 10 kcal/mol/Å² beyond 0.25 Å, minimised to 0.1 kcal/mol/Å |

On top of QuickPrep it covers what the [GPTomics bioSkills structure-preparation
skill](https://github.com/GPTomics/bioSkills/tree/main/structural-biology/structure-preparation)
asks of a preparation step:

| | |
| --- | --- |
| assess first | method, resolution, experimental or predicted; shaky inputs get a warning |
| predicted models | detected from the header (AlphaFold, ESMFold, …, or `--predicted`); `--plddt-trim 70` removes low-confidence residues first, and trimmed regions are capped, never rebuilt |
| unrecorded breaks | a chain break with no SEQRES behind it (ESMFold output, trimmed models) is capped, not bonded across |
| left out on purpose | `--no-rebuild A:5-8,12` (repeatable, one chain each) marks residues the caller removed or did not select; a gap they fall in is capped, never rebuilt from SEQRES |
| protonation by hand | `--state A:57=HIE` (repeatable) wins over PROPKA and the metal rules; groups within 1 pH unit, and groups shifted ≥ 2 units from their model pKa, are listed |
| heterogens on purpose | `--drop-additives` removes glycerol, sulfate, PEG, buffer and the like; metals and cofactors stay |
| electrostatics | `--pqr out.pqr`: Amber14 ff14SB charges and Rmin/2 radii, the same force field as the refinement; lone ions included, unparametrised ligands left out and named |
| provenance in the file | `REMARK 99` lines record pH, protonation model, and everything built, capped or trimmed; deposited atoms keep occupancy and B-factor (pLDDT on predicted models); built atoms are at occupancy 0.00 |

Every change is reported: altlocs resolved, waters deleted, gaps built, capped
or left, atoms rebuilt, non-default protonation states, flips, groups whose
PROPKA pKa is within one unit of the pH, and how far the refinement moved
things. `--report prep.json` writes the full record; `--help` lists the knobs
(`--ph`, `--drop LIG`, `--keep-water`, `--max-loop`, `--tether`, `--no-minimize`,
…).

Things PROPKA gets wrong that are fixed afterwards:

- **Metal ligands.** PDB2PQR does not see metals. It will flip a zinc-bound His
  ring and leave a carbon on the ion, and it protonates the nitrogen that
  coordinates. Flips on metal ligands are undone from the deposited
  coordinates; the coordinating His nitrogen loses its hydrogen, Cys becomes
  thiolate, Asp/Glu carboxylate. On 1CA2 this gives His94/96 HID and His119 HIE,
  as the zinc site requires.
- **Caps.** PDB2PQR has no hydrogen rules for ACE/NME. It is given the chain
  without them — across the gap it keeps both flanking residues as chain
  interior, which is the capped state — and OpenMM protonates the caps after,
  with every other residue's state pinned.

Known limits:

- Ligands and cofactors are not protonated or parametrised. During refinement
  they are rigid, uncharged steric walls; lone metal ions keep their formal
  charge, and residues on a metal are held fixed.
- Rebuilt residues have no experimental coordinates and PDBFixer's builds are
  not reproducible run to run. They sometimes come out as D; those side chains
  are mirrored to L, and bent backbone is straightened with bonded terms and a
  soft repulsion before the main refinement.
- Output is PDB, or mmCIF with a `.cif` name. The `REMARK 99` provenance header
  is PDB-only; `--report` writes the same record (and more) as JSON either way.

## Tests

```sh
~/.pyenv/versions/quickprep/bin/pip install -e '/home/sinas/projects/quickprep[test]'
~/.pyenv/versions/quickprep/bin/python -m pytest /home/sinas/projects/quickprep/tests
```

The test runs 1CA2 end to end: the zinc-site His tautomers, provenance in the
output, integral PQR charges, and how far refinement moves the deposited atoms.
A second test deletes three residues from 1HSG and checks that SEQRES rebuilds
them unless they are passed as `no_rebuild`, when the gap is capped.

## Used by

ProteinViewer's **Prepare** button runs `python -m quickprep` in this
environment on what is selected in its Receptor panel, passing `--no-rebuild`
for residues removed there or left out of the selection.
