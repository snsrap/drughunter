# ProteinViewer

A browser GUI for inspecting generated ligands against a receptor pocket: a 3D
stage with a per-molecule inspector, ensemble and centroid-cloud views, and
endpoint filters.

This is the **PaIspD Pocket Bench** viewer, copied here verbatim from the
published artifact so it can be developed as a standalone tool. It currently
ships with the NEAT-POCKET validation run baked in (9GC8 chain A, six
generation arms, ~5 800 molecules).

## Run

```sh
./serve.py
```

Or `./start.sh`, which takes the same arguments but first stops a viewer
already running on that port, so it doubles as a restart.

Serves `app/` on <http://127.0.0.1:8000/> and opens a browser. A real HTTP
origin is required — the page loads its data with dynamic `<script>` tags, so
`file://` will not work. 3Dmol.js and the fonts come from CDNs, so the first
load needs network access.

## Fetching other structures

The **Fetch from PDB** box at the top of the control rail takes a four-character
PDB ID (e.g. `1HSG`), downloads the entry from RCSB (`.pdb`, falling back to
`.cif` for large entries with no legacy file) plus its title, method and
resolution, and adds it as a `PDB <ID>` option under **Receptor**. It is drawn
as a cartoon with hetero groups as sticks and waters hidden.

The fetched entry is shown in its own deposited frame — nothing is superposed
onto 9GC8 — so the generated ligands, the A1IJS fragment and the
pocket-relative readouts stay in the 9GC8 frame. Residue labels and the pocket
surface are skipped for fetched entries. Fetching `9GC8` itself lands in the
same frame and highlights the pocket residues on chain A.

## Finding pockets

**Find pockets** runs [fpocket](https://github.com/Discngine/fpocket) on the
receptor currently on screen and lights up the residues of whichever pocket you
pick. It needs the fpocket binary on `PATH` (or `FPOCKET_BIN` set); `serve.py`
prints whether it found one at startup, and the panel says so if it did not.

fpocket is a command line program, so the browser cannot run it. The structure
being displayed is posted to the server once and buffered in a temp directory;
each pocket run reads it from that copy. The buffer holds the last 12
structures and is deleted when the server exits.

Pockets are turned into residues the way NEAT-POCKET's own `cut_pocket` does —
every whole standard amino-acid residue with an atom within 6 Å — except that
fpocket's alpha-sphere centres stand in for the ligand that would normally
define the site. On 3R8G this reproduces `cut_pocket` exactly, residue for
residue, at the same protonation state.

Two details that are easy to get wrong, both handled in `pockets.py`:

- fpocket **silently ignores `HETATM`**. A cofactor left as hetero is invisible
  to it, so its volume is detected as empty cavity and merged into the
  neighbouring pocket. Every receptor atom is written as `ATOM` before the run.
- A pocket can **span chains**, so residues are returned grouped by chain and
  selected with 3Dmol's `{or:[…]}`. A flat list of residue numbers would light
  that number up on every chain.

Residue numbering follows the author numbering (`auth_seq_id`), which is what
3Dmol displays, so the highlight lands where the labels say it should.

### What you see

The **Pockets** panel sits directly under **Receptor** and runs on whichever
receptor is selected there. After a run it shows:

- one row per pocket — residues, chains, alpha-sphere count, fpocket score, and
  volume as the tag — in fpocket's own ranking;
- a line naming what was executed: receptor atom count after preparation, how
  many pockets came back, how long it took, and the literal command;
- an **Alpha spheres** toggle that draws the selected pockets' alpha-sphere
  centres, plus each centroid. The highlighted residues are exactly those within
  6 Å of those points, so the toggle shows why the selection is what it is.

Rows are multi-select: tick as many pockets as you want. Each gets its own
colour, reused for the molecules generated for it. Ticking a row highlights its
residues and zooms to the selection.

### Receptors in another frame

The generated molecules and the A1IJS fragment are in the 9GC8 frame. A fetched
entry is drawn in its own deposited frame, so against that receptor they are not
a pose of anything — they would hang in unrelated space. They are therefore
hidden whenever the receptor is not in the 9GC8 frame, the ensemble controls
(Arm, Pose source, Filter, Molecule, Placement) are dimmed, and a banner says
so. Switching the receptor back restores them.

Known behaviour: a **bound ligand is not removed**, so on a holo entry it fills
its own site and fpocket ranks that site lower or misses it. `POST /api/pockets`
accepts `{"ligand": ["MK1"]}` to delete residues by name first; the GUI does not
expose it yet.

## Generating binders

Tick one or more pockets, set **Molecules per pocket**, and press **Generate
binders**. Results appear in a **Generated** section, one group per pocket,
drawn in that pocket's colour; each group can be shown or hidden, and a slider
caps how many are drawn at once.

NEAT-POCKET needs torch and its own environment, so `serve.py` does not import
it — it runs `NEAT-POCKET/scripts/generate_from_pocket.py` as a subprocess.
Locations are discovered and can be overridden with `NEAT_POCKET_DIR` and
`NEAT_PYTHON`; `serve.py` prints what it found at startup. A run takes tens of
seconds per pocket, so it is a background job the page polls.

### No reference ligand is needed

`generation_from_pdb.py` requires a bound ligand: it cuts the pocket around it,
and the ligand's centre of mass sets the model's input frame. That is the wrong
shape for a pocket that was found rather than observed, so
`generate_from_pocket.py` takes the residue selection directly and builds the
pocket tensors itself.

No stand-in ligand is required, and none is fabricated. During sampling the
system is recentred on the growing ligand's COM at every step, and
`NEAT.generate` returns coordinates relative to the *pocket* COM (`neat.py`,
steps 4.11 and 5), so any constant offset applied to `pocket_pos` cancels.
Only the pocket's own geometry matters; output is mapped back by adding the
pocket centroid in the target frame.

Checked on 3R8G: generated molecules land 0.8–2.6 Å from the crystallographic
ibuprofen's centroid, with closest atoms 0.2–0.5 Å from its atoms.

Two things are done differently from `dataset_spindr`, on purpose:

- residues are keyed on `(chain, res_id)`. Keying on `res_id` alone merges
  residues sharing a number across chains into one embedding — on the 1HVR
  pocket that collapses 41 residues into 21.
- `pocket_batch` is built explicitly rather than inferred from where the residue
  id decreases, which reads a two-chain pocket as two samples.

### The molecule table

Beneath the 3D stage, every generated molecule gets a row: a 2D depiction, the
pocket it came from (with that pocket's colour), formula, MW, heavy-atom count,
logP, TPSA, QED, rings, rotatable bonds and SMILES. Any column header sorts;
clicking a row selects that molecule, thickens it in the 3D view and centres on
it. **Visible groups only** ties the table to the group toggles, and **CSV**
downloads what is currently listed.

Depictions and properties are RDKit's, computed by `generate_from_pocket.py`
while it still has the molecules in hand — that avoids a second subprocess and a
second model load — and written to `molecules.json` beside the SDFs. `#000000`
is rewritten to `currentColor` in each SVG so the drawings follow the page's
text colour and stay legible in both themes; heteroatom colours are untouched.

Each row's `i` is the molecule's index in that pocket's SDF, which is how a row
maps back to the model drawn in the stage.

### Endpoints

| route | purpose |
| --- | --- |
| `GET /api/status` | whether fpocket was found, and where |
| `POST /api/structure` | buffer `{id, fmt, data}` → `{token, nAtoms, chains}` |
| `POST /api/pockets` | `{token, cutoff, ligand}` → ranked pockets, cached per structure |
| `POST /api/generate` | `{token, ranks, n}` → starts a job, returns `{job}` |
| `GET /api/job?id=` | job progress; `&mols=1` adds the SDF per pocket, `&table=1` the rows |
| `POST /api/prep` | `{token, ph}` → `{data, report, summary}`: the prepared PDB, from the quickprep package |

`pockets.py` is also a CLI and needs nothing outside the standard library:

```sh
./pockets.py structure.cif --ligand IZP --top 5
```

## Preparing structures

`Prepare` is backed by [quickprep](../quickprep), a separate package for
QuickPrep-style structure preparation (gemmi, PDBFixer, PDB2PQR + PROPKA,
OpenMM); its README covers what it does and how. It needs OpenMM, so it lives in
its own environment and the server runs it there as `python -m quickprep`:

```sh
pyenv virtualenv 3.11.14 quickprep
~/.pyenv/versions/quickprep/bin/pip install -e ../quickprep
```

`serve.py` looks for it at `~/.pyenv/versions/quickprep/bin/python` (or
`QUICKPREP_PYTHON`) and prints the version it found at startup.

In the GUI, **Prepare** under **Receptor** runs it on the receptor on screen
(a fetched entry, or the built-in 9GC8 holo/apo chains). The result is added
as its own receptor, "<id> · prepared", beside the original, so the two can be
compared and pockets found on either. It stays in the original's frame. The
summary line under the button lists what changed; **Full report** has the rest.
Missing chain ends are left off (quickprep's default).
Hydrogens are hidden in the 3D view.

## Layout

```
app/
  index.html               the whole GUI — markup, CSS and logic, no build step
  structures.js            receptor + reference structures, and the arm registry
  mols_<arm>_<source>.js   one file per (arm, pose source), loaded on demand
pockets.py                 fpocket wrapper, structure buffer, pocket→residue cut
generate.py                NEAT-POCKET job runner (subprocess, polled)
serve.py                   static server for app/, plus the pocket endpoints
```

`index.html` has no dependencies beyond 3Dmol.js from cdnjs. Edit it directly
and reload. `pockets.py` and `serve.py` are standard library only.

## Data contract

The GUI reads three globals. Point it at a different dataset by regenerating
these files; no code changes are needed as long as the shapes hold.

**`structures.js`** defines two globals.

`window.STRUCT` — structure text, parsed by 3Dmol:

| key | format | what it is | read by the GUI |
| --- | --- | --- | --- |
| `pocketTrue` | PDB | the residue cut the generator was conditioned on | yes |
| `proteinHolo` | PDB | the full receptor chain | yes |
| `proteinApo` | PDB | the apo chain, pre-superposed | yes |
| `pocketDecoy` | PDB | a decoy-site cut, for the negative-control arm | yes |
| `refLigand` | SDF | the withheld crystal ligand | yes |
| `pocketApo` | PDB | the apo residue cut | no |
| `decoyProbe` | PDB | probe atoms at the decoy site | no |

`window.INDEX`:

```js
{
  arms: {
    armA: {
      label: "A - holo pocket, cfg 0.5 (the method)",  // "<short> - <subtitle>"
      frame: "crystal",            // "crystal" | "decoy" | "origin"
      sources: {                   // omit a key when no file exists for it
        inplace: { file: "mols_armA_inplace.js", n: 991, kb: 621 },
        docked:  { file: "mols_armA_docked.js",  n: 991, kb: 621 }
      },
      hasMetrics: true             // false => geometry only, filters disabled
    }
  },
  com:        { pocketTrue: [x, y, z],     // pocket centroid, Angstroms
                pocketApo: [...],          // unused by the GUI
                pocketDecoy: [...] },      // unused by the GUI
  pocketResi: [13, 14, ...],               // residue numbers to highlight
  labels:     { "Ser89": [x, y, z] },      // contact-residue label anchors
  contactRes: [...]                        // unused by the GUI
}
```

Only `com.pocketTrue`, `pocketResi` and `labels` are read. `index.html` also
hardcodes a `CORE_RESI` list, the subset it zooms to on "Fit pocket".

`frame` drives the stage banner: `origin` warns that the arm is not in the
crystal frame and offers a rigid translation onto the pocket centroid, labelled
as a viewing aid rather than a pose; `decoy` notes the offset site.

**`mols_<arm>_<source>.js`** assigns `window.MOLDATA['<arm>:<source>']`:

```js
{
  arm: "armA", source: "inplace", n: 991,
  mols: [{
    i: 0,                       // index, aligned across sources and metrics
    s: ["N", "C", ...],         // element symbols, heavy atoms only
    c: [-2594, 2466, 1499,...], // xyz flattened, Angstroms x 100 as integers
    b: [0, 1, 1, ...],          // bonds flattened: atom_i, atom_j, order
    m: { ... }                  // metrics, or absent when hasMetrics is false
  }]
}
```

Coordinates are integers scaled by 100 to keep the files small; the GUI divides
by 100 on read. The metrics object `m` carries `smi`, `nha`, `mw`, `qed`, `sa`,
`logp`, `pb`, `clash`, `mind`, `rec`, `tan`, `ser89`, `bits`, `ip`, `dock` and
`dcom`; `ip`, `dock`, `dcom` and `mind` are nullable and render as an em dash.
The endpoint shown throughout is `pb && clash === 0 && rec >= 0.5`.

## Caveats carried over from the source dataset

Documented in the page itself, and true of the shipped data rather than of the
GUI:

- No docked poses exist for arms D1 or E — `armD1_docked.sdf` and
  `armE_docked.sdf` were never written, so the docked source is disabled there.
- Arm E is the unconditional control and has no pocket frame to return to; its
  ensemble sits ~37 Å from the pocket, on the model origin.
- Arms D1, E and the ChEMBL control carry geometry but no per-molecule scores.

## Where this is going

The GUI and its dataset are still entangled: the masthead, the arm ledger
table, and the two caveat sections in `index.html` are written about the PaIspD
run specifically. Generalising means lifting that copy into `INDEX` (or a small
`meta` global) so a new dataset can supply its own.
