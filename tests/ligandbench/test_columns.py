"""What the dialog is told about each column."""

from ligandbench.columns import (
    describe_columns,
    parse_number,
    split_qualified,
    structure_candidates,
)
from ligandbench.table import parse_csv


def test_structure_candidates_are_exact_aliases_or_a_smiles_suffix():
    headers = ["Molecule Name", "SMILES", "Canonical SMILES", "mol", "parent_smiles", "Smiles Note"]
    assert structure_candidates(headers) == ["SMILES", "Canonical SMILES", "mol", "parent_smiles"]


def test_molecule_name_is_a_name_not_a_structure():
    assert structure_candidates(["Molecule Name", "Molecule ID", "Batch"]) == []


def test_parse_number_is_not_pythons_float():
    assert parse_number(" 1.5e-3 ") == 0.0015
    for bad in ("nan", "inf", "1_000", "1,5", "", "1e999", "0x10", "<5"):
        assert parse_number(bad) is None, bad


def test_split_qualified():
    assert split_qualified(">10000") == (">", 10000.0)
    assert split_qualified("<= 0.5") == ("<=", 0.5)
    assert split_qualified("12") == (None, 12.0)
    assert split_qualified("=3") == ("=", 3.0)
    assert split_qualified("abc") == (None, None)


def test_a_column_of_bounds_and_numbers_is_a_number_column():
    t = parse_csv("a,b,c,d\n1,<5,x,\n2.5,>7,y,=\n,3,,>\n")
    cols = {c["name"]: c for c in describe_columns(t)}
    assert cols["a"]["type"] == "number" and cols["a"]["n_filled"] == 2
    assert cols["b"]["type"] == "number"
    assert cols["c"]["type"] == "text"
    assert cols["d"]["qualifier_like"] and not cols["a"]["qualifier_like"]
    assert [c["key"] for c in describe_columns(t)] == ["c0", "c1", "c2", "c3"]


def test_zero_and_one_are_numbers_that_may_be_classes_and_list_their_values():
    t = parse_csv("y\n0\n1\n1\n")
    (col,) = describe_columns(t)
    assert col["type"] == "number"
    assert col["values"] == [["0", 1], ["1", 2]]


def test_a_column_with_many_values_does_not_list_them_all():
    t = parse_csv("y\n" + "\n".join(str(i) for i in range(50)) + "\n")
    (col,) = describe_columns(t)
    assert col["values"] is None
