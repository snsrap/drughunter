# Graph Report - ProteinViewer  (2026-10-18)

## Corpus Check
- 11 files · ~24,263 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 1 file(s) not represented in the graph (top: (none) 1)

## Summary
- 223 nodes · 365 edges · 12 communities (8 shown, 4 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 17 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e38e6d2a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- pockets.py
- Handler
- Job
- ProteinViewer
- FpocketResult
- serve.py
- StructureBuffer
- start.sh
- score.py
- Ensemble pocket detection in ProteinViewer
- .run
- Pocket

## God Nodes (most connected - your core abstractions)
1. `Structure` - 16 edges
2. `Handler` - 15 edges
3. `Lacuna` - 13 edges
4. `Pocket` - 11 edges
5. `FpocketResult` - 10 edges
6. `Ensemble pocket detection in ProteinViewer` - 10 edges
7. `Job` - 8 edges
8. `Runner` - 8 edges
9. `main()` - 8 edges
10. `StructureBuffer` - 8 edges

## Surprising Connections (you probably didn't know these)
- `Design decisions worth knowing` --references--> `select_residues()`  [INFERRED]
  docs/lacuna-integration.md → pockets.py
- `2. What changed` --references--> `_parse_info()`  [INFERRED]
  docs/lacuna-integration.md → pockets.py
- `2. What changed` --references--> `Pocket`  [INFERRED]
  docs/lacuna-integration.md → pockets.py
- `Handler` --uses--> `LacunaError`  [INFERRED]
  serve.py → lacuna.py
- `Lacuna` --uses--> `Pocket`  [INFERRED]
  lacuna.py → pockets.py

## Import Cycles
- None detected.

## Communities (12 total, 4 thin omitted)

### Community 0 - "pockets.py"
Cohesion: 0.08
Nodes (10): Atom, _element_from_name(), Fpocket, FpocketError, main(), _parse_info(), _parse_vert(), select_residues() (+2 more)

### Community 3 - "Job"
Cohesion: 0.11
Nodes (4): _find_neat_dir(), _find_python(), Job, Runner

### Community 4 - "ProteinViewer"
Cohesion: 0.08
Nodes (21): API smoke test, Hit criteria, Reproducibility, Running a case, The pairs, Validation scripts, Bound ligands, Endpoints (+13 more)

### Community 6 - "serve.py"
Cohesion: 0.12
Nodes (4): main(), PrepError, QuickPrep, _ranges()

### Community 9 - "score.py"
Cohesion: 0.18
Nodes (6): main(), parse(), kabsch(), main(), parse(), post()

### Community 10 - "Ensemble pocket detection in ProteinViewer"
Cohesion: 0.12
Nodes (17): 1. Why, 3. Environments, 4. Backends, 5. Validation, 6. Measured timings, 7. Coverage against the MOE tutorial, 8. What is not done, 9. Verification performed (+9 more)

### Community 11 - ".run"
Cohesion: 0.13
Nodes (5): _f(), Lacuna, LacunaError, main(), _read_pseudoatoms()

### Community 12 - "Pocket"
Cohesion: 0.24
Nodes (4): 2. What changed, Design decisions worth knowing, _num(), Pocket

## Knowledge Gaps
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Ensemble pocket detection in ProteinViewer` connect `Ensemble pocket detection in ProteinViewer` to `ProteinViewer`, `Pocket`?**
  _High betweenness centrality (0.313) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Structure` (e.g. with `Lacuna` and `main()`) actually correct?**
  _`Structure` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `pockets.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0782051282051282 - nodes in this community are weakly interconnected._
- **Why does `2. What changed` connect `Pocket` to `pockets.py`, `Ensemble pocket detection in ProteinViewer`?**
  _High betweenness centrality (0.300) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `Handler` (e.g. with `GenerationError` and `LacunaError`) actually correct?**
  _`Handler` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `Job` be split into smaller, more focused modules?**
  _Cohesion score 0.11 - nodes in this community are weakly interconnected._
- **Why does `Pocket` connect `Pocket` to `pockets.py`, `.run`, `FpocketResult`?**
  _High betweenness centrality (0.217) - this node is a cross-community bridge._