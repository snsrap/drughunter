"""Reading structure files, and moving their text."""

from pathlib import Path

import pytest

from helpers import T0, rotation
from pocketbench.move import move_text
from pocketbench.structure import Structure, cif_loops, cif_tokens, ATOM_SITE
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
    out = move_text(atom + "\n" + aniso, Transform(rotation((0, 0, 1), 3.141592653589793 / 2), (0, 0, 0)),
                    "pdb").split("\n")
    assert out[0][30:54] == "  -2.000   1.000   3.000"
    assert out[1] == "ANISOU    1  CA  GLY A   1      200   1000     30     -4     -6      5       C"
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
