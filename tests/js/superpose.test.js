// Superposing one structure onto another: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import {BLOSUM62, align, fit, apply, chainsOf, superpose, moveText, related}
  from "../../app/js/model/superpose.js";
import {cifLoops, cifCol, cifTokens, cifSubset, ATOM_SITE} from "../../app/js/model/subset.js";

const DATA = new URL("../data/", import.meta.url);
const THREE = {A: "ALA", R: "ARG", N: "ASN", D: "ASP", C: "CYS", Q: "GLN", E: "GLU", G: "GLY",
               H: "HIS", I: "ILE", L: "LEU", K: "LYS", M: "MET", F: "PHE", P: "PRO", S: "SER",
               T: "THR", W: "TRP", Y: "TYR", V: "VAL"};

/* A seeded generator, so a failure can be run again. */
function rng(seed){ return () => (seed = (seed * 16807) % 2147483647) / 2147483647; }
function rotation(axis, angle){
  const l = Math.hypot(...axis), [x, y, z] = axis.map(v => v / l),
        c = Math.cos(angle), s = Math.sin(angle), C = 1 - c;
  return [c + x * x * C, x * y * C - z * s, x * z * C + y * s,
          y * x * C + z * s, c + y * y * C, y * z * C - x * s,
          z * x * C - y * s, z * y * C + x * s, c + z * z * C];
}
const T0 = {R: rotation([1, 2, 3], 1.1), t: [12, -7, 30]};
const det = R => R[0] * (R[4] * R[8] - R[5] * R[7]) - R[1] * (R[3] * R[8] - R[5] * R[6]) +
                 R[2] * (R[3] * R[7] - R[4] * R[6]);
const close = (a, b, eps = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < eps);

/* A Cα trace 3.8 Å a step, as atoms the way 3Dmol hands them over: N, CA and
   C per residue. */
function trace(n, seed){
  const r = rng(seed), out = [[0, 0, 0]];
  while (out.length < n){
    const v = [r() - 0.5, r() - 0.5, r() - 0.5], l = Math.hypot(...v), p = out[out.length - 1];
    out.push(p.map((x, k) => x + 3.8 * v[k] / l));
  }
  return out;
}
function chain(id, seq, cas, first = 1){
  const atoms = [];
  [...seq].forEach((aa, i) => {
    const [x, y, z] = cas[i], base = {chain: id, resi: first + i, resn: THREE[aa], icode: "", hetflag: false};
    atoms.push({...base, atom: "N", x: x - 1, y, z}, {...base, atom: "CA", x, y, z},
               {...base, atom: "C", x: x + 1, y, z});
  });
  return atoms;
}
const moved = (atoms, T) => atoms.map(a => {
  const [x, y, z] = apply(T, [a.x, a.y, a.z]);
  return {...a, x, y, z};
});

const SEQ = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQ";

test("BLOSUM62 is symmetric, with the diagonal it is known by", () => {
  for (let i = 0; i < BLOSUM62.length; i++)
    for (let j = 0; j < BLOSUM62.length; j++) assert.equal(BLOSUM62[i][j], BLOSUM62[j][i], i + "," + j);
  // A R N ... W Y V X
  assert.deepEqual(BLOSUM62.map((r, i) => r[i]),
                   [4, 5, 6, 6, 9, 5, 5, 6, 8, 4, 4, 5, 5, 6, 7, 4, 5, 11, 7, 4, -1]);
});

test("align: end gaps are free, an insertion opens one gap", () => {
  // The first residue of a hangs off the start; b has W inserted.
  const {pairs} = align("ACDEFGHIK", "CDEFGWHIK");
  assert.deepEqual(pairs, [[1, 0], [2, 1], [3, 2], [4, 3], [5, 4], [6, 6], [7, 7], [8, 8]]);
  // A shorter construct aligns into the middle, residue for residue.
  const sub = align(SEQ, SEQ.slice(10, 40)).pairs;
  assert.equal(sub.length, 30);
  assert.ok(sub.every(([i, j]) => i === j + 10));
  assert.deepEqual(align("", "ACD"), {score: 0, pairs: []});
});

test("fit recovers a rotation and a translation, and never reflects", () => {
  const P = trace(30, 7), Q = P.map(p => apply(T0, p)), X = fit(P, Q);
  assert.ok(X.rmsd < 1e-9);
  assert.ok(close(X.R, T0.R) && close(X.t, T0.t));
  // A mirror image is fitted as well as a rotation can, not by mirroring.
  const M = fit(P, P.map(p => [-p[0], p[1], p[2]]));
  assert.ok(Math.abs(det(M.R) - 1) < 1e-9);
  assert.ok(M.rmsd > 0.1);
});

test("chainsOf: amino acids with a Cα, not a calcium ion or a water", () => {
  const atoms = [...chain("A", "GS", trace(2, 1)),
                 {chain: "A", resi: 900, resn: "CA", atom: "CA", hetflag: true, x: 0, y: 0, z: 0},
                 {chain: "A", resi: 901, resn: "HOH", atom: "O", hetflag: true, x: 0, y: 0, z: 0},
                 {chain: "B", resi: 1, resn: "MSE", atom: "CA", hetflag: true, x: 0, y: 0, z: 0}];
  assert.deepEqual(chainsOf(atoms).map(c => [c.chain, c.seq]), [["A", "GS"], ["B", "M"]]);
});

test("superpose puts a moved copy back where it was", () => {
  const cas = trace(SEQ.length, 3), ref = chain("A", SEQ, cas),
        mob = moved(chain("A", SEQ, cas, 101), T0);     // renumbered too
  const s = superpose({atoms: mob, only: null}, {atoms: ref, only: null});
  assert.ok(s.rmsd < 1e-6);
  assert.deepEqual([s.n, s.aligned, s.ident], [SEQ.length, SEQ.length, 1]);
  assert.ok(close(apply(s, [mob[1].x, mob[1].y, mob[1].z]), cas[0]));
});

test("superpose: a loop that moved is left out of the fit", () => {
  const cas = trace(SEQ.length, 5), ref = chain("A", SEQ, cas),
        bent = cas.map((p, i) => (i >= 20 && i < 23 ? [p[0] + 8, p[1], p[2]] : p)),
        // a homologue: a point mutation and a tail the reference lacks
        seq = SEQ.slice(0, 30) + "W" + SEQ.slice(31),
        mob = moved(chain("B", seq + "GGG", bent.concat(trace(3, 9).map(p => p.map(v => v + 99)))), T0);
  const s = superpose({atoms: mob, only: null}, {atoms: ref, only: null});
  assert.equal(s.aligned, SEQ.length);
  assert.equal(s.n, SEQ.length - 3);
  assert.ok(s.rmsd < 1e-6, String(s.rmsd));
  assert.ok(Math.abs(s.ident - (SEQ.length - 1) / SEQ.length) < 1e-9);
});

test("superpose pairs chains by sequence, whatever their ids and order", () => {
  const a = SEQ.slice(0, 28), b = "WYHPFNDCEGQ" + SEQ.slice(28, 45),
        ca = trace(a.length, 11), cb = trace(b.length, 12).map(p => [p[0] + 15, p[1], p[2]]);
  const ref = [...chain("A", a, ca), ...chain("B", b, cb)],
        mob = moved([...chain("X", b, cb), ...chain("Y", a, ca)], T0);
  const s = superpose({atoms: mob, only: null}, {atoms: ref, only: null});
  assert.equal(s.aligned, a.length + b.length);
  assert.ok(s.rmsd < 1e-6);
});

test("superpose: a selection narrows the fit to the residues it holds", () => {
  const cas = trace(SEQ.length, 4), ref = chain("A", SEQ, cas),
        // half the chain hinged away: fitting on the other half finds it exactly
        hinge = cas.map((p, i) => (i < 30 ? p : apply({R: rotation([0, 0, 1], 0.6), t: [3, 0, 0]}, p))),
        mob = moved(chain("A", SEQ, hinge), T0),
        site = ref.filter(a => a.resi <= 12);
  const s = superpose({atoms: mob, only: null}, {atoms: ref, only: site});
  assert.equal(s.aligned, 12);
  assert.equal(s.n, 12);
  assert.ok(s.rmsd < 1e-6);
  // Whether the chains are related is a question about the whole chains.
  assert.deepEqual([s.paired, s.ident, s.related], [SEQ.length, 1, true]);
});

test("related: identity that shows homology depends on how long the alignment is", () => {
  assert.equal(related(0.23, 13), false);    // crambin onto HIV protease, as it happens
  assert.equal(related(0.98, 99), true);
  assert.equal(related(0.35, 100), true);
  assert.equal(related(0.25, 100), false);
  assert.equal(related(0.22, 300), true);
  assert.equal(related(0.21, 5000), true);    // the curve is read flat past 500
  assert.equal(related(1, 0), false);
});

test("superpose says why it cannot", () => {
  const ref = chain("A", SEQ, trace(SEQ.length, 2)),
        water = [{chain: "W", resi: 1, resn: "HOH", atom: "O", x: 0, y: 0, z: 0}];
  assert.throws(() => superpose({atoms: water, only: null}, {atoms: ref, only: null}),
                /it has no protein Cα atoms/);
  assert.throws(() => superpose({atoms: ref, only: null}, {atoms: ref, only: water}),
                /part of the reference selected has no protein Cα/);
  assert.throws(() => superpose({atoms: chain("A", "GS", trace(2, 1)), only: null}, {atoms: ref, only: null}),
                /residues pair up with the reference — at least 3/);
});

/* ---------------- the text ---------------- */
function pdbAtoms(text){
  return text.split("\n").filter(l => /^(ATOM|HETATM)/.test(l))
    .map(l => [30, 38, 46].map(c => parseFloat(l.slice(c, c + 8))));
}
function cifAtoms(text){
  const lines = text.split("\n"), site = cifLoops(lines, ATOM_SITE).find(L => L.cat === "_atom_site.");
  const cols = ["Cartn_x", "Cartn_y", "Cartn_z"].map(c => cifCol(site, c));
  return site.rows.map(j => { const t = cifTokens(lines[j]); return cols.map(c => parseFloat(t[c])); });
}

test("moveText moves every PDB atom and leaves every other record alone", () => {
  const text = fs.readFileSync(new URL("1CRN.pdb", DATA), "utf8"), out = moveText(text, "pdb", T0);
  const before = pdbAtoms(text), after = pdbAtoms(out);
  assert.equal(after.length, before.length);
  before.forEach((p, i) => assert.ok(close(apply(T0, p), after[i], 6e-4), String(i)));
  const a = text.split("\n"), b = out.split("\n");
  assert.equal(b.length, a.length);
  a.forEach((l, i) => {
    if (/^(ATOM|HETATM)/.test(l)){
      assert.equal(b[i].length, l.length);
      assert.equal(b[i].slice(0, 30) + b[i].slice(54), l.slice(0, 30) + l.slice(54));
    } else assert.equal(b[i], l);
  });
});

test("moveText turns an anisotropic tensor with its atom", () => {
  const atom = "ATOM      1  CA  GLY A   1       1.000   2.000   3.000  1.00 10.00           C",
        aniso = "ANISOU    1  CA  GLY A   1     1000    200     30      4      5      6       C";
  // a quarter turn about z: x goes to y, so U11 and U22 trade places
  const out = moveText(atom + "\n" + aniso, "pdb", {R: rotation([0, 0, 1], Math.PI / 2), t: [0, 0, 0]}).split("\n");
  assert.equal(out[0].slice(30, 54), "  -2.000   1.000   3.000");
  assert.equal(out[1], "ANISOU    1  CA  GLY A   1      200   1000     30     -4     -6      5       C");
  assert.throws(() => moveText(atom, "pdb", {R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [-1e5, 0, 0]}),
                /does not fit a PDB file's columns/);
});

test("moveText moves mmCIF atoms, and a cut still finds them afterwards", () => {
  const text = fs.readFileSync(new URL("1CRN.cif", DATA), "utf8"), out = moveText(text, "cif", T0);
  const before = cifAtoms(text), after = cifAtoms(out);
  before.forEach((p, i) => assert.ok(close(apply(T0, p), after[i], 6e-4), String(i)));
  // Nothing but the three coordinates changed on any line.
  const a = text.split("\n"), b = out.split("\n");
  assert.equal(b.length, a.length);
  a.forEach((l, i) => {
    if (l === b[i]) return;
    const x = cifTokens(l), y = cifTokens(b[i]);
    assert.equal(y.length, x.length);
    x.forEach((t, k) => { if (t !== y[k]) assert.ok([10, 11, 12].includes(k), i + ":" + k); });
  });
  // A selection is cut by coordinates, so it has to match the moved ones.
  const first = after.slice(0, 5).map(([x, y, z]) => ({x, y, z}));
  assert.equal(cifAtoms(cifSubset(out, first)).length, 5);
});
