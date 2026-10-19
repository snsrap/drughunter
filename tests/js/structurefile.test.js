// Reading a structure out of a file the user supplies: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {formatOf, idFromName, titleOf, firstModel, hasAtoms, fileSub}
  from "../../app/js/model/structurefile.js";

const PDB = [
  "HEADER    HYDROLASE",
  "TITLE     HIV-1 PROTEASE WITH AN INHIBITOR BOUND, A VERY LONG TITLE THAT",
  "TITLE    2 CARRIES ON TO A SECOND RECORD",
  "SEQRES   1 A    1  GLY",
  "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N",
  "END",
].join("\n");

const ENSEMBLE = [
  "HEADER    NMR ENSEMBLE",
  "SEQRES   1 A    1  GLY",
  "MODEL        1",
  "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N",
  "TER       2      GLY A   1",
  "ENDMDL",
  "MODEL        2",
  "ATOM      1  N   GLY A   1       9.000   0.000   0.000  1.00  0.00           N",
  "ENDMDL",
  "END",
].join("\n");

const CIF = [
  "data_TEST",
  "_struct.title     'A SMALL TEST STRUCTURE'",
  "loop_",
  "_atom_site.group_PDB",
  "_atom_site.id",
  "_atom_site.type_symbol",
  "_atom_site.label_atom_id",
  "_atom_site.label_comp_id",
  "_atom_site.auth_asym_id",
  "_atom_site.auth_seq_id",
  "_atom_site.Cartn_x",
  "_atom_site.Cartn_y",
  "_atom_site.Cartn_z",
  "_atom_site.pdbx_PDB_model_num",
  "ATOM 1 N N   GLY A 1 0.000 0.000 0.000 1",
  "ATOM 2 C CA  GLY A 1 1.000 0.000 0.000 1",
  "ATOM 3 N N   GLY A 1 0.500 0.500 0.500 2",
  "ATOM 4 C CA  GLY A 1 1.500 0.500 0.500 2",
  "#",
  "loop_",
  "_atom_site_anisotrop.id",
  "_atom_site_anisotrop.U[1][1]",
  "1 100",
  "3 100",
  "#",
].join("\n");

test("formatOf: the extension decides, then the text itself", () => {
  assert.equal(formatOf("1hsg.pdb", ""), "pdb");
  assert.equal(formatOf("1hsg.ent.gz", ""), "pdb");
  assert.equal(formatOf("/home/me/models/1hsg.cif", ""), "cif");
  assert.equal(formatOf("AF-P12345-F1.mmcif", ""), "cif");
  // No extension, or one that says nothing: an mmCIF carries a data_ block.
  assert.equal(formatOf("model", CIF), "cif");
  assert.equal(formatOf("model.txt", PDB), "pdb");
});

test("idFromName: only what a buffer name keeps, so two files cannot collide", () => {
  assert.equal(idFromName("1hsg.pdb"), "1hsg");
  assert.equal(idFromName("/tmp/run 3/AF-P12345-F1-model_v4.cif.gz"), "AF-P12345-F1-model_v4");
  assert.equal(idFromName("frame 12 (holo).pdb"), "frame-12-holo");
  assert.equal(idFromName("../../etc/passwd"), "passwd");   // the name, never a path
  // Nothing left to call it by: ".pdb", or a name that is all punctuation.
  assert.equal(idFromName(".hidden"), "structure");
  assert.equal(idFromName("---"), "structure");
  assert.equal(idFromName(""), "structure");
  assert.match(idFromName("x".repeat(80) + ".pdb"), /^x{40}$/);
  assert.equal(/^[A-Za-z0-9._-]*$/.test(idFromName("α β γ.pdb")), true);
});

test("titleOf: TITLE records joined, and _struct.title", () => {
  assert.equal(titleOf(PDB, "pdb"),
    "HIV-1 PROTEASE WITH AN INHIBITOR BOUND, A VERY LONG TITLE THAT CARRIES ON TO A SECOND RECORD");
  assert.equal(titleOf(CIF, "cif"), "A SMALL TEST STRUCTURE");
  assert.equal(titleOf("ATOM      1  N   GLY A   1", "pdb"), "");
});

test("titleOf: an mmCIF title written as a semicolon text block", () => {
  const cif = ["data_T", "_struct.title", ";Crystal structure of", "something long", ";", "#"].join("\n");
  assert.equal(titleOf(cif, "cif"), "Crystal structure of something long");
});

test("firstModel: a PDB ensemble keeps its header and its first model only", () => {
  const m = firstModel(ENSEMBLE, "pdb");
  assert.equal(m.models, 2);
  const lines = m.text.split("\n");
  assert.deepEqual(lines.filter(l => l.startsWith("ATOM")).length, 1);
  assert.ok(m.text.includes("0.000   0.000   0.000"));
  assert.ok(!m.text.includes("9.000"));
  // The file's own records stay; MODEL/ENDMDL go with the models they framed.
  assert.ok(lines.includes("HEADER    NMR ENSEMBLE"));
  assert.ok(lines.includes("SEQRES   1 A    1  GLY"));
  assert.ok(!m.text.includes("MODEL"));
});

test("firstModel: a single-model file is handed back untouched", () => {
  const m = firstModel(PDB, "pdb");
  assert.equal(m.models, 0);
  assert.equal(m.text, PDB);
});

test("firstModel: an mmCIF drops the later models' rows, and their aniso rows", () => {
  const m = firstModel(CIF, "cif");
  assert.equal(m.models, 2);
  const rows = m.text.split("\n").filter(l => l.startsWith("ATOM"));
  assert.deepEqual(rows.map(r => r.split(/\s+/)[1]), ["1", "2"]);
  assert.ok(m.text.includes("1 100"));
  assert.ok(!m.text.includes("3 100"));
});

test("hasAtoms: an unreadable file is caught before it reaches the tree", () => {
  assert.ok(hasAtoms(PDB, "pdb"));
  assert.ok(hasAtoms(CIF, "cif"));
  assert.ok(!hasAtoms("REMARK   1 nothing here", "pdb"));
  assert.ok(!hasAtoms("{\"not\": \"a structure\"}", "cif"));
});

test("fileSub: where it came from, in place of method and resolution", () => {
  assert.equal(fileSub({fmt: "pdb", models: 1, bytes: 4096}), "PDB file · 4 kB");
  assert.equal(fileSub({fmt: "cif", models: 20, bytes: 2 * 1048576}),
               "mmCIF file · 20 models · first only · 2.0 MB");
});
