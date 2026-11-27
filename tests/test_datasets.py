"""Ligand datasets through the service, with the real ligandbench behind it:
upload, resolve, reopen after a restart, and the page of rows a browser asks for.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from proteinviewer.dataset import DatasetStore  # noqa: E402
from proteinviewer.errors import BadRequest, Conflict, NotFound  # noqa: E402
from proteinviewer.ligandbench import Ligandbench  # noqa: E402
from proteinviewer.schemas import (  # noqa: E402
    DatasetIdRequest,
    DatasetResolveRequest,
    DatasetRowsRequest,
    DatasetUploadRequest,
)
from proteinviewer.service import ViewerService  # noqa: E402

CDD = (Path(__file__).parent / "ligandbench" / "data" / "cdd_export_hiv1_protease.csv").read_text()
KI = "HIV-1 PR FRET (Q7K): Ki (nM)"
QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier"


class Spy(Ligandbench):
    """The real adapter, counting what it is asked to draw."""

    def __init__(self):
        super().__init__()
        self.drawn: list[int] = []

    def depict(self, smiles):
        self.drawn.append(len(smiles))
        return super().depict(smiles)


@pytest.fixture(scope="module")
def tool():
    return Spy()


def make(tmp_path, tool):
    return ViewerService(None, None, None, None, ligands=tool, datasets=DatasetStore(tmp_path))


def upload(svc, text=CDD, name="cdd.csv"):
    return svc.upload_dataset(DatasetUploadRequest.parse({"name": name, "data": text}))


def resolve(svc, dataset_id, **choices):
    body = {
        "dataset_id": dataset_id,
        "structure_column": "SMILES",
        "target_column": KI,
        "target_kind": "regression",
        "qualifier_column": QUAL,
        "source_units": "nM",
        "transform": "pki",
    }
    body.update(choices)
    return svc.resolve_dataset(DatasetResolveRequest.parse(body))


def rows(svc, dataset_id, **q):
    return svc.dataset_rows(DatasetRowsRequest.parse({"dataset_id": dataset_id, **q}))


# -- upload and resolve ---------------------------------------------------------


def test_an_upload_is_staged_with_its_columns_and_candidates(tmp_path, tool):
    staged = upload(make(tmp_path, tool))
    assert staged["state"] == "staged" and staged["name"] == "cdd.csv"
    assert staged["n_rows"] == 106 and staged["structure_candidates"] == ["SMILES"]
    assert [c["name"] for c in staged["columns"]][:4] == [
        "Molecule Name",
        "CDD Number",
        "Batch Name",
        "SMILES",
    ]
    assert "rows" not in staged and "content_hash" not in staged


def test_a_staged_dataset_cannot_be_queried_or_trained_on(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)
    with pytest.raises(NotFound):
        rows(svc, staged["dataset_id"])
    # ...but it is still there by id, with its columns, to be picked up where it was left
    again = svc.dataset_status(DatasetIdRequest(staged["dataset_id"]))
    assert again == {k: staged[k] for k in again} and again["state"] == "staged"
    assert again["columns"] == staged["columns"] and again["structure_candidates"] == ["SMILES"]


def test_a_file_that_is_not_csv_is_refused_and_not_kept(tmp_path, tool):
    svc = make(tmp_path, tool)
    with pytest.raises(BadRequest, match="record 1 has 1 fields"):
        upload(svc, "a,b\nonly-one\n")
    assert not list((tmp_path / "staged").iterdir())


def test_resolving_the_cdd_export_makes_a_ready_dataset(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)
    ready = resolve(svc, staged["dataset_id"])
    assert ready["dataset_id"] == staged["dataset_id"] and ready["state"] == "ready"
    assert ready["n_rows"] == 106 and ready["n_parse_failures"] == 0
    assert ready["n_training_eligible"] == 102 and ready["exclusions"] == {"censored": 4}
    assert ready["structure_column"] == "SMILES" and ready["target_column"] == KI
    assert ready["target_kind"] == "regression" and len(ready["content_hash"]) == 64
    assert ready["target"]["output_units"] == "pKi" and ready["identity"]["rdkit"]
    keys = {c["key"]: c["type"] for c in ready["schema"]}
    assert keys["c4"] == "number" and keys["target"] == "number" and keys["smiles"] == "text"
    assert ready["operators"]["number"][-1] == "missing"


def test_choices_that_cannot_be_used_leave_the_upload_to_try_again(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)["dataset_id"]
    with pytest.raises(BadRequest, match="none of the 106 values in column 'Project'"):
        resolve(svc, staged, structure_column="Project")
    with pytest.raises(BadRequest, match="confirm that .* is in nM"):
        resolve(svc, staged, source_units="uM")
    assert not any((tmp_path / "ready").glob("*")), "nothing half-resolved is kept"
    assert resolve(svc, staged)["state"] == "ready", "the same upload, resolved properly"


def test_cancel_removes_a_staged_upload_and_only_that(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)["dataset_id"]
    assert svc.cancel_dataset(DatasetIdRequest(staged)) == {
        "dataset_id": staged,
        "state": "cancelled",
    }
    with pytest.raises(NotFound):
        resolve(svc, staged)
    ready = resolve(svc, upload(svc)["dataset_id"])["dataset_id"]
    with pytest.raises(Conflict):
        svc.cancel_dataset(DatasetIdRequest(ready))


def test_a_ready_dataset_reopens_after_a_restart_identical(tmp_path, tool):
    first = make(tmp_path, tool)
    ready = resolve(first, upload(first)["dataset_id"])
    before = rows(first, ready["dataset_id"], limit=100)

    restarted = make(tmp_path, tool)  # a new store on the same directory
    again = restarted.dataset_status(DatasetIdRequest(ready["dataset_id"]))
    assert again["content_hash"] == ready["content_hash"]
    after = rows(restarted, ready["dataset_id"], limit=100)
    assert [r["row_id"] for r in after["rows"]] == [r["row_id"] for r in before["rows"]]
    assert [r["cells"] for r in after["rows"]] == [r["cells"] for r in before["rows"]]
    source = (tmp_path / "ready" / ready["dataset_id"] / "source.csv").read_text()
    assert source == CDD


def test_resolving_a_ready_dataset_again_makes_a_new_dataset(tmp_path, tool):
    svc = make(tmp_path, tool)
    first = resolve(svc, upload(svc)["dataset_id"])
    second = resolve(svc, first["dataset_id"], transform="identity", source_units="nM")
    assert second["dataset_id"] != first["dataset_id"]
    assert second["content_hash"] != first["content_hash"]
    assert svc.dataset_status(DatasetIdRequest(first["dataset_id"]))["target"]["transform"] == "pki"


def test_a_dataset_without_a_target_is_for_prediction_only(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)["dataset_id"]
    ready = resolve(svc, staged, target_column="")
    assert ready["trainable"] is False and ready["target_column"] is None
    assert ready["n_training_eligible"] == 0 and ready["n_prediction_eligible"] == 106
    assert "target" not in {c["key"] for c in ready["schema"]}
    page = rows(svc, staged, limit=2)
    assert page["rows"][0]["exclusions"] == ["no_target"] and page["rows"][0]["svg"]


# -- the page of rows ---------------------------------------------------------------


@pytest.fixture
def ready(tmp_path, tool):
    svc = make(tmp_path, tool)
    meta = resolve(svc, upload(svc)["dataset_id"])
    tool.drawn.clear()
    return svc, meta["dataset_id"], tool


def test_a_page_carries_everything_a_card_and_its_detail_need(ready):
    svc, dataset_id, _ = ready
    page = rows(svc, dataset_id, limit=3)
    assert page["total"] == 106 and page["offset"] == 0 and page["limit"] == 3
    r = page["rows"][0]
    assert r["row_id"] == 0 and r["record"] == 1 and r["label"] == "DH-0001"
    assert r["cells"][0] == "DH-0001" and len(r["cells"]) == 8
    assert r["smiles"] and r["svg"].startswith("<svg") and "currentColor" in r["svg"]
    assert r["target"] == pytest.approx(12.0969, abs=1e-3) and r["training_eligible"] is True
    assert json.dumps(page)


def test_the_default_page_is_fifty_and_a_hundred_is_the_most(ready):
    svc, dataset_id, _ = ready
    assert len(rows(svc, dataset_id)["rows"]) == 50
    assert len(rows(svc, dataset_id, limit=100)["rows"]) == 100
    with pytest.raises(BadRequest, match="between 1 and 100"):
        rows(svc, dataset_id, limit=101)


def test_sorting_and_filtering_happen_before_the_page_is_cut(ready):
    svc, dataset_id, _ = ready
    page = rows(
        svc,
        dataset_id,
        sort={"key": "target", "dir": "desc"},
        filters=[{"key": "eligible", "op": "eq", "value": "yes"}],
        limit=5,
    )
    assert page["total"] == 102
    targets = [r["target"] for r in page["rows"]]
    assert targets == sorted(targets, reverse=True)
    second = rows(svc, dataset_id, sort={"key": "target", "dir": "desc"}, offset=5, limit=5)
    assert second["rows"][0]["target"] <= targets[-1]
    censored = rows(svc, dataset_id, filters=[{"key": "target", "op": "missing"}], limit=10)
    assert censored["total"] == 4
    assert {r["row_id"] for r in censored["rows"]} == {102, 103, 104, 105}
    assert all(r["target_source"].startswith(">") for r in censored["rows"])


def test_sorting_never_changes_which_row_is_which(ready):
    svc, dataset_id, _ = ready
    natural = {
        r["row_id"]: r
        for offset in (0, 100)
        for r in rows(svc, dataset_id, offset=offset, limit=100)["rows"]
    }
    by_name = rows(svc, dataset_id, sort={"key": "c0", "dir": "desc"}, limit=100)["rows"]
    assert [r["row_id"] for r in by_name][:2] == [105, 104]
    for r in by_name:
        assert r["cells"] == natural[r["row_id"]]["cells"]
        assert r["record"] == r["row_id"] + 1


def test_bad_queries_are_refused_with_what_is_wrong(ready):
    svc, dataset_id, _ = ready
    with pytest.raises(BadRequest, match="unknown column"):
        rows(svc, dataset_id, sort={"key": "nope"})
    with pytest.raises(BadRequest, match="cannot be filtered"):
        rows(svc, dataset_id, filters=[{"key": "smiles", "op": "gt", "value": "C"}])


def test_only_the_rows_on_a_page_are_drawn_and_a_drawing_is_made_once(ready):
    svc, dataset_id, tool = ready
    rows(svc, dataset_id, limit=7)
    assert tool.drawn == [7]
    rows(svc, dataset_id, limit=7)
    assert tool.drawn == [7], "the second request came from the cache"
    rows(svc, dataset_id, limit=10)
    assert tool.drawn == [7, 3], "only the rows not yet drawn"
    assert "svg" not in rows(svc, dataset_id, limit=5, depict=False)["rows"][0]


def test_failed_structures_are_placeholders_with_the_reason(tmp_path, tool):
    svc = make(tmp_path, tool)
    text = "Name,SMILES,y\nok,CCO,1\nbad,not-a-smiles,2\n,,3\nok2,CC,4\n"
    staged = upload(svc, text)["dataset_id"]
    resolve(
        svc,
        staged,
        structure_column="SMILES",
        target_column="y",
        qualifier_column=None,
        source_units="",
        transform="identity",
    )
    page = rows(svc, staged)
    bad, blank = page["rows"][1], page["rows"][2]
    assert (
        bad["smiles"] is None
        and bad["svg"] == ""
        and bad["structure_error"] == "not a valid SMILES"
    )
    assert bad["exclusions"] == ["invalid_structure"] and bad["label"] == "bad"
    assert blank["label"] == "3", "no name: the record number"
    assert page["total"] == 4
    ready = svc.dataset_status(DatasetIdRequest(staged))
    assert ready["n_parse_failures"] == 2
    assert [f["record"] for f in ready["parse_failures"]] == [2, 3]


# -- the size the browser must cope with --------------------------------------------------


def synthetic(n):
    lines = ["Name,SMILES,Activity"]
    side = int(n**0.5) + 1
    made = 0
    for a in range(1, side + 1):
        for b in range(1, side + 1):
            if made == n:
                break
            lines.append("m%d,%sN%sO,%d" % (made, "C" * a, "C" * b, made % 97))
            made += 1
    return "\n".join(lines) + "\n"


def test_ten_thousand_rows_are_paged_and_only_a_page_is_ever_drawn(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc, synthetic(10_000), "ten-thousand.csv")["dataset_id"]
    meta = resolve(
        svc,
        staged,
        structure_column="SMILES",
        target_column="Activity",
        qualifier_column=None,
        source_units="",
        transform="identity",
    )
    assert meta["n_rows"] == 10_000 and meta["n_parse_failures"] == 0
    tool.drawn.clear()
    first = rows(svc, staged)
    assert len(first["rows"]) <= 100 and first["total"] == 10_000
    assert tool.drawn == [50]
    rows(svc, staged, offset=9_950, limit=100, sort={"key": "target", "dir": "desc"})
    assert max(tool.drawn) <= 100 and sum(tool.drawn) <= 150
