# Third-party software

DrugHunter links to or runs the tools below. None of them is copied into this
repository; each keeps its own license and citation request. If you publish
results obtained with DrugHunter, please cite the tools you used as well.

## Installed by `pip install drughunter`

| Package | License |
|---|---|
| OpenMM | MIT / LGPL (OpenMM license) |
| PDBFixer | MIT |
| PDB2PQR | BSD |
| PROPKA | LGPL-2.1 (used as a library, not modified or bundled) |
| gemmi | MPL-2.0 |
| NumPy | BSD-3-Clause |

## Optional or external (found at startup, not installed by pip)

| Tool | License | Role |
|---|---|---|
| lacuna-pockets (`drughunter[lacuna]`) | MIT | ensemble pocket detector |
| fpocket | MIT | pocket detector |
| NEAT-POCKET (Rose, Jacob et al.) | MIT | ligand generation, run from the user's own checkout |
| smina (Koes et al.) | GPL-2.0 | pose scoring, found on PATH or at `SMINA_BIN` |
| gnina (Koes group) | Apache-2.0 | pose scoring, same flags as smina; `GNINA_BIN` |
| 3Dmol.js | BSD-3-Clause | viewer, loaded from a CDN |
| IBM Plex (Google Fonts) | SIL OFL 1.1 | viewer fonts, loaded from a CDN |

All of these are compatible with distributing DrugHunter under Apache-2.0.

smina is GPL-2.0, so it is worth being explicit about why that is not a
problem here: DrugHunter neither bundles nor links it. Like fpocket, it is
found on the user's `PATH` and run as a separate program, exchanging files on
disk -- the arrangement the GPL calls mere aggregation. Nothing in DrugHunter
derives from it, and a DrugHunter without it simply reports that pose scoring
is unavailable. If you publish scores, cite the scorer you used.
