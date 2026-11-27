"""The dataset summary through the server: the request, the adapter, the cache.

Most of this runs without RDKit, against a fake adapter that counts how often it
is asked; one test at the end runs the real command behind the real adapter.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from proteinviewer.dataset import DatasetStore
from proteinviewer.errors import BadRequest, Conflict, NotFound, ToolUnavailable
from proteinviewer.schemas import DatasetIdRequest, DatasetSummaryRequest
from proteinviewer.service import ViewerService
from proteinviewer.web import Api, ApiRequest

CDD = Path(__file__).parent / "ligandbench" / "data" / "cdd_export_hiv1_protease.csv"
KI = "HIV-1 PR FRET (Q7K): Ki (nM)"
QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier"


class CountingLigands:
    """An adapter that answers canned summaries and counts every request.

    ``version`` is what ``describe`` says the summary version is; changing it is
    what a new ligandbench would look like to the server.
    """

    def __init__(self, version="1", delay=0.0):
        self.version = version
        self.delay = delay
        self.summarized: list[str] = []
        self.fail = None

    def describe(self):
        return {"available": True, "summary": self.version, "depiction": "1"}

    def summarize(self, dataset_dir):
        self.summarized.append(dataset_dir)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return {
            "version": self.version,
            "populations": {"source_rows": {"n": 3}},
            "x": len(self.summarized),
        }


def ready(store, content_hash="a" * 64):
    """A ready dataset on disk, made the way ``commit`` makes one."""
    staged = store.stage("d.csv", "smiles\nCCO\n")
    work = store.workdir()
    (work / "dataset.json").write_text(json.dumps({"content_hash": content_hash, "n_rows": 1}))
    (work / "rows.json").write_text(json.dumps({"rows": []}))
    source = store.source(staged["dataset_id"])[0]
    return store.commit(work, name="d.csv", source=source, staged_id=staged["dataset_id"])[
        "dataset_id"
    ]


def make(tmp_path, tool=None):
    store = DatasetStore(tmp_path)
    tool = tool or CountingLigands()
    return ViewerService(None, None, None, None, ligands=tool, datasets=store), store, tool


def ask(svc, dataset_id):
    return svc.dataset_summary(DatasetSummaryRequest(dataset_id))


# -- the request ---------------------------------------------------------------


def test_the_request_names_a_dataset_by_dataset_id_or_id():
    assert DatasetSummaryRequest.parse({"dataset_id": "ds-0123456789ab"}).dataset_id == (
        "ds-0123456789ab"
    )
    assert DatasetSummaryRequest.parse({"id": "ds-0123456789ab"}).dataset_id == "ds-0123456789ab"
    with pytest.raises(BadRequest, match="name the dataset"):
        DatasetSummaryRequest.parse({})


# -- the cache ------------------------------------------------------------------


def test_the_second_request_for_a_dataset_does_not_run_ligandbench_again(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    first = ask(svc, ds)
    second = ask(svc, ds)
    assert len(tool.summarized) == 1
    assert first == second and first["dataset_id"] == ds and first["x"] == 1
    # It was asked about the dataset's own directory: files in, JSON out.
    assert tool.summarized == [str(tmp_path / "ready" / ds)]


def test_the_answer_survives_a_restart(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    ask(svc, ds)
    again, _, tool2 = make(tmp_path)
    assert ask(again, ds)["x"] == 1 and tool2.summarized == []


def test_a_new_summary_version_asks_again_and_the_old_one_stays_kept(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    assert ask(svc, ds)["version"] == "1"
    tool.version = "2"
    assert ask(svc, ds)["version"] == "2" and len(tool.summarized) == 2
    assert ask(svc, ds)["version"] == "2" and len(tool.summarized) == 2
    tool.version = "1"
    assert ask(svc, ds)["version"] == "1" and len(tool.summarized) == 2, "version 1 is still kept"
    assert len(list((tmp_path / "ready" / ds / "summaries").glob("*.json"))) == 2


def test_each_dataset_has_its_own_summary(tmp_path):
    svc, store, tool = make(tmp_path)
    one, two = ready(store, "a" * 64), ready(store, "b" * 64)
    assert ask(svc, one)["dataset_id"] == one and ask(svc, two)["dataset_id"] == two
    assert len(tool.summarized) == 2


def test_requests_at_the_same_moment_run_ligandbench_once(tmp_path):
    svc, store, tool = make(tmp_path, CountingLigands(delay=0.2))
    ds = ready(store)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(ask(svc, ds))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(answers) == 5 and len(tool.summarized) == 1
    assert all(a == answers[0] for a in answers)


def test_a_failed_summary_is_not_kept(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    tool.fail = BadRequest("rows.json is gone")
    with pytest.raises(BadRequest, match="rows.json is gone"):
        ask(svc, ds)
    assert not list((tmp_path / "ready" / ds).glob("summaries/*"))
    tool.fail = None
    assert ask(svc, ds)["x"] == 2


def test_a_summary_file_that_cannot_be_read_is_made_again(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    ask(svc, ds)
    (path,) = (tmp_path / "ready" / ds / "summaries").glob("*.json")
    path.write_text("{half")
    assert ask(svc, ds)["x"] == 2 and len(tool.summarized) == 2


def test_the_store_keeps_a_summary_beside_the_dataset_it_belongs_to(tmp_path):
    store = DatasetStore(tmp_path)
    ds = ready(store, "c0ffee" + "0" * 58)
    assert store.cached_summary(ds, "1") is None
    store.put_summary(ds, "1", {"a": 1})
    assert store.cached_summary(ds, "1") == {"a": 1} and store.cached_summary(ds, "2") is None
    assert DatasetStore(tmp_path).cached_summary(ds, "1") == {"a": 1}
    (path,) = (tmp_path / "ready" / ds / "summaries").iterdir()
    assert path.name == "c0ffee000000-v1.json"
    assert store.directory(ds) == tmp_path / "ready" / ds
    with pytest.raises(NotFound):
        store.directory("ds-000000000000")


# -- which datasets can be summarised ----------------------------------------------


def test_a_staged_dataset_cannot_be_summarised_the_way_it_cannot_be_queried(tmp_path):
    svc, store, tool = make(tmp_path)
    staged = store.stage("s.csv", "smiles\nCCO\n")["dataset_id"]
    with pytest.raises(NotFound):
        ask(svc, staged)
    with pytest.raises(NotFound):
        ask(svc, "ds-000000000000")
    assert tool.summarized == []


def test_a_server_with_no_dataset_directory_says_so():
    svc = ViewerService(None, None, None, None)
    with pytest.raises(Conflict):
        ask(svc, "ds-000000000000")


def test_ligandbench_that_cannot_run_is_reported_not_cached(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    tool.fail = ToolUnavailable("no RDKit", "ligandbench")
    with pytest.raises(ToolUnavailable):
        ask(svc, ds)
    assert store.cached_summary(ds, "1") is None


# -- the route ------------------------------------------------------------------------


def test_the_route_answers_a_get_with_the_summary_or_the_reason_it_cannot(tmp_path):
    svc, store, tool = make(tmp_path)
    ds = ready(store)
    staged = store.stage("s.csv", "smiles\nCCO\n")["dataset_id"]
    api = Api(svc)
    code, body = api.handle("GET", "/api/dataset/summary", ApiRequest({"id": ds}, {}))
    assert code == 200 and body["dataset_id"] == ds and body["populations"]
    json.dumps(body, allow_nan=False)  # plain JSON, nothing the page has to repair
    assert api.handle("GET", "/api/dataset/summary", ApiRequest({"id": staged}, {}))[0] == 404
    assert api.handle("GET", "/api/dataset/summary", ApiRequest({}, {}))[0] == 400
    assert api.handle("POST", "/api/dataset/summary", ApiRequest({}, {"id": ds}))[0] == 404
    tool.fail = ToolUnavailable("no RDKit", "ligandbench")
    other = ready(store, "d" * 64)
    assert api.handle("GET", "/api/dataset/summary", ApiRequest({"id": other}, {}))[0] == 503


# -- the real command behind the real adapter ----------------------------------------


def test_the_real_adapter_summarises_a_dataset_and_agrees_with_its_metadata(tmp_path):
    pytest.importorskip("rdkit")
    from proteinviewer.ligandbench import Ligandbench
    from proteinviewer.schemas import DatasetResolveRequest, DatasetUploadRequest

    class Spy(Ligandbench):
        def __init__(self):
            super().__init__()
            self.summarized = 0

        def summarize(self, dataset_dir):
            self.summarized += 1
            return super().summarize(dataset_dir)

    tool = Spy()
    svc = ViewerService(None, None, None, None, ligands=tool, datasets=DatasetStore(tmp_path))
    staged = svc.upload_dataset(
        DatasetUploadRequest.parse({"name": "c.csv", "data": CDD.read_text()})
    )
    meta = svc.resolve_dataset(
        DatasetResolveRequest.parse(
            {
                "dataset_id": staged["dataset_id"],
                "structure_column": "SMILES",
                "target_column": KI,
                "target_kind": "regression",
                "qualifier_column": QUAL,
                "source_units": "nM",
                "transform": "pki",
            }
        )
    )
    assert tool.describe()["summary"], "the adapter says which summary version it runs"
    s = ask(svc, meta["dataset_id"])
    pops = s["populations"]
    assert pops["source_rows"]["n"] == meta["n_rows"] == 106
    assert pops["training_eligible"]["n"] == meta["n_training_eligible"] == 102
    assert s["target"]["state"] == "regression" and s["target"]["units"] == "pKi"
    assert s["scaffolds"]["policy"] == "murcko-v1"
    assert ask(svc, meta["dataset_id"]) == s and tool.summarized == 1
    # The staged one, by the same id, is the dataset now; a fresh upload is not.
    fresh = svc.upload_dataset(
        DatasetUploadRequest.parse({"name": "c.csv", "data": CDD.read_text()})
    )
    with pytest.raises(NotFound):
        ask(svc, fresh["dataset_id"])
    assert svc.dataset_status(DatasetIdRequest(meta["dataset_id"]))["state"] == "ready"
