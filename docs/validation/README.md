# Validation scripts

Apo/holo recovery testing for the ensemble pocket detector, and a smoke test
for the pocket API. Background and the measured results are in
[../lacuna-integration.md](../lacuna-integration.md).

The method: prepare the **apo** structure, detect on it with the ligand never
present, and score what comes back against the **holo** ligand's 5 Å residue
shell. Residue overlap is compared by author numbering, which both entries of
each pair share, so it needs no superposition; centroid distances do, and
`score.py` superposes on common Cα atoms (Kabsch) first.

Any Python 3.10+ with numpy works — the lacuna environment already has it.

## Running a case

```sh
L=~/.pyenv/versions/lacuna/bin                 # or ~/micromamba/envs/lacuna-gpu/bin
W=/tmp/lacuna-check && mkdir -p $W/raw $W/work

# 1. fetch the pair
for id in 1BTL 1PZO; do
  curl -sfL -o $W/raw/$id.pdb https://files.rcsb.org/download/$id.pdb
done

# 2. build the apo input, and print the ground-truth shell from the holo entry
$L/python -I prep_and_truth.py $W/raw/1BTL.pdb $W/raw/1PZO.pdb CBT $W/work 5.0

# 3. detect on the apo structure only
cd $W/work && $L/lacuna discover 1BTL_protein.pdb \
    --backend nma --detector alpha --conformers 20 --top 10 --emit-pocket-pdbs

# 4. score the report against the holo ligand
$L/python -I score.py $W/work/1BTL_protein_lacuna/pocket_report.json \
    $W/work/1BTL_protein.pdb $W/raw/1PZO.pdb CBT 5.0
```

`score.py` prints every pocket with its residue overlap, coverage, Jaccard,
centroid distance, persistence, crypticity and druggability, then the best
rank that qualifies.

## The pairs

| target | apo | holo | ligand | expected |
|---|---|---|---|---|
| TEM-1 β-lactamase | `1BTL` | `1PZO` | `CBT` | recovered at rank 2, ~2 s with `--backend nma` |
| PTP1B | `1SUG` | `1T49` | `892` | not recovered; site splits across rank 3 (α3) and rank 6 (α6/α7) |
| KRAS G12C | `4LDJ` | `6OIM` | `MOV` | not yet run |
| IL-2 | `1M47` | `1PY2` | `FRH` | not yet run |
| trypsin (negative control) | `3PTB` | — | — | not yet run; the open orthosteric site should not read as cryptic |

Two ID traps, both checked against RCSB: **`1T48` is not PTP1B apo** — it
holds ligand `BB3` and is a second holo form from the same paper — and KRAS
should use `4LDJ` rather than the usual `4OBE` so that apo and holo share the
G12C background.

## Hit criteria

`score.py` marks a recovery at ≥50% of the shell residues covered, or a
centroid within 5 Å. **Those thresholds are local choices, not a published
convention**, and they change conclusions: at a 40% bar, TEM-1 with
`surface-fusion` hits at rank 1 and PTP1B with `nma` hits at rank 3. Read the
coverage and persistence columns rather than the verdict.

## API smoke test

Exercises `/api/status`, `/api/structure`, and `/api/pockets` for fpocket,
lacuna, the cache path and an unknown method. Start the server first.

```sh
./serve.py 8765 --no-open &
python3 smoke_api.py 8765 /tmp/lacuna-check/work/1BTL_protein.pdb
```

## Reproducibility

The `nma` backend is an eigendecomposition and returns the same ensemble every
time. The `openmm` backend **does not**: three runs over one PTP1B input
ranked the site 9th, 7th and 10th. Repeat any MD-backed number before
reporting it.
