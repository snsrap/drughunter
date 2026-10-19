"""The use cases, with every external tool replaced by a fake."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from proteinviewer.buffer import StructureBuffer
from proteinviewer.detectors import DetectorRegistry, PocketDetector
from proteinviewer.errors import BadRequest, Conflict, NotFound
from proteinviewer.pockets import DetectionResult, Pocket
from proteinviewer.project import ProjectStore
from proteinviewer.schemas import (BufferRequest, GenerateRequest, JobQuery,
                                   PocketRequest, PrepRequest, ProjectRequest,
                                   ProjectSaveRequest)
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


class ProjectArtifactsTest(unittest.TestCase):
    """Every run that produces a file writes it into the session's project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "work"
        self.detector, self.prep, self.gen = FakeDetector(), FakePrep(), FakeGenerator()
        self.store = ProjectStore(Path(self.tmp.name) / "projects", api=API_VERSION)
        self.svc = ViewerService(StructureBuffer(self.work / "structures"),
                                 DetectorRegistry([self.detector]), self.prep, self.gen,
                                 projects=self.store, work_root=self.work)
        self.token = self.svc.buffer_structure(
            BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")}))["token"]
        self.project = self.svc.create_project(ProjectRequest.parse({"name": "1CRN"}))

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return Path(self.project["path"]).joinpath(*parts)

    def files(self):
        return sorted(f["path"] for f in
                      json.loads(self.path("manifest.json").read_text())["artifacts"])

    def run_pockets(self, **kw):
        return self.svc.find_pockets(PocketRequest.parse(
            {"token": self.token, "method": "fake", "project": self.project["id"], **kw}))

    def test_status_says_where_projects_go(self):
        self.assertEqual(self.svc.status()["projects"],
                         {"available": True, "root": str(self.store.root)})

    def test_a_pocket_run_writes_the_pockets_and_what_they_were_found_in(self):
        self.run_pockets()
        self.assertEqual(self.files(), ["pockets/1CRN.fake.json",
                                        "pockets/1CRN.fake.residues.txt",
                                        "pockets/1CRN.fake.spheres.pdb",
                                        "structures/1CRN.pdb"])
        saved = json.loads(self.path("pockets", "1CRN.fake.json").read_text())
        self.assertEqual([p["rank"] for p in saved["pockets"]], [1, 2])
        self.assertEqual(saved["meta"]["tool"], "fake")
        # The structure is the text the detector ran on, not a rewrite of it.
        self.assertEqual(self.path("structures", "1CRN.pdb").read_text(), read("1CRN.pdb"))
        # The site points load beside it, one residue per pocket.
        spheres = self.path("pockets", "1CRN.fake.spheres.pdb").read_text()
        self.assertIn("HETATM    1  O   STP P   1", spheres)
        self.assertIn("HETATM    2  O   STP P   2", spheres)
        self.assertIn("chain A  1", self.path("pockets", "1CRN.fake.residues.txt").read_text())

    def test_a_cached_run_still_fills_the_project_that_asked(self):
        self.run_pockets()
        second = self.svc.create_project(ProjectRequest.parse({"name": "again"}))
        out = self.svc.find_pockets(PocketRequest.parse(
            {"token": self.token, "method": "fake", "project": second["id"]}))
        self.assertTrue(out["cached"])
        self.assertTrue((Path(second["path"]) / "pockets" / "1CRN.fake.json").is_file())

    def test_preparing_writes_the_prepared_structure_and_its_report(self):
        self.svc.prepare(PrepRequest.parse({"token": self.token, "ph": 6.5,
                                            "project": self.project["id"]}))
        self.assertEqual(self.files(), ["structures/1CRN-prep.pdb",
                                        "structures/1CRN-prep.report.json",
                                        "structures/1CRN-prep.report.txt",
                                        "structures/1CRN.pdb"])
        self.assertEqual(self.path("structures", "1CRN-prep.pdb").read_text(), "ATOM")
        self.assertEqual(json.loads(self.path("structures", "1CRN-prep.report.json")
                                    .read_text()), {"seconds": 1})

    def test_a_finished_job_writes_its_molecules_once(self):
        self.run_pockets()
        out = self.svc.start_generation(GenerateRequest.parse(
            {"token": self.token, "ranks": [1], "project": self.project["id"]}))
        job = self.gen.get(out["job"])
        # Nothing is written while it is still running.
        self.svc.job_status(JobQuery.parse({"id": job.id}))
        self.assertNotIn("molecules/job1/pocket-1.sdf", self.files())
        job.state = "done"
        self.svc.job_status(JobQuery.parse({"id": job.id, "mols": "1", "table": "1"}))
        self.assertIn("molecules/job1/pocket-1.sdf", self.files())
        self.assertEqual(self.path("molecules", "job1", "pocket-1.sdf").read_text(), "sdf")
        self.assertEqual(self.path("molecules", "job1", "molecules.csv").read_text(),
                         "rank,i,name,formula,mw,heavy,logp,tpsa,qed,rings,rotb,smiles\n"
                         "1,,,,,,,,,,,\n")
        # Polling a finished job again does not rewrite them.
        self.path("molecules", "job1", "pocket-1.sdf").write_text("edited")
        self.svc.job_status(JobQuery.parse({"id": job.id, "mols": "1"}))
        self.assertEqual(self.path("molecules", "job1", "pocket-1.sdf").read_text(), "edited")

    def test_saving_adds_the_pages_own_state_and_copies_everything(self):
        self.run_pockets()
        dest = Path(self.tmp.name) / "somewhere"
        out = self.svc.save_project(ProjectSaveRequest.parse(
            {"project": self.project["id"], "path": str(dest),
             "session": {"app": "proteinviewer", "ents": []}}))
        self.assertEqual(out["path"], str(dest))
        self.assertIn("session.json", out["files"])
        self.assertEqual(json.loads((dest / "session.json").read_text())["app"],
                         "proteinviewer")
        self.assertTrue((dest / "pockets" / "1CRN.fake.json").is_file())
        self.assertTrue((dest / "structures" / "1CRN.pdb").is_file())

    def test_a_project_cannot_be_saved_into_the_servers_own_tree(self):
        with self.assertRaises(BadRequest):
            self.svc.save_project(ProjectSaveRequest.parse(
                {"project": self.project["id"], "path": str(self.work / "structures")}))
        with self.assertRaises(BadRequest):
            self.svc.save_project(ProjectSaveRequest.parse(
                {"project": self.project["id"], "path": str(self.store.root / "x")}))

    def test_an_unknown_project_is_not_found(self):
        with self.assertRaises(NotFound):
            self.run_pockets(project="nope")
        with self.assertRaises(NotFound):
            self.svc.save_project(ProjectSaveRequest.parse(
                {"project": "nope", "path": str(Path(self.tmp.name) / "x")}))
        with self.assertRaises(BadRequest):
            ProjectSaveRequest.parse({"path": "/tmp"})

    def test_without_a_project_nothing_is_written_down(self):
        svc = ViewerService(StructureBuffer(self.work / "plain"),
                            DetectorRegistry([self.detector]), self.prep, self.gen)
        token = svc.buffer_structure(
            BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")}))["token"]
        svc.find_pockets(PocketRequest.parse({"token": token, "method": "fake"}))
        self.assertFalse(svc.status()["projects"]["available"])
        with self.assertRaises(NotFound):      # a page that names one anyway
            svc.find_pockets(PocketRequest.parse({"token": token, "method": "fake",
                                                  "project": "whatever"}))


if __name__ == "__main__":
    unittest.main()
