"""Reading the CSV: every cell kept, every problem located by data-record number."""

import pytest

from ligandbench.errors import BadInput
from ligandbench.table import parse_csv


def test_quoted_commas_and_newlines_are_cell_text():
    t = parse_csv('name,smiles,note\n"a, b",CCO,"line one\nline two"\nc,CC,plain\n')
    assert t.headers == ["name", "smiles", "note"]
    assert t.records == [["a, b", "CCO", "line one\nline two"], ["c", "CC", "plain"]]


def test_a_utf8_bom_is_not_part_of_the_first_header():
    t = parse_csv("﻿smiles,y\nCCO,1\n".encode())
    assert t.headers == ["smiles", "y"]
    t = parse_csv(b"\xef\xbb\xbfsmiles,y\nCCO,1\n")
    assert t.headers == ["smiles", "y"]


def test_cells_are_kept_exactly_and_headers_are_trimmed():
    t = parse_csv(" smiles ,y\n CCO , 1.50 \n")
    assert t.headers == ["smiles", "y"]
    assert t.records == [[" CCO ", " 1.50 "]]


def test_blank_lines_are_not_records_but_comma_only_lines_are():
    t = parse_csv("a,b\n\nx,y\n\n,\n")
    assert t.records == [["x", "y"], ["", ""]]


def test_duplicate_headers_name_both_columns():
    with pytest.raises(BadInput, match=r"columns 1 and 3 are both headed 'a'"):
        parse_csv("a,b,a\n1,2,3\n")


def test_an_empty_header_is_refused():
    with pytest.raises(BadInput, match="column 2 has an empty header"):
        parse_csv("a,,c\n1,2,3\n")


def test_a_ragged_record_is_located_by_data_record_not_by_line():
    # Record 2 starts on physical line 5, because record 1 has a newline inside a cell.
    text = 'a,b\n"x\ny\nz",1\nshort\n'
    with pytest.raises(BadInput, match=r"record 2 has 1 fields but the header has 2"):
        parse_csv(text)


def test_an_unterminated_quote_is_reported_not_swallowed():
    with pytest.raises(BadInput, match="not valid CSV"):
        parse_csv('a,b\n1,"never closed\n')


def test_input_that_is_not_utf8_says_so():
    with pytest.raises(BadInput, match="not UTF-8"):
        parse_csv(b"a,b\n\xff\xfe,1\n")


@pytest.mark.parametrize("text, match", [("", "empty"), ("\n\n", "empty"), ("a,b\n", "no records")])
def test_nothing_to_read(text, match):
    with pytest.raises(BadInput, match=match):
        parse_csv(text)
