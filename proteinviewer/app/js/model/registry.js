/* The experiment registry as the page reasons about it: naming and registering a
   completed run, the table of experiments (columns, deterministic sorting, and the
   rule that scores are only ever ordered among comparable ones), what a lifecycle
   state means, and the wording of rename and delete. Pure -- no DOM, no fetch, no
   clock -- so it is testable without a browser (`npm test`).

   The server's side is the experiment routes (docs/proteinviewer.md): a list entry
   carries the facts of the run, `selectable`, and a server-derived `headline`
   metric ({metric, strategy, value, n_defined, n_total, status, group}). The page
   never computes a score and never ranks two unlike ones: `group` says which
   numbers are of the same kind. */

import {metricNumber, metricLabel, strategyLabel} from "./training.js";

/* ---------------- registering ---------------- */

export const NAME_MAX = 200;

/* A name as the server accepts it: 1-200 characters, one line. */
export function validateName(text){
  const t = String(text === null || text === undefined ? "" : text).trim();
  if (t === "") return {ok: false, error: "Give the experiment a name."};
  if (t.length > NAME_MAX) return {ok: false, error: "The name is at most " + NAME_MAX + " characters."};
  if (/[\u0000-\u001f\u007f]/.test(t)) return {ok: false, error: "The name must be on one line."};
  return {ok: true, value: t};
}

/* A default to offer: the target and the date of the run, never the run id. */
export function suggestName(run){
  const t = run && run.target && run.target.column ? run.target.column : "model";
  const day = run && typeof run.created_utc === "string" ? run.created_utc.slice(0, 10) : "";
  return ((run && run.model_family) || "xgboost") + " " + t + (day ? " " + day : "");
}

export const idleRegistration = runId => ({run: runId || null, phase: "idle", record: null, error: null, repeat: false});

export function registering(reg){ return Object.assign({}, reg, {phase: "registering", error: null}); }

/* The server answers an idempotent repeat with the same record and a 200, whatever
   name came with it: when the name it kept is not the one just typed, say so. */
export function registered(reg, record, typedName){
  if (reg.phase !== "registering") return reg;
  return Object.assign({}, reg, {phase: "registered", record, error: null,
    repeat: !!(record && typedName && record.name !== typedName)});
}

export function registrationError(reg, err){
  if (reg.phase !== "registering") return reg;
  const status = err && err.status, m = String((err && err.message) || err || "unknown error");
  let kind = "failed", message = "Could not register this run: " + m;
  if (err && err.stale){ kind = "stale"; message = "This server predates the experiment registry — restart it."; }
  else if (status === 404){
    kind = "expired";
    message = "This run is no longer available (finished runs are kept only for a while, and not across a restart). " +
      "Train again, then register the new run.";
  }
  else if (status === 409) { kind = "refused"; message = "This run cannot be registered: " + m; }
  else if (status === 503){ kind = "unavailable"; message = "The experiment registry is not available: " + m; }
  else if (status === 400){ kind = "refused"; message = "The server refused this registration: " + m; }
  return Object.assign({}, reg, {phase: "error", error: {kind, message}});
}

/* What the Register control offers. A failed copy leaves the run registrable. */
export function registrationView(reg){
  switch (reg.phase){
    case "registering": return {kind: "registering", canSubmit: false, text: "Registering… copying the run to the registry"};
    case "registered": return {kind: "registered", canSubmit: false,
      text: reg.repeat
        ? "Already registered as “" + reg.record.name + "”. Registering again does not rename it; use Rename in the registry."
        : "Registered as “" + reg.record.name + "”.",
      record: reg.record};
    case "error": return {kind: "error", canSubmit: reg.error.kind !== "expired" && reg.error.kind !== "unavailable" && reg.error.kind !== "stale",
      text: reg.error.message, retry: reg.error.kind === "failed"};
    default: return {kind: "idle", canSubmit: true, text: ""};
  }
}

/* ---------------- lifecycle ---------------- */

export const STATE_TEXT = {
  ready: "Ready",
  pending: "Registration unfinished",
  failed: "Registration failed",
  deleting: "Deletion unfinished",
};

/* Only a ready, present (selectable) record is healthy. `reason` says why not. */
export function health(exp){
  if (exp.state === "ready" && exp.selectable) return {ok: true, label: "Ready", reason: ""};
  if (exp.state === "ready")
    return {ok: false, label: "Files missing or changed",
      reason: "The record is ready but its model files are missing or no longer the size they were registered at. It cannot be used."};
  const label = STATE_TEXT[exp.state] || String(exp.state);
  const detail = exp.state_detail ? " " + exp.state_detail : "";
  if (exp.state === "deleting") return {ok: false, label, reason: "A deletion was authorised and has not finished; it can be retried." + detail};
  return {ok: false, label, reason: (exp.state === "pending"
    ? "A registration started and did not finish; recovery finishes it only when its files verify." : "A registration stopped part way.") + detail};
}

/* ---------------- the table ---------------- */

/* The headline as a cell: the number and what it is a number of, never a bare score. */
export function headlineCell(h, target){
  if (!h) return {text: "—", label: "", title: "no headline metric for this task", status: "none"};
  const label = metricLabel(h.metric, target) + " (" + (h.strategy ? strategyLabel(h.strategy).toLowerCase() + " test" : "no test") + ")";
  if (h.status === "unavailable")
    return {text: "unavailable", label, title: "neither strategy had a valid split, so there is no test score", status: "unavailable"};
  const defined = h.n_defined + " of " + h.n_total + " repeats defined";
  if (h.status === "undefined")
    return {text: "undefined", label, title: "no repeat defined this metric (" + defined + ")", status: "undefined"};
  return {text: metricNumber(h.value), label, title: defined + (h.strategy === "random" ? "; scaffold was not available, so this is the random test" : ""), status: "ok",
          fallback: h.strategy === "random"};
}

/* [what a column is called, how to read the row's value for sorting]. `id` breaks every tie. */
export const COLUMNS = [
  {key: "name", label: "Name", get: e => String(e.name || "").toLowerCase()},
  {key: "model_family", label: "Model", get: e => e.model_family || ""},
  {key: "initiator", label: "Initiator", get: e => e.initiator || ""},
  {key: "created_utc", label: "Created", get: e => e.created_utc || ""},
  {key: "dataset_id", label: "Dataset", get: e => e.dataset_id || ""},
  {key: "target", label: "Target / task / units", get: e => targetText(e).toLowerCase()},
  {key: "production_strategy", label: "Production strategy", get: e => e.production_strategy || ""},
  {key: "headline", label: "Headline metric", get: e => e.headline && e.headline.status === "ok" ? e.headline.value : null, metric: true},
];

export function targetText(e){
  const t = e.target || {};
  const units = t.units || (t.transform === "pki" ? "pKi" : "");
  return [e.target_column || t.column || "—", e.task || t.task || "", units].filter(Boolean).join(" · ");
}

/* The headline groups among these rows (null for a row with no comparable group). */
export function groupsOf(rows){
  const seen = [];
  rows.forEach(e => { const g = e.headline ? e.headline.group : null; if (!seen.includes(g)) seen.push(g); });
  return seen;
}

/* Metric sorting is offered only when every row shown is the same kind of score:
   one task, target, unit, strategy and metric. Otherwise it is disabled, with the
   reason; the page never orders an RMSE in pKi against one in nM, or a regression
   against a classification. */
export function metricSortable(rows){
  if (rows.length < 2) return {ok: false, reason: "Nothing to compare: fewer than two experiments are shown."};
  const groups = groupsOf(rows);
  if (groups.length === 1 && groups[0]) return {ok: true, reason: ""};
  return {ok: false, reason: "The experiments shown measure different things (task, target, units, strategy or metric), so their scores are not ranked against each other. Narrow the list to one kind to sort by metric."};
}

const cmp = (a, b) => a < b ? -1 : a > b ? 1 : 0;

/* A sorted copy. Ties -- and missing values, always last -- fall back to the id, so
   the order is the same on every load. Sorting by the metric when it is not allowed
   is refused (the table falls back to its default order). RMSE-like errors sort low
   to high and balanced accuracy high to low when `dir` is "best": a plain "asc"/"desc"
   is what was asked. */
export function sortRows(rows, key, dir, sortable){
  const col = COLUMNS.find(c => c.key === key) || COLUMNS[3];
  const useKey = col.metric && !(sortable && sortable.ok) ? "created_utc" : col.key;
  const c = COLUMNS.find(x => x.key === useKey);
  const sign = dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const va = c.get(a), vb = c.get(b);
    const ma = va === null || va === undefined, mb = vb === null || vb === undefined;
    if (ma !== mb) return ma ? 1 : -1;
    const r = ma ? 0 : sign * cmp(va, vb);
    return r || cmp(a.id, b.id);
  });
}

/* ---------------- rename ---------------- */

/* The body of an edit: only what changed, and never a scientific fact. */
export function updateBody(exp, draft){
  const body = {id: exp.id};
  const name = validateName(draft.name);
  if (!name.ok) return {ok: false, error: name.error};
  if (name.value !== exp.name) body.name = name.value;
  const notes = normalizeNotes(draft.notes);
  if (!notes.ok) return {ok: false, error: notes.error};
  if (JSON.stringify(notes.value) !== JSON.stringify(exp.annotations || {})) body.annotations = notes.value;
  if (Object.keys(body).length === 1) return {ok: false, error: "Nothing was changed.", unchanged: true};
  return {ok: true, body};
}

/* [{key, value}] rows -> the server's object; up to 32 notes, keys 1-64, values <= 2000. */
export function normalizeNotes(rows){
  const out = {};
  const list = rows || [];
  for (const r of list){
    const k = String(r.key || "").trim();
    if (k === "" && String(r.value || "") === "") continue;
    if (k === "") return {ok: false, error: "Every note needs a name."};
    if (k.length > 64) return {ok: false, error: "A note's name is at most 64 characters."};
    if (String(r.value || "").length > 2000) return {ok: false, error: "A note is at most 2000 characters."};
    if (Object.prototype.hasOwnProperty.call(out, k)) return {ok: false, error: "Two notes are named “" + k + "”."};
    out[k] = String(r.value || "");
  }
  if (Object.keys(out).length > 32) return {ok: false, error: "At most 32 notes."};
  return {ok: true, value: out};
}

/* ---------------- delete ---------------- */

/* The confirmation names the experiment and what goes with it. */
export function deleteConfirmation(exp){
  return {
    title: "Delete “" + exp.name + "”?",
    body: "This permanently removes the experiment's metadata, its model and the dataset snapshot it " +
      "preserved. It cannot be undone. The original dataset you uploaded is not affected, and " +
      "predictions already stored with a dataset are kept, labelled as coming from a deleted model.",
    confirm: "Delete permanently",
  };
}

/* What a delete answered. Only `complete` is success: a 409 (an active use) or 500
   (a file or the row could not be removed) leaves a `deleting` record that stays in
   the list and can be retried. */
export function deleteOutcome(res, err){
  if (err){
    const report = err.report || null;
    const parts = report ? reportParts(report) : [];
    const why = err.status === 409 ? "The experiment is still in use; the deletion is waiting for that to end." :
      "The deletion did not finish: " + (err.message || "unknown error");
    return {done: false, retryable: err.status !== 404, message: why, parts,
      note: "The experiment is marked as being deleted and cannot be used. Delete it again to finish."};
  }
  if (res && res.complete) return {done: true, message: "Deleted.", parts: []};
  return {done: false, retryable: true, message: "The deletion is not complete.", parts: reportParts(res || {}),
    note: "The experiment is marked as being deleted and cannot be used. Delete it again to finish."};
}

export function reportParts(report){
  const out = [];
  [["artifact_dir", "Model and artifacts"], ["staging_dir", "Staging copy"], ["row", "Metadata record"]].forEach(([k, label]) => {
    if (report[k] !== undefined) out.push({label, status: String(report[k]).replace(/_/g, " "), bad: report[k] === "failed" || report[k] === "retained"});
  });
  return out;
}

/* ---------------- the detail ---------------- */

export const REPEAT_NOTE =
  "Repeat metrics evaluate the tuning procedure on held-out splits. They are not an independent test of the final model, which was refit on all eligible rows.";
export const FIXED_NOTE = "The model, target, splits and metrics are facts of the run and cannot be edited. To change one, train and register a new run.";

/* [label, value] rows of an experiment's provenance and where its files are. */
export function provenanceRows(rec){
  const v = rec.versions || {}, rows = [
    ["Experiment id", rec.id],
    ["Initiator (server user)", rec.initiator || "—"],
    ["Registered (UTC)", rec.created_utc || "—"],
    ["Source run", rec.source_run_id || "—"],
    ["Dataset", (rec.dataset_id || "—") + (rec.dataset_hash ? " · " + String(rec.dataset_hash).slice(0, 12) : "")],
    ["Artifacts", rec.artifact_path ? "<registry>/" + rec.artifact_path : "—"],
  ];
  ["rdkit", "xgboost", "python", "featuriser", "scaffold_policy", "split_algorithm"].forEach(k => { if (v[k]) rows.push([k, String(v[k])]); });
  return rows;
}

/* [label, value] rows of the feature and target configuration, as recorded. */
export function schemaRows(rec){
  const fs = rec.feature_schema || {}, t = rec.target || {}, rows = [];
  rows.push(["Target column", rec.target_column || t.column || "—"]);
  rows.push(["Task", rec.task || t.task || "—"]);
  if (t.units || t.transform) rows.push(["Units / transform", [t.units, t.transform].filter(Boolean).join(" · ")]);
  if (Array.isArray(t.classes)) rows.push(["Classes", t.classes.join(", ") + (t.positive_class !== null && t.positive_class !== undefined ? " (positive: " + t.positive_class + ")" : "")]);
  Object.keys(fs).forEach(k => rows.push(["feature " + k, typeof fs[k] === "object" ? JSON.stringify(fs[k]) : String(fs[k])]));
  return rows;
}
