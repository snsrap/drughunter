/* The training panel, as the page reasons about it: whether a dataset can be
   trained, how the seed is checked, which state a run is in and what to do next
   (poll, fetch the result, stop), and how a completed run's numbers are worded
   and laid side by side. Pure -- no DOM, no fetch, no clock -- so it is testable
   without a browser (`npm test`).

   The server's side is the QSAR run contract: POST /api/training starts a job and
   answers its progress; GET /api/training?id= polls it (`state` is queued, running,
   finishing, done, error or cancelled, with `stage`, `completed`, `total`,
   `elapsed`, and on an error `error`, and `error_code` and `unavailable` when the
   production strategy had no valid split); GET /api/training/run?id= answers the
   completed run's record. Everything here is wording and decisions; no number is
   computed from rows -- the page is never given any. */

import {number} from "./summary.js";
import {exclusionLine} from "./dataset.js";

/* ---------------- the form ---------------- */

/* The split's own limit (ligandbench: a repeat seed has to fit a JSON number). */
export const MAX_SEED = 4294967295;
export const DEFAULT_SEED_TEXT = "0";

/* What "production selection strategy" means, in one honest sentence. The
   choice is made before anything is evaluated: it is not picked from test scores. */
export const STRATEGIES = [
  {value: "scaffold", label: "Scaffold", note: "groups of molecules that share a ring scaffold stay together, so the model is chosen on molecules unlike those it learned from"},
  {value: "random", label: "Random", note: "molecules are assigned at random, so validation looks like the training data"},
];
export const DEFAULT_STRATEGY = "scaffold";
export const STRATEGY_HELP =
  "Both strategies are evaluated. The production strategy decides which one's validation " +
  "scores pick the hyperparameters of the model that is refit on all eligible rows; it is " +
  "fixed before training and never chosen from test scores.";
export const PROFILES = ["full", "tiny"];

export const otherStrategy = s => s === "scaffold" ? "random" : "scaffold";
export const strategyLabel = s => (STRATEGIES.find(x => x.value === s) || {label: String(s)}).label;

/* A whole number from 0 to MAX_SEED, as typed. Digits only: "1e3", "1.5", "-1",
   "+1", "0x10" and "" are not whole numbers a person meant. */
export function validateSeed(text){
  const t = String(text === null || text === undefined ? "" : text).trim();
  if (t === "") return {ok: false, error: "Enter a whole number from 0 to " + MAX_SEED + "."};
  if (!/^[0-9]+$/.test(t)) return {ok: false, error: "The seed is a whole number from 0 to " + MAX_SEED + ", digits only."};
  const n = Number(t);
  if (!Number.isFinite(n) || n > MAX_SEED) return {ok: false, error: "The seed is at most " + MAX_SEED + "."};
  return {ok: true, value: n};
}

export const newForm = () => ({seedText: DEFAULT_SEED_TEXT, strategy: DEFAULT_STRATEGY});

/* The POST body for the form, or the reason it cannot be sent. `profile` is
   "full" unless the page was opened with ?profile=tiny. */
export function buildRequest(datasetId, form, profile){
  const seed = validateSeed(form.seedText);
  if (!seed.ok) return {ok: false, error: seed.error};
  if (!STRATEGIES.some(s => s.value === form.strategy)) return {ok: false, error: "Choose a production selection strategy."};
  if (!datasetId) return {ok: false, error: "No dataset is open."};
  return {ok: true, body: {dataset_id: datasetId, seed: seed.value, production_strategy: form.strategy,
                           profile: PROFILES.includes(profile) ? profile : "full"}};
}

/* The test hook: ?profile=tiny asks for the CI search. Anything else is the full one. */
export const profileFromSearch = search => /(?:^|[?&])profile=tiny(?:&|$)/.test(search || "") ? "tiny" : "full";

/* ---------------- what is open, and whether it can train ---------------- */

/* `ds` is the page's dataset state: {ready, staged}. Training needs a resolved
   dataset with a target; the reason is shown when it cannot. */
export function gate(ds){
  const ready = ds && ds.ready, staged = ds && ds.staged;
  if (!ready && staged) return {ok: false, show: true, reason: "This upload is waiting for its columns to be chosen. Finish the set-up dialog first."};
  if (!ready) return {ok: false, show: false, reason: ""};
  if (!ready.target || ready.trainable === false)
    return {ok: false, show: true, reason: "This dataset has no target, so there is nothing to train on. Open one with a measured target to train."};
  if (!(ready.n_training_eligible > 0))
    return {ok: false, show: true, reason: "No row of this dataset is eligible to train: every row is missing a valid, exact target or a parsable structure."};
  return {ok: true, show: true, reason: ""};
}

/* [label, value] pairs: what training would be given. Excluded rows are counted
   as rows (a row can have several reasons, so the reasons do not add up to it). */
export function datasetRows(meta){
  if (!meta) return [];
  const t = meta.target, n = meta.n_rows, eligible = meta.n_training_eligible;
  if (!t) return [["Target", "none — prediction only"], ["Rows", String(n)]];
  let units;
  if (t.kind === "classification") units = "—";
  else if (t.transform === "pki") units = "pKi = 9 − log10(Ki in nM)";
  else units = t.output_units ? t.output_units + ", values as they are" : "no units stated, values as they are";
  const rows = [
    ["Target", t.column],
    ["Task", t.kind + (t.kind === "classification" && t.positive_class !== null && t.positive_class !== undefined
      ? " (positive class: " + t.positive_class + ")" : "")],
    ["Units / transform", units],
    ["Eligible to train", eligible + " of " + n],
    ["Excluded", (n - eligible) + " of " + n],
  ];
  const why = exclusionLine(meta.exclusions);
  if (why && n - eligible > 0) rows.push(["Why", why]);
  return rows;
}

/* ---------------- one run's life ---------------- */

const RANK = {queued: 0, running: 1, finishing: 2, done: 3, error: 3, cancelled: 3};
export const isTerminal = s => RANK[s] === 3;
export const MAX_POLL_FAILURES = 4;
export const POLL_MS = 1000;
export const POLL_RETRY_MS = 2000;

/* The task for the open dataset. phase:
     idle          nothing started
     starting      the POST is out
     start_failed  the POST was refused or failed: `error`
     tracking      a run the server knows (queued, running or finishing): `run`
     terminal      the run ended (done, error, cancelled): `run`; `result` for done
     expired       the server no longer knows the run (404)
     lost          polling failed repeatedly: `error`
   `result` is the completed run's record, fetched once the run is done:
     {status: "none"|"loading"|"ready"|"failed"|"expired", data, error}. */
export const idleTask = datasetId => ({
  dataset: datasetId || null, phase: "idle", request: null, run: null, error: null,
  failures: 0, cancelling: false, result: {status: "none", data: null, error: null},
});

/* An error as the features hand it over: {message, status, stale}. */
const plainError = e => ({message: String((e && e.message) || e || "unknown error"),
                          status: (e && e.status) || null, stale: !!(e && e.stale)});

export function beginStart(task, request){
  return Object.assign(idleTask(task.dataset), {phase: "starting", request});
}

/* What the server's refusal says, and what to do about it. */
export function startError(err){
  const e = plainError(err);
  if (e.stale) return "This server predates training — restart it.";
  const m = e.message;
  switch (e.status){
    case 404: return "The server does not know this dataset any more (" + m + "). Open the file again.";
    case 409: return "The server cannot start this now: " + m;
    case 400: return "The server refused this request: " + m;
    case 503: return "This server cannot train: " + m;
    default: return "Could not start training: " + m;
  }
}

export function startFailed(task, err){
  if (task.phase !== "starting") return task;
  return Object.assign({}, task, {phase: "start_failed", error: startError(err)});
}

/* The server's progress dict, from the POST or from a poll. An answer is applied
   only if it is for this task's run and does not move it backwards: a slow
   response that overtakes a later one is for a moment already passed. */
export function progressed(task, p){
  if (!p || !p.id) return task;
  if (task.run && task.run.id !== p.id) return task;
  if (task.phase === "idle" || task.phase === "start_failed" || task.phase === "expired" || task.phase === "lost") return task;
  if (task.run && isTerminal(task.run.state)) return task;
  if (task.run && RANK[p.state] !== undefined && RANK[p.state] < RANK[task.run.state]) return task;
  if (RANK[p.state] === undefined)
    return Object.assign({}, task, {phase: "terminal", run: Object.assign({}, p, {state: "error", error: "the server reported an unknown state “" + p.state + "”"}), failures: 0});
  const terminal = isTerminal(p.state);
  return Object.assign({}, task, {
    phase: terminal ? "terminal" : "tracking", run: p, failures: 0, error: null,
    cancelling: terminal ? false : task.cancelling,
    result: p.state === "done" ? {status: "loading", data: null, error: null} : task.result,
  });
}

/* A poll failed. A 404 is the run's expiry (finished runs are evicted, and a
   restarted server has none); anything else is tried again a few times before
   the panel says it has lost the run. */
export function pollFailed(task, err){
  if (task.phase !== "tracking") return task;
  const e = plainError(err);
  if (e.status === 404 && !e.stale) return Object.assign({}, task, {phase: "expired", error: null});
  const failures = task.failures + 1;
  if (e.stale || failures >= MAX_POLL_FAILURES)
    return Object.assign({}, task, {phase: "lost", failures, error: "Lost contact with the server (" + e.message + ")."});
  return Object.assign({}, task, {failures});
}

export const cancelRequested = task =>
  task.phase === "tracking" && !task.cancelling ? Object.assign({}, task, {cancelling: true}) : task;
/* A cancel that failed leaves the run as it was, with the reason, and can be retried. */
export function cancelFailed(task, err){
  const e = plainError(err);
  if (e.status === 404 && !e.stale) return Object.assign({}, task, {phase: "expired", cancelling: false, error: null});
  return Object.assign({}, task, {cancelling: false, error: "Could not cancel: " + e.message});
}

export function resultLoaded(task, data){
  if (task.phase !== "terminal" || !task.run || task.run.state !== "done") return task;
  return Object.assign({}, task, {result: {status: "ready", data, error: null}});
}
export function resultFailed(task, err){
  if (task.phase !== "terminal" || !task.run || task.run.state !== "done") return task;
  const e = plainError(err);
  if (e.status === 404 && !e.stale) return Object.assign({}, task, {phase: "expired", result: {status: "expired", data: null, error: null}});
  return Object.assign({}, task, {result: {status: "failed", data: null, error: "Could not read the run's results: " + e.message}});
}
export const resultRetry = task =>
  task.result.status === "failed" ? Object.assign({}, task, {result: {status: "loading", data: null, error: null}}) : task;

/* What the poller does next: null (stop), or {delay, what}. */
export function nextAction(task){
  if (task.phase === "tracking") return {what: "poll", delay: task.failures ? POLL_RETRY_MS : POLL_MS};
  if (task.phase === "terminal" && task.result.status === "loading") return {what: "result", delay: 0};
  return null;
}

/* ---------------- what the panel shows ---------------- */

export const UNAVAILABLE_CODE = "production_strategy_unavailable";

/* The one state the panel is in, and what it may do. */
export function panelState(task){
  const run = task.run, s = run && run.state;
  const base = {run, locked: false, canCancel: false, canTrain: true, progress: false};
  switch (task.phase){
    case "idle": return Object.assign(base, {kind: "idle"});
    case "starting": return Object.assign(base, {kind: "starting", locked: true, canTrain: false});
    case "start_failed": return Object.assign(base, {kind: "start_failed", message: task.error});
    case "expired": return Object.assign(base, {kind: "expired",
      message: "This run is no longer available; train again. A finished run is kept only for a while, and not across a server restart."});
    case "lost": return Object.assign(base, {kind: "lost", message: task.error});
    case "tracking": return Object.assign(base, {kind: s, locked: true, canTrain: false, progress: true,
      canCancel: (s === "queued" || s === "running") && !task.cancelling, cancelling: task.cancelling, message: task.error});
    case "terminal":
      if (s === "cancelled") return Object.assign(base, {kind: "cancelled"});
      if (s === "error"){
        if (run.error_code === UNAVAILABLE_CODE){
          const u = unavailableOf(run, task.request);
          return Object.assign(base, {kind: "unavailable", strategy: u.strategy, view: unavailableView(u.object),
            other: otherStrategy(u.strategy)});
        }
        return Object.assign(base, {kind: "error", message: run.error || "The run failed; the server gave no reason."});
      }
      return Object.assign(base, {kind: task.result.status === "ready" ? "done" : task.result.status === "failed" ? "result_failed" : "loading_result",
        locked: task.result.status === "loading", message: task.result.error});
    default: return Object.assign(base, {kind: "idle"});
  }
}

/* `unavailable` as the progress dict gives it: the allocator's object, or that object
   with its strategy beside it (the CLI's own answer). Both are read. */
function unavailableOf(run, request){
  const u = run.unavailable || {};
  const inner = u.unavailable && typeof u.unavailable === "object" ? u.unavailable : u;
  const strategy = u.strategy || run.strategy || (request && request.production_strategy) || DEFAULT_STRATEGY;
  return {strategy, object: inner};
}

/* The progress bar's fraction, or null while it cannot be known. */
export function fraction(run){
  if (!run || !(run.total > 0)) return null;
  return Math.max(0, Math.min(1, (run.completed || 0) / run.total));
}

export function stageText(run){
  if (!run) return "";
  const bits = [];
  if (run.state === "queued") bits.push("waiting for a free slot");
  else if (run.state === "finishing") bits.push(run.stage || "writing the run");
  else if (run.stage) bits.push(run.stage);
  if (run.total > 0) bits.push((run.completed || 0) + " of " + run.total + " fits");
  return bits.join(" · ");
}

/* 83 -> "1 min 23 s", 7 -> "7 s", 3725 -> "1 h 2 min". */
export function formatElapsed(seconds){
  if (seconds === null || seconds === undefined || !Number.isFinite(+seconds) || +seconds < 0) return "";
  const t = Math.floor(+seconds);
  if (t < 60) return t + " s";
  if (t < 3600) return Math.floor(t / 60) + " min " + String(t % 60).padStart(2, "0") + " s";
  return Math.floor(t / 3600) + " h " + Math.floor(t % 3600 / 60) + " min";
}

export const STATE_LABEL = {queued: "Queued", running: "Running", finishing: "Finishing", done: "Done", error: "Failed", cancelled: "Cancelled"};

/* ---------------- a completed run ---------------- */

/* What a reader needs before the numbers: said once, below them. */
export const CAVEATS = [
  "Mean ± SD describes variability across overlapping repeats, not independent replicates and not a confidence interval. SD is the sample SD (n − 1); it is not defined from fewer than two values.",
  "Evaluation metrics describe the tuning procedure (each repeat's train, validation and test split), not a fresh held-out test of the final model, which is refit on all eligible rows.",
  "The random/scaffold gap describes these partitions of this dataset, not a guarantee of performance on novel chemistry.",
];
export const TEMPORARY_NOTE =
  "A completed run is temporary until it is registered. Registration (a later feature) makes it durable; until then it can be lost when the server keeps too many runs or restarts.";

const METRICS = {
  regression: [["rmse", "RMSE"], ["mae", "MAE"], ["r2", "R²"], ["spearman", "Spearman ρ"]],
  classification: [["roc_auc", "ROC-AUC"], ["average_precision", "average precision"],
                   ["balanced_accuracy", "balanced accuracy"], ["log_loss", "log loss"]],
};
/* Never the ambiguous name: an average precision is labelled as one. */
const ALIASES = {pr_auc: "average_precision", prauc: "average_precision", auprc: "average_precision", ap: "average_precision",
                 auc: "roc_auc", rocauc: "roc_auc", roc_auc_ovr: "roc_auc", roc_auc_macro_ovr: "roc_auc",
                 average_precision_macro_ovr: "average_precision"};

export function metricLabel(key, target){
  const canon = ALIASES[key] || key;
  const known = [].concat(METRICS.regression, METRICS.classification).find(m => m[0] === canon);
  let label = known ? known[1] : String(key).replace(/_/g, " ");
  const multiclass = target && target.task === "classification" && Array.isArray(target.classes) && target.classes.length > 2;
  if (multiclass && (canon === "roc_auc" || canon === "average_precision")) label += " (macro one-vs-rest)";
  return label;
}

const canonKey = key => ALIASES[key] || key;
export function metricOrder(run, strategies){
  const task = run.target && run.target.task === "classification" ? "classification" : "regression";
  const present = [];
  strategies.forEach(s => {
    const S = run.strategies && run.strategies[s];
    if (S && S.aggregate) Object.keys(S.aggregate).forEach(k => { if (!present.includes(k)) present.push(k); });
  });
  const known = [];
  METRICS[task].forEach(m => present.forEach(k => { if (canonKey(k) === m[0] && !known.includes(k)) known.push(k); }));
  return known.concat(present.filter(k => !known.includes(k)));
}

/* One number of a metric, four significant figures. Null is "undefined". */
export const metricNumber = v => v === null || v === undefined || !Number.isFinite(+v) ? "undefined" : number(v);

/* One strategy's aggregate of one metric, worded: mean, sample SD (null from
   fewer than two values, and said so), and how many repeats it covers. */
export function aggregateCell(agg, reasons){
  if (!agg) return {mean: "—", sd: "—", defined: "—", undefinedAll: false, partial: false, why: "", title: "no aggregate in this run"};
  const nd = agg.n_defined, nt = agg.n_total;
  const none = !nd || agg.mean === null || agg.mean === undefined;
  const why = reasons && reasons.length ? reasons.join("; ") : "";
  return {
    mean: none ? "undefined" : metricNumber(agg.mean),
    sd: none ? "—" : agg.sd === null || agg.sd === undefined ? "n/a" : metricNumber(agg.sd),
    sdTitle: none ? "" : agg.sd === null || agg.sd === undefined ? "the sample SD needs at least two defined values" : "",
    defined: (nd || 0) + " of " + nt,
    undefinedAll: none,
    partial: !none && nd < nt,
    why,
    title: why,
  };
}

/* The distinct reasons a metric was undefined in a strategy's repeats. */
function reasonsFor(S, key){
  const out = [];
  ((S && S.repeats) || []).forEach(r => {
    const why = r.undefined && r.undefined[key];
    if (why && !out.includes(why)) out.push(why);
  });
  return out;
}

/* The compact reason a strategy had no valid split, from 04's `unavailable`. */
export function unavailableView(u){
  u = u || {};
  const lines = [];
  if (u.repeat !== undefined && u.repeat !== null)
    lines.push("No valid split for repeat " + (u.repeat + 1) + " of 5" +
      (u.candidates_tried ? " after " + u.candidates_tried + " candidates." : "."));
  if (u.n_rows !== undefined && u.n_groups !== undefined){
    const largest = (u.largest_groups && u.largest_groups[0] && u.largest_groups[0].n_rows) ||
                    (u.group_size_counts && u.group_size_counts[0] && u.group_size_counts[0][0]);
    lines.push(u.n_rows + " eligible rows in " + u.n_groups + " groups" +
      (largest ? "; the largest group has " + largest + " rows." : "."));
  }
  const cc = u.class_counts && Object.keys(u.class_counts).length ? u.class_counts : null;
  if (cc) lines.push("Class counts: " + Object.keys(cc).map(k => k + " " + cc[k]).join(", ") + ".");
  (u.failed_constraints || []).forEach(c => {
    const tried = u.candidates_tried;
    lines.push((c.detail || c.constraint + (c.subset ? " (" + c.subset + ")" : "")) +
      (c.candidates_violating !== undefined && tried ? " — broken by " + c.candidates_violating + " of " + tried + " candidates" : ""));
  });
  return {message: u.message || "No valid split was found for this strategy.", lines};
}

export function configText(cfg){
  if (!cfg || typeof cfg !== "object") return "";
  return Object.keys(cfg).map(k => k + " " + cfg[k]).join(" · ");
}

/* The side-by-side table and what goes with it, from the run's record. */
export function metricsView(run){
  const names = ["random", "scaffold"];
  const prod = run.production && run.production.strategy;
  const strategies = names.filter(n => run.strategies && run.strategies[n]);
  const cols = strategies.map(n => {
    const S = run.strategies[n];
    return {strategy: n, label: strategyLabel(n), production: n === prod, ok: S.status === "ok",
            status: S.status, unavailable: S.status === "ok" ? null : unavailableView(S.unavailable)};
  });
  const okNames = cols.filter(c => c.ok).map(c => c.strategy);
  const keys = metricOrder(run, okNames);
  const rows = keys.map(k => ({
    key: k, label: metricLabel(k, run.target),
    cells: cols.map(c => c.ok
      ? Object.assign({strategy: c.strategy}, aggregateCell(run.strategies[c.strategy].aggregate && run.strategies[c.strategy].aggregate[k],
                                                           reasonsFor(run.strategies[c.strategy], k)))
      : null),
  }));
  const t = run.target || {};
  const units = t.units || (t.transform === "pki" ? "pKi" : "");
  return {
    cols, rows, okNames,
    unitsNote: t.task === "regression" && units ? "RMSE and MAE are in " + units + "." : "",
    repeats: okNames.map(n => repeatsView(run, n, keys)),
  };
}

/* Each repeat's chosen configuration and test values: what the aggregate is made of. */
export function repeatsView(run, strategy, keys){
  const S = run.strategies[strategy];
  return {
    strategy, label: strategyLabel(strategy),
    metrics: keys.map(k => ({key: k, label: metricLabel(k, run.target)})),
    rows: (S.repeats || []).map(r => ({
      repeat: (r.repeat === undefined ? 0 : r.repeat) + 1, seed: r.seed, config: configText(r.config),
      values: keys.map(k => {
        const v = r.test ? r.test[k] : null;
        return {text: metricNumber(v), why: v === null || v === undefined ? (r.undefined && r.undefined[k]) || "" : ""};
      }),
    })),
  };
}

/* What was chosen and what it was fit to. */
export function productionView(run){
  const p = run.production || {}, d = run.dataset || {};
  const rows = [
    ["Selected on", strategyLabel(p.strategy) + " validation (frozen before training)"],
    ["Configuration", configText(p.config) || "—"],
  ];
  if (p.mean_validation_loss !== undefined && p.mean_validation_loss !== null)
    rows.push(["Mean validation loss", metricNumber(p.mean_validation_loss) + " across the five repeats"]);
  if (p.n_refit_rows !== undefined) rows.push(["Refit on", p.n_refit_rows + " eligible rows"]);
  if (run.seed !== undefined) rows.push(["Seed", String(run.seed)]);
  if (run.profile && run.profile !== "full") rows.push(["Search", run.profile + " profile (a reduced search; not the full grid)"]);
  if (d.content_hash) rows.push(["Dataset hash", String(d.content_hash).slice(0, 12)]);
  return rows;
}
