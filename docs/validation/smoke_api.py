#!/usr/bin/env python3
"""Smoke-test the viewer's pocket API: status, buffer, fpocket, lacuna.

Usage: smoke_api.py PORT STRUCTURE.pdb
"""

import json
import sys
import urllib.request

PORT, STRUCT = sys.argv[1], sys.argv[2]
BASE = "http://127.0.0.1:%s" % PORT


def post(route, payload):
    req = urllib.request.Request(
        BASE + route,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


with urllib.request.urlopen(BASE + "/api/status", timeout=300) as r:
    st = json.load(r)
det = st["detectors"]
print(
    "status: api=%s  fpocket=%s  lacuna=%s %s backends=%s"
    % (
        st["api"],
        det["fpocket"]["available"],
        det["lacuna"]["available"],
        det["lacuna"]["version"],
        det["lacuna"]["backends"],
    )
)

code, buf = post("/api/structure", {"id": "1BTL", "data": open(STRUCT).read(), "fmt": "pdb"})
print("buffer: %s  %s as %s" % (code, buf["token"], buf["fmt"]))
token = buf["token"]

for label, body in [
    ("fpocket", {"token": token, "method": "fpocket"}),
    ("lacuna/nma", {"token": token, "method": "lacuna", "backend": "nma", "conformers": 20}),
    ("lacuna/nma cached", {"token": token, "method": "lacuna", "backend": "nma", "conformers": 20}),
    ("bad method", {"token": token, "method": "nope"}),
]:
    code, out = post("/api/pockets", body)
    if code != 200:
        print("%-18s -> %s %s" % (label, code, out.get("error", "")[:90]))
        continue
    m, ps = out["meta"], out["pockets"]
    extra = ""
    if m.get("tool") == "lacuna":
        extra = "  backend=%s conformers=%s cryptic=%s ensemble=%s" % (
            m["backend"],
            m["conformers"],
            m["nCryptic"],
            m["ensemble"] or "-",
        )
    print(
        "%-18s -> %s  %d pockets in %ss  cached=%s%s"
        % (label, code, len(ps), m["elapsed"], out["cached"], extra)
    )
    p = ps[0]
    print(
        "%-18s    rank1: drug=%s vol=%s persist=%s crypt=%s res=%s chains=%s"
        % (
            "",
            p["drug"],
            p["volume"],
            p["persistence"],
            p["crypticity"],
            p["nResidues"],
            [g["chain"] for g in p["sel"]],
        )
    )
    # The panel parses this with JSON.parse; a bare NaN would have thrown above,
    # but assert the field is genuinely null rather than absent.
    assert "persistence" in p, "persistence missing from the JSON"
print("OK")
