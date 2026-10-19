"""Pockets as a detector reported them, read from either of the two files in
use: lacuna's ``pocket_report.json``, or the pockets file the viewer writes
into a project (``pockets/<structure>.<tool>.json``, any detector).

Each becomes a rank, a centre and a residue set. The two sources define the
residues differently -- lacuna lists its own lining residues, the viewer the
whole residues within its cutoff of the site points -- so scores from the two
are comparable in their distances but not exactly in their overlaps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .structure import Key
from .superpose import Point

__all__ = ["ReportedPocket", "read_pockets", "parse_pockets"]


@dataclass
class ReportedPocket:
    rank: int
    centroid: Point
    residues: frozenset[Key]
    info: dict = field(default_factory=dict)   # what the detector says beside


_LINING = re.compile(r"^\s*([A-Za-z]+?)?(-?\d+)([A-Za-z]?):(\S+)\s*$")


def _lining(s: str) -> Key | None:
    """lacuna's ``SER70:A`` (residue name, number, insertion code, chain)."""
    m = _LINING.match(str(s))
    return (m.group(4), int(m.group(2)), m.group(3)) if m else None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def parse_pockets(data) -> tuple[str, list[ReportedPocket]]:
    """(source, pockets) from a parsed report: ``"lacuna"`` or ``"viewer"``."""
    raw = data.get("pockets") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("no pocket list in this file")
    out, source = [], "viewer"
    for i, p in enumerate(raw):
        rank = int(p.get("rank", i + 1))
        c = p.get("centroid")
        if not c or len(c) < 3:
            raise ValueError("pocket %d has no centroid" % rank)
        if "lining_residues" in p:
            source = "lacuna"
            res = frozenset(k for k in map(_lining, p["lining_residues"]) if k)
            info = {k: _num(p.get(k)) for k in ("persistence", "crypticity", "druggability")}
        else:
            res = frozenset((g["chain"], int(r), "") for g in p.get("sel", []) for r in g["resi"])
            info = {"persistence": _num(p.get("persistence")), "crypticity": _num(p.get("crypticity")),
                    "druggability": _num(p.get("drug")), "score": _num(p.get("score"))}
        out.append(ReportedPocket(rank, (float(c[0]), float(c[1]), float(c[2])), res,
                          {k: v for k, v in info.items() if v is not None}))
    return source, sorted(out, key=lambda p: p.rank)


def read_pockets(path: str | Path) -> tuple[str, list[ReportedPocket]]:
    return parse_pockets(json.loads(Path(path).read_text()))
