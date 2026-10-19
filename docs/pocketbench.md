# pocketbench

Find pockets in protein structures, superpose structures, and score pocket
detectors against sites known from experiment.

Built for apo/holo recovery testing. A detector runs on the **apo** structure,
the ligand never present. The **holo** structure is superposed onto it, so its
ligand lands where the pocket should open. The detector's pockets are then
scored against that site.

ProteinViewer, beside it in drughunter, does its pocket detection and its
superposition through this package. Its server runs `python -m pocketbench …
--json` and never imports it (see *Driven by a program*).

Standard library only, Python 3.10+. The detectors are external: fpocket is a
binary, lacuna lives in its own environment.

## Install

pocketbench is part of drughunter: installing drughunter (the
[README](../README.md) says how) puts `pocketbench` in the environment's
`bin/`; `python -m pocketbench` is the same thing.

## Running an apo/holo case

TEM-1 β-lactamase: apo `1BTL`, holo `1PZO`, ligand `CBT`, with lacuna as the
detector (its own environment, here `~/.pyenv/versions/lacuna`).

```sh
W=/tmp/apoholo && mkdir -p $W/raw $W/work
for id in 1BTL 1PZO; do curl -sfL -o $W/raw/$id.pdb https://files.rcsb.org/download/$id.pdb; done

# 1. the detector's input: chain A, protein only
pocketbench apo $W/raw/1BTL.pdb -o $W/work/1BTL_protein.pdb --chain A

# 2. detect on the apo structure
cd $W/work && ~/.pyenv/versions/lacuna/bin/lacuna discover 1BTL_protein.pdb \
    --backend nma --detector alpha --conformers 20 --top 10

# 3. score the pockets against the holo ligand
pocketbench score 1BTL_protein_lacuna/pocket_report.json 1BTL_protein.pdb $W/raw/1PZO.pdb CBT
```

```
superposed 1PZO.pdb onto 1BTL_protein.pdb: RMSD 0.54 Å over 245 of 263 Cα, 99 % identical (chains A→A)
10 pockets from pocket_report.json (lacuna)

CBT A:300: 13 shell residues within 5.0 Å
rank  ovlp  cover  jacc    DCC    DCA persist   crypt    drug  meets
   1     4    31%  0.16   11.6    7.6     19%    0.54    0.49
   2     8    62%  0.40    6.3    2.4      5%    0.50    0.50  coverage dca
   ...
  best rank — coverage ≥ 50 %: 2 · DCC ≤ 5.0 Å: none · DCA ≤ 4.0 Å: 2
```

`pocketbench site APO HOLO LIGAND` prints the site alone: each copy of the
ligand, its centroid in the apo frame, and its residue shell in apo numbering.

This replaces `docs/validation/prep_and_truth.py` and `score.py` in
ProteinViewer. On TEM-1 it gives the same overlap, coverage and Jaccard for
every pocket and both CBT copies, and the same best ranks. The centroid
distances differ by 0.1–0.2 Å, because the fit drops outliers (see below)
where the old script fitted every Cα.

## Commands

| command | does |
| --- | --- |
| `detectors` | what this installation can run, each detector's options, and the additives every detector drops |
| `detect METHOD STRUCTURE` | find pockets with `fpocket` or `lacuna`, each resolved to whole residues |
| `superpose MOBILE REFERENCE [-o OUT]` | fit one structure onto another; `-o` writes the moved structure in its own format |
| `apo APO -o OUT [--chain A]` | a detector's input: standard amino acids, no hydrogens, the chains named. From a PDB file the lines are copied as they are |
| `site APO HOLO LIGAND` | the holo ligand's site, carried into the apo frame and numbering |
| `score POCKETS APO HOLO LIGAND` | each pocket's overlap, coverage, Jaccard, DCC and DCA, and the best rank per criterion |

All of them take `--json`. `superpose`, `site` and `score` take `--on SPEC`
to fit only on some residues of the reference (the apo structure), e.g.
`--on A:1-29,A:39-58,A:77-169` for KRAS without its switches; `superpose` also
takes `--mobile-on`. A spec names a chain (`A`), a residue (`A:52`, `A:52A`) or
a range (`A:10-50`, inserted residues included). `score` takes `--cutoff` for
the shell radius and `--coverage`, `--dcc`, `--dca` for the hit thresholds.

`detect` takes `--cutoff` (residues within this of a site point, default 6 Å),
`--ligand MK1,HEM` (residues to delete first), `--workdir` (keep the tool's
files), and each detector's own options as flags (`--backend`, `--detector`,
`--conformers` for lacuna). `detectors` and `detect` take `--fpocket-bin` and
`--lacuna-bin`; otherwise `FPOCKET_BIN`, `LACUNA_BIN`, then where each tool
usually lives.

## Detection

`detect` writes the structure as a receptor and runs the tool on it:

- **What the detector sees.** Solvent, crystallisation additives (`ADDITIVES`),
  hydrogens and any residue named with `--ligand` are dropped. Every remaining
  atom is written as `ATOM`, because fpocket silently ignores `HETATM`: a
  cofactor left as hetero is detected as empty cavity and merged into the
  pocket beside it.
- **What a pocket is.** The detector's site points (fpocket's alpha-sphere
  centres, lacuna's pocket pseudoatoms) stand in for a ligand. The pocket is
  every whole standard residue with an atom within the cutoff of any point:
  NEAT-POCKET's `cut_pocket`, driven by site points instead of a bound ligand.
  On 3R8G this reproduces `cut_pocket` residue for residue. Residues are kept
  per chain, since a pocket can span chains.
- **lacuna** detects over a conformational ensemble it generates (`nma`
  normal modes, or `openmm` MD), and reports each pocket's persistence across
  the ensemble and its crypticity. The MD backend is not reproducible from
  run to run; see `detectors/lacuna.py`.

With `--json` a run is `{tool, pockets, meta}`. Each pocket carries its rank,
scores, centroid, residues as `sel: [{chain, resi}]`, and site points.

## Driven by a program

ProteinViewer's server treats pocketbench as a tool it runs, not a library it imports:

- `detectors --json`, asked once at startup, says what is installed and which
  options each detector takes (`{default, choices}` or `{default, integer,
  min, max}`). The server checks a request against these before starting a run.
- `detect … --json` and `superpose … --json` print their result on stdout.
- **The exit code says whose problem a failure is:** 2 a bad input, 3 a tool
  that is not installed, 4 a tool that ran and failed. With `--json` the error
  is printed as `{"error", "kind"}`. The server maps these to 400, 503 and 502.

## Adding a detector

Subclass `PocketDetector` in `detectors/<tool>.py`. Give it:
- a `name` and its `OPTIONS` (`Option` objects);
- `available`, `describe()` and `status_line()`;
- a `run()` that returns a `DetectionResult` whose pockets went through
  `select_residues`; `write_receptor()` prepares the input.

Add an instance to `default_detectors()`. `detect` takes it by name and
`detectors` lists it. ProteinViewer passes both on, and needs only an
`<option>` in its page's detector menu.

`POCKETS` is either lacuna's `pocket_report.json` or the pockets file
ProteinViewer writes into a project (`pockets/<structure>.<tool>.json`, from
any detector). Distances from the two compare directly; overlaps less so,
since lacuna lists its own lining residues and the viewer takes every whole
residue within 6 Å of the site points.

## Method

**Superposition** (`superpose.py`), as PyMOL's `align` does it:

- **Pairing.** Each protein chain is read as a sequence and aligned to the
  reference's chains: BLOSUM62, a gap costing 10 to open and 0.5 to extend,
  and end gaps free (`align.py`). Chains pair up best alignment first, so
  chain ids and their order do not matter. A chain that aligns less than half
  as well per residue as the best pair is left out. The aligned residues give
  the Cα pairs, and also the residue mapping between the two structures.
- **Fitting.** The rotation and translation with the least RMSD over the
  pairs, by Horn's quaternion method, so never a mirror image. It is refined
  up to five times by dropping pairs more than twice the RMSD apart, so a
  loop that moved does not pull the core out of register. For a cryptic site
  that loop is usually the point.
- **Relatedness.** Sequence identity is checked against Rost's twilight-zone
  curve (Protein Eng 12:85, 1999). The output says when the identity is too
  low to show the two are related, since a fit of unrelated proteins still
  returns an RMSD.
- **Moving** (`move.py`). The transform is written into the structure's own
  text: coordinates and anisotropic tensors, PDB or mmCIF. Every other
  record stays verbatim.

**The site** (`site.py`). The site is the protein residues within 5 Å of the
ligand in the holo structure. They are carried into the apo structure through
the alignment's residue mapping rather than by residue number, so the two
entries need not share numbering, chain ids or the exact sequence. A shell
residue the apo structure lacks (a disordered loop, say) stays in the shell
as missing. No pocket can cover it, and counting it keeps coverage honest.

**Scoring** (`score.py`). Every pocket gets residue overlap, as coverage of
the shell and as Jaccard. It also gets two distances from its centre: **DCC**
to the ligand's centre, and **DCA** to the nearest ligand atom. There are
three hit criteria, reported separately with no single verdict:

- coverage ≥ 50 % and DCC ≤ 5 Å are the local choices ProteinViewer's
  validation used so far, not a published convention;
- DCA ≤ 4 Å is the criterion of P2Rank's benchmarks (Krivák & Hoksza, J
  Cheminform 2018), which makes numbers comparable outside this project.

Read the columns, not just the flags. Coverage alone misleads when two sites
share residues. On KRAS G12C (apo `4LDJ`, holo `6OIM`, sotorasib `MOV`), the
emptied nucleotide pocket covers exactly 50 % of the sotorasib shell through
the shared P-loop, yet sits 11.9 Å from it. DCC and DCA reject it; coverage
alone counts it. Scoring the same pockets against a second site (`GDP` in the
apo entry itself) shows which pocket is which.

## As a library

```python
from pocketbench import Structure, sites, read_pockets, score, best_rank

apo, holo = Structure.read("1BTL_protein.pdb"), Structure.read("1PZO.pdb")
sup, found = sites(apo, holo, "CBT")
_, pockets = read_pockets("1BTL_protein_lacuna/pocket_report.json")
for site in found:
    scores = score(pockets, site)
    print(site.copy, best_rank(scores, "dca"))
```

## Layout

```
pocketbench/
  structure.py   reading PDB and mmCIF: first model, one altloc, author numbering;
                   the receptor a detector sees
  pockets.py     the pocket model and the residue cut
  detectors/     one PocketDetector per tool: fpocket.py, lacuna.py
  errors.py  process.py   what can go wrong, and running the tools
  align.py       BLOSUM62 sequence alignment, end gaps free
  superpose.py   chain pairing, the fit, outlier rejection, relatedness
  move.py        writing a transform into a structure's own text
  site.py        a holo ligand's site in the apo frame and numbering
  reports.py     reading pocket files: lacuna reports, detect --json, ProteinViewer projects
  score.py       overlap, DCC, DCA, and the hit criteria
  cli.py         the commands
tests/pocketbench/  pytest; synthetic structures, a stand-in fpocket, 1CRN as PDB and mmCIF
```

```sh
python -m pytest tests/pocketbench                 # about a second
PB_SLOW_TESTS=1 python -m pytest tests/pocketbench  # also runs real lacuna
```

The real fpocket is exercised whenever it is on `PATH`.
