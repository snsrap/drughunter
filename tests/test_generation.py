import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from proteinviewer.errors import ToolFailed, ToolUnavailable
from proteinviewer.generation import Job, NeatPocket, ScoreJob, Scoring, _last_json_line
from proteinviewer.prep import QuickPrep, residue_ranges

from .helpers import fake_executable


class JobProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.job = Job("j1", self.dir, {"n": 2, "pockets": [{"rank": 1}, {"rank": 3}]})

    def tearDown(self):
        self.tmp.cleanup()

    def child_writes(self, **progress):
        (self.dir / "progress.json").write_text(json.dumps(progress))

    def test_child_stages_are_passed_on_while_running(self):
        self.job.state = "running"
        self.child_writes(state="generating", done=1, total=4)
        p = self.job.progress()
        self.assertEqual((p["state"], p["done"], p["ranks"]), ("generating", 1, [1, 3]))

    def test_child_done_is_not_done_until_its_results_are_read(self):
        # Regression: the child writes "done" before it exits; reporting that
        # sent the viewer for molecules the job did not have yet.
        self.job.state = "running"
        self.child_writes(state="done", done=4, results=[{"rank": 1, "sdf": "p.sdf"}])
        self.assertEqual(self.job.progress()["state"], "finishing")
        self.assertEqual(self.job.molecules(), {})

        (self.dir / "p.sdf").write_text("mol\n$$$$\n")
        self.job._finish("done", results=[{"rank": 1, "sdf": "p.sdf"}])
        self.assertEqual(self.job.progress()["state"], "done")
        self.assertEqual(self.job.molecules(), {"1": "mol\n$$$$\n"})

    def test_a_failed_child_is_reported_as_failed(self):
        self.job.state = "running"
        self.child_writes(state="error", error="boom")
        p = self.job.progress()
        self.assertEqual((p["state"], p["error"]), ("error", "boom"))

    def test_half_written_files_are_ignored(self):
        (self.dir / "progress.json").write_text('{"state": "gen')
        (self.dir / "molecules.json").write_text("[{")
        self.assertEqual(self.job.progress()["state"], "queued")
        self.assertEqual(self.job.table(), [])


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # A NEAT-POCKET checkout whose script just reports success.
        neat = root / "NEAT-POCKET"
        (neat / "scripts").mkdir(parents=True)
        (neat / "scripts" / "generation_from_pdb.py").write_text("")
        script = root / "pocketgen"
        script.mkdir()
        self.script = fake_executable(
            script,
            "neat_pocket.py",
            r"""
            import json, sys
            spec = json.load(open(sys.argv[sys.argv.index("--job") + 1]))
            assert sys.argv[sys.argv.index("--neat-dir") + 1]
            print("noise")
            print(json.dumps({"ok": True, "results": [{"rank": p["rank"]} for p in spec["pockets"]]}))
        """,
        )
        patcher = mock.patch("proteinviewer.generation.SCRIPT", Path(self.script))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.structure = root / "s.pdb"
        self.structure.write_text("ATOM\n")
        self.runner = NeatPocket(
            root / "jobs", neat_dir=str(neat), python=__import__("sys").executable, limit=1
        )

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, job):
        deadline = time.time() + 30
        while not job.done and time.time() < deadline:
            time.sleep(0.05)
        return job

    def test_a_job_runs_in_its_own_directory_with_its_own_copy(self):
        job = self.wait(self.runner.start(str(self.structure), [{"rank": 2, "residues": []}]))
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.results, [{"rank": 2}])
        self.assertTrue((job.out_dir / "source.pdb").is_file())
        self.assertEqual(
            json.loads((job.out_dir / "job.json").read_text())["structure"],
            str(job.out_dir / "source.pdb"),
        )

    def test_finished_jobs_past_the_limit_are_forgotten(self):
        first = self.wait(self.runner.start(str(self.structure), [{"rank": 1, "residues": []}]))
        second = self.runner.start(str(self.structure), [{"rank": 1, "residues": []}])
        self.assertIsNone(self.runner.get(first.id))
        self.assertFalse(first.out_dir.exists())
        self.assertIs(self.runner.get(second.id), second)
        self.wait(second)

    def test_unavailable(self):
        self.runner.neat_dir = None
        with self.assertRaises(ToolUnavailable):
            self.runner.start(str(self.structure), [])

    def test_last_json_line(self):
        self.assertEqual(_last_json_line('x\n{"ok": true}\n{bad}\n'), {"ok": True})
        self.assertIsNone(_last_json_line("no json here"))


class FakeScorer:
    """pocketbench as the scoring runner sees it: one scorer that works,
    except on the pocket ranks it is told to fail on.
    """

    def __init__(self, fail_on=(), available=True):
        self.fail_on = set(fail_on)
        self.installed = available
        self.calls = []

    def scorers(self):
        return {"smina": {"available": self.installed, "status": "smina: ok"}}

    def score_poses(
        self,
        receptor,
        poses,
        workdir,
        *,
        mode,
        padding,
        site=None,
        site_rank=None,
        scorer="",
        output=None,
        ligands=(),
    ):
        self.calls.append(
            {
                "receptor": receptor,
                "poses": poses,
                "mode": mode,
                "padding": padding,
                "site": site,
                "rank": site_rank,
                "ligands": tuple(ligands),
            }
        )
        if site_rank in self.fail_on:
            raise ToolFailed("smina could not score pocket %s" % site_rank, "smina")
        Path(output).write_text("relaxed\n$$$$\n")
        return {
            "poses": [{"index": 0, "name": "pocket%s_1" % site_rank, "score": -5.5, "le": 0.25}],
            "meta": {"scorer": "smina 1", "mode": mode, "minimized": output, "command": "smina"},
        }


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.receptor = self.root / "receptor.pdb"
        self.receptor.write_text("ATOM\n")

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, job):
        deadline = time.time() + 30
        while not job.done and time.time() < deadline:
            time.sleep(0.01)
        return job

    def start(self, scorer, ranks=(1, 2), **kw):
        runner = Scoring(scorer, self.root / "scores")
        poses = {r: "pose %d\n$$$$\n" % r for r in ranks}
        opts = {"mode": "minimize", "padding": 4}
        opts.update(kw)
        return runner, self.wait(runner.start(str(self.receptor), poses, "SPHERES\n", **opts))

    def test_every_pocket_is_scored_in_its_own_box(self):
        scorer = FakeScorer()
        _, job = self.start(scorer, ligands=("HEM",))
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(sorted(job.payload()["scores"]), ["1", "2"])
        self.assertEqual(job.payload()["scores"]["1"][0]["score"], -5.5)
        # each pocket's own points set its box, and the poses are kept beside
        # the numbers
        self.assertEqual([c["rank"] for c in scorer.calls], [1, 2])
        self.assertTrue(all(c["site"].endswith("pockets.spheres.pdb") for c in scorer.calls))
        self.assertEqual(scorer.calls[0]["ligands"], ("HEM",))
        self.assertTrue((job.out_dir / "pocket-1.sdf").is_file())
        self.assertTrue((job.out_dir / "pocket-1.min.sdf").is_file())
        written = json.loads((job.out_dir / "scores.json").read_text())
        self.assertEqual(written["scorers"]["1"]["mode"], "minimize")

    def test_one_pocket_failing_keeps_the_others(self):
        _, job = self.start(FakeScorer(fail_on={2}))
        self.assertEqual(job.state, "done")
        self.assertEqual(sorted(job.payload()["scores"]), ["1"])
        self.assertIn("pocket 2", job.progress()["error"])

    def test_a_scorer_that_fails_everywhere_fails_the_job(self):
        _, job = self.start(FakeScorer(fail_on={1, 2}))
        self.assertEqual(job.state, "error")
        self.assertIn("pocket 1", job.error)

    def test_progress_says_which_pocket_is_being_scored(self):
        job = ScoreJob("s1", self.root, {"pockets": [{"rank": 1}, {"rank": 3}]})
        job.state = "running"
        job.stage("scoring", done=1, total=2, current=3)
        p = job.progress()
        self.assertEqual(
            (p["state"], p["done"], p["current"], p["ranks"]), ("scoring", 1, 3, [1, 3])
        )

    def test_without_a_scorer_there_is_nothing_to_start(self):
        runner = Scoring(FakeScorer(available=False), self.root / "scores")
        self.assertFalse(runner.available)
        self.assertIn("NOT FOUND", runner.status_line())
        with self.assertRaises(ToolUnavailable):
            runner.start(str(self.receptor), {1: "x"}, "", mode="rescore", padding=4)

    def test_finished_jobs_past_the_limit_are_forgotten(self):
        runner = Scoring(FakeScorer(), self.root / "scores", limit=1)
        first = self.wait(runner.start(str(self.receptor), {1: "x"}, "", mode="rescore", padding=4))
        self.wait(runner.start(str(self.receptor), {1: "x"}, "", mode="rescore", padding=4))
        self.assertIsNone(runner.get(first.id))
        self.assertFalse(first.out_dir.exists())


class PrepTest(unittest.TestCase):
    def test_residue_ranges(self):
        self.assertEqual(residue_ranges([12, 5, 6, 7, 8, 8]), "5-8,12")
        self.assertEqual(residue_ranges([]), "")

    def test_an_interpreter_without_quickprep_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep = QuickPrep(fake_executable(tmp, "python", "raise SystemExit(1)"))
            self.assertFalse(prep.available)
            with self.assertRaises(ToolUnavailable):
                prep.run("in.pdb", tmp)


if __name__ == "__main__":
    unittest.main()
