import random
import unittest

from proteinviewer.pockets import DetectionResult, Pocket, select_residues
from proteinviewer.structure import STANDARD_AA, Structure

from .helpers import pdb_line, read


def brute_force(structure, centers, cutoff):
    """select_residues without the grid: every atom against every point."""
    hit = {a.key for a in structure.atoms
           if any((a.x - c[0]) ** 2 + (a.y - c[1]) ** 2 + (a.z - c[2]) ** 2 <= cutoff ** 2
                  for c in centers)}
    hit &= {a.key for a in structure.atoms if a.res_name in STANDARD_AA}
    return sorted(hit), sum(1 for a in structure.atoms if a.key in hit)


class SelectResiduesTest(unittest.TestCase):
    def test_grid_matches_brute_force(self):
        s = Structure.from_text(read("1CRN.pdb"))
        rng = random.Random(7)
        atoms = s.atoms
        for cutoff in (3.0, 6.0, 8.5):
            centers = [(a.x + rng.uniform(-3, 3), a.y + rng.uniform(-3, 3),
                        a.z + rng.uniform(-3, 3)) for a in rng.sample(atoms, 12)]
            self.assertEqual(select_residues(s, centers, cutoff),
                             brute_force(s, centers, cutoff))

    def test_no_points_no_residues(self):
        s = Structure.from_text(read("1CRN.pdb"))
        self.assertEqual(select_residues(s, []), ([], 0))

    def test_whole_residues_and_standard_only(self):
        s = Structure.from_text("\n".join([
            pdb_line(1, "N", "SER", "A", 1, 0, 0, 0, "N"),
            pdb_line(2, "OG", "SER", "A", 1, 30, 0, 0, "O"),   # far, same residue
            pdb_line(3, "C1", "LIG", "A", 2, 0.5, 0, 0, "C", het=True),
        ]), "pdb")
        residues, n_atoms = select_residues(s, [(0, 0, 0)], 2.0)
        self.assertEqual(residues, [("A", 1)])
        self.assertEqual(n_atoms, 2)  # the whole residue, the far atom too

    def test_chain_is_part_of_residue_identity(self):
        s = Structure.from_text("\n".join([
            pdb_line(1, "CA", "GLY", "A", 10, 0, 0, 0, "C"),
            pdb_line(2, "CA", "GLY", "B", 10, 50, 0, 0, "C"),
        ]), "pdb")
        self.assertEqual(select_residues(s, [(0, 0, 0)], 3.0)[0], [("A", 10)])


class PocketTest(unittest.TestCase):
    def pocket(self, **kw):
        base = dict(rank=1, score=0.5, druggability=0.25, volume=321.04,
                    centers=[(0, 0, 0), (2, 0, 0)], radii=[3.0, 3.5])
        base.update(kw)
        return Pocket(**base)

    def test_selection_is_grouped_by_chain(self):
        p = self.pocket(residues=[("B", 3), ("A", 9), ("A", 2)])
        self.assertEqual(p.selection(), [{"chain": "A", "resi": [2, 9]},
                                         {"chain": "B", "resi": [3]}])

    def test_unreported_numbers_go_out_as_null(self):
        d = self.pocket(score=float("nan")).as_dict()
        self.assertIsNone(d["score"])
        self.assertIsNone(d["persistence"])
        self.assertEqual(d["volume"], 321.0)
        self.assertEqual(d["centroid"], [1.0, 0.0, 0.0])
        self.assertEqual(d["spheres"][1], [2, 0, 0, 3.5])

    def test_result_meta_carries_detector_extras(self):
        r = DetectionResult("x", [self.pocket()], ["x", "-f", "in.pdb"], 1.234, 10, 6.0,
                            extra={"backend": "nma"})
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].rank, 1)
        self.assertEqual(r.meta(), {"tool": "x", "command": "x -f in.pdb", "elapsed": 1.23,
                                    "receptorAtoms": 10, "cutoff": 6.0, "nPockets": 1,
                                    "backend": "nma"})


if __name__ == "__main__":
    unittest.main()
