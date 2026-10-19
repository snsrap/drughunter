# Graph Report - drughunter  (2026-10-19)

## Corpus Check
- 93 files · ~73,837 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .pdb 4, (none) 2, .cif 2)

## Summary
- 1657 nodes · 4739 edges · 75 communities (57 shown, 18 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 331 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `336ff657`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- commit
- Structure Buffering & Locking
- Project
- web.py
- StructureBuffer
- NeatPocket
- app/js/model/structurefile.js
- pocketbench/__init__.py
- BadRequest
- ViewerService
- Scene Colors & Pockets
- lacuna.py
- app/js/model/session.js
- pocketbench.py
- service.py
- PocketTools
- ui_snapshot.mjs
- Session Model State
- smoke_api.py
- E2E Test Setup
- $
- imports
- CSV & Generation Jobs
- Ensemble pocket detection in ProteinViewer
- Structure
- PocketbenchTest
- Validation
- ProteinViewer
- FakePockets
- proteinviewer/app/js/scene.js
- FakeJob
- manifest.json
- structure.py
- test_superpose.py
- .superpose
- drughunter
- commit
- $
- cli.py
- LacunaDetector
- start.sh
- proteinviewer
- app/js/features/pockets.js
- app/js/features/fetch.js
- ProteinViewer
- proteinviewer/app/js/core/api.js
- proteinviewer/app/js/features/table.js
- protonate
- app/js/core/api.js
- app/js/features/tree.js
- ToolUnavailable
- app/js/features/table.js
- proteinviewer/app/js/model/structurefile.js
- main
- quickprep/prep.py
- proteinviewer/app/js/features/superpose.js
- model.test.js
- align
- prepare
- proteinviewer/app/js/features/pockets.js
- quickprep
- numpy
- fix
- StoreTest
- WebTest
- write_output
- DetectorRegistry
- Pocket
- Once
- resolve_destination
- pocketbench
- quickprep/__init__.py
- CLAUDE.md
- drughunter

## God Nodes (most connected - your core abstractions)
1. `Structure` - 54 edges
2. `BadRequest` - 51 edges
3. `ViewerService` - 47 edges
4. `commit()` - 45 edges
5. `commit()` - 45 edges
6. `$` - 42 edges
7. `$` - 42 edges
8. `esc()` - 36 edges
9. `esc()` - 36 edges
10. `LacunaDetector` - 29 edges

## Surprising Connections (you probably didn't know these)
- `Design decisions worth knowing` --references--> `select_residues()`  [INFERRED]
  docs/lacuna-integration.md → pocketbench/pockets.py
- `Hit criteria` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Running a case` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Method` --references--> `align()`  [INFERRED]
  docs/pocketbench.md → pocketbench/align.py
- `Superposing structures` --references--> `align()`  [INFERRED]
  docs/proteinviewer.md → pocketbench/align.py

## Import Cycles
- None detected.

## Communities (75 total, 18 thin omitted)

### Community 0 - "commit"
Cohesion: 0.15
Nodes (31): fault(), plural(), commit(), nodeOf(), state, subscribe(), views, bind() (+23 more)

### Community 1 - "Structure Buffering & Locking"
Cohesion: 0.14
Nodes (32): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), picksOf(), inModel(), clearLayer() (+24 more)

### Community 2 - "Project"
Cohesion: 0.08
Nodes (8): BufferedStructure, residue_list(), spheres_pdb(), free_dir(), Project, safe_name(), safe_relpath(), ProjectTest

### Community 3 - "web.py"
Cohesion: 0.12
Nodes (8): PayloadTooLarge, ViewerError, Api, ApiRequest, dumps(), _finite(), Handler, status_for()

### Community 4 - "StructureBuffer"
Cohesion: 0.12
Nodes (5): check_structure(), detect_format(), safe_token(), read(), BufferTest

### Community 5 - "NeatPocket"
Cohesion: 0.08
Nodes (5): find_neat_dir(), Job, NeatPocket, JobProgressTest, RunnerTest

### Community 6 - "app/js/model/structurefile.js"
Cohesion: 0.16
Nodes (23): download(), scopeAtoms(), scopeStructure(), basename(), CIF_EXT, cifFirstModel(), cifTitle(), firstModel() (+15 more)

### Community 7 - "pocketbench/__init__.py"
Cohesion: 0.08
Nodes (27): _lining(), _num(), parse_pockets(), read_pockets(), ReportedPocket, best_rank(), Criteria, PocketScore (+19 more)

### Community 8 - "BadRequest"
Cohesion: 0.08
Nodes (17): BadRequest, BufferRequest, GenerateRequest, JobQuery, PocketRequest, PrepRequest, _residues(), SuperposeRequest (+9 more)

### Community 9 - "ViewerService"
Cohesion: 0.06
Nodes (14): StructureBuffer, default_project_root(), Settings, ProjectStore, build_service(), main(), make_server(), parse_args() (+6 more)

### Community 11 - "lacuna.py"
Cohesion: 0.05
Nodes (17): Adding a detector, Adding a pocket detector, FpocketDetector, _parse_vert(), Option, PocketDetector, PocketbenchError, ToolError (+9 more)

### Community 12 - "app/js/model/session.js"
Cohesion: 0.09
Nodes (40): boldOnly(), ENT, entity(), fitOf(), flags(), num(), pocket(), point() (+32 more)

### Community 13 - "pocketbench.py"
Cohesion: 0.18
Nodes (4): ToolFailed, output_tail(), probe(), run()

### Community 14 - "service.py"
Cohesion: 0.09
Nodes (8): PocketRun, Conflict, NotFound, find_python(), _last_json_line(), _read_json(), _now(), pdb_line()

### Community 15 - "PocketTools"
Cohesion: 0.11
Nodes (4): GenerationJob, Generator, PocketTools, Preparer

### Community 16 - "ui_snapshot.mjs"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 17 - "Session Model State"
Cohesion: 0.16
Nodes (3): QuickPrep, residue_ranges(), PrepTest

### Community 19 - "E2E Test Setup"
Cohesion: 0.12
Nodes (3): ProjectRequest, ProjectSaveRequest, ProjectArtifactsTest

### Community 20 - "$"
Cohesion: 0.12
Nodes (51): job(), ENT_COLORS, $, esc(), fault(), note(), plural(), setHTML() (+43 more)

### Community 21 - "imports"
Cohesion: 0.16
Nodes (8): imports(), name(), visit(), LayeringTest, module_name(), PackagingTest, top_level_imports(), version()

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.22
Nodes (3): csv_text(), _csv_value(), NamesTest

### Community 23 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.12
Nodes (17): 1. Why, 3. Environments, 4. Backends, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed (+9 more)

### Community 24 - "Structure"
Cohesion: 0.09
Nodes (29): _near(), select_residues(), detect_format(), Structure, pdb_line(), read(), brute_force(), pocket() (+21 more)

### Community 26 - "Validation"
Cohesion: 0.33
Nodes (6): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation

### Community 27 - "ProteinViewer"
Cohesion: 0.11
Nodes (18): Bound ligands, Endpoints, Fetching other structures, Finding pockets, Generating binders, Getting results out, No reference ligand is needed, Opening your own files (+10 more)

### Community 29 - "proteinviewer/app/js/scene.js"
Cohesion: 0.13
Nodes (41): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), nodeOf() (+33 more)

### Community 31 - "manifest.json"
Cohesion: 0.40
Nodes (4): api, artifacts, created, project

### Community 32 - "structure.py"
Cohesion: 0.08
Nodes (19): 2. What changed, Design decisions worth knowing, _parse_info(), _like(), _move_cif(), _move_pdb(), move_text(), _pdb_coord() (+11 more)

### Community 33 - "test_superpose.py"
Cohesion: 0.11
Nodes (21): Atom, protein_chains(), Residue, superpose(), atom_line(), chain_lines(), ligand_lines(), pdb() (+13 more)

### Community 35 - "drughunter"
Cohesion: 0.33
Nodes (7): Adding a pocket detector, drughunter, External tools, Install, Layout, Run, Tests

### Community 36 - "commit"
Cohesion: 0.16
Nodes (31): How the pieces fit, clearRun(), commit(), downloadPockets(), bind(), bind(), clearPicks(), download() (+23 more)

### Community 37 - "$"
Cohesion: 0.19
Nodes (28): ENT_COLORS, $, attr(), esc(), setHTML(), setMsg(), frameOf(), frames() (+20 more)

### Community 38 - "cli.py"
Cohesion: 0.12
Nodes (15): cmd_apo(), cmd_detect(), cmd_detectors(), cmd_score(), cmd_site(), cmd_superpose(), _fit_line(), _json() (+7 more)

### Community 39 - "LacunaDetector"
Cohesion: 0.11
Nodes (6): _f(), LacunaDetector, _read_pseudoatoms(), Once, probe(), test_lacuna_options_defaults_case_and_bad_values()

### Community 43 - "app/js/features/pockets.js"
Cohesion: 0.18
Nodes (22): entity(), liveRun(), bind(), downloadPockets(), find(), ligands(), message(), parseMethod() (+14 more)

### Community 44 - "app/js/features/fetch.js"
Cohesion: 0.20
Nodes (19): note(), gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId() (+11 more)

### Community 45 - "ProteinViewer"
Cohesion: 0.10
Nodes (20): Bound ligands, Endpoints, Fetching other structures, Finding pockets, Generating binders, Getting results out, Layout, No reference ligand is needed (+12 more)

### Community 46 - "proteinviewer/app/js/core/api.js"
Cohesion: 0.18
Nodes (18): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), post(), prepare(), projectStatus() (+10 more)

### Community 47 - "proteinviewer/app/js/features/table.js"
Cohesion: 0.19
Nodes (18): gunzip(), readText(), save(), liveGen(), bind(), bindPanel(), csv(), CSV_COLS (+10 more)

### Community 48 - "protonate"
Cohesion: 0.15
Nodes (12): apply_states(), _final_states(), _label(), _merge(), _nm(), _pdb2pqr(), _pka_flags(), protonate() (+4 more)

### Community 49 - "app/js/core/api.js"
Cohesion: 0.20
Nodes (17): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), job(), post(), prepare() (+9 more)

### Community 50 - "app/js/features/tree.js"
Cohesion: 0.27
Nodes (17): clearRun(), bind(), clearPicks(), focusRow(), fold(), forget(), pick(), reload() (+9 more)

### Community 51 - "ToolUnavailable"
Cohesion: 0.12
Nodes (3): ToolError, ToolUnavailable, FakeService

### Community 52 - "app/js/features/table.js"
Cohesion: 0.24
Nodes (16): save(), liveGen(), bind(), bindPanel(), csv(), CSV_COLS, downloadCsv(), downloadSdf() (+8 more)

### Community 53 - "proteinviewer/app/js/model/structurefile.js"
Cohesion: 0.20
Nodes (15): basename(), CIF_EXT, cifTitle(), firstModel(), formatOf(), idFromName(), PDB_EXT, pdbFirstModel() (+7 more)

### Community 54 - "main"
Cohesion: 0.14
Nodes (11): Commands, main(), fake_executable(), fpocket(), run(), test_a_rerun_does_not_read_back_an_earlier_runs_pockets(), test_detect_answers_in_json(), test_detect_errors_say_whose_problem_they_are() (+3 more)

### Community 55 - "quickprep/prep.py"
Cohesion: 0.19
Nodes (6): clean(), cli(), _expdta(), _head(), main(), _ranges()

### Community 56 - "proteinviewer/app/js/features/superpose.js"
Cohesion: 0.32
Nodes (14): attr(), frameOf(), buffer(), fitOn(), message(), movers(), pickedNodes(), reference() (+6 more)

### Community 57 - "model.test.js"
Cohesion: 0.23
Nodes (13): buildTree(), CAPS, hider(), ligandsIn(), polymerLike(), WATERS, atom(), backbone() (+5 more)

### Community 58 - "align"
Cohesion: 0.18
Nodes (8): Method, Superposing structures, align(), _codes(), self_align(), Chain, _pair_chains(), test_align_end_gaps_are_free_and_an_insertion_opens_one_gap()

### Community 59 - "prepare"
Cohesion: 0.21
Nodes (6): prepare(), hydrogens(), run(), test_no_rebuild(), test_provenance(), test_zinc_histidines()

### Community 60 - "proteinviewer/app/js/features/pockets.js"
Cohesion: 0.30
Nodes (13): bind(), find(), ligands(), message(), parseMethod(), pocketRow(), rankedBy(), render() (+5 more)

### Community 61 - "quickprep"
Cohesion: 0.24
Nodes (6): Install, quickprep, Tests, Use, Used by, What it does

### Community 62 - "numpy"
Cohesion: 0.24
Nodes (5): _bonds_of(), _edit(), metal_sites(), _replace_hydrogens(), _ring_h()

### Community 63 - "fix"
Cohesion: 0.20
Nodes (7): _chirality(), fix(), _gap_numbers(), _key(), _number_caps(), _one(), refine()

### Community 66 - "write_output"
Cohesion: 0.20
Nodes (4): _remarks(), _wrap(), write_output(), write_pqr()

### Community 71 - "pocketbench"
Cohesion: 0.29
Nodes (7): As a library, Detection, Driven by a program, Install, Layout, pocketbench, Running an apo/holo case

## Knowledge Gaps
- **2 isolated node(s):** `drughunter`, `proteinviewer`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 468 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Layout` connect `ProteinViewer` to `lacuna.py`, `commit`?**
  _High betweenness centrality (0.261) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `Structure` (e.g. with `cmd_apo()` and `cmd_detect()`) actually correct?**
  _`Structure` has 29 INFERRED edges - model-reasoned connections that need verification._
- **What connects `drughunter`, `proteinviewer` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `commit` be split into smaller, more focused modules?**
  _Cohesion score 0.14935988620199148 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `commit` to `proteinviewer/app/js/features/superpose.js`, `ProteinViewer`?**
  _High betweenness centrality (0.255) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `Pocketbench`) actually correct?**
  _`BadRequest` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Should `Structure Buffering & Locking` be split into smaller, more focused modules?**
  _Cohesion score 0.1443850267379679 - nodes in this community are weakly interconnected._