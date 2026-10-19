// Saving a session to a file and opening it again: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {snapshot, restore, isSessionText, SESSION_APP, SESSION_VERSION}
  from "../../proteinviewer/app/js/model/session.js";

const PDB = "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\nEND\n";

/* A state with everything a session has to carry. */
function state(){
  return {
    ents: [{uid: "e1", id: "1HSG", data: PDB, fmt: "pdb", title: "HIV-1 PROTEASE",
            sub: "x-ray · 2.00 Å", visible: true, ci: 0, edited: true,
            open: {"e1/root": true, "e1/chain:A": true}, hidden: {"e1/waters": true},
            orig: {A: [1, 2, 3]},
            // rebuilt by parsing data again, so never saved
            model: {fake: true}, tree: {fake: true}, nodes: {fake: true}, rev: 3},
           {uid: "e2", id: "1HSG-prep", data: PDB, fmt: "pdb", prepared: true,
            prepLine: "Prepared in 8 s · pH 7 · heavy-atom RMSD <b>0.21 Å</b>",
            prepSummary: "input      1HSG", visible: false, ci: 1, open: {}, hidden: {}}],
    entSerial: 2,
    picks: ["e1/chain:A"], pickAnchor: "e1/chain:A",
    undo: [{kind: "part", what: "MK1 902 from 1HSG"}],
    method: {method: "lacuna", backend: "openmm"},
    project: {id: "2026-10-18-1HSG", dest: "~/work/1HSG-run1", files: 7, bytes: 2048,
              path: "/home/me/.local/share/proteinviewer/projects/2026-10-18-1HSG",
              busy: true, msg: {html: "saving"}},
    spheres: true, noLigand: true, genShow: 10,
    table: {sort: {key: "mw", dir: -1}, only: true, sel: {rank: 1, i: 2}},
    run: {id: "e1#1", ent: "e1", token: "1HSG", label: "1HSG · chain A", cached: false,
          picks: [0],
          pockets: [{rank: 1, nResidues: 2, nSpheres: 9, score: 12.5, volume: 301.25,
                     drug: 0.42, sel: [{chain: "A", resi: [1, 2]}],
                     spheres: [[0, 0, 0, 3.4], [1, 0, 0, 3.9]],
                     centroid: [0.5, 0, 0], persistence: null, crypticity: null}]},
    runSerial: 1,
    gen: {job: "job-1", forRun: "e1#1", meta: "NEAT-POCKET generated 2/2 for pocket 1",
          groups: {1: {mols: ["mol a\n$$$$\n", "mol b\n$$$$\n"], visible: true,
                       focus: -1, seconds: 31.2, written: 2}},
          table: [{rank: 1, i: 0, name: "mol-0", formula: "C6H6", mw: 78.11, heavy: 6,
                   logp: 2.1, tpsa: 0, qed: 0.47, rings: 1, rotb: 0, smiles: "c1ccccc1",
                   svg: "<svg viewBox='0 0 100 100'><path d='M0 0h1' fill='#111'/></svg>"}]},
    busy: null, fetch: {busy: true, msg: {html: "x"}}, session: {msg: null},
    prep: {busy: "1HSG", error: null}, pocketTask: {busy: "x", error: null},
    genTask: {busy: true, msg: null}, server: {known: true},
  };
}

/* What the page would do: stringify, write a file, read it, parse it. */
function roundTrip(st){
  const out = {};
  const sum = restore(out, JSON.parse(JSON.stringify(snapshot(st, "2026-10-18T09:00:00.000Z"))));
  return {out, sum};
}

test("a session round-trips through a file", () => {
  const st = state(), {out, sum} = roundTrip(st);
  assert.deepEqual(sum, {ents: 2, pockets: 1, molecules: 2, saved: "2026-10-18T09:00:00.000Z"});

  assert.deepEqual(out.ents.map(e => e.id), ["1HSG", "1HSG-prep"]);
  const e = out.ents[0];
  assert.equal(e.data, PDB);
  assert.equal(e.edited, true);
  assert.equal(e.visible, true);
  assert.equal(out.ents[1].visible, false);
  // Folding, hiding and what Prepare must not rebuild all come back.
  assert.deepEqual(e.open, {"e1/root": true, "e1/chain:A": true});
  assert.deepEqual(e.hidden, {"e1/waters": true});
  assert.deepEqual(e.orig, {A: [1, 2, 3]});
  // The parsed model is not in the file; the next commit rebuilds it.
  assert.equal(e.model, null);
  assert.equal(e.tree, null);
  assert.equal(e.rev, 0);

  assert.deepEqual(out.picks, ["e1/chain:A"]);
  assert.equal(out.pickAnchor, "e1/chain:A");
  assert.deepEqual(out.method, {method: "lacuna", backend: "openmm"});
  assert.equal(out.spheres, true);
  assert.equal(out.noLigand, true);
  assert.equal(out.genShow, 10);
  assert.deepEqual(out.table, {sort: {key: "mw", dir: -1}, only: true, sel: null});

  assert.equal(out.run.pockets[0].score, 12.5);
  assert.equal(out.run.pockets[0].drug, 0.42);
  assert.deepEqual(out.run.pockets[0].sel, [{chain: "A", resi: [1, 2]}]);
  // [x, y, z, radius]: the radius is kept, since a saved project writes it out.
  assert.deepEqual(out.run.pockets[0].spheres, [[0, 0, 0, 3.4], [1, 0, 0, 3.9]]);
  assert.deepEqual(out.run.picks, [0]);
  assert.deepEqual(out.gen.groups["1"].mols, ["mol a\n$$$$\n", "mol b\n$$$$\n"]);
  assert.equal(out.gen.table[0].smiles, "c1ccccc1");
  assert.equal(out.gen.table[0].svg, st.gen.table[0].svg);
  // The molecules still belong to the run they were generated for.
  assert.equal(out.gen.forRun, out.run.id);
});

test("a restored run says so: the server no longer holds its structure", () => {
  const {out} = roundTrip(state());
  assert.equal(out.run.restored, true);
});

test("the output directory it was collecting into comes back with it", () => {
  const st = state(), {out} = roundTrip(st);
  assert.equal(out.project.id, "2026-10-18-1HSG");
  assert.equal(out.project.path, st.project.path);
  assert.equal(out.project.dest, "~/work/1HSG-run1");
  // Counts are the server's to report, and nothing is in flight on reopening.
  assert.deepEqual([out.project.files, out.project.busy, out.project.msg], [0, false, null]);
});

test("tasks in flight and the undo history do not survive a save", () => {
  const {out} = roundTrip(state());
  assert.deepEqual(out.undo, []);
  assert.equal(out.busy, null);
  assert.deepEqual(out.prep, {busy: null, error: null});
  assert.deepEqual(out.genTask, {busy: false, msg: null});
  assert.equal(out.fetch.busy, false);
});

test("a structure loaded next cannot collide with a restored uid", () => {
  const st = state();
  st.entSerial = 0;                       // a file written before this was kept
  const {out} = roundTrip(st);
  assert.equal(out.entSerial, 2);
});

test("a superposed structure keeps its frame and its fit, typed", () => {
  const st = state();
  Object.assign(st.ents[0], {frame: "e7", sup: {onto: "2XYZ", rmsd: 0.42, n: 287, aligned: 301, ident: 0.98}});
  const snap = JSON.parse(JSON.stringify(snapshot(st)));
  snap.ents[1].sup = {onto: "<b>x</b>", rmsd: "<img src=x>", n: "12", aligned: null};
  const out = {};
  restore(out, snap);
  assert.equal(out.ents[0].frame, "e7");
  assert.deepEqual(out.ents[0].sup, {onto: "2XYZ", rmsd: 0.42, n: 287, aligned: 301, ident: 0.98});
  assert.equal(out.ents[1].frame, null);
  assert.deepEqual(out.ents[1].sup, {onto: "<b>x</b>", rmsd: 0, n: 12, aligned: 0, ident: 0});
  // A frame names a uid; the next structure loaded must not be given it.
  assert.equal(out.entSerial, 7);
  assert.deepEqual(out.sup, {ref: null, move: {}, busy: false, msg: null});
});

test("only sessions are opened", () => {
  assert.throws(() => restore({}, null), /not a Protein Viewer session/);
  assert.throws(() => restore({}, {hello: 1}), /not a Protein Viewer session/);
  assert.throws(() => restore({}, [{app: SESSION_APP}]), /not a Protein Viewer session/);
  assert.throws(() => restore({}, {app: SESSION_APP, version: SESSION_VERSION + 1}),
                /newer Protein Viewer/);
  assert.throws(() => restore({}, {app: SESSION_APP, version: 1, ents: []}),
                /holds no structures/);
  assert.throws(() => restore({}, {app: SESSION_APP, version: 1, ents: [{id: "x"}]}),
                /holds no structures/);
});

test("isSessionText tells a session from coordinates", () => {
  assert.ok(isSessionText(JSON.stringify(snapshot(state()))));
  assert.ok(!isSessionText(PDB));
  assert.ok(!isSessionText("data_1HSG\nloop_\n_atom_site.id\n"));
  assert.ok(!isSessionText('{"some": "other json"}'));
});

test("a structure named twice is kept once: picks name a structure by uid", () => {
  const snap = snapshot(state());
  snap.ents.push({...snap.ents[0], id: "COPY"});
  const out = {};
  assert.equal(restore(out, JSON.parse(JSON.stringify(snap))).ents, 2);
  assert.deepEqual(out.ents.map(e => e.uid), ["e1", "e2"]);
});

test("picks and a run that name a structure the file does not hold are dropped", () => {
  const snap = snapshot(state());
  snap.picks = ["e9/chain:A", "e1/chain:A", 7];
  snap.run.ent = "e9";
  const out = {};
  restore(out, JSON.parse(JSON.stringify(snap)));
  assert.deepEqual(out.picks, ["e1/chain:A"]);
  assert.equal(out.run, null);
  assert.equal(out.gen, null);        // its run is gone, so the molecules go
});

/* A session file is a document off disk, and three of its fields are rendered
   as markup. Everything else the page escapes where it prints it. */
test("markup in a session file is cut back to what the page writes", () => {
  const snap = snapshot(state());
  snap.ents[1].prepLine = "Prepared <b>fine</b> <img src=x onerror=alert(1)> <script>bad()</script>";
  snap.gen.meta = "<iframe src=javascript:alert(1)></iframe> generated";
  const out = {};
  restore(out, JSON.parse(JSON.stringify(snap)));
  assert.equal(out.ents[1].prepLine,
    "Prepared <b>fine</b> &lt;img src=x onerror=alert(1)&gt; &lt;script&gt;bad()&lt;/script&gt;");
  assert.equal(out.gen.meta, "&lt;iframe src=javascript:alert(1)&gt;&lt;/iframe&gt; generated");
});

test("a depiction that is not a plain drawing is dropped", () => {
  const bad = ["<svg><script>alert(1)</script></svg>",
               "<svg onload='alert(1)'><path d='M0 0'/></svg>",
               "<svg><foreignObject><b>x</b></foreignObject></svg>",
               "<svg><image href='http://elsewhere/x.png'/></svg>",
               "<img src=x onerror=alert(1)>"];
  for (const svg of bad){
    const snap = snapshot(state());
    snap.gen.table[0].svg = svg;
    const out = {};
    restore(out, JSON.parse(JSON.stringify(snap)));
    assert.equal(out.gen.table[0].svg, "", svg);
  }
});

test("the run line's numbers are numbers, whatever the file says", () => {
  const snap = snapshot(state());
  snap.run.meta = {tool: "fpocket", receptorAtoms: "<img src=x onerror=alert(1)>",
                   totalAtoms: "120", nPockets: 1.9, elapsed: "0.5", cutoff: 6,
                   command: "fpocket -f x.pdb", ligandRemoved: ["MK1"]};
  const out = {};
  restore(out, JSON.parse(JSON.stringify(snap)));
  assert.equal(out.run.meta.receptorAtoms, 0);
  assert.equal(out.run.meta.totalAtoms, 120);
  assert.equal(out.run.meta.nPockets, 1);
  assert.equal(out.run.meta.elapsed, 0.5);
  assert.deepEqual(out.run.meta.ligandRemoved, ["MK1"]);
});
