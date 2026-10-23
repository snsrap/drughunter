"""The use cases, with every external tool replaced by a fake."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from proteinviewer.buffer import StructureBuffer
from proteinviewer.errors import BadRequest, Conflict, NotFound
from proteinviewer.project import ProjectStore
from proteinviewer.schemas import (
    BufferRequest,
    GenerateRequest,
    JobQuery,
    PocketRequest,
    PrepRequest,
    ProjectRequest,
    ProjectSaveRequest,
    ScoreQuery,
    ScoreRequest,
    SuperposeRequest,
)
from proteinviewer.service import API_VERSION, ViewerService, merge_scores

from .helpers import read


class FakePockets:
    """pocketbench, as the service sees it: one detector, "fake", taking a
    "depth" option, and a superposition that writes a marker.
    """

    def __init__(self, delay=0.0):
        self.calls = []
        self.superposed = []
        self.delay = delay

    def describe(self):
        return {"available": True, "version": "0", "python": ""}

    def detectors(self):
        return {
            "fake": {
                "available": True,
                "options": {"depth": {"default": 1, "integer": True, "min": 1, "max": 9}},
            }
        }

    def additives(self):
        return ["HOH"]

    def scorers(self):
        return {
            "fakesmina": {
                "available": True,
                "status": "fakesmina: ok",
                "options": {
                    "mode": {"default": "minimize", "choices": ["rescore", "minimize"]},
                    "padding": {"default": 4, "integer": True, "min": 0, "max": 20},
                },
            }
        }

    def score_options(self, scorer, raw):
        if scorer not in ("", "fakesmina"):
            raise BadRequest("unknown scorer %r" % scorer)
        mode = str(raw.get("mode") or "minimize")
        if mode not in ("rescore", "minimize"):
            raise BadRequest("unknown mode %r" % mode)
        return {"mode": mode, "padding": int(raw.get("padding") or 4)}

    def options(self, method, raw):
        if method != "fake":
            raise BadRequest("unknown method %r (want fake)" % method)
        return {"depth": int(raw.get("depth") or 1)}

    def detect(self, method, path, workdir, *, cutoff, ligands=(), options=None):
        self.calls.append((path, cutoff, frozenset(ligands), dict(options or {})))
        time.sleep(self.delay)
        pockets = [
            {
                "rank": r,
                "score": 1.0,
                "volume": 1.0,
                "nResidues": 2,
                "nSpheres": 1,
                "centroid": [0, 0, 0],
                "spheres": [[0, 0, 0, 1.0]],
                "sel": [{"chain": "A", "resi": [r]}, {"chain": "B", "resi": [10 + r]}],
            }
            for r in (1, 2)
        ]
        return {
            "tool": method,
            "pockets": pockets,
            "meta": {
                "tool": method,
                "command": "fake",
                "elapsed": 0.01,
                "totalAtoms": 327,
                "depth": options["depth"],
            },
        }

    def superpose(self, mobile, reference, out, *, on=(), mobile_on=()):
        self.superposed.append((mobile, reference, tuple(on), tuple(mobile_on)))
        with open(out, "w") as fh:
            fh.write("MOVED\n")
        return {
            "rmsd": 0.5,
            "n": 10,
            "aligned": 12,
            "paired": 12,
            "ident": 1.0,
            "related": True,
            "chains": [["A", "A"]],
            "transform": {"R": [], "t": []},
        }


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
        # `i` is the molecule's place in its pocket's SDF: what a score is
        # joined back on.
        return [{"rank": 1, "i": 0}]


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


class FakeScoreJob:
    def __init__(self, job_id, ranks, mode, minimized):
        self.id, self.state, self.ranks, self.mode = job_id, "done", ranks, mode
        self.minimized = minimized

    def progress(self):
        return {"id": self.id, "state": self.state}

    def payload(self):
        return {
            "scores": {
                str(r): [
                    {
                        "index": 0,
                        "name": "pocket%s_1" % r,
                        "score": -5.5,
                        "le": 0.25,
                        "rmsd_min": 0.4,
                    }
                ]
                for r in self.ranks
            },
            "scorers": {
                str(r): {
                    "scorer": "fakesmina 1",
                    "mode": self.mode,
                    "command": "fakesmina -r r -l l",
                    "minimized": self.minimized,
                }
                for r in self.ranks
            },
        }


class FakeScoring:
    """The scoring runner, answering at once: the service only waits on its
    state, which is the part worth testing here.
    """

    def __init__(self, tmp):
        self.jobs = {}
        self.calls = []
        self.tmp = Path(tmp)

    def describe(self):
        return {"available": True, "scorers": {"fakesmina": {"available": True}}}

    def start(self, receptor, poses, spheres, *, mode, padding, scorer="", ligands=(), parent=""):
        self.calls.append(
            {
                "receptor": receptor,
                "ranks": sorted(poses),
                "spheres": spheres,
                "mode": mode,
                "padding": padding,
                "scorer": scorer,
                "ligands": tuple(ligands),
                "parent": parent,
            }
        )
        minimized = self.tmp / ("min_%d.sdf" % len(self.jobs))
        minimized.write_text("relaxed\n$$$$\n")
        job = FakeScoreJob("score%d" % (len(self.jobs) + 1), sorted(poses), mode, str(minimized))
        self.jobs[job.id] = job
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.detector = FakePockets()
        self.prep = FakePrep()
        self.gen = FakeGenerator()
        self.scoring = FakeScoring(self.tmp.name)
        self.svc = ViewerService(
            StructureBuffer(Path(self.tmp.name)),
            self.detector,
            self.prep,
            self.gen,
            scoring=self.scoring,
        )
        self.token = self.svc.buffer_structure(
            BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")})
        )["token"]

    def tearDown(self):
        self.tmp.cleanup()

    def pockets(self, **kw):
        return self.svc.find_pockets(
            PocketRequest.parse({"token": self.token, "method": "fake", **kw})
        )

    # -- pose scoring --------------------------------------------------------
    def generated(self, **kw):
        """A finished generation job on pocket 1."""
        self.pockets(**kw)
        out = self.svc.start_generation(GenerateRequest.parse({"token": self.token, "ranks": [1]}))
        job = self.gen.get(out["job"])
        job.state = "done"
        return job

    def test_scoring_runs_on_a_finished_jobs_poses_in_their_own_pockets(self):
        job = self.generated(ligand=["XYZ"])
        out = self.svc.start_scoring(
            ScoreRequest.parse({"job": job.id, "token": self.token, "mode": "rescore"})
        )
        self.assertEqual((out["ranks"], out["mode"], out["state"]), ([1], "rescore", "done"))
        call = self.scoring.calls[0]
        # the receptor is the structure the poses were generated in, and the
        # box comes from the pocket run that produced them
        self.assertTrue(call["receptor"].endswith(".pdb"))
        self.assertIn("pocket site points", call["spheres"])
        self.assertEqual((call["ranks"], call["parent"]), ([1], job.id))
        self.assertEqual(call["ligands"], ("XYZ",))

    def test_the_scorers_options_are_checked_against_what_pocketbench_publishes(self):
        job = self.generated()
        with self.assertRaises(BadRequest):
            self.svc.start_scoring(
                ScoreRequest.parse({"job": job.id, "token": self.token, "mode": "dock"})
            )
        with self.assertRaises(BadRequest):
            self.svc.start_scoring(
                ScoreRequest.parse({"job": job.id, "token": self.token, "scorer": "vina"})
            )

    def test_there_is_nothing_to_score_before_a_job_has_finished(self):
        self.pockets()
        out = self.svc.start_generation(GenerateRequest.parse({"token": self.token, "ranks": [1]}))
        with self.assertRaises(Conflict):
            self.svc.start_scoring(ScoreRequest.parse({"job": out["job"], "token": self.token}))
        with self.assertRaises(NotFound):
            self.svc.start_scoring(ScoreRequest.parse({"job": "nope", "token": self.token}))

    def test_a_pocket_without_poses_is_refused(self):
        job = self.generated()
        with self.assertRaises(Conflict):
            self.svc.start_scoring(
                ScoreRequest.parse({"job": job.id, "token": self.token, "ranks": [2]})
            )

    def test_the_scores_come_back_when_the_job_is_done(self):
        job = self.generated()
        started = self.svc.start_scoring(ScoreRequest.parse({"job": job.id, "token": self.token}))
        out = self.svc.score_status(ScoreQuery.parse({"id": started["job"]}))
        self.assertEqual(out["scores"]["1"][0]["score"], -5.5)
        self.assertEqual(out["scorers"]["1"]["scorer"], "fakesmina 1")
        with self.assertRaises(NotFound):
            self.svc.score_status(ScoreQuery.parse({"id": "nope"}))

    def test_status_lists_every_tool(self):
        st = self.svc.status()
        self.assertEqual(st["api"], API_VERSION)
        self.assertEqual(list(st["detectors"]), ["fake"])
        self.assertEqual((st["additives"], st["superpose"]["available"]), (["HOH"], True))
        self.assertTrue(st["scoring"]["available"])

    def test_buffering_checks_for_atoms_and_parses_nothing(self):
        out = self.svc.buffer_structure(BufferRequest.parse({"id": "x", "data": read("1CRN.cif")}))
        self.assertEqual((out["token"], out["fmt"]), ("x", "cif"))
        with self.assertRaises(BadRequest):
            self.svc.buffer_structure(BufferRequest.parse({"data": "garbage"}))

    def test_superposing_returns_the_moved_text_and_the_fit(self):
        other = self.svc.buffer_structure(
            BufferRequest.parse({"id": "ref", "data": read("1CRN.pdb")})
        )["token"]
        out = self.svc.superpose(
            SuperposeRequest.parse(
                {"token": self.token, "reference": other, "on": [["A", 1], ["A", 2, "B"]]}
            )
        )
        self.assertEqual(out["data"], "MOVED\n")
        self.assertEqual((out["fit"]["rmsd"], out["fit"]["n"]), (0.5, 10))
        self.assertNotIn("transform", out["fit"])
        ((mobile, ref, on, mobile_on),) = self.detector.superposed
        self.assertTrue(mobile.endswith("source.pdb") and "/ref/" in ref)
        self.assertEqual((on, mobile_on), ((("A", 1, ""), ("A", 2, "B")), ()))
        with self.assertRaises(NotFound):
            self.svc.superpose(SuperposeRequest.parse({"token": self.token, "reference": "nope"}))
        for bad in (
            {"token": self.token},
            {"token": self.token, "reference": other, "on": "A:1"},
            {"token": self.token, "reference": other, "on": [["A", "x"]]},
            {"token": self.token, "reference": other, "on": [["A,B", 1]]},
            {"token": self.token, "reference": other, "mobileOn": [["A", 1, "AB"]]},
        ):
            with self.assertRaises(BadRequest, msg=bad):
                SuperposeRequest.parse(bad)

    def test_a_repeat_is_answered_from_the_cache(self):
        first = self.pockets(ligand=["hem"])
        again = self.pockets(ligand=["HEM"])
        self.assertEqual((first["cached"], again["cached"]), (False, True))
        self.assertEqual(len(self.detector.calls), 1)
        self.assertEqual(first["meta"]["ligandRemoved"], ["HEM"])
        self.assertEqual(first["meta"]["method"], "fake")
        self.assertEqual(first["meta"]["totalAtoms"], 327)
        self.assertEqual(
            first["pockets"][0]["sel"], [{"chain": "A", "resi": [1]}, {"chain": "B", "resi": [11]}]
        )

    def test_detector_options_are_part_of_a_runs_identity(self):
        self.pockets(depth=1)
        out = self.pockets(depth=2)
        self.assertFalse(out["cached"])
        self.assertEqual(out["meta"]["depth"], 2)
        self.assertEqual(len(self.detector.calls), 2)

    def test_runs_on_one_structure_take_turns_and_share_the_result(self):
        self.detector.delay = 0.3
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(self.pockets())) for _ in range(3)
        ]
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
        self.svc.prepare(
            PrepRequest.parse({"token": self.token, "ph": 6.5, "noRebuild": {"A": [3, 4]}})
        )
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
        self.assertEqual(
            (status["molecules"], status["table"]), ({"1": "sdf"}, [{"rank": 1, "i": 0}])
        )


class MergeScoresTest(unittest.TestCase):
    """The join the feature exists for: a score onto the row whose pose it is."""

    rows = [
        {"rank": 1, "i": 0, "name": "pocket1_1", "qed": 0.5},
        {"rank": 1, "i": 1, "name": "pocket1_2", "qed": 0.6},
        {"rank": 2, "i": 0, "name": "pocket2_1", "qed": 0.7},
    ]
    scores = {
        "1": [
            {"index": 0, "score": -5.5, "le": 0.25, "rmsd_min": 0.4},
            {"index": 1, "score": None, "le": None, "rmsd_min": None, "error": "clash"},
        ]
    }
    scorers = {"1": {"scorer": "smina 1"}}

    def merged(self):
        return merge_scores(self.rows, self.scores, self.scorers)

    def test_each_score_lands_on_its_own_row(self):
        out = self.merged()
        self.assertEqual((out[0]["score"], out[0]["le"], out[0]["rmsd_min"]), (-5.5, 0.25, 0.4))
        self.assertEqual(out[0]["scorer"], "smina 1")
        self.assertEqual(out[0]["qed"], 0.5)  # nothing else is disturbed

    def test_a_pose_the_scorer_could_not_do_keeps_its_row_and_says_why(self):
        out = self.merged()
        self.assertIsNone(out[1]["score"])
        self.assertEqual(out[1]["scoreError"], "clash")

    def test_an_unscored_pocket_is_left_exactly_as_it_was(self):
        self.assertEqual(self.merged()[2], self.rows[2])

    def test_rows_without_a_score_are_never_given_a_zero(self):
        out = merge_scores(self.rows, {}, {})
        self.assertEqual(out, self.rows)
        self.assertNotIn("score", out[0])


class ProjectArtifactsTest(unittest.TestCase):
    """Every run that produces a file writes it into the session's project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "work"
        self.detector, self.prep, self.gen = FakePockets(), FakePrep(), FakeGenerator()
        self.store = ProjectStore(Path(self.tmp.name) / "projects", api=API_VERSION)
        self.scoring = FakeScoring(self.work)
        self.svc = ViewerService(
            StructureBuffer(self.work / "structures"),
            self.detector,
            self.prep,
            self.gen,
            scoring=self.scoring,
            projects=self.store,
            work_root=self.work,
        )
        self.token = self.svc.buffer_structure(
            BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")})
        )["token"]
        self.project = self.svc.create_project(ProjectRequest.parse({"name": "1CRN"}))

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return Path(self.project["path"]).joinpath(*parts)

    def files(self):
        return sorted(
            f["path"] for f in json.loads(self.path("manifest.json").read_text())["artifacts"]
        )

    def run_pockets(self, **kw):
        return self.svc.find_pockets(
            PocketRequest.parse(
                {"token": self.token, "method": "fake", "project": self.project["id"], **kw}
            )
        )

    def test_status_says_where_projects_go(self):
        self.assertEqual(
            self.svc.status()["projects"], {"available": True, "root": str(self.store.root)}
        )

    def test_a_pocket_run_writes_the_pockets_and_what_they_were_found_in(self):
        self.run_pockets()
        self.assertEqual(
            self.files(),
            [
                "pockets/1CRN.fake.json",
                "pockets/1CRN.fake.residues.txt",
                "pockets/1CRN.fake.spheres.pdb",
                "structures/1CRN.pdb",
            ],
        )
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
        out = self.svc.find_pockets(
            PocketRequest.parse({"token": self.token, "method": "fake", "project": second["id"]})
        )
        self.assertTrue(out["cached"])
        self.assertTrue((Path(second["path"]) / "pockets" / "1CRN.fake.json").is_file())

    def test_preparing_writes_the_prepared_structure_and_its_report(self):
        self.svc.prepare(
            PrepRequest.parse({"token": self.token, "ph": 6.5, "project": self.project["id"]})
        )
        self.assertEqual(
            self.files(),
            [
                "structures/1CRN-prep.pdb",
                "structures/1CRN-prep.report.json",
                "structures/1CRN-prep.report.txt",
                "structures/1CRN.pdb",
            ],
        )
        self.assertEqual(self.path("structures", "1CRN-prep.pdb").read_text(), "ATOM")
        self.assertEqual(
            json.loads(self.path("structures", "1CRN-prep.report.json").read_text()), {"seconds": 1}
        )

    def test_a_finished_job_writes_its_molecules_once(self):
        self.run_pockets()
        out = self.svc.start_generation(
            GenerateRequest.parse(
                {"token": self.token, "ranks": [1], "project": self.project["id"]}
            )
        )
        job = self.gen.get(out["job"])
        # Nothing is written while it is still running.
        self.svc.job_status(JobQuery.parse({"id": job.id}))
        self.assertNotIn("molecules/job1/pocket-1.sdf", self.files())
        job.state = "done"
        self.svc.job_status(JobQuery.parse({"id": job.id, "mols": "1", "table": "1"}))
        self.assertIn("molecules/job1/pocket-1.sdf", self.files())
        self.assertEqual(self.path("molecules", "job1", "pocket-1.sdf").read_text(), "sdf")
        # The score columns are in the header before anything is scored, and
        # empty: a run nothing could score still has the same shape.
        self.assertEqual(
            self.path("molecules", "job1", "molecules.csv").read_text(),
            "rank,i,name,formula,mw,heavy,logp,tpsa,qed,rings,rotb,"
            "score,le,rmsd_min,scorer,smiles\n1,0,,,,,,,,,,,,,,\n",
        )
        # Polling a finished job again does not rewrite them.
        self.path("molecules", "job1", "pocket-1.sdf").write_text("edited")
        self.svc.job_status(JobQuery.parse({"id": job.id, "mols": "1"}))
        self.assertEqual(self.path("molecules", "job1", "pocket-1.sdf").read_text(), "edited")

    def test_a_finished_scoring_job_writes_its_scores_once_and_rejoins_the_table(self):
        self.run_pockets()
        gen = self.svc.start_generation(
            GenerateRequest.parse(
                {"token": self.token, "ranks": [1], "project": self.project["id"]}
            )
        )
        job = self.gen.get(gen["job"])
        job.state = "done"
        self.svc.job_status(JobQuery.parse({"id": job.id, "mols": "1", "table": "1"}))

        started = self.svc.start_scoring(
            ScoreRequest.parse({"job": job.id, "token": self.token, "project": self.project["id"]})
        )
        self.svc.score_status(ScoreQuery.parse({"id": started["job"]}))

        files = self.files()
        self.assertIn("molecules/job1/scores.json", files)
        self.assertIn("molecules/job1/pocket-1.min.sdf", files)
        # the generated poses are never written over by the relaxed ones
        self.assertEqual(self.path("molecules", "job1", "pocket-1.sdf").read_text(), "sdf")
        self.assertEqual(
            self.path("molecules", "job1", "pocket-1.min.sdf").read_text(), "relaxed\n$$$$\n"
        )
        # the table and the CSV carry the score, joined onto the right row
        rows = json.loads(self.path("molecules", "job1", "molecules.json").read_text())
        self.assertEqual(rows[0]["score"], -5.5)
        self.assertEqual(rows[0]["scorer"], "fakesmina 1")
        csv = self.path("molecules", "job1", "molecules.csv").read_text()
        self.assertIn("-5.5", csv.splitlines()[1])
        # and the manifest says what made them
        manifest = json.loads(self.path("manifest.json").read_text())
        made = {a["path"]: a for a in manifest["artifacts"]}
        self.assertEqual(made["molecules/job1/scores.json"]["kind"], "scores")
        self.assertEqual(made["molecules/job1/pocket-1.min.sdf"]["command"], "fakesmina -r r -l l")

        # Polling a finished scoring job again does not rewrite anything.
        self.path("molecules", "job1", "scores.json").write_text("edited")
        self.svc.score_status(ScoreQuery.parse({"id": started["job"]}))
        self.assertEqual(self.path("molecules", "job1", "scores.json").read_text(), "edited")

    def test_saving_adds_the_pages_own_state_and_copies_everything(self):
        self.run_pockets()
        dest = Path(self.tmp.name) / "somewhere"
        out = self.svc.save_project(
            ProjectSaveRequest.parse(
                {
                    "project": self.project["id"],
                    "path": str(dest),
                    "session": {"app": "proteinviewer", "ents": []},
                }
            )
        )
        self.assertEqual(out["path"], str(dest))
        self.assertIn("session.json", out["files"])
        self.assertEqual(json.loads((dest / "session.json").read_text())["app"], "proteinviewer")
        self.assertTrue((dest / "pockets" / "1CRN.fake.json").is_file())
        self.assertTrue((dest / "structures" / "1CRN.pdb").is_file())

    def test_a_project_cannot_be_saved_into_the_servers_own_tree(self):
        with self.assertRaises(BadRequest):
            self.svc.save_project(
                ProjectSaveRequest.parse(
                    {"project": self.project["id"], "path": str(self.work / "structures")}
                )
            )
        with self.assertRaises(BadRequest):
            self.svc.save_project(
                ProjectSaveRequest.parse(
                    {"project": self.project["id"], "path": str(self.store.root / "x")}
                )
            )

    def test_an_unknown_project_is_not_found(self):
        with self.assertRaises(NotFound):
            self.run_pockets(project="nope")
        with self.assertRaises(NotFound):
            self.svc.save_project(
                ProjectSaveRequest.parse(
                    {"project": "nope", "path": str(Path(self.tmp.name) / "x")}
                )
            )
        with self.assertRaises(BadRequest):
            ProjectSaveRequest.parse({"path": "/tmp"})

    def test_without_a_project_nothing_is_written_down(self):
        svc = ViewerService(
            StructureBuffer(self.work / "plain"), self.detector, self.prep, self.gen
        )
        token = svc.buffer_structure(BufferRequest.parse({"id": "1CRN", "data": read("1CRN.pdb")}))[
            "token"
        ]
        svc.find_pockets(PocketRequest.parse({"token": token, "method": "fake"}))
        self.assertFalse(svc.status()["projects"]["available"])
        with self.assertRaises(NotFound):  # a page that names one anyway
            svc.find_pockets(
                PocketRequest.parse({"token": token, "method": "fake", "project": "whatever"})
            )


if __name__ == "__main__":
    unittest.main()
