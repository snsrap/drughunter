"""Start the viewer: build the service from settings, serve it, clean up.

The composition root -- the one module that names concrete tools. Everything
it wires together only sees interfaces: the service sees pocket tools, a
preparer and a generator; the web layer sees the service.

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

from . import __version__
from .buffer import StructureBuffer
from .config import Settings, default_project_root
from .generation import NeatPocket, Scoring
from .pocketbench import Pocketbench
from .prep import QuickPrep
from .project import ProjectStore
from .service import API_VERSION, ViewerService
from .web import LOOPBACK, Api, Handler

__all__ = ["build_service", "make_server", "main"]

log = logging.getLogger("proteinviewer")


def build_service(settings: Settings, work_root: Path) -> ViewerService:
    """Every tool the viewer drives, found and wired. The detectors themselves
    are pocketbench's: one added there is listed here with no change.
    """
    pockets = Pocketbench(
        settings.pocketbench_python,
        fpocket_bin=settings.fpocket_bin,
        lacuna_bin=settings.lacuna_bin,
    )
    return ViewerService(
        buffer=StructureBuffer(work_root / "structures", settings.buffer_limit),
        pockets=pockets,
        prep=QuickPrep(settings.quickprep_python),
        generator=NeatPocket(
            work_root / "jobs",
            neat_dir=settings.neat_dir,
            python=settings.neat_python,
            limit=settings.job_limit,
        ),
        # The scorer pocketbench found: scoring runs through the same command.
        scoring=Scoring(pockets, work_root / "scores", limit=settings.job_limit),
        projects=ProjectStore(settings.project_root or default_project_root(), api=API_VERSION),
        # What a project may not be saved into: the tree the runs write in.
        work_root=work_root,
        version=__version__,
    )


def make_server(settings: Settings, service: ViewerService) -> ThreadingHTTPServer:
    """A threaded server, so one long run does not hold up every other
    request.
    """
    handler = functools.partial(
        Handler,
        directory=str(settings.app_dir),
        api=Api(service),
        max_body=settings.max_body,
        allowed_hosts=LOOPBACK | {settings.host},
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)


def tool_status_lines(service: ViewerService) -> list[str]:
    return service.pockets.status_lines() + [
        service.generator.status_line(),
        service.prep.status_line(),
    ]


def _print_tool_status(service: ViewerService) -> None:
    for line in tool_status_lines(service):
        print(line, flush=True)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="serve.py", description="Serve the protein viewer and open it in a browser."
    )
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true", help="serve without opening a browser")
    ap.add_argument(
        "--projects",
        metavar="DIR",
        default=None,
        help="where session output directories are collected "
        "(default: ~/.local/share/proteinviewer/projects)",
    )
    return ap.parse_args(argv)


def _stop(signum, frame):
    # SIGTERM (what start.sh sends) would otherwise end the process without
    # unwinding, leaving the temp tree behind on every restart.
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overrides = {"port": args.port, "open_browser": not args.no_open}
    if args.projects:
        overrides["project_root"] = Path(args.projects).expanduser()
    settings = Settings.from_env(**overrides)
    logging.basicConfig(level=logging.INFO, format="  %(message)s", stream=sys.stderr)
    if not settings.app_dir.is_dir():
        print("no app/ directory at %s" % settings.app_dir, file=sys.stderr)
        return 1

    work_root = (
        Path(settings.work_root)
        if settings.work_root
        else Path(tempfile.mkdtemp(prefix="proteinviewer-"))
    )
    signal.signal(signal.SIGTERM, _stop)
    try:
        service = build_service(settings, work_root)
        try:
            httpd = make_server(settings, service)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print(
                    "port %d is taken -- stop whatever is on it (./start.sh "
                    "does that for a viewer) or pick another" % settings.port,
                    file=sys.stderr,
                )
                return 1
            raise
        with httpd:
            url = "http://%s:%d/" % (settings.host, settings.port)
            print("serving %s at %s  (ctrl-c to stop)" % (settings.app_dir, url))
            print("work directory: %s" % work_root)
            print("projects: %s" % service.projects.root, flush=True)
            # Probing the tools starts their interpreters (OpenMM takes a few
            # seconds to import), so it happens beside serving, not before.
            threading.Thread(
                target=_print_tool_status, args=(service,), daemon=True, name="tool-probe"
            ).start()
            if settings.open_browser:
                threading.Timer(0.4, webbrowser.open, [url]).start()
            httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        if not settings.work_root:
            shutil.rmtree(work_root, ignore_errors=True)
    return 0
