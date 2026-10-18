"""Start the viewer: build the service from settings, serve it, clean up.

The composition root -- the one module that names concrete tools. Everything
it wires together only sees interfaces: the service sees a detector registry,
a preparer and a generator; the web layer sees the service.

    ./serve.py            # port 8000, opens a browser
    ./serve.py 9000       # pick a port
    ./serve.py --no-open  # just serve
"""

from __future__ import annotations

import argparse
import errno
import functools
import logging
import shutil
import signal
import sys
import tempfile
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from .buffer import StructureBuffer
from .config import Settings
from .detectors import DetectorRegistry
from .detectors.fpocket import FpocketDetector
from .detectors.lacuna import LacunaDetector
from .generation import NeatPocket
from .prep import QuickPrep
from .service import ViewerService
from .web import LOOPBACK, Api, Handler

__all__ = ["build_service", "make_server", "main"]

log = logging.getLogger("proteinviewer")


def build_service(settings: Settings, work_root: Path) -> ViewerService:
    """Every tool the viewer drives, found and wired. Registering a detector
    here is all it takes to offer it."""
    detectors = DetectorRegistry([
        FpocketDetector(settings.fpocket_bin),
        LacunaDetector(settings.lacuna_bin),
    ])
    return ViewerService(
        buffer=StructureBuffer(work_root / "structures", settings.buffer_limit),
        detectors=detectors,
        prep=QuickPrep(settings.quickprep_python),
        generator=NeatPocket(work_root / "jobs", neat_dir=settings.neat_dir,
                             python=settings.neat_python, limit=settings.job_limit),
    )


def make_server(settings: Settings, service: ViewerService) -> ThreadingHTTPServer:
    """A threaded server, so one long run does not hold up every other
    request."""
    handler = functools.partial(
        Handler, directory=str(settings.app_dir), api=Api(service),
        max_body=settings.max_body, allowed_hosts=LOOPBACK | {settings.host})
    return ThreadingHTTPServer((settings.host, settings.port), handler)


def tool_status_lines(service: ViewerService) -> list[str]:
    return ([d.status_line() for d in service.detectors]
            + [service.generator.status_line(), service.prep.status_line()])


def _print_tool_status(service: ViewerService) -> None:
    for line in tool_status_lines(service):
        print(line, flush=True)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="serve.py", description="Serve the "
                                 "protein viewer and open it in a browser.")
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true",
                    help="serve without opening a browser")
    return ap.parse_args(argv)


def _stop(signum, frame):
    # SIGTERM (what start.sh sends) would otherwise end the process without
    # unwinding, leaving the temp tree behind on every restart.
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env(port=args.port, open_browser=not args.no_open)
    logging.basicConfig(level=logging.INFO, format="  %(message)s", stream=sys.stderr)
    if not settings.app_dir.is_dir():
        print("no app/ directory at %s" % settings.app_dir, file=sys.stderr)
        return 1

    work_root = (Path(settings.work_root) if settings.work_root
                 else Path(tempfile.mkdtemp(prefix="proteinviewer-")))
    signal.signal(signal.SIGTERM, _stop)
    try:
        service = build_service(settings, work_root)
        try:
            httpd = make_server(settings, service)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print("port %d is taken -- stop whatever is on it (./start.sh "
                      "does that for a viewer) or pick another" % settings.port,
                      file=sys.stderr)
                return 1
            raise
        with httpd:
            url = "http://%s:%d/" % (settings.host, settings.port)
            print("serving %s at %s  (ctrl-c to stop)" % (settings.app_dir, url))
            print("work directory: %s" % work_root, flush=True)
            # Probing the tools starts their interpreters (OpenMM takes a few
            # seconds to import), so it happens beside serving, not before.
            threading.Thread(target=_print_tool_status, args=(service,),
                             daemon=True, name="tool-probe").start()
            if settings.open_browser:
                threading.Timer(0.4, webbrowser.open, [url]).start()
            httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        if not settings.work_root:
            shutil.rmtree(work_root, ignore_errors=True)
    return 0
