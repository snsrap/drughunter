"""Shared test fixtures: data files, and fake tools that stand in for the real
external programs so the adapters can be tested without them.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def read(name: str) -> str:
    return (DATA / name).read_text()


def pdb_line(serial, name, res, chain, seq, x, y, z, element, het=False, alt=" "):
    """One fixed-column PDB atom record."""
    rec = "HETATM" if het else "ATOM  "
    return "%-6s%5d %-4s%1s%-3s %1s%4d    %8.3f%8.3f%8.3f  1.00  0.00          %2s" % (
        rec,
        serial,
        (" " + name) if len(name) < 4 else name,
        alt,
        res,
        chain,
        seq,
        x,
        y,
        z,
        element,
    )


def fake_executable(directory: str | os.PathLike, name: str, body: str) -> str:
    """A Python script, made executable, run by this interpreter."""
    path = Path(directory) / name
    path.write_text("#!%s\n%s" % (sys.executable, textwrap.dedent(body)))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)
