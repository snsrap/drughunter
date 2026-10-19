# Graph Report - drughunter  (2026-10-19)

## Corpus Check
- 93 files · ~73,837 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .pdb 4, (none) 2, .cif 2)

## Summary
- 1373 nodes · 3720 edges · 66 communities (51 shown, 15 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 306 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1ee0473e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ViewerService
- .setUp
- Project
- web.py
- StructureBuffer
- NeatPocket
- ToolFailed
- pocketbench/__init__.py
- BadRequest
- server.py
- Pocketbench
- fpocket.py
- app/js/model/session.js
- site.py
- service.py
- PocketTools
- ui_snapshot.mjs
- Session Model State
- select_residues
- E2E Test Setup
- $
- imports
- CSV & Generation Jobs
- Ensemble pocket detection in ProteinViewer
- Structure
- Validation
- quickprep
- FakePockets
- proteinviewer/app/js/scene.js
- FakeJob
- Once
- structure.py
- test_superpose.py
- drughunter
- commit
- 4. Backends
- cli.py
- LacunaDetector
- start.sh
- Generating binders
- ProteinViewer
- proteinviewer/app/js/core/api.js
- proteinviewer/app/js/features/table.js
- protonate
- web.py
- fetch.js
- test_score.py
- quickprep/prep.py
- proteinviewer/app/js/features/superpose.js
- model.test.js
- align
- prepare
- numpy
- fix
- write_output
- PocketDetector
- test_detect.py
- pocketbench
- CLAUDE.md
- drughunter

## God Nodes (most connected - your core abstractions)
1. `Structure` - 54 edges
2. `BadRequest` - 49 edges
3. `ViewerService` - 47 edges
4. `commit()` - 45 edges
5. `$` - 42 edges
6. `esc()` - 36 edges
7. `LacunaDetector` - 29 edges
8. `ProjectArtifactsTest` - 28 edges
9. `NotFound` - 27 edges
10. `StructureBuffer` - 26 edges

## Surprising Connections (you probably didn't know these)
- `Running a case` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Hit criteria` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Ensemble pocket detection in ProteinViewer` --references--> `score()`  [INFERRED]
  docs/lacuna-integration.md → pocketbench/score.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → pocketbench/detectors/fpocket.py
- `2. What changed` --references--> `Pocket`  [INFERRED]
  docs/lacuna-integration.md → pocketbench/pockets.py

## Import Cycles
- None detected.

## Communities (66 total, 15 thin omitted)

### Community 0 - "ViewerService"
Cohesion: 0.08
Nodes (10): BufferedStructure, PocketRun, residue_list(), spheres_pdb(), safe_name(), BufferRequest, JobQuery, PocketRequest (+2 more)

### Community 2 - "Project"
Cohesion: 0.07
Nodes (7): free_dir(), Project, ProjectStore, resolve_destination(), DestinationTest, ProjectTest, StoreTest

### Community 3 - "web.py"
Cohesion: 0.11
Nodes (6): ApiRequest, dumps(), _finite(), Handler, status_for(), WebTest

### Community 4 - "StructureBuffer"
Cohesion: 0.10
Nodes (6): check_structure(), detect_format(), safe_token(), StructureBuffer, read(), BufferTest

### Community 5 - "NeatPocket"
Cohesion: 0.06
Nodes (8): find_neat_dir(), Job, _last_json_line(), NeatPocket, _read_json(), fake_executable(), JobProgressTest, RunnerTest

### Community 6 - "ToolFailed"
Cohesion: 0.24
Nodes (3): PocketbenchError, ToolError, ToolFailed

### Community 7 - "pocketbench/__init__.py"
Cohesion: 0.13
Nodes (10): _lining(), _num(), parse_pockets(), read_pockets(), ReportedPocket, best_rank(), PocketScore, score() (+2 more)

### Community 8 - "BadRequest"
Cohesion: 0.10
Nodes (13): BadRequest, GenerateRequest, PrepRequest, _residues(), SuperposeRequest, _check_range(), choice(), _get() (+5 more)

### Community 9 - "server.py"
Cohesion: 0.10
Nodes (10): default_project_root(), Settings, find_python(), build_service(), main(), make_server(), parse_args(), _print_tool_status() (+2 more)

### Community 11 - "fpocket.py"
Cohesion: 0.14
Nodes (8): FpocketDetector, _parse_info(), _parse_vert(), ToolUnavailable, first_executable(), output_tail(), run(), test_a_missing_binary()

### Community 12 - "app/js/model/session.js"
Cohesion: 0.16
Nodes (20): boldOnly(), ENT, entity(), fitOf(), flags(), num(), pocket(), point() (+12 more)

### Community 13 - "site.py"
Cohesion: 0.25
Nodes (3): shell(), d2(), Superposition

### Community 14 - "service.py"
Cohesion: 0.07
Nodes (12): post(), e(), Conflict, ToolError, ToolFailed, ToolUnavailable, residue_spec(), output_tail() (+4 more)

### Community 15 - "PocketTools"
Cohesion: 0.10
Nodes (4): GenerationJob, Generator, PocketTools, Preparer

### Community 16 - "ui_snapshot.mjs"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 17 - "Session Model State"
Cohesion: 0.11
Nodes (6): QuickPrep, residue_ranges(), first_executable(), Once, probe(), PrepTest

### Community 18 - "select_residues"
Cohesion: 0.29
Nodes (4): 2. What changed, Design decisions worth knowing, _near(), select_residues()

### Community 20 - "$"
Cohesion: 0.14
Nodes (43): job(), ENT_COLORS, $, esc(), fault(), note(), setHTML(), setMsg() (+35 more)

### Community 21 - "imports"
Cohesion: 0.16
Nodes (8): imports(), name(), visit(), LayeringTest, module_name(), PackagingTest, top_level_imports(), version()

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.22
Nodes (4): csv_text(), _csv_value(), safe_relpath(), NamesTest

### Community 23 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.15
Nodes (13): 1. Why, 3. Environments, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed, CPU only (enough for the nma backend) (+5 more)

### Community 24 - "Structure"
Cohesion: 0.11
Nodes (24): move_text(), detect_format(), read_text(), Structure, pdb_line(), read(), test_chain_is_part_of_residue_identity(), test_nothing_left_after_solvent_is_bad_input() (+16 more)

### Community 26 - "Validation"
Cohesion: 0.33
Nodes (6): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation

### Community 27 - "quickprep"
Cohesion: 0.33
Nodes (6): Install, quickprep, Tests, Use, Used by, What it does

### Community 29 - "proteinviewer/app/js/scene.js"
Cohesion: 0.14
Nodes (39): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), nodeOf() (+31 more)

### Community 32 - "structure.py"
Cohesion: 0.11
Nodes (13): _like(), _move_cif(), _move_pdb(), _pdb_coord(), _rotate_u(), cif_loops(), cif_set_tokens(), cif_tokens() (+5 more)

### Community 33 - "test_superpose.py"
Cohesion: 0.10
Nodes (24): Atom, fit(), _jacobi(), protein_chains(), related(), superpose(), Transform, atom_line() (+16 more)

### Community 35 - "drughunter"
Cohesion: 0.33
Nodes (6): drughunter, External tools, Install, Layout, Run, Tests

### Community 36 - "commit"
Cohesion: 0.16
Nodes (29): clearRun(), commit(), state, subscribe(), views, adopt(), failed(), openSession() (+21 more)

### Community 37 - "4. Backends"
Cohesion: 0.50
Nodes (4): 4. Backends, Detectors, `nma` — default, 2 s, deterministic, `openmm` — 50 s on CUDA, **not reproducible**

### Community 38 - "cli.py"
Cohesion: 0.13
Nodes (16): cmd_apo(), cmd_detect(), cmd_detectors(), cmd_score(), cmd_site(), cmd_superpose(), _fit_line(), _json() (+8 more)

### Community 39 - "LacunaDetector"
Cohesion: 0.14
Nodes (5): _f(), LacunaDetector, _read_pseudoatoms(), probe(), test_real_lacuna_nma_on_crambin()

### Community 41 - "Generating binders"
Cohesion: 0.50
Nodes (4): Endpoints, Generating binders, No reference ligand is needed, The molecule table

### Community 45 - "ProteinViewer"
Cohesion: 0.12
Nodes (17): Adding a pocket detector, Bound ligands, Fetching other structures, Finding pockets, Getting results out, Layout, Opening your own files, Preparing structures (+9 more)

### Community 46 - "proteinviewer/app/js/core/api.js"
Cohesion: 0.26
Nodes (14): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), post(), prepare(), projectStatus() (+6 more)

### Community 47 - "proteinviewer/app/js/features/table.js"
Cohesion: 0.24
Nodes (16): save(), liveGen(), bind(), bindPanel(), csv(), CSV_COLS, downloadCsv(), downloadSdf() (+8 more)

### Community 48 - "protonate"
Cohesion: 0.14
Nodes (12): apply_states(), _final_states(), _label(), _merge(), _nm(), _pdb2pqr(), _pka_flags(), PrepError (+4 more)

### Community 51 - "web.py"
Cohesion: 0.10
Nodes (6): NotFound, PayloadTooLarge, ViewerError, ProjectRequest, Api, FakeService

### Community 53 - "fetch.js"
Cohesion: 0.12
Nodes (32): gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId(), addEntity() (+24 more)

### Community 54 - "test_score.py"
Cohesion: 0.18
Nodes (16): main(), sites(), fake_executable(), test_detect_answers_in_json(), test_detect_errors_say_whose_problem_they_are(), kind(), test_detectors_lists_options_and_additives(), apo_holo() (+8 more)

### Community 55 - "quickprep/prep.py"
Cohesion: 0.16
Nodes (7): clean(), cli(), _expdta(), _head(), main(), _ranges(), summary()

### Community 56 - "proteinviewer/app/js/features/superpose.js"
Cohesion: 0.30
Nodes (15): How the pieces fit, attr(), plural(), frameOf(), buffer(), fitOn(), message(), movers() (+7 more)

### Community 57 - "model.test.js"
Cohesion: 0.13
Nodes (27): downloadPockets(), download(), scope(), scopeAtoms(), scopeStructure(), summarize(), cifFirstModel(), ATOM_SITE (+19 more)

### Community 58 - "align"
Cohesion: 0.17
Nodes (9): Method, Superposing structures, align(), _codes(), self_align(), Chain, _pair_chains(), Residue (+1 more)

### Community 59 - "prepare"
Cohesion: 0.21
Nodes (6): prepare(), hydrogens(), run(), test_no_rebuild(), test_provenance(), test_zinc_histidines()

### Community 62 - "numpy"
Cohesion: 0.24
Nodes (5): _bonds_of(), _edit(), metal_sites(), _replace_hydrogens(), _ring_h()

### Community 63 - "fix"
Cohesion: 0.20
Nodes (7): _chirality(), fix(), _gap_numbers(), _key(), _number_caps(), _one(), refine()

### Community 66 - "write_output"
Cohesion: 0.17
Nodes (5): _remarks(), _variant(), _wrap(), write_output(), write_pqr()

### Community 67 - "PocketDetector"
Cohesion: 0.09
Nodes (4): Adding a detector, DetectorRegistry, PocketDetector, test_the_registry()

### Community 68 - "test_detect.py"
Cohesion: 0.07
Nodes (16): Commands, Option, DetectionResult, _num(), Pocket, brute_force(), fpocket(), run() (+8 more)

### Community 71 - "pocketbench"
Cohesion: 0.29
Nodes (7): As a library, Detection, Driven by a program, Install, Layout, pocketbench, Running an apo/holo case

## Knowledge Gaps
- **1 isolated node(s):** `drughunter`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 427 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Layout` connect `ProteinViewer` to `proteinviewer/app/js/features/superpose.js`?**
  _High betweenness centrality (0.340) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `Structure` (e.g. with `cmd_apo()` and `cmd_detect()`) actually correct?**
  _`Structure` has 29 INFERRED edges - model-reasoned connections that need verification._
- **What connects `drughunter` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ViewerService` be split into smaller, more focused modules?**
  _Cohesion score 0.0784313725490196 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `proteinviewer/app/js/features/superpose.js` to `commit`, `ProteinViewer`?**
  _High betweenness centrality (0.325) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `BufferRequest`) actually correct?**
  _`BadRequest` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `Project` be split into smaller, more focused modules?**
  _Cohesion score 0.06918238993710692 - nodes in this community are weakly interconnected._