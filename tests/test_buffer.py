import os
import tempfile
import unittest
from pathlib import Path

from proteinviewer.buffer import StructureBuffer, safe_token
from proteinviewer.errors import NotFound

from .helpers import read


class BufferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        self.buf = StructureBuffer(self.parent / "root", limit=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_text_is_the_same_entry(self):
        a = self.buf.put("1CRN", read("1CRN.pdb"), "pdb")
        a.pockets = "cached"
        b = self.buf.put("1CRN", read("1CRN.pdb"), "pdb")
        self.assertIs(a, b)
        self.assertEqual(b.pockets, "cached")

    def test_new_text_under_a_name_replaces_it(self):
        a = self.buf.put("x", read("1CRN.pdb"), "pdb")
        b = self.buf.put("x", read("1CRN.cif"), "cif")
        self.assertIsNot(a, b)
        self.assertIs(self.buf.get("x"), b)
        self.assertTrue(b.path.endswith("source.cif"))

    def test_source_file_follows_the_sniffed_format(self):
        e = self.buf.put("x", read("1CRN.cif"))
        self.assertEqual(e.fmt, "cif")
        self.assertTrue(e.path.endswith("source.cif"))

    def test_least_recently_used_is_evicted_with_its_files(self):
        a = self.buf.put("a", read("1CRN.pdb"))
        self.buf.put("b", read("1CRN.pdb"))
        self.buf.get("a")  # a is now the most recent
        self.buf.put("c", read("1CRN.pdb"))
        self.assertIsNone(self.buf.get("b"))
        self.assertIs(self.buf.get("a"), a)
        self.assertEqual(sorted(os.listdir(self.buf.root)), ["a", "c"])

    def test_unknown_token(self):
        with self.assertRaises(NotFound):
            self.buf.require("nope")

    def test_bad_text_raises_value_error_and_keeps_nothing(self):
        with self.assertRaises(ValueError):
            self.buf.put("x", "not a structure")
        self.assertEqual(len(self.buf), 0)

    def test_dot_names_stay_inside_the_buffer(self):
        # Regression: ".." used to pass the filter, and the old buffer then
        # ran rmtree on root/.. -- the parent of the buffer.
        canary = self.parent / "CANARY"
        canary.write_text("still here")
        for token in ("..", ".", "../..", ".hidden", "a/../.."):
            e = self.buf.put(token, read("1CRN.pdb"))
            self.assertEqual(os.path.dirname(e.workdir), self.buf.root, token)
        self.assertTrue(canary.exists())

    def test_safe_token(self):
        self.assertEqual(safe_token("1HSG.sel-abc"), "1HSG.sel-abc")
        self.assertEqual(safe_token("we ird/id"), "we_ird_id")
        self.assertEqual(safe_token(".."), "structure")
        self.assertEqual(len(safe_token("x" * 200)), 64)

    def test_locks_are_per_purpose(self):
        e = self.buf.put("x", read("1CRN.pdb"))
        self.assertIs(e.lock("pockets"), e.lock("pockets"))
        self.assertIsNot(e.lock("pockets"), e.lock("prep"))

    def test_close_removes_everything(self):
        self.buf.put("x", read("1CRN.pdb"))
        self.buf.close()
        self.assertFalse(os.path.exists(self.buf.root))


if __name__ == "__main__":
    unittest.main()
