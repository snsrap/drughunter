#!/usr/bin/env python3
"""A small Python wrapper around fpocket, plus the pocket-atom selection the
viewer highlights.

Selection method
----------------
fpocket's alpha-sphere centres are used as a stand-in for a ligand, and the
pocket is every whole standard amino-acid residue with an atom within
``cutoff`` of any centre. This is the criterion NEAT-POCKET was trained on
(``cut_pocket``), just driven by alpha spheres instead of a bound ligand.

Only ``pocketN_vert.pqr`` is read back from fpocket, and that file holds nothing
but coordinates -- residue identity is resolved against the structure parsed
here. fpocket never has to agree with us about chain IDs or numbering.

Two preparation steps are not optional:

* waters and crystallisation additives are dropped, and
* every remaining hetero atom is written as ``ATOM``.

fpocket silently ignores ``HETATM`` records, so a cofactor left as hetero is
invisible to it and its volume is detected as empty cavity, merging the
cofactor site into the pocket.

CLI
---
    ./pockets.py structure.cif [--cutoff 6.0] [--top 5]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

__all__ = [
    "Atom", "Structure", "Pocket", "Fpocket", "FpocketError", "FpocketResult",
    "StructureBuffer", "select_residues",
]

STANDARD_AA = frozenset(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE "
    "LEU LYS MET PHE PRO SER THR TRP TYR VAL".split()
)

# Solvent and common crystallisation additives. These are not part of the
# receptor; leaving them in blocks cavity that a ligand could occupy.
ADDITIVES = frozenset(
    "HOH WAT DOD EDO GOL SO4 PO4 ACT DMS MES TRS PEG PG4 IPA MPD FMT "
    "NO3 CL BR IOD AZI CAC BME".split()
)

DEFAULT_CUTOFF = 6.0


class FpocketError(RuntimeError):
    """fpocket is missing, failed, or produced nothing we can read."""


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Atom:
    name: str
    res_name: str
    chain: str
    res_seq: int
    x: float
    y: float
    z: float
    element: str
    hetero: bool

    @property
    def key(self) -> tuple[str, int]:
        """Residue identity. Chain is part of it -- res_seq alone is ambiguous
        across chains, which is how homodimer pockets get silently merged."""
        return (self.chain, self.res_seq)


class Structure:
    """Coordinates parsed from PDB or mmCIF text.

    Only the fields the pocket cut needs are kept. Residue numbering follows
    the author's numbering (``auth_seq_id``), which is what 3Dmol shows and
    therefore what a ``resi`` selection in the viewer must match.
    """

    def __init__(self, atoms: list[Atom]):
        self.atoms = atoms

    def __len__(self) -> int:
        return len(self.atoms)

    # -- parsing ------------------------------------------------------------
    @classmethod
    def from_text(cls, text: str, fmt: str | None = None) -> "Structure":
        fmt = (fmt or "").lower()
        if fmt in ("cif", "mmcif"):
            return cls._from_cif(text)
        if fmt == "pdb":
            return cls._from_pdb(text)
        # sniff: mmCIF always carries a data_ block
        head = text[:4096]
        if "_atom_site." in head or head.lstrip().startswith("data_"):
            return cls._from_cif(text)
        return cls._from_pdb(text)

    @classmethod
    def _from_pdb(cls, text: str) -> "Structure":
        atoms: list[Atom] = []
        for line in text.splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            if len(line) < 54:
                continue
            alt = line[16].strip()
            if alt not in ("", "A", "1"):  # keep one altloc only
                continue
            try:
                res_seq = int(line[22:26])
                x, y, z = (float(line[30:38]), float(line[38:46]),
                           float(line[46:54]))
            except ValueError:
                continue
            element = (line[76:78].strip() if len(line) >= 78 else "")
            name = line[12:16].strip()
            if not element:
                element = _element_from_name(name)
            chain = line[21].strip() or "A"
            atoms.append(Atom(name, line[17:20].strip(), chain, res_seq,
                              x, y, z, element.capitalize(),
                              line.startswith("HETATM")))
        if not atoms:
            raise ValueError("no ATOM/HETATM records found")
        return cls(atoms)

    @classmethod
    def _from_cif(cls, text: str) -> "Structure":
        """Parse the _atom_site loop. Author numbering is preferred so the
        residue IDs match what the viewer displays."""
        lines = text.splitlines()
        cols: dict[str, int] = {}
        i, n = 0, len(lines)
        while i < n:
            if lines[i].strip().startswith("_atom_site."):
                start = i
                while i < n and lines[i].strip().startswith("_atom_site."):
                    cols[lines[i].strip().split(".", 1)[1].strip()] = i - start
                    i += 1
                break
            i += 1
        if not cols:
            raise ValueError("no _atom_site loop found in mmCIF")

        def col(*names: str) -> int | None:
            for nm in names:
                if nm in cols:
                    return cols[nm]
            return None

        c_group = col("group_PDB")
        c_name = col("label_atom_id", "auth_atom_id")
        c_alt = col("label_alt_id")
        c_res = col("auth_comp_id", "label_comp_id")
        c_chain = col("auth_asym_id", "label_asym_id")
        c_seq = col("auth_seq_id", "label_seq_id")
        c_x, c_y, c_z = col("Cartn_x"), col("Cartn_y"), col("Cartn_z")
        c_el = col("type_symbol")
        c_model = col("pdbx_PDB_model_num")
        if None in (c_name, c_res, c_chain, c_seq, c_x, c_y, c_z):
            raise ValueError("mmCIF _atom_site loop is missing required fields")

        atoms: list[Atom] = []
        model0: str | None = None
        for line in lines[i:]:
            s = line.strip()
            if not s or s.startswith("#"):
                break
            if s.startswith("_") or s.startswith("loop_"):
                break
            f = _split_cif(s)
            if len(f) <= max(x for x in (c_name, c_res, c_chain, c_seq,
                                         c_x, c_y, c_z) if x is not None):
                continue
            if c_model is not None and c_model < len(f):
                if model0 is None:
                    model0 = f[c_model]
                elif f[c_model] != model0:  # first model only
                    continue
            if c_alt is not None and c_alt < len(f):
                if f[c_alt] not in (".", "?", "A", "1"):
                    continue
            try:
                res_seq = int(f[c_seq])
                x, y, z = float(f[c_x]), float(f[c_y]), float(f[c_z])
            except ValueError:
                continue
            element = (f[c_el] if c_el is not None and c_el < len(f) else "")
            if element in (".", "?"):
                element = ""
            name = f[c_name].strip('"')
            if not element:
                element = _element_from_name(name)
            hetero = (c_group is not None and c_group < len(f)
                      and f[c_group] == "HETATM")
            chain = f[c_chain].strip('"') or "A"
            atoms.append(Atom(name, f[c_res], chain, res_seq, x, y, z,
                              element.capitalize(), hetero))
        if not atoms:
            raise ValueError("mmCIF _atom_site loop yielded no atoms")
        return cls(atoms)

    # -- preparation --------------------------------------------------------
    def receptor(self, drop: frozenset[str] = ADDITIVES,
                 drop_extra: frozenset[str] = frozenset()) -> "Structure":
        """Everything fpocket should treat as wall: no solvent, no additives,
        no hydrogens, and no ligand named in ``drop_extra``."""
        skip = drop | drop_extra
        return Structure([a for a in self.atoms
                          if a.res_name not in skip and a.element != "H"])

    def to_pdb(self) -> str:
        """Serialise as PDB with every atom as ``ATOM``.

        fpocket ignores HETATM, so cofactors and ions must be promoted or they
        are treated as empty space. Serial numbers and chain IDs are rewritten
        freely: nothing is read back from fpocket by identity, only alpha-sphere
        coordinates, so this file does not have to round-trip.
        """
        out = []
        chain_map: dict[str, str] = {}
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        for a in self.atoms:
            if a.chain not in chain_map:
                chain_map[a.chain] = alphabet[len(chain_map) % len(alphabet)]
        for i, a in enumerate(self.atoms, start=1):
            name = a.name if len(a.name) >= 4 else " " + a.name.ljust(3)
            out.append(
                "ATOM  %5d %-4s %-3s %1s%4d    %8.3f%8.3f%8.3f  1.00  0.00          %2s"
                % (i % 100000, name[:4], a.res_name[:3], chain_map[a.chain],
                   a.res_seq % 10000, a.x, a.y, a.z, a.element.upper()[:2])
            )
        out.append("END")
        return "\n".join(out) + "\n"

    def chains(self) -> list[str]:
        seen: list[str] = []
        for a in self.atoms:
            if a.chain not in seen:
                seen.append(a.chain)
        return seen


def _element_from_name(name: str) -> str:
    m = re.match(r"\s*([A-Za-z]{1,2})", name.lstrip("0123456789"))
    return m.group(1)[:1] if m else "C"


def _split_cif(line: str) -> list[str]:
    """Split an mmCIF data line, honouring single and double quotes."""
    out, cur, quote = [], [], ""
    for ch in line:
        if quote:
            if ch == quote:
                quote = ""
            else:
                cur.append(ch)
        elif ch in "'\"":
            quote = ch
        elif ch.isspace():
            if cur:
                out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


# ---------------------------------------------------------------------------
# pockets
# ---------------------------------------------------------------------------
def _num(v: float, digits: int = 3):
    """A JSON-safe number. ``json.dumps`` writes NaN as a bare ``NaN``, which
    ``JSON.parse`` rejects, so a field the detector did not report is sent as
    null and the panel can show it as absent rather than as zero."""
    return None if v != v else round(v, digits)


@dataclass
class Pocket:
    rank: int
    score: float
    druggability: float
    volume: float
    centers: list[tuple[float, float, float]]
    radii: list[float]
    residues: list[tuple[str, int]] = field(default_factory=list)
    n_atoms: int = 0
    # Ensemble detectors only -- see lacuna.py. A single-structure run leaves
    # these alone, and they go out as null.
    persistence: float = float("nan")
    crypticity: float = float("nan")
    cryptic: bool = False

    @property
    def n_spheres(self) -> int:
        return len(self.centers)

    @property
    def centroid(self) -> tuple[float, float, float]:
        """Stand-in for the ligand centroid. NEAT-POCKET centres its input
        frame on the ligand's centre of mass, so this is the value that plays
        that role when there is no ligand."""
        n = len(self.centers) or 1
        return tuple(sum(c[i] for c in self.centers) / n for i in range(3))

    def selection(self) -> list[dict]:
        """Residues grouped by chain, for a 3Dmol ``{or:[...]}`` selection.

        Grouped rather than flattened on purpose: a pocket can span chains, and
        a bare list of residue numbers would light up that number on every one.
        """
        by_chain: dict[str, list[int]] = {}
        for chain, seq in self.residues:
            by_chain.setdefault(chain, []).append(seq)
        return [{"chain": c, "resi": sorted(v)} for c, v in sorted(by_chain.items())]

    def as_dict(self, max_spheres: int = 400) -> dict:
        """JSON form. ``spheres`` is included so the viewer can draw what
        fpocket actually found, rather than only the residues derived from it."""
        return {
            "rank": self.rank, "score": _num(self.score),
            "drug": _num(self.druggability), "volume": _num(self.volume, 1),
            "persistence": _num(self.persistence),
            "crypticity": _num(self.crypticity), "cryptic": self.cryptic,
            "nSpheres": self.n_spheres, "nResidues": len(self.residues),
            "nAtoms": self.n_atoms,
            "centroid": [round(v, 3) for v in self.centroid],
            "sel": self.selection(),
            "spheres": [[round(c[0], 2), round(c[1], 2), round(c[2], 2),
                         round(r, 2)]
                        for c, r in zip(self.centers[:max_spheres],
                                        self.radii[:max_spheres])],
        }


def select_residues(structure: Structure,
                    centers: list[tuple[float, float, float]],
                    cutoff: float = DEFAULT_CUTOFF,
                    standard_only: bool = True) -> tuple[list[tuple[str, int]], int]:
    """Whole residues with any atom within ``cutoff`` of any alpha-sphere centre.

    Returns (residue keys, atom count). Uses a cell grid so the cost stays
    linear -- a plain all-pairs loop is ~10^6 distances on a mid-sized entry,
    which is slow in pure Python.
    """
    if not centers:
        return [], 0
    cell = max(cutoff, 1e-6)
    grid: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
    for c in centers:
        k = (int(c[0] // cell), int(c[1] // cell), int(c[2] // cell))
        grid.setdefault(k, []).append(c)

    cut2 = cutoff * cutoff
    hit: set[tuple[str, int]] = set()
    for a in structure.atoms:
        if a.key in hit:
            continue
        gx, gy, gz = int(a.x // cell), int(a.y // cell), int(a.z // cell)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for c in grid.get((gx + dx, gy + dy, gz + dz), ()):
                        if ((a.x - c[0]) ** 2 + (a.y - c[1]) ** 2
                                + (a.z - c[2]) ** 2) <= cut2:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found:
            hit.add(a.key)

    if standard_only:
        ok = {a.key for a in structure.atoms if a.res_name in STANDARD_AA}
        hit &= ok
    n_atoms = sum(1 for a in structure.atoms if a.key in hit)
    return sorted(hit), n_atoms


class FpocketResult:
    """Pockets plus a record of how they were produced.

    Behaves like the list of pockets, so callers that only want the pockets can
    iterate, index or slice it directly.
    """

    def __init__(self, pockets: list[Pocket], command: list[str],
                 elapsed: float, receptor_atoms: int, cutoff: float):
        self.pockets = pockets
        self.command = command
        self.elapsed = elapsed
        self.receptor_atoms = receptor_atoms
        self.cutoff = cutoff

    def __iter__(self):
        return iter(self.pockets)

    def __len__(self) -> int:
        return len(self.pockets)

    def __getitem__(self, i):
        return self.pockets[i]

    def meta(self) -> dict:
        return {"command": " ".join(self.command), "elapsed": round(self.elapsed, 2),
                "receptorAtoms": self.receptor_atoms, "cutoff": self.cutoff,
                "nPockets": len(self.pockets)}


class Fpocket:
    """Runs the fpocket binary and returns :class:`Pocket` objects."""

    def __init__(self, binary: str | None = None):
        self.binary = (binary or os.environ.get("FPOCKET_BIN")
                       or shutil.which("fpocket"))

    @property
    def available(self) -> bool:
        return bool(self.binary) and os.path.exists(self.binary)

    def run(self, structure: Structure, workdir: str,
            cutoff: float = DEFAULT_CUTOFF,
            ligand_resnames: frozenset[str] = frozenset(),
            timeout: float = 300.0) -> list[Pocket]:
        """Detect pockets and resolve each one to whole residues.

        ``structure`` is the full entry as displayed; the receptor handed to
        fpocket is derived from it here so callers cannot forget the
        HETATM promotion.
        """
        if not self.available:
            raise FpocketError(
                "fpocket binary not found. Install it and put it on PATH, or "
                "set FPOCKET_BIN to its location."
            )
        receptor = structure.receptor(drop_extra=ligand_resnames)
        if len(receptor) == 0:
            raise FpocketError("nothing left to search after removing solvent")

        os.makedirs(workdir, exist_ok=True)
        pdb_path = os.path.join(workdir, "receptor.pdb")
        with open(pdb_path, "w") as fh:
            fh.write(receptor.to_pdb())

        cmd = [self.binary, "-f", pdb_path]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise FpocketError("fpocket timed out after %gs" % timeout) from exc
        elapsed = time.monotonic() - started
        out_dir = os.path.join(workdir, "receptor_out")
        if not os.path.isdir(out_dir):
            raise FpocketError(
                "fpocket produced no output (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[:400])
            )

        info = _parse_info(out_dir)
        pockets: list[Pocket] = []
        pocket_dir = os.path.join(out_dir, "pockets")
        if not os.path.isdir(pocket_dir):
            return FpocketResult([], cmd, elapsed, len(receptor), cutoff)
        for fn in os.listdir(pocket_dir):
            m = re.match(r"pocket(\d+)_vert\.pqr$", fn)
            if not m:
                continue
            rank = int(m.group(1))
            centers, radii = _parse_vert(os.path.join(pocket_dir, fn))
            if not centers:
                continue
            meta = info.get(rank, {})
            p = Pocket(rank=rank,
                       score=meta.get("Score", float("nan")),
                       druggability=meta.get("Druggability Score", float("nan")),
                       volume=meta.get("Volume", float("nan")),
                       centers=centers, radii=radii)
            p.residues, p.n_atoms = select_residues(structure, centers, cutoff)
            pockets.append(p)

        pockets.sort(key=lambda p: p.rank)
        return FpocketResult(pockets, cmd, elapsed, len(receptor), cutoff)


def _parse_vert(path: str) -> tuple[list[tuple[float, float, float]], list[float]]:
    centers, radii = [], []
    with open(path) as fh:
        for line in fh:
            if not line.startswith(("ATOM", "HETATM")) or len(line) < 54:
                continue
            try:
                centers.append((float(line[30:38]), float(line[38:46]),
                                float(line[46:54])))
            except ValueError:
                continue
            tail = line[54:].split()
            radii.append(float(tail[-1]) if tail else 0.0)
    return centers, radii


def _parse_info(out_dir: str) -> dict[int, dict[str, float]]:
    path = None
    for fn in os.listdir(out_dir):
        if fn.endswith("_info.txt"):
            path = os.path.join(out_dir, fn)
            break
    if not path:
        return {}
    info: dict[int, dict[str, float]] = {}
    cur = None
    with open(path) as fh:
        for line in fh:
            m = re.match(r"^Pocket (\d+)\s*:", line)
            if m:
                cur = int(m.group(1))
                info[cur] = {}
            elif cur is not None and ":" in line:
                k, _, v = line.partition(":")
                try:
                    info[cur][k.strip()] = float(v.strip())
                except ValueError:
                    pass
    return info


# ---------------------------------------------------------------------------
# buffering
# ---------------------------------------------------------------------------
class StructureBuffer:
    """Holds the structures the viewer is showing, on disk, in a temp tree.

    fpocket is a command line program: it reads a file and writes its results
    into a directory beside it. So whatever the browser is displaying is
    written here once and every later pocket run reads it from this location,
    rather than the coordinates being posted again per request.

    Each entry gets its own directory so fpocket's ``receptor_out`` output
    cannot collide between structures.
    """

    def __init__(self, root: str | None = None, limit: int = 12):
        self.root = root or tempfile.mkdtemp(prefix="proteinviewer-")
        self.limit = limit
        self._order: list[str] = []
        self._entries: dict[str, dict] = {}

    def put(self, token: str, text: str, fmt: str | None = None) -> dict:
        """Buffer a structure under ``token``; returns its record."""
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", token)[:64] or "structure"
        # Same name, same text: reuse it (and its pocket results). Same name,
        # new text -- a structure prepared again -- replaces the old copy.
        old = self._entries.get(token)
        if old is not None and old["text"] == text:
            self._order.remove(token)
            self._order.append(token)
            return old
        if old is not None:
            self._order.remove(token)
            del self._entries[token]

        structure = Structure.from_text(text, fmt)
        work = os.path.join(self.root, token)
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        src = os.path.join(work, "source." + ("cif" if (fmt or "").lower()
                                              in ("cif", "mmcif") else "pdb"))
        with open(src, "w") as fh:
            fh.write(text)

        rec = {"token": token, "path": src, "workdir": work, "text": text,
               "structure": structure, "pockets": None}
        self._entries[token] = rec
        self._order.append(token)
        self._evict()
        return rec

    def get(self, token: str) -> dict | None:
        rec = self._entries.get(token)
        if rec:
            self._order.remove(token)
            self._order.append(token)
        return rec

    def _evict(self) -> None:
        while len(self._order) > self.limit:
            old = self._order.pop(0)
            rec = self._entries.pop(old, None)
            if rec:
                shutil.rmtree(rec["workdir"], ignore_errors=True)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self._order.clear()
        self._entries.clear()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Detect pockets with fpocket.")
    ap.add_argument("path")
    ap.add_argument("--cutoff", type=float, default=DEFAULT_CUTOFF)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--ligand", default="",
                    help="comma-separated residue names to delete first")
    args = ap.parse_args(argv[1:])

    text = open(args.path).read()
    fmt = "cif" if args.path.lower().endswith((".cif", ".mmcif")) else "pdb"
    structure = Structure.from_text(text, fmt)
    lig = frozenset(x.strip().upper() for x in args.ligand.split(",") if x.strip())

    with tempfile.TemporaryDirectory(prefix="fpocket-") as work:
        pockets = Fpocket().run(structure, work, cutoff=args.cutoff,
                                ligand_resnames=lig)

    print("%d atoms, chains %s -> %d pockets in %.2fs"
          % (len(structure), ",".join(structure.chains()), len(pockets),
             pockets.elapsed))
    print("$ %s" % " ".join(pockets.command))
    print("%4s %8s %7s %9s %8s %6s %7s  %s"
          % ("rank", "score", "drug", "volume", "spheres", "resid", "atoms",
             "centroid"))
    for p in pockets[:args.top]:
        print("%4d %8.3f %7.3f %9.1f %8d %6d %7d  %s"
              % (p.rank, p.score, p.druggability, p.volume, p.n_spheres,
                 len(p.residues), p.n_atoms,
                 " ".join("%.2f" % v for v in p.centroid)))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
