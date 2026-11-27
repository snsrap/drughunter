"""The HTTP layer over a real socket, with a fake service behind it."""

import functools
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from proteinviewer.errors import Conflict, NotFound, ToolFailed, ToolUnavailable, ViewerError
from proteinviewer.web import Api, Handler, dumps


class FakeService:
    def status(self):
        return {"api": 2, "nan": float("nan")}

    def job_status(self, q):
        raise NotFound("no such job")

    def buffer_structure(self, req):
        return {"token": req.token}

    def find_pockets(self, req):
        raise {
            "busy": ToolFailed("x failed"),
            "missing": ToolUnavailable("no x"),
            "late": Conflict("too early"),
            "bug": KeyError("oops"),
        }[req.token]

    def prepare(self, req):
        return {"ph": req.ph}

    def start_generation(self, req):
        return {"ranks": sorted(req.ranks)}

    def create_project(self, req):
        return {"id": "2026-10-18-" + (req.name or "session")}

    def save_project(self, req):
        return {
            "project": req.project,
            "path": req.path,
            "session": req.session is not None,
            "overwrite": req.overwrite,
        }

    def project_status(self, project_id):
        raise NotFound("no such project %r" % project_id)

    # -- ligand datasets
    def upload_dataset(self, req):
        return {"dataset_id": "ds-1", "state": "staged", "name": req.name}

    def resolve_dataset(self, req):
        return {"dataset_id": req.dataset_id, "config": dict(req.config)}

    def cancel_dataset(self, req):
        raise Conflict("dataset %s is already resolved" % req.dataset_id)

    def dataset_status(self, req):
        raise NotFound("no resolved dataset %s" % req.dataset_id)

    def dataset_rows(self, req):
        return {"offset": req.offset, "limit": req.limit, "depict": req.depict}

    # -- training
    def start_training(self, req):
        if req.dataset_id == "staged":
            raise Conflict("dataset staged is staged, not resolved")
        if req.dataset_id == "missing":
            raise ToolUnavailable("training is not available: no XGBoost")
        return {"id": "run-1", "seed": req.seed, "strategy": req.production_strategy}

    def training_status(self, q):
        raise NotFound("no such training run %r" % q.run_id)

    def training_run(self, q):
        raise Conflict("training run %s is running, not completed" % q.run_id)

    def cancel_training(self, q):
        return {"id": q.run_id, "state": "cancelled"}

    # -- experiments
    def register_experiment(self, req):
        if req.run_id == "run-unknown":
            raise NotFound("no such training run")
        if req.run_id == "run-busy":
            raise Conflict("training run run-busy is running, not completed")
        if req.run_id == "run-store":
            raise StoreProblem("the experiment registry could not be used")
        return {"id": "e-1", "name": req.name, "source_run_id": req.run_id}

    def experiments_view(self, q):
        if q.experiment_id:
            return {"id": q.experiment_id, "everything": q.everything}
        return {"experiments": [], "everything": q.everything}

    def update_experiment(self, req):
        return {"id": req.experiment_id, "name": req.name, "annotations": dict(req.annotations)}

    def delete_experiment(self, req):
        report = {"id": req.experiment_id, "complete": False, "row": "retained"}
        if req.experiment_id.startswith("0"):
            raise InUse("experiment is in use", report)
        raise StoreProblem("could not remove", report)

    def experiments_health(self, q):
        return {"ok": True, "deep": q.deep}

    def recover_experiments(self):
        return {"completed": [], "deleted": [], "retained": []}


class StoreProblem(ViewerError):
    """Any ViewerError the web layer has no status for: the server's own trouble."""

    def __init__(self, message, report=None):
        super().__init__(message)
        if report is not None:
            self.report = report


class InUse(Conflict):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app = Path(cls.tmp.name)
        (app / "index.html").write_text("<!doctype html><title>t</title>")
        (app / "main.js").write_text("export {};")
        (app / "ligands.html").write_text("<!doctype html><title>ligands</title>")
        handler = functools.partial(
            Handler, directory=str(app), api=Api(FakeService()), max_body=1000
        )
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        data = (
            body
            if isinstance(body, bytes)
            else (json.dumps(body).encode() if body is not None else None)
        )
        h = {"Content-Type": "application/json"} if data is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def api(self, method, path, body=None, headers=None):
        code, h, raw = self.request(method, path, body, headers)
        self.assertEqual(h["Content-Type"], "application/json")
        return code, json.loads(raw)

    def test_get_and_post_reach_the_service(self):
        self.assertEqual(self.api("GET", "/api/status"), (200, {"api": 2, "nan": None}))
        self.assertEqual(
            self.api("POST", "/api/structure", {"id": "x", "data": "ATOM"}), (200, {"token": "x"})
        )
        self.assertEqual(
            self.api("POST", "/api/generate/", {"ranks": [2, 1]}), (200, {"ranks": [1, 2]})
        )

    def test_the_project_routes(self):
        self.assertEqual(
            self.api("POST", "/api/project", {"name": "1HSG"}), (200, {"id": "2026-10-18-1HSG"})
        )
        self.assertEqual(
            self.api(
                "POST",
                "/api/project/save",
                {"project": "p1", "path": "~/out", "session": {"app": "x"}},
            ),
            (200, {"project": "p1", "path": "~/out", "session": True, "overwrite": False}),
        )
        # A save with nothing to save, and a project nobody has.
        self.assertEqual(self.api("POST", "/api/project/save", {"path": "~/out"})[0], 400)
        self.assertEqual(self.api("GET", "/api/project?id=nope")[0], 404)

    def test_the_dataset_routes(self):
        self.assertEqual(
            self.api("POST", "/api/dataset/upload", {"name": "a/b.csv", "data": "x\n1"}),
            (200, {"dataset_id": "ds-1", "state": "staged", "name": "b.csv"}),
        )
        code, body = self.api(
            "POST",
            "/api/dataset/resolve",
            {"dataset_id": "ds-1", "structure_column": "S", "target_column": ""},
        )
        self.assertEqual((code, body["config"]), (200, {"structure_column": "S", "target": None}))
        self.assertEqual(
            self.api("POST", "/api/dataset/rows", {"dataset_id": "ds-1", "limit": 100}),
            (200, {"offset": 0, "limit": 100, "depict": True}),
        )
        self.assertEqual(self.api("POST", "/api/dataset/cancel", {"dataset_id": "ds-1"})[0], 409)
        self.assertEqual(self.api("GET", "/api/dataset?id=ds-1")[0], 404)

    def test_dataset_requests_are_checked_at_the_edge(self):
        for path, body in (
            ("/api/dataset/upload", {"data": "  "}),
            (
                "/api/dataset/resolve",
                {
                    "dataset_id": "ds-1",
                    "structure_column": "S",
                    "target_column": "y",
                    "target_kind": "regression",
                },
            ),
            ("/api/dataset/rows", {"dataset_id": "ds-1", "limit": 101}),
            ("/api/dataset/cancel", {}),
        ):
            self.assertEqual(self.api("POST", path, body)[0], 400, path)
        self.assertEqual(self.api("GET", "/api/dataset")[0], 400)

    def test_the_training_routes(self):
        self.assertEqual(
            self.api("POST", "/api/training", {"dataset_id": "ds-1"}),
            (200, {"id": "run-1", "seed": 0, "strategy": "scaffold"}),
        )
        self.assertEqual(
            self.api(
                "POST",
                "/api/training",
                {"dataset_id": "ds-1", "seed": 5, "production_strategy": "random"},
            ),
            (200, {"id": "run-1", "seed": 5, "strategy": "random"}),
        )
        self.assertEqual(self.api("POST", "/api/training", {"dataset_id": "staged"})[0], 409)
        self.assertEqual(self.api("POST", "/api/training", {"dataset_id": "missing"})[0], 503)
        self.assertEqual(self.api("GET", "/api/training?id=run-9")[0], 404)
        self.assertEqual(self.api("GET", "/api/training/run?id=run-1")[0], 409)
        self.assertEqual(
            self.api("POST", "/api/training/cancel", {"id": "run-1"}),
            (200, {"id": "run-1", "state": "cancelled"}),
        )

    def test_training_requests_are_checked_at_the_edge(self):
        for body in (
            {},
            {"dataset_id": "ds-1", "seed": -1},
            {"dataset_id": "ds-1", "seed": 2**32},
            {"dataset_id": "ds-1", "production_strategy": "best"},
            {"dataset_id": "ds-1", "profile": "xl"},
        ):
            self.assertEqual(self.api("POST", "/api/training", body)[0], 400, body)
        # A number too large for a float is the client's mistake, not a server error.
        for raw in (
            b'{"dataset_id": "ds-1", "seed": 1e400}',
            b'{"dataset_id": "ds-1", "seed": 1%s}' % (b"0" * 400),
        ):
            code, body = self.api("POST", "/api/training", raw)
            self.assertEqual(code, 400, raw[:40])
            self.assertIn("seed", body["error"])
        self.assertEqual(self.api("GET", "/api/training")[0], 400)
        self.assertEqual(self.api("GET", "/api/training/run")[0], 400)
        self.assertEqual(self.api("POST", "/api/training/cancel", {})[0], 400)

    def test_the_experiment_routes(self):
        uid = "123e4567-e89b-42d3-a456-426614174000"
        self.assertEqual(
            self.api("POST", "/api/experiments", {"run_id": "run-1", "name": " n "}),
            (200, {"id": "e-1", "name": "n", "source_run_id": "run-1"}),
        )
        self.assertEqual(
            self.api("POST", "/api/experiments", {"run_id": "run-unknown", "name": "n"})[0], 404
        )
        self.assertEqual(
            self.api("POST", "/api/experiments", {"run_id": "run-busy", "name": "n"})[0], 409
        )
        # A store's own trouble is a 500 with its message, and no traceback.
        code, body = self.api("POST", "/api/experiments", {"run_id": "run-store", "name": "n"})
        self.assertEqual(code, 500)
        self.assertEqual(body, {"error": "the experiment registry could not be used"})
        self.assertEqual(
            self.api("GET", "/api/experiments"), (200, {"experiments": [], "everything": False})
        )
        self.assertEqual(
            self.api("GET", "/api/experiments?all=1"),
            (200, {"experiments": [], "everything": True}),
        )
        self.assertEqual(
            self.api("GET", "/api/experiments?id=" + uid), (200, {"id": uid, "everything": False})
        )
        self.assertEqual(
            self.api("POST", "/api/experiments/update", {"id": uid, "annotations": {"a": "b"}}),
            (200, {"id": uid, "name": None, "annotations": {"a": "b"}}),
        )
        self.assertEqual(
            self.api("GET", "/api/experiments/health?deep=0"), (200, {"ok": True, "deep": False})
        )
        self.assertEqual(self.api("POST", "/api/experiments/recover", {})[0], 200)
        self.assertEqual(self.api("GET", "/api/experiments/recover")[0], 404)

    def test_an_error_that_carries_a_report_sends_it(self):
        code, body = self.api(
            "POST", "/api/experiments/delete", {"id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}
        )
        self.assertEqual(code, 409)
        self.assertEqual(body["report"]["row"], "retained")
        code, body = self.api(
            "POST", "/api/experiments/delete", {"id": "123e4567-e89b-42d3-a456-426614174000"}
        )
        self.assertEqual((code, body["report"]["complete"]), (500, False))
        self.assertEqual(body["error"], "could not remove")

    def test_experiment_requests_are_checked_at_the_edge(self):
        uid = "123e4567-e89b-42d3-a456-426614174000"
        for path, body in (
            ("/api/experiments", {}),
            ("/api/experiments", {"run_id": "run-1"}),
            ("/api/experiments", {"run_id": "../x", "name": "n"}),
            ("/api/experiments/update", {"id": uid}),
            ("/api/experiments/update", {"id": uid, "model_family": "x"}),
            ("/api/experiments/update", {"id": "nope", "name": "n"}),
            ("/api/experiments/delete", {}),
            ("/api/experiments/delete", {"id": "../../etc"}),
        ):
            self.assertEqual(self.api("POST", path, body)[0], 400, (path, body))
        self.assertEqual(self.api("GET", "/api/experiments?id=nope")[0], 400)
        self.assertEqual(self.api("GET", "/api/experiments?all=maybe")[0], 400)

    def test_errors_map_to_statuses(self):
        for token, code in (("busy", 502), ("missing", 503), ("late", 409)):
            got, body = self.api("POST", "/api/pockets", {"token": token})
            self.assertEqual(got, code, token)
            self.assertIn("error", body)
        # A bug is the server's fault, and leaves a traceback in the log.
        with self.assertLogs("proteinviewer", "ERROR"):
            got, body = self.api("POST", "/api/pockets", {"token": "bug"})
        self.assertEqual(got, 500)
        self.assertIn("KeyError", body["error"])
        self.assertEqual(self.api("GET", "/api/job?id=x")[0], 404)
        self.assertEqual(self.api("POST", "/api/prep", {"ph": 99})[0], 400)
        self.assertEqual(self.api("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.api("POST", "/api/nothing", {})[0], 404)

    def test_bad_bodies(self):
        self.assertEqual(self.api("POST", "/api/structure", b"{nope")[0], 400)
        self.assertEqual(self.api("POST", "/api/structure", [1, 2])[0], 400)
        self.assertEqual(self.api("POST", "/api/structure", {"data": "x" * 2000})[0], 413)

    def test_cross_site_requests_are_refused(self):
        # A page on another site can send a text/plain POST without asking first.
        code, _ = self.api(
            "POST", "/api/structure", {"data": "ATOM"}, {"Content-Type": "text/plain"}
        )
        self.assertEqual(code, 415)
        code, _ = self.api(
            "POST", "/api/structure", {"data": "ATOM"}, {"Origin": "http://evil.example"}
        )
        self.assertEqual(code, 403)
        # DNS rebinding: the right address under someone else's name.
        self.assertEqual(self.api("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)
        # The page itself is fine.
        port = self.base.rsplit(":", 1)[1]
        self.assertEqual(
            self.api(
                "POST", "/api/structure", {"data": "ATOM"}, {"Origin": "http://127.0.0.1:" + port}
            )[0],
            200,
        )

    def test_static_files(self):
        code, h, body = self.request("GET", "/")
        self.assertEqual((code, h["Cache-Control"]), (200, "no-store"))
        self.assertIn(b"<title>t</title>", body)
        code, h, _ = self.request("GET", "/main.js")
        self.assertEqual(h["Content-Type"], "text/javascript")

    def test_the_ligands_page_has_a_path_of_its_own_and_one_per_dataset(self):
        for path in ("/ligands", "/ligands/", "/ligands/ds-0123456789ab", "/ligands/ds-x?y=1"):
            code, h, body = self.request("GET", path)
            self.assertEqual(code, 200, path)
            self.assertIn(b"<title>ligands</title>", body, path)
            self.assertIn("text/html", h["Content-Type"])
        code, _, body = self.request("HEAD", "/ligands/ds-0123456789ab")
        self.assertEqual(code, 200)
        # Only that one file is ever served for it, and only under that name.
        self.assertIn(b"ligands", self.request("GET", "/ligands/../main.js")[2])
        self.assertEqual(self.request("GET", "/ligandsx")[0], 404)
        self.assertEqual(self.request("GET", "/ligands.html")[0], 200)
        self.assertIn(b"<title>t</title>", self.request("GET", "/")[2])

    def test_dumps_never_writes_nan(self):
        self.assertEqual(json.loads(dumps({"a": [float("inf"), 1.5]})), {"a": [None, 1.5]})


if __name__ == "__main__":
    unittest.main()
