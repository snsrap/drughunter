#!/usr/bin/env python3
"""Serve the viewer in app/ over HTTP and open it in a browser.

Pockets, preparation and generation go through the API below, so open the page
from here rather than as a file. 3Dmol.js and the fonts come from CDNs, so the
first load needs network access.

    ./serve.py            # port 8000, opens a browser
    ./serve.py 9000       # pick a port
    ./serve.py --no-open  # just serve

Pocket detection
----------------
fpocket is a command line program, so the browser cannot run it. Whatever
structure the viewer is displaying is posted here once and buffered in a temp
directory; pocket runs then read the structure from that location. See
pockets.py.

    GET  /api/status     which tools are available?
    POST /api/structure  buffer the displayed structure -> {token}
    POST /api/pockets    detect pockets in a buffered structure
    POST /api/prep       QuickPrep-style preparation of a buffered structure

Ensemble pocket detection
-------------------------
``POST /api/pockets`` with ``{"method": "lacuna"}`` detects over a
conformational ensemble instead of the structure as deposited, which is what
it takes to see a pocket that is closed in the unbound form. lacuna lives in
its own environment -- it needs scipy, and its MD backend needs OpenMM -- and
is run as a command: LACUNA_BIN, else the micromamba env "lacuna-gpu", else
the pyenv virtualenv "lacuna". See lacuna.py.

Structure preparation
---------------------
Preparation is the separate quickprep package (../quickprep). It needs
OpenMM, PDBFixer and PDB2PQR, so it is installed in its own environment and
run there as ``python -m quickprep``: QUICKPREP_PYTHON, else the pyenv
virtualenv "quickprep".
"""

import atexit
import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import GenerationError, Runner  # noqa: E402
from lacuna import Lacuna, LacunaError  # noqa: E402
from pockets import Fpocket, FpocketError, StructureBuffer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "app")
MAX_BODY = 64 * 1024 * 1024  # deposited mmCIF entries get large

BUFFER = StructureBuffer()
FPOCKET = Fpocket()
LACUNA = Lacuna()
RUNNER = Runner()
_LOCK = threading.Lock()


class PrepError(RuntimeError):
    """The quickprep package is not reachable, or it failed."""


class QuickPrep:
    """Runs the quickprep package in the interpreter it is installed in."""

    def __init__(self):
        cands = [os.environ.get("QUICKPREP_PYTHON"),
                 os.path.expanduser("~/.pyenv/versions/quickprep/bin/python")]
        self.python = next((p for p in cands if p and os.access(p, os.X_OK)), None)
        self._version = None

    @property
    def version(self):
        """quickprep's version in that interpreter, or "" if it does not import.
        Asked once; importing OpenMM takes a moment."""
        if self._version is None:
            self._version = ""
            if self.python:
                try:
                    proc = subprocess.run(
                        [self.python, "-c",
                         "import quickprep; print(quickprep.__version__)"],
                        capture_output=True, text=True, timeout=120)
                    if proc.returncode == 0:
                        self._version = proc.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return self._version

    @property
    def available(self):
        return bool(self.version)

    def run(self, path, workdir, ph=7.0, name=None, no_rebuild=None):
        if not self.available:
            raise PrepError("the quickprep package is not installed. Install it "
                            "into its environment: ~/.pyenv/versions/quickprep/"
                            "bin/pip install -e ../quickprep (or set "
                            "QUICKPREP_PYTHON).")
        out = os.path.join(workdir, "prepared.pdb")
        report = os.path.join(workdir, "prepared.json")
        # Terminal tails are not rebuilt from the GUI: PDBFixer's tails are
        # the one part of the build that comes out distorted at times.
        cmd = [self.python, "-m", "quickprep", path, "-o", out, "--report", report,
               "--ph", "%g" % ph, "--max-tail", "0", "--name", name or "structure"]
        # Residues the viewer removed or left out of the selection: SEQRES
        # still lists them, and they must not come back as built loops.
        for chain, nums in sorted((no_rebuild or {}).items()):
            cmd += ["--no-rebuild", "%s:%s" % (chain, _ranges(nums))]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not os.path.exists(out):
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            raise PrepError("preparation failed: " + " ".join(tail)[-500:])
        with open(out) as fh, open(report) as rh:
            return {"data": fh.read(), "report": json.load(rh),
                    "summary": proc.stdout}


def _ranges(nums):
    """[5, 6, 7, 8, 12] -> "5-8,12"."""
    nums = sorted({int(n) for n in nums})
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else "%d-%d" % (nums[i], nums[j]))
        i = j + 1
    return ",".join(out)


PREP = QuickPrep()
atexit.register(BUFFER.close)


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # The data files are large and regenerated often; never let the browser
        # serve a stale copy while iterating on them.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- api ----------------------------------------------------------------
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("payload too large (%.1f MB)" % (n / 1e6))
        return json.loads(self.rfile.read(n).decode("utf-8", "replace"))

    def do_GET(self):
        route = self.path.split("?", 1)[0].rstrip("/")
        if route == "/api/status":
            return self._json(200, {"fpocket": FPOCKET.available,
                                    "binary": FPOCKET.binary or "",
                                    "neat": RUNNER.describe(),
                                    "prep": {"available": PREP.available,
                                             "version": PREP.version,
                                             "python": PREP.python or ""},
                                    "lacuna": {"available": LACUNA.available,
                                               "version": LACUNA.version,
                                               "binary": LACUNA.binary or "",
                                               "backends": list(LACUNA.backends)}})
        if route == "/api/job":
            return self._json(200, self._job_status())
        return super().do_GET()

    def _job_status(self):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        job = RUNNER.get((q.get("id") or [""])[0])
        if job is None:
            return {"error": "no such job"}
        out = job.progress()
        # Molecules are only sent once they exist, and only when asked for.
        if (q.get("mols") or [""])[0] == "1":
            out["molecules"] = job.molecules()
        if (q.get("table") or [""])[0] == "1":
            out["table"] = job.table()
        return out

    def do_POST(self):
        route = self.path.split("?", 1)[0].rstrip("/")
        try:
            payload = self._body()
        except Exception as exc:
            return self._json(400, {"error": str(exc)})

        try:
            if route == "/api/structure":
                return self._json(200, self._buffer_structure(payload))
            if route == "/api/pockets":
                return self._json(200, self._find_pockets(payload))
            if route == "/api/generate":
                return self._json(200, self._generate(payload))
            if route == "/api/prep":
                return self._json(200, self._prepare(payload))
        except (FpocketError, LacunaError, GenerationError, PrepError) as exc:
            return self._json(501, {"error": str(exc)})
        except KeyError as exc:
            return self._json(404, {"error": "unknown structure %s" % exc})
        except Exception as exc:
            self.log_message("api error: %s: %s", type(exc).__name__, exc)
            return self._json(400, {"error": "%s: %s" % (type(exc).__name__, exc)})
        return self._json(404, {"error": "no such endpoint"})

    def _buffer_structure(self, payload):
        """Write the structure the viewer is showing into the temp buffer."""
        text = payload.get("data") or ""
        if not text.strip():
            raise ValueError("no structure data supplied")
        token = payload.get("id") or payload.get("token") or "structure"
        with _LOCK:
            rec = BUFFER.put(token, text, payload.get("fmt"))
        st = rec["structure"]
        return {"token": rec["token"], "nAtoms": len(st),
                "chains": st.chains(), "path": rec["path"]}

    def _prepare(self, payload):
        """Prepare a buffered structure; returns the prepared PDB text. Runs
        outside the lock -- it takes seconds to tens of seconds."""
        token = payload.get("token") or ""
        with _LOCK:
            rec = BUFFER.get(token)
        if rec is None:
            raise KeyError(token)
        no_rebuild = payload.get("noRebuild") or {}
        if not isinstance(no_rebuild, dict):
            raise ValueError("noRebuild must map chain ids to residue numbers")
        out = PREP.run(rec["path"], rec["workdir"], float(payload.get("ph", 7.0)),
                       name=rec["token"], no_rebuild=no_rebuild)
        self.log_message("quickprep %s: %ss", token, out["report"].get("seconds"))
        return out

    def _find_pockets(self, payload):
        """Run the chosen detector against the buffered copy, never against
        posted text.

        ``method`` picks between fpocket on the structure as it stands and
        lacuna over a conformational ensemble built from it; see lacuna.py for
        why the second one finds sites the first cannot.

        fpocket returns in seconds and runs under the lock. lacuna takes
        seconds with the nma backend and up to a minute with openmm, so it
        runs outside the lock the way preparation does -- the server is
        threaded, and holding the lock that long would stall every other
        request, including the status poll.
        """
        token = payload.get("token") or ""
        with _LOCK:
            rec = BUFFER.get(token)
        if rec is None:
            raise KeyError(token)

        method = str(payload.get("method") or "fpocket").lower()
        if method not in ("fpocket", "lacuna"):
            raise ValueError("unknown method %r (want fpocket or lacuna)" % method)
        cutoff = float(payload.get("cutoff") or 6.0)
        ligand = frozenset(
            str(x).strip().upper()
            for x in (payload.get("ligand") or []) if str(x).strip()
        )
        backend = str(payload.get("backend") or "nma").lower()
        detector = str(payload.get("detector") or "alpha").lower()
        conformers = int(payload.get("conformers") or 20)

        # The ensemble settings are part of the identity of a result: the same
        # structure at a different backend is a different run, not a cache hit.
        cache_key = ((method, round(cutoff, 3), tuple(sorted(ligand)))
                     + ((backend, detector, conformers)
                        if method == "lacuna" else ()))
        if rec.get("pockets") and rec["pockets"][0] == cache_key:
            return {"token": token, "cached": True,
                    "pockets": rec["pockets"][1], "meta": rec["pockets"][2]}

        if method == "lacuna":
            result = LACUNA.run(rec["structure"], rec["workdir"],
                                cutoff=cutoff, ligand_resnames=ligand,
                                backend=backend, detector=detector,
                                conformers=conformers)
        else:
            with _LOCK:
                result = FPOCKET.run(rec["structure"], rec["workdir"],
                                     cutoff=cutoff, ligand_resnames=ligand)
        out = [p.as_dict() for p in result]
        meta = result.meta()
        meta["ligandRemoved"] = sorted(ligand)
        meta["totalAtoms"] = len(rec["structure"])
        meta.setdefault("tool", "fpocket")
        meta["method"] = method
        rec["pockets"] = (cache_key, out, meta)
        self.log_message("%s %s: %d pockets in %.2fs",
                         method, token, len(out), result.elapsed)
        return {"token": token, "cached": False, "pockets": out, "meta": meta}

    def _generate(self, payload):
        """Start a NEAT-POCKET run for the requested pockets of a buffered
        structure. Returns immediately; the viewer polls /api/job."""
        token = payload.get("token") or ""
        with _LOCK:
            rec = BUFFER.get(token)
        if rec is None:
            raise KeyError(token)
        cached = rec.get("pockets")
        if not cached:
            raise GenerationError("run pocket detection on this structure first")

        wanted = {int(r) for r in (payload.get("ranks") or [])}
        if not wanted:
            raise GenerationError("no pockets selected")
        chosen = []
        for p in cached[1]:
            if p["rank"] not in wanted:
                continue
            residues = []
            for group in p["sel"]:
                residues += [[group["chain"], int(r)] for r in group["resi"]]
            chosen.append({"rank": p["rank"], "residues": residues})
        if not chosen:
            raise GenerationError("selected pockets are not in the current results")

        job = RUNNER.start(
            structure_path=rec["path"], pockets=chosen,
            out_root=rec["workdir"],
            n=int(payload.get("n") or 50),
            cfg=float(payload.get("cfg", 0.5)),
            batch_size=int(payload.get("batch_size") or 50),
        )
        self.log_message("neat-pocket job %s: pockets %s, %d each",
                         job.id, sorted(wanted), int(payload.get("n") or 50))
        return {"job": job.id, "ranks": sorted(wanted), "state": job.state}


def main(argv):
    args = [a for a in argv[1:] if a != "--no-open"]
    port = int(args[0]) if args else 8000
    open_browser = "--no-open" not in argv[1:]

    if not os.path.isdir(APP_DIR):
        sys.exit("no app/ directory next to %s" % __file__)

    handler = functools.partial(Handler, directory=APP_DIR)
    # Threaded, so a preparation run does not hold up every other request.
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as httpd:
        url = "http://127.0.0.1:%d/" % port
        print("serving %s at %s  (ctrl-c to stop)" % (APP_DIR, url))
        print("structure buffer: %s" % BUFFER.root)
        print("fpocket: %s" % (FPOCKET.binary if FPOCKET.available else
                               "NOT FOUND - pocket detection disabled "
                               "(put fpocket on PATH or set FPOCKET_BIN)"))
        print("NEAT-POCKET: %s" % (RUNNER.neat_dir + "  (" + RUNNER.python + ")"
                                   if RUNNER.available else
                                   "NOT FOUND - generation disabled "
                                   "(set NEAT_POCKET_DIR and NEAT_PYTHON)"))
        print("quickprep: %s" % ("%s  (%s)" % (PREP.version, PREP.python)
                                 if PREP.available else
                                 "NOT FOUND - Prepare disabled (install "
                                 "../quickprep into its env or set QUICKPREP_PYTHON)"))
        if LACUNA.available:
            # The MD backend runs on whatever platform OpenMM finds, and a pip
            # install of OpenMM has no CUDA plugin at all -- worth knowing
            # before waiting on a run that silently went to the CPU.
            platform = LACUNA.platform()
            print("lacuna: %s  (%s)  backends: %s%s"
                  % (LACUNA.version, LACUNA.binary, "/".join(LACUNA.backends),
                     "  openmm platform: " + platform if platform else ""))
        else:
            print("lacuna: NOT FOUND - ensemble pocket detection disabled "
                  "(pip install lacuna-pockets in its own env, or set LACUNA_BIN)")
        if open_browser:
            threading.Timer(0.4, webbrowser.open, [url]).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main(sys.argv)
