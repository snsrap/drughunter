# ProteinViewer

A browser-based protein structure viewer. Fetch entries from the PDB — several
at once if you like — browse their chains, residues, ligands and waters in a
structure tree, select any part of one, and prepare it, find pockets in it, or
generate binders for the pockets you pick.

It began as the PaIspD Pocket Bench viewer, a browser for one NEAT-POCKET
validation run. That bundled dataset (9GC8, six generation arms, ~5 800
molecules) has been removed; it is in git history at `e38e6d2` if it is needed.

## Run

```sh
./serve.py
```

Or `./start.sh`, which takes the same arguments but first stops a viewer
already running on that port, so it doubles as a restart.

Serves `app/` on <http://127.0.0.1:8000/> and opens a browser. Open the page
through the server, not as a file: pockets, preparation and generation all go
through its API. 3Dmol.js and the fonts come from CDNs, so the first load needs
network access.

## Fetching other structures

The **Fetch from PDB** box at the top of the control rail takes a four-character
PDB ID (e.g. `1HSG`), downloads the entry from RCSB (`.pdb`, falling back to
`.cif` for large entries with no legacy file) plus its title, method and
resolution, and adds it to the **Receptor** panel. It is drawn as a cartoon
with hetero groups as sticks and waters hidden.

Nothing is bundled: the viewer opens empty. Every structure you fetch is added
to the view rather than replacing what is there, each in its own cartoon colour
and its own deposited frame — nothing is superposed. Fetching an entry that is
already loaded shows it again instead of adding a copy.

## Structure tree

The **Receptor** panel lists every loaded structure as a collapsible tree: the
entry at the root (with its colour swatch), then one folder per chain holding
its residues, then **Ligands** and **Water**. A new structure opens one level
deep; everything below starts folded. The caret, or the right and left arrow
keys, unfold and fold a row; up and down move between rows.

### Showing, hiding and removing

Every row has an eye and a ×, not only the structure: on the structure row
they are always there, below it they appear on hover or focus (an eye that is
switched off stays in view). From the keyboard, **H** does the eye and
**Delete** the × for the focused row.

- **Hiding is inherited.** Hiding a structure, a chain or a folder hides
  everything under it; those rows dim and their eye says what hides them
  ("Hidden with Chain B"). A row hidden that way waits for its parent — its own
  eye does nothing until the parent is shown again. Hiding is display only:
  Prepare and Find pockets still see hidden parts.
- **Waters start hidden**, as deposited files are full of them; the eye on
  **Water** shows them as spheres.
- **Removing is real.** × on a residue, ligand, water, chain or folder cuts
  those atoms out of the structure's own text (the same cut as a selection, see
  below) and parses it again, so Prepare, Find pockets and everything after work
  on what is left. Folding, hiding and picks carry over wherever their node still
  exists; pocket results for that structure are cleared, since they described
  the atoms as they were. The structure row then says *edited*, and fetching the
  same ID again adds a fresh copy (`1HSG-2`) instead of showing the edited one.
- **Undo** in the panel header, or Ctrl/⌘-Z in the tree, puts back the last
  removal — a part or a whole structure — up to twenty steps back.
- Chain-end caps (ACE, NME, NH2), which Prepare puts at gaps it does not build,
  are listed in their chain rather than under Ligands.

Selection works as in a file manager:

| action | effect |
| --- | --- |
| click | select that row (clicking the only selected row again clears it) |
| Ctrl/⌘-click | add the row to the selection, or drop it |
| Shift-click | select every row between the last pick and this one |
| Esc, or **Clear selection** | clear the selection |

Any row can be selected — a whole structure, a chain, a residue, a ligand, the
Ligands or Water folder, a single water. The 3D view frames the selection; a
selected chain or structure is recoloured, residues and ligands turn to orange
sticks (labelled, up to a dozen at a time), and waters show as spheres.

The tree is read from the model 3Dmol parsed, so each row selects exactly what
is drawn. A `HETATM` residue with a peptide backbone (N, CA, C) or a nucleotide
one (P, C1′) is listed in its chain as a modified residue (MSE, SEP, …) rather
than as a ligand.

### What the operations run on

**Prepare** and **Find pockets** run on the selection, and the line above the
Prepare button says exactly what that is — e.g. *1HSG · chain A, MK1 902*. With
nothing selected they run on the whole structure, if only one is loaded or only
one is shown; otherwise the panel asks you to pick one. A selection that spans
two structures is refused: they sit in unrelated frames, so there is no single
structure to run on.

A selection is cut from the original file, not re-written from 3Dmol's atoms,
so nothing the file says about those atoms is lost. From a PDB file the header
records are kept (SEQRES is what lets Prepare rebuild missing loops) along with
the selected atom lines verbatim. From an mmCIF file every category is kept and
only the `_atom_site` rows of the selected atoms remain, with their
`_atom_site_anisotrop` rows. 3Dmol numbers mmCIF atoms by position rather than
by `_atom_site.id`, so rows are matched on the coordinates 3Dmol read from them.
The cut is posted as its own structure (`<id>.sel-<hash>`), so pocket results
and server-side caching stay separate per selection.

SEQRES still lists the residues a cut lacks, and PDBFixer builds any gap of up
to ten residues from it — so residues you removed, or did not select, would come
back as built loops. Prepare therefore passes them to quickprep as
`--no-rebuild` (the polymer residues the structure had when it was loaded,
minus the ones being prepared, for each chain still present), and quickprep caps
those gaps instead; its report marks them `[left out]`. Loops that were never
in the deposited coordinates are still built as before.

Generated binders come from the pockets of the last pocket run, so they follow
the same scope.

## Finding pockets

**Find pockets** runs [fpocket](https://github.com/Discngine/fpocket) on the
current selection (see *What the operations run on*) and lights up the residues
of whichever pocket you pick. It needs the fpocket binary on `PATH` (or `FPOCKET_BIN` set); `serve.py`
prints whether it found one at startup, and the panel says so if it did not.

The detector dropdown also offers [lacuna](https://github.com/mooreneural/lacuna),
which detects over a conformational ensemble it generates rather than over the
structure as deposited — which is what it takes to see a pocket that is closed
in the unbound form. Setup, backends and measured apo/holo results are in
[docs/lacuna-integration.md](docs/lacuna-integration.md).

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

The **Pockets** panel sits directly under **Receptor** and runs on what is
selected there. One pocket run is live at a time; its results stay listed while
you go on selecting, and are drawn whenever their structure is shown. After a
run it shows:

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

### Bound ligands

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

In the GUI, **Prepare** under **Receptor** runs it on the current selection —
a whole structure or any part of one. The result is added as a new structure,
`<id>-prep`, in the original's frame; the original is hidden rather than
removed, so its eye brings it back for comparison. Selecting the prepared
structure shows the summary line of what changed under the button; **Full
report** has the rest.
Missing chain ends are left off (quickprep's default).
Hydrogens are hidden in the 3D view.

## Layout

```
app/
  index.html               the whole GUI — markup, CSS and logic, no build step
pockets.py                 fpocket wrapper, structure buffer, pocket→residue cut
lacuna.py                  lacuna wrapper: ensemble pocket detection (see docs/)
generate.py                NEAT-POCKET job runner (subprocess, polled)
serve.py                   static server for app/, plus the pocket endpoints
```

`index.html` has no dependencies beyond 3Dmol.js from cdnjs. Edit it directly
and reload. `pockets.py` and `serve.py` are standard library only.
