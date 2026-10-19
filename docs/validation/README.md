# Validation

Apo/holo recovery testing for the pocket detectors, and a smoke test for the
pocket API. Background and the measured results are in
[../lacuna-integration.md](../lacuna-integration.md).

The method: detect on the **apo** structure with the ligand never present,
superpose the **holo** structure onto it, and score what came back against the
holo ligand's 5 Å residue shell. The tooling is
[pocketbench](../../../pocketbench)'s, which replaced the two scripts that
used to live here (`prep_and_truth.py`, `score.py`). On TEM-1 it gives the same
lacuna input byte for byte, and the same overlaps and best ranks.

Two things it does that the scripts did not:

- **Residues are carried across by the alignment, not by number.** The
  holo's shell residues are mapped onto the apo structure through the
  superposition's sequence alignment, so a pair need not share numbering or
  chain ids. A shell residue the apo structure lacks counts as missed.
- **Distances as well as overlap.** Each pocket also gets DCC (centre to the
  ligand's centre) and DCA (centre to the nearest ligand atom), the criterion
  P2Rank's benchmarks use.

## Running a case

```sh
PB=~/.pyenv/versions/pocketbench/bin/pocketbench
L=~/.pyenv/versions/lacuna/bin                 # or ~/micromamba/envs/lacuna-gpu/bin
W=/tmp/apoholo && mkdir -p $W/raw $W/work

# 1. fetch the pair
for id in 1BTL 1PZO; do
  curl -sfL -o $W/raw/$id.pdb https://files.rcsb.org/download/$id.pdb
done

# 2. the apo input: chain A, protein only
$PB apo $W/raw/1BTL.pdb -o $W/work/1BTL_protein.pdb --chain A

# 3. detect on the apo structure only
cd $W/work && $L/lacuna discover 1BTL_protein.pdb \
    --backend nma --detector alpha --conformers 20 --top 10 --emit-pocket-pdbs

# 4. score the report against the holo ligand
$PB score 1BTL_protein_lacuna/pocket_report.json 1BTL_protein.pdb $W/raw/1PZO.pdb CBT
```

`pocketbench site APO HOLO LIGAND` prints the ground truth alone. `score`
also reads the pockets file the viewer writes into a project
(`pockets/<structure>.<tool>.json`), so a run made in the GUI is scored the
same way.

## The pairs

| target | apo | holo | ligand | result, lacuna `nma`/`alpha`, 20 conformers |
|---|---|---|---|---|
| TEM-1 β-lactamase | `1BTL` | `1PZO` | `CBT` | recovered at rank 2, ~2 s |
| PTP1B | `1SUG` | `1T49` | `892` | not recovered; site splits across rank 3 (α3) and rank 6 (α6/α7) |
| KRAS G12C | `4LDJ` | `6OIM` | `MOV` | recovered (2026-10-19): rank 1 meets all three criteria but also takes in the edge of the emptied nucleotide site; rank 3 is the switch-II pocket alone (68 % coverage, DCC 2.5 Å, DCA 1.4 Å) |
| IL-2 | `1M47` | `1PY2` | `FRH` | not yet run |
| trypsin (negative control) | `3PTB` | — | — | not yet run; the open orthosteric site should not read as cryptic |

Two ID traps, both checked against RCSB: **`1T48` is not PTP1B apo** — it
holds ligand `BB3` and is a second holo form from the same paper — and KRAS
should use `4LDJ` rather than the usual `4OBE` so that apo and holo share the
G12C background.

**KRAS has a second site beside the one being tested.** The apo input drops
GDP, as every protein-only input does, so the nucleotide pocket is empty
during detection and sits against the switch-II pocket through the P-loop. A
pocket in the nucleotide site covers exactly 50 % of the sotorasib shell
through those shared residues, while lying 11.9 Å from the ligand. Scoring the
same report against `GDP` in `4LDJ` itself shows which pocket is which.
Whether the detector should see GDP is an open protocol question.

## Hit criteria

`score` reports three criteria, each separately: coverage ≥ 50 % of the
shell, DCC ≤ 5 Å, and DCA ≤ 4 Å. **The first two are local choices, not a
published convention**, and they change conclusions: at a 40 % bar, TEM-1
with `surface-fusion` hits at rank 1 and PTP1B with `nma` hits at rank 3.
Coverage alone also credits a neighbouring site that shares residues, as on
KRAS. DCA ≤ 4 Å is P2Rank's criterion. Read the columns rather than the flags.

## API smoke test

Exercises `/api/status`, `/api/structure`, and `/api/pockets` for fpocket,
lacuna, the cache path and an unknown method. Start the server first.

```sh
./serve.py 8765 --no-open &
python3 smoke_api.py 8765 /tmp/apoholo/work/1BTL_protein.pdb
```

## Reproducibility

The `nma` backend is an eigendecomposition and returns the same ensemble every
time. The `openmm` backend **does not**: three runs over one PTP1B input
ranked the site 9th, 7th and 10th. Repeat any MD-backed number before
reporting it.
