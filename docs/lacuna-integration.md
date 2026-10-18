# Ensemble pocket detection in ProteinViewer

How lacuna was added to the viewer, how to set up the environments it needs,
what was measured on real structures, and what is still missing.

Written 2026-10-17. Every number here was measured on this machine; where a
figure comes from lacuna's own documentation rather than a local run, it says
so.

---

## 1. Why

`Find pockets` ran fpocket on the structure as displayed. That is exactly the
configuration that cannot see a cryptic pocket: a site that is closed or
shallow in the unbound form and opens on binding leaves no concavity for a
single-snapshot detector to find. KRAS was called undruggable for thirty years
on that basis.

[lacuna](https://github.com/mooreneural/lacuna) (MIT, `pip install
lacuna-pockets`) generates a conformational ensemble from the one structure it
is given, detects pockets in every conformer, clusters them across the
ensemble and ranks the result. It adds a second detector alongside fpocket
rather than replacing it — on an ordinary open orthosteric site fpocket is
faster and no worse.

## 2. What changed

| File | Change |
|---|---|
| `lacuna.py` | New. Discovers the CLI, probes which backends work, runs `lacuna discover`, parses `pocket_report.json` into `Pocket` objects. Has its own CLI for use outside the server. |
| `serve.py` | `POST /api/pockets` accepts `method`, `backend`, `conformers`. `/api/status` reports lacuna's version, binary and usable backends. Startup banner prints them plus the OpenMM platform. |
| `pockets.py` | `Pocket` gains `persistence`, `crypticity`, `cryptic`. Numbers in `as_dict` now go out JSON-safe. |
| `app/index-compact.html` | Detector dropdown in the Pockets panel, method-aware availability and messages, persistence/crypticity on each row, provenance line names the backend. |
| `app/index.html` | **Retired and deleted.** |

`app/` held two independent pages with 66 of their ~80 functions duplicated.
`index-compact.html` was the newer of the two — it has layered drawing and
draw caching that the other lacked — so it is now the only viewer, and
`serve.py` rewrites `/` to it. `/index-compact.html` keeps working;
`/index.html` is a 404. Anything added to the Pockets panel from here needs
writing once rather than twice, which is why this was worth doing before the
panel grew a second detector.

The surviving page was later renamed from `index-compact.html` to
`index.html`: `/` and `/index.html` serve it, `/index-compact.html` is a 404,
and `serve.py` no longer rewrites `/`.

Two latent bugs were fixed on the way, both of which the new detector would
have triggered immediately:

- `json.dumps` writes a float NaN as a bare `NaN`, which `JSON.parse` rejects.
  fpocket's `_parse_info` defaults a missing field to NaN, so any entry where
  fpocket omitted a score would have broken the whole panel. Missing numbers
  are now sent as `null`.
- `p.score.toFixed(2)` in `paintPockets` threw on a null score. lacuna does
  not produce an fpocket-style score, so every lacuna row would have hit it.

### Design decisions worth knowing

**Residues come from `select_residues`, not from lacuna.** lacuna reports its
own lining residues, and they are deliberately ignored. The viewer hands
residue selections to NEAT-POCKET, which was trained on `cut_pocket` — whole
standard residues within a cutoff of the site. Using lacuna's list instead
would put a second, slightly different definition of "pocket" into the
generation path. lacuna's pocket pseudoatoms drive the same selection code
that fpocket's alpha-sphere centres do, which also means the panel's existing
sphere overlay draws them for free.

**lacuna runs outside `_LOCK`.** fpocket returns in well under a second and
runs under the lock as before. lacuna takes 2 s (nma) to 51 s (openmm), and
the server is threaded — holding the lock that long would stall every other
request, including the status poll. This follows what `/api/prep` already
does.

**Ensemble settings are part of the cache identity.** The same structure at a
different backend, detector or conformer count is a different run, not a cache
hit.

**`pocket_<i>_site.pdb` ↔ `pockets[i]`, rank = i+1.** Verified rather than
assumed: the mean of the pseudoatoms in each file equals the `centroid` in the
report entry exactly.

## 3. Environments

lacuna needs scipy and biopython; its MD backend needs OpenMM and PDBFixer. It
lives in its own environment and is run as a command. The search order is
`LACUNA_BIN`, then the micromamba env `lacuna-gpu`, then the pyenv virtualenv
`lacuna`, then `PATH`.

### CPU only (enough for the nma backend)

```sh
pyenv virtualenv 3.11.14 lacuna
~/.pyenv/versions/lacuna/bin/pip install lacuna-pockets
```

### With the MD backend on the GPU

**OpenMM from PyPI cannot use the GPU on this machine**, and the failure is
silent — it runs on the CPU instead. Two independent reasons, both verified:

1. The PyPI wheel ships `libOpenMMCPU.so`, `libOpenMMOpenCL.so` and the
   Reference platform. There is **no `libOpenMMCUDA.so` in it at all**.
2. Its OpenCL plugin loads fine but finds no device: `clGetPlatformIDs`
   returns `-1001 CL_PLATFORM_NOT_FOUND`. `/etc/OpenCL/vendors/nvidia.icd`
   points at `libnvidia-opencl.so.580` in `/usr/lib/x86_64-linux-gnu`, a
   native-Linux driver library, while WSL2 routes GPU access through
   `/usr/lib/wsl/lib`, which ships no OpenCL.

There is also a packaging bug worth knowing about independently:
`Platform.getDefaultPluginsDirectory()` in the wheel returns a stale
build-time path, `/usr/local/openmm/lib/plugins`, so none of the wheel's own
plugins load unless `OPENMM_PLUGIN_DIR` points at
`site-packages/OpenMM.libs/lib/plugins`.

The CUDA platform ships only in the conda-forge build:

```sh
micromamba create -y -n lacuna-gpu -c conda-forge python=3.11 openmm "cuda-version=12.*" pip
micromamba install -y -n lacuna-gpu -c conda-forge pdbfixer
micromamba run -n lacuna-gpu pip install lacuna-pockets
```

Two traps in that command:

- `openmm=*=*cuda*` **does not exist**. conda-forge dropped CUDA from openmm's
  build strings; CUDA arrives through the `cuda-version` metapackage.
- The default `openmm 8.6.1` build wants **CUDA 13.4**, while the WSL driver
  advertises `__cuda=12.9`, so it will not solve. Pinning `cuda-version=12.*`
  selects `cuda-nvrtc 12.9.86` and solves. Check a change like this with
  `--dry-run` before the download.

**No nvcc is needed.** OpenMM 8.6 compiles its kernels with `nvrtc`, which
arrives as a package dependency. This matters here because `/usr/bin/nvcc` is
12.0 and cannot target `sm_120` (this GPU is an RTX 5060, compute capability
12.0). `/usr/local/cuda-12.8` and `/usr/local/cuda-12.9` both do list
`compute_120` if some other tool needs a system compiler.

Confirm before trusting a run — a platform that registers but fails its first
JIT looks like success until the MD dies:

```sh
micromamba run -n lacuna-gpu python -m openmm.testInstallation
```

Expected: `3 CUDA - Successfully computed forces`, CPU-vs-CUDA median force
difference around `7.6e-07`.

The viewer's startup banner prints the platform lacuna's MD backend will
actually select, so a silent fall back to CPU is visible before anyone waits
on a run.

## 4. Backends

### `nma` — default, 2 s, deterministic

An anisotropic network model. It builds a Hessian over Cα atoms with springs
inside an 8 Å cutoff, takes one partial eigendecomposition, keeps the 10
lowest-frequency non-trivial modes, and displaces along them at amplitudes
ramping linearly to `max_rmsd = 2.0 Å`, propagating Cα displacements out to
all atoms.

**There is no MD here.** No force field, no solvent, no integrator, no
timestep — grep the backend for any of those terms and it returns nothing. The
ensemble is a deterministic sweep of mode amplitude, not a trajectory: emitted
Cα RMSD against the input climbs monotonically 0.00 → 1.29 Å across the 21
models, and "conformer 17" is not a later point in time than conformer 5
because there is no time axis. It reproduces exactly between runs.

### `openmm` — 50 s on CUDA, **not reproducible**

Amber14 with the GBn2 implicit solvent, 310 K, 10 ps equilibration then 50 ps
production at a 2 fs timestep, snapshots every 2.5 ps. It seeds velocities,
but GPU force summation is not bit-identical between runs and 50 ps of chaotic
dynamics amplifies the difference — see §5. It also **drops HETATM**, so the
ligand never enters the simulation.

The CLI exposes no temperature or duration flag. Changing either means driving
`OpenMMBackend` through its Python API and feeding the frames back via
`--ensemble PATH`, which takes a multi-model PDB or a directory. That seam is
the clean way to attach a real MD protocol (explicit solvent, cosolvents,
enhanced sampling) later: lacuna's detection and ranking stages are reused
unchanged.

### Detectors

`alpha` is the default and is what was used for every number below.
`surface-fusion` is what lacuna's documentation recommends, reporting
CryptoBench coverage rising 68.5% → 86.4% and top-five recovery 57.1% → 73.9%
— but that figure assumes the ESM-2 sequence pass, which needs the `plm`
extra. Run geometry-only with `--no-sequence`, as it was here, it was **worse
on both local targets** (TEM-1 rank 9 vs rank 2; PTP1B rank 9 vs rank 3).
lacuna's own docs are consistent with this: geometry alone keeps a little
under half the gain. Install the `plm` extra before revisiting the default.

## 5. Validation

Two apo/holo pairs, both real structures with published allosteric sites. The
apo entry is prepared and detected on with the ligand never present; ground
truth is the holo ligand's 5 Å residue shell, compared by author numbering
(shared between each pair, so no superposition is needed for residue overlap).
Scripts are in `docs/validation/`.

**All PDB IDs here were checked against RCSB.** One correction worth carrying:
the commonly cited PTP1B apo partner **`1T48` is not apo** — it contains
ligand `BB3` and is a second holo form from the same paper. Use `1SUG`. For
KRAS, use `4LDJ` (G12C·GDP, 1.15 Å) rather than the usual `4OBE`, so apo and
holo share the G12C background.

### TEM-1 β-lactamase — recovered

`1BTL` apo → `1PZO` holo, ligand `CBT`. 263 residues. Two CBT copies; the
primary sits 10.2 Å from the catalytic Ser70 OG with a 13-residue shell.

| detector | best qualifying rank | coverage | centroid | persistence |
|---|---|---|---|---|
| nma / alpha | **2 of 10** | 62% (8/13) | 6.2 Å | 5% — 1 conformer of 21 |
| nma / alpha | 9 | 69% (9/13) | 5.2 Å | 90% — 19 of 21 |

Recovered at rank 2 in **2.1 s**, CPU only.

The two rows are the same site, and they are the reason `persistence` is a
column in the panel rather than folded into the ranking: the ranker put a
pocket seen in 1 conformer of 21 above one covering more of the site in 19 of
21. Rank alone would have pointed at the weaker evidence.

### PTP1B — not recovered, for a legible reason

`1SUG` apo → `1T49` holo, ligand `892`. 298 residues. The 14-residue shell
splits across the α3 helix (187–200) and the α6/α7 region (276–282).

With `nma`, nothing cleared the threshold. The site arrived as **two adjacent
pockets**: rank 3 covering six α3 residues and no α6/α7, rank 6 covering three
α6/α7 residues and almost no α3. Together they cover 9 of 14. In apo `1SUG`
the α7 helix (283–299) is ordered and packed against α6, occluding that half
of the site, and α7 displacement is the actual binding event.

This is why the panel's multi-select matters, and it is the one feature gap
that follows directly from the data: ticking rank 3 and rank 6 should merge
into one residue selection before it reaches `generate_from_pocket.py`.

### The MD backend does not replicate

Three `openmm` runs over the same input file, same seed:

| run | best coverage | α6/α7 residues found |
|---|---|---|
| 1 | rank 9 — 7/14 | **5** (276, 277, 279, 280, 281) |
| 2 | rank 7 — 7/14 | 1 (280) |
| 3 | rank 10 — 6/14 | 1 (280) |

An initial reading of run 1 looked like a mechanistic result — MD reaching the
α7-occluded face that the ANM ramp cannot. It replicates once in three. The
site's rank moves between 7 and 10 across runs.

Chasing the same discrepancy through the HTTP path first implicated the
receptor the viewer writes; that was ruled out. `Structure.receptor().to_pdb()`
differs from a hand-stripped file only in B-factors, atom serial numbers and
the altloc letter — the coordinates are identical, and none of those reach the
force field.

**So: use TEM-1 with the nma backend as the demo.** It is deterministic, takes
two seconds, needs no GPU, and recovers a published allosteric site at rank 2.
PTP1B remains valuable as the case that shows where single-structure detection
fails — not as evidence that MD fixes it.

Also note the hit criteria used above (≥50% of shell residues covered, or
centroid within 5 Å) are **local choices, not a published convention**. Under a
40% bar, TEM-1 `surface-fusion` hits at rank 1 and PTP1B `nma` hits at rank 3.
Report coverage and persistence; do not hard-code a verdict.

## 6. Measured timings

| run | structure | wall |
|---|---|---|
| nma, 20 conformers | 1BTL, 263 res | 2.1 s |
| nma, 20 conformers | 1SUG, 298 res | 2.4 s |
| surface-fusion `--no-sequence` | 1BTL / 1SUG | 15 s / 17 s |
| openmm on CUDA | 1SUG, 298 res | 49–51 s (includes nvrtc JIT) |
| fpocket, for comparison | 1BTL | 0.5 s |

## 7. Coverage against the MOE tutorial

Against `MoE Protein Dynamics Tutorial/outputs/moe-molecular-dynamics-tutorial.md`:

| # | Tutorial stage | Status |
|---|---|---|
| 1 | Load & inspect, biomolecule assembly, chain breaks | partial — viewer fetches PDB/CIF, `quickprep` reports gaps, `lacuna --homodimer` reads BIOMT. No Sequence-Editor equivalent |
| 2 | Quick Prep: repairs, protonation, H-bond net, restrained minimization | **covered** — `quickprep` via `/api/prep` |
| 2b | Loop Modeler / Homology Modeler | gap — PDBFixer builds gaps ≤10 residues only |
| 3 | Ligand protomers at target pH | gap — PROPKA covers protein only |
| 4 | MD engine + force field | partial — OpenMM/Amber14 on CUDA, but **the ligand is dropped**, and the tutorial's subject is a protein–ligand complex |
| 5 | Explicit solvent box, 100 mM NaCl, 10 Å margin | gap — GBn2 implicit solvent, no box, no ions |
| 6 | Staged protocol: minimize → heat → NVT → NPT → production | gap — fixed 310 K, 10 ps + 50 ps |
| 7 | Run locally / export batch | partial — local only |
| 8 | Dynamics Player: playback, superpose, hide solvent | gap, but cheap — `--emit-conformers` already writes a multi-model PDB and reports it in `meta.ensemble`; 3Dmol animates multi-model files natively |
| 9 | Descriptors: helix/strand content, volume, SASA, CSV | gap |

Roughly **1 of 9 covered, 3 partial, 5 gaps.** Worth keeping in proportion:
the tutorial is a general MD workflow that ends at secondary-structure
descriptors and never mentions cryptic or allosteric pockets, so the detection,
ensemble clustering, ranking and apo/holo scoring here sit outside its scope
entirely. The gaps that matter for pocket work are **#4** (no ligand in the
simulation) and **#5/#6** (implicit solvent, no real protocol).

## 8. What is not done

1. **Pocket merging.** PTP1B needs rank 3 + rank 6 as one selection. The panel
   multi-selects already; the union does not reach the generation path.
2. **Ensemble animation.** The multi-model PDB is produced and named in
   `meta.ensemble` but nothing draws it. This is tutorial stage 8 for very
   little code.
3. **Ligand-aware MD.** The single biggest divergence from the tutorial.
4. **The `plm` extra**, which would let `surface-fusion` be evaluated as its
   documentation intends.
5. **The remaining two targets**, KRAS (`4LDJ`→`6OIM`) and IL-2
   (`1M47`→`1PY2`), plus `3PTB` as a negative control.
6. **A long-MD test.** If 50 ps already surfaces the α6/α7 face one time in
   three, a few ns may move it up the ranking — but given §5 that needs
   repeats, not a single run.

## 9. Verification performed

- `lacuna.py`, `pockets.py`, `serve.py` parse; the page's inline JS passes
  `node --check`.
- `lacuna.py` CLI against 1BTL: finds the micromamba env, reports both
  backends, 10 pockets in 2.09 s.
- Over HTTP: `/api/status` reports lacuna 1.2.0 with `['nma', 'openmm']`;
  fpocket path unchanged at 11 pockets in 0.51 s; `lacuna/nma` 10 pockets in
  2.08 s with persistence and crypticity populated; a repeat returns
  `cached=True`; an unknown `method` returns 400 with a readable message.
- PTP1B through `/api/pockets` with `backend=openmm` on CUDA: 10 pockets, 21
  conformers, 51.19 s.
- `openmm.testInstallation`: CUDA computes forces, within tolerance of CPU.
- Routing after retiring the second page: `/` serves the compact viewer and
  carries the detector dropdown, `/index-compact.html` returns 200,
  `/index.html` returns 404.

Not verified: the panel has not been exercised in a browser. The JavaScript
parses and the endpoints it calls are tested, but no one has clicked the
dropdown.
