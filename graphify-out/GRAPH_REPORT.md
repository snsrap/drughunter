# Graph Report - drughunter  (2026-10-19)

## Corpus Check
- 95 files · ~94,436 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .pdb 4, (none) 2, .cif 2)

## Summary
- 1407 nodes · 3789 edges · 68 communities (55 shown, 13 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 315 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `70a82b8f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- pocketbench.py
- test_superpose.py
- DetectionResult
- FakePockets
- StructureBuffer
- esc
- ToolFailed
- pocketbench/__init__.py
- BadRequest
- server.py
- Job
- fpocket.py
- test_service.py
- pocketbench/__init__.py
- service.py
- lacuna.py
- ui_snapshot.mjs
- Session Model State
- model/session.js
- E2E Test Setup
- web.py
- NeatPocket
- CSV & Generation Jobs
- Ensemble pocket detection in ProteinViewer
- Structure
- BadRequest
- Validation
- WebTest
- FakePockets
- proteinviewer/app/js/scene.js
- FakeJob
- Once
- structure.py
- test_superpose.py
- .residue
- $
- service.py
- quickprep/prep.py
- superpose.py
- LacunaDetector
- start.sh
- Generating binders
- neat_pocket.py
- pocketbench
- 4. Backends
- test_detect.py
- DetectionResult
- protonate
- FpocketDetector
- drughunter
- ToolUnavailable
- pockets.js
- fetch.js
- test_score.py
- quickprep/prep.py
- QuickPrep
- ViewerService
- Option
- test_pocketbench.py
- Validation
- sys
- 5. Validation
- Once
- FakeJob
- generation.py

## God Nodes (most connected - your core abstractions)
1. `Structure` - 54 edges
2. `BadRequest` - 51 edges
3. `ViewerService` - 47 edges
4. `commit()` - 45 edges
5. `$` - 43 edges
6. `esc()` - 36 edges
7. `LacunaDetector` - 30 edges
8. `ProjectArtifactsTest` - 28 edges
9. `NotFound` - 27 edges
10. `Pocketbench` - 27 edges

## Surprising Connections (you probably didn't know these)
- `Hit criteria` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Running a case` --references--> `score()`  [INFERRED]
  docs/validation/README.md → pocketbench/score.py
- `Method` --references--> `align()`  [INFERRED]
  docs/pocketbench.md → pocketbench/align.py
- `Superposing structures` --references--> `align()`  [INFERRED]
  docs/proteinviewer.md → pocketbench/align.py
- `Adding a detector` --references--> `Option`  [INFERRED]
  docs/pocketbench.md → pocketbench/detectors/__init__.py

## Import Cycles
- None detected.

## Communities (68 total, 13 thin omitted)

### Community 0 - "pocketbench.py"
Cohesion: 0.14
Nodes (6): ToolFailed, first_executable(), Once, output_tail(), probe(), run()

### Community 1 - "test_superpose.py"
Cohesion: 0.14
Nodes (25): sites(), atom_line(), chain_lines(), ligand_lines(), pdb(), trace(), test_residue_specs(), apo_holo() (+17 more)

### Community 2 - "DetectionResult"
Cohesion: 0.11
Nodes (5): Adding a detector, DetectorRegistry, PocketDetector, BadInput, test_the_registry()

### Community 4 - "StructureBuffer"
Cohesion: 0.14
Nodes (12): _lining(), _num(), parse_pockets(), read_pockets(), ReportedPocket, best_rank(), Criteria, PocketScore (+4 more)

### Community 5 - "esc"
Cohesion: 0.19
Nodes (25): attr(), esc(), note(), plural(), openFiles(), report(), adopt(), bind() (+17 more)

### Community 6 - "ToolFailed"
Cohesion: 0.10
Nodes (9): default_project_root(), Settings, build_service(), main(), make_server(), parse_args(), _print_tool_status(), _stop() (+1 more)

### Community 7 - "pocketbench/__init__.py"
Cohesion: 0.11
Nodes (35): gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId(), addEntity() (+27 more)

### Community 8 - "BadRequest"
Cohesion: 0.10
Nodes (24): move_text(), detect_format(), Structure, Transform, pdb_line(), read(), rotation(), test_nothing_left_after_solvent_is_bad_input() (+16 more)

### Community 9 - "server.py"
Cohesion: 0.16
Nodes (29): cssVar(), entColor(), picksOf(), inModel(), hider(), clearLayer(), drawLabels(), ensureModels() (+21 more)

### Community 10 - "Job"
Cohesion: 0.15
Nodes (3): Job, _read_json(), JobProgressTest

### Community 11 - "fpocket.py"
Cohesion: 0.16
Nodes (17): download(), scopeAtoms(), scopeStructure(), leafSels(), leftOut(), pdbSubset(), buildTree(), CAPS (+9 more)

### Community 12 - "test_service.py"
Cohesion: 0.12
Nodes (9): BufferRequest, GenerateRequest, JobQuery, PocketRequest, PrepRequest, ProjectRequest, SuperposeRequest, Api (+1 more)

### Community 13 - "pocketbench/__init__.py"
Cohesion: 0.10
Nodes (13): _like(), _move_cif(), _move_pdb(), _pdb_coord(), _rotate_u(), cif_loops(), cif_set_tokens(), cif_tokens() (+5 more)

### Community 14 - "service.py"
Cohesion: 0.09
Nodes (6): check_structure(), detect_format(), safe_token(), StructureBuffer, read(), BufferTest

### Community 15 - "lacuna.py"
Cohesion: 0.16
Nodes (5): _parse_info(), ToolFailed, first_executable(), output_tail(), run()

### Community 17 - "Session Model State"
Cohesion: 0.11
Nodes (3): free_dir(), safe_relpath(), ProjectTest

### Community 18 - "model/session.js"
Cohesion: 0.17
Nodes (19): boldOnly(), ENT, entity(), fitOf(), flags(), isSessionText(), num(), pocket() (+11 more)

### Community 19 - "E2E Test Setup"
Cohesion: 0.11
Nodes (4): GenerationJob, Generator, PocketTools, Preparer

### Community 20 - "web.py"
Cohesion: 0.09
Nodes (9): Conflict, PayloadTooLarge, ViewerError, ApiRequest, dumps(), _finite(), Handler, status_for() (+1 more)

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.14
Nodes (33): How the pieces fit, clearRun(), commit(), frameOf(), nodeOf(), state, subscribe(), views (+25 more)

### Community 23 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.13
Nodes (15): cmd_apo(), cmd_detect(), cmd_detectors(), cmd_score(), cmd_site(), cmd_superpose(), _fit_line(), _json() (+7 more)

### Community 24 - "Structure"
Cohesion: 0.14
Nodes (5): _f(), LacunaDetector, _read_pseudoatoms(), probe(), test_lacuna_options_defaults_case_and_bad_values()

### Community 25 - "BadRequest"
Cohesion: 0.20
Nodes (9): BadRequest, _residues(), _check_range(), choice(), _get(), integer(), names(), number() (+1 more)

### Community 26 - "Validation"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 27 - "WebTest"
Cohesion: 0.08
Nodes (22): Adding a pocket detector, Bound ligands, Endpoints, Fetching other structures, Finding pockets, Generating binders, Getting results out, Layout (+14 more)

### Community 28 - "FakePockets"
Cohesion: 0.17
Nodes (11): apply_states(), _final_states(), _label(), _merge(), _nm(), _pdb2pqr(), _pka_flags(), protonate() (+3 more)

### Community 29 - "proteinviewer/app/js/scene.js"
Cohesion: 0.16
Nodes (8): imports(), name(), visit(), LayeringTest, module_name(), PackagingTest, top_level_imports(), version()

### Community 30 - "FakeJob"
Cohesion: 0.21
Nodes (6): prepare(), hydrogens(), run(), test_no_rebuild(), test_provenance(), test_zinc_histidines()

### Community 31 - "Once"
Cohesion: 0.21
Nodes (4): 2. What changed, Design decisions worth knowing, _num(), Pocket

### Community 32 - "structure.py"
Cohesion: 0.22
Nodes (9): 1. Why, 3. Environments, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed, CPU only (enough for the nma backend), Ensemble pocket detection in ProteinViewer (+1 more)

### Community 33 - "test_superpose.py"
Cohesion: 0.13
Nodes (3): FakeGenerator, FakePrep, ProjectArtifactsTest

### Community 34 - ".residue"
Cohesion: 0.15
Nodes (29): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), job(), post(), prepare() (+21 more)

### Community 35 - "$"
Cohesion: 0.13
Nodes (36): POCKET_COLORS, pocketColor(), rankColor(), $, setHTML(), setMsg(), save(), entity() (+28 more)

### Community 36 - "service.py"
Cohesion: 0.07
Nodes (12): residue_list(), spheres_pdb(), csv_text(), _csv_value(), _now(), Project, ProjectStore, resolve_destination() (+4 more)

### Community 37 - "quickprep/prep.py"
Cohesion: 0.15
Nodes (8): clean(), cli(), _expdta(), _head(), main(), PrepError, _ranges(), summary()

### Community 38 - "superpose.py"
Cohesion: 0.07
Nodes (18): Method, Superposing structures, align(), _codes(), self_align(), shell(), Atom, Chain (+10 more)

### Community 39 - "LacunaDetector"
Cohesion: 0.25
Nodes (4): _bonds_of(), _replace_hydrogens(), _ring_h(), _variant()

### Community 40 - "start.sh"
Cohesion: 0.17
Nodes (9): _chirality(), _edit(), fix(), _gap_numbers(), _key(), metal_sites(), _number_caps(), _one() (+1 more)

### Community 41 - "Generating binders"
Cohesion: 0.20
Nodes (4): _remarks(), _wrap(), write_output(), write_pqr()

### Community 42 - "neat_pocket.py"
Cohesion: 0.09
Nodes (11): collate(), depict(), describe(), generate_for_pocket(), load_models(), load_structure(), main(), flush() (+3 more)

### Community 43 - "pocketbench"
Cohesion: 0.14
Nodes (12): As a library, Commands, Detection, Driven by a program, Install, Layout, pocketbench, Running an apo/holo case (+4 more)

### Community 44 - "4. Backends"
Cohesion: 0.50
Nodes (4): 4. Backends, Detectors, `nma` — default, 2 s, deterministic, `openmm` — 50 s on CUDA, **not reproducible**

### Community 45 - "test_detect.py"
Cohesion: 0.13
Nodes (12): _near(), select_residues(), fake_executable(), brute_force(), test_chain_is_part_of_residue_identity(), test_detect_answers_in_json(), test_detect_errors_say_whose_problem_they_are(), kind() (+4 more)

### Community 46 - "DetectionResult"
Cohesion: 0.22
Nodes (5): DetectionResult, pocket(), test_a_selection_is_grouped_by_chain(), test_result_meta_carries_detector_extras(), test_unreported_numbers_go_out_as_null()

### Community 48 - "protonate"
Cohesion: 0.33
Nodes (6): Install, quickprep, Tests, Use, Used by, What it does

### Community 49 - "FpocketDetector"
Cohesion: 0.18
Nodes (6): FpocketDetector, _parse_vert(), PocketbenchError, ToolError, ToolUnavailable, test_a_missing_binary()

### Community 50 - "drughunter"
Cohesion: 0.33
Nodes (6): drughunter, Install, Layout, Optional and external tools, Run, Tests

### Community 51 - "ToolUnavailable"
Cohesion: 0.13
Nodes (3): ToolError, ToolUnavailable, FakeService

### Community 52 - "pockets.js"
Cohesion: 0.29
Nodes (12): bind(), ligands(), message(), parseMethod(), pocketRow(), rankedBy(), render(), runLine() (+4 more)

### Community 57 - "QuickPrep"
Cohesion: 0.23
Nodes (3): QuickPrep, residue_ranges(), PrepTest

### Community 58 - "ViewerService"
Cohesion: 0.08
Nodes (6): BufferedStructure, PocketRun, NotFound, ProjectSaveRequest, ViewerService, ServiceTest

### Community 60 - "test_pocketbench.py"
Cohesion: 0.20
Nodes (3): residue_spec(), fake_executable(), ResidueSpecTest

### Community 61 - "Validation"
Cohesion: 0.33
Nodes (6): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation

### Community 63 - "5. Validation"
Cohesion: 0.50
Nodes (4): 5. Validation, PTP1B — not recovered, for a legible reason, TEM-1 β-lactamase — recovered, The MD backend does not replicate

### Community 72 - "generation.py"
Cohesion: 0.18
Nodes (3): find_neat_dir(), find_python(), _last_json_line()

## Knowledge Gaps
- **1 isolated node(s):** `drughunter`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 446 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Layout` connect `WebTest` to `CSV & Generation Jobs`?**
  _High betweenness centrality (0.332) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `Structure` (e.g. with `cmd_apo()` and `cmd_detect()`) actually correct?**
  _`Structure` has 29 INFERRED edges - model-reasoned connections that need verification._
- **What connects `drughunter` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `pocketbench.py` be split into smaller, more focused modules?**
  _Cohesion score 0.13675213675213677 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `CSV & Generation Jobs` to `WebTest`?**
  _High betweenness centrality (0.325) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `Pocketbench`) actually correct?**
  _`BadRequest` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Should `test_superpose.py` be split into smaller, more focused modules?**
  _Cohesion score 0.13636363636363635 - nodes in this community are weakly interconnected._