"""The Bemis-Murcko scaffold: one definition for the dashboard and the split."""

import re
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402

from ligandbench.errors import BadInput  # noqa: E402
from ligandbench.scaffold import SCAFFOLD_POLICY, murcko_scaffold  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs" / "ligandbench.md"


def test_side_chains_are_removed_and_the_ring_stays():
    assert murcko_scaffold("CCc1ccccc1") == "c1ccccc1"


def test_a_molecule_with_no_ring_has_the_empty_scaffold():
    assert murcko_scaffold("CCO") == ""


def test_a_ring_system_keeps_its_fused_rings():
    assert murcko_scaffold("c1ccc2ccccc2c1CC") == "c1ccc2ccccc2c1"


def test_the_linker_between_two_rings_is_kept_and_the_side_chains_are_not():
    molecule = "CCOc1ccc(CCc2ccc(Cl)cc2)cc1N"
    assert murcko_scaffold(molecule) == "c1ccc(CCc2ccccc2)cc1"


def test_a_biaryl_keeps_its_bond_and_loses_its_substituents():
    molecule = "Cc1ccc(-c2ccccn2)cc1C(=O)O"
    assert murcko_scaffold(molecule) == "c1ccc(-c2ccccn2)cc1"


def test_a_carbonyl_that_links_two_rings_is_part_of_the_scaffold():
    assert murcko_scaffold("c1ccccc1C(=O)c1ccccc1") == "O=C(c1ccccc1)c1ccccc1"


# -- stereochemistry is not part of a scaffold ----------------------------------


def test_enantiomers_share_a_scaffold():
    one = murcko_scaffold("C[C@H](F)c1ccccc1")
    other = murcko_scaffold("C[C@@H](F)c1ccccc1")
    assert one == other == "c1ccccc1"


def test_cis_and_trans_ring_stereoisomers_share_a_scaffold():
    cis = murcko_scaffold("C[C@H]1CC[C@@H](C)CC1")
    trans = murcko_scaffold("C[C@H]1CC[C@H](C)CC1")
    assert cis == trans == "C1CCCCC1"


def test_the_scaffold_carries_no_stereo_marks_even_when_the_ring_has_them():
    scaffold = murcko_scaffold("C[C@H]1CC[C@@H](C/C=C/c2ccccc2)CC1")
    assert not set("@/\\") & set(scaffold)


# -- what is not done to the input ------------------------------------------------


def test_a_salt_is_scaffolded_as_given_and_the_ring_system_is_not_dropped():
    assert murcko_scaffold("c1ccccc1CC.[Na+].[Cl-]") == "c1ccccc1"


def test_fragments_with_no_ring_contribute_nothing_and_the_result_is_empty():
    assert murcko_scaffold("CC.[Na+].[Cl-]") == ""


def test_two_ring_fragments_are_both_kept_whichever_is_written_first():
    one = murcko_scaffold("c1ccccc1CC.C1CCCCC1N")
    other = murcko_scaffold("C1CCCCC1N.c1ccccc1CC")
    assert one == other == "C1CCCCC1.c1ccccc1"


def test_a_charged_ring_atom_is_not_neutralised():
    # N-methylpyridinium is not pyridine: nothing is neutralised on the way.
    scaffold = murcko_scaffold("C[n+]1ccccc1")
    assert "+" in scaffold
    assert scaffold != murcko_scaffold("Cc1ccccn1")


# -- bad input --------------------------------------------------------------------


@pytest.mark.parametrize("text", ["not a smiles(", "c1ccccc", "C1CC", "Xx"])
def test_a_string_that_does_not_parse_is_bad_input(text):
    with pytest.raises(BadInput):
        murcko_scaffold(text)


@pytest.mark.parametrize("text", ["", "   "])
def test_nothing_is_not_a_structure(text):
    with pytest.raises(BadInput):
        murcko_scaffold(text)


# -- one answer per molecule ------------------------------------------------------

MOLECULES = [
    "CCOc1ccc(CCc2ccc(Cl)cc2)cc1N",
    "Cc1ccc(-c2ccccn2)cc1C(=O)O",
    "C[C@H](F)c1ccccc1",
    "c1ccccc1CC.C1CCCCC1N",
    "O=C(Nc1ccccc1)C1CC1",
    "CCO",
]


@pytest.mark.parametrize("smiles", MOLECULES)
def test_the_same_input_gives_the_same_answer(smiles):
    assert murcko_scaffold(smiles) == murcko_scaffold(smiles)


@pytest.mark.parametrize("smiles", MOLECULES)
def test_every_way_of_writing_a_molecule_gives_the_same_scaffold(smiles):
    # Not canonical, one SMILES per starting atom: the string differs, the
    # molecule does not.
    mol = Chem.MolFromSmiles(smiles)
    written = {
        Chem.MolToSmiles(mol, canonical=False, rootedAtAtom=atom.GetIdx())
        for atom in mol.GetAtoms()
    }
    assert len({murcko_scaffold(text) for text in written | {smiles}}) == 1


# -- the policy id ----------------------------------------------------------------


def test_the_policy_is_a_short_stable_id():
    assert re.fullmatch(r"murcko-v\d+", SCAFFOLD_POLICY)


def test_the_policy_is_documented():
    text = DOCS.read_text()
    assert SCAFFOLD_POLICY in text
    assert "murcko_scaffold" in text
