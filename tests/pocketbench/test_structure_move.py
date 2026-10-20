"""Reading structure files, and moving their text."""

from pathlib import Path

import pytest
from helpers import T0, rotation

from pocketbench.move import move_text
from pocketbench.structure import ATOM_SITE, Structure, cif_loops, cif_tokens
from pocketbench.superpose import Transform

DATA = Path(__file__).parent / "data"


def close(a, b, eps):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def test_pdb_and_mmcif_of_one_entry_agree():
    p, c = Structure.read(DATA / "1CRN.pdb"), Structure.read(DATA / "1CRN.cif")
    assert len(p) == len(c) == 327
    assert [a.key for a in p.atoms] == [a.key for a in c.atoms]
    assert all(close(a.xyz, b.xyz, 1e-9) for a, b in zip(p.atoms, c.atoms))


def test_only_the_first_model_and_one_altloc_are_read():
    line = "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00  0.00           C"
    alt = line[:16] + "B" + line[17:]
    text = "MODEL        1\n%s\n%s\nENDMDL\nMODEL        2\n%s\nENDMDL\n" % (line, alt, line)
    assert len(Structure.from_text(text)) == 1


def test_moving_a_pdb_file_changes_its_coordinates_and_nothing_else():
    text = (DATA / "1CRN.pdb").read_text()
    out = move_text(text, T0, "pdb")
    before, after = Structure.from_text(text), Structure.from_text(out)
    assert all(close(T0.apply(a.xyz), b.xyz, 6e-4) for a, b in zip(before.atoms, after.atoms))
    for l, m in zip(text.split("\n"), out.split("\n")):
        if l.startswith(("ATOM", "HETATM")):
            assert len(m) == len(l) and m[:30] + m[54:] == l[:30] + l[54:]
        else:
            assert m == l


def test_an_anisotropic_tensor_turns_with_its_atom():
    atom = "ATOM      1  CA  GLY A   1       1.000   2.000   3.000  1.00 10.00           C"
    aniso = "ANISOU    1  CA  GLY A   1     1000    200     30      4      5      6       C"
    # A quarter turn about z: x goes to y, so U11 and U22 trade places.
    out = move_text(
        atom + "\n" + aniso, Transform(rotation((0, 0, 1), 3.141592653589793 / 2), (0, 0, 0)), "pdb"
    ).split("\n")
    assert out[0][30:54] == "  -2.000   1.000   3.000"
    assert (
        out[1] == "ANISOU    1  CA  GLY A   1      200   1000     30     -4     -6      5       C"
    )
    with pytest.raises(ValueError, match="does not fit"):
        move_text(atom, Transform((1, 0, 0, 0, 1, 0, 0, 0, 1), (-1e5, 0, 0)), "pdb")


def test_moving_an_mmcif_file_rewrites_three_tokens_a_row():
    text = (DATA / "1CRN.cif").read_text()
    out = move_text(text, T0)
    before, after = Structure.from_text(text), Structure.from_text(out)
    assert all(close(T0.apply(a.xyz), b.xyz, 6e-4) for a, b in zip(before.atoms, after.atoms))
    lines, moved = text.split("\n"), out.split("\n")
    site = next(L for L in cif_loops(lines, ATOM_SITE) if L.cat == "_atom_site.")
    cols = {site.col("Cartn_x"), site.col("Cartn_y"), site.col("Cartn_z")}
    for l, m in zip(lines, moved):
        if l != m:
            x, y = cif_tokens(l), cif_tokens(m)
            assert len(x) == len(y) and {k for k in range(len(x)) if x[k] != y[k]} <= cols


# ---------------------------------------------------------------- ported from ProteinViewer
from helpers import pdb_line, read

from pocketbench.structure import detect_format


def test_format_is_sniffed_when_not_declared():
    assert detect_format(read("1CRN.cif")) == "cif"
    assert detect_format(read("1CRN.pdb")) == "pdb"
    assert detect_format("anything", "mmCIF") == "cif"
    assert len(Structure.from_text(read("1CRN.cif"))) == 327


def test_one_altloc_is_kept():
    s = Structure.from_text(
        "\n".join(
            [
                pdb_line(1, "CA", "SER", "A", 5, 0, 0, 0, "C", alt="A"),
                pdb_line(2, "CA", "SER", "A", 5, 9, 9, 9, "C", alt="B"),
                pdb_line(3, "CA", "GLY", "A", 6, 1, 1, 1, "C"),
            ]
        ),
        "pdb",
    )
    assert [(a.resi, a.x) for a in s.atoms] == [(5, 0.0), (6, 1.0)]


def test_element_falls_back_to_the_atom_name():
    assert (
        Structure.from_text(pdb_line(1, "CA", "GLY", "A", 1, 0, 0, 0, ""), "pdb").atoms[0].element
        == "C"
    )


def test_no_atoms_is_an_error():
    with pytest.raises(ValueError):
        Structure.from_text("HEADER nothing here\n", "pdb")
    with pytest.raises(ValueError):
        Structure.from_text("data_x\n_cell.length_a 1\n", "cif")


def test_cif_quoted_values_and_insertion_codes():
    text = "\n".join(
        [
            "data_q",
            "loop_",
            "_atom_site.group_PDB",
            "_atom_site.label_atom_id",
            "_atom_site.auth_comp_id",
            "_atom_site.auth_asym_id",
            "_atom_site.auth_seq_id",
            "_atom_site.pdbx_PDB_ins_code",
            "_atom_site.Cartn_x",
            "_atom_site.Cartn_y",
            "_atom_site.Cartn_z",
            "_atom_site.type_symbol",
            'HETATM "C1\'" NAG B 7 ? 1.0 2.0 3.0 C',
            "ATOM CA GLY B 52 A 4.0 5.0 6.0 C",
            "#",
        ]
    )
    a, b = Structure.from_text(text).atoms
    assert (a.name, a.resn, a.chain, a.resi, a.hetero, a.icode) == ("C1'", "NAG", "B", 7, True, "")
    assert b.key == ("B", 52, "A")


def receptor_input():
    return Structure.from_text(
        "\n".join(
            [
                pdb_line(1, "CA", "ALA", "A", 1, 0, 0, 0, "C"),
                pdb_line(2, "H", "ALA", "A", 1, 0, 0, 1, "H"),
                pdb_line(3, "O", "HOH", "A", 100, 5, 5, 5, "O", het=True),
                pdb_line(4, "C1", "LIG", "B", 200, 3, 3, 3, "C", het=True),
                pdb_line(5, "ZN", "ZN", "B", 300, 4, 4, 4, "ZN", het=True),
            ]
        ),
        "pdb",
    )


def test_receptor_drops_solvent_hydrogens_and_named_ligands():
    r = receptor_input().receptor(drop_extra=frozenset({"LIG"}))
    assert [a.resn for a in r.atoms] == ["ALA", "ZN"]


def test_every_receptor_atom_is_written_as_ATOM():
    # fpocket ignores HETATM, so a cofactor left as hetero reads as cavity.
    lines = receptor_input().receptor().to_pdb().splitlines()
    assert all(l.startswith("ATOM  ") for l in lines[:-1]) and lines[-1] == "END"
    assert len(Structure.from_text(receptor_input().receptor().to_pdb())) == 3
