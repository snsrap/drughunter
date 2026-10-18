import unittest

from proteinviewer.errors import BadRequest
from proteinviewer.schemas import (BufferRequest, GenerateRequest, JobQuery,
                                   PocketRequest, PrepRequest)


class SchemaTest(unittest.TestCase):
    def test_buffer_defaults(self):
        r = BufferRequest.parse({"data": "ATOM"})
        self.assertEqual((r.token, r.fmt), ("structure", None))
        self.assertEqual(BufferRequest.parse({"data": "A", "token": "t"}).token, "t")
        with self.assertRaises(BadRequest):
            BufferRequest.parse({"data": "   "})

    def test_pocket_defaults_and_normalisation(self):
        r = PocketRequest.parse({"token": "t", "method": " LaCuNa ",
                                 "ligand": ["mk1", " ", "hem"], "cutoff": ""})
        self.assertEqual(r.method, "lacuna")
        self.assertEqual(r.cutoff, 6.0)
        self.assertEqual(r.ligands, frozenset({"MK1", "HEM"}))
        self.assertEqual(r.raw["method"], " LaCuNa ")

    def test_pocket_rejects_bad_values(self):
        for bad in ({"cutoff": "abc"}, {"cutoff": 0.1}, {"cutoff": True},
                    {"cutoff": float("nan")}, {"ligand": "MK1"}):
            with self.assertRaises(BadRequest, msg=bad):
                PocketRequest.parse(bad)

    def test_prep(self):
        r = PrepRequest.parse({"token": "t", "noRebuild": {"A": [1, "2"]}})
        self.assertEqual((r.ph, dict(r.no_rebuild)), (7.0, {"A": (1, 2)}))
        for bad in ({"noRebuild": [1]}, {"noRebuild": {"A": "12"}},
                    {"noRebuild": {"A": ["x"]}}, {"ph": 15}):
            with self.assertRaises(BadRequest, msg=bad):
                PrepRequest.parse(bad)

    def test_generate(self):
        r = GenerateRequest.parse({"token": "t", "ranks": [2, "1", 2]})
        self.assertEqual((r.ranks, r.n, r.cfg, r.batch_size),
                         (frozenset({1, 2}), 50, 0.5, 50))
        for bad in ({"ranks": []}, {"ranks": "1"}, {"ranks": [1], "n": 0},
                    {"ranks": [1], "n": 2.5}, {"ranks": ["x"]}):
            with self.assertRaises(BadRequest, msg=bad):
                GenerateRequest.parse(bad)

    def test_job_query(self):
        q = JobQuery.parse({"id": "j", "mols": "1"})
        self.assertEqual((q.job_id, q.molecules, q.table), ("j", True, False))


if __name__ == "__main__":
    unittest.main()
