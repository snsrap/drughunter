// The dataset summary's own logic -- which state the panel is in, which display
// the target gets, how each number is worded, the geometry of the charts: run
// with `npm test`. The answers below have the shape ligandbench/summary.py
// returns (checked against a real one when they were written).
import {test} from "node:test";
import assert from "node:assert/strict";

import {
  MAX_BARS, count, percent, number, idleTask, startTask, finishTask, failTask, panelState,
  populationRows, exclusionView, duplicateRows, scaffoldRows, scaffoldPolicy, missingView,
  targetLabel, axisLabel, histogramBars, binLabel, axisTicks, classRows, targetView, errorText,
} from "../../proteinviewer/app/js/model/summary.js";

const KI = "HIV-1 PR FRET (Q7K): Ki (nM)";

const pops = (n, valid, eligible) => ({
  source_rows: {label: "source rows", n, of: null},
  valid_structures: {label: "valid structures", n: valid, of: "source_rows"},
  training_eligible: {label: "training-eligible rows", n: eligible, of: "source_rows"},
});

/* The CDD export under a pKi: 106 rows, 4 of them bounds. */
function regression(){
  return {
    version: "1", populations: pops(106, 106, 102),
    parse_failures: {population: "source_rows", n: 0},
    exclusions: {population: "source_rows", overlap: true, n_rows: 4,
      by_reason: [{code: "censored", label: "the target is a bound (<, >), not a measurement", n: 4}]},
    duplicates: {population: "valid_structures", n_distinct: 106, n_duplicate_rows: 0, n_groups: 0,
      n_rows_in_groups: 0, n_conflicting: null, conflicting_reason: "not defined for regression"},
    target: {state: "regression", population: "training_eligible", n: 102, column: KI, kind: "regression",
      transform: "pki", source_units: "nM", units: "pKi", n_binned: 102, n_not_binned: 0,
      min: 4.58, max: 12.0969, mean: 8.7947, median: 9.4893,
      bins: {rule: "Rice rule", n_bins: 10, edges: [4.58, 5.332, 6.083, 6.835, 7.587, 8.338, 9.09, 9.842, 10.594, 11.345, 12.097],
             counts: [6, 5, 12, 7, 1, 9, 23, 31, 6, 2]}},
    scaffolds: {population: "valid_structures", policy: "murcko-v1", n_structures: 106, n_distinct: 31,
      largest_group_size: 19, top10_groups: 10, top10_rows: 77, top10_share: 77 / 106, acyclic_rows: 0,
      n_unscaffolded: 0},
    missing: {population: "source_rows", columns: [
      {index: 0, name: "Molecule Name", n_missing: 0}, {index: 5, name: "Ki Qualifier", n_missing: 102}]},
  };
}

/* Two classes, a duplicate pair that disagrees, an unparsed row, and an acyclic one. */
function classification(){
  const s = regression();
  s.populations = pops(8, 7, 6);
  s.parse_failures.n = 1;
  s.exclusions = {population: "source_rows", overlap: true, n_rows: 3, by_reason: [
    {code: "invalid_structure", label: "the structure did not parse", n: 2},
    {code: "missing_target", label: "the target is blank", n: 2}]};
  s.duplicates = {population: "valid_structures", n_distinct: 5, n_duplicate_rows: 2, n_groups: 2,
    n_rows_in_groups: 4, n_conflicting: 1, conflicting_reason: null};
  s.target = {state: "classification", population: "training_eligible", n: 6, column: "y", kind: "classification",
    positive_class: "1", classes: [{label: "1", count: 3, positive: true}, {label: "0", count: 3, positive: false}]};
  s.scaffolds = {population: "valid_structures", policy: "murcko-v1", n_structures: 7, n_distinct: 3,
    largest_group_size: 4, top10_groups: 3, top10_rows: 7, top10_share: 1, acyclic_rows: 4, n_unscaffolded: 0};
  return s;
}

function noTarget(){
  const s = classification();
  s.populations = pops(8, 7, 0);
  s.exclusions.by_reason = [{code: "no_target", label: "the dataset has no target", n: 8}];
  s.exclusions.n_rows = 8;
  s.duplicates.n_conflicting = null;
  s.duplicates.conflicting_reason = "the dataset has no target";
  s.target = {state: "no_target", population: "training_eligible", n: 0};
  return s;
}

function empty(){
  const s = regression();
  s.populations = pops(2, 2, 0);
  s.target = {state: "empty", population: "training_eligible", n: 0, column: "y", kind: "regression"};
  return s;
}

// ---- the panel's four states ----

test("the panel is idle with no dataset, loading while asked, an error when refused, ready with an answer", () => {
  assert.equal(panelState(idleTask()).kind, "idle");
  assert.equal(panelState(null).kind, "idle");
  const t = startTask("ds-aaaaaaaaaaaa");
  assert.deepEqual(panelState(t), {kind: "loading"});
  assert.equal(panelState(finishTask(t, "ds-aaaaaaaaaaaa", regression())).kind, "ready");
  const failed = panelState(failTask(t, "ds-aaaaaaaaaaaa", "ligandbench is not installed"));
  assert.deepEqual(failed, {kind: "error", message: "ligandbench is not installed"});
  assert.equal(startTask(null).status, "idle");
});

test("a refusal is worded for the user; a server too old for the endpoint is told to restart", () => {
  assert.equal(errorText("no such endpoint"), "This server predates the dataset summary — restart it.");
  assert.equal(errorText("no resolved dataset ds-aaaaaaaaaaaa"), "no resolved dataset ds-aaaaaaaaaaaa");
  assert.equal(errorText(""), "The summary could not be made.");
});

test("an answer for a dataset the page has left changes nothing", () => {
  const t = startTask("ds-bbbbbbbbbbbb");
  assert.equal(finishTask(t, "ds-aaaaaaaaaaaa", regression()), t);
  assert.equal(failTask(t, "ds-aaaaaaaaaaaa", "late"), t);
  assert.equal(panelState(t).kind, "loading");
});

test("a ready task with nothing in it is shown as a fault, not as an empty panel", () => {
  const odd = {id: "ds-aaaaaaaaaaaa", status: "ready", data: null, error: null};
  assert.equal(panelState(odd).kind, "error");
  assert.equal(panelState({id: "x", status: "error", data: null, error: ""}).kind, "error");
});

// ---- the four target displays ----

test("regression, classification, no-target and empty each choose their own display", () => {
  assert.equal(targetView(regression().target).kind, "regression");
  assert.equal(targetView(classification().target).kind, "classification");
  assert.equal(targetView(noTarget().target).kind, "no_target");
  assert.equal(targetView(empty().target).kind, "empty");
  // An unknown state is the empty display, not a chart.
  assert.equal(targetView({state: "surprise", n: 0}).kind, "empty");
});

test("no target says No target and draws nothing", () => {
  const v = targetView(noTarget().target);
  assert.equal(v.message, "No target");
  assert.match(v.detail, /prediction only/);
  assert.equal(v.bars, undefined);
  assert.equal(v.rows, undefined);
});

test("no training rows says so and draws nothing, whatever the task was", () => {
  const v = targetView(empty().target);
  assert.equal(v.message, "No training-eligible rows");
  assert.equal(v.bars, undefined);
  assert.match(v.detail, /nothing to draw/);
});

test("a regression target names the units it was made in and draws its bins", () => {
  const v = targetView(regression().target);
  assert.equal(v.axis, "pKi");
  assert.match(v.caption, /pKi = 9 − log10\(Ki in nM\), from “HIV-1 PR FRET \(Q7K\): Ki \(nM\)”/);
  assert.match(v.caption, /102 training-eligible rows/);
  assert.equal(v.bars.length, 10);
  assert.deepEqual(v.stats.map(s => s[0]), ["Smallest", "Median", "Mean", "Largest", "Bins"]);
  assert.equal(v.stats[0][1], "4.58");
  assert.deepEqual(v.ticks.map(t => t.pos), [0, 0.5, 1]);
});

test("an untransformed target is labelled with its column and units; with none, just the column", () => {
  const t = regression().target;
  Object.assign(t, {transform: "identity", units: "µM"});
  assert.equal(targetLabel(t), "“" + KI + "” in µM");
  assert.equal(axisLabel(t), "µM");
  Object.assign(t, {units: ""});
  assert.equal(targetLabel(t), "“" + KI + "”");
  assert.equal(axisLabel(t), KI);
});

test("values that could not be drawn are said, not silently dropped", () => {
  const t = regression().target;
  t.n = 104; t.n_not_binned = 2;
  assert.match(targetView(t).caption, /2 with no finite value are not drawn/);
});

test("classification shows every class in the server's order with its share", () => {
  const v = targetView(classification().target);
  assert.deepEqual(v.rows.map(r => [r.label, r.value, r.share, r.positive]),
    [["1", "3", "50%", true], ["0", "3", "50%", false]]);
  assert.match(v.caption, /the positive class is “1”/);
  assert.deepEqual(v.rows.map(r => r.frac), [1, 1]);
  // A class with fewer rows is a shorter bar, and zero is none.
  const t = classification().target;
  t.classes = [{label: "a", count: 8, positive: false}, {label: "b", count: 2, positive: false},
               {label: "c", count: 0, positive: false}];
  t.n = 10; t.positive_class = null;
  assert.deepEqual(classRows(t).map(r => r.frac), [1, 0.25, 0]);
  assert.doesNotMatch(targetView(t).caption, /positive class/);
});

// ---- the charts draw bins, not rows ----

test("a histogram is one bar per bin, scaled to the tallest, inside the box", () => {
  const bars = histogramBars(regression().target.bins, 400, 120);
  assert.equal(bars.length, 10);
  const tallest = bars.find(b => b.count === 31);
  assert.equal(tallest.h, 120);
  assert.equal(tallest.y, 0);
  for (const b of bars){
    assert.ok(b.x >= 0 && b.x + b.w <= 400 + 1e-9);
    assert.ok(Math.abs(b.y + b.h - 120) < 1e-9, "bars stand on the axis");
    assert.equal(b.h, b.count / 31 * 120);
  }
  assert.equal(bars[0].lo, 4.58);
  assert.equal(bars[9].hi, 12.097);
});

test("ten thousand rows are at most fifty bars", () => {
  /* What the server returns for 10,000 values: the bins, with the rows counted in them. */
  const counts = Array.from({length: MAX_BARS}, () => 200);
  const edges = Array.from({length: MAX_BARS + 1}, (_, i) => i / 10);
  const bars = histogramBars({counts, edges}, 400, 120);
  assert.equal(bars.length, MAX_BARS);
  assert.equal(bars.reduce((a, b) => a + b.count, 0), 10000);
  /* The classification chart is a row per class, however many rows were counted. */
  const big = classification().target;
  big.n = 10000; big.classes = [{label: "a", count: 9000, positive: false}, {label: "b", count: 1000, positive: true}];
  assert.equal(classRows(big).length, 2);
});

test("one distinct value is one full-width bar and one tick", () => {
  const bins = {edges: [5, 5], counts: [3]};
  const bars = histogramBars(bins, 400, 120);
  assert.equal(bars.length, 1);
  assert.deepEqual([bars[0].x, bars[0].w, bars[0].h], [0, 400, 120]);
  assert.deepEqual(axisTicks(bins), [{pos: 0.5, label: "5"}]);
  assert.equal(binLabel(5, 5, 3), "5: 3 rows");
  assert.equal(binLabel(1, 2, 1), "1 – 2: 1 row");
});

// ---- the counts and their populations ----

test("every count is worded with the population it is of", () => {
  const rows = Object.fromEntries(populationRows(classification()).map(r => [r.label, r]));
  assert.equal(rows["Source rows"].value, "8");
  assert.equal(rows["Valid structures"].value, "7");
  assert.equal(rows["Valid structures"].note, "87.5% of source rows");
  assert.equal(rows["Parse failures"].value, "1");
  assert.equal(rows["Parse failures"].note, "12.5% of source rows");
  assert.equal(rows["Training-eligible"].value, "6");
  assert.match(rows["Training-eligible"].note, /^75% of source rows/);
});

test("a dataset with no target has no training-eligible count, only the reason", () => {
  const rows = Object.fromEntries(populationRows(noTarget()).map(r => [r.label, r]));
  assert.equal(rows["Training-eligible"].value, "—");
  assert.match(rows["Training-eligible"].note, /prediction only/);
  assert.equal(exclusionView(noTarget()).shown, false);
});

test("exclusion reasons are listed with an explicit statement that they overlap", () => {
  const v = exclusionView(classification());
  assert.equal(v.shown, true);
  assert.equal(v.heading, "3 rows left out of training");
  assert.deepEqual(v.rows.map(r => [r.label, r.value]),
    [["The structure did not parse", "2"], ["The target is blank", "2"]]);
  assert.match(v.note, /^Reasons overlap: a row can have more than one, so these add up to 4 for 3 rows left out\.$/);
  assert.equal(exclusionView(regression()).rows.length, 1);
  const none = regression(); none.exclusions.by_reason = []; none.exclusions.n_rows = 0;
  assert.equal(exclusionView(none).shown, false);
});

test("a parse-failure count of zero says every structure parsed", () => {
  const rows = populationRows(regression());
  assert.equal(rows[2].note, "every structure parsed");
});

test("duplicates are valid rows minus distinct structures, with conflicts where they are defined", () => {
  const [dup, groups, conflict] = duplicateRows(classification());
  assert.equal(dup.value, "2");
  assert.equal(dup.note, "7 valid rows − 5 distinct structures");
  assert.equal(groups.value, "2");
  assert.equal(groups.note, "found in 4 rows");
  assert.deepEqual([conflict.label, conflict.value], ["Conflicting targets", "1"]);
  // Not defined for regression: the dash and the reason, not a made-up zero.
  const r = duplicateRows(regression());
  assert.equal(r[0].note, "every valid row is a different structure");
  assert.deepEqual([r[2].value, r[2].note], ["—", "Not defined for regression"]);
  assert.equal(duplicateRows(noTarget())[2].note, "The dataset has no target");
});

test("scaffolds say the empty scaffold is one group and give its rows separately", () => {
  const rows = Object.fromEntries(scaffoldRows(classification()).map(r => [r.label, r]));
  assert.equal(rows["Distinct scaffolds"].value, "3");
  assert.equal(rows["Largest group"].value, "4 rows");
  assert.equal(rows["Largest group"].note, "57.1% of valid structures");
  assert.equal(rows["Top-10 share"].value, "100%");
  assert.match(rows["Acyclic rows"].note, /counted as one group/);
  assert.equal(rows["Acyclic rows"].value, "4");
  assert.equal(rows["Not scaffolded"], undefined);
  assert.equal(scaffoldPolicy(classification()), "Bemis–Murcko scaffolds, murcko-v1");
  const s = classification(); s.scaffolds.n_unscaffolded = 1;
  assert.equal(scaffoldRows(s).at(-1).label, "Not scaffolded");
  const r = scaffoldRows(regression());
  assert.equal(r.find(x => x.label === "Top-10 share").note, "77 rows in the 10 largest groups");
});

test("missing cells list every source column, in order, with its share of the source rows", () => {
  const v = missingView(regression());
  assert.deepEqual(v.rows.map(r => [r.name, r.value, r.share]), [["Molecule Name", "0", "0%"], ["Ki Qualifier", "102", "96.2%"]]);
  assert.equal(v.heading, "1 column of 2 with missing cells");
  assert.equal(v.rows[1].frac, 102 / 106);
  assert.match(v.note, /wrong for its column is not/);
  const clean = regression(); clean.missing.columns.forEach(c => { c.n_missing = 0; });
  assert.equal(missingView(clean).heading, "no missing cells in any of 2 columns");
});

// ---- the numbers, as a person reads them ----

test("counts get thousands separators, shares one decimal, measurements four figures", () => {
  assert.equal(count(10000), "10,000");
  assert.equal(count(999), "999");
  assert.equal(count(1234567), "1,234,567");
  assert.equal(percent(1, 3), "33.3%");
  assert.equal(percent(0, 5), "0%");
  assert.equal(percent(5, 5), "100%");
  assert.equal(percent(1, 5000), "<0.1%");
  assert.equal(percent(4999, 5000), ">99.9%");
  assert.equal(percent(1, 0), "—");
  assert.equal(number(9.489281301766454), "9.489");
  assert.equal(number(12), "12");
  assert.equal(number(0.0008), "8e-4");
  assert.equal(number(null), "—");
  assert.equal(number(NaN), "—");
  assert.equal(number(0), "0");
});
