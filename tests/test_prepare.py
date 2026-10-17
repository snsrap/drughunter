"""End-to-end check on 1CA2 (carbonic anhydrase II): a zinc site bound by
His94 NE2, His96 NE2 and His119 ND1 -- the case PROPKA and PDB2PQR get wrong
on their own."""

from pathlib import Path

import gemmi
import pytest

from quickprep import prepare

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("prep")
    report = prepare(str(DATA / "1CA2.pdb"), str(out / "out.pdb"),
                     pqr=str(out / "out.pqr"))
    return report, out


def hydrogens(path, num):
    st = gemmi.read_structure(str(path))
    res = next(r for r in st[0]["A"] if r.seqid.num == num)
    return {a.name for a in res if a.element.name == "H"}


def test_zinc_histidines(run):
    report, out = run
    for num in (94, 96):                       # NE2 on zinc -> H on ND1 only
        h = hydrogens(out / "out.pdb", num)
        assert "HD1" in h and "HE2" not in h, (num, h)
    h = hydrogens(out / "out.pdb", 119)        # ND1 on zinc -> H on NE2 only
    assert "HE2" in h and "HD1" not in h, h
    assert report["protonation"]["metalSites"]


def test_provenance(run):
    report, out = run
    text = (out / "out.pdb").read_text()
    assert "REMARK  99 PH         7" in text
    st = gemmi.read_structure(str(out / "out.pdb"))
    built = [a for c in st[0] for r in c for a in r
             if a.element.name != "H" and a.occ == 0]
    assert len(built) == report["output"]["builtHeavyAtoms"]


def test_pqr_charges_are_integral(run):
    report, out = run
    per_res = {}
    for line in (out / "out.pqr").read_text().splitlines():
        if line.startswith("ATOM"):
            key = line[21:27]
            per_res[key] = per_res.get(key, 0.0) + float(line[54:62])
    assert all(abs(q - round(q)) < 1e-3 for q in per_res.values())
    assert abs(report["pqr"]["netCharge"] - round(report["pqr"]["netCharge"])) < 1e-3


def test_refinement_stays_close(run):
    report, _ = run
    assert report["refinement"]["rmsdA"] < 0.5
