import unittest

from proteinviewer.structure import Structure, detect_format

from .helpers import pdb_line, read


class ParseTest(unittest.TestCase):
    def test_pdb_and_mmcif_of_one_entry_agree(self):
        pdb = Structure.from_text(read("1CRN.pdb"), "pdb")
        cif = Structure.from_text(read("1CRN.cif"), "cif")
        self.assertEqual(len(pdb), 327)
        self.assertEqual(len(pdb), len(cif))
        self.assertEqual([a.key for a in pdb.atoms], [a.key for a in cif.atoms])
        self.assertEqual(pdb.chains(), ["A"])

    def test_format_is_sniffed_when_not_declared(self):
        self.assertEqual(detect_format(read("1CRN.cif")), "cif")
        self.assertEqual(detect_format(read("1CRN.pdb")), "pdb")
        self.assertEqual(detect_format("anything", "mmCIF"), "cif")
        self.assertEqual(len(Structure.from_text(read("1CRN.cif"))), 327)

    def test_one_altloc_is_kept(self):
        text = "\n".join([
            pdb_line(1, "CA", "SER", "A", 5, 0, 0, 0, "C", alt="A"),
            pdb_line(2, "CA", "SER", "A", 5, 9, 9, 9, "C", alt="B"),
            pdb_line(3, "CA", "GLY", "A", 6, 1, 1, 1, "C"),
        ])
        s = Structure.from_text(text, "pdb")
        self.assertEqual([(a.res_seq, a.x) for a in s.atoms], [(5, 0.0), (6, 1.0)])

    def test_element_falls_back_to_the_atom_name(self):
        line = pdb_line(1, "CA", "GLY", "A", 1, 0, 0, 0, "")
        self.assertEqual(Structure.from_text(line, "pdb").atoms[0].element, "C")

    def test_no_atoms_is_an_error(self):
        with self.assertRaises(ValueError):
            Structure.from_text("HEADER nothing here\n", "pdb")
        with self.assertRaises(ValueError):
            Structure.from_text("data_x\n_cell.length_a 1\n", "cif")

    def test_cif_quoted_values(self):
        text = "\n".join([
            "data_q", "loop_",
            "_atom_site.group_PDB", "_atom_site.label_atom_id", "_atom_site.auth_comp_id",
            "_atom_site.auth_asym_id", "_atom_site.auth_seq_id",
            "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
            "_atom_site.type_symbol",
            "HETATM \"C1'\" NAG B 7 1.0 2.0 3.0 C",
            "#",
        ])
        (atom,) = Structure.from_text(text).atoms
        self.assertEqual((atom.name, atom.res_name, atom.chain, atom.res_seq, atom.hetero),
                         ("C1'", "NAG", "B", 7, True))


class ReceptorTest(unittest.TestCase):
    def setUp(self):
        self.s = Structure.from_text("\n".join([
            pdb_line(1, "CA", "ALA", "A", 1, 0, 0, 0, "C"),
            pdb_line(2, "H", "ALA", "A", 1, 0, 0, 1, "H"),
            pdb_line(3, "O", "HOH", "A", 100, 5, 5, 5, "O", het=True),
            pdb_line(4, "C1", "LIG", "B", 200, 3, 3, 3, "C", het=True),
            pdb_line(5, "ZN", "ZN", "B", 300, 4, 4, 4, "ZN", het=True),
        ]), "pdb")

    def test_drops_solvent_hydrogens_and_named_ligands(self):
        r = self.s.receptor(drop_extra=frozenset({"LIG"}))
        self.assertEqual([a.res_name for a in r.atoms], ["ALA", "ZN"])

    def test_every_atom_is_written_as_ATOM(self):
        # fpocket ignores HETATM, so a cofactor left as hetero reads as cavity.
        lines = self.s.receptor().to_pdb().splitlines()
        self.assertTrue(all(l.startswith("ATOM  ") for l in lines[:-1]))
        self.assertEqual(lines[-1], "END")
        self.assertEqual(len(Structure.from_text(self.s.receptor().to_pdb())), 3)


if __name__ == "__main__":
    unittest.main()
