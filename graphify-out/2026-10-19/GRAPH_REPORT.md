# Graph Report - drughunter  (2026-10-19)

## Corpus Check
- 95 files · ~94,436 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .pdb 4, (none) 2, .cif 2)

## Summary
- 1411 nodes · 3742 edges · 69 communities (52 shown, 17 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 263 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `70a82b8f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_service.py
- test_superpose.py
- DetectionResult
- FakePockets
- StructureBuffer
- pockets.js
- ToolFailed
- pocketbench/__init__.py
- BadRequest
- server.py
- NeatPocket
- fpocket.py
- web.py
- pocketbench/__init__.py
- service.py
- PocketTools
- ui_snapshot.mjs
- Session Model State
- sites
- E2E Test Setup
- $
- imports
- CSV & Generation Jobs
- Ensemble pocket detection in ProteinViewer
- Structure
- BadRequest
- Validation
- quickprep
- FakePockets
- proteinviewer/app/js/scene.js
- FakeJob
- Once
- structure.py
- test_superpose.py
- .residue
- drughunter
- NotFound
- quickprep/prep.py
- superpose.py
- LacunaDetector
- start.sh
- Generating binders
- neat_pocket.py
- .lock
- 4. Backends
- test_score.py
- test_detect.py
- protonate
- Structure tree
- drughunter
- fetch.js
- test_score.py
- quickprep/prep.py
- QuickPrep
- ServiceTest
- ViewerService
- 5. Validation
- resolve_destination
- FakeJob
- Generating binders

## God Nodes (most connected - your core abstractions)
1. `BadRequest` - 48 edges
2. `Structure` - 47 edges
3. `commit()` - 45 edges
4. `$` - 43 edges
5. `esc()` - 36 edges
6. `ViewerService` - 31 edges
7. `ProjectArtifactsTest` - 28 edges
8. `NotFound` - 26 edges
9. `StructureBuffer` - 25 edges
10. `Project` - 24 edges

## Surprising Connections (you probably didn't know these)
- `Adding a detector` --references--> `DetectionResult`  [INFERRED]
  docs/pocketbench.md → pocketbench/pockets.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → pocketbench/detectors/fpocket.py
- `How the pieces fit` --references--> `commit()`  [INFERRED]
  docs/proteinviewer.md → proteinviewer/app/js/core/state.js
- `Adding a detector` --references--> `name()`  [INFERRED]
  docs/pocketbench.md → tests/test_architecture.py
- `Adding a detector` --references--> `select_residues()`  [INFERRED]
  docs/pocketbench.md → pocketbench/pockets.py

## Import Cycles
- None detected.

## Communities (69 total, 17 thin omitted)

### Community 0 - "test_service.py"
Cohesion: 0.07
Nodes (14): post(), e(), ToolError, ToolFailed, ToolUnavailable, _last_json_line(), _read_json(), residue_spec() (+6 more)

### Community 1 - "test_superpose.py"
Cohesion: 0.15
Nodes (19): fit(), _jacobi(), related(), superpose(), chain_lines(), pdb(), rotation(), trace() (+11 more)

### Community 2 - "DetectionResult"
Cohesion: 0.07
Nodes (9): Adding a detector, Adding a pocket detector, default_detectors(), DetectorRegistry, Option, PocketDetector, BadInput, test_options_describe_themselves() (+1 more)

### Community 4 - "StructureBuffer"
Cohesion: 0.10
Nodes (16): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation, _lining(), _num() (+8 more)

### Community 5 - "pockets.js"
Cohesion: 0.14
Nodes (45): ENT_COLORS, $, esc(), fault(), note(), plural(), setHTML(), setMsg() (+37 more)

### Community 6 - "ToolFailed"
Cohesion: 0.07
Nodes (13): PocketRun, default_project_root(), Settings, find_python(), residue_list(), spheres_pdb(), build_service(), main() (+5 more)

### Community 7 - "pocketbench/__init__.py"
Cohesion: 0.07
Nodes (53): gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId(), addEntity() (+45 more)

### Community 8 - "BadRequest"
Cohesion: 0.12
Nodes (22): cmd_apo(), cmd_detect(), Structure, pdb_line(), read(), test_chain_is_part_of_residue_identity(), test_nothing_left_after_solvent_is_bad_input(), test_real_fpocket_on_crambin() (+14 more)

### Community 9 - "server.py"
Cohesion: 0.13
Nodes (42): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), nodeOf() (+34 more)

### Community 10 - "NeatPocket"
Cohesion: 0.08
Nodes (5): find_neat_dir(), Job, NeatPocket, JobProgressTest, RunnerTest

### Community 11 - "fpocket.py"
Cohesion: 0.16
Nodes (23): download(), scopeAtoms(), scopeStructure(), cifFirstModel(), cifCol(), cifLoops(), cifSubset(), cifTokens() (+15 more)

### Community 12 - "web.py"
Cohesion: 0.11
Nodes (7): Conflict, PayloadTooLarge, ViewerError, ProjectRequest, SuperposeRequest, Api, status_for()

### Community 13 - "pocketbench/__init__.py"
Cohesion: 0.08
Nodes (18): _like(), _move_cif(), _move_pdb(), move_text(), _pdb_coord(), _rotate_u(), cif_loops(), cif_set_tokens() (+10 more)

### Community 14 - "service.py"
Cohesion: 0.11
Nodes (5): detect_format(), safe_token(), StructureBuffer, read(), BufferTest

### Community 15 - "PocketTools"
Cohesion: 0.09
Nodes (12): FpocketDetector, _parse_info(), _parse_vert(), PocketbenchError, ToolError, ToolFailed, ToolUnavailable, first_executable() (+4 more)

### Community 17 - "Session Model State"
Cohesion: 0.11
Nodes (4): free_dir(), _now(), Project, ProjectTest

### Community 18 - "sites"
Cohesion: 0.25
Nodes (4): shell(), sites(), d2(), Superposition

### Community 19 - "E2E Test Setup"
Cohesion: 0.11
Nodes (4): GenerationJob, Generator, PocketTools, Preparer

### Community 20 - "$"
Cohesion: 0.12
Nodes (5): ApiRequest, dumps(), _finite(), Handler, WebTest

### Community 21 - "imports"
Cohesion: 0.28
Nodes (15): How the pieces fit, attr(), frameOf(), bind(), buffer(), fitOn(), message(), movers() (+7 more)

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.17
Nodes (25): clearRun(), commit(), state, subscribe(), views, bind(), bind(), clearPicks() (+17 more)

### Community 23 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.18
Nodes (11): cmd_detectors(), cmd_score(), cmd_site(), cmd_superpose(), _fit_line(), _json(), _key(), parser() (+3 more)

### Community 24 - "Structure"
Cohesion: 0.14
Nodes (5): _f(), LacunaDetector, _read_pseudoatoms(), probe(), test_real_lacuna_nma_on_crambin()

### Community 25 - "BadRequest"
Cohesion: 0.09
Nodes (13): BadRequest, BufferRequest, _residues(), _check_range(), choice(), _get(), integer(), names() (+5 more)

### Community 26 - "Validation"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 27 - "quickprep"
Cohesion: 0.15
Nodes (13): Bound ligands, Fetching other structures, Finding pockets, Getting results out, Layout, Opening your own files, Preparing structures, ProteinViewer (+5 more)

### Community 28 - "FakePockets"
Cohesion: 0.14
Nodes (12): apply_states(), _final_states(), _label(), _merge(), _nm(), _pdb2pqr(), _pka_flags(), PrepError (+4 more)

### Community 29 - "proteinviewer/app/js/scene.js"
Cohesion: 0.14
Nodes (9): check_structure(), imports(), name(), visit(), LayeringTest, module_name(), PackagingTest, top_level_imports() (+1 more)

### Community 30 - "FakeJob"
Cohesion: 0.21
Nodes (6): prepare(), hydrogens(), run(), test_no_rebuild(), test_provenance(), test_zinc_histidines()

### Community 31 - "Once"
Cohesion: 0.13
Nodes (7): 2. What changed, Design decisions worth knowing, _near(), _num(), Pocket, select_residues(), test_whole_residues_and_standard_only()

### Community 32 - "structure.py"
Cohesion: 0.22
Nodes (9): 1. Why, 3. Environments, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed, CPU only (enough for the nma backend), Ensemble pocket detection in ProteinViewer (+1 more)

### Community 34 - ".residue"
Cohesion: 0.24
Nodes (15): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), job(), post(), prepare() (+7 more)

### Community 35 - "drughunter"
Cohesion: 0.22
Nodes (17): save(), liveGen(), bind(), bind(), bindPanel(), csv(), CSV_COLS, downloadCsv() (+9 more)

### Community 36 - "NotFound"
Cohesion: 0.12
Nodes (4): NotFound, ProjectStore, StoreTest, FakeService

### Community 37 - "quickprep/prep.py"
Cohesion: 0.16
Nodes (7): clean(), cli(), _expdta(), _head(), main(), _ranges(), summary()

### Community 38 - "superpose.py"
Cohesion: 0.10
Nodes (11): Method, Superposing structures, align(), _codes(), self_align(), Atom, Chain, _pair_chains() (+3 more)

### Community 39 - "LacunaDetector"
Cohesion: 0.24
Nodes (5): _bonds_of(), _edit(), metal_sites(), _replace_hydrogens(), _ring_h()

### Community 40 - "start.sh"
Cohesion: 0.20
Nodes (7): _chirality(), fix(), _gap_numbers(), _key(), _number_caps(), _one(), refine()

### Community 41 - "Generating binders"
Cohesion: 0.17
Nodes (5): _remarks(), _variant(), _wrap(), write_output(), write_pqr()

### Community 42 - "neat_pocket.py"
Cohesion: 0.09
Nodes (11): collate(), depict(), describe(), generate_for_pocket(), load_models(), load_structure(), main(), flush() (+3 more)

### Community 43 - ".lock"
Cohesion: 0.29
Nodes (7): As a library, Detection, Driven by a program, Install, Layout, pocketbench, Running an apo/holo case

### Community 44 - "4. Backends"
Cohesion: 0.50
Nodes (4): 4. Backends, Detectors, `nma` — default, 2 s, deterministic, `openmm` — 50 s on CUDA, **not reproducible**

### Community 45 - "test_score.py"
Cohesion: 0.20
Nodes (15): main(), atom_line(), ligand_lines(), test_detect_errors_say_whose_problem_they_are(), kind(), test_detectors_lists_options_and_additives(), apo_holo(), lacuna_report() (+7 more)

### Community 46 - "test_detect.py"
Cohesion: 0.09
Nodes (15): Commands, DetectionResult, fake_executable(), brute_force(), fpocket(), run(), pocket(), test_a_rerun_does_not_read_back_an_earlier_runs_pockets() (+7 more)

### Community 48 - "protonate"
Cohesion: 0.33
Nodes (6): Install, quickprep, Tests, Use, Used by, What it does

### Community 49 - "Structure tree"
Cohesion: 0.67
Nodes (3): Showing, hiding and removing, Structure tree, What the operations run on

### Community 50 - "drughunter"
Cohesion: 0.33
Nodes (6): drughunter, Install, Layout, Optional and external tools, Run, Tests

### Community 57 - "QuickPrep"
Cohesion: 0.11
Nodes (5): QuickPrep, first_executable(), Once, probe(), PrepTest

### Community 58 - "ServiceTest"
Cohesion: 0.11
Nodes (5): GenerateRequest, JobQuery, PocketRequest, PrepRequest, ServiceTest

### Community 60 - "ViewerService"
Cohesion: 0.10
Nodes (7): BufferedStructure, csv_text(), _csv_value(), safe_name(), safe_relpath(), ViewerService, NamesTest

### Community 63 - "5. Validation"
Cohesion: 0.50
Nodes (4): 5. Validation, PTP1B — not recovered, for a legible reason, TEM-1 β-lactamase — recovered, The MD backend does not replicate

### Community 69 - "Generating binders"
Cohesion: 0.50
Nodes (4): Endpoints, Generating binders, No reference ligand is needed, The molecule table

## Knowledge Gaps
- **1 isolated node(s):** `drughunter`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 450 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Layout` connect `quickprep` to `DetectionResult`, `imports`?**
  _High betweenness centrality (0.288) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `BufferRequest`) actually correct?**
  _`BadRequest` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `drughunter` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_service.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06971153846153846 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `imports` to `quickprep`, `CSV & Generation Jobs`?**
  _High betweenness centrality (0.284) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `Structure` (e.g. with `cmd_apo()` and `cmd_detect()`) actually correct?**
  _`Structure` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Should `DetectionResult` be split into smaller, more focused modules?**
  _Cohesion score 0.06976744186046512 - nodes in this community are weakly interconnected._