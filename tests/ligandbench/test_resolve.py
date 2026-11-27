"""Resolution: what each row is, and what it may be used for."""

import math
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.resolve import resolve  # noqa: E402

DATA = Path(__file__).parent / "data"
CDD = (DATA / "cdd_export_hiv1_protease.csv").read_bytes()
KI = "HIV-1 PR FRET (Q7K): Ki (nM)"
QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier"


def regression(column="y", qualifier=None, transform="identity", units="", **extra):
    return {
        "structure_column": "smiles",
        "target": {
            "column": column,
            "kind": "regression",
            "qualifier_column": qualifier,
            "transform": transform,
            "source_units": units,
            **extra,
        },
    }


def classification(column="y", positive=None):
    return {
        "structure_column": "smiles",
        "target": {
            "column": column,
            "kind": "classification",
            "qualifier_column": None,
            "positive_class": positive,
        },
    }


def csv(*lines):
    return ("smiles,y,q\n" + "\n".join(lines) + "\n").encode()


# -- the CDD export ------------------------------------------------------------


def cdd_config(transform="pki", units="nM"):
    return {
        "structure_column": "SMILES",
        "target": {
            "column": KI,
            "kind": "regression",
            "qualifier_column": QUAL,
            "transform": transform,
            "source_units": units,
        },
    }


def test_the_cdd_export_resolves_with_its_censored_rows_kept_but_not_trainable():
    meta, rows = resolve(CDD, cdd_config())
    assert meta["n_rows"] == len(rows) == 106
    assert meta["id_column"] == "Molecule Name"
    assert meta["n_parse_failures"] == 0
    assert meta["exclusions"] == {"censored": 4}
    assert meta["n_training_eligible"] == 102 and meta["n_prediction_eligible"] == 106
    censored = [r for r in rows if r["censored"]]
    assert len(censored) == 4
    assert all(r["target"] is None and r["qualifier"] == ">" for r in censored)
    assert all(r["target_source"] for r in censored), "the source text is never lost"
    assert meta["target"]["output_units"] == "pKi"


def test_pki_is_nine_minus_log10_of_nanomolar():
    _, rows = resolve(CDD, cdd_config())
    first = rows[0]  # Ki 0.0008 nM
    assert first["cells"][4] == "0.0008"
    assert first["target"] == pytest.approx(9 - math.log10(0.0008))


def test_row_ids_follow_source_order_and_records_start_at_one():
    _, rows = resolve(CDD, cdd_config())
    assert [r["row_id"] for r in rows] == list(range(106))
    assert [r["record"] for r in rows] == list(range(1, 107))
    assert rows[0]["cells"][0] == "DH-0001"


def test_the_same_choices_hash_the_same_and_other_choices_do_not():
    a = resolve(CDD, cdd_config())[0]["content_hash"]
    assert resolve(CDD, cdd_config())[0]["content_hash"] == a
    assert resolve(CDD, cdd_config("identity", "nM"))[0]["content_hash"] != a
    assert resolve(CDD + b"\n", cdd_config())[0]["content_hash"] != a, "the source bytes count"
    assert resolve(CDD.replace(b"0.0008", b"0.0009"), cdd_config())[0]["content_hash"] != a


# -- the choices must be made ----------------------------------------------------------


def test_a_qualifier_choice_must_be_made_even_if_it_is_none():
    cfg = regression(units="nM")
    del cfg["target"]["qualifier_column"]
    with pytest.raises(BadInput, match="confirm the qualifier column"):
        resolve(csv("CCO,1,"), cfg)


def test_units_must_be_confirmed_for_a_regression_target():
    cfg = regression()
    del cfg["target"]["source_units"]
    with pytest.raises(BadInput, match="confirm the units"):
        resolve(csv("CCO,1,"), cfg)


def test_pki_is_refused_unless_the_units_are_confirmed_as_nanomolar():
    for units in ("", "uM", "M"):
        with pytest.raises(BadInput, match="confirm that 'y' is in nM"):
            resolve(csv("CCO,1,"), regression(transform="pki", units=units))


def test_the_task_must_be_chosen():
    cfg = regression()
    cfg["target"]["kind"] = None
    with pytest.raises(BadInput, match="regression or classification"):
        resolve(csv("CCO,1,"), cfg)


def test_columns_that_are_not_in_the_file_are_named_with_the_ones_that_are():
    with pytest.raises(BadInput, match=r"'nope' is not in the file; its columns are: smiles; y; q"):
        resolve(csv("CCO,1,"), {"structure_column": "nope", "target": None})
    with pytest.raises(BadInput, match="target column 'nope'"):
        resolve(csv("CCO,1,"), regression("nope"))


def test_a_qualifier_does_not_apply_to_class_labels():
    cfg = classification()
    cfg["target"]["qualifier_column"] = "q"
    with pytest.raises(BadInput, match="not to a class label"):
        resolve(csv("CCO,a,", "CC,b,"), cfg)


# -- targets ---------------------------------------------------------------------------


def test_identity_keeps_values_and_records_the_units():
    meta, rows = resolve(csv("CCO,2.5,", "CC,3,"), regression(units="uM"))
    assert [r["target"] for r in rows] == [2.5, 3.0]
    assert meta["target"]["source_units"] == "uM" and meta["target"]["output_units"] == "uM"


def test_blank_targets_keep_their_rows_and_say_why():
    meta, rows = resolve(csv("CCO,1,", "CC,,", "CCC,  ,"), regression())
    assert [r["exclusions"] for r in rows] == [[], ["missing_target"], ["missing_target"]]
    assert [r["training_eligible"] for r in rows] == [True, False, False]
    assert meta["exclusions"] == {"missing_target": 2}
    assert all(r["prediction_eligible"] for r in rows)


@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "1_000", "1,5", "5 nM", "--3"])
def test_a_target_that_is_not_a_number_is_invalid_not_zero(bad):
    _, rows = resolve(csv("CCO,1,", 'CC,"%s",' % bad), regression())
    assert rows[1]["exclusions"] == ["invalid_target"] and rows[1]["target"] is None


def test_an_inline_bound_is_censored_never_exact():
    _, rows = resolve(csv("CCO,1,", "CC,<0.5,", "CCC,>=7,"), regression())
    assert [r["censored"] for r in rows] == [False, True, True]
    assert [r["qualifier"] for r in rows] == ["=", "<", ">="]
    assert [r["target"] for r in rows] == [1.0, None, None]
    assert rows[1]["target_source"] == "<0.5"


def test_a_qualifier_column_makes_its_row_censored_blank_and_equals_are_exact():
    _, rows = resolve(csv("CCO,1,", "CC,2,=", "CCC,3,<", "CCCC,4,>="), regression(qualifier="q"))
    assert [r["censored"] for r in rows] == [False, False, True, True]
    assert [r["target"] for r in rows] == [1.0, 2.0, None, None]


def test_an_unknown_qualifier_or_a_disagreement_is_not_trainable():
    _, rows = resolve(
        csv("CCO,1,", "CC,2,~", "CCC,<3,>", "CCCC,<3,<", "CCCCC,=3,<"), regression(qualifier="q")
    )
    assert [r["exclusions"] for r in rows] == [
        [],
        ["unknown_qualifier"],
        ["conflicting_qualifiers"],
        ["censored"],
        ["conflicting_qualifiers"],
    ]


def test_pki_needs_a_positive_value():
    meta, rows = resolve(
        csv("CCO,10,", "CC,0,", "CCC,-4,"), regression(transform="pki", units="nM")
    )
    assert rows[0]["target"] == pytest.approx(8.0)
    assert [r["exclusions"] for r in rows[1:]] == [["nonpositive_for_pki"]] * 2
    assert meta["exclusions"] == {"nonpositive_for_pki": 2}


def test_zero_and_one_can_be_classes_and_labels_stay_strings():
    meta, rows = resolve(csv("CCO,1,", "CC,0,", "CCC,1,"), classification(positive="1"))
    assert meta["target_kind"] == "classification"
    assert meta["classes"] == ["1", "0"] and meta["target"]["positive_class"] == "1"
    assert [r["target"] for r in rows] == ["1", "0", "1"]
    assert all(r["training_eligible"] for r in rows)


def test_a_binary_target_asks_for_its_positive_class():
    with pytest.raises(BadInput, match="choose the positive class of 'y': '1' or '0'"):
        resolve(csv("CCO,1,", "CC,0,"), classification())
    with pytest.raises(BadInput, match="'9' is not one of the classes"):
        resolve(csv("CCO,1,", "CC,0,"), classification(positive="9"))


def test_more_than_two_classes_have_no_single_positive():
    meta, _ = resolve(csv("CCO,a,", "CC,b,", "CCC,c,"), classification(positive="a"))
    assert meta["classes"] == ["a", "b", "c"] and meta["target"]["positive_class"] is None


def test_one_class_cannot_train():
    with pytest.raises(BadInput, match=r"one class \('1'\).*at least two"):
        resolve(csv("CCO,1,", "CC,1,"), classification(positive="1"))


def test_classes_are_counted_among_rows_that_parse():
    # The only row of class "0" has a bad structure, so one class is left to learn.
    with pytest.raises(BadInput, match="at least two classes"):
        resolve(csv("CCO,1,", "CC,1,", "not-smiles,0,"), classification(positive="1"))


def test_a_dataset_with_no_target_predicts_but_cannot_train():
    meta, rows = resolve(csv("CCO,1,", "CC,2,"), {"structure_column": "smiles", "target": None})
    assert meta["trainable"] is False and meta["target_column"] is None
    assert meta["n_prediction_eligible"] == 2 and meta["n_training_eligible"] == 0
    assert all(r["prediction_eligible"] and not r["training_eligible"] for r in rows)
    assert rows[0]["exclusions"] == ["no_target"]


def test_nothing_trainable_is_an_error_naming_the_reasons():
    with pytest.raises(BadInput, match=r"no row of 2 can be trained on.*2 the target is a bound"):
        resolve(csv("CCO,<1,", "CC,>2,"), regression())


# -- structures -------------------------------------------------------------------------


def test_failed_structures_stay_as_rows_with_their_record_numbers():
    text = csv("CCO,1,", "bogus,2,", ",3,", "CC,4,")
    meta, rows = resolve(text, regression())
    assert meta["n_rows"] == 4 and meta["n_parse_failures"] == 2
    assert meta["parse_failures"] == [
        {"record": 2, "value": "bogus", "error": "not a valid SMILES"},
        {"record": 3, "value": "", "error": "no structure"},
    ]
    assert [r["prediction_eligible"] for r in rows] == [True, False, False, True]
    assert rows[1]["exclusions"] == ["invalid_structure"]
    assert rows[1]["smiles"] is None and rows[1]["cells"] == ["bogus", "2", ""]
    assert meta["exclusions"] == {"invalid_structure": 2}


def test_a_column_where_nothing_parses_is_refused_with_examples():
    with pytest.raises(BadInput, match=r"none of the 2 values.*record 1: 'hello'.*columns are"):
        resolve(csv("hello,1,", "world,2,"), regression())


def test_an_empty_structure_column_is_refused():
    with pytest.raises(BadInput, match="is empty"):
        resolve(csv(",1,", ",2,"), regression())


def test_a_structure_column_is_parsed_as_smiles_whatever_it_is_called():
    meta, rows = resolve(
        b"Molecule Name,y\nCCO,1\nCC,2\n", {"structure_column": "Molecule Name", "target": None}
    )
    assert meta["n_parse_failures"] == 0 and rows[0]["smiles"] == "CCO"


def test_identity_is_canonical_isomeric_smiles_with_nothing_normalised_away():
    meta, rows = resolve(
        csv(
            "OCC,1,",
            "C[C@H](N)C(=O)O,1,",
            "C[C@@H](N)C(=O)O,0,",
            "[Na+].[Cl-],0,",
            "C[N+](C)(C)C,0,",
        ),
        regression(),
    )
    ethanol, l_ala, d_ala, salt, quat = (r["smiles"] for r in rows)
    assert ethanol == "CCO"
    assert l_ala != d_ala and "@" in l_ala and "@" in d_ala
    assert salt == "[Cl-].[Na+]", "components are not stripped"
    assert "+" in quat
    assert meta["identity"]["standardization"] == "none" and meta["identity"]["rdkit"]


def test_the_same_molecule_written_two_ways_is_one_identity_and_two_rows():
    meta, rows = resolve(csv("OCC,a,", "C(O)C,a,", "c1ccccc1,b,"), classification(positive="a"))
    assert rows[0]["smiles"] == rows[1]["smiles"] != rows[2]["smiles"]
    assert len(rows) == 3, "duplicates remain separate observations"
    assert meta["duplicates"]["n_structures"] == 1 and meta["duplicates"]["n_rows"] == 2
    assert meta["duplicates"]["n_conflicting"] == 0


def test_conflicting_duplicate_labels_are_reported():
    meta, _ = resolve(csv("OCC,a,", "C(O)C,b,", "c1ccccc1,b,"), classification(positive="a"))
    dup = meta["duplicates"]
    assert dup["n_conflicting"] == 1
    assert dup["examples"] == [{"smiles": "CCO", "records": [1, 2], "labels": ["a", "b"]}]


def test_synthetic_classification_fixture():
    data = (DATA / "synthetic_classification.csv").read_bytes()
    cfg = {
        "structure_column": "SMILES",
        "target": {
            "column": "Active",
            "kind": "classification",
            "qualifier_column": None,
            "positive_class": "1",
        },
    }
    meta, rows = resolve(data, cfg)
    assert meta["n_training_eligible"] == 9 and meta["id_column"] == "Name"
    assert rows[4]["smiles"] != rows[5]["smiles"], "enantiomers stay distinct"
