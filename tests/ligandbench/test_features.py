"""The featuriser: one vector per molecule, the same one at training and inference."""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402

from ligandbench import features as F  # noqa: E402
from ligandbench.chem import canonical_smiles  # noqa: E402
from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.features import (  # noqa: E402
    FEATURISER_VERSION,
    FeatureResult,
    Featuriser,
    feature_schema,
    require_all,
)

DOCS = Path(__file__).resolve().parents[2] / "docs" / "ligandbench.md"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def one(smiles):
    result = Featuriser().featurise([("r", smiles)])
    assert result.failures == [], result.failures
    return result.matrix[0]


# -- shape, order, dtype ----------------------------------------------------------


def test_the_matrix_is_float32_with_2055_columns():
    result = Featuriser().featurise([(0, ASPIRIN), (1, "CCO")])
    assert result.matrix.dtype == np.float32
    assert result.matrix.shape == (2, 2055)
    assert result.row_ids == [0, 1]


def test_fingerprint_bits_are_exactly_zero_or_one_and_come_first():
    row = one(ASPIRIN)
    bits = row[:2048]
    assert set(np.unique(bits)) <= {0.0, 1.0}
    assert bits.sum() > 0


def test_the_seven_descriptors_follow_the_bits_in_the_stated_order():
    row = one(ASPIRIN)
    mol = Chem.MolFromSmiles(ASPIRIN)
    from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors

    expected = [
        Descriptors.MolWt(mol),
        Crippen.MolLogP(mol),
        rdMolDescriptors.CalcTPSA(mol),
        rdMolDescriptors.CalcNumHBD(mol),
        rdMolDescriptors.CalcNumHBA(mol),
        rdMolDescriptors.CalcNumRotatableBonds(mol),
        mol.GetNumHeavyAtoms(),
    ]
    assert row[2048:] == pytest.approx(np.float32(expected), rel=0, abs=0)
    # aspirin, by hand: C9H8O4 is 180.159 and has 13 heavy atoms, one acid OH donor
    assert row[2048] == pytest.approx(180.159, abs=1e-3)
    assert row[2054] == 13
    assert row[2051] == 1


def test_the_descriptor_columns_are_named_in_the_schema_in_order():
    schema = feature_schema()
    assert [d["name"] for d in schema["descriptors"]] == [
        "mol_wt",
        "log_p",
        "tpsa",
        "hbd",
        "hba",
        "rotatable_bonds",
        "heavy_atoms",
    ]
    assert schema["columns"][:2] == ["morgan_0000", "morgan_0001"]
    assert schema["columns"][2047] == "morgan_2047"
    assert schema["columns"][2048:] == [d["name"] for d in schema["descriptors"]]
    assert len(schema["columns"]) == schema["n_features"] == 2055


def test_the_fingerprint_is_the_documented_morgan_generator():
    from rdkit.Chem import rdFingerprintGenerator as gen

    mol = Chem.MolFromSmiles(ASPIRIN)
    reference = gen.GetMorganGenerator(
        radius=2, fpSize=2048, includeChirality=True, countSimulation=False
    )
    assert (one(ASPIRIN)[:2048] == reference.GetFingerprintAsNumPy(mol)).all()


# -- identity, chirality, determinism ---------------------------------------------


def test_one_molecule_written_two_ways_gives_one_vector_through_its_identity():
    a, _ = canonical_smiles("OC(=O)c1ccccc1OC(C)=O")
    b, _ = canonical_smiles(ASPIRIN)
    assert a == b
    assert (one(a) == one(b)).all()


def test_enantiomers_differ_in_the_stereo_bits_only_because_of_include_chirality():
    left = one("C[C@H](N)c1ccccc1")
    right = one("C[C@@H](N)c1ccccc1")
    assert (left[:2048] != right[:2048]).any()
    # descriptors do not see handedness
    assert (left[2048:] == right[2048:]).all()


def test_the_vector_is_deterministic_across_calls_and_instances():
    first = Featuriser().featurise([("a", ASPIRIN), ("b", "CCN")]).matrix
    second = Featuriser().featurise([("a", ASPIRIN), ("b", "CCN")]).matrix
    assert (first == second).all()


def test_a_row_is_the_same_alone_and_in_a_batch():
    batch = Featuriser().featurise([("a", "CCN"), ("b", ASPIRIN), ("c", "CCO")]).matrix
    assert (batch[1] == one(ASPIRIN)).all()


def test_the_structure_is_not_normalised_a_salt_changes_the_vector():
    assert (one("CCN") != one("CCN.[Cl-]")).any()


def test_order_follows_the_input_never_the_ids():
    result = Featuriser().featurise([(9, "CCO"), (2, "CCN"), (5, "CCC")])
    assert result.row_ids == [9, 2, 5]
    assert (result.matrix[1] == one("CCN")).all()


# -- failures are reported, never raised ------------------------------------------


def test_an_unparseable_structure_is_a_failure_with_its_row_id_and_the_rest_keep_order():
    result = Featuriser().featurise([(0, "CCO"), (1, "not smiles"), (2, ""), (3, "CCN")])
    assert result.row_ids == [0, 3]
    assert result.matrix.shape == (2, 2055)
    assert result.failures == [
        {"row_id": 1, "reason": "not a valid SMILES"},
        {"row_id": 2, "reason": "no structure"},
    ]


def test_a_nonfinite_descriptor_is_a_failure_that_names_the_descriptor(monkeypatch):
    # No real molecule gives a nonfinite RDKit descriptor, so the logP function is
    # replaced by one that does for a single molecule.
    real = F._DESCRIPTORS[1]
    nitrogen = Chem.MolFromSmarts("[#7]")

    def broken(mol):
        return (
            float("nan")
            if mol.GetNumAtoms() == 3 and mol.HasSubstructMatch(nitrogen)
            else real[2](mol)
        )

    patched = list(F._DESCRIPTORS)
    patched[1] = (real[0], real[1], broken)
    monkeypatch.setattr(F, "_DESCRIPTORS", patched)
    result = Featuriser().featurise([(0, "CCO"), (1, "CCN"), (2, ASPIRIN)])
    assert result.row_ids == [0, 2]
    assert result.failures == [{"row_id": 1, "reason": "descriptor log_p is not finite"}]


def test_a_descriptor_too_large_for_float32_is_a_failure_not_an_infinity(monkeypatch):
    real = F._DESCRIPTORS[0]
    patched = list(F._DESCRIPTORS)
    patched[0] = (real[0], real[1], lambda mol: 1e300)
    monkeypatch.setattr(F, "_DESCRIPTORS", patched)
    result = Featuriser().featurise([(0, "CCO")])
    assert result.failures == [{"row_id": 0, "reason": "descriptor mol_wt is not finite"}]
    assert np.isfinite(result.matrix).all()


def test_an_empty_input_is_an_empty_matrix_of_the_right_width():
    result = Featuriser().featurise([])
    assert result.matrix.shape == (0, 2055) and result.matrix.dtype == np.float32
    assert result.row_ids == [] and result.failures == []


def test_require_all_returns_a_clean_result_and_names_failing_rows():
    clean = Featuriser().featurise([(0, "CCO")])
    assert require_all(clean) is clean
    bad = Featuriser().featurise([(7, "xx"), (8, "CCO"), (9, "yy")])
    with pytest.raises(BadInput) as raised:
        require_all(bad)
    message = str(raised.value)
    assert "7" in message and "9" in message and "more" not in message


def test_require_all_lists_at_most_twenty_ids():
    result = FeatureResult(
        matrix=np.zeros((0, 2055), np.float32),
        row_ids=[],
        failures=[{"row_id": 100 + i, "reason": "not a valid SMILES"} for i in range(25)],
    )
    with pytest.raises(BadInput) as raised:
        require_all(result)
    message = str(raised.value)
    assert "119" in message and "120" not in message
    assert "and 5 more" in message


# -- the schema ---------------------------------------------------------------------


def test_the_schema_is_json_and_round_trips_to_an_equal_featuriser():
    schema = feature_schema()
    assert json.loads(json.dumps(schema)) == schema
    rebuilt = Featuriser.from_schema(json.loads(json.dumps(schema)))
    assert rebuilt.schema == schema
    assert (
        rebuilt.featurise([(0, ASPIRIN)]).matrix == Featuriser().featurise([(0, ASPIRIN)]).matrix
    ).all()


def test_the_schema_records_every_generator_flag_and_the_dependencies():
    schema = feature_schema()
    fp = schema["fingerprint"]
    assert (fp["kind"], fp["radius"], fp["fpSize"]) == ("morgan", 2, 2048)
    assert fp["includeChirality"] is True and fp["countSimulation"] is False
    assert fp["output"] == "bit"
    for flag in (
        "useBondTypes",
        "includeRingMembership",
        "onlyNonzeroInvariants",
        "numBitsPerFeature",
    ):
        assert flag in fp
    assert schema["version"] == FEATURISER_VERSION
    assert schema["dtype"] == "float32"
    assert set(schema["dependencies"]) == {"rdkit", "numpy"}
    assert (schema["scaling"], schema["imputation"]) == ("none", "none")
    assert all("." in d["function"] for d in schema["descriptors"])


def test_the_schema_returned_by_a_featuriser_is_a_copy():
    featuriser = Featuriser()
    featuriser.schema["fingerprint"]["radius"] = 9
    assert featuriser.schema["fingerprint"]["radius"] == 2


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s.update(version="morgan2048-desc7-v0"),
        lambda s: s["fingerprint"].update(radius=3),
        lambda s: s["fingerprint"].update(fpSize=1024),
        lambda s: s["fingerprint"].update(includeChirality=False),
        lambda s: s["fingerprint"].update(countSimulation=True),
        lambda s: s["fingerprint"].update(useBondTypes=False),
        lambda s: s["descriptors"].reverse(),
        lambda s: s["descriptors"].pop(),
        lambda s: s.update(dtype="float64"),
        lambda s: s["columns"].reverse(),
        lambda s: s.update(imputation="mean"),
        lambda s: s.pop("fingerprint"),
        lambda s: s.update(extra_option=True),
    ],
)
def test_a_schema_this_build_does_not_implement_is_refused(edit):
    schema = copy.deepcopy(feature_schema())
    edit(schema)
    with pytest.raises(BadInput):
        Featuriser.from_schema(schema)


def test_a_schema_that_is_not_an_object_is_refused():
    with pytest.raises(BadInput):
        Featuriser.from_schema(None)  # type: ignore[arg-type]


def test_a_different_dependency_version_is_reported_not_refused():
    schema = feature_schema()
    schema["dependencies"] = {"rdkit": "1999.01.1", "numpy": schema["dependencies"]["numpy"]}
    Featuriser.from_schema(schema)
    assert Featuriser.dependency_drift(schema) == {
        "rdkit": {"recorded": "1999.01.1", "current": feature_schema()["dependencies"]["rdkit"]}
    }
    assert Featuriser.dependency_drift(feature_schema()) == {}


# -- layering and docs ------------------------------------------------------------------


def test_the_module_is_documented():
    text = DOCS.read_text()
    assert FEATURISER_VERSION in text
    assert "features.py" in text
