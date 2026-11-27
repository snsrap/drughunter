"""Sorting, filtering and paging: deterministic, and decided before the page is cut."""

import pytest

from proteinviewer import rowquery
from proteinviewer.errors import BadRequest

META = {
    "target_column": "Ki",
    "target_kind": "regression",
    "columns": [
        {"index": 0, "key": "c0", "name": "Name", "type": "text"},
        {"index": 1, "key": "c1", "name": "Ki", "type": "number"},
        {"index": 2, "key": "c2", "name": "Note", "type": "text"},
    ],
}


def row(i, name, ki, note="", smiles="C", target=None, eligible=True):
    return {
        "row_id": i,
        "record": i + 1,
        "cells": [name, ki, note],
        "smiles": smiles,
        "target": target,
        "training_eligible": eligible,
    }


ROWS = [
    row(0, "beta", "5", "x"),
    row(1, "Alpha", "<0.5", ""),
    row(2, "alpha", "5", "has x"),
    row(3, "", "", "y", smiles=None, eligible=False),
    row(4, "gamma", "10", "X"),
]
COLUMNS = rowquery.schema(META)


def ids(page):
    return [r["row_id"] for r in page]


def q(**kw):
    kw.setdefault("limit", 100)
    filters = rowquery.parse_filters(COLUMNS, kw.pop("filters", []))
    sort = rowquery.parse_sort(COLUMNS, kw.pop("sort", None))
    return rowquery.run(COLUMNS, ROWS, filters=filters, sort=sort, **kw)


def test_the_schema_gives_every_column_a_stable_key_and_a_type():
    by = {c.key: c for c in COLUMNS}
    assert by["c1"].type == "number" and by["c0"].type == "text"
    assert by["target"].type == "number", "a regression target is a number"
    assert [c.key for c in COLUMNS][:3] == ["record", "row_id", "smiles"]
    cls = rowquery.schema({**META, "target_kind": "classification"})
    assert {c.key: c.type for c in cls}["target"] == "text"
    assert "target" not in {c.key for c in rowquery.schema({"columns": []})}


def test_the_default_order_is_source_order():
    page, total = q()
    assert ids(page) == [0, 1, 2, 3, 4] and total == 5


def test_numbers_sort_as_numbers_and_a_bound_sorts_by_its_number():
    page, _ = q(sort={"key": "c1", "dir": "asc"})
    assert ids(page) == [1, 0, 2, 4, 3]  # 0.5, 5, 5, 10, then the blank
    page, _ = q(sort={"key": "c1", "dir": "desc"})
    assert ids(page) == [4, 0, 2, 1, 3]


def test_missing_values_sort_last_in_either_direction_and_ties_go_to_the_lower_row_id():
    asc, _ = q(sort={"key": "c1", "dir": "asc"})
    desc, _ = q(sort={"key": "c1", "dir": "desc"})
    assert ids(asc)[-1] == ids(desc)[-1] == 3
    # rows 0 and 2 both hold 5: 0 comes first whichever way it is sorted
    assert ids(asc).index(0) < ids(asc).index(2)
    assert ids(desc).index(0) < ids(desc).index(2)


def test_text_sorts_without_regard_to_case_with_ties_by_row_id():
    page, _ = q(sort={"key": "c0", "dir": "asc"})
    assert ids(page) == [1, 2, 0, 4, 3]  # Alpha, alpha (row order), beta, gamma, blank
    page, _ = q(sort={"key": "c0", "dir": "desc"})
    assert ids(page) == [4, 0, 1, 2, 3]


@pytest.mark.parametrize(
    "op, value, want",
    [
        ("eq", 5, [0, 2]),
        ("lt", 5, [1]),
        ("lte", 5, [0, 1, 2]),
        ("gt", 5, [4]),
        ("gte", 5, [0, 2, 4]),
        ("eq", "5", [0, 2]),
    ],
)
def test_number_filters_compare_the_number_in_the_cell(op, value, want):
    page, total = q(filters=[{"key": "c1", "op": op, "value": value}])
    assert ids(page) == want and total == len(want)


def test_text_contains_ignores_case_and_eq_is_exact():
    page, _ = q(filters=[{"key": "c2", "op": "contains", "value": "X"}])
    assert ids(page) == [0, 2, 4]
    page, _ = q(filters=[{"key": "c2", "op": "eq", "value": "x"}])
    assert ids(page) == [0]
    page, _ = q(filters=[{"key": "c0", "op": "eq", "value": "alpha"}])
    assert ids(page) == [2]


def test_every_column_answers_missing_and_a_blank_cell_is_missing():
    for key, want in (
        ("c0", [3]),
        ("c1", [3]),
        ("c2", [1]),
        ("smiles", [3]),
        ("target", [0, 1, 2, 3, 4]),
    ):
        page, _ = q(filters=[{"key": key, "op": "missing"}])
        assert ids(page) == want, key
    page, _ = q(filters=[{"key": "c1", "op": "missing", "value": False}])
    assert ids(page) == [0, 1, 2, 4]


def test_a_missing_value_matches_no_comparison():
    page, _ = q(filters=[{"key": "c1", "op": "lt", "value": 100}])
    assert 3 not in ids(page)


def test_filters_combine_with_and():
    page, total = q(
        filters=[
            {"key": "c2", "op": "contains", "value": "x"},
            {"key": "c1", "op": "gte", "value": 5},
            {"key": "c0", "op": "contains", "value": "a"},
        ]
    )
    assert ids(page) == [0, 2, 4] and total == 3


def test_derived_columns_filter_too():
    page, _ = q(filters=[{"key": "eligible", "op": "eq", "value": "no"}])
    assert ids(page) == [3]
    page, _ = q(filters=[{"key": "structure", "op": "eq", "value": "failed"}])
    assert ids(page) == [3]


def test_paging_is_cut_after_sorting_and_filtering_and_reports_the_total():
    page, total = q(sort={"key": "c1", "dir": "asc"}, offset=1, limit=2)
    assert ids(page) == [0, 2] and total == 5
    page, total = q(filters=[{"key": "c1", "op": "gte", "value": 5}], offset=2, limit=2)
    assert ids(page) == [4] and total == 3
    page, total = q(offset=50)
    assert page == [] and total == 5


def test_source_cell_text_survives_a_numeric_sort():
    page, _ = q(sort={"key": "c1", "dir": "asc"})
    assert page[0]["cells"][1] == "<0.5"


@pytest.mark.parametrize(
    "bad, match",
    [
        ({"key": "nope", "op": "eq", "value": 1}, "unknown column 'nope'"),
        ({"key": "c1", "op": "contains", "value": "5"}, "cannot be filtered with 'contains'"),
        ({"key": "c0", "op": "gt", "value": "a"}, "cannot be filtered with 'gt'"),
        ({"key": "c1", "op": "like", "value": 1}, "cannot be filtered with 'like'"),
        ({"key": "c1", "op": "eq", "value": "abc"}, "needs a number"),
        ({"key": "c1", "op": "eq", "value": ""}, "needs a number"),
        ({"key": "c1", "op": "eq", "value": True}, "needs a number"),
        ({"key": "c1", "op": "eq", "value": float("inf")}, "finite"),
        ({"key": "c0", "op": "eq", "value": 5}, "needs text"),
        ({"key": "c0", "op": "missing", "value": "yes"}, "true or false"),
        ({"op": "eq", "value": 1}, "unknown column"),
    ],
)
def test_an_unknown_key_or_operator_is_refused_not_ignored(bad, match):
    with pytest.raises(BadRequest, match=match):
        rowquery.parse_filters(COLUMNS, [bad])


def test_a_sort_is_checked_too():
    with pytest.raises(BadRequest, match="unknown column"):
        rowquery.parse_sort(COLUMNS, {"key": "nope"})
    with pytest.raises(BadRequest, match="asc or desc"):
        rowquery.parse_sort(COLUMNS, {"key": "c0", "dir": "up"})
    col, desc = rowquery.parse_sort(COLUMNS, None)
    assert (col.key, desc) == ("row_id", False)


def test_there_is_a_limit_on_filters():
    with pytest.raises(BadRequest, match="at most"):
        rowquery.parse_filters(COLUMNS, [{"key": "c0", "op": "missing"}] * 21)
