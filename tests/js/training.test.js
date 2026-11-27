// The training panel's own logic -- the seed, the gate, one run's states and errors,
// expiry, a strategy with no valid split, stale answers, and how a completed run's
// numbers are worded and laid side by side: run with `npm test`. The run's shape
// is the QSAR run contract's (proteinviewer's /api/training routes and run.json).
import {test} from "node:test";
import assert from "node:assert/strict";

import {
  MAX_SEED, validateSeed, newForm, buildRequest, profileFromSearch, otherStrategy, gate, datasetRows,
  idleTask, beginStart, startFailed, startError, progressed, pollFailed, cancelRequested, cancelFailed, resume,
  resultLoaded, resultFailed, resultRetry, nextAction, panelState, fraction, stageText, formatElapsed, isTerminal,
  metricLabel, metricNumber, aggregateCell, unavailableView, configText, metricsView, productionView,
  STRATEGIES, STRATEGY_HELP, CAVEATS, TEMPORARY_NOTE, MAX_POLL_FAILURES, POLL_MS, POLL_RETRY_MS,
} from "../../proteinviewer/app/js/model/training.js";

const DS = "ds-0123456789ab";

// ---- the seed ----

test("a seed is a whole number from 0 to 4294967295", () => {
  assert.deepEqual(validateSeed("0"), {ok: true, value: 0});
  assert.deepEqual(validateSeed(" 42 "), {ok: true, value: 42});
  assert.deepEqual(validateSeed("007"), {ok: true, value: 7});
  assert.deepEqual(validateSeed(String(MAX_SEED)), {ok: true, value: 4294967295});
  assert.equal(validateSeed("4294967296").ok, false);
  assert.equal(validateSeed("99999999999999999999999").ok, false);
});

test("a seed that is not a whole number is refused with a reason", () => {
  for (const bad of ["", "  ", "-1", "+1", "1.5", "1e3", "0x10", "abc", "1 2", "١٢"]){
    const r = validateSeed(bad);
    assert.equal(r.ok, false, JSON.stringify(bad));
    assert.match(r.error, /whole number|seed/i);
  }
  assert.equal(validateSeed(null).ok, false);
  assert.equal(validateSeed(undefined).ok, false);
});

test("the form starts at seed 0 and the scaffold strategy", () => {
  assert.deepEqual(newForm(), {seedText: "0", strategy: "scaffold"});
  assert.deepEqual(STRATEGIES.map(s => s.value), ["scaffold", "random"]);
  assert.match(STRATEGY_HELP, /production/);
  assert.match(STRATEGY_HELP, /never chosen from test scores/);
});

test("a request is the form's choices, and the profile is full unless asked", () => {
  const r = buildRequest(DS, {seedText: "5", strategy: "random"}, "full");
  assert.deepEqual(r, {ok: true, body: {dataset_id: DS, seed: 5, production_strategy: "random", profile: "full"}});
  assert.equal(buildRequest(DS, newForm(), "tiny").body.profile, "tiny");
  assert.equal(buildRequest(DS, newForm(), "bogus").body.profile, "full");
  assert.equal(buildRequest(DS, {seedText: "x", strategy: "random"}, "full").ok, false);
  assert.equal(buildRequest(DS, {seedText: "1", strategy: "nope"}, "full").ok, false);
  assert.equal(buildRequest(null, newForm(), "full").ok, false);
});

test("only ?profile=tiny asks for the tiny search", () => {
  assert.equal(profileFromSearch("?profile=tiny"), "tiny");
  assert.equal(profileFromSearch("?a=1&profile=tiny"), "tiny");
  assert.equal(profileFromSearch("?profile=tiny&a=1"), "tiny");
  assert.equal(profileFromSearch("?profile=tinyish"), "full");
  assert.equal(profileFromSearch("?xprofile=tiny"), "full");
  assert.equal(profileFromSearch(""), "full");
  assert.equal(profileFromSearch(undefined), "full");
});

test("the other strategy is the other one", () => {
  assert.equal(otherStrategy("scaffold"), "random");
  assert.equal(otherStrategy("random"), "scaffold");
});

// ---- the gate ----

const regMeta = (over) => Object.assign({
  dataset_id: DS, name: "hiv.csv", n_rows: 106, n_training_eligible: 102, trainable: true,
  target: {column: "Ki (nM)", kind: "regression", transform: "pki", output_units: "pKi", source_units: "nM"},
  exclusions: {censored: 4},
}, over);

test("training is offered for a ready dataset with a target and eligible rows", () => {
  assert.deepEqual(gate({ready: regMeta(), staged: null}), {ok: true, show: true, reason: ""});
});

test("training is disabled, with the reason, without a target", () => {
  const g = gate({ready: regMeta({target: null, trainable: false}), staged: null});
  assert.equal(g.ok, false);
  assert.equal(g.show, true);
  assert.match(g.reason, /no target/);
});

test("training is disabled for a staged upload, and nothing at all when no dataset is open", () => {
  const g = gate({ready: null, staged: {dataset_id: DS}});
  assert.equal(g.ok, false);
  assert.equal(g.show, true);
  assert.match(g.reason, /waiting for its columns/);
  assert.deepEqual(gate({ready: null, staged: null}), {ok: false, show: false, reason: ""});
  assert.equal(gate(null).show, false);
});

test("a dataset with no eligible row cannot train", () => {
  const g = gate({ready: regMeta({n_training_eligible: 0}), staged: null});
  assert.equal(g.ok, false);
  assert.match(g.reason, /No row/);
});

test("the dataset's lines name the target, task, units and the counts", () => {
  const rows = Object.fromEntries(datasetRows(regMeta()));
  assert.equal(rows["Target"], "Ki (nM)");
  assert.equal(rows["Task"], "regression");
  assert.match(rows["Units / transform"], /pKi = 9 − log10\(Ki in nM\)/);
  assert.equal(rows["Eligible to train"], "102 of 106");
  assert.equal(rows["Excluded"], "4 of 106");
  assert.match(rows["Why"], /4 the target is a bound/);
});

test("units without a transform, and a classification's positive class", () => {
  const plain = Object.fromEntries(datasetRows(regMeta({target: {column: "y", kind: "regression", transform: "identity", output_units: "uM"}})));
  assert.equal(plain["Units / transform"], "uM, values as they are");
  const none = Object.fromEntries(datasetRows(regMeta({target: {column: "y", kind: "regression", transform: "identity", output_units: ""}})));
  assert.match(none["Units / transform"], /no units stated/);
  const cls = Object.fromEntries(datasetRows(regMeta({target: {column: "act", kind: "classification", positive_class: "1"}})));
  assert.equal(cls["Task"], "classification (positive class: 1)");
  assert.equal(cls["Units / transform"], "—");
  const zero = Object.fromEntries(datasetRows(regMeta({target: {column: "act", kind: "classification", positive_class: 0}})));
  assert.equal(zero["Task"], "classification (positive class: 0)");   // 0 is a class, not "none"
  assert.deepEqual(datasetRows(null), []);
});

test("nothing is excluded: no 'Why' line", () => {
  const rows = Object.fromEntries(datasetRows(regMeta({n_training_eligible: 106, exclusions: {}})));
  assert.equal(rows["Excluded"], "0 of 106");
  assert.equal("Why" in rows, false);
});

test("a target-free dataset says so", () => {
  assert.deepEqual(datasetRows(regMeta({target: null})), [["Target", "none — prediction only"], ["Rows", "106"]]);
});

// ---- one run's life ----

const prog = (state, extra) => Object.assign({id: "run-1", state, stage: "", completed: 0, total: 0, elapsed: 0}, extra || {});
const REQ = {dataset_id: DS, seed: 0, production_strategy: "scaffold", profile: "full"};
const started = () => beginStart(idleTask(DS), REQ);

test("a task is idle until a run starts, then starting, then tracked", () => {
  assert.equal(panelState(idleTask(DS)).kind, "idle");
  const s = started();
  assert.equal(panelState(s).kind, "starting");
  assert.equal(panelState(s).locked, true);
  assert.equal(panelState(s).canTrain, false);
  const q = progressed(s, prog("queued"));
  assert.equal(panelState(q).kind, "queued");
  assert.equal(panelState(q).canCancel, true);
  assert.equal(panelState(q).locked, true);
  const r = progressed(q, prog("running", {stage: "tuning", completed: 3, total: 130}));
  assert.equal(panelState(r).kind, "running");
  assert.equal(panelState(r).canCancel, true);
  const f = progressed(r, prog("finishing", {completed: 130, total: 131}));
  assert.equal(panelState(f).kind, "finishing");
  assert.equal(panelState(f).canCancel, false);     // not once the run is being written
});

test("a run can only move forward: a late 'running' after 'finishing' is ignored", () => {
  let t = progressed(started(), prog("finishing"));
  const late = progressed(t, prog("running", {completed: 1, total: 10}));
  assert.strictEqual(late, t);
});

test("a terminal state is final", () => {
  const done = progressed(started(), prog("cancelled"));
  assert.equal(done.phase, "terminal");
  assert.strictEqual(progressed(done, prog("running")), done);
  assert.strictEqual(progressed(done, prog("done")), done);
});

test("an answer for another run is ignored (a stale response after a new start)", () => {
  const t = progressed(started(), prog("running", {id: "run-2"}));
  assert.equal(t.run.id, "run-2");
  const stale = progressed(t, prog("done", {id: "run-1"}));
  assert.strictEqual(stale, t);
  assert.equal(progressed(idleTask(DS), prog("running")).phase, "idle");   // nothing was asked for
  assert.equal(progressed(started(), null).phase, "starting");
  assert.equal(progressed(started(), {state: "running"}).phase, "starting");   // no id
});

test("a state the page does not know ends the run with a message", () => {
  const t = progressed(started(), prog("exploded"));
  const v = panelState(t);
  assert.equal(v.kind, "error");
  assert.match(v.message, /unknown state “exploded”/);
});

test("a done run fetches its record, and shows it once it is there", () => {
  const d = progressed(progressed(started(), prog("running")), prog("done"));
  assert.equal(d.phase, "terminal");
  assert.equal(panelState(d).kind, "loading_result");
  assert.deepEqual(nextAction(d), {what: "result", delay: 0});
  const ready = resultLoaded(d, {schema: "qsar-run-v1"});
  assert.equal(panelState(ready).kind, "done");
  assert.equal(nextAction(ready), null);
  assert.equal(ready.result.data.schema, "qsar-run-v1");
});

test("a result that arrives for a run that is not done changes nothing", () => {
  const t = progressed(started(), prog("running"));
  assert.strictEqual(resultLoaded(t, {}), t);
  assert.strictEqual(resultFailed(t, new Error("x")), t);
  const c = progressed(started(), prog("cancelled"));
  assert.strictEqual(resultLoaded(c, {}), c);
});

test("a result that cannot be read says why, and can be tried again", () => {
  const d = progressed(started(), prog("done"));
  const bad = resultFailed(d, {message: "HTTP 500", status: 500});
  assert.equal(panelState(bad).kind, "result_failed");
  assert.match(panelState(bad).message, /Could not read the run's results: HTTP 500/);
  assert.equal(nextAction(bad), null);
  const again = resultRetry(bad);
  assert.equal(panelState(again).kind, "loading_result");
  assert.deepEqual(nextAction(again), {what: "result", delay: 0});
  assert.strictEqual(resultRetry(d), d);     // nothing failed: nothing to retry
});

test("a run that expires while its record is read is an expiry", () => {
  const d = progressed(started(), prog("done"));
  const t = resultFailed(d, {message: "no such run", status: 404});
  assert.equal(panelState(t).kind, "expired");
  assert.match(panelState(t).message, /no longer available; train again/);
});

test("a failed job shows the server's error and no cancel", () => {
  const t = progressed(started(), prog("error", {error: "ligandbench exited with status 2: target has no values"}));
  const v = panelState(t);
  assert.equal(v.kind, "error");
  assert.equal(v.message, "ligandbench exited with status 2: target has no values");
  assert.equal(v.canCancel, false);
  assert.equal(v.canTrain, true);
  assert.equal(panelState(progressed(started(), prog("error"))).message, "The run failed; the server gave no reason.");
});

test("a cancelled run is a terminal state that offers a new run", () => {
  const v = panelState(progressed(started(), prog("cancelled")));
  assert.equal(v.kind, "cancelled");
  assert.equal(v.canTrain, true);
  assert.equal(nextAction(progressed(started(), prog("cancelled"))), null);
});

test("the production strategy with no valid split is not a plain error: it offers the other strategy", () => {
  const unavailable = {repeat: 0, message: "no split keeps the largest scaffold group in one subset", candidates_tried: 256,
    n_rows: 40, n_groups: 3, group_size_counts: [[30, 1], [5, 2]], class_counts: {},
    failed_constraints: [{constraint: "min_size", subset: "test", candidates_violating: 256, detail: "test would have 0 rows; at least 2 are needed"}]};
  const t = progressed(started(), prog("error", {error: "the scaffold split is unavailable", error_code: "production_strategy_unavailable", unavailable}));
  const v = panelState(t);
  assert.equal(v.kind, "unavailable");
  assert.equal(v.strategy, "scaffold");     // what was asked for
  assert.equal(v.other, "random");
  assert.equal(v.view.message, unavailable.message);
  assert.deepEqual(v.view.lines, [
    "No valid split for repeat 1 of 5 after 256 candidates.",
    "40 eligible rows in 3 groups; the largest group has 30 rows.",
    "test would have 0 rows; at least 2 are needed — broken by 256 of 256 candidates",
  ]);
});

test("the unavailable object may arrive with its strategy beside it", () => {
  const wrapped = {strategy: "random", unavailable: {repeat: 2, message: "m", n_rows: 4, n_groups: 4}};
  const v = panelState(progressed(beginStart(idleTask(DS), Object.assign({}, REQ, {production_strategy: "random"})),
    prog("error", {error_code: "production_strategy_unavailable", unavailable: wrapped})));
  assert.equal(v.kind, "unavailable");
  assert.equal(v.strategy, "random");
  assert.equal(v.other, "scaffold");
  assert.equal(v.view.message, "m");
  assert.match(v.view.lines[0], /repeat 3 of 5\.$/);
});

test("an unavailable report with nothing in it still says something", () => {
  const v = panelState(progressed(started(), prog("error", {error_code: "production_strategy_unavailable"})));
  assert.equal(v.kind, "unavailable");
  assert.match(v.view.message, /No valid split/);
  assert.deepEqual(v.view.lines, []);
  assert.deepEqual(unavailableView(null).lines, []);
});

test("class counts and largest groups appear in the compact reason", () => {
  const v = unavailableView({repeat: 4, message: "m", n_rows: 12, n_groups: 6, largest_groups: [{key: "c1ccccc1", n_rows: 7}],
    class_counts: {a: 5, b: 1}, candidates_tried: 3, failed_constraints: [{constraint: "class_coverage", subset: "validation"}]});
  assert.deepEqual(v.lines, [
    "No valid split for repeat 5 of 5 after 3 candidates.",
    "12 eligible rows in 6 groups; the largest group has 7 rows.",
    "Class counts: a 5, b 1.",
    "class_coverage (validation)",
  ]);
});

// ---- errors on the way in ----

test("a refused start says what the server said, by status", () => {
  const e = (status, message) => startError({status, message});
  assert.match(e(404, "unknown dataset"), /does not know this dataset any more \(unknown dataset\)/);
  assert.match(e(409, "dataset is staged"), /cannot start this now: dataset is staged/);
  assert.match(e(409, "the training queue is full"), /queue is full/);
  assert.match(e(400, "seed must be at most 4294967295"), /refused this request: seed must be at most/);
  assert.match(e(503, "xgboost is not installed"), /cannot train: xgboost is not installed/);
  assert.match(e(500, "boom"), /Could not start training: boom/);
  assert.match(startError({message: "x", stale: true}), /predates training/);
  assert.match(startError(new Error("net down")), /Could not start training: net down/);
});

test("a failed start is shown, keeps the request, and can be tried again", () => {
  const t = startFailed(started(), {status: 409, message: "queue is full"});
  const v = panelState(t);
  assert.equal(v.kind, "start_failed");
  assert.match(v.message, /queue is full/);
  assert.equal(v.canTrain, true);
  assert.equal(v.locked, false);
  assert.deepEqual(t.request, REQ);
  assert.equal(nextAction(t), null);
  // and a start that is not pending cannot fail
  const idle = idleTask(DS);
  assert.strictEqual(startFailed(idle, {message: "x"}), idle);
});

test("starting again discards the old run", () => {
  const old = progressed(started(), prog("error", {error: "x"}));
  const again = beginStart(old, REQ);
  assert.equal(again.run, null);
  assert.equal(again.phase, "starting");
  assert.equal(again.result.status, "none");
});

// ---- polling, expiry, lost contact ----

test("polling runs while a run is queued, running or finishing, and stops at a terminal state", () => {
  for (const s of ["queued", "running", "finishing"]){
    const n = nextAction(progressed(started(), prog(s)));
    assert.deepEqual(n, {what: "poll", delay: POLL_MS}, s);
  }
  for (const s of ["error", "cancelled"]) assert.equal(nextAction(progressed(started(), prog(s))), null, s);
  assert.equal(nextAction(idleTask(DS)), null);
  assert.equal(nextAction(started()), null);       // the POST is still out: nothing to poll
  assert.equal(isTerminal("done") && isTerminal("error") && isTerminal("cancelled"), true);
  assert.equal(isTerminal("running"), false);
});

test("a 404 on a poll is the run's expiry", () => {
  const t = pollFailed(progressed(started(), prog("running")), {status: 404, message: "unknown run"});
  const v = panelState(t);
  assert.equal(v.kind, "expired");
  assert.match(v.message, /This run is no longer available; train again\./);
  assert.equal(v.canTrain, true);
  assert.equal(nextAction(t), null);
});

test("a server too old for the route is not an expiry", () => {
  let t = progressed(started(), prog("running"));
  t = pollFailed(t, {status: 404, message: "this serve.py predates this page", stale: true});
  assert.equal(panelState(t).kind, "lost");
});

test("a few failed polls are retried before the run is called lost", () => {
  let t = progressed(started(), prog("running"));
  for (let i = 1; i < MAX_POLL_FAILURES; i++){
    t = pollFailed(t, new Error("network down"));
    assert.equal(panelState(t).kind, "running", "failure " + i);
    assert.deepEqual(nextAction(t), {what: "poll", delay: POLL_RETRY_MS});
  }
  t = pollFailed(t, new Error("network down"));
  const v = panelState(t);
  assert.equal(v.kind, "lost");
  assert.match(v.message, /Lost contact with the server \(network down\)/);
  assert.equal(nextAction(t), null);
});

test("after lost contact the panel can look again, and finds the run still going", () => {
  let t = progressed(started(), prog("running"));
  for (let i = 0; i < MAX_POLL_FAILURES; i++) t = pollFailed(t, new Error("down"));
  assert.equal(panelState(t).kind, "lost");
  const r = resume(t);
  assert.equal(panelState(r).kind, "running");
  assert.equal(r.failures, 0);
  assert.deepEqual(nextAction(r), {what: "poll", delay: POLL_MS});
  assert.strictEqual(resume(progressed(started(), prog("running"))).phase, "tracking");   // not lost: unchanged
  assert.equal(resume(idleTask(DS)).phase, "idle");
});

test("an answer after a failure resets the count", () => {
  let t = progressed(started(), prog("running"));
  t = pollFailed(t, new Error("x")); t = pollFailed(t, new Error("x"));
  assert.equal(t.failures, 2);
  t = progressed(t, prog("running", {completed: 4, total: 9}));
  assert.equal(t.failures, 0);
});

test("a poll failure outside tracking changes nothing", () => {
  const t = progressed(started(), prog("cancelled"));
  assert.strictEqual(pollFailed(t, new Error("x")), t);
});

// ---- cancel ----

test("cancel is offered while queued or running, once", () => {
  const r = progressed(started(), prog("running"));
  assert.equal(panelState(r).canCancel, true);
  const c = cancelRequested(r);
  assert.equal(panelState(c).canCancel, false);
  assert.equal(panelState(c).cancelling, true);
  assert.strictEqual(cancelRequested(c), c);
  const done = progressed(c, prog("cancelled"));
  assert.equal(panelState(done).kind, "cancelled");
  assert.equal(done.cancelling, false);
});

test("a cancel that fails is said and can be tried again; one for a vanished run is an expiry", () => {
  const c = cancelRequested(progressed(started(), prog("running")));
  const bad = cancelFailed(c, {message: "HTTP 500", status: 500});
  assert.equal(panelState(bad).kind, "running");
  assert.equal(panelState(bad).canCancel, true);
  assert.match(panelState(bad).message, /Could not cancel: HTTP 500/);
  assert.equal(panelState(cancelFailed(c, {message: "gone", status: 404})).kind, "expired");
});

// ---- the bar and the text ----

test("the progress fraction is clamped, and unknown until there is a total", () => {
  assert.equal(fraction(prog("running", {completed: 3, total: 12})), 0.25);
  assert.equal(fraction(prog("running", {completed: 20, total: 12})), 1);
  assert.equal(fraction(prog("running", {completed: -2, total: 12})), 0);
  assert.equal(fraction(prog("running", {completed: 0, total: 0})), null);
  assert.equal(fraction(prog("queued")), null);
  assert.equal(fraction(null), null);
});

test("the stage line says what the run is doing", () => {
  assert.equal(stageText(prog("queued")), "waiting for a free slot");
  assert.equal(stageText(prog("running", {stage: "tuning", completed: 3, total: 130})), "tuning · 3 of 130 fits");
  assert.equal(stageText(prog("finishing", {total: 131, completed: 131})), "writing the run · 131 of 131 fits");
  assert.equal(stageText(prog("running", {stage: "featurizing"})), "featurizing");
  assert.equal(stageText(null), "");
});

test("elapsed time reads as a person says it", () => {
  assert.equal(formatElapsed(0), "0 s");
  assert.equal(formatElapsed(7.9), "7 s");
  assert.equal(formatElapsed(83), "1 min 23 s");
  assert.equal(formatElapsed(120), "2 min 00 s");
  assert.equal(formatElapsed(3725), "1 h 2 min");
  assert.equal(formatElapsed(null), "");
  assert.equal(formatElapsed(-1), "");
  assert.equal(formatElapsed(NaN), "");
  assert.equal(formatElapsed(undefined), "");
});

// ---- formatting a metric ----

test("a metric is four significant figures, exponent only where a plain number would be a wall of digits", () => {
  assert.equal(metricNumber(0.81234567), "0.8123");
  assert.equal(metricNumber(0), "0");
  assert.equal(metricNumber(-0.1234567), "-0.1235");
  assert.equal(metricNumber(12345.678), "12350");
  assert.equal(metricNumber(0.00001234), "1.23e-5");
  assert.equal(metricNumber(0.00001), "1e-5");
  assert.equal(metricNumber(123456789), "1.23e+8");
  assert.equal(metricNumber(1), "1");
});

test("a null or non-finite metric is 'undefined', never 0 or NaN", () => {
  for (const v of [null, undefined, NaN, Infinity, -Infinity]) assert.equal(metricNumber(v), "undefined");
});

test("an aggregate is mean, sample SD and how many repeats it covers", () => {
  const c = aggregateCell({mean: 0.8123, sd: 0.0345, n_defined: 5, n_total: 5}, []);
  assert.equal(c.mean, "0.8123");
  assert.equal(c.sd, "0.0345");
  assert.equal(c.defined, "5 of 5");
  assert.equal(c.undefinedAll, false);
  assert.equal(c.partial, false);
});

test("a single defined value has a mean and no SD, and says why", () => {
  const c = aggregateCell({mean: 0.7, sd: null, n_defined: 1, n_total: 5}, ["only one class in test"]);
  assert.equal(c.mean, "0.7");
  assert.equal(c.sd, "n/a");
  assert.match(c.sdTitle, /at least two/);
  assert.equal(c.defined, "1 of 5");
  assert.equal(c.partial, true);
  assert.equal(c.why, "only one class in test");
});

test("no defined value is 'undefined' with its reason", () => {
  const c = aggregateCell({mean: null, sd: null, n_defined: 0, n_total: 5}, ["a", "b"]);
  assert.equal(c.mean, "undefined");
  assert.equal(c.sd, "—");
  assert.equal(c.defined, "0 of 5");
  assert.equal(c.undefinedAll, true);
  assert.equal(c.why, "a; b");
  assert.equal(c.whyText, "undefined in 5 of 5: a; b");
  assert.equal(c.title, "a; b");
});

test("a mean that is zero is a mean, not an undefined one", () => {
  const c = aggregateCell({mean: 0, sd: 0, n_defined: 5, n_total: 5}, []);
  assert.equal(c.mean, "0");
  assert.equal(c.sd, "0");
  assert.equal(c.undefinedAll, false);
});

test("a metric the run has no aggregate for is a dash", () => {
  assert.equal(aggregateCell(undefined, []).mean, "—");
});

test("metrics are labelled as the issue words them", () => {
  assert.equal(metricLabel("rmse"), "RMSE");
  assert.equal(metricLabel("r2"), "R²");
  assert.equal(metricLabel("average_precision"), "average precision");
  assert.equal(metricLabel("balanced_accuracy"), "balanced accuracy");
  assert.equal(metricLabel("log_loss"), "log loss");
  assert.equal(metricLabel("roc_auc"), "ROC-AUC");
  assert.equal(metricLabel("some_new_metric"), "some new metric");
  // never the ambiguous name
  for (const k of ["pr_auc", "prauc", "auprc", "ap", "average_precision"]) assert.equal(metricLabel(k), "average precision", k);
  assert.doesNotMatch(["pr_auc", "average_precision", "roc_auc"].map(k => metricLabel(k)).join(" "), /PR-AUC/i);
});

test("a multiclass AUC and average precision say they are macro one-vs-rest", () => {
  const multi = {task: "classification", classes: ["a", "b", "c"]};
  assert.equal(metricLabel("roc_auc", multi), "ROC-AUC (macro one-vs-rest)");
  assert.equal(metricLabel("average_precision", multi), "average precision (macro one-vs-rest)");
  assert.equal(metricLabel("log_loss", multi), "log loss");
  assert.equal(metricLabel("roc_auc", {task: "classification", classes: ["0", "1"]}), "ROC-AUC");
});

test("a configuration is its keys and values in order", () => {
  assert.equal(configText({max_depth: 6, learning_rate: 0.1, n_estimators: 300}), "max_depth 6 · learning_rate 0.1 · n_estimators 300");
  assert.equal(configText(null), "");
  assert.equal(configText({}), "");
});

// ---- a completed run, side by side ----

const S = (status, extra) => Object.assign({status, unavailable: null, repeats: [], aggregate: {}}, extra);
const okRepeats = (metrics) => [0, 1, 2, 3, 4].map(r => ({
  repeat: r, seed: 1000 + r, config: {max_depth: 3, learning_rate: 0.1, n_estimators: 100},
  validation: [], test: Object.assign({}, metrics), undefined: {},
}));
const agg = (mean, sd, nd, nt) => ({mean, sd, n_defined: nd, n_total: nt === undefined ? 5 : nt});

function regRun(){
  return {
    schema: "qsar-run-v1", status: "completed", seed: 3, profile: "tiny",
    dataset: {id: DS, content_hash: "0123456789abcdef0123", n_rows: 106, n_eligible: 102},
    target: {column: "Ki", task: "regression", units: "pKi", transform: "pki", classes: null, positive_class: null},
    production: {strategy: "scaffold", config: {max_depth: 6, learning_rate: 0.05, n_estimators: 300}, mean_validation_loss: 0.5512, n_refit_rows: 102, tie_break: []},
    strategies: {
      random: S("ok", {repeats: okRepeats({rmse: 0.5, mae: 0.4, r2: 0.7, spearman: 0.8}),
        aggregate: {rmse: agg(0.55, 0.04, 5), mae: agg(0.41, 0.03, 5), r2: agg(0.71, 0.05, 5), spearman: agg(0.82, 0.02, 5)}}),
      scaffold: S("ok", {repeats: okRepeats({rmse: 0.7, mae: 0.5, r2: 0.4, spearman: 0.6}),
        aggregate: {rmse: agg(0.72, 0.09, 5), mae: agg(0.52, 0.06, 5), r2: agg(0.41, 0.12, 5), spearman: agg(0.61, 0.07, 5)}}),
    },
  };
}

test("both strategies are laid side by side, a row a metric, the production one marked", () => {
  const v = metricsView(regRun());
  assert.deepEqual(v.cols.map(c => [c.strategy, c.label, c.production, c.ok]),
    [["random", "Random", false, true], ["scaffold", "Scaffold", true, true]]);
  assert.deepEqual(v.rows.map(r => r.label), ["RMSE", "MAE", "R²", "Spearman ρ"]);
  const rmse = v.rows[0];
  assert.equal(rmse.cells[0].mean, "0.55");
  assert.equal(rmse.cells[0].sd, "0.04");
  assert.equal(rmse.cells[1].mean, "0.72");
  assert.equal(rmse.cells[1].defined, "5 of 5");
  assert.equal(v.unitsNote, "RMSE and MAE are in pKi.");
  assert.equal(v.repeats.length, 2);
});

test("an unavailable strategy has its reason, not numbers; the other still shows", () => {
  const run = regRun();
  run.strategies.scaffold = S("split_unavailable", {unavailable: {repeat: 0, message: "no valid scaffold split", n_rows: 20, n_groups: 2}});
  run.production.strategy = "random";
  const v = metricsView(run);
  assert.equal(v.cols[1].ok, false);
  assert.equal(v.cols[1].unavailable.message, "no valid scaffold split");
  assert.equal(v.cols[0].production, true);
  assert.equal(v.rows[0].cells[1], null);
  assert.equal(v.rows[0].cells[0].mean, "0.55");
  assert.deepEqual(v.okNames, ["random"]);
  assert.equal(v.repeats.length, 1);
});

test("both strategies unavailable: no metric rows at all", () => {
  const run = regRun();
  run.strategies.random = S("split_unavailable", {unavailable: {message: "a"}});
  run.strategies.scaffold = S("split_unavailable", {unavailable: {message: "b"}});
  const v = metricsView(run);
  assert.deepEqual(v.rows, []);
  assert.deepEqual(v.repeats, []);
});

test("a classification run's metrics, with an undefined one and the reason on the cell", () => {
  const run = regRun();
  run.target = {column: "act", task: "classification", units: null, transform: null, classes: ["0", "1"], positive_class: "1"};
  const reps = okRepeats({roc_auc: 0.9, average_precision: 0.8, balanced_accuracy: 0.7, log_loss: 0.4});
  reps[1].test.roc_auc = null; reps[1].undefined = {roc_auc: "the test set holds one class"};
  reps[3].test.roc_auc = null; reps[3].undefined = {roc_auc: "the test set holds one class"};
  run.strategies.random = S("ok", {repeats: reps, aggregate: {
    roc_auc: agg(0.9, 0.01, 3), average_precision: agg(0.8, 0.02, 5), balanced_accuracy: agg(0.7, 0.03, 5), log_loss: agg(0.4, 0.05, 5)}});
  run.strategies.scaffold = S("ok", {repeats: okRepeats({roc_auc: 0.8}), aggregate: {
    roc_auc: agg(null, null, 0), average_precision: agg(0.7, null, 1), balanced_accuracy: agg(0.6, 0.1, 5), log_loss: agg(0.5, 0.1, 5)}});
  const v = metricsView(run);
  assert.deepEqual(v.rows.map(r => r.label), ["ROC-AUC", "average precision", "balanced accuracy", "log loss"]);
  assert.equal(v.unitsNote, "");
  const auc = v.rows[0];
  assert.equal(auc.cells[0].defined, "3 of 5");
  assert.equal(auc.cells[0].partial, true);
  assert.equal(auc.cells[0].why, "the test set holds one class");     // de-duplicated across repeats
  assert.equal(auc.cells[1].mean, "undefined");
  assert.equal(v.rows[1].cells[1].sd, "n/a");
  // the per-repeat table shows the undefined repeat with its reason
  const rr = v.repeats[0].rows;
  assert.equal(rr[1].values[0].text, "undefined");
  assert.equal(rr[1].values[0].why, "the test set holds one class");
  assert.equal(rr[0].values[0].text, "0.9");
  assert.equal(rr[0].values[0].why, "");
  assert.equal(rr[0].repeat, 1);
  assert.equal(rr[0].config, "max_depth 3 · learning_rate 0.1 · n_estimators 100");
});

test("a metric the run reports beyond the known ones follows them", () => {
  const run = regRun();
  run.strategies.random.aggregate.extra_thing = agg(1, 0.1, 5);
  const labels = metricsView(run).rows.map(r => r.label);
  assert.deepEqual(labels, ["RMSE", "MAE", "R²", "Spearman ρ", "extra thing"]);
});

test("pr_auc under another key is still labelled average precision, once", () => {
  const run = regRun();
  run.target.task = "classification"; run.target.classes = ["0", "1"];
  run.strategies.random.aggregate = {roc_auc: agg(0.9, 0.1, 5), pr_auc: agg(0.8, 0.1, 5)};
  run.strategies.scaffold.aggregate = {roc_auc: agg(0.8, 0.1, 5), pr_auc: agg(0.7, 0.1, 5)};
  const v = metricsView(run);
  assert.deepEqual(v.rows.map(r => r.label), ["ROC-AUC", "average precision"]);
});

test("a one-strategy run has one column", () => {
  const run = regRun();
  delete run.strategies.random;
  const v = metricsView(run);
  assert.equal(v.cols.length, 1);
  assert.equal(v.rows[0].cells.length, 1);
});

test("the production choice says what was selected on and what it was fit to", () => {
  const rows = Object.fromEntries(productionView(regRun()));
  assert.match(rows["Selected on"], /^Scaffold validation/);
  assert.equal(rows["Configuration"], "max_depth 6 · learning_rate 0.05 · n_estimators 300");
  assert.match(rows["Mean validation loss"], /^0\.5512 across/);
  assert.equal(rows["Refit on"], "102 eligible rows");
  assert.equal(rows["Seed"], "3");
  assert.match(rows["Search"], /tiny profile/);
  assert.equal(rows["Dataset hash"], "0123456789ab");
});

test("a full-profile run does not mention the profile; a seed of zero is shown", () => {
  const run = regRun(); run.profile = "full"; run.seed = 0;
  const rows = Object.fromEntries(productionView(run));
  assert.equal("Search" in rows, false);
  assert.equal(rows["Seed"], "0");
});

test("the caveats say what the numbers are not", () => {
  const all = CAVEATS.join(" ");
  assert.match(all, /not independent replicates and not a confidence interval/);
  assert.match(all, /not a fresh held-out test of the final model/);
  assert.match(all, /not a guarantee of performance on novel chemistry/);
  assert.doesNotMatch(all, /PR-AUC/);
  assert.match(TEMPORARY_NOTE, /temporary until it is registered/);
  assert.match(TEMPORARY_NOTE, /makes it durable/);
});
