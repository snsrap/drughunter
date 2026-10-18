"""The use cases, with every external tool replaced by a fake."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from proteinviewer.buffer import StructureBuffer
from proteinviewer.detectors import DetectorRegistry, PocketDetector
from proteinviewer.errors import BadRequest, Conflict, NotFound
from proteinviewer.pockets import DetectionResult, Pocket
from proteinviewer.schemas import (BufferRequest, GenerateRequest, JobQuery,
                                   PocketRequest, PrepRequest)
from proteinviewer.service import API_VERSION, ViewerService

from .helpers import read


class FakeDetector(PocketDetector):
    name = "fake"

    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay

    available = True

    def describe(self):
        return {"available": True}

    def status_line(self):
        return "fake: ok"

    def options(self, raw):
        return {"depth": int(raw.get("depth") or 1)}

    def run(self, structure, workdir, *, cutoff, ligand_resnames, **options):
        self.calls.append((cutoff, ligand_resnames, options))
        time.sleep(self.delay)
        pockets = [Pocket(rank=r, score=1.0, druggability=0.0, volume=1.0,
                          centers=[(0, 0, 0)], radii=[1.0],
                          residues=[("A", r), ("B", 10 + r)]) for r in (1, 2)]
        return DetectionResult(self.name, pockets, ["fake"], 0.01, len(structure),
                               cutoff, extra={"depth": options["depth"]})


class FakePrep:
    def __init__(self):
        self.calls = []

    def describe(self):
        return {"available": True}

    def run(self, path, workdir, ph=7.0, name=None, no_rebuild=None):
        self.calls.append((path, ph, name, dict(no_rebuild or {})))
        return {"data": "ATOM", "report": {"seconds": 1}, "summary": "ok"}


class FakeJob:
    def __init__(self, job_id, pockets):
        self.id, self.state, self.pockets = job_id, "queued", pockets

    def progress(self):
        return {"id": self.id, "state": self.state}

    def molecules(self):
        return {"1": "sdf"}

    def table(self):
        return [{"rank": 1}]


class FakeGenerator:
    def __init__(self):
        self.jobs = {}

    def describe(self):
        return {"available": True}

    def start(self, structure_path, pockets, *, n, cfg, batch_size):
        job = FakeJob("job%d" % (len(self.jobs) + 1), pockets)
        self.jobs[job.id] = job
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.detector = FakeDetector()
        self.prep = FakePrep()
        self.gen = FakeGenerator()
        self.svc = ViewerService(StructureBuffer(Path(self.tmp.name)),
                                 DetectorRegistry([self.detector]), self.prep, self.gen)
        self.token = self.svc.buffer_structure(
            BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")}))["token"]

    def tearDown(self):
        self.tmp.cleanup()

    def pockets(self, **kw):
        return self.svc.find_pockets(PocketRequest.parse(
            {"token": self.token, "method": "fake", **kw}))

    def test_status_lists_every_tool(self):
        st = self.svc.status()
        self.assertEqual(st["api"], API_VERSION)
        self.assertEqual(st["detectors"], {"fake": {"available": True}})

    def test_buffering_reports_the_structure(self):
        out = self.svc.buffer_structure(BufferRequest.parse({"id": "x", "data": read("1CRN.cif")}))
        self.assertEqual((out["token"], out["nAtoms"], out["chains"]), ("x", 327, ["A"]))
        with self.assertRaises(BadRequest):
            self.svc.buffer_structure(BufferRequest.parse({"data": "garbage"}))

    def test_a_repeat_is_answered_from_the_cache(self):
        first = self.pockets(ligand=["hem"])
        again = self.pockets(ligand=["HEM"])
        self.assertEqual((first["cached"], again["cached"]), (False, True))
        self.assertEqual(len(self.detector.calls), 1)
        self.assertEqual(first["meta"]["ligandRemoved"], ["HEM"])
        self.assertEqual(first["meta"]["method"], "fake")
        self.assertEqual(first["meta"]["totalAtoms"], 327)
        self.assertEqual(first["pockets"][0]["sel"],
                         [{"chain": "A", "resi": [1]}, {"chain": "B", "resi": [11]}])

    def test_detector_options_are_part_of_a_runs_identity(self):
        self.pockets(depth=1)
        out = self.pockets(depth=2)
        self.assertFalse(out["cached"])
        self.assertEqual(out["meta"]["depth"], 2)
        self.assertEqual(len(self.detector.calls), 2)

    def test_runs_on_one_structure_take_turns_and_share_the_result(self):
        self.detector.delay = 0.3
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.pockets()))
                   for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.detector.calls), 1)
        self.assertEqual(sorted(r["cached"] for r in results), [False, True, True])

    def test_unknown_things(self):
        with self.assertRaises(BadRequest):
            self.pockets(method="nope")
        with self.assertRaises(NotFound):
            self.svc.find_pockets(PocketRequest.parse({"token": "nope"}))
        with self.assertRaises(NotFound):
            self.svc.job_status(JobQuery.parse({"id": "nope"}))

    def test_prepare_passes_the_buffered_copy(self):
        self.svc.prepare(PrepRequest.parse({"token": self.token, "ph": 6.5,
                                            "noRebuild": {"A": [3, 4]}}))
        ((path, ph, name, no_rebuild),) = self.prep.calls
        self.assertTrue(path.endswith("source.pdb"))
        self.assertEqual((ph, name, no_rebuild), (6.5, "1CRN", {"A": (3, 4)}))

    def test_generation_needs_a_pocket_run_and_known_ranks(self):
        req = GenerateRequest.parse({"token": self.token, "ranks": [2]})
        with self.assertRaises(Conflict):
            self.svc.start_generation(req)
        self.pockets()
        with self.assertRaises(Conflict):
            self.svc.start_generation(GenerateRequest.parse({"token": self.token, "ranks": [9]}))
        out = self.svc.start_generation(req)
        job = self.gen.get(out["job"])
        # Residues go to the generator as (chain, resi) pairs, chains kept apart.
        self.assertEqual(job.pockets, [{"rank": 2, "residues": [["A", 2], ["B", 12]]}])
        status = self.svc.job_status(JobQuery.parse({"id": out["job"], "mols": "1", "table": "1"}))
        self.assertEqual((status["molecules"], status["table"]), ({"1": "sdf"}, [{"rank": 1}]))


if __name__ == "__main__":
    unittest.main()
