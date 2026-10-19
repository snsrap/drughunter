# Graph Report - ProteinViewer  (2026-10-19)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 896 nodes · 2445 edges · 43 communities (30 shown, 13 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 182 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a5538c06`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- commit
- Structure Buffering & Locking
- Project
- Handler
- StructureBuffer
- NeatPocket
- model.test.js
- service.py
- Neat Pocket Job
- HTTP Request Handler
- Scene Colors & Pockets
- Server Configuration
- model/session.js
- pocketbench.py
- errors.py
- PocketTools
- ui_snapshot.mjs
- Session Model State
- generation.py
- E2E Test Setup
- ServiceTest
- imports
- CSV & Generation Jobs
- Ensemble pocket detection in ProteinViewer
- .setUp
- helpers.py
- Validation
- ProteinViewer
- FakePockets
- pockets.py
- FakeJob
- manifest.json
- Startup Script
- ProteinViewer Root
- Tests Package Init
- Layout
- Generating binders
- Finding pockets
- Structure tree
- start.sh
- proteinviewer

## God Nodes (most connected - your core abstractions)
1. `commit()` - 45 edges
2. `ViewerService` - 42 edges
3. `$` - 42 edges
4. `BadRequest` - 38 edges
5. `esc()` - 36 edges
6. `ProjectArtifactsTest` - 25 edges
7. `Project` - 24 edges
8. `StructureBuffer` - 24 edges
9. `liveRun()` - 24 edges
10. `Pocketbench` - 23 edges

## Surprising Connections (you probably didn't know these)
- `How the pieces fit` --references--> `commit()`  [INFERRED]
  README.md → app/js/core/state.js
- `How the pieces fit` --references--> `buffer()`  [INFERRED]
  README.md → app/js/features/superpose.js
- `BufferTest` --uses--> `NotFound`  [INFERRED]
  tests/test_buffer.py → proteinviewer/errors.py
- `StoreTest` --uses--> `NotFound`  [INFERRED]
  tests/test_project.py → proteinviewer/errors.py
- `FakeService` --uses--> `NotFound`  [INFERRED]
  tests/test_web.py → proteinviewer/errors.py

## Import Cycles
- None detected.

## Communities (43 total, 13 thin omitted)

### Community 0 - "commit"
Cohesion: 0.05
Nodes (129): API_VERSION, bufferStructure(), createProject(), findPockets(), isJSON(), job(), post(), prepare() (+121 more)

### Community 1 - "Structure Buffering & Locking"
Cohesion: 0.10
Nodes (54): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveGen(), liveRun() (+46 more)

### Community 2 - "Project"
Cohesion: 0.07
Nodes (8): free_dir(), Project, ProjectStore, resolve_destination(), safe_relpath(), DestinationTest, ProjectTest, StoreTest

### Community 3 - "Handler"
Cohesion: 0.07
Nodes (7): ApiRequest, dumps(), _finite(), Handler, status_for(), FakeService, WebTest

### Community 4 - "StructureBuffer"
Cohesion: 0.09
Nodes (7): BufferedStructure, check_structure(), detect_format(), safe_token(), StructureBuffer, read(), BufferTest

### Community 5 - "NeatPocket"
Cohesion: 0.09
Nodes (4): Job, NeatPocket, JobProgressTest, RunnerTest

### Community 6 - "model.test.js"
Cohesion: 0.10
Nodes (29): CIF_EXT, cifFirstModel(), cifTitle(), firstModel(), PDB_EXT, pdbFirstModel(), tidy(), titleOf() (+21 more)

### Community 7 - "service.py"
Cohesion: 0.13
Nodes (10): Conflict, ViewerError, BufferRequest, GenerateRequest, JobQuery, PocketRequest, PrepRequest, ProjectRequest (+2 more)

### Community 8 - "Neat Pocket Job"
Cohesion: 0.14
Nodes (11): BadRequest, PayloadTooLarge, _residues(), _check_range(), choice(), _get(), integer(), names() (+3 more)

### Community 9 - "HTTP Request Handler"
Cohesion: 0.11
Nodes (9): default_project_root(), Settings, build_service(), main(), make_server(), parse_args(), _print_tool_status(), _stop() (+1 more)

### Community 11 - "Server Configuration"
Cohesion: 0.12
Nodes (4): PocketRun, NotFound, safe_name(), ViewerService

### Community 12 - "model/session.js"
Cohesion: 0.16
Nodes (22): boldOnly(), ENT, entity(), fitOf(), flags(), isSessionText(), num(), pocket() (+14 more)

### Community 13 - "pocketbench.py"
Cohesion: 0.14
Nodes (4): first_executable(), output_tail(), probe(), run()

### Community 14 - "errors.py"
Cohesion: 0.16
Nodes (3): ToolError, ToolFailed, ToolUnavailable

### Community 15 - "PocketTools"
Cohesion: 0.11
Nodes (4): GenerationJob, Generator, PocketTools, Preparer

### Community 16 - "ui_snapshot.mjs"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 17 - "Session Model State"
Cohesion: 0.16
Nodes (4): QuickPrep, residue_ranges(), Once, PrepTest

### Community 18 - "generation.py"
Cohesion: 0.14
Nodes (5): post(), find_neat_dir(), find_python(), _last_json_line(), _read_json()

### Community 21 - "imports"
Cohesion: 0.27
Nodes (5): imports(), name(), visit(), LayeringTest, module_name()

### Community 22 - "CSV & Generation Jobs"
Cohesion: 0.21
Nodes (4): csv_text(), _csv_value(), _now(), NamesTest

### Community 23 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.18
Nodes (11): 1. Why, 2. What changed, 3. Environments, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed, CPU only (enough for the nma backend) (+3 more)

### Community 26 - "Validation"
Cohesion: 0.22
Nodes (6): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation

### Community 27 - "ProteinViewer"
Cohesion: 0.22
Nodes (9): Fetching other structures, Getting results out, Opening your own files, Preparing structures, ProteinViewer, Run, Saving a session, Superposing structures (+1 more)

### Community 31 - "manifest.json"
Cohesion: 0.40
Nodes (4): api, artifacts, created, project

### Community 32 - "Startup Script"
Cohesion: 0.50
Nodes (4): 4. Backends, Detectors, `nma` — default, 2 s, deterministic, `openmm` — 50 s on CUDA, **not reproducible**

### Community 33 - "ProteinViewer Root"
Cohesion: 0.50
Nodes (4): 5. Validation, PTP1B — not recovered, for a legible reason, TEM-1 β-lactamase — recovered, The MD backend does not replicate

### Community 35 - "Layout"
Cohesion: 0.50
Nodes (4): Adding a pocket detector, How the pieces fit, Layout, Tests

### Community 36 - "Generating binders"
Cohesion: 0.50
Nodes (4): Endpoints, Generating binders, No reference ligand is needed, The molecule table

### Community 38 - "Finding pockets"
Cohesion: 0.67
Nodes (3): Bound ligands, Finding pockets, What you see

### Community 39 - "Structure tree"
Cohesion: 0.67
Nodes (3): Showing, hiding and removing, Structure tree, What the operations run on

## Knowledge Gaps
- **1 isolated node(s):** `proteinviewer`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 257 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ProteinViewer` connect `ProteinViewer` to `Layout`, `Generating binders`, `Finding pockets`, `Structure tree`, `Validation`?**
  _High betweenness centrality (0.459) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `ViewerService` (e.g. with `make_server()` and `_print_tool_status()`) actually correct?**
  _`ViewerService` has 17 INFERRED edges - model-reasoned connections that need verification._
- **What connects `proteinviewer` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `commit` be split into smaller, more focused modules?**
  _Cohesion score 0.05239463601532567 - nodes in this community are weakly interconnected._
- **Why does `Generating binders` connect `Generating binders` to `ProteinViewer`?**
  _High betweenness centrality (0.433) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `BadRequest` (e.g. with `DestinationTest` and `NamesTest`) actually correct?**
  _`BadRequest` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `Structure Buffering & Locking` be split into smaller, more focused modules?**
  _Cohesion score 0.09800362976406533 - nodes in this community are weakly interconnected._