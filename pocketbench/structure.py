"""Atoms read from PDB or mmCIF text.

The same reading the viewer's server does (proteinviewer/structure.py): the
first model only, one alternate location, and the author's numbering
(``auth_seq_id``), which is what 3Dmol shows and what pocket residues are
reported in. Unlike the server it keeps insertion codes, since a residue
identity that drops them merges 52 and 52A.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Atom", "Structure", "STANDARD_AA", "ADDITIVES", "detect_format", "read_text",
           "cif_tokens", "cif_loops", "ATOM_SITE"]

STANDARD_AA = frozenset(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE "
    "LEU LYS MET PHE PRO SER THR TRP TYR VAL".split()
)

# Solvent and common crystallisation additives. Not part of a receptor: left
# in, they block cavity a ligand could occupy. ProteinViewer offers to delete a
# structure's bound ligands before a run and needs to know which residues a
# detector drops anyway, so this list is part of what `pocketbench detectors`
# reports.
ADDITIVES = frozenset(
    "HOH WAT DOD EDO GOL SO4 PO4 ACT DMS MES TRS PEG PG4 IPA MPD FMT "
    "NO3 CL BR IOD AZI CAC BME".split()
)

Key = tuple[str, int, str]          # chain, residue number, insertion code


@dataclass(frozen=True, slots=True)
class Atom:
    name: str
    resn: str
    chain: str
    resi: int
    icode: str
    x: float
    y: float
    z: float
    element: str
    hetero: bool
    line: int = -1          # its line in the text it was read from

    @property
    def key(self) -> Key:
        """The residue this atom belongs to. Chain is part of it: a residue
        number alone is ambiguous across chains."""
        return (self.chain, self.resi, self.icode)

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


def detect_format(text: str, fmt: str | None = None) -> str:
    """``"cif"`` or ``"pdb"``: the declared format if there is one, else
    sniffed -- an mmCIF always carries a ``data_`` block."""
    fmt = (fmt or "").lower()
    if fmt in ("cif", "mmcif", "pdbx"):
        return "cif"
    if fmt in ("pdb", "ent"):
        return "pdb"
    head = text[:4096]
    return "cif" if "_atom_site." in head or head.lstrip().startswith("data_") else "pdb"


def read_text(path: str | Path) -> tuple[str, str]:
    """A structure file's text and format, gzipped or not."""
    p = Path(path)
    raw = p.read_bytes()
    text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8", "replace")
    ext = p.name.removesuffix(".gz").rsplit(".", 1)[-1].lower()
    return text, detect_format(text, ext if ext in ("cif", "mmcif", "pdbx", "pdb", "ent") else None)


class Structure:
    """An ordered list of atoms, first model and one altloc only, plus the
    text they were read from (which is what gets moved)."""

    def __init__(self, atoms: list[Atom], text: str = "", fmt: str = "pdb"):
        self.atoms = atoms
        self.text = text
        self.fmt = fmt

    def __len__(self) -> int:
        return len(self.atoms)

    @classmethod
    def read(cls, path: str | Path) -> "Structure":
        text, fmt = read_text(path)
        return cls.from_text(text, fmt)

    @classmethod
    def from_text(cls, text: str, fmt: str | None = None) -> "Structure":
        """Parse PDB or mmCIF. Raises ``ValueError`` if there are no atoms."""
        fmt = detect_format(text, fmt)
        atoms = _from_cif(text) if fmt == "cif" else _from_pdb(text)
        if not atoms:
            raise ValueError("no atoms found" if fmt == "pdb" else "no _atom_site rows found")
        return cls(atoms, text, fmt)

    def chains(self) -> list[str]:
        """Chain ids in order of first appearance."""
        return list(dict.fromkeys(a.chain for a in self.atoms))

    def receptor(self, drop: frozenset[str] = ADDITIVES,
                 drop_extra: frozenset[str] = frozenset()) -> "Structure":
        """Everything a detector should treat as wall: no solvent, no
        additives, no hydrogens, and no residue named in ``drop_extra`` (a
        bound ligand the user chose to delete)."""
        skip = drop | drop_extra
        return Structure([a for a in self.atoms if a.resn not in skip and a.element != "H"],
                         fmt=self.fmt)

    def to_pdb(self) -> str:
        """Every atom as an ``ATOM`` record.

        fpocket ignores HETATM, so a cofactor or ion left as hetero is detected
        as empty cavity. Serial numbers and chain ids are rewritten freely:
        nothing is read back from a detector by identity, only coordinates, so
        this file does not have to round-trip.
        """
        chain_map: dict[str, str] = {}
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        for a in self.atoms:
            if a.chain not in chain_map:
                chain_map[a.chain] = alphabet[len(chain_map) % len(alphabet)]
        out = []
        for i, a in enumerate(self.atoms, start=1):
            name = a.name if len(a.name) >= 4 else " " + a.name.ljust(3)
            out.append("ATOM  %5d %-4s %-3s %1s%4d%1s   %8.3f%8.3f%8.3f  1.00  0.00          %2s"
                       % (i % 100000, name[:4], a.resn[:3], chain_map[a.chain], a.resi % 10000,
                          a.icode[:1], a.x, a.y, a.z, a.element.upper()[:2]))
        out.append("END")
        return "\n".join(out) + "\n"

    def residue(self, code: str) -> dict[Key, list[Atom]]:
        """Every copy of the residue named ``code``, by residue identity."""
        out: dict[Key, list[Atom]] = {}
        for a in self.atoms:
            if a.resn == code:
                out.setdefault(a.key, []).append(a)
        return out


def _element(name: str) -> str:
    m = re.match(r"\s*([A-Za-z]{1,2})", name.lstrip("0123456789"))
    return m.group(1)[:1] if m else "C"


def _from_pdb(text: str) -> list[Atom]:
    atoms: list[Atom] = []
    models = 0
    for n, line in enumerate(text.splitlines()):
        if line.startswith("MODEL "):
            models += 1
            if models > 1:
                break
            continue
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 54:
            continue
        if line[16].strip() not in ("", "A", "1"):     # one altloc only
            continue
        try:
            resi = int(line[22:26])
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
        except ValueError:
            continue
        name = line[12:16].strip()
        element = (line[76:78].strip() if len(line) >= 78 else "") or _element(name)
        atoms.append(Atom(name, line[17:20].strip(), line[21].strip() or "A", resi,
                          line[26].strip() if len(line) > 26 else "", x, y, z,
                          element.capitalize(), line.startswith("HETATM"), n))
    return atoms


# ---------------------------------------------------------------- mmCIF
_CIF_TOKEN = re.compile(r"""'(?:[^']|'(?=\S))*'|"(?:[^"]|"(?=\S))*"|\S+""")
ATOM_SITE = re.compile(r"^_atom_site(_anisotrop)?\.")


def cif_tokens(line: str) -> list[str]:
    """The tokens of one mmCIF data line, quoted values kept quoted."""
    return _CIF_TOKEN.findall(line)


def cif_set_tokens(line: str, values: dict[int, str]) -> str:
    """The same line with some of its tokens replaced, {index: text}; the rest
    of it, spacing included, is left as it was."""
    k = -1

    def swap(m: re.Match) -> str:
        nonlocal k
        k += 1
        return values.get(k, m.group(0))
    return _CIF_TOKEN.sub(swap, line)


@dataclass
class CifLoop:
    cat: str                 # the category prefix, e.g. "_atom_site."
    cols: list[str]          # column names in order
    rows: list[int]          # line indices of its rows

    def col(self, name: str) -> int:
        """Where one column sits in a row, or -1 if the file omits it."""
        try:
            return self.cols.index(self.cat + name)
        except ValueError:
            return -1


def cif_loops(lines: list[str], pattern: re.Pattern) -> list[CifLoop]:
    """The looped categories whose name matches ``pattern``. One reader, so
    parsing and moving read a file's loops the same way."""
    loops, n, i = [], len(lines), 0
    while i < n:
        m = lines[i].strip() == "loop_" and i + 1 < n and pattern.match(lines[i + 1].strip())
        if not m:
            i += 1
            continue
        L = CifLoop(m.group(0), [], [])
        j = i + 1
        while j < n and lines[j].strip().startswith(L.cat):
            L.cols.append(lines[j].strip())
            j += 1
        while j < n:
            t = lines[j].strip()
            if not t or t[0] in "#_" or t == "loop_" or t.startswith("data_"):
                break
            L.rows.append(j)
            j += 1
        loops.append(L)
        i = j
    return loops


def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def _from_cif(text: str) -> list[Atom]:
    lines = text.splitlines()
    site = next((L for L in cif_loops(lines, ATOM_SITE) if L.cat == "_atom_site."), None)
    if site is None:
        return []

    def col(*names: str) -> int:
        for nm in names:
            c = site.col(nm)
            if c >= 0:
                return c
        return -1

    c_group, c_name, c_alt = col("group_PDB"), col("auth_atom_id", "label_atom_id"), col("label_alt_id")
    c_resn, c_chain = col("auth_comp_id", "label_comp_id"), col("auth_asym_id", "label_asym_id")
    c_seq, c_ins = col("auth_seq_id", "label_seq_id"), col("pdbx_PDB_ins_code")
    c_x, c_y, c_z = col("Cartn_x"), col("Cartn_y"), col("Cartn_z")
    c_el, c_model = col("type_symbol"), col("pdbx_PDB_model_num")
    if min(c_name, c_resn, c_chain, c_seq, c_x, c_y, c_z) < 0:
        raise ValueError("the mmCIF _atom_site loop lacks a required column")

    atoms: list[Atom] = []
    model0 = None
    for j in site.rows:
        f = cif_tokens(lines[j])
        if len(f) < len(site.cols):
            continue
        if c_model >= 0:
            model0 = f[c_model] if model0 is None else model0
            if f[c_model] != model0:              # first model only
                continue
        if c_alt >= 0 and f[c_alt] not in (".", "?", "A", "1"):
            continue
        try:
            resi = int(f[c_seq])
            x, y, z = float(f[c_x]), float(f[c_y]), float(f[c_z])
        except ValueError:
            continue
        name = _unquote(f[c_name])
        element = f[c_el] if c_el >= 0 and f[c_el] not in (".", "?") else _element(name)
        icode = f[c_ins] if c_ins >= 0 and f[c_ins] not in (".", "?") else ""
        atoms.append(Atom(name, _unquote(f[c_resn]), _unquote(f[c_chain]) or "A", resi, icode,
                          x, y, z, element.capitalize(), c_group >= 0 and f[c_group] == "HETATM", j))
    return atoms
