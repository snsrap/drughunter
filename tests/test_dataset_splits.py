"""Split generation through the server: the request, the service resolving a
dataset id, the route and its statuses, and the subprocess's failures mapped to
the errors the page is told about. The science is in tests/ligandbench/test_split.py;
here ligandbench is the real thing behind the real adapter, except where a failure
has to be made up.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("rdkit")

from proteinviewer.dataset import DatasetStore  # noqa: E402
from proteinviewer.errors import (  # noqa: E402
    BadRequest,
    Conflict,
    NotFound,
    ToolFailed,
    ToolUnavailable,
)
from proteinviewer.ligandbench import Ligandbench  # noqa: E402
from proteinviewer.schemas import (  # noqa: E402
    DatasetIdRequest,
    DatasetResolveRequest,
    DatasetSplitRequest,
    DatasetUploadRequest,
)
from proteinviewer.service import ViewerService  # noqa: E402
from proteinviewer.web import Api, ApiRequest  # noqa: E402

from .helpers import fake_executable  # noqa: E402

CDD = (Path(__file__).parent / "ligandbench" / "data" / "cdd_export_hiv1_protease.csv").read_text()
KI = "HIV-1 PR FRET (Q7K): Ki (nM)"
QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier"

RINGS = (
    "c1ccccc1",
    "c1ccncc1",
    "C1CCCCC1",
    "c1ccc2ccccc2c1",
    "c1ccsc1",
    "c1ccoc1",
    "C1CCNCC1",
    "c1ccc2[nH]ccc2c1",
    "c1ccnc2ccccc12",
    "C1CCOC1",
)


@pytest.fixture(scope="module")
def tool():
    return Ligandbench()


def make(tmp_path, tool):
    return ViewerService(None, None, None, None, ligands=tool, datasets=DatasetStore(tmp_path))


def upload(svc, text=CDD, name="cdd.csv"):
    return svc.upload_dataset(DatasetUploadRequest.parse({"name": name, "data": text}))[
        "dataset_id"
    ]


def resolve_cdd(svc, dataset_id, **choices):
    body = {
        "dataset_id": dataset_id,
        "structure_column": "SMILES",
        "target_column": KI,
        "target_kind": "regression",
        "qualifier_column": QUAL,
        "source_units": "nM",
        "transform": "pki",
        **choices,
    }
    return svc.resolve_dataset(DatasetResolveRequest.parse(body))


def resolve_labels(svc, dataset_id):
    return svc.resolve_dataset(
        DatasetResolveRequest.parse(
            {
                "dataset_id": dataset_id,
                "structure_column": "SMILES",
                "target_column": "Active",
                "target_kind": "classification",
                "qualifier_column": None,
                "positive_class": "1",
            }
        )
    )


def labelled_csv(acyclic=False):
    """A hundred labelled molecules: ten scaffolds of ten, each holding both labels,
    or (``acyclic``) a hundred chains with no ring at all.
    """
    lines = ["Name,SMILES,Active"]
    if acyclic:
        lines += ["a%d,%sO,%d" % (k, "C" * k, k % 2) for k in range(2, 102)]
    else:
        for c, ring in enumerate(RINGS):
            lines += ["m%d-%d,%s%s,%d" % (c, k, "C" * k, ring, (c + k) % 2) for k in range(1, 11)]
    return "\n".join(lines) + "\n"


def request(dataset_id, strategy="scaffold", seed=0):
    return DatasetSplitRequest.parse({"dataset_id": dataset_id, "strategy": strategy, "seed": seed})


@pytest.fixture
def ready(tmp_path, tool):
    svc = make(tmp_path, tool)
    return svc, resolve_cdd(svc, upload(svc))["dataset_id"]


# -- the request ---------------------------------------------------------------------


def test_a_split_request_names_a_dataset_a_strategy_and_a_seed():
    r = DatasetSplitRequest.parse({"dataset_id": "ds-1", "strategy": "Scaffold", "seed": 42})
    assert (r.dataset_id, r.strategy, r.seed) == ("ds-1", "scaffold", 42)
    assert DatasetSplitRequest.parse({"dataset_id": "ds-1", "strategy": "random"}).seed == 0
    assert (
        DatasetSplitRequest.parse({"dataset_id": "ds-1", "strategy": "random", "seed": "7"}).seed
        == 7
    )
    big = DatasetSplitRequest.parse({"dataset_id": "d", "strategy": "random", "seed": 2**32 - 1})
    assert big.seed == 2**32 - 1


@pytest.mark.parametrize(
    "body, message",
    [
        ({}, "name the dataset"),
        ({"dataset_id": "ds-1"}, "say which strategy: random or scaffold"),
        ({"dataset_id": "ds-1", "strategy": ""}, "say which strategy"),
        ({"dataset_id": "ds-1", "strategy": "cluster"}, "unknown strategy 'cluster'"),
        ({"dataset_id": "ds-1", "strategy": 3}, "strategy must be a string"),
        ({"dataset_id": 1, "strategy": "random"}, "dataset_id must be a string"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": -1}, "seed must be at least 0"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": 2**32}, "seed must be at most"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": 1.5}, "whole number"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": "x"}, "seed must be a number"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": True}, "seed must be a number"),
        ({"dataset_id": "ds-1", "strategy": "random", "seed": [1]}, "seed must be a number"),
    ],
)
def test_a_split_request_that_cannot_be_used_says_what_to_change(body, message):
    with pytest.raises(BadRequest, match=message):
        DatasetSplitRequest.parse(body)


# -- the service ----------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["random", "scaffold"])
def test_a_ready_dataset_is_split_by_the_real_ligandbench(ready, strategy):
    svc, dataset_id = ready
    out = svc.dataset_splits(request(dataset_id, strategy, seed=5))
    assert out["dataset_id"] == dataset_id and out["status"] == "ok"
    assert out["strategy"] == strategy and out["seed"] == 5
    assert len(out["repeats"]) == 5 and out["unavailable"] is None
    assert out["n_eligible"] == 102 and [e["row_id"] for e in out["excluded"]] == [
        102,
        103,
        104,
        105,
    ]
    meta = svc.dataset_status(DatasetIdRequest(dataset_id))
    assert out["dataset"]["content_hash"] == meta["content_hash"]
    for repeat in out["repeats"]:
        rows = repeat["train"] + repeat["validation"] + repeat["test"]
        assert sorted(rows) == out["row_ids"] and 102 not in rows
    json.dumps(out)


def test_the_same_request_gives_the_same_answer_even_after_a_restart(tmp_path, tool):
    svc = make(tmp_path, tool)
    dataset_id = resolve_cdd(svc, upload(svc))["dataset_id"]
    first = svc.dataset_splits(request(dataset_id, "scaffold", seed=3))
    again = svc.dataset_splits(request(dataset_id, "scaffold", seed=3))
    restarted = make(tmp_path, tool).dataset_splits(request(dataset_id, "scaffold", seed=3))
    other = svc.dataset_splits(request(dataset_id, "scaffold", seed=4))
    assert first == again == restarted
    key = json.dumps(first, sort_keys=True)
    assert key == json.dumps(restarted, sort_keys=True)
    assert first["repeats"] != other["repeats"]


def test_a_split_that_cannot_be_made_is_an_answer_with_the_reason_not_an_error(tmp_path, tool):
    svc = make(tmp_path, tool)
    dataset_id = upload(svc, labelled_csv(acyclic=True), "acyclic.csv")
    resolve_labels(svc, dataset_id)
    out = svc.dataset_splits(request(dataset_id, "scaffold"))
    assert out["status"] == "split_unavailable" and out["repeats"] == []
    assert out["dataset_id"] == dataset_id
    assert out["unavailable"]["largest_groups"][0]["key"] == ""
    assert out["unavailable"]["failed_constraints"]
    assert svc.dataset_splits(request(dataset_id, "random"))["status"] == "ok"


def test_a_classification_dataset_is_split_with_every_class_in_every_subset(tmp_path, tool):
    svc = make(tmp_path, tool)
    dataset_id = upload(svc, labelled_csv(), "labels.csv")
    resolve_labels(svc, dataset_id)
    for strategy in ("random", "scaffold"):
        out = svc.dataset_splits(request(dataset_id, strategy, seed=1))
        assert out["status"] == "ok" and out["dataset"]["task"] == "classification"
        for repeat in out["repeats"]:
            assert all(
                set(c) == {"1", "0"} and min(c.values()) > 0
                for c in repeat["class_counts"].values()
            )


def test_the_server_returns_what_the_command_prints_and_the_bytes_survive_the_trip(tmp_path, tool):
    svc = make(tmp_path, tool)
    dataset_id = resolve_cdd(svc, upload(svc))["dataset_id"]
    out = svc.dataset_splits(request(dataset_id, "scaffold", seed=8))
    answer = {k: v for k, v in out.items() if k != "dataset_id"}  # the server's own addition
    command = [sys.executable, "-m", "ligandbench", "split", str(tmp_path / "ready" / dataset_id)]
    proc = subprocess.run(
        [*command, "--strategy", "scaffold", "--seed", "8"], capture_output=True, text=True
    )
    canonical = json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    assert proc.returncode == 0 and proc.stdout.strip() == canonical


def test_an_unknown_dataset_is_not_found(tmp_path, tool):
    svc = make(tmp_path, tool)
    for dataset_id in ("ds-000000000000", "nope", "../x"):
        with pytest.raises(NotFound):
            svc.dataset_splits(request(dataset_id))


def test_a_staged_dataset_cannot_be_split_and_is_told_to_resolve_first(tmp_path, tool):
    svc = make(tmp_path, tool)
    staged = upload(svc)
    with pytest.raises(Conflict, match="staged.*resolve"):
        svc.dataset_splits(request(staged))


def test_a_dataset_with_no_target_cannot_be_split(tmp_path, tool):
    svc = make(tmp_path, tool)
    dataset_id = resolve_cdd(svc, upload(svc), target_column="")["dataset_id"]
    with pytest.raises(BadRequest, match="no target"):
        svc.dataset_splits(request(dataset_id))


def test_a_server_without_a_dataset_directory_refuses(tool):
    with pytest.raises(Conflict, match="no dataset directory"):
        ViewerService(None, None, None, None, ligands=tool).dataset_splits(request("ds-1"))


# -- the route ---------------------------------------------------------------------------


def post(api, body):
    return api.handle("POST", "/api/dataset/splits", ApiRequest({}, body))


def test_the_route_answers_with_the_statuses_of_its_errors(tmp_path, tool):
    svc = make(tmp_path, tool)
    api = Api(svc)
    staged = upload(svc)
    ready_id = resolve_cdd(svc, upload(svc))["dataset_id"]
    bare = resolve_cdd(svc, upload(svc), target_column="")["dataset_id"]

    code, body = post(api, {"dataset_id": ready_id, "strategy": "random", "seed": 2})
    assert code == 200 and body["status"] == "ok" and body["dataset_id"] == ready_id
    assert json.dumps(body)
    assert post(api, {"dataset_id": ready_id, "strategy": "scaffold"})[0] == 200
    assert post(api, {"dataset_id": "ds-000000000000", "strategy": "random"})[0] == 404
    assert post(api, {"dataset_id": staged, "strategy": "random"})[0] == 409
    assert post(api, {"dataset_id": bare, "strategy": "random"})[0] == 400
    assert post(api, {"dataset_id": ready_id, "strategy": "cluster"})[0] == 400
    assert post(api, {"dataset_id": ready_id, "strategy": "random", "seed": -1})[0] == 400
    assert post(api, {"strategy": "random"})[0] == 400
    code, body = post(api, {"dataset_id": ready_id})
    assert code == 400 and "strategy" in body["error"]


def test_the_route_is_post_only_and_has_its_own_path(tmp_path, tool):
    api = Api(make(tmp_path, tool))
    assert ("POST", "/api/dataset/splits") in api.routes
    assert api.handle("GET", "/api/dataset/splits", ApiRequest({}, {}))[0] == 404


# -- the subprocess's failures --------------------------------------------------------------

INFO = '{"version": "1", "rdkit": "x", "depiction": "1"}'


def fake(tmp, split_body):
    """A Python that answers ``info`` as ligandbench does and runs ``split_body``
    for ``split``, so a failure can be made up.
    """
    body = (
        "import json, sys, time\n"
        "if sys.argv[3] == 'info':\n"
        "    print(%r); raise SystemExit(0)\n" % INFO
    ) + split_body
    return fake_executable(tmp, "python", body)


def split_with(tmp_path, body, **kw):
    """The service, a ready dataset, and the adapter swapped for a made-up one."""
    real = make(tmp_path, Ligandbench())
    dataset_id = resolve_cdd(real, upload(real))["dataset_id"]
    with tempfile.TemporaryDirectory() as tmp:
        real.ligands = Ligandbench(fake(tmp, body), **kw)
        return real.dataset_splits(request(dataset_id))


def test_exit_two_is_the_users_problem_and_says_why(tmp_path):
    body = "print(json.dumps({'error': 'the seed is bad', 'kind': 'bad-input'})); sys.exit(2)\n"
    with pytest.raises(BadRequest, match="the seed is bad"):
        split_with(tmp_path, body)


def test_exit_three_is_a_tool_that_is_not_available(tmp_path):
    body = "print(json.dumps({'error': 'no rdkit', 'kind': 'unavailable'})); sys.exit(3)\n"
    with pytest.raises(ToolUnavailable, match="no rdkit"):
        split_with(tmp_path, body)


def test_any_other_failure_is_a_tool_that_failed(tmp_path):
    with pytest.raises(ToolFailed, match="exploded"):
        split_with(tmp_path, "print(json.dumps({'error': 'exploded'})); sys.exit(1)\n")
    with pytest.raises(ToolFailed, match="exit 1"):
        split_with(tmp_path, "print('Traceback: not json'); sys.exit(1)\n")
    with pytest.raises(ToolFailed, match="exit 0"):
        split_with(tmp_path, "print('not json at all')\n")


def test_a_split_that_runs_out_of_time_is_a_tool_that_failed(tmp_path):
    with pytest.raises(ToolFailed, match="timed out"):
        split_with(tmp_path, "time.sleep(30)\n", timeout=0.5)


def test_the_adapter_hands_ligandbench_the_directory_the_strategy_and_the_seed(tmp_path):
    body = "print(json.dumps({'argv': sys.argv[4:], 'status': 'ok', 'repeats': []}))\n"
    out = split_with(tmp_path, body)
    directory = Path(out["argv"][0])
    assert (directory / "rows.json").is_file() and (directory / "dataset.json").is_file()
    assert out["argv"][1:] == ["--strategy", "scaffold", "--seed", "0"]
    assert out["dataset_id"] == directory.name


def test_a_split_without_ligandbench_is_a_tool_that_is_not_available(tmp_path):
    real = make(tmp_path, Ligandbench())
    dataset_id = resolve_cdd(real, upload(real))["dataset_id"]
    with tempfile.TemporaryDirectory() as tmp:
        real.ligands = Ligandbench(fake_executable(tmp, "python", "raise SystemExit(1)\n"))
        with pytest.raises(ToolUnavailable, match="needs RDKit"):
            real.dataset_splits(request(dataset_id))
