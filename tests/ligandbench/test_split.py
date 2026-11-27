"""Split generation: which rows may be split, and the groups that move whole.

The allocator and the result are tested further down, from the same synthetic
datasets. Nothing below trusts the allocator's own word: every property of a
split is recomputed from the rows (``check_repeat``).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from ligandbench import split as split_module  # noqa: E402
from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.resolve import resolve  # noqa: E402
from ligandbench.scaffold import SCAFFOLD_POLICY, murcko_scaffold  # noqa: E402
from ligandbench.split import (  # noqa: E402
    ALGORITHM_VERSION,
    CANDIDATES,
    REPEATS,
    SUBSETS,
    canonical_json,
    eligible_rows,
    make_groups,
    repeat_seed,
    scaffold_groups,
    size_windows,
    split,
    structure_groups,
)

DATA = Path(__file__).parent / "data"
CDD = (DATA / "cdd_export_hiv1_protease.csv").read_bytes()

# Rings with a place to hang a chain: a molecule is CORES[i] with k carbons in
# front of it, so the same core with different k is one scaffold and different
# structures, and a different core is a different scaffold.
CORES = (
    "c1ccccc1",
    "c1ccncc1",
    "C1CCCCC1",
    "c1ccc2ccccc2c1",
    "c1ccsc1",
    "c1ccoc1",
    "C1CCNCC1",
    "c1ccc2[nH]ccc2c1",
    "c1ccnc2ccccc12",
    "C1CCOC1",
    "c1cnccn1",
    "C1CCCC1",
    "C1CC1",
    "C1CCC1",
    "C1CCCCCC1",
    "c1cncnc1",
    "c1ccnnc1",
    "c1cc[nH]c1",
    "c1c[nH]cn1",
    "c1cocn1",
    "c1cscn1",
    "C1COCCN1",
    "C1CNCCN1",
    "C1CCOCC1",
)


def mol(core, k):
    """A molecule on ring ``core`` with a chain of ``k`` carbons; ``core`` None
    is the chain alone (no ring), which needs ``k`` of at least 2.
    """
    return "C" * k + (CORES[core] if core is not None else "O")


def csv_of(pairs):
    return ("smiles,y\n" + "\n".join("%s,%s" % p for p in pairs) + "\n").encode()


def classification(positive="1"):
    return {
        "structure_column": "smiles",
        "target": {
            "column": "y",
            "kind": "classification",
            "qualifier_column": None,
            "positive_class": positive,
        },
    }


def regression():
    return {
        "structure_column": "smiles",
        "target": {
            "column": "y",
            "kind": "regression",
            "qualifier_column": None,
            "transform": "identity",
            "source_units": "",
        },
    }


def dataset(pairs, kind="classification"):
    """``(meta, rows)`` as ``ligandbench resolve`` makes them, from ``(smiles, y)``."""
    return resolve(csv_of(pairs), classification() if kind == "classification" else regression())


# -- which rows are split ------------------------------------------------------


def test_only_training_eligible_rows_are_split_and_the_rest_say_why():
    pairs = [
        ("CCO", 1),
        ("CCN", 0),
        ("not-smiles", 1),  # a structure that does not parse
        ("CCC", ""),  # a blank label
        ("CCCC", 0),
    ]
    meta, rows = dataset(pairs)
    use, excluded = eligible_rows(meta, rows)
    assert [r["row_id"] for r in use] == [0, 1, 4]
    assert excluded == [
        {"row_id": 2, "reasons": ["invalid_structure"]},
        {"row_id": 3, "reasons": ["missing_target"]},
    ]
    assert [r["label"] for r in use] == ["1", "0", "0"]


def test_a_censored_regression_row_is_excluded_not_split():
    meta, rows = resolve(
        b"smiles,y\nCCO,1.5\nCCN,<2\nCCC,3\n",
        regression(),
    )
    use, excluded = eligible_rows(meta, rows)
    assert [r["row_id"] for r in use] == [0, 2]
    assert excluded == [{"row_id": 1, "reasons": ["censored"]}]
    assert all(r["label"] is None for r in use)


def test_rows_are_taken_in_row_id_order_whatever_order_they_arrive_in():
    meta, rows = dataset([("CCO", 1), ("CCN", 0), ("CCC", 1)])
    use, _ = eligible_rows(meta, list(reversed(rows)))
    assert [r["row_id"] for r in use] == [0, 1, 2]


def test_a_dataset_without_a_target_cannot_be_split():
    meta, rows = resolve(
        csv_of([("CCO", 1), ("CCN", 0)]), {"structure_column": "smiles", "target": None}
    )
    assert meta["trainable"] is False
    with pytest.raises(BadInput, match="no target"):
        eligible_rows(meta, rows)


def test_rows_that_contradict_the_dataset_are_refused_not_guessed_at():
    meta, rows = dataset([("CCO", 1), ("CCN", 0)])
    with pytest.raises(BadInput, match="distinct whole numbers"):
        eligible_rows(meta, rows + [dict(rows[0])])
    broken = [dict(rows[0], target="maybe"), rows[1]]
    with pytest.raises(BadInput, match="not one of the classes"):
        eligible_rows(meta, broken)
    with pytest.raises(BadInput, match="no structure"):
        eligible_rows(meta, [dict(rows[0], smiles=None), rows[1]])
    with pytest.raises(BadInput, match="nothing to split"):
        eligible_rows(meta, [dict(r, training_eligible=False) for r in rows])


# -- structure groups -----------------------------------------------------------


def test_identical_structures_are_one_group_however_they_were_written():
    # three spellings of ethanol, two of benzene, and the stereoisomers kept apart
    pairs = [
        ("CCO", 1),
        ("OCC", 0),  # conflicting label, same structure
        ("C(C)O", 1),
        ("c1ccccc1", 1),
        ("C1=CC=CC=C1", 1),
        ("C[C@H](N)C(=O)O", 1),
        ("C[C@@H](N)C(=O)O", 0),
    ]
    meta, rows = dataset(pairs)
    use, _ = eligible_rows(meta, rows)
    groups = structure_groups(use, meta["classes"])
    by_ids = {g.row_ids for g in groups}
    assert by_ids == {(0, 1, 2), (3, 4), (5,), (6,)}
    ethanol = next(g for g in groups if g.row_ids == (0, 1, 2))
    # the conflicting labels are not resolved: the group carries both, and moves whole
    assert dict(zip(meta["classes"], ethanol.class_counts)) == {"1": 2, "0": 1}
    assert [g.first for g in groups] == [0, 3, 5, 6], "ordered by their lowest row id"
    assert all(g.n_structures == 1 for g in groups)


def test_regression_groups_carry_no_class_counts():
    meta, rows = dataset([("CCO", 1.0), ("OCC", 2.0), ("CCN", 3.0)], kind="regression")
    use, _ = eligible_rows(meta, rows)
    groups = structure_groups(use, meta["classes"])
    assert [g.row_ids for g in groups] == [(0, 1), (2,)]
    assert all(g.class_counts == () for g in groups)


# -- scaffold groups -------------------------------------------------------------


def test_molecules_on_one_scaffold_are_one_group_and_no_structure_group_is_divided():
    pairs = [(mol(0, k), k % 2) for k in (1, 2, 3)]  # benzene with three chains
    pairs += [(mol(0, 2), 0)]  # a duplicate of one of them
    pairs += [(mol(1, k), 1) for k in (1, 2)]  # pyridine
    meta, rows = dataset(pairs)
    use, _ = eligible_rows(meta, rows)
    groups = scaffold_groups(use, meta["classes"])
    assert {g.key: g.row_ids for g in groups} == {
        murcko_scaffold(mol(0, 1)): (0, 1, 2, 3),
        murcko_scaffold(mol(1, 1)): (4, 5),
    }
    benzene = groups[0]
    assert benzene.n_structures == 3, "the duplicate is one structure, kept with its twin"
    structure = {g.key: g.row_ids for g in structure_groups(use, meta["classes"])}
    for s_ids in structure.values():
        assert any(set(s_ids) <= set(g.row_ids) for g in groups), "a structure group stayed whole"


def test_stereoisomers_share_a_scaffold_group():
    meta, rows = dataset([("C[C@H](N)c1ccccc1", 1), ("C[C@@H](N)c1ccccc1", 0), ("CCc1ccccc1", 1)])
    use, _ = eligible_rows(meta, rows)
    assert len(structure_groups(use, meta["classes"])) == 3
    groups = scaffold_groups(use, meta["classes"])
    assert [g.row_ids for g in groups] == [(0, 1, 2)]


def test_every_acyclic_molecule_is_one_group_with_the_empty_scaffold():
    pairs = [(mol(None, k), k % 2) for k in range(2, 8)] + [(mol(0, 1), 1), (mol(0, 2), 0)]
    meta, rows = dataset(pairs)
    use, _ = eligible_rows(meta, rows)
    groups = scaffold_groups(use, meta["classes"])
    acyclic = [g for g in groups if g.key == ""]
    assert len(acyclic) == 1 and acyclic[0].row_ids == (0, 1, 2, 3, 4, 5)
    assert acyclic[0].n_structures == 6
    assert len(groups) == 2


def test_make_groups_picks_the_strategy_and_refuses_an_unknown_one():
    meta, rows = dataset([(mol(0, 1), 1), (mol(0, 2), 0), (mol(1, 1), 1)])
    use, _ = eligible_rows(meta, rows)
    assert len(make_groups("random", use, meta["classes"])) == 3
    assert len(make_groups("scaffold", use, meta["classes"])) == 2
    with pytest.raises(BadInput, match="unknown split strategy 'cluster'"):
        make_groups("cluster", use, meta["classes"])


def test_groups_do_not_depend_on_the_order_the_rows_came_in():
    pairs = [(mol(c, k), (c + k) % 2) for c in range(4) for k in (1, 2, 3)]
    meta, rows = dataset(pairs)
    use, _ = eligible_rows(meta, rows)
    for strategy in ("random", "scaffold"):
        forward = make_groups(strategy, use, meta["classes"])
        backward = make_groups(strategy, list(reversed(use)), meta["classes"])
        assert forward == backward


def test_the_cdd_export_groups_into_scaffolds_that_keep_its_structures_whole():
    meta, rows = resolve(
        CDD,
        {
            "structure_column": "SMILES",
            "target": {
                "column": "HIV-1 PR FRET (Q7K): Ki (nM)",
                "kind": "regression",
                "qualifier_column": "HIV-1 PR FRET (Q7K): Ki Qualifier",
                "transform": "pki",
                "source_units": "nM",
            },
        },
    )
    use, excluded = eligible_rows(meta, rows)
    assert len(use) == 102 and len(excluded) == 4
    groups = scaffold_groups(use, meta["classes"])
    assert sum(g.size for g in groups) == 102
    assert sorted(i for g in groups for i in g.row_ids) == [r["row_id"] for r in use]


# -- what a split must satisfy, recomputed from the rows ---------------------------


def check_repeat(result, repeat, meta, rows, strategy):
    """Every promise of a valid repeat, checked from the rows and the result's
    own ``row_ids`` and ``excluded``, never from the allocator's claims.
    """
    by_id = {r["row_id"]: r for r in rows}
    parts = [repeat[name] for name in SUBSETS]
    flat = [i for part in parts for i in part]
    n = len(result["row_ids"])
    # each eligible row exactly once; no excluded row ever
    assert sorted(flat) == result["row_ids"] and len(flat) == len(set(flat)) == n
    assert not set(flat) & {e["row_id"] for e in result["excluded"]}
    assert all(by_id[i]["training_eligible"] for i in flat)
    assert all(part == sorted(part) for part in parts)
    # at least two rows, and within five points of the target share (ends included)
    for part, pct in zip(parts, (80, 10, 10)):
        assert len(part) >= 2
        assert abs(100 * len(part) - pct * n) <= 5 * n
    assert repeat["sizes"] == dict(zip(SUBSETS, map(len, parts)))
    # every class in every subset
    if meta["target_kind"] == "classification":
        for part in parts:
            assert {by_id[i]["target"] for i in part} == set(meta["classes"])
    # no structure on both sides of any pair of subsets
    for a in range(3):
        for b in range(a + 1, 3):
            assert not {by_id[i]["smiles"] for i in parts[a]} & {
                by_id[i]["smiles"] for i in parts[b]
            }
            if strategy == "scaffold":
                scaffolds = [
                    {murcko_scaffold(by_id[i]["smiles"]) for i in parts[k]} for k in (a, b)
                ]
                assert not scaffolds[0] & scaffolds[1]


def check_ok(result, meta, rows, strategy):
    assert result["status"] == "ok" and result["unavailable"] is None
    assert result["strategy"] == strategy and len(result["repeats"]) == REPEATS == 5
    assert [r["repeat"] for r in result["repeats"]] == [0, 1, 2, 3, 4]
    for repeat in result["repeats"]:
        check_repeat(result, repeat, meta, rows, strategy)


def scaffold_dataset(groups=20, per=5, kind="classification"):
    """``groups`` scaffolds of ``per`` structures each, every group holding both labels."""
    pairs = [(mol(c, k), (c + k) % 2) for c in range(groups) for k in range(1, per + 1)]
    return dataset(pairs, kind=kind)


def test_the_synthetic_cores_are_distinct_scaffolds():
    scaffolds = {murcko_scaffold(mol(c, 1)) for c in range(len(CORES))}
    assert len(scaffolds) == len(CORES) and "" not in scaffolds
    assert len({murcko_scaffold(mol(0, k)) for k in range(1, 30)}) == 1


# -- the windows -------------------------------------------------------------------


def test_the_size_windows_are_the_target_share_within_five_points_and_at_least_two():
    assert size_windows(100) == [(75, 85), (5, 15), (5, 15)]
    assert size_windows(40) == [(30, 34), (2, 6), (2, 6)]
    assert size_windows(102) == [
        (77, 86),
        (6, 15),
        (6, 15),
    ]  # 76.5 up, 86.7 down, 5.1 up, 15.3 down
    train, val, test = size_windows(10)
    assert train == (8, 8) and val[0] > val[1] and test[0] > test[1], (
        "no whole number of rows is both at least two and within 15% of ten"
    )
    assert size_windows(20)[1] == (2, 3)


# -- both strategies, on a dataset where both can be satisfied -----------------------


@pytest.mark.parametrize("strategy", ["random", "scaffold"])
@pytest.mark.parametrize("kind", ["classification", "regression"])
def test_both_strategies_give_five_valid_repeats_on_a_feasible_dataset(strategy, kind):
    meta, rows = scaffold_dataset(kind=kind)
    result = split(meta, rows, strategy=strategy, seed=7)
    check_ok(result, meta, rows, strategy)
    assert result["dataset"]["task"] == kind
    assert result["n_groups"] == (100 if strategy == "random" else 20)


def test_repeats_are_different_holdouts_not_copies_of_one():
    meta, rows = scaffold_dataset()
    result = split(meta, rows, strategy="random", seed=3)
    tests = {tuple(r["test"]) for r in result["repeats"]}
    assert len(tests) == 5
    assert len({r["seed"] for r in result["repeats"]}) == 5


def test_a_scaffold_is_never_on_two_sides_even_when_it_would_balance_the_split_better():
    # twenty scaffolds of five: a split by structure could balance 80/10/10 exactly, a
    # scaffold split has to put whole scaffolds of five on each side
    meta, rows = scaffold_dataset()
    result = split(meta, rows, strategy="scaffold", seed=1)
    check_ok(result, meta, rows, "scaffold")
    for repeat in result["repeats"]:
        assert all(size % 5 == 0 for size in repeat["sizes"].values())


def test_excluded_rows_are_never_split_and_say_why():
    pairs = [(mol(c, k), (c + k) % 2) for c in range(20) for k in range(1, 6)]
    pairs[3] = ("not-smiles", 1)
    pairs[40] = (pairs[40][0], "")
    meta, rows = dataset(pairs)
    for strategy in ("random", "scaffold"):
        result = split(meta, rows, strategy=strategy, seed=11)
        check_ok(result, meta, rows, strategy)
        assert result["excluded"] == [
            {"row_id": 3, "reasons": ["invalid_structure"]},
            {"row_id": 40, "reasons": ["missing_target"]},
        ]
        assert result["n_eligible"] == 98 and result["dataset"]["n_rows"] == 100
        assert 3 not in result["row_ids"] and 40 not in result["row_ids"]


def test_duplicate_structures_never_leak_and_conflicting_labels_move_together():
    pairs = []
    for c in range(20):
        for k in range(1, 6):
            pairs.append((mol(c, k), (c + k) % 2))
    # every fifth structure is written twice, the copy with the other label
    pairs += [(pairs[i][0], 1 - int(pairs[i][1])) for i in range(0, 100, 5)]
    meta, rows = dataset(pairs)
    assert meta["duplicates"]["n_conflicting"] == 20
    for strategy in ("random", "scaffold"):
        result = split(meta, rows, strategy=strategy, seed=5)
        check_ok(result, meta, rows, strategy)
        for repeat in result["repeats"]:
            side = {i: name for name in SUBSETS for i in repeat[name]}
            for i in range(0, 100, 5):
                assert side[i] == side[100 + i // 5], "a duplicate was split from its original"


# -- when the rules cannot be met -----------------------------------------------------


def test_a_dominant_scaffold_makes_the_scaffold_split_unavailable_not_relaxed():
    # ninety of a hundred on benzene: more than the 85% the train subset may hold
    pairs = [(mol(0, k), k % 2) for k in range(1, 91)]
    pairs += [(mol(c, 1), c % 2) for c in range(1, 11)]
    meta, rows = dataset(pairs)
    scaffold = split(meta, rows, strategy="scaffold", seed=1)
    assert scaffold["status"] == "split_unavailable" and scaffold["repeats"] == []
    why = scaffold["unavailable"]
    assert why["largest_groups"][0]["n_rows"] == 90 and why["n_groups"] == 11
    assert why["largest_groups"][0]["key"] == murcko_scaffold(mol(0, 1))
    assert why["size_windows"]["train"] == {"min": 75, "max": 85}
    assert why["candidates_tried"] == CANDIDATES
    failed = {(f["constraint"], f["subset"]) for f in why["failed_constraints"]}
    assert ("size_tolerance", "train") in failed or ("size_tolerance", "validation") in failed
    assert all(f["detail"] for f in why["failed_constraints"])
    assert "no valid scaffold split" in why["message"] and "not a proof" in why["message"]
    assert "infeasible" not in why["message"] and "impossible" not in why["message"]
    # the structures are as splittable as ever: the other strategy is not touched by it
    check_ok(split(meta, rows, strategy="random", seed=1), meta, rows, "random")
    # and nothing was substituted: there is no 'random' repeat under the scaffold answer
    assert "train" not in scaffold and scaffold["strategy"] == "scaffold"


def test_acyclic_molecules_are_one_group_so_scaffold_is_unavailable_and_random_is_not():
    pairs = [(mol(None, k), k % 2) for k in range(2, 102)]
    meta, rows = dataset(pairs)
    scaffold = split(meta, rows, strategy="scaffold", seed=2)
    assert scaffold["status"] == "split_unavailable"
    only = scaffold["unavailable"]["largest_groups"]
    assert len(only) == 1 and only[0]["key"] == "" and only[0]["n_rows"] == 100
    assert scaffold["unavailable"]["group_size_counts"] == [[100, 1]]
    check_ok(split(meta, rows, strategy="random", seed=2), meta, rows, "random")


@pytest.mark.parametrize("n", [3, 5, 8, 10])
def test_a_tiny_dataset_is_unavailable_and_says_which_size_rule_it_fails(n):
    meta, rows = dataset([(mol(None, k + 2), k % 2) for k in range(n)])
    for strategy in ("random", "scaffold"):
        result = split(meta, rows, strategy=strategy, seed=1)
        assert result["status"] == "split_unavailable"
        why = result["unavailable"]
        assert why["n_rows"] == n and why["failed_constraints"]
        kinds = {f["constraint"] for f in why["failed_constraints"]}
        assert kinds & {"min_size", "size_tolerance", "class_coverage"}
        for f in why["failed_constraints"]:
            assert f["subset"] in SUBSETS and f["candidates_violating"] >= 1


@pytest.mark.parametrize("n", [10, 11, 12, 13, 14, 15])
def test_a_subset_of_one_row_is_refused_though_the_tolerance_alone_would_allow_it(n):
    # Ten rows: 5% to 15% of ten is 0.5 to 1.5, so the tolerance gives validation and test
    # exactly one row each and train eight. 8/1/1 is inside every window, but a subset needs
    # two rows: the minimum size is a rule of its own, and the only one broken here.
    assert split_module._tolerance(n)[1][0] == 1 and size_windows(n)[1][0] == 2
    meta, rows = dataset([(mol(None, k + 2), float(k)) for k in range(n)], kind="regression")
    result = split(meta, rows, strategy="random", seed=1)
    assert result["status"] == "split_unavailable" and result["repeats"] == []
    failed = result["unavailable"]["failed_constraints"]
    assert {f["constraint"] for f in failed} == {"min_size"}, (
        "the nearest candidate fits the tolerance"
    )
    assert all(f["size"] < 2 and f["required"] == 2 for f in failed)
    assert all(f["candidates_violating"] >= 1 and f["subset"] != "train" for f in failed)


def test_the_smallest_dataset_that_can_be_split_is_split():
    # twenty rows: 16 / 2 / 2 is within five points of 80/10/10 and has two in each
    meta, rows = dataset([(mol(None, k + 2), 0) for k in range(20)], kind="regression")
    result = split(meta, rows, strategy="random", seed=1)
    check_ok(result, meta, rows, "random")
    assert all(r["sizes"] == {"train": 16, "validation": 2, "test": 2} for r in result["repeats"])


def test_a_class_too_rare_to_reach_every_subset_makes_the_split_unavailable():
    pairs = [(mol(c, k), 0) for c in range(6) for k in range(1, 11)]
    for i in (5, 40):  # two rows of a class, and three subsets to put it in
        pairs[i] = (pairs[i][0], 1)
    meta, rows = dataset(pairs)
    for strategy in ("random", "scaffold"):
        result = split(meta, rows, strategy=strategy, seed=1)
        assert result["status"] == "split_unavailable"
        why = result["unavailable"]
        assert why["class_counts"] == {"0": 58, "1": 2}
    random = split(meta, rows, strategy="random", seed=1)["unavailable"]
    assert any(
        f["constraint"] == "class_coverage" and f["class"] == "1"
        for f in random["failed_constraints"]
    )


@pytest.mark.parametrize("seed", range(6))
def test_a_class_of_exactly_three_rows_reaches_every_subset(seed):
    pairs = [(mol(c, k), 0) for c in range(6) for k in range(1, 11)]
    for i in (5, 17, 40):
        pairs[i] = (pairs[i][0], 1)
    meta, rows = dataset(pairs)
    result = split(meta, rows, strategy="random", seed=seed)
    check_ok(result, meta, rows, "random")
    for repeat in result["repeats"]:
        assert all(repeat["class_counts"][name]["1"] == 1 for name in SUBSETS)


def test_a_valid_split_with_imperfect_proportions_is_accepted_not_rejected_for_them():
    # 33 + 4 + 3 rows: train is 82.5% of 40, test 7.5% -- off the 80/10/10 target but
    # inside five points, and no way of keeping the three scaffolds whole is nearer
    pairs = [(mol(0, k), k % 2) for k in range(1, 34)]
    pairs += [(mol(1, k), k % 2) for k in range(1, 5)]
    pairs += [(mol(2, k), k % 2) for k in range(1, 4)]
    for kind in ("regression", "classification"):
        meta, rows = dataset(pairs, kind=kind)
        result = split(meta, rows, strategy="scaffold", seed=4)
        check_ok(result, meta, rows, "scaffold")
        for repeat in result["repeats"]:
            assert repeat["sizes"]["train"] == 33
            assert sorted([repeat["sizes"]["validation"], repeat["sizes"]["test"]]) == [3, 4]
        if kind == "regression":
            assert {r["objective"] for r in result["repeats"]} == {200}


# -- seeds ------------------------------------------------------------------------------


def test_the_same_seed_gives_the_same_result_and_another_seed_a_different_one():
    meta, rows = scaffold_dataset()
    for strategy in ("random", "scaffold"):
        one = split(meta, rows, strategy=strategy, seed=1)
        again = split(meta, rows, strategy=strategy, seed=1)
        other = split(meta, rows, strategy=strategy, seed=2)
        check_ok(other, meta, rows, strategy)
        assert canonical_json(one) == canonical_json(again)
        assert canonical_json(one) != canonical_json(other)
        assert [r["test"] for r in one["repeats"]] != [r["test"] for r in other["repeats"]]
        assert one["repeat_seeds"] != other["repeat_seeds"]


def test_repeat_seeds_come_from_the_seed_by_a_fixed_scheme():
    assert repeat_seed(0, 0) == 32134078769029
    assert repeat_seed(0, 1) != repeat_seed(0, 0) != repeat_seed(1, 0)
    assert all(0 <= repeat_seed(s, r) < 2**48 for s in (0, 1, 2**32 - 1) for r in range(5))
    meta, rows = scaffold_dataset()
    result = split(meta, rows, strategy="random", seed=9)
    assert result["repeat_seeds"] == [repeat_seed(9, r) for r in range(5)]
    assert [r["seed"] for r in result["repeats"]] == result["repeat_seeds"]


def test_stopping_at_a_candidate_nothing_can_beat_changes_no_answer(monkeypatch):
    meta, rows = scaffold_dataset(kind="regression")
    quick = split(meta, rows, strategy="random", seed=6)
    assert {r["candidates_tried"] for r in quick["repeats"]} == {1}
    monkeypatch.setattr(split_module, "_lower_bound", lambda problem: -1)
    full = split(meta, rows, strategy="random", seed=6)
    assert {r["candidates_tried"] for r in full["repeats"]} == {CANDIDATES}
    for a, b in zip(quick["repeats"], full["repeats"]):
        assert {k: v for k, v in a.items() if k != "candidates_tried"} == {
            k: v for k, v in b.items() if k != "candidates_tried"
        }


def test_input_order_and_extra_row_fields_do_not_change_the_answer():
    meta, rows = scaffold_dataset()
    one = split(meta, rows, strategy="scaffold", seed=3)
    other = split(meta, [dict(r, extra=1) for r in reversed(rows)], strategy="scaffold", seed=3)
    assert canonical_json(one) == canonical_json(other)


# -- what is recorded -------------------------------------------------------------------


def test_the_result_records_what_a_rerun_would_need():
    import rdkit

    meta, rows = scaffold_dataset()
    result = split(meta, rows, strategy="scaffold", seed=12)
    assert result["format"] == split_module.FORMAT
    assert result["algorithm"] == {
        "version": ALGORITHM_VERSION,
        "candidates": 256,
        "repeats": 5,
        "target_percent": {"train": 80, "validation": 10, "test": 10},
        "tolerance_points": 5,
        "min_rows": 2,
    }
    assert result["rdkit"] == rdkit.__version__ and result["scaffold_policy"] == SCAFFOLD_POLICY
    assert result["seed"] == 12 and result["strategy"] == "scaffold"
    assert result["dataset"] == {
        "content_hash": meta["content_hash"],
        "identity": meta["identity"],
        "task": "classification",
        "classes": ["1", "0"] if rows[0]["target"] == "1" else ["0", "1"],
        "n_rows": 100,
    }
    assert result["row_ids"] == list(range(100)) and result["excluded"] == []
    first = result["repeats"][0]
    assert set(first) == {
        "repeat",
        "seed",
        "candidate",
        "candidates_tried",
        "objective",
        "sizes",
        "class_counts",
        "train",
        "validation",
        "test",
    }
    assert sum(sum(c.values()) for c in first["class_counts"].values()) == 100


def test_a_regression_result_has_no_class_counts():
    meta, rows = scaffold_dataset(kind="regression")
    result = split(meta, rows, strategy="random", seed=1)
    assert result["dataset"]["classes"] == [] and result["repeats"][0]["class_counts"] == {}


def test_canonical_json_is_sorted_compact_ascii_and_the_same_for_equal_results():
    a = {"b": [2, 1], "a": {"y": 1, "x": "é"}}
    b = {"a": {"x": "é", "y": 1}, "b": [2, 1]}
    assert canonical_json(a) == canonical_json(b) == '{"a":{"x":"\\u00e9","y":1},"b":[2,1]}'
    assert canonical_json({"a": 1}).isascii()
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


@pytest.mark.parametrize("seed", [-1, 2**32, 1.5, "1", True, None])
def test_a_seed_that_is_not_a_whole_number_in_range_is_refused(seed):
    meta, rows = scaffold_dataset()
    with pytest.raises(BadInput, match="seed"):
        split(meta, rows, strategy="random", seed=seed)


def test_an_unknown_strategy_and_a_target_free_dataset_are_refused():
    meta, rows = scaffold_dataset()
    with pytest.raises(BadInput, match="unknown split strategy 'cluster'"):
        split(meta, rows, strategy="cluster", seed=1)
    bare, bare_rows = resolve(
        csv_of([("CCO", 1), ("CCN", 0)]), {"structure_column": "smiles", "target": None}
    )
    with pytest.raises(BadInput, match="no target"):
        split(bare, bare_rows, strategy="random", seed=1)


# -- the CDD export -----------------------------------------------------------------------


def cdd():
    return resolve(
        CDD,
        {
            "structure_column": "SMILES",
            "target": {
                "column": "HIV-1 PR FRET (Q7K): Ki (nM)",
                "kind": "regression",
                "qualifier_column": "HIV-1 PR FRET (Q7K): Ki Qualifier",
                "transform": "pki",
                "source_units": "nM",
            },
        },
    )


@pytest.mark.parametrize("strategy", ["random", "scaffold"])
def test_the_cdd_export_splits_into_valid_repeats_and_its_censored_rows_stay_out(strategy):
    # This fixture yields valid assignments for both strategies (its largest scaffold
    # group is far below the 15% a validation or test subset may hold).
    meta, rows = cdd()
    result = split(meta, rows, strategy=strategy, seed=1)
    check_ok(result, meta, rows, strategy)
    assert result["n_eligible"] == 102 and result["dataset"]["n_rows"] == 106
    assert [e["row_id"] for e in result["excluded"]] == [102, 103, 104, 105]
    assert all(e["reasons"] == ["censored"] for e in result["excluded"])
    assert result["n_groups"] == (102 if strategy == "random" else 28)


# -- the command, as a separate process ---------------------------------------------------------


def write_dataset(tmp_path, meta, rows):
    out = tmp_path / "ds"
    out.mkdir()
    (out / "dataset.json").write_text(json.dumps(meta))
    (out / "rows.json").write_text(json.dumps({"rows": rows}))
    return out


def run_split(directory, *args, hashseed="0"):
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    proc = subprocess.run(
        [sys.executable, "-m", "ligandbench", "split", str(directory), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.mark.parametrize("strategy", ["random", "scaffold"])
def test_two_processes_write_identical_bytes_whatever_their_hash_seed(tmp_path, strategy):
    meta, rows = scaffold_dataset()
    directory = write_dataset(tmp_path, meta, rows)
    args = ("--strategy", strategy, "--seed", "21")
    code1, out1, err1 = run_split(directory, *args, hashseed="1")
    code2, out2, err2 = run_split(directory, *args, hashseed="12345")
    assert (code1, code2) == (0, 0) and err1 == err2 == ""
    assert out1 == out2 and out1.endswith("\n")
    assert out1.strip() == canonical_json(split(meta, rows, strategy=strategy, seed=21))
    result = json.loads(out1)
    check_ok(result, meta, rows, strategy)
    code3, out3, _ = run_split(directory, "--strategy", strategy, "--seed", "22")
    assert code3 == 0 and out3 != out1


def test_the_command_reports_an_unavailable_split_as_an_answer_not_an_error(tmp_path):
    meta, rows = dataset([(mol(None, k), k % 2) for k in range(2, 102)])
    directory = write_dataset(tmp_path, meta, rows)
    code, out, _ = run_split(directory, "--strategy", "scaffold", "--seed", "0")
    assert code == 0 and json.loads(out)["status"] == "split_unavailable"


def test_the_command_exits_two_with_the_reason_for_what_cannot_be_used(tmp_path):
    meta, rows = scaffold_dataset()
    directory = write_dataset(tmp_path, meta, rows)
    bare, bare_rows = resolve(
        csv_of([("CCO", 1), ("CCN", 0)]), {"structure_column": "smiles", "target": None}
    )
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    (bare_dir / "dataset.json").write_text(json.dumps(bare))
    (bare_dir / "rows.json").write_text(json.dumps({"rows": bare_rows}))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "dataset.json").write_text("{not json")
    (broken / "rows.json").write_text("{}")
    cases = [
        ((directory, "--strategy", "cluster", "--seed", "1"), "unknown split strategy"),
        ((directory, "--strategy", "random", "--seed", "-3"), "seed"),
        ((directory, "--strategy", "random", "--seed", "x"), "seed"),
        ((directory, "--seed", "1"), "strategy"),
        ((bare_dir, "--strategy", "random", "--seed", "1"), "no target"),
        ((tmp_path / "missing", "--strategy", "random", "--seed", "1"), "cannot read"),
        ((broken, "--strategy", "random", "--seed", "1"), "not JSON"),
    ]
    for args, message in cases:
        code, out, _ = run_split(*args)
        assert code == 2, args
        answer = json.loads(out)
        assert answer["kind"] == "bad-input" and message in answer["error"], (args, answer)
