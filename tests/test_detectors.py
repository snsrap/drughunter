import os
import shutil
import tempfile
import unittest
from unittest import mock

from proteinviewer.detectors import DetectorRegistry
from proteinviewer.detectors.fpocket import FpocketDetector
from proteinviewer.detectors.lacuna import LacunaDetector
from proteinviewer.errors import BadRequest, ToolUnavailable
from proteinviewer.structure import Structure

from .helpers import FAKE_FPOCKET, fake_executable, pdb_line, read

# Residues on the x axis at 10 A spacing, so the fake fpocket's pocket i
# (spheres around x = 10 i) lands on residue i.
LINE = Structure.from_text("\n".join(
    pdb_line(i, "CA", "GLY", "A", i, 10.0 * i, 0, 0, "C") for i in range(1, 6)), "pdb")


class FpocketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.det = FpocketDetector(fake_executable(self.tmp, "fpocket", FAKE_FPOCKET))
        self.work = os.path.join(self.tmp, "work")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_with(self, n, **kw):
        with mock.patch.dict(os.environ, {"FAKE_FPOCKET_N": str(n)}):
            return self.det.run(LINE, self.work, cutoff=3.0, **kw)

    def test_pockets_are_parsed_and_resolved_to_residues(self):
        result = self.run_with(2)
        self.assertEqual([p.rank for p in result], [1, 2])
        self.assertEqual([p.residues for p in result], [[("A", 1)], [("A", 2)]])
        self.assertEqual((result[1].score, result[1].volume), (0.5, 200.0))
        self.assertEqual(result.meta()["tool"], "fpocket")
        self.assertEqual(result.receptor_atoms, 5)

    def test_a_rerun_does_not_read_back_an_earlier_runs_pockets(self):
        # Regression: fpocket leaves an existing output directory's files in
        # place, so a second run with fewer pockets reported the old extras.
        self.assertEqual(len(self.run_with(4)), 4)
        self.assertEqual(len(self.run_with(1)), 1)

    def test_missing_binary(self):
        with mock.patch("proteinviewer.detectors.fpocket.shutil.which", return_value=None):
            det = FpocketDetector(os.path.join(self.tmp, "absent"))
        self.assertFalse(det.available)
        with self.assertRaises(ToolUnavailable):
            det.run(LINE, self.work)

    def test_nothing_left_after_solvent_is_a_bad_request(self):
        water = Structure.from_text(pdb_line(1, "O", "HOH", "A", 1, 0, 0, 0, "O", het=True))
        with self.assertRaises(BadRequest):
            self.det.run(water, self.work)


class LacunaOptionsTest(unittest.TestCase):
    def setUp(self):
        self.det = LacunaDetector()  # probes are lazy; options() needs none

    def test_defaults_and_case(self):
        self.assertEqual(self.det.options({"backend": "NMA"}),
                         {"backend": "nma", "detector": "alpha", "conformers": 20})

    def test_bad_values(self):
        for bad in ({"backend": "xx"}, {"detector": "xx"}, {"conformers": 0}):
            with self.assertRaises(BadRequest, msg=bad):
                self.det.options(bad)


class RegistryTest(unittest.TestCase):
    def test_lookup_and_listing(self):
        a = mock.Mock(); a.name = "a"; a.describe.return_value = {"available": True}
        b = mock.Mock(); b.name = "b"; b.describe.return_value = {"available": False}
        reg = DetectorRegistry([a, b])
        self.assertIs(reg.get("b"), b)
        self.assertEqual(reg.names(), ["a", "b"])
        self.assertEqual(reg.describe(), {"a": {"available": True}, "b": {"available": False}})
        with self.assertRaisesRegex(BadRequest, "want a or b"):
            reg.get("c")
        with self.assertRaises(ValueError):
            reg.register(a)


@unittest.skipUnless(shutil.which("fpocket") or os.environ.get("FPOCKET_BIN"),
                     "fpocket is not installed")
class RealFpocketTest(unittest.TestCase):
    def test_crambin(self):
        s = Structure.from_text(read("1CRN.pdb"))
        with tempfile.TemporaryDirectory() as work:
            result = FpocketDetector(os.environ.get("FPOCKET_BIN")).run(s, work)
        self.assertGreater(len(result), 0)
        for p in result:
            self.assertTrue(p.residues)
            self.assertTrue(all(chain == "A" for chain, _ in p.residues))


@unittest.skipUnless(os.environ.get("PV_SLOW_TESTS"), "set PV_SLOW_TESTS=1 to run lacuna")
class RealLacunaTest(unittest.TestCase):
    def test_crambin_nma(self):
        det = LacunaDetector(os.environ.get("LACUNA_BIN"))
        if not det.available:
            self.skipTest("lacuna is not installed")
        s = Structure.from_text(read("1CRN.pdb"))
        with tempfile.TemporaryDirectory() as work:
            result = det.run(s, work, conformers=5)
        self.assertEqual(result.meta()["backend"], "nma")
        self.assertTrue(all(p.residues for p in result))


if __name__ == "__main__":
    unittest.main()
