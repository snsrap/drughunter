"""HTTP: static files for the page in ``app/``, and the JSON API over the
service.

The only layer that knows about HTTP. Requests are parsed into the types in
``schemas.py``, handed to :class:`~proteinviewer.service.ViewerService`, and
whatever comes back -- a dict, or one of the errors in ``errors.py`` -- is
written as JSON with the status the error's class calls for.

Every API request must come from the page itself. The server writes into a
temp tree -- and, for ``/api/project/save``, wherever the user it belongs to
asked -- on behalf of whoever asks, so a page on another site must not be
able to ask: POSTs must be JSON (a cross-site "simple" request cannot be, so
the browser has to ask first, and is refused), must not name another origin,
and the Host must be a loopback name (which defeats DNS rebinding).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import (BadRequest, Conflict, NotFound, PayloadTooLarge,
                     ToolFailed, ToolUnavailable, ViewerError)
from .schemas import (BufferRequest, GenerateRequest, JobQuery, PocketRequest,
                      PrepRequest, ProjectRequest, ProjectSaveRequest)
from .service import ViewerService

__all__ = ["Api", "ApiRequest", "Handler", "status_for"]

log = logging.getLogger("proteinviewer")

# Most specific first: PayloadTooLarge is a BadRequest.
STATUS: tuple[tuple[type[ViewerError], int], ...] = (
    (PayloadTooLarge, 413),
    (BadRequest, 400),
    (NotFound, 404),
    (Conflict, 409),
    (ToolUnavailable, 503),
    (ToolFailed, 502),
)

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def status_for(exc: ViewerError) -> int:
    for cls, code in STATUS:
        if isinstance(exc, cls):
            return code
    return 500


@dataclass(frozen=True)
class ApiRequest:
    query: dict[str, str]
    body: dict[str, Any]


Route = Callable[[ApiRequest], dict]


class Api:
    """The routing table: (method, path) to a use case."""

    def __init__(self, service: ViewerService):
        s = service
        self.routes: dict[tuple[str, str], Route] = {
            ("GET", "/api/status"): lambda r: s.status(),
            ("GET", "/api/job"): lambda r: s.job_status(JobQuery.parse(r.query)),
            ("POST", "/api/structure"): lambda r: s.buffer_structure(BufferRequest.parse(r.body)),
            ("POST", "/api/pockets"): lambda r: s.find_pockets(PocketRequest.parse(r.body)),
            ("POST", "/api/prep"): lambda r: s.prepare(PrepRequest.parse(r.body)),
            ("POST", "/api/generate"): lambda r: s.start_generation(GenerateRequest.parse(r.body)),
            ("GET", "/api/project"): lambda r: s.project_status(r.query.get("id", "")),
            ("POST", "/api/project"): lambda r: s.create_project(ProjectRequest.parse(r.body)),
            ("POST", "/api/project/save"): lambda r: s.save_project(ProjectSaveRequest.parse(r.body)),
        }

    def handle(self, method: str, path: str, req: ApiRequest) -> tuple[int, dict]:
        route = self.routes.get((method, path))
        if route is None:
            return 404, {"error": "no such endpoint"}
        try:
            return 200, route(req)
        except ViewerError as exc:
            return status_for(exc), {"error": str(exc)}
        except Exception as exc:  # a bug, not the client's fault
            log.exception("api error on %s %s", method, path)
            return 500, {"error": "internal error: %s: %s" % (type(exc).__name__, exc)}


class Handler(SimpleHTTPRequestHandler):
    """Static files from ``directory``, and ``/api/*`` through :class:`Api`."""

    # Module scripts are refused unless served with a JavaScript type, so do
    # not leave it to whatever the system's mime.types says.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".mjs": "text/javascript",
                      ".css": "text/css", ".json": "application/json"}

    def __init__(self, *args, api: Api, max_body: int,
                 allowed_hosts: frozenset[str] = LOOPBACK, **kwargs):
        # Set before super().__init__, which handles the request.
        self.api = api
        self.max_body = max_body
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)

    def end_headers(self):
        # The page and its modules change while iterating on them; never let
        # the browser serve a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    # -- dispatch -----------------------------------------------------------
    def do_GET(self):
        path, query = self._split()
        if not path.startswith("/api/"):
            return super().do_GET()
        refused = self._refuse()
        if refused:
            return self._json(*refused)
        self._json(*self.api.handle("GET", path, ApiRequest(query, {})))

    def do_POST(self):
        path, query = self._split()
        if ("POST", path) not in self.api.routes:
            return self._json(404, {"error": "no such endpoint"})
        refused = self._refuse(post=True)
        if refused:
            return self._json(*refused)
        try:
            body = self._body()
        except ViewerError as exc:
            self.close_connection = True  # the body may be unread
            return self._json(status_for(exc), {"error": str(exc)})
        self._json(*self.api.handle("POST", path, ApiRequest(query, body)))

    # -- helpers ------------------------------------------------------------
    def _split(self) -> tuple[str, dict[str, str]]:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        return parts.path.rstrip("/") or "/", query

    def _refuse(self, post: bool = False) -> tuple[int, dict] | None:
        host = urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        if host not in self.allowed_hosts:
            return 403, {"error": "refused: unexpected Host %r" % host}
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return 403, {"error": "refused: cross-origin request from %s" % origin}
        if post:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                return 415, {"error": "send the request body as application/json"}
        return None

    def _body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("invalid Content-Length") from None
        if n <= 0:
            return {}
        if n > self.max_body:
            raise PayloadTooLarge("payload too large (%.1f MB)" % (n / 1e6))
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except ValueError as exc:
            raise BadRequest("invalid JSON: %s" % exc) from None
        if not isinstance(body, dict):
            raise BadRequest("the request body must be a JSON object")
        return body

    def _json(self, code: int, payload: Any) -> None:
        body = dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def dumps(payload: Any) -> str:
    """JSON the browser can parse. ``json.dumps`` writes NaN and infinity as
    bare words, which ``JSON.parse`` rejects; they go out as null."""
    try:
        return json.dumps(payload, allow_nan=False)
    except ValueError:
        return json.dumps(_finite(payload), allow_nan=False)


def _finite(v: Any) -> Any:
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v
