"""The pocketbench adapter, against a stand-in for ``python -m pocketbench``
that answers the way the real one does: JSON on stdout, and an exit code
saying whose problem a failure was."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from proteinviewer.errors import BadRequest, ToolFailed, ToolUnavailable
from proteinviewer.pocketbench import Pocketbench, residue_spec

from .helpers import fake_executable

# Records each call's arguments in $FAKE_PB_LOG, then answers by command. A
# method named "bad", "missing" or "broken" fails the way pocketbench would.
FAKE_PYTHON = r"""
import json, os, sys
args = sys.argv[3:]                       # after "-m pocketbench"
with open(os.environ["FAKE_PB_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
cmd = args[0]
if cmd == "detectors":
    print(json.dumps({"version": "0.2.0", "cutoff": 6.0, "additives": ["HOH", "SO4"],
        "detectors": {
            "fpocket": {"available": True, "options": {}, "status": "fpocket: /bin/fpocket"},
            "lacuna": {"available": True, "backends": ["nma"], "status": "lacuna: 1.2.0",
                       "options": {"backend": {"default": "nma", "choices": ["nma", "openmm"]},
                                   "conformers": {"default": 20, "integer": True, "min": 1, "max": 500}}}}}))
elif cmd == "detect":
    method = args[1]
    fails = {"bad": (2, "bad-input"), "missing": (3, "unavailable"), "broken": (4, "failed")}
    if method in fails:
        print(json.dumps({"error": method + " went wrong", "kind": fails[method][1]}))
        sys.exit(fails[method][0])
    if method == "garbage":
        print("Traceback: not JSON at all")
        sys.exit(1)
    print(json.dumps({"tool": method, "pockets": [{"rank": 1}], "meta": {"tool": method}}))
elif cmd == "superpose":
    open(args[args.index("-o") + 1], "w").write("MOVED\n")
    print(json.dumps({"rmsd": 0.4, "n": 9, "output": "x"}))
"""


class PocketbenchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.log"
        os.environ["FAKE_PB_LOG"] = str(self.log)
        self.pb = Pocketbench(fake_executable(self.tmp.name, "python", FAKE_PYTHON),
                              fpocket_bin="/opt/fpocket")

    def tearDown(self):
        os.environ.pop("FAKE_PB_LOG", None)
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def test_what_it_can_do_is_asked_once(self):
        self.assertTrue(self.pb.available)
        self.assertEqual(self.pb.version, "0.2.0")
        self.assertEqual(self.pb.additives(), ["HOH", "SO4"])
        self.assertEqual(list(self.pb.detectors()), ["fpocket", "lacuna"])
        self.assertEqual(self.pb.status_lines()[1:], ["fpocket: /bin/fpocket", "lacuna: 1.2.0"])
        # The viewer's FPOCKET_BIN goes through to pocketbench.
        self.assertEqual(self.calls(), [["detectors", "--json", "--fpocket-bin", "/opt/fpocket"]])

    def test_options_are_checked_against_what_pocketbench_publishes(self):
        self.assertEqual(self.pb.options("lacuna", {"backend": "NMA"}),
                         {"backend": "nma", "conformers": 20})
        self.assertEqual(self.pb.options("fpocket", {"backend": "whatever"}), {})
        for bad in ({"backend": "xx"}, {"conformers": 0}, {"conformers": "many"}):
            with self.assertRaises(BadRequest, msg=bad):
                self.pb.options("lacuna", bad)
        with self.assertRaisesRegex(BadRequest, "want fpocket or lacuna"):
            self.pb.options("nope", {})

    def test_a_run_is_one_command_and_its_json(self):
        out = self.pb.detect("lacuna", "/w/source.pdb", "/w", cutoff=6.0, ligands={"MK1", "HEM"},
                             options={"backend": "nma", "conformers": 20})
        self.assertEqual(out["pockets"], [{"rank": 1}])
        self.assertEqual(self.calls()[-1], [
            "detect", "lacuna", "/w/source.pdb", "--workdir", "/w", "--cutoff", "6", "--json",
            "--fpocket-bin", "/opt/fpocket", "--ligand", "HEM,MK1",
            "--backend", "nma", "--conformers", "20"])

    def test_exit_codes_become_the_viewers_errors(self):
        for method, err in (("bad", BadRequest), ("missing", ToolUnavailable), ("broken", ToolFailed),
                            ("garbage", ToolFailed)):
            with self.assertRaises(err, msg=method) as ctx:
                self.pb.detect(method, "/w/s.pdb", "/w", cutoff=6.0)
            if method != "garbage":
                self.assertEqual(str(ctx.exception), method + " went wrong")

    def test_superposing_passes_the_fit_restriction_as_a_spec(self):
        out_path = Path(self.tmp.name) / "moved.pdb"
        fit = self.pb.superpose("/m.pdb", "/r.pdb", str(out_path), on=[("A", 1, ""), ("A", 2, "")])
        self.assertEqual((fit["rmsd"], out_path.read_text()), (0.4, "MOVED\n"))
        self.assertEqual(self.calls()[-1],
                         ["superpose", "/m.pdb", "/r.pdb", "-o", str(out_path), "--json", "--on", "A:1-2"])

    def test_not_installed(self):
        pb = Pocketbench(os.path.join(self.tmp.name, "absent"))
        pb.python = None    # the server's own interpreter may have the real one
        self.assertFalse(pb.available)
        self.assertIn("NOT FOUND", pb.status_lines()[0])
        with self.assertRaises(ToolUnavailable):
            pb.options("fpocket", {})
        with self.assertRaises(ToolUnavailable):
            pb.superpose("/m", "/r", "/o")


class ResidueSpecTest(unittest.TestCase):
    def test_runs_become_ranges_and_inserted_residues_stand_alone(self):
        keys = [("A", n, "") for n in (5, 1, 2, 3, 9)] + [("B", 7, ""), ("A", 52, "A")]
        self.assertEqual(residue_spec(keys), "A:1-3,A:5,A:9,B:7,A:52A")
        self.assertEqual(residue_spec([]), "")


if __name__ == "__main__":
    unittest.main()
