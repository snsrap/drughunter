/* Applying a registered model to the open dataset, as the page reasons about it: which
   experiments can be chosen and why the others cannot, how one job's life is followed
   (start, poll, end, expire, a different dataset taking over), what its errors say, and how
   a prediction is worded beside a measurement. Pure -- no DOM, no fetch, no clock -- so it
   is testable without a browser (`npm test`).

   The server's side is the prediction routes (docs/proteinviewer.md): POST
   /api/dataset/predict {dataset_id, experiment_id} starts a job and answers its progress
   (`state` queued, running, finishing, done, error or cancelled; `completed` of `total`
   rows; on an error `error` and maybe `error_code`); GET /api/dataset/predict?id= polls it;
   GET /api/dataset/predictions?dataset_id= answers the columns and the kept sets.
   Every job names the dataset and the experiment it was started for, and the page believes
   that, not what is open now: an answer for another dataset paints nothing. */

import {metricNumber} from "./training.js";
import {headlineCell, targetText} from "./registry.js";

export {isTerminal, formatElapsed, POLL_MS, POLL_RETRY_MS, MAX_POLL_FAILURES} from "./training.js";
import {isTerminal, MAX_POLL_FAILURES, POLL_MS, POLL_RETRY_MS} from "./training.js";

/* ---------------- the selector ---------------- */

export const ACTION_LABEL = "Predict entire dataset";
export const SCOPE_NOTE =
  "Predicts every row of this dataset, whatever the filter, sort or page shows now. " +
  "A prediction is the model's estimate, not a measurement, and it is not a test of the model: " +
  "this dataset may overlap what the model was trained on. The model's held-out evaluation is " +
  "in the registry.";

/* One <option> per experiment: what it is, how it was evaluated, and -- when it cannot be
   chosen -- why. Order is the server's (newest first). */
export function selectorOptions(experiments, allowed){
  return (experiments || []).map(e => {
    let reason = "";
    if (allowed === false) reason = "predictions are not available on this server";
    else if (!e.selectable) reason = "its files are missing, or it is not ready";
    else if (e.usable && !e.usable.ok) reason = e.usable.reason || "it cannot be applied here";
    const h = headlineCell(e.headline, e.target);
    const metric = h.status === "ok" ? h.label + " " + h.text : h.label ? h.label + " " + h.text : "no evaluation";
    return {
      id: e.id, disabled: !!reason, reason,
      text: e.name + " (" + e.id.slice(0, 8) + ") — " + [e.model_family, targetText(e), metric].filter(Boolean).join(" · ") +
        (reason ? " — unavailable: " + reason : ""),
      name: e.name, family: e.model_family || "", target: targetText(e), metric, headline: h,
    };
  });
}

/* The experiment to keep selected after a reload of the list: the one that was, if it is
   still choosable; else none (the choice is never made for the user). */
export function keepSelection(options, id){
  const o = options.find(x => x.id === id);
  return o && !o.disabled ? id : "";
}

/* What the controls offer. `ds` is the open dataset (ready), `choice` the experiment id. */
export function canStart(ds, choice, options, task){
  if (!ds) return {ok: false, reason: ""};
  if (task && (task.phase === "starting" || task.phase === "tracking")) return {ok: false, reason: "A prediction is running for this dataset."};
  if (!choice) return {ok: false, reason: options.length ? "Choose a model." : "No model is registered yet."};
  const o = options.find(x => x.id === choice);
  if (!o) return {ok: false, reason: "Choose a model."};
  if (o.disabled) return {ok: false, reason: "This model cannot be used: " + o.reason + "."};
  return {ok: true, reason: ""};
}

export const startBody = (ds, choice) => ({dataset_id: ds.dataset_id, experiment_id: choice});

/* ---------------- one job's life ---------------- */

const RANK = {queued: 0, running: 1, finishing: 2, done: 3, error: 3, cancelled: 3};
export {RANK as STATE_RANK};

/* phase: idle | starting | start_failed | tracking | terminal | expired | lost.
   `dataset` and `experiment` are what the task was begun for. */
export const idleTask = datasetId => ({dataset: datasetId || null, experiment: null, phase: "idle", job: null,
  error: null, failures: 0, cancelling: false});

export const beginStart = (task, experimentId) =>
  Object.assign(idleTask(task.dataset), {phase: "starting", experiment: experimentId});

export function startError(err){
  const m = String((err && err.message) || err || "unknown error"), s = err && err.status;
  if (err && err.stale) return "This server predates predictions — restart it.";
  if (s === 404) return "The dataset or the experiment is not there any more (" + m + "). Reload the list.";
  if (s === 409) return "This cannot start: " + m;
  if (s === 400) return "The server refused this request: " + m;
  if (s === 503) return "This server cannot predict: " + m;
  return "Could not start the prediction: " + m;
}

export function startFailed(task, err){
  return task.phase === "starting" ? Object.assign({}, task, {phase: "start_failed", error: startError(err)}) : task;
}

/* A progress dict, from the POST or from a poll. It is believed only if it is for this
   task's dataset and job, and does not move the job backwards. */
export function progressed(task, p){
  if (!p || !p.id || p.dataset_id !== task.dataset) return task;
  if (task.experiment && p.experiment_id && p.experiment_id !== task.experiment) return task;
  if (task.phase === "idle" || task.phase === "start_failed" || task.phase === "expired" || task.phase === "lost") return task;
  if (task.job && task.job.id !== p.id) return task;
  if (task.job && isTerminal(task.job.state)) return task;
  if (task.job && RANK[p.state] !== undefined && RANK[p.state] < RANK[task.job.state]) return task;
  if (RANK[p.state] === undefined)
    return Object.assign({}, task, {phase: "terminal", job: Object.assign({}, p, {state: "error", error: "the server reported an unknown state “" + p.state + "”"})});
  return Object.assign({}, task, {phase: isTerminal(p.state) ? "terminal" : "tracking", job: p, failures: 0, error: null,
    cancelling: isTerminal(p.state) ? false : task.cancelling});
}

export function pollFailed(task, err){
  if (task.phase !== "tracking") return task;
  const s = err && err.status, m = String((err && err.message) || err || "unknown error");
  if (s === 404 && !(err && err.stale)) return Object.assign({}, task, {phase: "expired", error: null});
  const failures = task.failures + 1;
  if ((err && err.stale) || failures >= MAX_POLL_FAILURES)
    return Object.assign({}, task, {phase: "lost", failures, error: "Lost contact with the server (" + m + ")."});
  return Object.assign({}, task, {failures});
}

export const resume = task => task.phase === "lost" && task.job ? Object.assign({}, task, {phase: "tracking", failures: 0, error: null}) : task;
export const cancelRequested = task => task.phase === "tracking" && !task.cancelling ? Object.assign({}, task, {cancelling: true}) : task;
export function cancelFailed(task, err){
  if (err && err.status === 404 && !err.stale) return Object.assign({}, task, {phase: "expired", cancelling: false, error: null});
  return Object.assign({}, task, {cancelling: false, error: "Could not cancel: " + String((err && err.message) || err)});
}

/* What the poller does next: null, or {what: "poll", delay}. */
export function nextAction(task){
  return task.phase === "tracking" ? {what: "poll", delay: task.failures ? POLL_RETRY_MS : POLL_MS} : null;
}

/* The words for a finished job's failure: the server's message, plus what to do. */
export function failureAdvice(job){
  const code = job && job.error_code;
  if (code === "incompatible") return "This model cannot be applied by this server. Nothing was predicted, and any earlier predictions were kept.";
  if (code === "experiment_unavailable") return "The experiment was deleted or damaged before the job could use it. Choose another model.";
  if (code === "dataset_or_model_changed") return "The dataset or the model is not the one the job started with, so its result was discarded.";
  return "Nothing was predicted, and any earlier predictions of this model were kept.";
}

/* The one state the controls show. */
export function panelState(task){
  const job = task.job, s = job && job.state;
  const base = {job, locked: false, canCancel: false};
  switch (task.phase){
    case "starting": return Object.assign(base, {kind: "starting", locked: true});
    case "start_failed": return Object.assign(base, {kind: "start_failed", message: task.error});
    case "expired": return Object.assign(base, {kind: "expired",
      message: "The server no longer knows this job (it was forgotten or the server restarted). Check the columns; if the predictions are missing, run it again."});
    case "lost": return Object.assign(base, {kind: "lost", message: task.error});
    case "tracking": return Object.assign(base, {kind: s, locked: true, progress: true,
      canCancel: (s === "queued" || s === "running") && !task.cancelling, cancelling: task.cancelling, message: task.error});
    case "terminal":
      if (s === "done") return Object.assign(base, {kind: "done"});
      if (s === "cancelled") return Object.assign(base, {kind: "cancelled", message: job.message || "cancelled"});
      return Object.assign(base, {kind: "error", message: job.error || "The job failed; the server gave no reason.", advice: failureAdvice(job)});
    default: return Object.assign(base, {kind: "idle"});
  }
}

export function progressText(job){
  if (!job) return "";
  const parts = [];
  if (job.state === "queued") parts.push("waiting for a free slot");
  else if (job.stage) parts.push(job.stage);
  if (job.total > 0 && job.state !== "queued") parts.push(job.completed + " of " + job.total + " rows");
  return parts.join(" · ");
}

export const fraction = job => job && job.total > 0 ? Math.max(0, Math.min(1, (job.completed || 0) / job.total)) : null;

/* A finished job's one-line outcome: how many rows have a number, and how many have none. */
export function doneText(job){
  const failed = job.n_failed || 0, ok = job.total - failed;
  return ok + " of " + job.total + " rows predicted" + (failed ? "; " + failed + " could not be (their reasons are on the rows)" : "") + ".";
}

/* ---------------- what is kept, and how it reads ---------------- */

/* The sets as a list: a name, whether its model still exists, how many rows have nothing. */
export function setRows(predictions){
  return (predictions || []).map(s => ({
    key: s.key, name: s.experiment.name, id: s.experiment.id, deleted: !!s.source_deleted,
    note: (s.source_deleted ? "source model deleted · " : "") + (s.n_failed ? s.n_failed + " of " + s.n_rows + " rows have no prediction" : "every row predicted"),
    outputs: s.outputs.map(o => o.label),
  }));
}

/* A column of kind "prediction", by its key; null for any other. */
export const predictionColumns = schema => (schema || []).filter(c => c.kind === "prediction");

/* One prediction as text: a number (four significant figures, with its units), a class, or a dash. */
export function cellText(col, value){
  if (value === null || value === undefined) return "—";
  if (col.type === "number"){
    const u = col.meta && col.meta.units ? " " + col.meta.units : "";
    return metricNumber(value) + u;
  }
  return String(value);
}

/* The primary output of each set (a regression's value or a classifier's class), for a card:
   [{label, text}] with the model named, never a bare number. */
export function cardPredictions(row, schema){
  const out = [];
  predictionColumns(schema).forEach(c => {
    if (c.meta && (c.meta.output === "value" || c.meta.output === "class")){
      const v = row.predictions ? row.predictions[c.key] : undefined;
      if (v !== undefined) out.push({key: c.key, label: c.meta.experiment_name, text: cellText(c, v), title: c.label});
    }
  });
  return out;
}

/* The detail panel's rows: every output of every set, and for a row a set could not
   predict, its reason in place of a number. */
export function detailPredictions(row, schema){
  const cols = predictionColumns(schema), rows = [];
  const bySet = new Map();
  cols.forEach(c => { if (!bySet.has(c.meta.set)) bySet.set(c.meta.set, []); bySet.get(c.meta.set).push(c); });
  bySet.forEach((list, set) => {
    const why = row.prediction_errors && row.prediction_errors[set];
    list.forEach(c => {
      const v = row.predictions ? row.predictions[c.key] : undefined;
      rows.push(why && v === undefined
        ? {label: c.label, text: "no prediction", why}
        : {label: c.label, text: v === undefined ? "—" : cellText(c, v), why: ""});
    });
  });
  return rows;
}

/* After a set is removed (or the columns change): a query that names a column that is
   gone would be refused, so those parts of it are dropped. */
export function pruneQuery(query, schema){
  const keys = new Set((schema || []).map(c => c.key));
  const sort = keys.has(query.sort.key) ? query.sort : {key: "row_id", dir: "asc"};
  const filters = query.filters.filter(f => keys.has(f.key));
  const changed = sort !== query.sort || filters.length !== query.filters.length;
  return changed ? Object.assign({}, query, {sort, filters, offset: 0}) : query;
}

/* A key for the controls' contents: they are rebuilt when it changes. */
export const schemaKey = ds => ds ? ds.dataset_id + "|" + (ds.schema || []).map(c => c.key).join(",") : "";
