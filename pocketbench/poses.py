"""SDF records as text: split them, read their tags, count their atoms.

A pose scorer hands an SDF to an external tool and reads one back, so the
records have to be taken apart here -- without RDKit, which pocketbench does
not have and the server could not import anyway. Nothing is interpreted
chemically: a record is text that keeps its order, and its heavy atoms are
counted from the atom block so ligand efficiency can be worked out beside the
score.
"""

from __future__ import annotations

import re

__all__ = ["split", "join", "tags", "set_tags", "title", "heavy_atoms", "number"]

_TAG = re.compile(r"^>\s+<([^>]+)>")


def split(text: str) -> list[str]:
    """The records in an SDF, each without its ``$$$$`` terminator.

    Blank trailing text is dropped, so a file that ends in a terminator and a
    newline gives no empty last record.
    """
    out: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "$$$$":
            if any(x.strip() for x in current):
                out.append("\n".join(current) + "\n")
            current = []
        else:
            current.append(line)
    if any(x.strip() for x in current):
        out.append("\n".join(current) + "\n")
    return out


def join(records: list[str]) -> str:
    """Records back into one SDF, each terminated."""
    return "".join((r if r.endswith("\n") else r + "\n") + "$$$$\n" for r in records)


def title(record: str) -> str:
    """The record's name: its first line."""
    lines = record.splitlines()
    return lines[0].strip() if lines else ""


def tags(record: str) -> dict[str, str]:
    """The record's data tags. A tag's value runs to the next blank line."""
    out: dict[str, str] = {}
    lines = record.splitlines()
    i = 0
    while i < len(lines):
        m = _TAG.match(lines[i])
        if not m:
            i += 1
            continue
        value: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip():
            value.append(lines[i])
            i += 1
        out[m.group(1)] = "\n".join(value).strip()
    return out


def set_tags(record: str, values: dict[str, object]) -> str:
    """``record`` with these tags added, replacing any it already has.

    A value of None is left out rather than written empty: a missing number
    and a number that is zero must not read the same.
    """
    lines, keep, i = record.splitlines(), [], 0
    drop = set(values)
    while i < len(lines):
        m = _TAG.match(lines[i])
        if m and m.group(1) in drop:
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            continue
        keep.append(lines[i])
        i += 1
    while keep and not keep[-1].strip():
        keep.pop()
    out = "\n".join(keep) + "\n"
    add = {k: v for k, v in values.items() if v is not None}
    # A data item's value runs to a blank line, so one has to separate the
    # tags already there from the ones going on the end.
    if add and any(_TAG.match(x) for x in keep):
        out += "\n"
    for k, v in add.items():
        out += "> <%s>\n%s\n\n" % (k, v)
    return out


def number(text: str | None) -> float | None:
    """``text`` as a float, or None if it is not one."""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def heavy_atoms(record: str) -> int | None:
    """Atoms in the record that are not hydrogen, or None if its atom block
    cannot be read (a V3000 record, say).

    Counted here rather than from a SMILES string: the pose is what was
    scored, and its own atoms are what the score has to be divided by.
    """
    lines = record.splitlines()
    if len(lines) < 4 or "V2000" not in lines[3]:
        return None
    try:
        count = int(lines[3][0:3])
    except ValueError:
        return None
    if count <= 0 or len(lines) < 4 + count:
        return None
    heavy = 0
    for line in lines[4 : 4 + count]:
        element = line[31:34].strip() if len(line) >= 34 else ""
        if not element:
            return None
        if element.upper() != "H":
            heavy += 1
    return heavy
