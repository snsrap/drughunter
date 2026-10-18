# Graph Report - ProteinViewer  (2026-10-18)

## Corpus Check
- 59 files · ~35,072 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 1, .css 1, .cif 1)

## Summary
- 773 nodes · 2016 edges · 33 communities (26 shown, 7 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 184 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b2c257a8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- lacuna.py
- LacunaDetector
- scene.js
- ToolUnavailable
- ProteinViewer
- BadRequest
- server.py
- web.py
- start.sh
- score.py
- state.js
- model.test.js
- PocketDetector
- read
- $
- ui_snapshot.mjs
- features/tree.js
- NotFound
- Structure
- .from_text
- ServiceTest
- ViewerService
- FpocketDetector
- pockets.js
- helpers.py
- select_residues
- GenerationJob
- api.js
- FakeGenerator
- generate.js
- PayloadTooLarge
- proteinviewer

## God Nodes (most connected - your core abstractions)
1. `BadRequest` - 40 edges
2. `Structure` - 36 edges
3. `$` - 34 edges
4. `commit()` - 31 edges
5. `LacunaDetector` - 30 edges
6. `ViewerService` - 29 edges
7. `StructureBuffer` - 24 edges
8. `ToolUnavailable` - 24 edges
9. `esc()` - 23 edges
10. `PocketDetector` - 22 edges

## Surprising Connections (you probably didn't know these)
- `How the pieces fit` --references--> `commit()`  [INFERRED]
  README.md → app/js/core/state.js
- `Adding a pocket detector` --references--> `PocketDetector`  [INFERRED]
  README.md → proteinviewer/detectors/__init__.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → proteinviewer/detectors/fpocket.py
- `Adding a pocket detector` --references--> `DetectionResult`  [INFERRED]
  README.md → proteinviewer/pockets.py
- `Design decisions worth knowing` --references--> `select_residues()`  [INFERRED]
  docs/lacuna-integration.md → proteinviewer/pockets.py

## Import Cycles
- None detected.

## Communities (33 total, 7 thin omitted)

### Community 0 - "lacuna.py"
Cohesion: 0.12
Nodes (5): _parse_info(), ToolFailed, first_executable(), run(), _element_from_name()

### Community 1 - "LacunaDetector"
Cohesion: 0.08
Nodes (8): _f(), LacunaDetector, main(), _read_pseudoatoms(), Once, probe(), LacunaOptionsTest, RealLacunaTest

### Community 2 - "scene.js"
Cohesion: 0.16
Nodes (33): cssVar(), entColor(), POCKET_COLORS, pocketColor(), rankColor(), entity(), liveRun(), picksOf() (+25 more)

### Community 3 - "ToolUnavailable"
Cohesion: 0.05
Nodes (13): ToolUnavailable, find_neat_dir(), find_python(), Job, _last_json_line(), NeatPocket, _read_json(), QuickPrep (+5 more)

### Community 4 - "ProteinViewer"
Cohesion: 0.05
Nodes (40): 1. Why, 3. Environments, 4. Backends, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed (+32 more)

### Community 5 - "BadRequest"
Cohesion: 0.17
Nodes (9): BadRequest, _check_range(), choice(), _get(), integer(), names(), number(), text() (+1 more)

### Community 6 - "server.py"
Cohesion: 0.06
Nodes (14): Settings, build_service(), main(), make_server(), parse_args(), _print_tool_status(), _stop(), tool_status_lines() (+6 more)

### Community 7 - "web.py"
Cohesion: 0.18
Nodes (6): BufferRequest, GenerateRequest, JobQuery, PocketRequest, PrepRequest, Api

### Community 9 - "score.py"
Cohesion: 0.10
Nodes (12): main(), parse(), kabsch(), main(), parse(), post(), Adding a pocket detector, imports() (+4 more)

### Community 10 - "state.js"
Cohesion: 0.17
Nodes (19): ENT_COLORS, cache, fetchEntry(), getText(), isPdbId(), addEntity(), freeId(), state (+11 more)

### Community 11 - "model.test.js"
Cohesion: 0.16
Nodes (19): scopeStructure(), summarize(), cifSubset(), inModel(), leafSels(), leftOut(), pdbSubset(), buildTree() (+11 more)

### Community 12 - "PocketDetector"
Cohesion: 0.06
Nodes (10): 2. What changed, Design decisions worth knowing, DetectorRegistry, PocketDetector, DetectionResult, _num(), Pocket, RegistryTest (+2 more)

### Community 13 - "read"
Cohesion: 0.13
Nodes (4): BufferedStructure, safe_token(), read(), BufferTest

### Community 14 - "$"
Cohesion: 0.20
Nodes (20): $, setHTML(), setMsg(), liveGen(), render(), renderKey(), render(), bind() (+12 more)

### Community 15 - "ui_snapshot.mjs"
Cohesion: 0.10
Nodes (17): description, devDependencies, playwright-core, name, private, scripts, e2e, test (+9 more)

### Community 16 - "features/tree.js"
Cohesion: 0.26
Nodes (20): attr(), clearRun(), commit(), nodeOf(), bind(), clearPicks(), focusRow(), fold() (+12 more)

### Community 17 - "NotFound"
Cohesion: 0.11
Nodes (5): Conflict, NotFound, ToolError, ViewerError, FakeService

### Community 18 - "Structure"
Cohesion: 0.11
Nodes (3): Atom, _split_cif(), Structure

### Community 19 - ".from_text"
Cohesion: 0.18
Nodes (4): detect_format(), pdb_line(), ParseTest, ReceptorTest

### Community 21 - "ViewerService"
Cohesion: 0.13
Nodes (3): StructureBuffer, ViewerService, FakePrep

### Community 22 - "FpocketDetector"
Cohesion: 0.22
Nodes (5): FpocketDetector, main(), _parse_vert(), FpocketTest, RealFpocketTest

### Community 23 - "pockets.js"
Cohesion: 0.32
Nodes (13): esc(), bind(), find(), message(), parseMethod(), pocketRow(), rankedBy(), render() (+5 more)

### Community 25 - "select_residues"
Cohesion: 0.21
Nodes (4): _near(), select_residues(), brute_force(), SelectResiduesTest

### Community 26 - "GenerationJob"
Cohesion: 0.20
Nodes (3): GenerationJob, Generator, Preparer

### Community 27 - "api.js"
Cohesion: 0.35
Nodes (10): API_VERSION, bufferStructure(), findPockets(), isJSON(), post(), prepare(), request(), StaleServerError (+2 more)

### Community 29 - "generate.js"
Cohesion: 0.58
Nodes (8): job(), fault(), note(), finish(), load(), poll(), run(), message()

## Knowledge Gaps
- **1 isolated node(s):** `proteinviewer`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 233 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `commit()` connect `features/tree.js` to `ProteinViewer`, `state.js`, `$`, `pockets.js`, `generate.js`?**
  _High betweenness centrality (0.322) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `BadRequest` (e.g. with `StructureBuffer` and `DetectorRegistry`) actually correct?**
  _`BadRequest` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `proteinviewer` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `lacuna.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12299465240641712 - nodes in this community are weakly interconnected._
- **Why does `How the pieces fit` connect `ProteinViewer` to `features/tree.js`, `ServiceTest`?**
  _High betweenness centrality (0.321) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `Structure` (e.g. with `BufferedStructure` and `StructureBuffer`) actually correct?**
  _`Structure` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `LacunaDetector` be split into smaller, more focused modules?**
  _Cohesion score 0.08478513356562137 - nodes in this community are weakly interconnected._