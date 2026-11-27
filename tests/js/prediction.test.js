// The prediction selector's own logic -- which models can be chosen and why not, one job's
// states, a job for another dataset that must paint nothing, the wording of a prediction
// beside a measurement, and what happens to a query when a column goes: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {
  selectorOptions, keepSelection, canStart, startBody, idleTask, beginStart, startFailed, startError, progressed,
  pollFailed, resume, cancelRequested, cancelFailed, nextAction, panelState, progressText, fraction, doneText,
  failureAdvice, setRows, predictionColumns, cellText, cardPredictions, detailPredictions, pruneQuery, schemaKey,
  ACTION_LABEL, SCOPE_NOTE, MAX_POLL_FAILURES, POLL_MS, POLL_RETRY_MS,
} from "../../proteinviewer/app/js/model/prediction.js";

const DS = "ds-0123456789ab", OTHER = "ds-0123456789ac", EXP = "123e4567-e89b-42d3-a456-426614174000";
const E = (id, over = {}) => Object.assign({
  id, name: "model " + id.slice(0, 2), selectable: true, usable: {ok: true, reason: ""}, model_family: "xgboost",
  task: "regression", target_column: "pKi", target: {units: "nM", transform: "pki"},
  headline: {metric: "rmse", strategy: "scaffold", value: 0.71234, n_defined: 5, n_total: 5, status: "ok", group: "g"},
}, over);
const job = (over = {}) => Object.assign({id: "pred-1", state: "queued", total: 100, completed: 0, dataset_id: DS, experiment_id: EXP, stage: ""}, over);
const started = () => progressed(beginStart(idleTask(DS), EXP), job());

test("the action is labelled as the whole dataset, and the scope is said in words", () => {
  assert.equal(ACTION_LABEL, "Predict entire dataset");
  assert.match(SCOPE_NOTE, /every row/); assert.match(SCOPE_NOTE, /filter, sort or page/);
  assert.match(SCOPE_NOTE, /not a measurement/); assert.match(SCOPE_NOTE, /not a test of the model/);
});

test("a model is offered with its name, id, family, target, units and labelled metric", () => {
  const [o] = selectorOptions([E(EXP)], true);
  assert.equal(o.disabled, false);
  for (const s of ["model 12", EXP.slice(0, 8), "xgboost", "pKi · regression · nM", "RMSE (scaffold test) 0.7123"]) assert.ok(o.text.includes(s), s + " in " + o.text);
});

test("a model that cannot be used is disabled, with the reason beside it", () => {
  const opts = selectorOptions([
    E("a1", {selectable: false}),
    E("b2", {usable: {ok: false, reason: "trained with rdkit 2024.09.1; this server runs rdkit 2026.09.1"}}),
    E("c3"),
  ], true);
  assert.deepEqual(opts.map(o => o.disabled), [true, true, false]);
  assert.match(opts[0].reason, /files are missing/);
  assert.match(opts[1].text, /unavailable: trained with rdkit/);
  assert.equal(selectorOptions([E("c3")], false)[0].disabled, true);
});

test("a selection is kept only while it can still be chosen", () => {
  const opts = selectorOptions([E("a1"), E("b2", {selectable: false})], true);
  assert.equal(keepSelection(opts, "a1"), "a1");
  assert.equal(keepSelection(opts, "b2"), "");
  assert.equal(keepSelection(opts, "gone"), "");
});

test("Predict needs a dataset, a usable choice, and no job already going", () => {
  const opts = selectorOptions([E("a1"), E("b2", {selectable: false})], true);
  const ds = {dataset_id: DS};
  assert.equal(canStart(null, "a1", opts, null).ok, false);
  assert.match(canStart(ds, "", opts, idleTask(DS)).reason, /Choose a model/);
  assert.match(canStart(ds, "", [], idleTask(DS)).reason, /No model is registered/);
  assert.match(canStart(ds, "b2", opts, idleTask(DS)).reason, /cannot be used/);
  assert.equal(canStart(ds, "a1", opts, idleTask(DS)).ok, true);
  assert.equal(canStart(ds, "a1", opts, started()).ok, false);
  assert.deepEqual(startBody(ds, "a1"), {dataset_id: DS, experiment_id: "a1"});
});

test("a job moves forward only, and an answer for another dataset or job changes nothing", () => {
  let t = started();
  assert.equal(t.phase, "tracking");
  t = progressed(t, job({state: "running", completed: 40}));
  assert.equal(t.job.completed, 40);
  assert.equal(progressed(t, job({state: "queued"})), t);                     // a slow, older answer
  assert.equal(progressed(t, job({id: "pred-2", state: "running"})), t);      // another job
  assert.equal(progressed(t, job({dataset_id: OTHER, state: "done"})), t);    // another dataset: paints nothing
  assert.equal(progressed(t, job({experiment_id: "other", state: "done"})), t);
  assert.equal(progressed(idleTask(DS), job()).phase, "idle");
  const done = progressed(t, job({state: "done", completed: 100, n_failed: 2}));
  assert.equal(done.phase, "terminal");
  assert.equal(progressed(done, job({state: "running"})), done);              // a terminal job stays
  assert.equal(progressed(t, job({state: "mystery"})).job.state, "error");
});

test("a start the server refused says why, by status", () => {
  const t = beginStart(idleTask(DS), EXP);
  assert.match(startFailed(t, {status: 409, message: "this model is already being applied"}).error, /cannot start: this model is already/);
  assert.match(startError({status: 404, message: "no such dataset"}), /not there any more/);
  assert.match(startError({status: 503, message: "xgboost missing"}), /cannot predict/);
  assert.match(startError({stale: true}), /restart it/);
  assert.match(startError({status: 400, message: "x"}), /refused/);
  assert.equal(startFailed(idleTask(DS), {status: 500}).phase, "idle");
});

test("losing a job: a 404 is expiry, repeated failures are lost contact, and resuming looks again", () => {
  let t = started();
  assert.equal(pollFailed(t, {status: 404}).phase, "expired");
  for (let i = 0; i < MAX_POLL_FAILURES - 1; i++){ t = pollFailed(t, {message: "net"}); assert.equal(t.phase, "tracking"); }
  assert.equal(nextAction(t).delay, POLL_RETRY_MS);
  t = pollFailed(t, {message: "net"});
  assert.equal(t.phase, "lost");
  assert.equal(resume(t).phase, "tracking");
  assert.equal(nextAction(started()).delay, POLL_MS);
  assert.equal(nextAction(idleTask(DS)), null);
  assert.equal(pollFailed(idleTask(DS), {status: 404}).phase, "idle");
});

test("cancelling: requested once, a failure keeps the job and says so", () => {
  const t = started();
  const c = cancelRequested(t);
  assert.equal(c.cancelling, true); assert.equal(cancelRequested(c), c);
  assert.equal(panelState(c).canCancel, false);
  assert.equal(panelState(t).canCancel, true);
  assert.match(cancelFailed(c, {message: "x"}).error, /Could not cancel/);
  assert.equal(cancelFailed(c, {status: 404}).phase, "expired");
  const cancelled = progressed(c, job({state: "cancelled", message: "cancelled"}));
  assert.equal(panelState(cancelled).kind, "cancelled");
});

test("the panel's states: working, done, failed with advice", () => {
  assert.equal(panelState(beginStart(idleTask(DS), EXP)).locked, true);
  assert.equal(panelState(started()).kind, "queued");
  assert.equal(panelState(progressed(started(), job({state: "running", completed: 10}))).kind, "running");
  assert.equal(panelState(progressed(started(), job({state: "done", completed: 100}))).kind, "done");
  const bad = panelState(progressed(started(), job({state: "error", error: "boom", error_code: "incompatible"})));
  assert.equal(bad.kind, "error"); assert.match(bad.message, /boom/); assert.match(bad.advice, /earlier predictions were kept/);
  assert.match(failureAdvice({error_code: "experiment_unavailable"}), /deleted or damaged/);
  assert.match(failureAdvice({error_code: "dataset_or_model_changed"}), /discarded/);
  assert.match(panelState(pollFailed(started(), {status: 404})).message, /no longer knows/);
});

test("progress is words and a fraction, and a finished job says how many rows have no number", () => {
  assert.equal(progressText(job()), "waiting for a free slot");
  assert.equal(progressText(job({state: "running", stage: "predicting", completed: 30})), "predicting · 30 of 100 rows");
  assert.equal(fraction(job({completed: 25})), 0.25);
  assert.equal(fraction(job({total: 0})), null);
  assert.equal(doneText(job({state: "done", n_failed: 0})), "100 of 100 rows predicted.");
  assert.match(doneText(job({state: "done", n_failed: 3})), /97 of 100 rows predicted; 3 could not be/);
});

const COLS = [
  {key: "smiles", kind: "derived", type: "text", label: "SMILES"},
  {key: "pred_x_value", kind: "prediction", type: "number", label: "alpha (12345678) · predicted pKi (pKi)", meta: {set: "pred_x", output: "value", units: "pKi", experiment_name: "alpha"}},
  {key: "pred_y_class", kind: "prediction", type: "text", label: "beta (87654321) · predicted class", meta: {set: "pred_y", output: "class", units: "", experiment_name: "beta"}},
  {key: "pred_y_p0", kind: "prediction", type: "number", label: "beta (87654321) · P(a)", meta: {set: "pred_y", output: "p0", units: "", class: "a", experiment_name: "beta"}},
];

test("a prediction is worded with its units, a class as text and a missing value as a dash", () => {
  assert.equal(cellText(COLS[1], 6.12345), "6.123 pKi");        // pKi stays pKi
  assert.equal(cellText(COLS[2], "a"), "a");
  assert.equal(cellText(COLS[3], 0.5), "0.5");
  assert.equal(cellText(COLS[1], null), "—");
  assert.equal(cellText(COLS[1], undefined), "—");
  assert.deepEqual(predictionColumns(COLS).map(c => c.key), ["pred_x_value", "pred_y_class", "pred_y_p0"]);
});

test("a card names the model beside its primary output; a row that could not be predicted shows its reason in the detail", () => {
  const row = {predictions: {pred_x_value: 6.1, pred_y_class: "a", pred_y_p0: 0.8}, prediction_errors: {}};
  assert.deepEqual(cardPredictions(row, COLS).map(c => [c.label, c.text]), [["alpha", "6.1 pKi"], ["beta", "a"]]);
  const failed = {predictions: {pred_y_class: "a", pred_y_p0: 0.8}, prediction_errors: {pred_x: "the structure did not parse"}};
  const d = detailPredictions(failed, COLS);
  assert.equal(d.find(r => r.label.startsWith("alpha")).text, "no prediction");
  assert.equal(d.find(r => r.label.startsWith("alpha")).why, "the structure did not parse");
  assert.equal(d.find(r => r.label.includes("P(a)")).text, "0.8");
  assert.deepEqual(cardPredictions({predictions: {}}, COLS), []);
  assert.deepEqual(cardPredictions({}, COLS), []);
});

test("the kept sets are listed, those of a deleted model are labelled", () => {
  const rows = setRows([
    {key: "k1", experiment: {name: "alpha", id: "i1"}, source_deleted: false, n_rows: 10, n_failed: 0, outputs: [{label: "L1"}]},
    {key: "k2", experiment: {name: "beta", id: "i2"}, source_deleted: true, n_rows: 10, n_failed: 2, outputs: [{label: "L2"}]},
  ]);
  assert.equal(rows[0].note, "every row predicted");
  assert.equal(rows[1].deleted, true);
  assert.match(rows[1].note, /source model deleted · 2 of 10 rows have no prediction/);
});

test("when a column goes, a query that named it is cleaned, and one that did not is left alone", () => {
  const q = {offset: 50, limit: 50, sort: {key: "pred_x_value", dir: "desc"}, filters: [{key: "pred_x_value", op: "gt", value: 1}, {key: "smiles", op: "contains", value: "C"}]};
  const gone = pruneQuery(q, [COLS[0]]);
  assert.deepEqual(gone.sort, {key: "row_id", dir: "asc"});
  assert.deepEqual(gone.filters.map(f => f.key), ["smiles"]);
  assert.equal(gone.offset, 0);
  const keep = pruneQuery(q, COLS);
  assert.equal(keep, q);
  assert.notEqual(schemaKey({dataset_id: DS, schema: COLS}), schemaKey({dataset_id: DS, schema: [COLS[0]]}));
  assert.equal(schemaKey(null), "");
});
