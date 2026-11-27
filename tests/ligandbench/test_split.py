"""Split generation: which rows may be split, and the groups that move whole.

The allocator and the result's shape are tested further down, from the same
synthetic datasets.
"""

from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.resolve import resolve  # noqa: E402
from ligandbench.scaffold import murcko_scaffold  # noqa: E402
from ligandbench.split import (  # noqa: E402
    eligible_rows,
    make_groups,
    scaffold_groups,
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
