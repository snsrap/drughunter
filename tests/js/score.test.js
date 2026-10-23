// The join that puts a score on the row whose pose it is: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {merge} from "../../proteinviewer/app/js/features/score.js";

/* Rows as the child writes them: `i` is the molecule's place in its pocket's
   SDF, which is the order the scorer was handed. */
function rows(){
  return [
    {rank: 1, i: 0, name: "pocket1_1", qed: 0.5},
    {rank: 1, i: 1, name: "pocket1_2", qed: 0.6},
    {rank: 2, i: 0, name: "pocket2_1", qed: 0.7},
  ];
}

const SCORES = {
  "1": [
    {index: 0, score: -5.5, le: 0.25, rmsd_min: 0.4},
    {index: 1, score: null, le: null, rmsd_min: null, error: "clash"},
  ],
};
const SCORERS = {"1": {scorer: "gnina v1.1", mode: "minimize"}};

test("each score lands on its own row", () => {
  const table = rows();
  const scored = merge(table, SCORES, SCORERS);
  assert.equal(scored, 1);
  assert.deepEqual(
    [table[0].score, table[0].le, table[0].rmsd_min, table[0].scorer],
    [-5.5, 0.25, 0.4, "gnina v1.1"]
  );
  assert.equal(table[0].qed, 0.5, "nothing else on the row is disturbed");
});

test("a pose the scorer could not do keeps its row and says why", () => {
  const table = rows();
  merge(table, SCORES, SCORERS);
  assert.equal(table[1].score, null);
  assert.equal(table[1].scoreError, "clash");
});

test("a pocket that was not scored is left exactly as it was", () => {
  const table = rows();
  merge(table, SCORES, SCORERS);
  assert.deepEqual(table[2], rows()[2]);
});

test("scoring again replaces the last scores rather than keeping stale ones", () => {
  const table = rows();
  merge(table, SCORES, SCORERS);
  merge(table, {"1": [{index: 0, score: -9, le: 0.5, rmsd_min: 0.1}]}, SCORERS);
  assert.equal(table[0].score, -9);
  // the row that failed last time has no error left over from it
  assert.equal(table[1].scoreError, undefined);
  assert.equal(table[1].score, undefined);
});

test("rows are matched by (rank, i), not by their place in the table", () => {
  const table = [
    {rank: 1, i: 1, name: "pocket1_2"},
    {rank: 1, i: 0, name: "pocket1_1"},
  ];
  merge(table, SCORES, SCORERS);
  assert.equal(table[0].score, null, "i=1 is the one the scorer could not do");
  assert.equal(table[1].score, -5.5);
});
