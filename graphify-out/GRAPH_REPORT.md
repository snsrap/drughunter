# Graph Report - ProteinViewer  (2026-10-17)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 131 nodes · 209 edges · 9 communities (2 shown, 7 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 3 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- pockets.py
- serve.py
- Handler
- Job
- Structure
- FpocketResult
- QuickPrep
- StructureBuffer
- start.sh

## God Nodes (most connected - your core abstractions)
1. `Handler` - 14 edges
2. `Structure` - 12 edges
3. `Job` - 8 edges
4. `Runner` - 8 edges
5. `FpocketResult` - 8 edges
6. `StructureBuffer` - 8 edges
7. `Fpocket` - 7 edges
8. `GenerationError` - 7 edges
9. `Pocket` - 7 edges
10. `FpocketError` - 6 edges

## Surprising Connections (you probably didn't know these)
- `Handler` --uses--> `FpocketError`  [INFERRED]
  serve.py → pockets.py
- `Handler` --uses--> `GenerationError`  [INFERRED]
  serve.py → generate.py

## Import Cycles
- None detected.

## Communities (9 total, 7 thin omitted)

### Community 0 - "pockets.py"
Cohesion: 0.11
Nodes (8): _element_from_name(), Fpocket, FpocketError, main(), _parse_info(), _parse_vert(), select_residues(), _split_cif()

### Community 1 - "serve.py"
Cohesion: 0.13
Nodes (3): _find_neat_dir(), _find_python(), main()

## Knowledge Gaps
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Handler` connect `Handler` to `pockets.py`, `serve.py`?**
  _High betweenness centrality (0.156) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Handler` (e.g. with `GenerationError` and `FpocketError`) actually correct?**
  _`Handler` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `pockets.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11333333333333333 - nodes in this community are weakly interconnected._
- **Why does `Structure` connect `Structure` to `pockets.py`?**
  _High betweenness centrality (0.122) - this node is a cross-community bridge._
- **Should `serve.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12554112554112554 - nodes in this community are weakly interconnected._
- **Why does `Pocket` connect `FpocketResult` to `pockets.py`?**
  _High betweenness centrality (0.109) - this node is a cross-community bridge._