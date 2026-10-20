"""Carrying a holo ligand's site into the apo structure, scoring pockets
against it, and the command line around both.
"""

import json

from helpers import SEQ, T0, chain_lines, ligand_lines, pdb, trace

from pocketbench.cli import main
from pocketbench.reports import parse_pockets
from pocketbench.score import Criteria, best_rank, score
from pocketbench.site import sites

CAS = trace(len(SEQ), 21)
# A ligand sitting against residues 10-12.
LIG = [(CAS[10][0] + dx, CAS[10][1] + 2.5, CAS[10][2]) for dx in (-1.0, 0.0, 1.0)]


def apo_holo():
    """The apo entry as deposited; the holo one in another frame, on chain B
    and renumbered from 101 -- neither of which may change where its site
    lands. Also an apo entry lacking residue 11 (disordered, say).
    """
    apo = pdb(chain_lines("A", SEQ, CAS))
    holo = pdb(
        chain_lines("B", SEQ, CAS, first=101, T=T0), ligand_lines("LIG", "B", 900, LIG, T=T0)
    )
    apo_missing = pdb([l for l in chain_lines("A", SEQ, CAS) if " A  11 " not in l])
    return apo, holo, apo_missing


def test_a_site_lands_in_the_apo_frame_and_numbering():
    apo, holo, _ = apo_holo()
    sup, [s] = sites(apo, holo, "LIG", cutoff=5.0)
    assert sup.rmsd < 1e-3
    assert all(abs(a - b) < 2e-3 for p, q in zip(s.atoms, LIG) for a, b in zip(p, q))
    assert s.copy == ("B", 900, "")
    assert ("B", 111, "") in s.holo_shell
    assert s.shell == {("A", k[1] - 100, "") for k in s.holo_shell}
    assert not s.missing


def test_a_shell_residue_the_apo_lacks_still_counts():
    _, holo, apo = apo_holo()
    _, [s] = sites(apo, holo, "LIG", cutoff=5.0)
    assert s.missing == {("B", 111, "")}  # residue 11 of the apo, as numbered in the holo
    assert ("A", 11, "") not in s.shell
    assert s.size == len(s.shell) + len(s.missing)


def lacuna_report(apo_site):
    shell = sorted(apo_site.shell)
    c = apo_site.centroid
    return {
        "n_pockets_found": 2,
        "pockets": [
            {
                "rank": 1,
                "centroid": [c[0] + 30, c[1], c[2]],
                "lining_residues": ["GLY50:A"],
                "persistence": 0.9,
                "crypticity": 0.1,
                "druggability": 0.4,
            },
            {
                "rank": 2,
                "centroid": [c[0] + 1, c[1], c[2]],
                "lining_residues": ["%s%d:%s" % ("ALA", k[1], k[0]) for k in shell],
                "persistence": 0.6,
                "crypticity": 0.7,
                "druggability": 0.8,
            },
        ],
    }


def test_pockets_are_scored_on_every_criterion_separately():
    apo, holo, _ = apo_holo()
    _, [s] = sites(apo, holo, "LIG")
    source, pockets = parse_pockets(lacuna_report(s))
    assert source == "lacuna" and pockets[1].info["crypticity"] == 0.7
    far, near = score(pockets, s, Criteria())
    assert far.hits == {"coverage": False, "dcc": False, "dca": False}
    assert near.coverage == 1.0 and near.jaccard == 1.0 and near.dcc < 1.1
    assert near.dca < near.dcc
    assert [best_rank([far, near], c) for c in ("coverage", "dcc", "dca")] == [2, 2, 2]


def test_viewer_pocket_files_read_too():
    source, [p] = parse_pockets(
        {
            "structure": "1BTL",
            "meta": {},
            "pockets": [
                {
                    "rank": 3,
                    "centroid": [1, 2, 3],
                    "sel": [{"chain": "A", "resi": [70, 73]}],
                    "drug": 0.5,
                    "persistence": None,
                    "score": 12.5,
                }
            ],
        }
    )
    assert source == "viewer" and p.rank == 3
    assert p.residues == {("A", 70, ""), ("A", 73, "")}
    assert p.info == {"druggability": 0.5, "score": 12.5}


def write(tmp_path, apo, holo, report):
    paths = {}
    for name, st in (("apo.pdb", apo), ("holo.pdb", holo)):
        (tmp_path / name).write_text(st.text)
        paths[name] = str(tmp_path / name)
    (tmp_path / "report.json").write_text(json.dumps(report))
    return paths["apo.pdb"], paths["holo.pdb"], str(tmp_path / "report.json")


def test_the_command_line_scores_a_report(tmp_path, capsys):
    apo, holo, _ = apo_holo()
    _, [s] = sites(apo, holo, "LIG")
    a, h, r = write(tmp_path, apo, holo, lacuna_report(s))
    assert main(["score", r, a, h, "LIG"]) == 0
    out = capsys.readouterr().out
    assert "best rank — coverage ≥ 50 %: 2 · DCC ≤ 5.0 Å: 2 · DCA ≤ 4.0 Å: 2" in out
    assert main(["score", r, a, h, "LIG", "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["source"] == "lacuna" and j["sites"][0]["best"] == {"coverage": 2, "dcc": 2, "dca": 2}


def test_the_command_line_superposes_and_writes_the_moved_file(tmp_path, capsys):
    apo, holo, _ = apo_holo()
    a, h, _ = write(tmp_path, apo, holo, {"pockets": []})
    out = tmp_path / "moved.pdb"
    assert main(["superpose", h, a, "-o", str(out), "--on", "A:1-30"]) == 0
    assert "100 % identical" in capsys.readouterr().out
    lig = [l for l in out.read_text().splitlines() if l.startswith("HETATM")]
    assert abs(float(lig[1][30:38]) - LIG[1][0]) < 2e-3


def test_the_command_line_reports_errors_without_a_traceback(tmp_path, capsys):
    apo, holo, _ = apo_holo()
    a, h, r = write(tmp_path, apo, holo, {"pockets": []})
    assert main(["site", a, h, "NOPE"]) == 2
    assert "no residue named NOPE" in capsys.readouterr().err
