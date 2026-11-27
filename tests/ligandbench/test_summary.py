"""The dataset summary: the numbers behind the dashboard, from the rows 01 wrote.

Every count a person would compare with the dataset's own metadata is asserted
equal to it; the rest is checked against a fixture small enough to count by hand.
"""

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from ligandbench import summary as summary_module  # noqa: E402
from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.resolve import REASONS, resolve  # noqa: E402
from ligandbench.scaffold import SCAFFOLD_POLICY  # noqa: E402
from ligandbench.summary import (  # noqa: E402
    MAX_BINS,
    SUMMARY_VERSION,
    bin_count,
    histogram,
    summarize,
)

DATA = Path(__file__).parent / "data"
CDD = (DATA / "cdd_export_hiv1_protease.csv").read_bytes()
SYNTHETIC = (DATA / "synthetic_classification.csv").read_bytes()
KI = "HIV-1 PR FRET (Q7K): Ki (nM)"
QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier"


def regression(column="y", qualifier=None, transform="identity", units=""):
    return {
        "structure_column": "smiles",
        "target": {
            "column": column,
            "kind": "regression",
            "qualifier_column": qualifier,
            "transform": transform,
            "source_units": units,
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


NO_TARGET = {"structure_column": "smiles", "target": None}


def read(csv_text, config):
    """``(meta, rows, summary)`` of a CSV read the way the server reads it."""
    meta, rows = resolve(csv_text.encode() if isinstance(csv_text, str) else csv_text, config)
    return meta, rows, summarize(meta, rows)


def cdd(transform="pki", units="nM"):
    config = {
        "structure_column": "SMILES",
        "target": {
            "column": KI,
            "kind": "regression",
            "qualifier_column": QUAL,
            "transform": transform,
            "source_units": units,
        },
    }
    return read(CDD, config)


# A classification table with every difficulty at once: a duplicate whose labels
# agree (CCO twice, one of them written backwards), one whose labels conflict
# (benzene twice), a molecule with no ring that is not a duplicate, a row that is
# not a molecule, a missing label, and a blank, a whitespace-only and a
# non-empty cell in the one column that holds them.
DUPLICATES = "\n".join(
    [
        "name,smiles,y,note",
        "a,CCO,1,first",
        "b,OCC,1,",
        "c,c1ccccc1,1,   ",
        "d,C1=CC=CC=C1,0,second",
        "e,CC(=O)O,0,third",
        "f,not a molecule,1,fourth",
        "g,CCN,,fifth",
        "h,c1ccncc1,0,sixth",
    ]
)


# -- populations and the counts 01 already keeps ----------------------------------


def test_the_populations_are_named_with_their_denominators():
    meta, rows, s = read(DUPLICATES, classification(positive="1"))
    pops = s["populations"]
    assert set(pops) == {"source_rows", "valid_structures", "training_eligible"}
    assert pops["source_rows"] == {"label": "source rows", "n": 8, "of": None}
    assert pops["valid_structures"] == {"label": "valid structures", "n": 7, "of": "source_rows"}
    assert pops["training_eligible"] == {
        "label": "training-eligible rows",
        "n": 6,
        "of": "source_rows",
    }
    assert s["version"] == SUMMARY_VERSION and s["scaffolds"]["policy"] == SCAFFOLD_POLICY
    assert s["identity"] == meta["identity"]


def synthetic():
    config = classification("Active", "1")
    config["structure_column"] = "SMILES"
    return read(SYNTHETIC, config)


@pytest.mark.parametrize(
    "make",
    [
        lambda: read(DUPLICATES, classification(positive="1")),
        lambda: read(DUPLICATES, NO_TARGET),
        lambda: read(DUPLICATES + "\ni,CCN,0,\nj,c1ccncc1,,\n", classification(positive="1")),
        cdd,
        synthetic,
    ],
    ids=["duplicates", "no-target", "more-rows", "cdd", "synthetic"],
)
def test_the_counts_equal_the_datasets_own_metadata(make):
    meta, rows, s = make()
    pops = s["populations"]
    assert pops["source_rows"]["n"] == meta["n_rows"] == len(rows)
    assert pops["valid_structures"]["n"] == meta["n_prediction_eligible"]
    assert pops["valid_structures"]["n"] + s["parse_failures"]["n"] == meta["n_rows"]
    assert s["parse_failures"]["n"] == meta["n_parse_failures"]
    assert pops["training_eligible"]["n"] == meta["n_training_eligible"]
    assert {e["code"]: e["n"] for e in s["exclusions"]["by_reason"]} == meta["exclusions"]
    # A row that cannot train has at least one reason, and only those do.
    assert s["exclusions"]["n_rows"] == meta["n_rows"] - meta["n_training_eligible"]
    d, md = s["duplicates"], meta["duplicates"]
    assert d["n_groups"] == md["n_structures"] and d["n_rows_in_groups"] == md["n_rows"]
    assert d["n_conflicting"] == md["n_conflicting"]
    # Every column's missing count is 01's own: rows minus the filled cells.
    assert [(c["index"], meta["n_rows"] - c["n_missing"]) for c in s["missing"]["columns"]] == [
        (c["index"], c["n_filled"]) for c in meta["columns"]
    ]


def test_exclusion_reasons_overlap_and_say_so():
    # Row f has no structure and a label; row g has a structure and no label; this
    # one has neither.
    meta, rows, s = read(DUPLICATES + "\ni,also not a molecule,,\n", classification(positive="1"))
    ex = s["exclusions"]
    assert ex["overlap"] is True and ex["population"] == "source_rows"
    by = {e["code"]: e for e in ex["by_reason"]}
    assert by["invalid_structure"]["n"] == 2 and by["missing_target"]["n"] == 2
    assert by["missing_target"]["label"] == REASONS["missing_target"]
    # Three rows are out; the two reasons add up to four because one row has both.
    assert ex["n_rows"] == 3 and sum(e["n"] for e in ex["by_reason"]) == 4
    # Most common first, so the first thing shown is the biggest loss.
    ns = [e["n"] for e in ex["by_reason"]]
    assert ns == sorted(ns, reverse=True)


# -- duplicates ----------------------------------------------------------------


def test_duplicates_are_valid_rows_minus_distinct_identities():
    meta, rows, s = read(DUPLICATES, classification(positive="1"))
    d = s["duplicates"]
    # 7 valid rows; CCO (twice), benzene (twice), acetic acid, CCN, pyridine -> 5 identities.
    assert d["population"] == "valid_structures"
    assert d["n_distinct"] == 5
    assert d["n_duplicate_rows"] == 7 - 5
    assert d["n_groups"] == 2 and d["n_rows_in_groups"] == 4


def test_conflicting_groups_use_01s_definition_for_classification():
    meta, rows, s = read(DUPLICATES, classification(positive="1"))
    # CCO is 1 and 1; benzene is 1 and 0.
    assert s["duplicates"]["n_conflicting"] == 1 == meta["duplicates"]["n_conflicting"]
    assert s["duplicates"]["conflicting_reason"] is None
    # A second duplicate that agrees does not change it; one that disagrees does.
    again = read(DUPLICATES + "\ni,CC(O)=O,1,\n", classification(positive="1"))[2]
    assert again["duplicates"]["n_conflicting"] == 2


def test_a_missing_label_does_not_conflict_with_a_label():
    # The same rule as 01: only labels that are present are compared.
    text = "name,smiles,y\na,CCO,1\nb,OCC,\nc,CCN,0\nd,NCC,0\n"
    meta, rows, s = read(text, classification(positive="1"))
    assert s["duplicates"]["n_groups"] == 2
    assert s["duplicates"]["n_conflicting"] == 0 == meta["duplicates"]["n_conflicting"]


def test_conflicting_targets_are_not_defined_for_regression_and_say_why():
    meta, rows, s = read("name,smiles,y\na,CCO,1.5\nb,OCC,2.5\nc,CCN,3\n", regression())
    d = s["duplicates"]
    assert d["n_groups"] == 1 and d["n_duplicate_rows"] == 1
    assert d["n_conflicting"] is None and meta["duplicates"]["n_conflicting"] is None
    assert d["conflicting_reason"] == "not defined for regression"


def test_conflicting_targets_without_a_target_say_so():
    s = read(DUPLICATES, NO_TARGET)[2]
    assert s["duplicates"]["n_conflicting"] is None
    assert s["duplicates"]["conflicting_reason"] == "the dataset has no target"


def test_a_dataset_without_duplicates_has_none():
    s = read(SYNTHETIC, {**classification("Active", "1"), "structure_column": "SMILES"})[2]
    assert s["duplicates"]["n_duplicate_rows"] == 0 and s["duplicates"]["n_groups"] == 0
    assert s["duplicates"]["n_conflicting"] == 0


# -- scaffolds ------------------------------------------------------------------


def test_scaffold_groups_count_the_empty_scaffold_once():
    meta, rows, s = read(DUPLICATES, classification(positive="1"))
    sc = s["scaffolds"]
    # Valid rows: CCO x2, benzene x2, acetic acid, CCN (all but benzene have no ring),
    # and pyridine. Acyclic rows: 2 + 1 + 1 = 4. Groups: acyclic 4, benzene 2, pyridine 1.
    assert sc["population"] == "valid_structures" and sc["n_structures"] == 7
    assert sc["n_distinct"] == 3
    assert sc["acyclic_rows"] == 4
    assert sc["largest_group_size"] == 4, "the acyclic group is a group like another"
    assert sc["top10_groups"] == 3 and sc["top10_rows"] == 7 and sc["top10_share"] == 1.0
    assert sc["n_unscaffolded"] == 0


def test_a_dataset_with_no_ring_at_all_is_one_group():
    s = read("name,smiles,y\na,CCO,1\nb,CCN,2\nc,CCCC,3\n", regression())[2]
    sc = s["scaffolds"]
    assert (sc["n_distinct"], sc["largest_group_size"], sc["acyclic_rows"]) == (1, 3, 3)
    assert sc["top10_share"] == 1.0


def test_top_ten_share_is_the_ten_largest_groups_over_valid_structures():
    # Twelve scaffolds with 12, 11, ... 1 rows each: alkyl cycloalkanes, rings of 3 to 14.
    lines = ["name,smiles,y"]
    n = 0
    sizes = {}
    for ring, size in zip(range(3, 15), range(12, 0, -1)):
        sizes[ring] = size
        for k in range(size):
            lines.append("m%d,%sC1%sC1,%d" % (n, "C" * (k + 1), "C" * (ring - 2), n))
            n += 1
    meta, rows, s = read("\n".join(lines), regression())
    sc = s["scaffolds"]
    assert sc["n_distinct"] == 12 and sc["largest_group_size"] == 12
    assert sc["n_structures"] == sum(range(1, 13)) == 78
    assert sc["top10_groups"] == 10 and sc["top10_rows"] == sum(range(3, 13)) == 75
    assert sc["top10_share"] == pytest.approx(75 / 78)


def test_stereoisomers_and_duplicates_share_a_scaffold():
    text = "name,smiles,y\na,C[C@H](F)c1ccccc1,1\nb,C[C@@H](F)c1ccccc1,2\nc,CCc1ccccc1,3\n"
    sc = read(text, regression())[2]["scaffolds"]
    assert sc["n_distinct"] == 1 and sc["largest_group_size"] == 3 and sc["acyclic_rows"] == 0


def test_a_row_whose_scaffold_cannot_be_made_is_counted_and_not_hidden(monkeypatch):
    real = summary_module.murcko_scaffold

    def flaky(smiles):
        if smiles == "CCN":
            raise BadInput("RDKit has no scaffold")
        return real(smiles)

    monkeypatch.setattr(summary_module, "murcko_scaffold", flaky)
    sc = read(DUPLICATES, classification(positive="1"))[2]["scaffolds"]
    # CCN is in no group; the denominator is still every valid structure.
    assert sc["n_unscaffolded"] == 1 and sc["n_structures"] == 7
    assert sc["acyclic_rows"] == 3 and sc["top10_rows"] == 6
    assert sc["top10_share"] == pytest.approx(6 / 7)


# -- the target: the four displays ---------------------------------------------


def test_a_regression_target_is_a_histogram_over_training_rows_in_model_units():
    meta, rows, s = cdd()
    t = s["target"]
    assert t["state"] == "regression" and t["population"] == "training_eligible"
    assert t["n"] == meta["n_training_eligible"] == 102
    # The model sees pKi, not nM: the numbers are the transformed ones.
    assert t["transform"] == "pki" and t["units"] == "pKi" and t["source_units"] == "nM"
    assert t["column"] == KI
    values = [r["target"] for r in rows if r["training_eligible"]]
    assert t["min"] == min(values) and t["max"] == max(values)
    assert t["mean"] == pytest.approx(sum(values) / len(values))
    assert t["min"] > 0, "a pKi from nanomolar Ki of ~1e-3 to 1e1 is a positive number"
    # Censored rows (4) are not in it.
    bins = t["bins"]
    assert sum(bins["counts"]) == 102 == t["n_binned"]
    assert len(bins["edges"]) == len(bins["counts"]) + 1 == bin_count(102) + 1
    assert bins["edges"][0] == t["min"] and bins["edges"][-1] == t["max"]
    assert bins["edges"] == sorted(bins["edges"])
    assert "Rice" in bins["rule"]


def test_untransformed_values_keep_their_own_units():
    meta, rows, s = cdd(transform="identity", units="nM")
    t = s["target"]
    assert t["units"] == "nM" and t["transform"] == "identity"
    values = [r["target"] for r in rows if r["training_eligible"]]
    assert t["min"] == min(values) == 0.0008


def test_a_classification_target_is_label_counts_in_class_order():
    meta, rows, s = read(DUPLICATES, classification(positive="1"))
    t = s["target"]
    assert t["state"] == "classification" and t["n"] == 6 and t["positive_class"] == "1"
    # meta["classes"] is first-seen order among rows with a structure: "1" then "0".
    assert meta["classes"] == ["1", "0"]
    assert [(c["label"], c["count"], c["positive"]) for c in t["classes"]] == [
        ("1", 3, True),
        ("0", 3, False),
    ]
    assert sum(c["count"] for c in t["classes"]) == t["n"]


def test_a_dataset_with_no_target_has_an_explicit_state_not_a_distribution():
    meta, rows, s = read(DUPLICATES, NO_TARGET)
    t = s["target"]
    assert t["state"] == "no_target" and "bins" not in t and "classes" not in t
    assert s["populations"]["training_eligible"]["n"] == 0 == meta["n_training_eligible"]
    # Every row is out of training, for the same reason.
    assert {e["code"]: e["n"] for e in s["exclusions"]["by_reason"]}["no_target"] == 8


def test_no_row_to_train_on_is_an_empty_state_not_an_empty_histogram():
    # resolve refuses a dataset nothing can train on, so build the rows the way
    # resolve would have left them if it did not.
    meta, rows = resolve(b"smiles,y\nCCO,1\nCCN,2\n", regression())
    meta = copy.deepcopy(meta)
    rows = copy.deepcopy(rows)
    for r in rows:
        r.update(training_eligible=False, target=None, exclusions=["censored"])
    meta.update(n_training_eligible=0, exclusions={"censored": 2})
    s = summarize(meta, rows)
    t = s["target"]
    assert t["state"] == "empty" and t["n"] == 0 and "bins" not in t
    assert t["kind"] == "regression" and t["column"] == "y"
    assert s["populations"]["training_eligible"]["n"] == 0
    # ...and so is a classification one.
    cmeta, crows = resolve(b"smiles,y\nCCO,a\nCCN,b\n", classification(positive="a"))
    cmeta, crows = copy.deepcopy(cmeta), copy.deepcopy(crows)
    for r in crows:
        r.update(training_eligible=False, exclusions=["missing_target"])
    assert summarize(cmeta, crows)["target"]["state"] == "empty"


def test_values_that_are_not_finite_are_left_out_and_counted_not_fatal():
    meta, rows = resolve(b"smiles,y\nCCO,1\nCCN,2\nCCC,3\n", regression())
    rows = copy.deepcopy(rows)
    rows[0]["target"] = float("nan")
    rows[1]["target"] = float("inf")
    s = summarize(meta, rows)
    t = s["target"]
    assert t["state"] == "regression" and t["n"] == 3 and t["n_binned"] == 1
    assert t["n_not_binned"] == 2 and t["min"] == t["max"] == 3.0
    json.dumps(s, allow_nan=False)
    # All of them non-finite: nothing to draw, which is the empty state.
    rows[2]["target"] = float("-inf")
    assert summarize(meta, rows)["target"]["state"] == "empty"


# -- the histogram rule ---------------------------------------------------------


def test_the_bin_count_is_rices_rule_and_never_over_fifty():
    # ceil(2 * n^(1/3)), exact for cubes.
    assert [bin_count(n) for n in (1, 8, 27, 64, 100, 106, 1000, 10000)] == [
        2,
        4,
        6,
        8,
        10,
        10,
        20,
        44,
    ]
    assert bin_count(15625) == 50 and bin_count(10**6) == MAX_BINS == 50
    assert bin_count(10**30) == 50
    assert all(bin_count(n) >= 1 for n in range(1, 200))


def test_the_histogram_has_the_edges_it_says_and_counts_every_value():
    h = histogram([1, 2, 3, 4])
    # n = 4 -> 4 bins of width 3/4 between the extremes; the top edge is closed.
    assert h["edges"] == [1, 1.75, 2.5, 3.25, 4]
    assert h["counts"] == [1, 1, 1, 1]
    assert histogram([1, 1, 1, 4])["counts"] == [3, 0, 0, 1]


def test_a_value_on_an_edge_goes_to_the_bin_that_starts_there():
    h = histogram([0, 1, 2, 3, 4, 5, 6, 7, 8])
    # n = 9 -> 5 bins of width 1.6: 0 | 1.6 | 3.2 | 4.8 | 6.4 | 8.
    assert h["edges"] == pytest.approx([0, 1.6, 3.2, 4.8, 6.4, 8])
    assert h["counts"] == [2, 2, 1, 2, 2]
    # Counted by the edges that are published, not by a division that can round the other way.
    for value in h["edges"][:-1]:
        again = histogram([value] + [0, 8])
        k = max(i for i, e in enumerate(again["edges"][:-1]) if e <= value)
        assert again["counts"][k] >= 1


def test_one_distinct_value_is_one_bin_with_equal_edges():
    for values in ([5.0], [5.0, 5.0, 5.0]):
        h = histogram(values)
        assert h["edges"] == [5.0, 5.0] and h["counts"] == [len(values)]


def test_the_histogram_is_deterministic_and_plain_json():
    values = [math.sin(i) * 7 for i in range(500)]
    a, b = histogram(values), histogram(list(reversed(values)))
    assert a == b
    assert json.loads(json.dumps(a, allow_nan=False)) == a


# -- missingness ---------------------------------------------------------------------


def test_blank_and_whitespace_only_cells_are_missing_and_a_malformed_value_is_not():
    # y is numeric but for "abc"; q is blank, whitespace, a tab and a value.
    text = "\n".join(
        [
            "smiles,y,q",
            "CCO,1.5,",
            "CCN,   ,x",
            'CCC,"\t",',
            "CCCC,abc,  ",
            "CCCCC,2.5,y",
        ]
    )
    meta, rows, s = read(text, regression())
    missing = {c["name"]: c["n_missing"] for c in s["missing"]["columns"]}
    assert missing == {"smiles": 0, "y": 2, "q": 3}
    # "abc" is filled: 01 counts it as filled, and rejects it as a target instead.
    assert [c["n_filled"] for c in meta["columns"]] == [5, 3, 2]
    codes = {e["code"]: e["n"] for e in s["exclusions"]["by_reason"]}
    assert codes == {"missing_target": 2, "invalid_target": 1}
    # The target column's missing cells are exactly the rows left out for lacking one.
    assert missing["y"] == codes["missing_target"]


def test_every_source_column_is_listed_in_source_order_with_its_name():
    meta, rows, s = cdd()
    cols = s["missing"]["columns"]
    assert [c["name"] for c in cols] == [c["name"] for c in meta["columns"]]
    assert [c["index"] for c in cols] == list(range(8))
    assert s["missing"]["population"] == "source_rows"
    # Only 4 of the 106 have a qualifier.
    assert {c["name"]: c["n_missing"] for c in cols}[QUAL] == 102


# -- ten thousand rows -----------------------------------------------------------


RINGS = ["c1ccccc1", "c1ccncc1", "c1ccc2ccccc2c1", "C1CCCCC1", "c1ccsc1", "c1ccoc1", "C1CCNCC1"]
TAILS = [*RINGS, "O"]  # the last has no ring: eight scaffolds, one of them the empty one


def ten_thousand():
    """25 x 25 chain lengths x 8 tails x 2 (a methyl or not): 10,000 different
    molecules, none of them real, and the scaffold index each belongs to.
    """
    lines, tail_of = ["name,smiles,y"], []
    for a in range(1, 26):
        for b in range(1, 26):
            for t, tail in enumerate(TAILS):
                for methyl in ("", "C"):
                    n = len(tail_of)
                    tail_of.append(t)
                    smiles = "%sN%s%s%s" % ("C" * a, "C" * b, tail, methyl)
                    lines.append("m%d,%s,%s" % (n, smiles, (n * 37 % 997) / 100))
    return "\n".join(lines), tail_of


def test_ten_thousand_rows_make_a_small_response():
    text, tail_of = ten_thousand()
    meta, rows, s = read(text, regression())
    assert len(rows) == 10000 and s["populations"]["training_eligible"]["n"] == 10000
    t = s["target"]
    assert len(t["bins"]["counts"]) <= MAX_BINS and len(t["bins"]["edges"]) <= MAX_BINS + 1
    assert len(t["bins"]["counts"]) == bin_count(10000) == 44
    assert sum(t["bins"]["counts"]) == 10000
    assert s["duplicates"]["n_duplicate_rows"] == 0
    sc = s["scaffolds"]
    assert sc["n_distinct"] == 8 and sc["acyclic_rows"] == tail_of.count(7) == 1250
    assert sc["top10_groups"] == 8 and sc["top10_share"] == 1.0
    assert sc["largest_group_size"] == 1250
    # Nothing in the answer grows with the rows: it is the same size at 100 as at 10000.
    assert len(json.dumps(s, allow_nan=False)) < 8000
    assert len(s["missing"]["columns"]) == 3


# -- the command -------------------------------------------------------------------


def run(*args):
    proc = subprocess.run(
        [sys.executable, "-m", "ligandbench", *map(str, args)], capture_output=True, text=True
    )
    return proc.returncode, json.loads(proc.stdout), proc.stderr


def written(tmp_path, csv_bytes=CDD):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        json.dumps(
            {
                "structure_column": "SMILES",
                "target": {
                    "column": KI,
                    "kind": "regression",
                    "qualifier_column": QUAL,
                    "transform": "pki",
                    "source_units": "nM",
                },
            }
        )
    )
    src = tmp_path / "source.csv"
    src.write_bytes(csv_bytes)
    out = tmp_path / "ds"
    code, meta, err = run("resolve", src, "--config", cfg, "--out", out)
    assert code == 0, err
    return out, meta


def test_summarize_reads_a_resolved_directory_and_prints_one_json_object(tmp_path):
    out, meta = written(tmp_path)
    code, s, err = run("summarize", out)
    assert code == 0 and err == ""
    assert s["version"] == SUMMARY_VERSION and s["populations"]["source_rows"]["n"] == 106
    assert s["populations"]["training_eligible"]["n"] == meta["n_training_eligible"]
    rows = json.loads((out / "rows.json").read_text())["rows"]
    assert s == summarize(json.loads((out / "dataset.json").read_text()), rows)


def test_info_names_the_summary_version():
    code, out, _ = run("info")
    assert code == 0 and out["summary"] == SUMMARY_VERSION


def test_a_directory_that_is_not_a_dataset_exits_two(tmp_path):
    code, out, _ = run("summarize", tmp_path / "nope")
    assert code == 2 and out["kind"] == "bad-input" and "cannot read" in out["error"]
    (tmp_path / "dataset.json").write_text("{not json")
    (tmp_path / "rows.json").write_text("{}")
    code, out, _ = run("summarize", tmp_path)
    assert code == 2 and "not JSON" in out["error"]
    (tmp_path / "dataset.json").write_text("{}")
    code, out, _ = run("summarize", tmp_path)
    assert code == 2 and "not a ligandbench dataset" in out["error"]
