/* Train a model on the open dataset: the seed and the strategy that picks the
   hyperparameters, the run's progress, and -- once it is done -- its metrics for
   both split strategies side by side.

   Like the summary, the panel needs the dataset and only the dataset: it watches
   which one is ready (`follow`, registered ahead of the views), and neither the
   ligand panel nor the summary has to have loaded. Each request it makes carries
   a ticket (model/dataset.js's makeLatest), so an answer that comes back after the
   dataset changed, or after a newer request, paints nothing. What state a run is
   in, what it may do next and how its numbers are worded is model/training.js's,
   and testable. Everything the server says goes through esc(), and through attr()
   inside a quoted attribute. */

import {state, commit} from "../core/state.js";
import {$, esc, attr, setHTML} from "../core/dom.js";
import * as api from "../core/api.js";
import * as T from "../model/training.js";
import {makeLatest} from "../model/dataset.js";

const latest = makeLatest();
let task = T.idleTask(null);        /* the one run the panel follows */
let form = T.newForm();             /* the seed as typed, and the strategy */
/* The test hook: ?profile=tiny runs the CI search; every other page asks for the full one. */
const profile = T.profileFromSearch(location.search);
let timer = null;
let open = true;

/* An Error as the model wants it: its message, the HTTP status, whether the server is too old. */
const info = err => ({message: err && err.message, status: err && err.status, stale: err instanceof api.StaleServerError});

/* ---------------- following the dataset ---------------- */

/* Runs on every commit, ahead of the views. A different dataset is a different
   panel: the old run is not followed any more (and, being invisible, not left
   running on the server either). */
export function follow(st){
  const id = st.dataset.ready ? st.dataset.ready.dataset_id : null;
  if (id === task.dataset) return;
  stop(true);
  task = T.idleTask(id);
}

/* The panel is torn down: no poll in flight may paint, no timer may fire. */
function stop(cancelRun){
  if (timer) clearTimeout(timer);
  timer = null;
  latest.issue();
  if (cancelRun && task.phase === "tracking" && task.run) api.cancelTraining(task.run.id).catch(() => {});
}
export function teardown(){ stop(false); }

/* ---------------- actions ---------------- */

/* Start a run with the form as it stands. */
async function begin(){
  const g = T.gate(state.dataset);
  const req = g.ok ? T.buildRequest(task.dataset, form, profile) : {ok: false};
  if (!req.ok || !T.panelState(task).canTrain) return;
  stop(false);
  const ticket = latest.issue();
  task = T.beginStart(task, req.body);
  commit();
  let p;
  try{ p = await api.startTraining(req.body); }
  catch(err){
    if (!latest.current(ticket)) return;
    task = T.startFailed(task, info(err));
    return commit();
  }
  /* The panel was torn down while the server was answering: the run it started is
     one nobody is following. */
  if (!latest.current(ticket)){
    if (p && p.id) api.cancelTraining(p.id).catch(() => {});
    return;
  }
  task = T.progressed(task, p);
  settle();
}

/* After any change to the task: decide what happens next, then draw. */
function settle(){
  if (timer) clearTimeout(timer);
  timer = null;
  const next = T.nextAction(task);
  if (next && next.what === "poll") timer = setTimeout(poll, next.delay);
  else if (next && next.what === "result") loadResult();
  commit();
}

async function poll(){
  timer = null;
  if (task.phase !== "tracking") return;
  const ticket = latest.issue();
  try{
    const p = await api.trainingProgress(task.run.id);
    if (!latest.current(ticket)) return;
    task = T.progressed(task, p);
  }catch(err){
    if (!latest.current(ticket)) return;
    task = T.pollFailed(task, info(err));
  }
  settle();
}

async function loadResult(){
  const ticket = latest.issue();
  try{
    const data = await api.trainingRun(task.run.id);
    if (!latest.current(ticket)) return;
    task = T.resultLoaded(task, data);
  }catch(err){
    if (!latest.current(ticket)) return;
    task = T.resultFailed(task, info(err));
  }
  commit();
}

async function cancel(){
  if (!T.panelState(task).canCancel) return;
  const ds = task.dataset, id = task.run.id;
  task = T.cancelRequested(task);
  commit();
  let p = null, failure = null;
  try{ p = await api.cancelTraining(id); }catch(err){ failure = err; }
  /* Another dataset, or another run, has taken the panel over. */
  if (task.dataset !== ds || !task.run || task.run.id !== id) return;
  task = failure ? T.cancelFailed(task, info(failure)) : T.progressed(task, p);
  settle();
}

function copyRunId(btn){
  const id = task.run && task.run.id;
  if (!id) return;
  const done = () => { btn.textContent = "Copied"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(id).then(done, select);
  else select();
  /* No clipboard (an insecure page, a refusal): select the id, so Ctrl+C works. */
  function select(){
    const el = $("trn-run-id");
    if (!el) return;
    const r = document.createRange();
    r.selectNodeContents(el);
    const sel = getSelection();
    sel.removeAllRanges(); sel.addRange(r);
  }
}

/* ---------------- view ---------------- */

export function render(st){
  const g = T.gate(st.dataset), meta = st.dataset.ready;
  $("trn-panel").hidden = !g.show;
  if (!g.show) return;
  const view = T.panelState(task), seed = T.validateSeed(form.seedText);
  $("trn-panel").classList.toggle("closed", !open);
  setHTML($("trn-ds"), meta ? T.datasetRows(meta).map(r => "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>").join("") : "");

  const locked = view.locked || !g.ok;
  if ($("trn-seed").value !== form.seedText) $("trn-seed").value = form.seedText;
  $("trn-seed").disabled = locked;
  $("trn-seed").setAttribute("aria-invalid", !seed.ok);
  $("trn-seed-err").textContent = seed.ok ? "" : seed.error;
  document.querySelectorAll("input[name=trn-strategy]").forEach(r => { r.checked = r.value === form.strategy; r.disabled = locked; });
  $("trn-go").disabled = locked || !view.canTrain || !seed.ok;
  $("trn-go").textContent = view.kind === "idle" || view.kind === "starting" ? "Train" : "Train again";
  const cancelShown = view.progress && (view.kind === "queued" || view.kind === "running");
  $("trn-cancel").hidden = !cancelShown;
  $("trn-cancel").disabled = !!view.cancelling;
  $("trn-cancel").textContent = view.cancelling ? "Cancelling…" : "Cancel";
  $("trn-reason").className = "pdbq-msg" + (g.ok ? "" : " err");
  setHTML($("trn-reason"), g.ok ? "" : esc(g.reason));

  $("trn-status").setAttribute("aria-busy", view.kind === "starting" || view.progress || view.kind === "loading_result");
  setHTML($("trn-status"), status(view));
  setHTML($("trn-result"), view.kind === "done" ? result(task.result.data, task.run) : "");
}

const alertBox = (message, extra, buttons) =>
  "<div class='tfail' role='alert'><span>" + esc(message) + "</span>" + (extra || "") +
  "<span class='tbtns'>" + (buttons || "") + "</span></div>";
const button = (id, text, data) => "<button class='btn' type='button' id='" + id + "'" +
  (data ? " data-strategy='" + attr(data) + "'" : "") + ">" + esc(text) + "</button>";

function status(v){
  const run = v.run;
  switch (v.kind){
    case "idle": return "<p class='snote'>Nothing has been trained for this dataset yet.</p>";
    case "starting": return "<p class='tline'><span class='tstate'>Starting</span> sending the request…</p>";
    case "queued": case "running": case "finishing": return progress(v);
    case "loading_result": return "<p class='tline'><span class='tstate'>Done</span> reading the run's results…</p>";
    case "cancelled": return "<p class='tline'><span class='tstate'>Cancelled</span> nothing was kept from this run.</p>" +
      "<span class='tbtns'>" + button("trn-retry", "Train again") + "</span>";
    case "start_failed": return alertBox(v.message, "", button("trn-retry", "Try again"));
    case "error": return alertBox(v.message, "", button("trn-retry", "Train again"));
    case "expired": return alertBox(v.message, "", button("trn-retry", "Train again"));
    case "lost": return alertBox(v.message + " The run may still be going.", "", button("trn-resume", "Check again") + button("trn-retry", "Train again"));
    case "result_failed": return alertBox(v.message, "", button("trn-result-retry", "Try again"));
    case "unavailable": return unavailable(v);
    case "done": return "<p class='tline'><span class='tstate ok'>Done</span>" +
      (run.elapsed !== undefined && T.formatElapsed(run.elapsed) ? " in " + esc(T.formatElapsed(run.elapsed)) : "") + "</p>";
    default: return "";
  }
}

function progress(v){
  const run = v.run, f = T.fraction(run), pct = f === null ? "" : Math.round(f * 100) + "%";
  const label = T.STATE_LABEL[run.state] || run.state;
  return "<p class='tline'><span class='tstate'>" + esc(label) + "</span> <span class='tstage'>" + esc(T.stageText(run)) + "</span></p>" +
    "<div class='tbar2'><progress class='tprog' max='1'" + (f === null ? "" : " value='" + f.toFixed(4) + "'") +
    " aria-label='Training progress'" + (pct ? " aria-valuetext='" + attr(pct) + "'" : "") + "></progress>" +
    "<span class='mono tpct'>" + esc(pct) + "</span>" +
    "<span class='mono telapsed' title='elapsed'>" + esc(T.formatElapsed(run.elapsed)) + "</span></div>" +
    (v.message ? "<p class='pdbq-msg err'>" + esc(v.message) + "</p>" : "");
}

/* The production strategy had no valid split: the run stopped before it trained.
   A new run with the other one is offered, never taken. */
function unavailable(v){
  const lines = v.view.lines.map(l => "<li>" + esc(l) + "</li>").join("");
  return "<div class='tfail' role='alert'><b>" + esc(T.strategyLabel(v.strategy)) + " selection is not possible for this dataset.</b>" +
    "<span>" + esc(v.view.message) + "</span>" +
    (lines ? "<ul class='tlines'>" + lines + "</ul>" : "") +
    "<span class='snote'>Training stopped before it began. Nothing was switched for you; a run with the other " +
    "strategy is a new run.</span>" +
    "<span class='tbtns'>" + button("trn-other", "Train again with " + T.strategyLabel(v.other) + " selection", v.other) + "</span></div>";
}

/* ---------------- a completed run ---------------- */

function result(run, progressDict){
  const m = T.metricsView(run);
  return "<div class='trun'><span class='eyebrow'>Run id</span> <code class='mono trid' id='trn-run-id'>" + esc(progressDict.id) + "</code>" +
    "<button class='btn' type='button' id='trn-copy' aria-label='Copy the run id'>Copy</button></div>" +
    "<p class='snote'>" + esc(T.TEMPORARY_NOTE) + "</p>" +
    "<div class='tgrid2'>" +
      "<section class='ssec'><h3 class='eyebrow'>Production model</h3>" + kv(T.productionView(run)) + "</section>" +
      "<section class='ssec wide'><h3 class='eyebrow'>Metrics on the test sets</h3>" + metricsTable(m) + "</section>" +
    "</div>" +
    m.repeats.map(repeatTable).join("") +
    "<ul class='tcaveats'>" + T.CAVEATS.map(c => "<li>" + esc(c) + "</li>").join("") + "</ul>";
}

const kv = rows => "<dl class='kv tkv'>" + rows.map(r => "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>").join("") + "</dl>";

function metricsTable(m){
  const head = m.cols.map(c => "<th colspan='3' scope='colgroup' class='tcol" + (c.production ? " prod" : "") + "'>" + esc(c.label) +
    (c.production ? " <span class='tprod'>production</span>" : "") + "</th>").join("");
  const sub = m.cols.map(() => "<th scope='col'>Mean</th><th scope='col'>SD</th><th scope='col'>Defined</th>").join("");
  const body = m.rows.map(r => "<tr><th scope='row'>" + esc(r.label) + "</th>" + r.cells.map((c, i) => {
    if (!c) return "<td colspan='3' class='tna'>no valid split</td>";
    return "<td class='mono" + (c.undefinedAll ? " tund" : "") + (m.cols[i].production ? " prod" : "") + "' title='" + attr(c.title) + "'>" + esc(c.mean) + "</td>" +
      "<td class='mono" + (c.sd === "n/a" ? " tund" : "") + "' title='" + attr(c.sdTitle || "") + "'>" + esc(c.sd) + "</td>" +
      "<td class='tdef'>" + esc(c.defined) + (c.whyText ? "<span class='twhy'>" + esc(c.whyText) + "</span>" : "") + "</td>";
  }).join("") + "</tr>").join("");
  const fails = m.cols.filter(c => !c.ok).map(c =>
    "<div class='tfail inline'><b>" + esc(c.label) + " has no valid split.</b> <span>" + esc(c.unavailable.message) + "</span>" +
    (c.unavailable.lines.length ? "<ul class='tlines'>" + c.unavailable.lines.map(l => "<li>" + esc(l) + "</li>").join("") + "</ul>" : "") + "</div>").join("");
  return (m.rows.length
    ? "<div class='tscroll'><table class='ttab'><thead><tr><th rowspan='2' scope='col'>Metric</th>" + head + "</tr><tr>" + sub + "</tr></thead><tbody>" + body + "</tbody></table></div>"
    : "") +
    (m.unitsNote ? "<p class='snote'>" + esc(m.unitsNote) + "</p>" : "") + fails;
}

function repeatTable(r){
  if (!r.rows.length) return "";
  return "<details class='trep'><summary>Each repeat — " + esc(r.label) + "</summary><div class='tscroll'><table class='ttab'><thead><tr>" +
    "<th scope='col'>Repeat</th><th scope='col'>Chosen configuration</th>" + r.metrics.map(x => "<th scope='col'>" + esc(x.label) + "</th>").join("") +
    "</tr></thead><tbody>" + r.rows.map(row => "<tr><th scope='row'>" + esc(String(row.repeat)) + "</th><td>" + esc(row.config) + "</td>" +
      row.values.map(v => "<td class='mono" + (v.why ? " tund" : "") + "' title='" + attr(v.why) + "'>" + esc(v.text) + "</td>").join("") + "</tr>").join("") +
    "</tbody></table></div></details>";
}

/* ---------------- controls ---------------- */

export function bind(){
  $("trn-strategy").insertAdjacentHTML("beforeend", T.STRATEGIES.map(s =>
    "<label class='chk'><input type='radio' name='trn-strategy' value='" + attr(s.value) + "'" + (s.value === form.strategy ? " checked" : "") + ">" +
    esc(s.label) + (s.value === T.DEFAULT_STRATEGY ? " <span class='aside'>(default)</span>" : "") +
    " <span class='aside'>" + esc(s.note) + "</span></label>").join(""));
  $("trn-help").textContent = T.STRATEGY_HELP;
  $("trn-seed").oninput = ev => { form = Object.assign({}, form, {seedText: ev.target.value}); commit(); };
  $("trn-strategy").onchange = ev => {
    if (ev.target.name === "trn-strategy"){ form = Object.assign({}, form, {strategy: ev.target.value}); commit(); }
  };
  $("trn-form").onsubmit = ev => { ev.preventDefault(); begin(); };
  $("trn-cancel").onclick = cancel;
  const act = ev => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.id === "trn-retry") begin();
    else if (b.id === "trn-other"){ form = Object.assign({}, form, {strategy: b.dataset.strategy}); begin(); }
    else if (b.id === "trn-resume"){ task = T.resume(task); settle(); }
    else if (b.id === "trn-result-retry"){ task = T.resultRetry(task); settle(); }
    else if (b.id === "trn-copy") copyRunId(b);
  };
  $("trn-status").onclick = act;
  $("trn-result").onclick = act;
  $("trn-tog").onclick = () => {
    open = !open;
    $("trn-tog").setAttribute("aria-expanded", open);
    $("trn-tog").setAttribute("aria-label", open ? "Collapse the training panel" : "Expand the training panel");
    $("trn-tog").innerHTML = open ? "&#9662;" : "&#9652;";
    commit();
  };
  window.addEventListener("pagehide", teardown);
}
