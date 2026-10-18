"""Coordinates parsed from PDB or mmCIF text: the one structure model every
tool adapter works from.

Domain layer: standard library only, no I/O, no knowledge of any tool or of
the web. Only the fields the pocket cut needs are kept. Residue numbering
follows the author's numbering (``auth_seq_id``), which is what 3Dmol shows
and therefore what a ``resi`` selection in the viewer must match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Atom", "Structure", "STANDARD_AA", "ADDITIVES", "detect_format"]

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


def detect_format(text: str, fmt: str | None = None) -> str:
    """``"cif"`` or ``"pdb"``: the declared format if there is one, else
    sniffed -- mmCIF always carries a ``data_`` block."""
    fmt = (fmt or "").lower()
    if fmt in ("cif", "mmcif"):
        return "cif"
    if fmt == "pdb":
        return "pdb"
    head = text[:4096]
    if "_atom_site." in head or head.lstrip().startswith("data_"):
        return "cif"
    return "pdb"


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
    """An ordered list of atoms, first model and one altloc only."""

    def __init__(self, atoms: list[Atom]):
        self.atoms = atoms

    def __len__(self) -> int:
        return len(self.atoms)

    # -- parsing ------------------------------------------------------------
    @classmethod
    def from_text(cls, text: str, fmt: str | None = None) -> "Structure":
        """Parse PDB or mmCIF. Raises ``ValueError`` if there are no atoms."""
        if detect_format(text, fmt) == "cif":
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
        required = (c_name, c_res, c_chain, c_seq, c_x, c_y, c_z)
        if None in required:
            raise ValueError("mmCIF _atom_site loop is missing required fields")
        last_required = max(required)

        atoms: list[Atom] = []
        model0: str | None = None
        for line in lines[i:]:
            s = line.strip()
            if not s or s.startswith("#"):
                break
            if s.startswith("_") or s.startswith("loop_"):
                break
            f = _split_cif(s)
            if len(f) <= last_required:
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

    # -- derived structures -------------------------------------------------
    def receptor(self, drop: frozenset[str] = ADDITIVES,
                 drop_extra: frozenset[str] = frozenset()) -> "Structure":
        """Everything a detector should treat as wall: no solvent, no
        additives, no hydrogens, and no ligand named in ``drop_extra``."""
        skip = drop | drop_extra
        return Structure([a for a in self.atoms
                          if a.res_name not in skip and a.element != "H"])

    def to_pdb(self) -> str:
        """Serialise as PDB with every atom as ``ATOM``.

        fpocket ignores HETATM, so cofactors and ions must be promoted or they
        are treated as empty space. Serial numbers and chain IDs are rewritten
        freely: nothing is read back from a detector by identity, only
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
        """Chain IDs in order of first appearance."""
        return list(dict.fromkeys(a.chain for a in self.atoms))


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
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out
