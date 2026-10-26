"""Pose scoring: the SDF plumbing, the scorer behind one interface, and the
command ProteinViewer's server drives.

The scorer is a fake smina: it writes the scores the real one would write, as
SD tags on the records it is given, so everything around it -- the receptor
cut, the box, the join back onto the input order, a tool that fails on one
molecule of many -- is tested without smina installed.
"""

import json
import math
import os

import pytest
from helpers import fake_executable, pdb_line, read

from pocketbench import poses as sdf
from pocketbench.cli import main
from pocketbench.errors import BadInput, ToolFailed, ToolUnavailable
from pocketbench.scoring import MODES, ScorerRegistry, site_points, write_receptor
from pocketbench.scoring.smina import GninaScorer, SminaScorer
from pocketbench.structure import Structure

# One molecule: two heavy atoms and a hydrogen, so heavy_atoms has something
# to leave out.
MOL = """pocket1_%d
     RDKit          3D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
    2.0000    0.9000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""


def poses_sdf(n=3):
    return "".join((MOL % i) + "$$$$\n" for i in range(1, n + 1))


def receptor_pdb():
    """A few protein atoms, a water, and a hydrogen that must survive."""
    return (
        "\n".join(
            [
                pdb_line(1, "CA", "GLY", "A", 1, 0, 0, 0, "C"),
                pdb_line(2, "CB", "GLY", "A", 1, 1, 0, 0, "C"),
                pdb_line(3, "HA", "GLY", "A", 1, 1, 1, 0, "H"),
                pdb_line(4, "O", "HOH", "A", 2, 9, 9, 9, "O", het=True),
                pdb_line(5, "C1", "HEC", "A", 3, 2, 2, 2, "C", het=True),
            ]
        )
        + "\nEND\n"
    )


def spheres_pdb():
    """Pocket points as a project writes them: one residue per pocket, the
    residue number being the rank.
    """
    return (
        "\n".join(
            [
                pdb_line(1, "O", "STP", "P", 1, 0, 0, 0, "O", het=True),
                pdb_line(2, "O", "STP", "P", 1, 1, 0, 0, "O", het=True),
                pdb_line(3, "O", "STP", "P", 2, 40, 0, 0, "O", het=True),
            ]
        )
        + "\nEND\n"
    )


# A stand-in for smina: copies each record through with a score on it. The
# scores descend, so sorting by them is visibly not the input order.
FAKE_SMINA = r"""
import os, sys
args = sys.argv
if "--version" in args:
    print("smina 2020.12.10")
    raise SystemExit(0)
lig = args[args.index("-l") + 1]
out = args[args.index("-o") + 1]
rec = args[args.index("-r") + 1]
open(os.environ["FAKE_SMINA_LOG"], "a").write(" ".join(args[1:]) + "\n")
records = [r for r in open(lig).read().split("$$$$") if r.strip()]
fail = os.environ.get("FAKE_SMINA_FAIL_ON")
moved = "--score_only" not in args
with open(out, "w") as fh:
    for i, r in enumerate(records):
        if fail is not None and ("pocket1_%s" % fail) in r:
            if len(records) > 1:
                raise SystemExit(1)      # the whole batch dies on the bad one
            raise SystemExit(1)          # and so does it on its own
        fh.write(r.rstrip("\n") + "\n")
        fh.write("> <minimizedAffinity>\n-%d.5\n\n" % (i + 5))
        if moved:
            fh.write("> <minimizedRMSD>\n0.%d\n\n" % (i + 1))
        fh.write("$$$$\n")
"""


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """A fake smina, and the log of how it was called."""
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_SMINA_LOG", str(log))
    binary = fake_executable(tmp_path, "smina", FAKE_SMINA)
    scorer = SminaScorer(binary)
    scorer.log = log  # type: ignore[attr-defined]
    return scorer


@pytest.fixture
def files(tmp_path):
    (tmp_path / "receptor.pdb").write_text(receptor_pdb())
    (tmp_path / "poses.sdf").write_text(poses_sdf())
    (tmp_path / "spheres.pdb").write_text(spheres_pdb())
    return tmp_path


# A real molecule, since a real scorer reads it with a real chemistry toolkit:
# benzene, planar, 1.39 Å ring, written where the caller puts it.
RING = [
    (1.39, 0.0),
    (0.695, 1.204),
    (-0.695, 1.204),
    (-1.39, 0.0),
    (-0.695, -1.204),
    (0.695, -1.204),
]
BONDS = [(1, 2, 2), (2, 3, 1), (3, 4, 2), (4, 5, 1), (5, 6, 2), (6, 1, 1)] + [
    (i, i + 6, 1) for i in range(1, 7)
]


def benzene_sdf(centre: tuple[float, float, float]) -> str:
    cx, cy, cz = centre
    atoms = [(cx + x, cy + y, cz, "C") for x, y in RING]
    atoms += [(cx + x * 1.777, cy + y * 1.777, cz, "H") for x, y in RING]
    lines = ["benzene", "     drughunter          3D", ""]
    lines.append("%3d%3d  0  0  0  0  0  0  0  0999 V2000" % (len(atoms), len(BONDS)))
    for x, y, z, el in atoms:
        lines.append("%10.4f%10.4f%10.4f %-3s 0  0  0  0  0  0  0  0  0  0  0  0" % (x, y, z, el))
    for a, b, order in BONDS:
        lines.append("%3d%3d%3d  0" % (a, b, order))
    lines += ["M  END", "$$$$", ""]
    return "\n".join(lines)


def centre_of(pdb_text: str) -> tuple[float, float, float]:
    xyz = [
        (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        for line in pdb_text.splitlines()
        if line.startswith("ATOM")
    ]
    n = len(xyz)
    return tuple(sum(c[i] for c in xyz) / n for i in range(3))  # type: ignore[return-value]


# ---------------------------------------------------------------- the SDF plumbing
def test_records_are_split_and_rejoined():
    records = sdf.split(poses_sdf(3))
    assert len(records) == 3
    assert sdf.title(records[0]) == "pocket1_1"
    assert sdf.split(sdf.join(records)) == records
    assert sdf.split("") == []
    assert sdf.split("$$$$\n") == []


def test_heavy_atoms_leaves_hydrogens_out():
    assert sdf.heavy_atoms(sdf.split(poses_sdf(1))[0]) == 2
    assert sdf.heavy_atoms("not a molecule\n") is None


def test_tags_are_read_and_replaced():
    record = sdf.split(poses_sdf(1))[0]
    with_tags = sdf.set_tags(record, {"score": -7.5, "rmsd_min": None, "note": "a\nb"})
    assert sdf.tags(with_tags) == {"score": "-7.5", "note": "a\nb"}
    again = sdf.set_tags(with_tags, {"score": -1.0})
    assert sdf.tags(again)["score"] == "-1.0"
    # replacing a tag must not multiply it, and the atom block must survive
    assert again.count("> <score>") == 1
    assert sdf.heavy_atoms(again) == 2


def test_number_only_accepts_numbers():
    assert sdf.number(" -2.5 ") == -2.5
    assert sdf.number("") is None and sdf.number(None) is None


# ---------------------------------------------------------------- the receptor and the box
def test_the_receptor_keeps_its_hydrogens_and_cofactor(files):
    path, warnings = write_receptor(str(files / "receptor.pdb"), str(files / "work"))
    written = Structure.read(path)
    # Hydrogens stay: a scoring function types donors by them, so dropping
    # them (as pocket detection does) would change every number.
    assert [a.element for a in written.atoms].count("H") == 1
    resn = {a.resn for a in written.atoms}
    assert "HEC" in resn  # a cofactor is part of the wall
    assert "HOH" not in resn  # solvent is not
    assert warnings == []


def test_a_receptor_without_hydrogens_is_warned_about(files):
    (files / "dry.pdb").write_text(pdb_line(1, "CA", "GLY", "A", 1, 0, 0, 0, "C") + "\nEND\n")
    _, warnings = write_receptor(str(files / "dry.pdb"), str(files / "work"))
    assert warnings and "hydrogens" in warnings[0]


def test_deleting_the_ligand_can_empty_the_receptor(files):
    with pytest.raises(BadInput):
        write_receptor(str(files / "receptor.pdb"), str(files / "work"), frozenset({"GLY", "HEC"}))


def test_the_box_comes_from_one_pockets_points(files):
    path = site_points(str(files / "spheres.pdb"), 1, str(files / "work"))
    assert len(Structure.read(path).atoms) == 2  # pocket 1 only, not pocket 2
    with pytest.raises(BadInput):
        site_points(str(files / "spheres.pdb"), 9, str(files / "work"))


# ---------------------------------------------------------------- the scorer
def test_every_pose_is_scored_and_joined_back_by_order(tool, files):
    run = tool.score(
        str(files / "receptor.pdb"),
        str(files / "poses.sdf"),
        str(files / "work"),
        mode="minimize",
        site=str(files / "spheres.pdb"),
        site_rank=1,
    )
    assert [p.index for p in run] == [0, 1, 2]
    assert [p.name for p in run] == ["pocket1_1", "pocket1_2", "pocket1_3"]
    assert [p.score for p in run] == [-5.5, -6.5, -7.5]
    assert [p.rmsd_min for p in run] == [0.1, 0.2, 0.3]
    # ligand efficiency is the score over the pose's own heavy atoms
    assert [p.heavy for p in run] == [2, 2, 2]
    assert [p.le for p in run] == [2.75, 3.25, 3.75]
    assert run.failed == 0
    meta = run.meta()
    assert meta["scored"] == 3 and meta["mode"] == "minimize"
    assert "smina" in meta["scorer"]
    assert os.path.isfile(run.minimized)


def test_the_mode_decides_the_flags(tool, files):
    def flags(**kw):
        tool.log.write_text("")
        tool.score(str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"), **kw)
        return tool.log.read_text()

    assert "--score_only" in flags(mode="rescore")
    assert "--minimize" in flags(mode="minimize-free")
    boxed = flags(mode="minimize", site=str(files / "spheres.pdb"), site_rank=1, padding=6)
    assert "--local_only" in boxed and "--autobox_add 6" in boxed
    assert "--autobox_ligand" in boxed


def test_minimize_without_a_box_is_refused_rather_than_guessed(tool, files):
    with pytest.raises(BadInput):
        tool.score(str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"))


def test_rescore_keeps_no_poses_because_nothing_moved(tool, files):
    run = tool.score(
        str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"), mode="rescore"
    )
    assert run.minimized == ""
    assert [p.rmsd_min for p in run] == [None, None, None]


def test_one_bad_molecule_does_not_cost_the_others(tool, files, monkeypatch):
    monkeypatch.setenv("FAKE_SMINA_FAIL_ON", "2")
    run = tool.score(
        str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"), mode="rescore"
    )
    assert [p.index for p in run] == [0, 1, 2]
    assert run.failed == 1
    assert run.poses[1].score is None and run.poses[1].error
    assert run.poses[0].score is not None and run.poses[2].score is not None
    assert any("one molecule at a time" in w for w in run.warnings)


def test_an_empty_sdf_is_a_bad_input(tool, files):
    (files / "none.sdf").write_text("")
    with pytest.raises(BadInput):
        tool.score(str(files / "receptor.pdb"), str(files / "none.sdf"), str(files / "work"))


def test_a_missing_tool_is_unavailable(files):
    scorer = SminaScorer("/nowhere/smina")
    assert not scorer.available
    assert "NOT FOUND" in scorer.status_line()
    with pytest.raises(ToolUnavailable):
        scorer.score(str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"))


def test_an_unknown_mode_is_a_bad_input(tool, files):
    with pytest.raises(BadInput):
        tool.score(
            str(files / "receptor.pdb"), str(files / "poses.sdf"), str(files / "work"), mode="dock"
        )


def test_gnina_turns_its_network_off_so_it_scores_like_smina():
    assert GninaScorer("/nowhere/gnina").base_flags() == ["--cnn_scoring", "none", "--no_gpu"]


# ---------------------------------------------------------------- the registry
def test_the_registry_offers_what_is_installed(tool):
    missing = SminaScorer("/nowhere/smina")
    missing.name = "gnina"
    reg = ScorerRegistry([missing, tool])
    assert reg.names() == ["gnina", "smina"]
    assert reg.get("smina") is tool
    assert reg.get(None) is tool  # nothing asked for: the one that can run
    with pytest.raises(BadInput):
        reg.get("vina")
    with pytest.raises(ToolUnavailable):
        ScorerRegistry([missing]).get(None)


def test_what_a_scorer_publishes_is_checkable(tool):
    spec = ScorerRegistry([tool]).describe()["smina"]
    assert spec["available"] is True
    assert spec["options"]["mode"]["choices"] == list(MODES)
    assert spec["options"]["padding"]["default"] == 4
    assert tool.options({"mode": "RESCORE"}) == {"mode": "rescore", "padding": 4}
    assert tool.options({})["mode"] == "minimize"
    with pytest.raises(BadInput):
        tool.options({"padding": 99})
    with pytest.raises(BadInput):
        tool.options({"mode": "dock"})


# ---------------------------------------------------------------- the command line
def run_cli(*args):
    return main(list(args))


def test_the_command_scores_an_sdf_and_prints_json(tool, files, capsys):
    code = run_cli(
        "score-poses",
        str(files / "receptor.pdb"),
        str(files / "poses.sdf"),
        "--smina-bin",
        tool.binary,
        "--mode",
        "minimize",
        "--site",
        str(files / "spheres.pdb"),
        "--site-rank",
        "1",
        "-o",
        str(files / "min.sdf"),
        "--json",
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert [p["index"] for p in out["poses"]] == [0, 1, 2]
    assert out["poses"][0]["score"] == -5.5
    assert out["poses"][0]["le"] == 2.75
    assert out["meta"]["scored"] == 3
    assert (files / "min.sdf").is_file()
    # the input is never written over
    assert sdf.tags(sdf.split((files / "poses.sdf").read_text())[0]) == {}


def test_the_command_prints_a_table_and_says_what_a_score_is_not(tool, files, capsys):
    code = run_cli(
        "score-poses",
        str(files / "receptor.pdb"),
        str(files / "poses.sdf"),
        "--smina-bin",
        tool.binary,
        "--mode",
        "rescore",
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "pocket1_1" in text and "-5.50" in text
    assert "not a measured affinity" in text


def test_a_tool_that_scores_nothing_at_all_is_a_failed_tool(tool, files, monkeypatch):
    monkeypatch.setenv("FAKE_SMINA_FAIL_ON", "")  # every record holds "pocket1_"
    with pytest.raises(ToolFailed):
        tool.score(
            str(files / "receptor.pdb"),
            str(files / "poses.sdf"),
            str(files / "work"),
            mode="rescore",
        )


def test_exit_codes_say_whose_problem_it_was(tool, files, capsys, monkeypatch):
    def code_and_kind(*args):
        code = run_cli("score-poses", str(files / "receptor.pdb"), str(files / "poses.sdf"), *args)
        return code, json.loads(capsys.readouterr().out).get("kind")

    # 2: the request cannot be met
    assert code_and_kind("--smina-bin", tool.binary, "--json") == (2, "bad-input")
    assert code_and_kind("--smina-bin", tool.binary, "--mode", "dock", "--json") == (
        2,
        "bad-input",
    )
    # 3: the tool is not installed
    assert code_and_kind("--scorer", "smina", "--smina-bin", "/nowhere/smina", "--json") == (
        3,
        "unavailable",
    )
    # 4: the tool ran and could score nothing
    monkeypatch.setenv("FAKE_SMINA_FAIL_ON", "")
    assert code_and_kind("--smina-bin", tool.binary, "--mode", "rescore", "--json") == (4, "failed")


# ------------------------------------------------------------------- the tool
# Everything above runs against the fake smina. This is the only place a real
# scorer runs, so an installed smina or gnina is exercised by a plain pytest.


@pytest.mark.smoke
def test_a_real_scorer_rescores_a_pose_in_a_real_receptor(tmp_path, scorer):
    """Crambin and a benzene at its centre, rescored where it stands.

    ``rescore`` needs no box, so this does not depend on a detector having run.
    The assertion is that a number came back, never which number: that is the
    scorer's business and it varies with its version and build.
    """
    receptor_text = read("1CRN.pdb")
    receptor = tmp_path / "1CRN.pdb"
    receptor.write_text(receptor_text)
    poses = tmp_path / "poses.sdf"
    poses.write_text(benzene_sdf(centre_of(receptor_text)))

    run = scorer.score(str(receptor), str(poses), str(tmp_path), mode="rescore")

    assert len(run) == 1, run.meta()
    pose = run.poses[0]
    assert pose.error == "", pose.error
    assert pose.score is not None and math.isfinite(pose.score), run.meta()
    assert pose.heavy == 6, "benzene has six heavy atoms, got %r" % pose.heavy
    assert run.meta()["scored"] == 1 and run.meta()["failed"] == 0
