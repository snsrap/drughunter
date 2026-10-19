# Graph Report - ProteinViewer  (2026-10-18)

## Corpus Check
- 76 files · ~539,783 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .pdb 3, .cif 2, (none) 1)

## Summary
- 1039 nodes · 2877 edges · 40 communities (32 shown, 8 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 257 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `47fbc05d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ServiceTest
- Structure Buffering & Locking
- Lacuna Detector Backend
- Detector Registry Interface
- Lacuna Integration Docs
- service.py
- superpose.test.js
- lacuna.py
- Neat Pocket Job
- HTTP Request Handler
- Scene Colors & Pockets
- Server Configuration
- Pocket Scoring & Prep
- Structure Load & Generate
- read
- File Fetch & RCSB
- StructureBuffer
- Session Model State
- Feature Tree UI
- E2E Test Setup
- resolve_destination
- Structure & Atom Parsing
- CSV & Generation Jobs
- Fpocket Detector
- ViewerService
- Pocket Table UI
- Pockets Panel UI
- API Client
- ToolUnavailable
- App State & Bootstrap
- $
- Test Fixtures & Helpers
- Startup Script
- ProteinViewer Root
- BadRequest
- PocketDetector
- first_executable
- FakeJob
- manifest.json

## God Nodes (most connected - your core abstractions)
1. `BadRequest` - 49 edges
2. `commit()` - 45 edges
3. `ViewerService` - 45 edges
4. `$` - 42 edges
5. `esc()` - 36 edges
6. `Structure` - 36 edges
7. `LacunaDetector` - 30 edges
8. `ProjectArtifactsTest` - 28 edges
9. `StructureBuffer` - 27 edges
10. `NotFound` - 27 edges

## Surprising Connections (you probably didn't know these)
- `Superposing structures` --references--> `align()`  [INFERRED]
  README.md → app/js/model/superpose.js
- `How the pieces fit` --references--> `commit()`  [INFERRED]
  README.md → app/js/core/state.js
- `Adding a pocket detector` --references--> `PocketDetector`  [INFERRED]
  README.md → proteinviewer/detectors/__init__.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → proteinviewer/detectors/fpocket.py
- `Adding a pocket detector` --references--> `DetectionResult`  [INFERRED]
  README.md → proteinviewer/pockets.py

## Import Cycles
- None detected.

## Communities (40 total, 8 thin omitted)

### Community 0 - "ServiceTest"
Cohesion: 0.11
Nodes (3): PocketRequest, SchemaTest, ServiceTest

### Community 1 - "Structure Buffering & Locking"
Cohesion: 0.06
Nodes (10): residue_list(), spheres_pdb(), free_dir(), _now(), Project, safe_name(), safe_relpath(), NamesTest (+2 more)

### Community 2 - "Lacuna Detector Backend"
Cohesion: 0.11
Nodes (7): _f(), LacunaDetector, main(), _read_pseudoatoms(), probe(), LacunaOptionsTest, RealLacunaTest

### Community 3 - "Detector Registry Interface"
Cohesion: 0.10
Nodes (7): 2. What changed, Design decisions worth knowing, DetectionResult, _num(), Pocket, PocketTest, FakeDetector

### Community 4 - "Lacuna Integration Docs"
Cohesion: 0.04
Nodes (45): 1. Why, 3. Environments, 4. Backends, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed (+37 more)

### Community 5 - "service.py"
Cohesion: 0.08
Nodes (13): PocketRun, safe_token(), Conflict, NotFound, ViewerError, ProjectStore, BufferRequest, GenerateRequest (+5 more)

### Community 6 - "superpose.test.js"
Cohesion: 0.06
Nodes (61): downloadPockets(), scopeAtoms(), scopeStructure(), CIF_EXT, cifFirstModel(), cifTitle(), firstModel(), PDB_EXT (+53 more)

### Community 7 - "lacuna.py"
Cohesion: 0.12
Nodes (5): _parse_info(), find_python(), output_tail(), run(), _element_from_name()

### Community 8 - "Neat Pocket Job"
Cohesion: 0.07
Nodes (7): find_neat_dir(), Job, _last_json_line(), NeatPocket, _read_json(), JobProgressTest, RunnerTest

### Community 9 - "HTTP Request Handler"
Cohesion: 0.07
Nodes (7): PayloadTooLarge, ApiRequest, dumps(), _finite(), Handler, FakeService, WebTest

### Community 10 - "Scene Colors & Pockets"
Cohesion: 0.15
Nodes (36): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), picksOf() (+28 more)

### Community 11 - "Server Configuration"
Cohesion: 0.11
Nodes (9): default_project_root(), Settings, build_service(), main(), make_server(), parse_args(), _print_tool_status(), _stop() (+1 more)

### Community 12 - "Pocket Scoring & Prep"
Cohesion: 0.09
Nodes (13): main(), parse(), kabsch(), main(), parse(), post(), Adding a pocket detector, imports() (+5 more)

### Community 13 - "Structure Load & Generate"
Cohesion: 0.23
Nodes (26): job(), projectStatus(), esc(), fault(), note(), commit(), bind(), finish() (+18 more)

### Community 14 - "read"
Cohesion: 0.07
Nodes (11): BufferedStructure, _near(), select_residues(), detect_format(), pdb_line(), read(), BufferTest, brute_force() (+3 more)

### Community 15 - "File Fetch & RCSB"
Cohesion: 0.17
Nodes (22): ENT_COLORS, gunzip(), hasFiles(), readText(), cache, fetchEntry(), getText(), isPdbId() (+14 more)

### Community 16 - "StructureBuffer"
Cohesion: 0.09
Nodes (5): StructureBuffer, DetectorRegistry, RegistryTest, FakeGenerator, FakePrep

### Community 17 - "Session Model State"
Cohesion: 0.16
Nodes (20): boldOnly(), ENT, entity(), fitOf(), flags(), num(), pocket(), point() (+12 more)

### Community 18 - "Feature Tree UI"
Cohesion: 0.27
Nodes (17): clearRun(), bind(), clearPicks(), focusRow(), fold(), forget(), pick(), reload() (+9 more)

### Community 19 - "E2E Test Setup"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 21 - "Structure & Atom Parsing"
Cohesion: 0.11
Nodes (3): Atom, _split_cif(), Structure

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.14
Nodes (5): csv_text(), _csv_value(), GenerationJob, Generator, Preparer

### Community 23 - "Fpocket Detector"
Cohesion: 0.20
Nodes (5): FpocketDetector, main(), _parse_vert(), FpocketTest, RealFpocketTest

### Community 24 - "ViewerService"
Cohesion: 0.15
Nodes (3): ProjectSaveRequest, ViewerService, ProjectArtifactsTest

### Community 25 - "Pocket Table UI"
Cohesion: 0.24
Nodes (16): save(), liveGen(), bind(), bindPanel(), csv(), CSV_COLS, downloadCsv(), downloadSdf() (+8 more)

### Community 26 - "Pockets Panel UI"
Cohesion: 0.19
Nodes (17): bind(), find(), ligands(), message(), parseMethod(), pocketRow(), rankedBy(), render() (+9 more)

### Community 27 - "API Client"
Cohesion: 0.29
Nodes (12): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), post(), prepare(), request() (+4 more)

### Community 28 - "ToolUnavailable"
Cohesion: 0.15
Nodes (6): ToolError, ToolFailed, ToolUnavailable, QuickPrep, residue_ranges(), PrepTest

### Community 29 - "App State & Bootstrap"
Cohesion: 0.25
Nodes (11): nodeOf(), state, subscribe(), views, frames(), render(), bind(), FEATURES (+3 more)

### Community 30 - "$"
Cohesion: 0.21
Nodes (23): $, attr(), plural(), setHTML(), setMsg(), frameOf(), render(), render() (+15 more)

### Community 35 - "BadRequest"
Cohesion: 0.23
Nodes (8): BadRequest, _check_range(), choice(), _get(), integer(), names(), number(), text()

### Community 39 - "manifest.json"
Cohesion: 0.40
Nodes (4): api, artifacts, created, project

## Knowledge Gaps
- **1 isolated node(s):** `proteinviewer`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 295 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `commit()` connect `Structure Load & Generate` to `Lacuna Integration Docs`, `superpose.test.js`, `Scene Colors & Pockets`, `File Fetch & RCSB`, `Feature Tree UI`, `Pocket Table UI`, `Pockets Panel UI`, `App State & Bootstrap`, `$`?**
  _High betweenness centrality (0.407) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `DetectorRegistry`) actually correct?**
  _`BadRequest` has 17 INFERRED edges - model-reasoned connections that need verification._
- **What connects `proteinviewer` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ServiceTest` be split into smaller, more focused modules?**
  _Cohesion score 0.10837438423645321 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `Lacuna Integration Docs` to `ServiceTest`, `Structure Load & Generate`?**
  _High betweenness centrality (0.399) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `ViewerService` (e.g. with `make_server()` and `_print_tool_status()`) actually correct?**
  _`ViewerService` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Should `Structure Buffering & Locking` be split into smaller, more focused modules?**
  _Cohesion score 0.06428988895382817 - nodes in this community are weakly interconnected._