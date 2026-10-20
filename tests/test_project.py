"""Session output directories: what goes in them, and where they may be
copied.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from proteinviewer.errors import BadRequest, NotFound
from proteinviewer.project import (
    Project,
    ProjectStore,
    csv_text,
    free_dir,
    resolve_destination,
    safe_name,
    safe_relpath,
)


class NamesTest(unittest.TestCase):
    def test_safe_name_leaves_nothing_that_could_be_a_path(self):
        self.assertEqual(safe_name("1HSG"), "1HSG")
        self.assertEqual(safe_name("1HSG.sel-1a2b3c"), "1HSG.sel-1a2b3c")
        self.assertEqual(safe_name("my structure (holo)"), "my-structure-holo")
        # A name is one component: no separators, and never the parent.
        self.assertEqual(safe_name("../../etc/passwd"), "etc-passwd")
        self.assertEqual(safe_name(".."), "session")
        self.assertEqual(safe_name(""), "session")
        self.assertEqual(safe_name("", "x"), "x")
        self.assertEqual(len(safe_name("x" * 90)), 48)

    def test_safe_relpath_refuses_anything_outside_the_project(self):
        self.assertEqual(safe_relpath("pockets/1HSG.fake.json"), Path("pockets") / "1HSG.fake.json")
        for bad in [
            "../outside",
            "pockets/../../x",
            "/etc/passwd",
            "pockets/../x",
            "",
            "   ",
            "a/../b",
        ]:
            with self.assertRaises(BadRequest, msg=bad):
                safe_relpath(bad)

    def test_csv_text_quotes_what_needs_it_and_leaves_gaps_empty(self):
        rows = [{"a": 1, "b": "x,y"}, {"a": None, "b": 'say "hi"'}]
        self.assertEqual(csv_text(rows, ["a", "b", "c"]), 'a,b,c\n1,"x,y",\n,"say ""hi""",\n')


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = Project("2026-10-18-1HSG", self.root / "2026-10-18-1HSG", api=3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_artifact_is_written_and_recorded(self):
        self.project.put_text(
            "pockets/1HSG.fake.json", "{}", kind="pockets", tool="fake", command="fake -f x.pdb"
        )
        self.assertEqual((self.project.path / "pockets" / "1HSG.fake.json").read_text(), "{}")
        ((entry,)) = [
            a for a in self.project.manifest()["artifacts"] if a["path"] == "pockets/1HSG.fake.json"
        ]
        self.assertEqual(entry["kind"], "pockets")
        self.assertEqual(entry["command"], "fake -f x.pdb")
        self.assertIn("when", entry)
        self.assertEqual(self.project.manifest()["api"], 3)

    def test_rewriting_an_artifact_records_it_once(self):
        self.project.put_text("a.txt", "1", kind="x")
        self.project.put_text("a.txt", "2", kind="y")
        artifacts = self.project.manifest()["artifacts"]
        self.assertEqual([a["path"] for a in artifacts], ["a.txt"])
        self.assertEqual(artifacts[0]["kind"], "y")
        self.assertEqual((self.project.path / "a.txt").read_text(), "2")

    def test_a_tools_file_is_copied_in(self):
        src = self.root / "source.pdb"
        src.write_text("ATOM\n")
        self.project.put_file("structures/1HSG.pdb", src, kind="structure")
        self.assertEqual((self.project.path / "structures" / "1HSG.pdb").read_text(), "ATOM\n")

    def test_describe_counts_what_is_there(self):
        self.project.put_text("a.txt", "12345", kind="x")
        out = self.project.describe()
        self.assertEqual((out["id"], out["files"]), ("2026-10-18-1HSG", 2))  # and the manifest
        self.assertGreater(out["bytes"], 5)

    def test_saving_copies_the_whole_project(self):
        self.project.put_text("structures/1HSG.pdb", "ATOM\n", kind="structure")
        self.project.put_text("molecules/j1/pocket-1.sdf", "mol\n$$$$\n", kind="molecules")
        out = self.project.copy_to(self.root / "out")
        self.assertEqual(out["path"], str(self.root / "out"))
        self.assertEqual(
            sorted(out["files"]),
            ["manifest.json", "molecules/j1/pocket-1.sdf", "structures/1HSG.pdb"],
        )
        self.assertEqual((self.root / "out" / "structures" / "1HSG.pdb").read_text(), "ATOM\n")

    def test_a_directory_with_work_in_it_is_never_written_into(self):
        self.project.put_text("a.txt", "new\n", kind="x")
        dest = self.root / "out"
        (dest / "sub").mkdir(parents=True)
        (dest / "a.txt").write_text("someone else's\n")
        out = self.project.copy_to(dest)
        self.assertEqual(out["path"], str(self.root / "out-2"))
        self.assertEqual((dest / "a.txt").read_text(), "someone else's\n")
        # ... unless that is what was asked for, and then only the files
        # in the way are replaced.
        again = self.project.copy_to(dest, overwrite=True)
        self.assertEqual(again["path"], str(dest))
        self.assertEqual((dest / "a.txt").read_text(), "new\n")
        self.assertTrue((dest / "sub").is_dir())

    def test_an_empty_directory_is_used_as_it_is(self):
        dest = self.root / "empty"
        dest.mkdir()
        self.assertEqual(free_dir(dest), dest)
        self.assertEqual(free_dir(self.root / "never-made"), self.root / "never-made")
        self.assertEqual(self.project.copy_to(dest)["path"], str(dest))
        # Saving again does not write over what the last save put there.
        self.assertEqual(free_dir(dest), self.root / "empty-2")


class DestinationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_path_the_user_typed(self):
        self.assertEqual(resolve_destination(str(self.root / "out")), self.root / "out")
        self.assertEqual(resolve_destination(" %s/out/ " % self.root), self.root / "out")
        self.assertEqual(resolve_destination("~/x"), Path(os.path.expanduser("~")) / "x")

    def test_what_is_refused(self):
        file_path = self.root / "a.pdb"
        file_path.write_text("ATOM\n")
        for bad in [
            "",
            "   ",
            "relative/path",
            str(self.root / "no" / "such" / "parent"),
            str(file_path),
        ]:
            with self.assertRaises(BadRequest, msg=bad):
                resolve_destination(bad)

    def test_the_servers_own_working_tree_is_refused(self):
        work = self.root / "work"
        (work / "structures").mkdir(parents=True)
        for bad in [work, work / "structures", work / "structures" / "deeper"]:
            with self.assertRaises(BadRequest, msg=str(bad)):
                resolve_destination(str(bad), forbid=[work])
        # Beside it is fine.
        self.assertEqual(
            resolve_destination(str(self.root / "out"), forbid=[work]), self.root / "out"
        )


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.tmp.name) / "projects", api=3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_project_is_named_for_the_day_and_what_is_loaded(self):
        project = self.store.create("1HSG")
        self.assertEqual(project.id, time.strftime("%Y-%m-%d") + "-1HSG")
        self.assertTrue(project.path.is_dir())
        self.assertEqual(json.loads(project.manifest_path.read_text())["project"], project.id)
        # Two sessions on the same day, with the same structure, stay apart.
        self.assertEqual(self.store.create("1HSG").id, project.id + "-2")
        self.assertEqual(self.store.create(None).id, time.strftime("%Y-%m-%d") + "-session")

    def test_a_project_is_found_again_after_a_restart(self):
        project = self.store.create("1HSG")
        project.put_text("a.txt", "x", kind="x")
        later = ProjectStore(self.store.root, api=3)  # a new server run
        found = later.require(project.id)
        self.assertEqual(found.path, project.path)
        self.assertEqual((found.path / "a.txt").read_text(), "x")
        self.assertIs(later.get(project.id), found)  # and kept open

    def test_an_id_that_is_not_a_project_is_not_found(self):
        self.store.create("1HSG")
        for bad in ["", "nope", "../..", "/etc", "a/b"]:
            self.assertIsNone(self.store.get(bad), bad)
            with self.assertRaises(NotFound, msg=bad):
                self.store.require(bad)


if __name__ == "__main__":
    unittest.main()
