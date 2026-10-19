// Unit tests for the page's pure model modules: run with `npm test`
// (node --test), no install needed.
import {test} from "node:test";
import assert from "node:assert/strict";

import {buildTree, hider, polymerLike, ligandsIn} from "../../app/js/model/tree.js";
import {pdbSubset, cifSubset, leftOut, leafSels, inModel} from "../../app/js/model/subset.js";
import {scope, summarize, scopeStructure} from "../../app/js/model/scope.js";

/* Atoms the way 3Dmol hands them over. */
const atom = (chain, resi, resn, name, het = false, extra = {}) =>
  ({chain, resi, resn, atom: name, hetflag: het, icode: "", ...extra});
const backbone = (chain, resi, resn) =>
  ["N", "CA", "C", "O"].map(n => atom(chain, resi, resn, n));

function entity(uid, atoms, extra = {}){
  const e = {uid, id: uid.toUpperCase(), visible: true, ...extra};
  buildTree(e, atoms);
  return e;
}

test("tree: chains, modified residues, caps, ligands and waters", () => {
  const e = entity("e1", [
    ...backbone("A", 1, "GLY"), ...backbone("A", 2, "MSE").map(a => ({...a, hetflag: true})),
    atom("A", 3, "NME", "N", true),
    atom("A", 900, "MK1", "C1", true),
    atom("A", 1001, "HOH", "O", true), atom("B", 1002, "HOH", "O", true),
    ...backbone("B", 1, "ALA"),
  ]);
  const kinds = e.tree.kids.map(k => [k.kind, k.name, k.count]);
  assert.deepEqual(kinds, [["chain", "Chain A", "3 res"], ["chain", "Chain B", "1 res"],
                           ["ligs", "Ligands", 1], ["waters", "Water", 2]]);
  assert.equal(e.tree.count, "2 chains");
  // MSE is HETATM but has a backbone: a residue of chain A, not a ligand.
  assert.deepEqual(e.tree.kids[0].kids.map(n => n.name), ["GLY 1", "MSE 2", "NME 3"]);
  assert.equal(e.nodes["e1/A:900:MK1"].kind, "lig");
  // Waters start hidden; only the structure row starts open.
  assert.deepEqual(e.hidden, {"e1/waters": true});
  assert.deepEqual(e.open, {"e1/root": true});
  assert.deepEqual(e.orig, {A: [1, 2, 3], B: [1]});
});

test("tree: rebuilding keeps folding and hiding for nodes that survive", () => {
  const atoms = [...backbone("A", 1, "GLY"), ...backbone("A", 2, "SER")];
  const e = entity("e1", atoms);
  e.open["e1/chain:A"] = true;
  e.hidden["e1/A:2:SER"] = true;
  e.hidden["e1/gone"] = true;
  buildTree(e, atoms.slice(0, 4).concat(backbone("A", 2, "SER")));
  assert.equal(e.open["e1/chain:A"], true);
  assert.deepEqual(e.hidden, {"e1/A:2:SER": true});
});

test("hider: a node is hidden by itself, a folder above it, or its structure", () => {
  const e = entity("e1", [...backbone("A", 1, "GLY"), atom("A", 9, "HOH", "O", true)]);
  const water = e.nodes["e1/A:9:HOH"], gly = e.nodes["e1/A:1:GLY"];
  assert.equal(hider(e, water).key, "e1/waters");
  assert.equal(hider(e, gly), null);
  e.visible = false;
  assert.equal(hider(e, gly).kind, "root");
});

test("ligandsIn: bound ligands, not the additives the server lists, nor modified residues", () => {
  const additives = ["HOH", "GOL", "SO4"];     // the server's list, from pocketbench
  const e = entity("e1", [
    ...backbone("A", 1, "GLY"),
    ...backbone("A", 2, "MSE").map(a => ({...a, hetflag: true})),   // modified residue
    atom("A", 900, "MK1", "C1", true),
    atom("B", 901, "MK1", "C1", true),                              // the same ligand twice
    atom("A", 902, "HEM", "FE", true),
    atom("A", 903, "GOL", "C1", true),                              // cryoprotectant
    atom("A", 904, "SO4", "S", true),                               // crystallisation salt
    atom("A", 1001, "HOH", "O", true),
  ]);
  assert.deepEqual(ligandsIn(e, [], additives), ["HEM", "MK1"]);
  // On a selection, only the ligands it covers.
  assert.deepEqual(ligandsIn(e, [e.nodes["e1/B:901:MK1"]], additives), ["MK1"]);
  assert.deepEqual(ligandsIn(e, [e.nodes["e1/chain:A"]], additives), []);
  assert.deepEqual(ligandsIn(e, [e.nodes["e1/ligs"]], additives), ["HEM", "MK1"]);
  // Before the server has said, nothing is held back.
  assert.deepEqual(ligandsIn(e, []), ["GOL", "HEM", "MK1", "SO4"]);
});

test("polymerLike", () => {
  assert.ok(polymerLike({N: 1, CA: 1, C: 1}, "SEP"));
  assert.ok(polymerLike({P: 1, "C1'": 1}, "DA"));
  assert.ok(polymerLike({}, "ACE"));
  assert.ok(!polymerLike({C1: 1}, "LIG"));
});

const PDB = [
  "HEADER    TEST",
  "SEQRES   1 A    3  GLY SER ALA",
  "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N",
  "ANISOU    1  N   GLY A   1     100    100    100      0      0      0       N",
  "ATOM      2  CA  SER A   2       1.000   0.000   0.000  1.00  0.00           C",
  "ATOM      3  CA  ALA A   3       2.000   0.000   0.000  1.00  0.00           C",
  "CONECT    1    2",
  "CONECT    2    3",
  "END",
].join("\n");
const pdbAtoms = PDB.split("\n").filter(l => l.startsWith("ATOM")).map(l => ({pdbline: l}));

test("pdbSubset keeps the header, the chosen atoms with their ANISOU, and bonds among them", () => {
  const out = pdbSubset(PDB, [pdbAtoms[0], pdbAtoms[1]]).split("\n");
  assert.deepEqual(out, [
    "HEADER    TEST", "SEQRES   1 A    3  GLY SER ALA",
    PDB.split("\n")[2], PDB.split("\n")[3], PDB.split("\n")[4],
    "CONECT    1    2", "END", ""]);
});

const CIF = [
  "data_t", "_cell.length_a 10", "loop_",
  "_atom_site.group_PDB", "_atom_site.id", "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
  "ATOM 1 0.000 0.000 0.000", "ATOM 2 1.500 0.000 0.000", "ATOM 3 3.000 0.000 0.000", "#",
  "loop_", "_atom_site_anisotrop.id", "_atom_site_anisotrop.U[1][1]", "1 0.1", "2 0.2", "#",
].join("\n");

test("cifSubset matches rows by coordinate and drops orphaned anisotropic rows", () => {
  const out = cifSubset(CIF, [{x: 1.5, y: 0, z: 0}]).split("\n");
  assert.ok(out.includes("_cell.length_a 10"));
  assert.ok(out.includes("ATOM 2 1.500 0.000 0.000"));
  assert.ok(!out.includes("ATOM 1 0.000 0.000 0.000"));
  assert.ok(out.includes("2 0.2") && !out.includes("1 0.1"));
  assert.throws(() => cifSubset("data_x\n", []), /no _atom_site loop/);
});

test("leftOut lists loaded residues a cut lacks, per chain the cut still has", () => {
  const e = {orig: {A: [1, 2, 3, 4], B: [1, 2]}};
  assert.deepEqual(leftOut(e, [{chain: "A", resi: 1}, {chain: "A", resi: 4}]), {A: [2, 3]});
  assert.equal(leftOut(e, [{chain: "B", resi: 1}, {chain: "B", resi: 2}]), null);
});

test("leafSels and inModel", () => {
  assert.deepEqual(leafSels({or: [{chain: "A"}, {or: [{resi: 1}, {resi: 2}]}]}),
                   [{chain: "A"}, {resi: 1}, {resi: 2}]);
  assert.deepEqual(inModel({chain: "A"}, "m"), {model: "m", chain: "A"});
});

function twoStructures(){
  const a = entity("e1", [...backbone("A", 1, "GLY"), ...backbone("A", 2, "SER"),
                          atom("A", 900, "MK1", "C1", true)]);
  const b = entity("e2", [...backbone("B", 5, "ALA")]);
  return {ents: [a, b], picks: []};
}

test("scope: nothing picked runs on the only visible structure, else asks", () => {
  const st = twoStructures();
  assert.equal(scope(st).soft, true);
  st.ents[1].visible = false;
  assert.equal(scope(st).label, "E1");
  assert.equal(scope({ents: [], picks: []}).err, "Fetch a PDB entry, or open a file, above.");
});

test("scope: picks name the part; two structures is an error", () => {
  const st = twoStructures();
  st.picks = ["e1/chain:A", "e1/A:900:MK1"];
  const sc = scope(st);
  assert.equal(sc.whole, false);
  assert.equal(sc.label, "E1 · chain A, MK1 900");
  st.picks.push("e2/root");
  assert.match(scope(st).err, /spans E1 and E2/);
  st.picks = ["e1/root", "e1/chain:A"];
  assert.equal(scope(st).whole, true);
});

test("summarize collapses long lists", () => {
  const res = n => ({kind: "res", name: "R" + n});
  assert.equal(summarize([res(1), res(2)]), "R1, R2");
  assert.equal(summarize([res(1), res(2), res(3), {kind: "waters"}]), "3 residues, all waters");
});

test("scopeStructure posts a cut under a stable per-selection token", () => {
  const st = twoStructures(), e = st.ents[0];
  Object.assign(e, {data: PDB, fmt: "pdb",
    model: {selectedAtoms: sel => (sel.or ? [pdbAtoms[0]] : pdbAtoms).map(a => ({...a, chain: "A", resi: 1}))}});
  st.picks = ["e1/A:1:GLY"];
  const one = scopeStructure(scope(st)), again = scopeStructure(scope(st));
  assert.match(one.token, /^E1\.sel-[0-9a-z]+$/);
  assert.equal(one.token, again.token);
  assert.equal(one.data.split("\n").filter(l => l.startsWith("ATOM")).length, 1);
  assert.deepEqual(one.noRebuild, {A: [2]});
  st.picks = [];
  st.ents[1].visible = false;
  assert.equal(scopeStructure(scope(st)).token, "E1");
});
