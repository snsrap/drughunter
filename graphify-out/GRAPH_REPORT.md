# Graph Report - ProteinViewer  (2026-10-18)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 984 nodes · 2673 edges · 35 communities (31 shown, 4 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 223 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6690c42b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ProjectArtifactsTest
- features/tree.js
- Ensemble pocket detection in ProteinViewer
- NeatPocket
- json
- service.py
- LacunaDetector
- lacuna.py
- start.sh
- Structure
- server.py
- server.py
- test_architecture.py
- esc
- model/session.js
- fetch.js
- ui_snapshot.mjs
- BadRequest
- commit
- ui_snapshot.mjs
- project.py
- QuickPrep
- GenerationJob
- api.js
- .from_text
- table.js
- pockets.js
- FakeGenerator
- helpers.py
- .options
- $
- helpers.py
- start.sh
- start.sh

## God Nodes (most connected - your core abstractions)
1. `commit()` - 42 edges
2. `BadRequest` - 39 edges
3. `ViewerService` - 39 edges
4. `$` - 39 edges
5. `Structure` - 36 edges
6. `esc()` - 32 edges
7. `LacunaDetector` - 30 edges
8. `ProjectArtifactsTest` - 25 edges
9. `Project` - 25 edges
10. `StructureBuffer` - 24 edges

## Surprising Connections (you probably didn't know these)
- `Adding a pocket detector` --references--> `PocketDetector`  [INFERRED]
  README.md → proteinviewer/detectors/__init__.py
- `Adding a pocket detector` --references--> `DetectionResult`  [INFERRED]
  README.md → proteinviewer/pockets.py
- `Adding a pocket detector` --references--> `build_service()`  [INFERRED]
  README.md → proteinviewer/server.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → proteinviewer/detectors/fpocket.py
- `How the pieces fit` --references--> `commit()`  [INFERRED]
  README.md → app/js/core/state.js

## Import Cycles
- None detected.

## Communities (35 total, 4 thin omitted)

### Community 0 - "ProjectArtifactsTest"
Cohesion: 0.05
Nodes (13): PocketRun, _check_range(), choice(), _get(), integer(), names(), number(), text() (+5 more)

### Community 1 - "features/tree.js"
Cohesion: 0.06
Nodes (10): BufferedStructure, free_dir(), _now(), Project, ProjectStore, safe_name(), safe_relpath(), NamesTest (+2 more)

### Community 2 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.07
Nodes (11): _f(), LacunaDetector, main(), _read_pseudoatoms(), QuickPrep, residue_ranges(), Once, probe() (+3 more)

### Community 3 - "NeatPocket"
Cohesion: 0.06
Nodes (9): 2. What changed, Design decisions worth knowing, DetectorRegistry, PocketDetector, DetectionResult, _num(), Pocket, PocketTest (+1 more)

### Community 4 - "json"
Cohesion: 0.04
Nodes (44): 1. Why, 3. Environments, 4. Backends, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed (+36 more)

### Community 5 - "service.py"
Cohesion: 0.12
Nodes (14): BadRequest, Conflict, PayloadTooLarge, ViewerError, BufferRequest, GenerateRequest, JobQuery, PocketRequest (+6 more)

### Community 6 - "LacunaDetector"
Cohesion: 0.10
Nodes (34): scopeAtoms(), scopeStructure(), CIF_EXT, cifFirstModel(), cifTitle(), firstModel(), PDB_EXT, pdbFirstModel() (+26 more)

### Community 7 - "lacuna.py"
Cohesion: 0.10
Nodes (7): ToolError, ToolFailed, ToolUnavailable, _last_json_line(), first_executable(), output_tail(), run()

### Community 8 - "start.sh"
Cohesion: 0.08
Nodes (6): find_neat_dir(), Job, NeatPocket, _read_json(), JobProgressTest, RunnerTest

### Community 9 - "Structure"
Cohesion: 0.07
Nodes (7): ApiRequest, dumps(), _finite(), Handler, status_for(), FakeService, WebTest

### Community 10 - "server.py"
Cohesion: 0.15
Nodes (35): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), picksOf() (+27 more)

### Community 11 - "server.py"
Cohesion: 0.09
Nodes (9): default_project_root(), Settings, build_service(), main(), make_server(), parse_args(), _print_tool_status(), _stop() (+1 more)

### Community 12 - "test_architecture.py"
Cohesion: 0.09
Nodes (13): main(), parse(), kabsch(), main(), parse(), post(), Adding a pocket detector, imports() (+5 more)

### Community 13 - "esc"
Cohesion: 0.19
Nodes (26): bufferStructure(), ENT_COLORS, esc(), fault(), note(), finish(), load(), poll() (+18 more)

### Community 14 - "model/session.js"
Cohesion: 0.13
Nodes (4): safe_token(), StructureBuffer, read(), BufferTest

### Community 15 - "fetch.js"
Cohesion: 0.17
Nodes (22): plural(), gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId() (+14 more)

### Community 16 - "ui_snapshot.mjs"
Cohesion: 0.13
Nodes (6): _parse_info(), find_python(), residue_list(), spheres_pdb(), _element_from_name(), RegistryTest

### Community 17 - "BadRequest"
Cohesion: 0.17
Nodes (20): boldOnly(), ENT, entity(), flags(), num(), pocket(), point(), residueMap() (+12 more)

### Community 18 - "commit"
Cohesion: 0.23
Nodes (22): attr(), clearRun(), commit(), nodeOf(), bind(), clearPicks(), focusRow(), fold() (+14 more)

### Community 19 - "ui_snapshot.mjs"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 20 - "project.py"
Cohesion: 0.14
Nodes (3): NotFound, resolve_destination(), DestinationTest

### Community 21 - "QuickPrep"
Cohesion: 0.11
Nodes (3): Atom, _split_cif(), Structure

### Community 22 - "GenerationJob"
Cohesion: 0.13
Nodes (5): csv_text(), _csv_value(), GenerationJob, Generator, Preparer

### Community 23 - "api.js"
Cohesion: 0.18
Nodes (5): FpocketDetector, main(), _parse_vert(), FpocketTest, RealFpocketTest

### Community 24 - ".from_text"
Cohesion: 0.18
Nodes (4): detect_format(), pdb_line(), ParseTest, ReceptorTest

### Community 25 - "table.js"
Cohesion: 0.22
Nodes (17): save(), liveGen(), bind(), bind(), bindPanel(), csv(), CSV_COLS, downloadCsv() (+9 more)

### Community 26 - "pockets.js"
Cohesion: 0.27
Nodes (14): bind(), find(), ligands(), message(), parseMethod(), pocketRow(), rankedBy(), render() (+6 more)

### Community 27 - "FakeGenerator"
Cohesion: 0.27
Nodes (13): API_VERSION, createProject(), findPockets(), isJSON(), job(), post(), prepare(), projectStatus() (+5 more)

### Community 28 - "helpers.py"
Cohesion: 0.21
Nodes (4): _near(), select_residues(), brute_force(), SelectResiduesTest

### Community 29 - ".options"
Cohesion: 0.29
Nodes (7): state, subscribe(), views, bind(), FEATURES, probeServer(), start()

### Community 30 - "$"
Cohesion: 0.49
Nodes (9): $, setHTML(), setMsg(), render(), renderKey(), render(), render(), render() (+1 more)

## Knowledge Gaps
- **1 isolated node(s):** `proteinviewer`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 282 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `commit()` connect `commit` to `json`, `server.py`, `esc`, `fetch.js`, `table.js`, `pockets.js`, `.options`?**
  _High betweenness centrality (0.370) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `DetectorRegistry`) actually correct?**
  _`BadRequest` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `proteinviewer` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ProjectArtifactsTest` be split into smaller, more focused modules?**
  _Cohesion score 0.05017543859649123 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `json` to `ProjectArtifactsTest`, `commit`?**
  _High betweenness centrality (0.367) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `ViewerService` (e.g. with `make_server()` and `_print_tool_status()`) actually correct?**
  _`ViewerService` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Should `features/tree.js` be split into smaller, more focused modules?**
  _Cohesion score 0.05961538461538462 - nodes in this community are weakly interconnected._