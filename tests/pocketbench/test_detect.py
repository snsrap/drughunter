"""Pocket detection: the residue cut, the detectors behind one interface, and
the commands ProteinViewer's server drives. Ported from ProteinViewer's own
tests when detection moved here.
"""

import json
import os
import random
import shutil

import pytest
from helpers import FAKE_FPOCKET, chain_lines, fake_executable, pdb, pdb_line, read

from pocketbench.cli import main, residues
from pocketbench.detectors import DetectorRegistry, Option
from pocketbench.detectors.fpocket import FpocketDetector
from pocketbench.detectors.lacuna import LacunaDetector
from pocketbench.errors import BadInput, ToolUnavailable
from pocketbench.pockets import DetectionResult, Pocket, select_residues
from pocketbench.structure import STANDARD_AA, Structure

# Residues on the x axis at 10 Å spacing, so the fake fpocket's pocket i
# (spheres around x = 10 i) lands on residue i.
LINE_TEXT = "\n".join(pdb_line(i, "CA", "GLY", "A", i, 10.0 * i, 0, 0, "C") for i in range(1, 6))
LINE = Structure.from_text(LINE_TEXT, "pdb")


# ---------------------------------------------------------------- the residue cut
def brute_force(structure, centers, cutoff):
    """select_residues without the grid: every atom against every point."""
    hit = {
        a.key
        for a in structure.atoms
        if any(
            (a.x - c[0]) ** 2 + (a.y - c[1]) ** 2 + (a.z - c[2]) ** 2 <= cutoff**2 for c in centers
        )
    }
    hit &= {a.key for a in structure.atoms if a.resn in STANDARD_AA}
    return sorted(hit), sum(1 for a in structure.atoms if a.key in hit)


def test_the_grid_matches_brute_force():
    s = Structure.from_text(read("1CRN.pdb"))
    rng = random.Random(7)
    for cutoff in (3.0, 6.0, 8.5):
        centers = [
            (a.x + rng.uniform(-3, 3), a.y + rng.uniform(-3, 3), a.z + rng.uniform(-3, 3))
            for a in rng.sample(s.atoms, 12)
        ]
        assert select_residues(s, centers, cutoff) == brute_force(s, centers, cutoff)
    assert select_residues(s, []) == ([], 0)


def test_whole_residues_and_standard_only():
    s = Structure.from_text(
        "\n".join(
            [
                pdb_line(1, "N", "SER", "A", 1, 0, 0, 0, "N"),
                pdb_line(2, "OG", "SER", "A", 1, 30, 0, 0, "O"),  # far, same residue
                pdb_line(3, "C1", "LIG", "A", 2, 0.5, 0, 0, "C", het=True),
            ]
        ),
        "pdb",
    )
    assert select_residues(s, [(0, 0, 0)], 2.0) == ([("A", 1, "")], 2)  # the far atom too


def test_chain_is_part_of_residue_identity():
    s = Structure.from_text(
        "\n".join(
            [
                pdb_line(1, "CA", "GLY", "A", 10, 0, 0, 0, "C"),
                pdb_line(2, "CA", "GLY", "B", 10, 50, 0, 0, "C"),
            ]
        ),
        "pdb",
    )
    assert select_residues(s, [(0, 0, 0)], 3.0)[0] == [("A", 10, "")]


def pocket(**kw):
    base = dict(
        rank=1,
        score=0.5,
        druggability=0.25,
        volume=321.04,
        centers=[(0, 0, 0), (2, 0, 0)],
        radii=[3.0, 3.5],
    )
    return Pocket(**{**base, **kw})


def test_a_selection_is_grouped_by_chain():
    p = pocket(residues=[("B", 3, ""), ("A", 9, ""), ("A", 2, ""), ("A", 2, "A")])
    assert p.selection() == [{"chain": "A", "resi": [2, 9]}, {"chain": "B", "resi": [3]}]


def test_unreported_numbers_go_out_as_null():
    d = pocket(score=float("nan")).as_dict()
    assert d["score"] is None and d["persistence"] is None
    assert (d["volume"], d["centroid"], d["spheres"][1]) == (321.0, [1.0, 0.0, 0.0], [2, 0, 0, 3.5])


def test_result_meta_carries_detector_extras():
    r = DetectionResult(
        "x", [pocket()], ["x", "-f", "in.pdb"], 1.234, 10, 6.0, extra={"backend": "nma"}
    )
    assert (len(r), r[0].rank) == (1, 1)
    assert r.meta() == {
        "tool": "x",
        "command": "x -f in.pdb",
        "elapsed": 1.23,
        "receptorAtoms": 10,
        "cutoff": 6.0,
        "nPockets": 1,
        "backend": "nma",
    }


# ---------------------------------------------------------------- the detectors
@pytest.fixture
def fpocket(tmp_path, monkeypatch):
    """A detector over the fake fpocket, and a way to run it for n pockets."""
    det = FpocketDetector(fake_executable(tmp_path, "fpocket", FAKE_FPOCKET))

    def run(n, **kw):
        monkeypatch.setenv("FAKE_FPOCKET_N", str(n))
        return det.run(LINE, str(tmp_path / "work"), cutoff=3.0, **kw)

    return det, run


def test_fpocket_pockets_are_parsed_and_resolved_to_residues(fpocket):
    _, run = fpocket
    result = run(2)
    assert [p.rank for p in result] == [1, 2]
    assert [p.residues for p in result] == [[("A", 1, "")], [("A", 2, "")]]
    assert (result[1].score, result[1].volume) == (0.5, 200.0)
    assert (result.meta()["tool"], result.receptor_atoms) == ("fpocket", 5)


def test_a_rerun_does_not_read_back_an_earlier_runs_pockets(fpocket):
    # Regression: fpocket leaves an existing output directory's files in
    # place, so a second run with fewer pockets reported the old extras.
    _, run = fpocket
    assert len(run(4)) == 4
    assert len(run(1)) == 1


def test_a_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr("pocketbench.detectors.fpocket.shutil.which", lambda _: None)
    det = FpocketDetector(str(tmp_path / "absent"))
    assert not det.available
    with pytest.raises(ToolUnavailable):
        det.run(LINE, str(tmp_path))


def test_lacuna_beside_the_interpreter_is_found(tmp_path, monkeypatch):
    # Where drughunter's ``lacuna`` extra installs it: pocketbench's own environment.
    binary = fake_executable(tmp_path, "lacuna", "")
    monkeypatch.setattr("pocketbench.detectors.lacuna.SEARCH_PATHS", ())
    monkeypatch.setattr("pocketbench.detectors.lacuna.shutil.which", lambda _: None)
    monkeypatch.setattr("pocketbench.detectors.lacuna.sys.executable", str(tmp_path / "python"))
    assert LacunaDetector().binary == binary


def test_nothing_left_after_solvent_is_bad_input(fpocket, tmp_path):
    det, _ = fpocket
    water = Structure.from_text(pdb_line(1, "O", "HOH", "A", 1, 0, 0, 0, "O", het=True))
    with pytest.raises(BadInput):
        det.run(water, str(tmp_path))


def test_lacuna_options_defaults_case_and_bad_values():
    det = LacunaDetector()  # probes are lazy; options() needs none
    assert det.options({"backend": "NMA"}) == {
        "backend": "nma",
        "detector": "alpha",
        "conformers": 20,
    }
    for bad in ({"backend": "xx"}, {"detector": "xx"}, {"conformers": 0}, {"conformers": 2.5}):
        with pytest.raises(BadInput):
            det.options(bad)


def test_options_describe_themselves():
    assert Option("backend", "nma", choices=("nma", "openmm")).as_dict() == {
        "default": "nma",
        "choices": ["nma", "openmm"],
    }
    assert Option("conformers", 20, lo=1, hi=500).as_dict() == {
        "default": 20,
        "integer": True,
        "min": 1,
        "max": 500,
    }


def test_the_registry():
    a, b = FpocketDetector("/absent"), LacunaDetector("/absent")
    reg = DetectorRegistry([a, b])
    assert reg.get("lacuna") is b and reg.names() == ["fpocket", "lacuna"]
    with pytest.raises(BadInput, match="want fpocket or lacuna"):
        reg.get("c")
    with pytest.raises(ValueError):
        reg.register(a)


@pytest.mark.skipif(
    not (shutil.which("fpocket") or os.environ.get("FPOCKET_BIN")),
    reason="fpocket is not installed",
)
def test_real_fpocket_on_crambin(tmp_path):
    result = FpocketDetector(os.environ.get("FPOCKET_BIN")).run(
        Structure.from_text(read("1CRN.pdb")), str(tmp_path)
    )
    assert len(result) > 0
    assert all(p.residues and all(k[0] == "A" for k in p.residues) for p in result)


@pytest.mark.skipif(not os.environ.get("PB_SLOW_TESTS"), reason="set PB_SLOW_TESTS=1 to run lacuna")
def test_real_lacuna_nma_on_crambin(tmp_path):
    det = LacunaDetector(os.environ.get("LACUNA_BIN"))
    if not det.available:
        pytest.skip("lacuna is not installed")
    result = det.run(Structure.from_text(read("1CRN.pdb")), str(tmp_path), conformers=5)
    assert result.meta()["backend"] == "nma" and all(p.residues for p in result)


# ---------------------------------------------------------------- the commands
def test_detect_answers_in_json(tmp_path, capsys, monkeypatch):
    fp = fake_executable(tmp_path, "fpocket", FAKE_FPOCKET)
    (tmp_path / "line.pdb").write_text(LINE_TEXT + "\n")
    monkeypatch.setenv("FAKE_FPOCKET_N", "3")
    assert (
        main(
            [
                "detect",
                "fpocket",
                str(tmp_path / "line.pdb"),
                "--cutoff",
                "3",
                "--fpocket-bin",
                fp,
                "--workdir",
                str(tmp_path / "w"),
                "--json",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["tool"] == "fpocket" and len(out["pockets"]) == 3
    assert out["pockets"][0]["sel"] == [{"chain": "A", "resi": [1]}]
    assert (out["meta"]["totalAtoms"], out["meta"]["cutoff"], out["meta"]["options"]) == (
        5,
        3.0,
        {},
    )


def test_detect_errors_say_whose_problem_they_are(tmp_path, capsys, monkeypatch):
    line = str(tmp_path / "line.pdb")
    (tmp_path / "line.pdb").write_text(LINE_TEXT + "\n")

    def kind(*argv):
        code = main(["detect", *argv, "--json"])
        return code, json.loads(capsys.readouterr().out)

    assert kind("nope", line) == (
        2,
        {"error": "unknown method 'nope' (want fpocket or lacuna)", "kind": "bad-input"},
    )
    code, out = kind("fpocket", line, "--conformers", "5")
    assert (code, out["error"]) == (2, "fpocket takes no --conformers")
    # Not installed anywhere: unavailable.
    monkeypatch.setattr("pocketbench.detectors.fpocket.shutil.which", lambda _: None)
    assert (
        kind("fpocket", line, "--fpocket-bin", str(tmp_path / "absent"))[1]["kind"] == "unavailable"
    )
    # Installed, ran, wrote nothing: failed.
    silent = fake_executable(tmp_path, "silent", "pass\n")
    code, out = kind("fpocket", line, "--fpocket-bin", silent)
    assert (code, out["kind"]) == (4, "failed")


def test_detectors_lists_options_and_additives(capsys):
    assert main(["detectors", "--fpocket-bin", "/absent", "--lacuna-bin", "/absent", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    lac = out["detectors"]["lacuna"]
    assert lac["options"]["backend"] == {"default": "nma", "choices": ["nma", "openmm"]}
    assert "HOH" in out["additives"] and out["cutoff"] == 6.0
    assert "status" in out["detectors"]["fpocket"]


def test_residue_specs():
    st = pdb(
        [l for l in chain_lines("A", "GSGS", [(i, 0, 0) for i in range(4)])],
        ["ATOM    999  CA  GLY A   2A      9.000   0.000   0.000  1.00 10.00           C"],
    )
    assert residues(st, "A:2") == {("A", 2, "")}
    assert residues(st, "A:2A") == {("A", 2, "A")}
    assert residues(st, "A:2-3") == {("A", 2, ""), ("A", 2, "A"), ("A", 3, "")}
    assert len(residues(st, "A")) == 5
    with pytest.raises(BadInput):
        residues(st, "B:1")
    with pytest.raises(BadInput):
        residues(st, "A:x")
