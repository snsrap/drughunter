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

from proteinviewer.errors import Conflict, NotFound, ToolFailed, ToolUnavailable
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


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app = Path(cls.tmp.name)
        (app / "index.html").write_text("<!doctype html><title>t</title>")
        (app / "main.js").write_text("export {};")
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

    def test_dumps_never_writes_nan(self):
        self.assertEqual(json.loads(dumps({"a": [float("inf"), 1.5]})), {"a": [None, 1.5]})


if __name__ == "__main__":
    unittest.main()
