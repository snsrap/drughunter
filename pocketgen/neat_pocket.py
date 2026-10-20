"""Generate ligands for pockets given as residue selections, with no reference ligand.

``generation_from_pdb.py`` needs a bound ligand: it cuts the pocket around it and
its centre of mass sets the input frame. This script takes the pocket directly --
a list of ``(chain, res_id)`` residues, such as the ones fpocket produces -- and
builds the model's pocket tensors itself.

No stand-in ligand is required. During sampling the system is recentred on the
growing ligand's COM at every step, and ``NEAT.generate`` returns coordinates
relative to the *pocket* COM (neat.py, steps 4.11 and 5), so any constant offset
applied to ``pocket_pos`` cancels out. Only the pocket's own geometry matters,
and the output is mapped back by adding the pocket centroid in the target frame.

Two things are done differently from ``dataset_spindr`` on purpose:

* residues are keyed on ``(chain, res_id)``. Keying on ``res_id`` alone merges
  residues that share a number across chains into one embedding -- on a homodimer
  pocket that silently collapses about half of them.
* ``pocket_batch`` is built explicitly rather than inferred from where the
  residue id decreases, which misreads a multi-chain pocket as several samples.

Usage::

    python neat_pocket.py --neat-dir ~/NEAT-POCKET --job job.json

Run by NEAT-POCKET's own interpreter, never imported. ``--neat-dir`` is a
stock NEAT-POCKET checkout: its ``neat`` package, ``scripts/`` and
``trained_models/`` are used from there.

``job.json``::

    {
        "structure": "…/source.cif",
        "pockets": [{"rank": 1, "residues": [["A", 52], ["A", 54]]}],
        "n": 50,
        "batch_size": 25,
        "cfg": 0.5,
        "max_atoms": 100,
        "out_dir": "…/gen",
    }

Writes ``pocket_<rank>.sdf`` per pocket into ``out_dir``, keeps ``progress.json``
updated while running, and prints a JSON summary on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import torch

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--neat-dir", required=True)
ROOT = Path(_pre.parse_known_args()[0].neat_dir).expanduser().resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from generation_from_pdb import load_protein_structure  # noqa: E402
from neat.dataset.dataset_utils import (
    AA_VOCABULARY,
    AA_VOCABULARY_UNK,  # noqa: E402
    ATOM_VOCABULARY,
)
from neat.model import NEAT  # noqa: E402
from neat.model.bond_predictor import BondPredictor  # noqa: E402
from neat.model.molecule_builder import MoleculeBuilder  # noqa: E402
from rdkit import Chem, RDLogger  # noqa: E402
from rdkit.Chem import QED, AllChem, Crippen, Descriptors, rdMolDescriptors  # noqa: E402
from rdkit.Chem.Draw import rdMolDraw2D  # noqa: E402
from torch_geometric.data import Data  # noqa: E402

RDLogger.DisableLog("rdApp.*")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ---------------------------------------------------------------------------
def load_structure(path: str):
    """Protonated structure, as the training pockets were."""
    try:
        return load_protein_structure(path, add_bonds=True, add_hs=True)
    except Exception as exc:
        # Entries with deposited hydrogens make hydride refuse; they are already
        # protonated, so carry on.
        print("note: add_hs failed (%s); using the structure as deposited" % exc, file=sys.stderr)
        return load_protein_structure(path, add_bonds=True, add_hs=False)


def select_residues(structure, residues: list[tuple[str, int]]):
    want = {"%s|%s" % (c, r) for c, r in residues}
    keys = np.array(["%s|%s" % (c, r) for c, r in zip(structure.chain_id, structure.res_id)])
    return structure[np.isin(keys, list(want))]


def pocket_data(pocket) -> tuple[Data, torch.Tensor]:
    """Model inputs for one pocket, plus its centroid in the target frame."""
    pt = Chem.GetPeriodicTable()
    x, restype, rid = [], [], []
    seen: dict[str, int] = {}
    for element, res_name, chain, res_seq in zip(
        pocket.element, pocket.res_name, pocket.chain_id, pocket.res_id
    ):
        try:
            z = pt.GetAtomicNumber(str(element).capitalize())
        except Exception:
            z = 0
        x.append(ATOM_VOCABULARY.get(z, 0))
        restype.append(AA_VOCABULARY.get(str(res_name), AA_VOCABULARY_UNK))
        key = "%s|%s" % (chain, res_seq)  # composite, see module docstring
        if key not in seen:
            seen[key] = len(seen)
        rid.append(seen[key])

    pos = torch.tensor(np.asarray(pocket.coord, dtype=np.float32))
    centroid = pos.mean(dim=0, keepdim=True)
    data = Data(
        pocket_x=torch.tensor(x, dtype=torch.long),
        pocket_pos=pos - centroid,
        pocket_residue_id=torch.tensor(rid, dtype=torch.long),
        pocket_residue_type=torch.tensor(restype, dtype=torch.long),
    )
    return data, centroid.squeeze(0)


def collate(data: Data, samples: int, device) -> dict:
    """Repeat one pocket across ``samples`` molecules.

    pocket_batch is built here rather than inferred from residue-id order.
    """
    n = data.pocket_x.numel()
    return {
        "pocket_x": data.pocket_x.to(device).repeat(samples),
        "pocket_pos": data.pocket_pos.to(device).repeat(samples, 1),
        "pocket_residue_id": data.pocket_residue_id.to(device).repeat(samples),
        "pocket_residue_type": data.pocket_residue_type.to(device).repeat(samples),
        "pocket_batch": torch.arange(samples, device=device).repeat_interleave(n),
    }


def load_models(checkpoints: str, bond_predictor_dir: str | None):
    ckpt_dir = ROOT / checkpoints / "checkpoints"
    files = sorted(f for f in os.listdir(ckpt_dir) if f.endswith(".ckpt"))
    pick = (
        [f for f in files if f.startswith("periodic-epoch")]
        or [f for f in files if "best-val-loss" in f]
        or files
    )
    if not pick:
        raise SystemExit("no .ckpt in %s" % ckpt_dir)
    model = NEAT.load_from_checkpoint(str(ckpt_dir / pick[0]), map_location=DEVICE)
    model.eval()

    bp = None
    if bond_predictor_dir:
        bp_dir = ROOT / bond_predictor_dir / "checkpoints"
        bp_files = [f for f in os.listdir(bp_dir) if f.endswith(".ckpt")]
        if bp_files:
            bp = BondPredictor.load_from_checkpoint(str(bp_dir / bp_files[0]), map_location=DEVICE)
            bp.eval()
    return model, bp


def generate_for_pocket(
    model,
    bp,
    builder,
    data: Data,
    centroid: torch.Tensor,
    n: int,
    batch_size: int,
    cfg: float,
    max_atoms: int,
    steps: int,
    on_batch=None,
) -> list[Chem.Mol]:
    mols: list[Chem.Mol] = []
    done = 0
    while done < n:
        take = min(batch_size, n - done)
        info = collate(data, take, DEVICE)
        with torch.no_grad():
            out = model.generate(
                batch_size=take,
                max_atoms=max_atoms,
                num_time_steps=steps,
                time_step_spacing="linear",
                integration_method="euler_maruyama",
                pocket_info=info,
                cfg_factor=cfg,
                device=DEVICE,
            )
        out.batch = out.batch - out.batch.min()
        # generate() returns positions relative to the pocket COM
        pos = out.pos + centroid.to(out.pos.device, out.pos.dtype)
        if bp is not None:
            batch_mols = builder.generate_rdkit_molecules_via_bond_predictor(
                out.x, pos, out.batch, bond_predictor=bp, progress_bar=False
            )
        else:
            batch_mols = builder.generate_rdkit_molecules_via_xyz2mol(
                out.x, pos, out.batch, progress_bar=False
            )
        mols.extend(m for m in batch_mols if m is not None)
        done += take
        if on_batch:
            on_batch(done, len(mols))
    return mols


def depict(mol: Chem.Mol, width: int = 150, height: int = 108) -> str:
    """2D depiction as inline SVG.

    Black is swapped for ``currentColor`` so the drawing follows the page's text
    colour and stays legible in both themes; heteroatom colours are left alone.
    """
    try:
        flat = Chem.RemoveHs(Chem.Mol(mol))
        AllChem.Compute2DCoords(flat)
        d = rdMolDraw2D.MolDraw2DSVG(width, height)
        opts = d.drawOptions()
        opts.setBackgroundColour((1, 1, 1, 0))
        opts.bondLineWidth = 1
        rdMolDraw2D.PrepareAndDrawMolecule(d, flat)
        d.FinishDrawing()
        svg = d.GetDrawingText()
        svg = svg.replace("<?xml version='1.0' encoding='iso-8859-1'?>", "").strip()
        return svg.replace("#000000", "currentColor").replace("#000", "currentColor")
    except Exception:
        return ""


def describe(mol: Chem.Mol, rank, idx: int) -> dict:
    """Row for the molecule table: identity, properties and a depiction."""
    rec = {"rank": rank, "i": idx, "name": "pocket%s_%d" % (rank, idx + 1)}
    try:
        flat = Chem.RemoveHs(Chem.Mol(mol))
    except Exception:
        flat = mol

    def safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    rec["smiles"] = safe(lambda: Chem.MolToSmiles(flat), "") or ""
    rec["formula"] = safe(lambda: rdMolDescriptors.CalcMolFormula(flat), "") or ""
    rec["mw"] = safe(lambda: round(Descriptors.MolWt(flat), 1))
    rec["logp"] = safe(lambda: round(Crippen.MolLogP(flat), 2))
    rec["tpsa"] = safe(lambda: round(rdMolDescriptors.CalcTPSA(flat), 1))
    rec["qed"] = safe(lambda: round(QED.qed(flat), 3))
    rec["heavy"] = safe(lambda: flat.GetNumHeavyAtoms())
    rec["rings"] = safe(lambda: rdMolDescriptors.CalcNumRings(flat))
    rec["rotb"] = safe(lambda: rdMolDescriptors.CalcNumRotatableBonds(flat))
    rec["svg"] = depict(mol)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--neat-dir", required=True)
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    job = json.loads(Path(args.job).read_text())

    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    prog_path = out_dir / "progress.json"
    state = {
        "state": "starting",
        "pockets": [],
        "done": 0,
        "total": len(job["pockets"]) * int(job.get("n", 50)),
    }

    def flush(**kw):
        state.update(kw)
        prog_path.write_text(json.dumps(state))

    flush(state="loading model")
    try:
        model, bp = load_models(
            job.get("checkpoints", "trained_models/neat_spindr"),
            job.get("bond_predictor", "trained_models/bond_predictor/bp_spindr"),
        )
        builder = MoleculeBuilder(vocab=job.get("dataset", "SPINDR"))

        flush(state="reading structure")
        structure = load_structure(job["structure"])

        n = int(job.get("n", 50))
        batch_size = int(job.get("batch_size", 25))
        results = []
        table: list[dict] = []  # one row per written molecule
        produced = 0

        for spec in job["pockets"]:
            rank = spec["rank"]
            residues = [(str(c), int(r)) for c, r in spec["residues"]]
            pocket = select_residues(structure, residues)
            if len(pocket) == 0:
                results.append({"rank": rank, "error": "no atoms matched the selection"})
                continue
            flush(state="generating", current=rank)
            data, centroid = pocket_data(pocket)
            started = time.monotonic()

            def on_batch(done_in_pocket, kept, _rank=rank):
                flush(state="generating", current=_rank, done=produced + done_in_pocket, kept=kept)

            mols = generate_for_pocket(
                model,
                bp,
                builder,
                data,
                centroid,
                n,
                batch_size,
                float(job.get("cfg", 0.5)),
                int(job.get("max_atoms", 100)),
                int(job.get("steps", 60)),
                on_batch=on_batch,
            )

            sdf = out_dir / ("pocket_%s.sdf" % rank)
            w = Chem.SDWriter(str(sdf))
            written = 0
            for m in mols:
                try:
                    m.SetProp("_Name", "pocket%s_%d" % (rank, written + 1))
                    w.write(m)
                    # index matches the SDF record order the viewer will parse
                    table.append(describe(m, rank, written))
                    written += 1
                except Exception:
                    pass
            w.close()
            (out_dir / "molecules.json").write_text(json.dumps(table))
            produced += n
            results.append(
                {
                    "rank": rank,
                    "requested": n,
                    "written": written,
                    "pocketAtoms": int(len(pocket)),
                    "pocketResidues": len(residues),
                    "seconds": round(time.monotonic() - started, 1),
                    "sdf": sdf.name,
                }
            )
            flush(state="generating", done=produced, results=results)

        flush(state="done", results=results, done=produced)
        print(json.dumps({"ok": True, "results": results}))
        return 0
    except Exception as exc:
        flush(state="error", error="%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
