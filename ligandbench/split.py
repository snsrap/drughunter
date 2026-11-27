"""Five repeated holdouts of a ligand dataset, by random or scaffold groups.

Pure: a resolved dataset (``dataset.json``'s metadata and ``rows.json``'s rows,
as ``ligandbench resolve`` writes them), a strategy and a seed in; a result out.
It knows no dataset id and writes nothing -- the caller stores what it is given.

*Eligibility.* Only rows the resolution marked ``training_eligible`` are split;
every other row is returned in ``excluded`` with the reasons it carried. A
dataset without a target cannot be split.

*Groups.* A split moves whole groups, never rows out of one. Rows with the same
structure identity (``smiles``, the canonical isomeric SMILES) are one group
whichever strategy is used, so a duplicate never leaks across subsets. The
scaffold strategy merges structure groups that share a Bemis-Murcko scaffold
(``scaffold.murcko_scaffold``), and every molecule without a ring -- scaffold
``""`` -- is one group. A group is never broken to improve a proportion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import BadInput
from .scaffold import murcko_scaffold

__all__ = [
    "Group",
    "STRATEGIES",
    "TASKS",
    "eligible_rows",
    "structure_groups",
    "scaffold_groups",
    "make_groups",
]

STRATEGIES = ("random", "scaffold")
TASKS = ("regression", "classification")


@dataclass(frozen=True)
class Group:
    """Rows that must stay together.

    ``key`` is the structure identity (random strategy) or the scaffold (scaffold
    strategy; ``""`` is the group of molecules with no ring). ``row_ids`` ascend.
    ``class_counts`` follows the dataset's ``classes`` and is empty for regression.
    """

    key: str
    row_ids: tuple[int, ...]
    n_structures: int
    class_counts: tuple[int, ...]

    @property
    def size(self) -> int:
        return len(self.row_ids)

    @property
    def first(self) -> int:
        """The lowest row id: the group's name wherever one is needed that does
        not depend on the order rows were handed in.
        """
        return self.row_ids[0]


def eligible_rows(
    meta: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[list[dict], list[dict]]:
    """``(rows to split, excluded)`` for a resolved dataset.

    The first are ``{"row_id", "smiles", "label"}`` by ascending ``row_id``
    (``label`` is the class, or ``None`` for regression); the second are
    ``{"row_id", "reasons"}`` by ascending ``row_id``. ``BadInput`` for a
    dataset that has no target or nothing to train on.
    """
    if not meta.get("trainable") or meta.get("target_kind") not in TASKS:
        raise BadInput(
            "this dataset has no target, so it cannot be split for training; "
            "resolve it with a target column"
        )
    classification = meta["target_kind"] == "classification"
    classes = list(meta.get("classes") or [])
    if classification and len(classes) < 2:
        raise BadInput("a classification dataset needs at least two classes to be split")

    use: dict[int, dict] = {}
    out: dict[int, dict] = {}
    for row in rows:
        rid = row.get("row_id")
        if not isinstance(rid, int) or isinstance(rid, bool) or rid in use or rid in out:
            raise BadInput("row ids must be distinct whole numbers (saw %r)" % (rid,))
        if not row.get("training_eligible"):
            out[rid] = {"row_id": rid, "reasons": list(row.get("exclusions") or [])}
            continue
        smiles = row.get("smiles")
        if not isinstance(smiles, str) or not smiles:
            raise BadInput("row %d is marked trainable but has no structure" % rid)
        label = None
        if classification:
            label = row.get("target")
            if label not in classes:
                raise BadInput(
                    "row %d has the label %r, which is not one of the classes" % (rid, label)
                )
        elif row.get("target") is None:
            raise BadInput("row %d is marked trainable but has no target" % rid)
        use[rid] = {"row_id": rid, "smiles": smiles, "label": label}
    if not use:
        raise BadInput("no row of this dataset can be trained on, so there is nothing to split")
    return [use[i] for i in sorted(use)], [out[i] for i in sorted(out)]


def structure_groups(rows: Sequence[Mapping[str, Any]], classes: Sequence[str]) -> list[Group]:
    """One group per structure identity, by the lowest row id in it."""
    index = {c: i for i, c in enumerate(classes)}
    found: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        found.setdefault(row["smiles"], []).append(row)
    groups = []
    for smiles, members in found.items():
        counts = [0] * len(classes)
        for row in members:
            if classes:
                counts[index[row["label"]]] += 1
        groups.append(Group(smiles, tuple(sorted(r["row_id"] for r in members)), 1, tuple(counts)))
    return sorted(groups, key=lambda g: g.first)


def scaffold_groups(rows: Sequence[Mapping[str, Any]], classes: Sequence[str]) -> list[Group]:
    """One group per Bemis-Murcko scaffold, built from the structure groups so
    that none of those is ever divided. Every acyclic molecule shares ``""``.
    """
    merged: dict[str, list[Group]] = {}
    for group in structure_groups(rows, classes):
        try:
            scaffold = murcko_scaffold(group.key)
        except BadInput as exc:
            raise BadInput("row %d: %s" % (group.first, exc)) from None
        merged.setdefault(scaffold, []).append(group)
    out = []
    for scaffold, parts in merged.items():
        counts = tuple(sum(p.class_counts[c] for p in parts) for c in range(len(classes)))
        ids = tuple(sorted(i for p in parts for i in p.row_ids))
        out.append(Group(scaffold, ids, sum(p.n_structures for p in parts), counts))
    return sorted(out, key=lambda g: g.first)


def make_groups(
    strategy: str, rows: Sequence[Mapping[str, Any]], classes: Sequence[str]
) -> list[Group]:
    """The groups a strategy moves, ordered by their lowest row id."""
    if strategy == "random":
        return structure_groups(rows, classes)
    if strategy == "scaffold":
        return scaffold_groups(rows, classes)
    raise BadInput("unknown split strategy %r (want %s)" % (strategy, " or ".join(STRATEGIES)))
