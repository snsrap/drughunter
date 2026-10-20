import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from proteinviewer.errors import ToolUnavailable
from proteinviewer.generation import Job, NeatPocket, _last_json_line
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
        self.script = fake_executable(script, "neat_pocket.py", r"""
            import json, sys
            spec = json.load(open(sys.argv[sys.argv.index("--job") + 1]))
            assert sys.argv[sys.argv.index("--neat-dir") + 1]
            print("noise")
            print(json.dumps({"ok": True, "results": [{"rank": p["rank"]} for p in spec["pockets"]]}))
        """)
        patcher = mock.patch("proteinviewer.generation.SCRIPT", Path(self.script))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.structure = root / "s.pdb"
        self.structure.write_text("ATOM\n")
        self.runner = NeatPocket(root / "jobs", neat_dir=str(neat),
                                 python=__import__("sys").executable, limit=1)

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
        self.assertEqual(json.loads((job.out_dir / "job.json").read_text())["structure"],
                         str(job.out_dir / "source.pdb"))

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
