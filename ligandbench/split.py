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

*Allocation.* Each repeat targets 80% train, 10% validation and 10% test. The
allocator builds up to ``CANDIDATES`` candidate allocations from a seed, larger
groups first, and keeps the valid one with the smallest squared deviation from
the target row counts (plus, for classification, from the per-class target
counts). A candidate is valid when every subset has at least two rows and is
within five percentage points of its target share, and, for classification,
holds every class. Nothing is relaxed: when no candidate is valid the answer is
``split_unavailable``, with what failed, and never a random split in its place.
The allocator found no valid split; that is not a proof that none exists.

*Reproducibility.* Nothing here uses ``hash()``, an unseeded generator or the
iteration order of a set. Repeat seeds and every tie-break are SHA-256 of the
seed and indices, so the same input, versions and seed give the same result in
any process, and ``canonical_json`` writes it with sorted keys so equal results
are equal bytes. ``ALGORITHM_VERSION`` changes when the answer for the same
input would.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .chem import require_rdkit
from .errors import BadInput
from .scaffold import SCAFFOLD_POLICY, murcko_scaffold

__all__ = [
    "ALGORITHM_VERSION",
    "CANDIDATES",
    "FORMAT",
    "Group",
    "REPEATS",
    "STRATEGIES",
    "SUBSETS",
    "TASKS",
    "canonical_json",
    "eligible_rows",
    "make_groups",
    "repeat_seed",
    "scaffold_groups",
    "size_windows",
    "split",
    "structure_groups",
]

#: The layout of the result; bump when it changes shape.
FORMAT = 1
#: Bump when the same dataset, strategy and seed would be assigned differently,
#: so a stored split says which allocator made it. A stored split is the record
#: of what was assigned; this is how to tell whether a rerun could differ.
ALGORITHM_VERSION = "group-greedy-v1"

STRATEGIES = ("random", "scaffold")
TASKS = ("regression", "classification")

REPEATS = 5
CANDIDATES = 256
SUBSETS = ("train", "validation", "test")
#: What each subset is aimed at, in percent, and how far from it it may be, in
#: percentage points; and the fewest rows a subset may have.
TARGET_PERCENT = (80, 10, 10)
TOLERANCE_POINTS = 5
MIN_ROWS = 2
#: Seeds are whole numbers below this: they fit a browser's integers and a
#: command line, and nothing here needs more.
MAX_SEED = 2**32


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


# -- the allocator -------------------------------------------------------------


def size_windows(n: int) -> list[tuple[int, int]]:
    """``(fewest, most)`` rows each subset may have out of ``n``: within
    ``TOLERANCE_POINTS`` of its target share (the ends included), and at least
    ``MIN_ROWS``. Whole numbers throughout, so no rounding decides a boundary.
    The window may be empty (``fewest`` above ``most``) for a small ``n``.
    """
    return [(max(lo, MIN_ROWS), hi) for lo, hi in _tolerance(n)]


def _tolerance(n: int) -> list[tuple[int, int]]:
    out = []
    for pct in TARGET_PERCENT:
        lo = -(-n * (pct - TOLERANCE_POINTS) // 100)  # ceiling
        hi = n * (pct + TOLERANCE_POINTS) // 100
        out.append((lo, hi))
    return out


def repeat_seed(seed: int, repeat: int) -> int:
    """The seed of one repeat: 48 bits of SHA-256 over the seed and the repeat's
    index, so repeats differ, depend on nothing else, and fit a JSON number.
    """
    digest = hashlib.sha256(("drughunter-split|%d|%d" % (seed, repeat)).encode()).digest()
    return int.from_bytes(digest[:6], "big")


@dataclass(frozen=True)
class _Problem:
    """What a candidate is built from: per group (in the order ``groups`` has them)
    its size, its lowest row id and its class counts; the row and class totals; and
    the tolerance window of each subset.
    """

    sizes: list[int]
    firsts: list[int]
    nz: list[tuple[tuple[int, int], ...]]  # per group: (class index, count), count > 0
    n: int
    totals: list[int]  # rows per class
    lo: list[int]  # tolerance window, before the minimum size
    hi: list[int]


@dataclass(frozen=True)
class _Candidate:
    index: int
    assign: list[int]  # subset index per group
    sizes: list[int]
    counts: list[list[int]]  # [subset][class]
    objective: int


# The objective is the sum of squared deviations of each subset's row count from
# its target count, plus the same for every class's count in each subset. Targets
# are tenths of a total, so each deviation is multiplied by ten and it is all
# whole numbers: the objective reported is 100 times the sum of squares.
_TENTHS = tuple(p // 10 for p in TARGET_PERCENT)


def _problem(groups: Sequence[Group], classes: Sequence[str], n: int) -> _Problem:
    tol = _tolerance(n)
    return _Problem(
        sizes=[g.size for g in groups],
        firsts=[g.first for g in groups],
        nz=[tuple((c, m) for c, m in enumerate(g.class_counts) if m) for g in groups],
        n=n,
        totals=[sum(g.class_counts[c] for g in groups) for c in range(len(classes))],
        lo=[lo for lo, _ in tol],
        hi=[hi for _, hi in tol],
    )


def _candidate(p: _Problem, seed: int, index: int) -> _Candidate:
    """One allocation. Groups are placed largest first, equal sizes in an order
    drawn from the seed; each goes where it adds least to the objective, unless
    that would push a subset past its window (then it goes elsewhere if it can).

    Candidate 0 follows the rule exactly. The others follow it with a
    deterministic chance of taking the second or third choice, rising with the
    index, and odd ones also prefer a subset that still lacks a class the group
    holds -- so a rare class has a way to reach every subset.
    """
    groups = len(p.sizes)
    draws = [
        hashlib.sha256(("%d|%d|%d" % (seed, index, first)).encode()).digest() for first in p.firsts
    ]
    order = sorted(range(groups), key=lambda i: (-p.sizes[i], draws[i][:8]))
    noise = 0 if index == 0 else 5 + (index * 45) // (CANDIDATES - 1)  # percent
    classed = len(p.totals)
    cover = classed > 0 and index % 2 == 1

    dev_size = [-t * p.n for t in _TENTHS]
    dev_class = [[-t * total for total in p.totals] for t in _TENTHS]
    sizes = [0, 0, 0]
    counts = [[0] * classed for _ in SUBSETS]
    assign = [0] * groups
    for i in order:
        g, d = p.sizes[i], draws[i]
        ranked = []
        for k in range(len(SUBSETS)):
            delta = 20 * g * dev_size[k] + 100 * g * g
            holes = 0
            for c, m in p.nz[i]:
                delta += 20 * m * dev_class[k][c] + 100 * m * m
                if cover and not counts[k][c]:
                    holes += 1
            ranked.append((sizes[k] + g > p.hi[k], -holes, delta, (k + d[16]) % 3, k))
        ranked.sort()
        pick = ranked[0]
        if noise and d[8] % 100 < noise:
            options = [r for r in ranked[1:] if not r[0]]
            if options:
                pick = options[d[9] % len(options)]
        k = pick[4]
        assign[i] = k
        sizes[k] += g
        dev_size[k] += 10 * g
        for c, m in p.nz[i]:
            counts[k][c] += m
            dev_class[k][c] += 10 * m
    objective = sum(x * x for x in dev_size) + sum(x * x for row in dev_class for x in row)
    return _Candidate(index, assign, sizes, counts, objective)


def _violations(p: _Problem, c: _Candidate) -> list[tuple[str, int, int]]:
    """``(constraint, subset, class)`` for each rule a candidate breaks; the
    class is ``-1`` where the rule is not about one.
    """
    bad = []
    for k in range(len(SUBSETS)):
        if c.sizes[k] < MIN_ROWS:
            bad.append(("min_size", k, -1))
        if not p.lo[k] <= c.sizes[k] <= p.hi[k]:
            bad.append(("size_tolerance", k, -1))
        bad.extend(("class_coverage", k, cls) for cls, m in enumerate(c.counts[k]) if m == 0)
    return bad


def _best_integer_split(total: int) -> int:
    """The smallest objective any whole-number split of ``total`` into three can
    have; a deviation is measured from ``TARGET_PERCENT`` of ``total``.
    """
    around = total // 10
    best = None
    for b in range(max(0, around - 3), around + 4):
        for c in range(max(0, around - 3), around + 4):
            a = total - b - c
            if a < 0:
                continue
            cost = sum((10 * s - t * total) ** 2 for s, t in zip((a, b, c), _TENTHS))
            best = cost if best is None or cost < best else best
    return best or 0


def _lower_bound(p: _Problem) -> int:
    """No allocation, valid or not, has a smaller objective. A valid candidate
    that reaches it cannot be beaten, and the search can stop there.
    """
    return _best_integer_split(p.n) + sum(_best_integer_split(t) for t in p.totals)


@dataclass(frozen=True)
class _Outcome:
    """What the candidates of one repeat came to: the best valid one, or none,
    and then what the nearest miss broke and how often each rule was broken.
    """

    best: _Candidate | None
    tried: int
    near: _Candidate | None
    near_broke: list[tuple[str, int, int]]
    broke: dict[tuple[str, int, int], int]


def _allocate(p: _Problem, seed: int) -> _Outcome:
    best = near = None
    near_key: tuple | None = None
    near_broke: list[tuple[str, int, int]] = []
    broke: dict[tuple[str, int, int], int] = {}
    floor = _lower_bound(p)
    tried = 0
    for index in range(CANDIDATES):
        cand = _candidate(p, seed, index)
        tried += 1
        bad = _violations(p, cand)
        if not bad:
            # Ties keep the earlier candidate: the lowest index is the stable breaker.
            if best is None or cand.objective < best.objective:
                best = cand
            if cand.objective == floor:
                break
            continue
        if best is not None:
            continue
        for rule in bad:
            broke[rule] = broke.get(rule, 0) + 1
        key = (len(bad), cand.objective, cand.index)
        if near_key is None or key < near_key:
            near, near_key, near_broke = cand, key, bad
    return _Outcome(best, tried, near, near_broke, broke)


# -- the result ------------------------------------------------------------------


def split(
    meta: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *, strategy: str, seed: int
) -> dict:
    """Five repeated holdouts of a resolved dataset, or why there are none.

    ``meta`` and ``rows`` are ``dataset.json`` and ``rows.json``'s rows. The
    result is ``{"status": "ok", "repeats": [five], ...}`` or ``{"status":
    "split_unavailable", "repeats": [], "unavailable": {...}, ...}``; both carry
    what is needed to say how they were made. ``BadInput`` for a choice or a
    dataset that cannot be split at all.
    """
    if strategy not in STRATEGIES:
        raise BadInput("unknown split strategy %r (want %s)" % (strategy, " or ".join(STRATEGIES)))
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < MAX_SEED:
        raise BadInput("the seed must be a whole number from 0 to %d" % (MAX_SEED - 1))
    use, excluded = eligible_rows(meta, rows)
    classes = list(meta.get("classes") or []) if meta["target_kind"] == "classification" else []
    groups = make_groups(strategy, use, classes)
    problem = _problem(groups, classes, len(use))
    seeds = [repeat_seed(seed, r) for r in range(REPEATS)]
    out: dict[str, Any] = {
        "format": FORMAT,
        "algorithm": {
            "version": ALGORITHM_VERSION,
            "candidates": CANDIDATES,
            "repeats": REPEATS,
            "target_percent": dict(zip(SUBSETS, TARGET_PERCENT)),
            "tolerance_points": TOLERANCE_POINTS,
            "min_rows": MIN_ROWS,
        },
        "strategy": strategy,
        "seed": seed,
        "repeat_seeds": seeds,
        "rdkit": require_rdkit(),
        "scaffold_policy": SCAFFOLD_POLICY,
        "dataset": {
            "content_hash": meta.get("content_hash"),
            "identity": meta.get("identity"),
            # What decided which rows may train: the structure column and the target as it
            # was confirmed (column, task, qualifier column, units, transform), under the
            # dataset layout's format. The rows themselves are in row_ids and excluded.
            "format": meta.get("format"),
            "structure_column": meta.get("structure_column"),
            "target": dict(meta["target"]) if meta.get("target") else None,
            "task": meta["target_kind"],
            "classes": classes,
            "n_rows": len(rows),
        },
        "n_eligible": len(use),
        "n_groups": len(groups),
        "row_ids": [r["row_id"] for r in use],
        "excluded": excluded,
    }
    repeats = []
    for r, rseed in enumerate(seeds):
        outcome = _allocate(problem, rseed)
        if outcome.best is None:
            out.update(
                status="split_unavailable",
                repeats=[],
                unavailable=_unavailable(problem, groups, classes, outcome, strategy, r),
            )
            return out
        repeats.append(_repeat(r, rseed, outcome, groups, classes))
    out.update(status="ok", repeats=repeats, unavailable=None)
    return out


def _repeat(
    r: int, seed: int, outcome: _Outcome, groups: Sequence[Group], classes: Sequence[str]
) -> dict:
    cand = outcome.best
    assert cand is not None
    members: list[list[int]] = [[] for _ in SUBSETS]
    for group, k in zip(groups, cand.assign):
        members[k].extend(group.row_ids)
    out: dict[str, Any] = {
        "repeat": r,
        "seed": seed,
        "candidate": cand.index,
        "candidates_tried": outcome.tried,
        "objective": cand.objective,
        "sizes": dict(zip(SUBSETS, cand.sizes)),
        "class_counts": {name: dict(zip(classes, cand.counts[k])) for k, name in enumerate(SUBSETS)}
        if classes
        else {},
    }
    for name, ids in zip(SUBSETS, members):
        out[name] = sorted(ids)
    return out


def _unavailable(
    p: _Problem,
    groups: Sequence[Group],
    classes: Sequence[str],
    outcome: _Outcome,
    strategy: str,
    repeat: int,
) -> dict:
    """What a failed repeat can say: how the rows are grouped, what each subset
    was allowed, and which rules the nearest miss broke and how many of the
    candidates broke each. It says what the allocator found, not that nothing
    could be found.
    """
    near = outcome.near
    assert near is not None
    windows = size_windows(p.n)
    ranked = sorted(groups, key=lambda g: (-g.size, g.first))
    by_size: dict[int, int] = {}
    for g in groups:
        by_size[g.size] = by_size.get(g.size, 0) + 1
    failed = []
    for rule, k, cls in outcome.near_broke:
        name = SUBSETS[k]
        entry: dict[str, Any] = {
            "constraint": rule,
            "subset": name,
            "candidates_violating": outcome.broke[(rule, k, cls)],
        }
        size = near.sizes[k]
        if rule == "min_size":
            entry["size"] = size
            entry["required"] = MIN_ROWS
            entry["detail"] = "%s has %d rows; every subset needs at least %d" % (
                name,
                size,
                MIN_ROWS,
            )
        elif rule == "size_tolerance":
            entry["size"] = size
            entry["allowed"] = [p.lo[k], p.hi[k]]
            entry["detail"] = (
                "%s has %d of %d rows; within %d points of %d%% it may have %d to %d"
                % (name, size, p.n, TOLERANCE_POINTS, TARGET_PERCENT[k], p.lo[k], p.hi[k])
            )
        else:
            entry["class"] = classes[cls]
            entry["class_total"] = p.totals[cls]
            entry["detail"] = "%s has no row of class %r (%d rows of it in all)" % (
                name,
                classes[cls],
                p.totals[cls],
            )
        failed.append(entry)
    largest = ranked[0]
    return {
        "repeat": repeat,
        "message": (
            "the allocator found no valid %s split: none of the %d candidate allocations tried "
            "for repeat %d kept every group whole and met the size and class rules. This is what "
            "the allocator found, not a proof that no split exists. The largest group holds %d of "
            "%d rows."
        )
        % (
            strategy,
            outcome.tried,
            repeat,
            largest.size,
            p.n,
        ),
        "candidates_tried": outcome.tried,
        "n_rows": p.n,
        "n_groups": len(groups),
        "group_size_counts": [[s, by_size[s]] for s in sorted(by_size, reverse=True)],
        "largest_groups": [
            {
                "key": g.key,
                "n_rows": g.size,
                "n_structures": g.n_structures,
                "class_counts": dict(zip(classes, g.class_counts)),
            }
            for g in ranked[:10]
        ],
        "class_counts": dict(zip(classes, p.totals)),
        "size_windows": {name: {"min": lo, "max": hi} for name, (lo, hi) in zip(SUBSETS, windows)},
        "failed_constraints": failed,
        "nearest_candidate": {
            "candidate": near.index,
            "objective": near.objective,
            "sizes": dict(zip(SUBSETS, near.sizes)),
        },
    }


def canonical_json(result: Mapping[str, Any]) -> str:
    """The result as the bytes to keep: sorted keys, no spaces, ASCII only, and
    no NaN. Equal results are equal strings, in any process and any locale.
    """
    return json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
