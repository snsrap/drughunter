"""Alignment, fitting and superposition -- the same cases the viewer's
tests/js/superpose.test.js checks, so the two implementations agree.
"""

import pytest
from helpers import SEQ, T0, chain_lines, pdb, read, rotation, trace

from pocketbench.align import BLOSUM62, align
from pocketbench.cli import main
from pocketbench.structure import Atom
from pocketbench.superpose import Transform, fit, protein_chains, related, superpose


def close(a, b, eps=1e-6):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def det(R):
    return (
        R[0] * (R[4] * R[8] - R[5] * R[7])
        - R[1] * (R[3] * R[8] - R[5] * R[6])
        + R[2] * (R[3] * R[7] - R[4] * R[6])
    )


def test_blosum62_is_symmetric_with_its_known_diagonal():
    n = len(BLOSUM62)
    assert all(BLOSUM62[i][j] == BLOSUM62[j][i] for i in range(n) for j in range(n))
    assert [BLOSUM62[i][i] for i in range(n)] == [
        4,
        5,
        6,
        6,
        9,
        5,
        5,
        6,
        8,
        4,
        4,
        5,
        5,
        6,
        7,
        4,
        5,
        11,
        7,
        4,
        -1,
    ]


def test_align_end_gaps_are_free_and_an_insertion_opens_one_gap():
    _, pairs = align("ACDEFGHIK", "CDEFGWHIK")
    assert pairs == [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4), (6, 6), (7, 7), (8, 8)]
    _, sub = align(SEQ, SEQ[10:40])
    assert len(sub) == 30 and all(i == j + 10 for i, j in sub)
    assert align("", "ACD") == (0.0, [])


def test_fit_recovers_a_rotation_and_never_reflects():
    P = trace(30, 7)
    X, rmsd = fit(P, [T0.apply(p) for p in P])
    assert rmsd < 1e-9 and close(X.R, T0.R) and close(X.t, T0.t)
    M, rmsd = fit(P, [(-p[0], p[1], p[2]) for p in P])
    assert abs(det(M.R) - 1) < 1e-9 and rmsd > 0.1


def test_protein_chains_skip_a_calcium_ion_and_water():
    st = pdb(chain_lines("A", "GS", trace(2, 1)))
    atoms = st.atoms + [
        Atom("CA", "CA", "A", 900, "", 0, 0, 0, "Ca", True),
        Atom("O", "HOH", "A", 901, "", 0, 0, 0, "O", True),
        Atom("CA", "MSE", "B", 1, "", 0, 0, 0, "C", True),
    ]
    assert [(c.chain, c.seq) for c in protein_chains(atoms)] == [("A", "GS"), ("B", "M")]


def test_a_moved_and_renumbered_copy_goes_back_where_it_was():
    cas = trace(len(SEQ), 3)
    ref = pdb(chain_lines("A", SEQ, cas))
    mob = pdb(chain_lines("A", SEQ, cas, first=101, T=T0))
    s = superpose(mob.atoms, ref.atoms)
    assert s.rmsd < 2e-3  # PDB text keeps 3 decimals
    assert (s.n, s.aligned, s.ident, s.related) == (len(SEQ), len(SEQ), 1.0, True)
    assert s.mapping[("A", 101, "")] == ("A", 1, "")


def test_a_loop_that_moved_is_left_out_of_the_fit():
    cas = trace(len(SEQ), 5)
    bent = [(p[0] + 8, p[1], p[2]) if 20 <= i < 23 else p for i, p in enumerate(cas)]
    seq = SEQ[:30] + "W" + SEQ[31:]  # a point mutation
    tail = [(p[0] + 99, p[1], p[2]) for p in trace(3, 9)]
    ref = pdb(chain_lines("A", SEQ, cas))
    mob = pdb(chain_lines("B", seq + "GGG", bent + tail, T=T0))
    s = superpose(mob.atoms, ref.atoms)
    assert (s.aligned, s.n) == (len(SEQ), len(SEQ) - 3)
    assert s.rmsd < 1e-3
    assert s.ident == pytest.approx((len(SEQ) - 1) / len(SEQ))


def test_chains_pair_by_sequence_whatever_their_ids_and_order():
    a, b = SEQ[:28], "WYHPFNDCEGQ" + SEQ[28:45]
    ca, cb = trace(len(a), 11), [(p[0] + 15, p[1], p[2]) for p in trace(len(b), 12)]
    ref = pdb(chain_lines("A", a, ca), chain_lines("B", b, cb))
    mob = pdb(chain_lines("X", b, cb, T=T0), chain_lines("Y", a, ca, T=T0))
    s = superpose(mob.atoms, ref.atoms)
    assert s.aligned == len(a) + len(b) and s.rmsd < 1e-3
    assert sorted(s.chains) == [("X", "B"), ("Y", "A")]


def test_a_selection_narrows_the_fit_but_not_the_pairing():
    cas = trace(len(SEQ), 4)
    hinge = Transform(rotation((0, 0, 1), 0.6), (3.0, 0.0, 0.0))
    moved = [p if i < 30 else hinge.apply(p) for i, p in enumerate(cas)]
    ref = pdb(chain_lines("A", SEQ, cas))
    mob = pdb(chain_lines("A", SEQ, moved, T=T0))
    s = superpose(mob.atoms, ref.atoms, reference_on={("A", r, "") for r in range(1, 13)})
    assert (s.aligned, s.n) == (12, 12) and s.rmsd < 1e-3
    assert (s.paired, s.ident, s.related) == (len(SEQ), 1.0, True)


def test_related_asks_more_identity_of_a_shorter_alignment():
    assert not related(0.23, 13)  # crambin onto HIV protease, as it happens
    assert related(0.98, 99) and related(0.35, 100) and not related(0.25, 100)
    assert related(0.22, 300) and related(0.21, 5000) and not related(1, 0)


def test_superpose_says_why_it_cannot():
    ref = pdb(chain_lines("A", SEQ, trace(len(SEQ), 2)))
    water = [Atom("O", "HOH", "W", 1, "", 0, 0, 0, "O", True)]
    with pytest.raises(ValueError, match="being moved has no protein"):
        superpose(water, ref.atoms)
    with pytest.raises(ValueError, match="part of the reference selected"):
        superpose(ref.atoms, ref.atoms, reference_on={("W", 1, "")})
    with pytest.raises(ValueError, match="at least 3"):
        superpose(pdb(chain_lines("A", "GS", trace(2, 1))).atoms, ref.atoms)


# ------------------------------------------------- a whole real structure
# The cases above are constructed geometry, where the right answer is known
# exactly. This one pushes a real file through the real command: crambin onto a
# moved copy of itself, which the fit has to undo.


def shifted(text: str, dx: float) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            line = "%s%8.3f%s" % (line[:30], float(line[30:38]) + dx, line[38:])
        out.append(line)
    return "\n".join(out) + "\n"


@pytest.mark.smoke
def test_a_whole_real_structure_superposes_onto_a_moved_copy(tmp_path, capsys):
    original = read("1CRN.pdb")
    reference = tmp_path / "1CRN.pdb"
    reference.write_text(original)
    moved = tmp_path / "moved.pdb"
    moved.write_text(shifted(original, 7.5))
    out = tmp_path / "back.pdb"

    assert main(["superpose", str(moved), str(reference), "-o", str(out)]) == 0
    capsys.readouterr()

    first = next(line for line in original.splitlines() if line.startswith("ATOM"))
    came_back = next(line for line in out.read_text().splitlines() if line.startswith("ATOM"))
    assert abs(float(came_back[30:38]) - float(first[30:38])) < 1e-2
    assert abs(float(came_back[38:46]) - float(first[38:46])) < 1e-2
