# ProteinViewer

A browser-based protein structure viewer. Fetch entries from the PDB — several
at once if you like — or open structures of your own from disk, browse their
chains, residues, ligands and waters in a structure tree, select any part of
one, and prepare it, find pockets in it, or generate binders for the pockets
you pick. A session saves to a file and opens again as it was.

It began as the PaIspD Pocket Bench viewer, a browser for one NEAT-POCKET
validation run. That bundled dataset (9GC8, six generation arms, ~5 800
molecules) has been removed; it is in git history at `e38e6d2` if it is needed.

## Run

```sh
./serve.py
```

Or `./start.sh`, which takes the same arguments but first stops a viewer
already running on that port, so it doubles as a restart. `python -m
proteinviewer` is the same server.

Serves `app/` on <http://127.0.0.1:8000/> and opens a browser. Open the page
through the server, not as a file: pockets, preparation and generation all go
through its API. 3Dmol.js and the fonts come from CDNs, so the first load needs
network access.

It takes a port, `--no-open`, and `--projects DIR` for where session output
directories are collected (see *The project directory*); at startup it prints
that path, its temporary work directory, and what each tool's install can do.

## Fetching other structures

The box at the top of the control rail, under **Load a structure**, takes a
four-character PDB ID (e.g. `1HSG`), downloads the entry from RCSB (`.pdb`, falling back to
`.cif` for large entries with no legacy file) plus its title, method and
resolution, and adds it to the **Receptor** panel. It is drawn as a cartoon
with hetero groups as sticks and waters hidden.

Nothing is bundled: the viewer opens empty. Every structure you fetch is added
to the view rather than replacing what is there, each in its own cartoon colour
and its own deposited frame until **Superpose** moves it (see *Superposing
structures*). Fetching an entry that is already loaded shows it again instead
of adding a copy.

## Opening your own files

**Open file…** takes a `.pdb` or `.cif` file from disk — gzipped or not — and
adds it exactly as a fetched entry is added: a predicted model, a docked pose,
a frame out of a trajectory, a structure prepared in an earlier session.
Dropping files anywhere on the page does the same, several at once. Nothing
about the pipeline is tied to the PDB: the server takes coordinates as text
rather than an ID, so Prepare, Find pockets and Generate work the same on a
file of your own.

It is named after the file (`AF-P12345-F1-model_v4.cif` becomes
`AF-P12345-F1-model_v4`), reduced to the characters a buffer name keeps so two
files cannot be rewritten into one another's server-side copy, and its title
comes from the file's own `TITLE` records or `_struct.title`.

**Only the first model is read**, and the structure row says so (*20 models ·
first only*). That is what the server's parser reads too, so an NMR ensemble
or a trajectory would otherwise show dozens of models on screen while Prepare
and the detectors worked on one. A file with no `ATOM`/`HETATM` records, or no
`_atom_site` loop, is refused with a line saying as much rather than appearing
as an empty structure in the tree.

## Getting results out

Every result can leave the viewer, one file at a time or all at once.

| where | what it writes |
| --- | --- |
| **Download** in *Receptor* | the scoped structure as it stands — `1HSG.pdb`, `1HSG-prep.pdb`, or the selection cut from its own text as `1HSG.sel-yluki0.pdb` |
| **Download** in *Pockets* | the ticked pockets' residues, cut from the structure the same way: `1HSG.pocket1+2.pdb` |
| **SDF** under the molecule table | the generated molecules themselves — the poses, each record carrying its properties as SD tags |
| **CSV** under the molecule table | the properties of the rows listed, without coordinates |
| **Save project** | the whole session's artifacts as a directory (below) |
| **Session file** | the session's state as one JSON file, straight from the browser (below) |

The two table downloads follow the table: **Visible groups only** and the
current sort apply to both, so what you get is what you are looking at. A
structure or pocket download is cut from the original file rather than
re-written from 3Dmol's atoms, so the header records survive (see *What the
operations run on*).

## The project directory

Every run that produces a file writes it into one directory per session, and
**Save project** copies that directory wherever you ask:

```
~/work/1HSG-run1/
  manifest.json                     every artifact, with the command that made it
  session.json                      the viewer's own state: open it here to come back
  structures/1HSG.pdb               what the runs ran on, verbatim
  structures/1HSG-prep.pdb          quickprep's output
  structures/1HSG-prep.report.json  and its report, plus .report.txt
  pockets/1HSG.fpocket.json         the pockets as the API returned them, with the meta
  pockets/1HSG.fpocket.residues.txt the same, as text to read or grep
  pockets/1HSG.fpocket.spheres.pdb  the site points as STP pseudo-atoms, radius in the B-factor
  molecules/<job>/pocket-1.sdf      the generated poses, per pocket
  molecules/<job>/molecules.json    properties and 2D depictions, RDKit's
  molecules/<job>/molecules.csv     the same properties as a table
```

The live directory is made on demand — a session that only looks at structures
never leaves one behind — and lives under
`~/.local/share/proteinviewer/projects/<date>-<structure>`, or wherever
`--projects DIR` (`PV_PROJECT_DIR`) says. It is not temporary: results outlive
the job directories, which are pruned to the last 32, and the work tree, which
goes when the server stops. `serve.py` prints both paths at startup.

**Save a copy to** takes any path you type, with four guards: `~` expands, the
parent must already exist (a typo should not create a tree of directories), the
server's own work tree is refused, and a directory with something else in it is
never written into — the copy goes to `1HSG-run1-2` instead, and the panel says
so. Nothing is ever deleted.

Reopening a saved `session.json` picks the same project directory back up, so a
session that is stopped and resumed goes on filling one directory rather than
scattering several. If that directory is not on this server — another machine,
or a projects root that has moved — the page says so and starts a new one with
the next run.

## Saving a session

**Session file** writes everything on screen to one JSON file: each structure's
own text and what was done to it (removals, preparation, what is folded, hidden
and selected), the live pocket run with its residues and alpha spheres, and the
generated molecules with their depictions and properties. Opening that file —
through **Open file…** or by dropping it — puts it all back. The same state is
written into a saved project as `session.json`, so a project directory is both
the files other programs read and the session this page reopens.

The file holds the coordinates themselves, so a restored session needs nothing
from the server: pockets stay listed and drawn, and the molecule table is as it
was. Two things do not come back. The undo history is not in the file, since
putting back atoms this session never had would be a lie. And generating *more*
molecules needs the server's buffered copy of the structure, which went when
the server did — so a restored run says `restored from a session`, and
**Generate binders** asks for **Find pockets** to be pressed again first. That
is cheap: the structure and the selection are unchanged, so the same run
usually comes straight back out of the server's cache.

A session is a document like any other, which means one can arrive from
somewhere else. Opening it coerces every value to the type the page expects,
and cuts the three fields the page renders as markup — the preparation line,
the generation line and the molecule depictions — back to what the page itself
writes: `<b>` and a plain `<svg>` drawing. Anything else is escaped or
dropped, so a session cannot run script against the viewer's own API.

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
two structures is refused, since there is no single structure to run on.

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

## Superposing structures

With two or more structures loaded, the **Superpose** panel under *Receptor*
moves them into one frame. Choose the reference under **Onto** — it stays where
it is — and tick the structures to move; every structure not already in the
reference's frame starts ticked. Each is fitted on its own by
[pocketbench](../pocketbench), which the server runs (see *Finding pockets* for
installing it), the way PyMOL's `align` does it:

- **Pairing.** Each structure's protein chains are read as sequences and
  aligned to the reference's chains: BLOSUM62, a gap costing 10 to open and 0.5
  per residue more, end gaps free, so a longer construct or a missing tail costs
  nothing. Chains pair up best alignment first, so chain ids and their order do
  not matter, and a chain that aligns less than half as well per residue as the
  best pair is left out rather than forced onto an unrelated one. Every pair of
  aligned residues gives a pair of Cα atoms.
- **Fitting.** The rotation and translation with the least RMSD over those
  pairs (Horn's quaternion method: always a rotation, never a mirror image),
  refined up to five times by dropping the pairs more than twice the RMSD
  apart, so a loop that moved or a domain that hinged does not pull the core
  out of register.
- **Moving.** The transform is written into the structure's own text: the
  coordinates and ANISOU tensors of a PDB file, `Cartn_x/y/z` and the
  `_atom_site_anisotrop` tensors of an mmCIF. Every other record is kept
  verbatim, so CRYST1, SCALE and the assembly records still describe the
  deposited frame, as in a file PyMOL saves after `align`.

The panel says what each fit rests on — *RMSD 0.40 Å over 182 of 198 Cα, 98 %
identical* — and the row of every structure in the reference's frame keeps its
numbers. A fit of two unrelated proteins still returns an RMSD, so the identity
is checked against Rost's twilight-zone curve, which asks for more identity the
shorter the alignment (about 47 % over 40 residues, 29 % over 100, 20 % from 500
on); below it the panel says the residue pairing may be chance.

**A selection narrows the fit.** Picks in the reference — a chain, the residues
lining a site — fit on those residues only, and picks in a structure being
moved restrict its side the same way. The alignment still runs over the whole
chains, so which residue pairs with which does not change.

**Superposing is an edit**, like a removal: what is drawn, downloaded, prepared
and searched for pockets from then on is the superposed structure, and its row
says *superposed*. **Undo** puts every structure moved together back where it
was. Pocket results on a structure that moves are cleared, since they were
found in its old frame; the panel says so before the button is pressed, and
the reference defaults to the structure the live pocket run is on so that it
does not happen by accident.

Frames are tracked, not assumed. A prepared copy starts in its original's
frame; a structure superposed onto one that later moves is no longer counted as
in its frame; the overlay says *3 structures in 1HSG's frame*, or how many
frames there are. A session file keeps each structure's frame and its last fit.
Only protein Cα atoms are fitted, so a structure with no protein chain cannot be
superposed, and the panel says so.

## Finding pockets

**Find pockets** runs [fpocket](https://github.com/Discngine/fpocket) on the
current selection (see *What the operations run on*) and lights up the residues
of whichever pocket you pick.

Detection is [pocketbench](../pocketbench)'s, a separate package that drives
the detectors, turns what they find into residues, and also does the
superposing above. The server runs it in its own environment as `python -m
pocketbench`, as it runs quickprep:

```sh
pyenv virtualenv 3.11.14 pocketbench
~/.pyenv/versions/pocketbench/bin/pip install -e ../pocketbench
```

`serve.py` looks for it at `~/.pyenv/versions/pocketbench/bin/python` (or
`POCKETBENCH_PYTHON`). fpocket itself needs its binary on `PATH` (or
`FPOCKET_BIN` set, which is passed on to pocketbench). `serve.py` prints what
each tool's install can do at startup, and the panel says so when something is
missing.

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

Two details that are easy to get wrong, both handled in pocketbench
(`pocketbench/detectors/__init__.py` and `pocketbench/pockets.py`):

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

A bound ligand fills its own site, so a detector reads that volume as wall and
ranks the site low or misses it altogether. When the structure (or the selected
part of it) has one, the panel offers **Delete MK1 first**, naming what it
would delete; ticking it sends `{"ligand": ["MK1"]}` with the run and the
residues go server-side before the detector sees the file. The run line then
says `ligand MK1 deleted`, and the atom count drops with it.

The names come from the structure tree, which has already decided what is a
ligand and what is a modified residue of a chain, minus the solvent and
crystallisation additives every detector drops on its own (pocketbench's
`ADDITIVES`, which the page reads from `/api/status` rather than keeping a copy
of). It is off by default — deleting part of the structure is the user's
decision, not the viewer's — and it is part of a run's identity, so the results
on screen keep saying which ligands they were found without.

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
| `GET /api/status` | `{api, detectors: {fpocket, lacuna}, additives, superpose, prep, neat, projects}`: what each tool's install can do, what every detector drops on its own, and where projects go |
| `POST /api/structure` | buffer `{id, fmt, data}` → `{token, fmt, path}`; refused if the text holds no atom records |
| `POST /api/superpose` | `{token, reference, on, mobileOn}` → `{data, fit}`: the first structure moved onto the second, in its own format; `on` and `mobileOn` are the residues to fit on, as `[chain, number, insertion code]` |
| `POST /api/pockets` | `{token, method, cutoff, ligand, …}` → ranked pockets, cached per structure; lacuna also takes `backend`, `detector`, `conformers` |
| `POST /api/generate` | `{token, ranks, n}` → starts a job, returns `{job}` |
| `GET /api/job?id=` | job progress; `&mols=1` adds the SDF per pocket, `&table=1` the rows |
| `POST /api/prep` | `{token, ph, noRebuild}` → `{data, report, summary}`: the prepared PDB, from the quickprep package |
| `POST /api/project` | `{name}` → `{id, path, files, bytes}`: start this session's output directory |
| `GET /api/project?id=` | what is in it now |
| `POST /api/project/save` | `{project, path, session, overwrite}` → `{path, files, bytes}`: write `session.json` into it, then copy it to `path` |

`/api/pockets`, `/api/prep` and `/api/generate` each also take `project`: the
id of the directory that run's artifacts are written into. It is optional —
without it a run leaves nothing on disk but the server's own scratch files.

Errors come back as `{"error": "…"}` with a status that says whose problem it
is: 400 a bad request (413 too large), 404 an unknown structure, job or route,
409 a request out of order (generation before any pocket run), 503 a tool that
is not installed, 502 a tool that ran and failed, 500 a bug in the server.
`api` goes up when a response changes shape; the page compares it with its own
and asks for a restart when the running server is older.

The API answers only the page itself. It writes into a temp tree on behalf of
whoever asks, so a page on another site must not be able to ask: POSTs must be
`application/json` (which a cross-site form or "simple" request cannot send
without the browser asking first), must not name another `Origin`, and the
`Host` must be a loopback name, which defeats DNS rebinding.

Detection and superposition are pocketbench's own commands too:

```sh
pocketbench detect fpocket structure.cif --ligand IZP --top 5
pocketbench detect lacuna structure.cif --backend nma --conformers 20
pocketbench superpose mobile.pdb reference.pdb -o moved.pdb
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
serve.py                     entry point (start.sh looks for it): runs proteinviewer.server
proteinviewer/               the server: Python standard library only
  errors.py  validate.py       what can go wrong, and checking request values
  config.py                    every setting and environment variable, in one place
  process.py                   subprocess plumbing shared by the tool adapters
  pocketbench.py               adapter: detection and superposition (../pocketbench)
  prep.py  generation.py       adapters: quickprep and NEAT-POCKET
  buffer.py                    structures held on disk, per-structure locks, LRU eviction
  project.py  pockets.py       a session's artifacts on disk, and pocket results as files
  schemas.py  service.py       parsed requests, and the use cases over them
  web.py                       HTTP: routing, status codes, the cross-site checks
  server.py                    composition root: picks the tools, wires, serves, cleans up
app/                         the page: no build step, no install
  index.html                   markup only
  css/tokens.css  css/app.css  theme colours, then components and layout
  js/main.js                   boot: registers the views, binds the features, probes the server
  js/core/                     state store, API client, RCSB fetch, files in and out,
                                 colours, DOM helpers
  js/model/                    pure: tree building, cutting a selection out of its file,
                                 scope, reading a structure file, the session snapshot
  js/scene.js                  the 3D stage (3Dmol), drawn in keyed layers
  js/features/                 one module per panel: its view, its controls, its actions
tests/                       python -m unittest; tests/js via npm test; tests/e2e walkthrough
```

### How the pieces fit

**Server.** Each layer imports only from the ones below it, and
`tests/test_architecture.py` fails the build when one does not: the adapters
know their tool and nothing of HTTP; the stores (`buffer`, `project`) know the
filesystem and neither of those; `service.py` holds the use cases and sees
tools only through small protocols; `web.py` turns requests into calls on the
service and errors into statuses; `server.py` is the one place that names
concrete tools.

The server holds no science of its own. Reading structures, the pocket model,
the detectors and superposition are [pocketbench](../pocketbench); preparation
is quickprep; generation is NEAT-POCKET. Each is a package in its own
environment, run as a command, so the server never parses a structure: it
buffers text, passes it on, and keeps what comes back. What used to be the
domain layer here left as pocketbench, as quickprep had before it.

Requests on different structures run in parallel. Each buffered structure has
one lock per kind of work, so two pocket runs on the same structure take turns
(they share its scratch directory) while a preparation on it does not wait.
Each generation job runs in its own directory with its own copy of the
structure, so evicting a structure cannot pull files out from under a job, and
only the last 32 finished jobs are kept.

**Page.** All state lives in one object (`js/core/state.js`). A feature's action
changes it and calls `commit()`, and every view re-renders from it in a fixed
order, so no action decides what else needs repainting. Views skip DOM work
when their markup has not changed, which keeps keyboard focus where it was; the
molecule table rebuilds only when its rows change, and the 3D scene touches
only the layers whose inputs changed.

### Adding a pocket detector

Detectors live in pocketbench: subclass `PocketDetector` in
`pocketbench/detectors/<tool>.py` and add it to `default_detectors` (its README
says what to give it). `pocketbench detectors --json` then lists it with its
options, and the server passes both on, so status, option checking, caching and
the API need no change here. In the page, add an `<option>` to the detector
menu and, if it reports new numbers, show them in `js/features/pockets.js`.

### Tests

```sh
python3 -m unittest discover -s tests -t .   # server: about a second, no tools needed
npm test                                     # the page's pure modules, Node 18+
npm install && npm run e2e -- http://127.0.0.1:8000 /tmp/pv-ui --slow   # the whole UI
```

The server tests use fake tools, so they pass with nothing installed; real
fpocket and lacuna are exercised in pocketbench's own tests. The end-to-end walkthrough drives a
real browser through every panel and writes what each one said, plus a
screenshot of the stage, at every step. Run it against the old and the new
server and diff the two to check that a refactor kept behaviour.
