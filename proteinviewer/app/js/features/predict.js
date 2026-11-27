/* Apply a registered model to the open dataset: the selector in the ligand panel, the job's
   progress, and the list of predictions kept with the dataset.

   The action is always the whole dataset: nothing here reads the panel's filter, sort or
   page. The job names the dataset and experiment it was started for, so an answer for another
   dataset (the user opened one meanwhile) is ignored, and when a job ends the panel asks the
   server for that dataset's columns, never for whatever is open now. The results come back as
   columns of the server's own query schema, so sorting and filtering by a prediction is the
   ordinary sort and filter. What a state means and how a prediction is worded is
   model/prediction.js's, and testable. Everything the server says goes through esc(), and
   through attr() inside a quoted attribute. */

import {state, commit} from "../core/state.js";
import {$, esc, attr, setHTML} from "../core/dom.js";
import * as api from "../core/api.js";
import * as P from "../model/prediction.js";
import {makeLatest} from "../model/dataset.js";
import * as molecules from "./molecules.js";

const latest = makeLatest();          /* one ticket for the job's requests, so a stale answer paints nothing */
const listLatest = makeLatest();      /* and one for the list of models */
const setsLatest = makeLatest();      /* and one for reading the dataset's columns back */
let task = P.idleTask(null);
let models = {status: "idle", items: [], error: ""};   /* idle | loading | ready | error | unavailable */
let choice = "";
let timer = null;
let notice = null;                    /* {text, err}: what happened to the last removal or read */

const info = err => ({message: err && err.message, status: err && err.status, stale: err instanceof api.StaleServerError});
const dsId = () => state.dataset.ready ? state.dataset.ready.dataset_id : null;

/* ---------------- following the dataset ---------------- */

/* Runs on every commit, ahead of the views. Another dataset is another panel: the job of the
   old one is not followed (it carries on, on the server, and its result is kept with the
   dataset it was for). */
export function follow(st){
  const id = st.dataset.ready ? st.dataset.ready.dataset_id : null;
  if (id === task.dataset) return;
  stop();
  task = P.idleTask(id);
  notice = null;
  /* A new dataset is a fresh look at the registry (a model may have been registered since). Not
     inside this commit: loadModels commits too. */
  if (id && models.status !== "loading") Promise.resolve().then(loadModels);
}

function stop(){
  if (timer) clearTimeout(timer);
  timer = null;
  latest.issue();
  setsLatest.issue();
}
export function teardown(){ stop(); }

/* ---------------- the models ---------------- */

async function loadModels(){
  const ticket = listLatest.issue();
  models = Object.assign({}, models, {status: "loading", error: ""});
  commit();
  try{
    const j = await api.experiments(false);
    if (!listLatest.current(ticket)) return;
    models = {status: "ready", items: j.experiments || [], error: ""};
  }catch(err){
    if (!listLatest.current(ticket)) return;
    const e = info(err);
    models = {status: e.status === 503 ? "unavailable" : "error", items: [],
              error: e.stale ? "This server predates the experiment registry — restart it." : e.message || "unknown error"};
  }
  choice = P.keepSelection(options(), choice);
  commit();
}

const options = () => P.selectorOptions(models.items, true);

/* ---------------- the job ---------------- */

async function begin(){
  const ds = state.dataset.ready, opts = options();
  if (!ds || !P.canStart(ds, choice, opts, task).ok) return;
  stop();
  const ticket = latest.issue(), id = ds.dataset_id;
  task = P.beginStart(task, choice);
  notice = null;
  commit();
  let p;
  try{ p = await api.startPrediction(P.startBody(ds, choice)); }
  catch(err){
    if (!latest.current(ticket) || task.dataset !== id) return;
    task = P.startFailed(task, info(err));
    return commit();
  }
  /* The server started a job for the dataset that was open; if another has taken the panel, the
     job runs on and its result is kept with its own dataset. */
  if (!latest.current(ticket) || task.dataset !== id) return;
  task = P.progressed(task, p);
  settle();
}

function settle(){
  if (timer) clearTimeout(timer);
  timer = null;
  const next = P.nextAction(task);
  if (next) timer = setTimeout(poll, next.delay);
  else if (task.phase === "terminal" && task.job && task.job.state === "done") readColumns(task.dataset);
  commit();
}

async function poll(){
  timer = null;
  if (task.phase !== "tracking") return;
  const ticket = latest.issue(), ds = task.dataset;
  try{
    const p = await api.predictionProgress(task.job.id);
    if (!latest.current(ticket) || task.dataset !== ds) return;
    task = P.progressed(task, p);
  }catch(err){
    if (!latest.current(ticket) || task.dataset !== ds) return;
    task = P.pollFailed(task, info(err));
  }
  settle();
}

async function cancel(){
  if (!P.panelState(task).canCancel) return;
  const ds = task.dataset, id = task.job.id;
  task = P.cancelRequested(task);
  commit();
  let p = null, failure = null;
  try{ p = await api.cancelPrediction(id); }catch(err){ failure = err; }
  if (task.dataset !== ds || !task.job || task.job.id !== id) return;
  task = failure ? P.cancelFailed(task, info(failure)) : P.progressed(task, p);
  settle();
}

/* The dataset's columns and kept sets, read back for `datasetId` -- the dataset the job was
   for, which is the one open or the answer is dropped -- and the page of rows asked for again. */
async function readColumns(datasetId){
  const ticket = setsLatest.issue();
  let j;
  try{ j = await api.datasetPredictions(datasetId); }
  catch(err){
    if (!setsLatest.current(ticket) || dsId() !== datasetId) return;
    notice = {text: "The predictions were made but could not be read back: " + (err.message || "unknown error") + ". Reload the page.", err: true};
    return commit();
  }
  if (!setsLatest.current(ticket) || dsId() !== datasetId) return;
  adopt(j);
}

function adopt(j){
  const ds = state.dataset.ready;
  ds.schema = j.schema;
  ds.predictions = j.predictions;
  state.mol.query = P.pruneQuery(state.mol.query, ds.schema);
  commit();
  molecules.refresh();
}

async function removeSet(key){
  const ds = state.dataset.ready;
  if (!ds) return;
  const id = ds.dataset_id;
  try{
    await api.deletePredictions(id, key);
    notice = {text: "Removed. The model and the dataset are unchanged.", err: false};
  }catch(err){
    notice = {text: "Could not remove it: " + (err.message || "unknown error"), err: true};
  }
  if (dsId() !== id) return;
  await readColumns(id);
  commit();
}

/* ---------------- view ---------------- */

export function render(st){
  const ds = st.dataset.ready, box = $("pr-panel");
  box.hidden = !ds || !st.mol.query;
  if (box.hidden) return;
  const opts = options(), view = P.panelState(task), gate = P.canStart(ds, choice, opts, task);

  const sel = $("pr-model"), optHtml = "<option value=''>" + (models.status === "loading" ? "Reading the registry…" : opts.length ? "Choose a model…" : "No model registered") + "</option>" +
    opts.map(o => "<option value='" + attr(o.id) + "'" + (o.disabled ? " disabled" : "") + ">" + esc(o.text) + "</option>").join("");
  if (setHTML(sel, optHtml)) sel.value = choice;
  if (sel.value !== choice) sel.value = choice;
  sel.disabled = view.locked || models.status === "loading";

  const chosen = opts.find(o => o.id === choice);
  const why = models.status === "unavailable" ? "The experiment registry is not available: " + models.error
    : models.status === "error" ? "Could not read the registry: " + models.error
    : !gate.ok && choice ? gate.reason : "";
  $("pr-reason").className = "pdbq-msg" + (why ? " err" : "");
  $("pr-reason").textContent = why || (chosen ? chosen.family + " · " + chosen.target + " · " + chosen.metric : "");

  $("pr-go").textContent = P.ACTION_LABEL;
  $("pr-go").disabled = !gate.ok;
  $("pr-cancel").hidden = !(view.progress && (view.kind === "queued" || view.kind === "running"));
  $("pr-cancel").disabled = !!view.cancelling;
  $("pr-cancel").textContent = view.cancelling ? "Cancelling…" : "Cancel";
  $("pr-note").textContent = P.SCOPE_NOTE;
  $("pr-status").setAttribute("aria-busy", view.kind === "starting" || !!view.progress);
  setHTML($("pr-status"), status(view));

  const sets = P.setRows(ds.predictions);
  setHTML($("pr-sets"), sets.length
    ? "<h3 class='eyebrow'>Predictions kept with this dataset</h3><ul class='psets'>" + sets.map(s =>
        "<li><span class='pname'>" + esc(s.name) + " <span class='aside'>(" + esc(s.id.slice(0, 8)) + ")</span></span>" +
        "<span class='pnote" + (s.deleted ? " gone" : "") + "'>" + esc(s.note) + "</span>" +
        "<button class='btn' type='button' data-remove='" + attr(s.key) + "' aria-label='Remove the predictions of " + attr(s.name) + "'>Remove</button></li>").join("") + "</ul>"
    : "");
  setHTML($("pr-notice"), notice ? esc(notice.text) : "");
  $("pr-notice").className = "pdbq-msg" + (notice && notice.err ? " err" : "");
}

function status(v){
  const j = v.job, button = (id, t) => "<button class='btn' type='button' id='" + id + "'>" + esc(t) + "</button>";
  switch (v.kind){
    case "starting": return "<p class='tline'><span class='tstate'>Starting</span> sending the request…</p>";
    case "queued": case "running": case "finishing": {
      const f = P.fraction(j), pct = f === null ? "" : Math.round(f * 100) + "%";
      return "<p class='tline'><span class='tstate'>" + esc(v.kind) + "</span> <span class='tstage'>" + esc(P.progressText(j)) + "</span></p>" +
        "<div class='tbar2'><progress class='tprog' max='1'" + (f === null ? "" : " value='" + f.toFixed(4) + "'") + " aria-label='Prediction progress'></progress>" +
        "<span class='mono tpct'>" + esc(pct) + "</span><span class='mono telapsed'>" + esc(P.formatElapsed(j.elapsed)) + "</span></div>" +
        (v.message ? "<p class='pdbq-msg err'>" + esc(v.message) + "</p>" : "");
    }
    case "done": return "<p class='tline'><span class='tstate ok'>Done</span> " + esc(P.doneText(j)) + "</p>";
    case "cancelled": return "<p class='tline'><span class='tstate'>Cancelled</span> nothing was written; earlier predictions were kept.</p>";
    case "start_failed": return alert(v.message);
    case "error": return alert(v.message, v.advice);
    case "expired": return alert(v.message);
    case "lost": return alert(v.message + " The job may still be running.", "", button("pr-resume", "Check again"));
    default: return "";
  }
}
const alert = (message, extra, buttons) => "<div class='tfail' role='alert'><span>" + esc(message) + "</span>" +
  (extra ? "<span class='snote'>" + esc(extra) + "</span>" : "") + (buttons ? "<span class='tbtns'>" + buttons + "</span>" : "") + "</div>";

/* ---------------- controls ---------------- */

export function bind(){
  $("pr-model").onchange = ev => { choice = ev.target.value; commit(); };
  $("pr-form").onsubmit = ev => { ev.preventDefault(); begin(); };
  $("pr-cancel").onclick = cancel;
  $("pr-refresh").onclick = loadModels;
  $("pr-status").onclick = ev => {
    if (ev.target.closest("#pr-resume")){ task = P.resume(task); settle(); }
  };
  $("pr-sets").onclick = ev => {
    const b = ev.target.closest("[data-remove]");
    if (b) removeSet(b.dataset.remove);
  };
  window.addEventListener("pagehide", teardown);
}
