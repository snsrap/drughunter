/* The dataset summary: the counts, the target's distribution, duplicates,
   scaffold concentration and missing cells of the dataset that is open.

   The panel needs the dataset, and only the dataset -- not the ligand panel
   beside it. It watches which dataset is ready (`follow`, registered ahead of the
   views), asks the server once for its summary, and draws what comes back:
   a loading line while the server computes (the first time on a big dataset is
   slow, and nothing here waits on it), the answer, or the error with a way to try
   again. The server sends bins and class counts, never rows, so the charts are
   a bar per bin or per class. What each number says, and which of the target's
   four displays to show, is model/summary.js's, and testable. */

import {commit} from "../core/state.js";
import {$, esc, setHTML} from "../core/dom.js";
import * as api from "../core/api.js";
import * as S from "../model/summary.js";
import {makeLatest} from "../model/dataset.js";

const latest = makeLatest();
let task = S.idleTask();     /* the one summary request: {id, status, data, error} */
let open = true;

/* ---------------- loading ---------------- */

/* Runs on every commit, ahead of the views: when the dataset that is ready is not
   the one the task is for, start asking for its summary. Nothing here commits --
   the views that follow in this same pass draw the loading state, and the answer
   commits when it arrives. */
export function follow(st){
  const id = st.dataset.ready ? st.dataset.ready.dataset_id : null;
  if (id === task.id) return;
  task = S.startTask(id);
  if (id) load(id);
}

async function load(id){
  const ticket = latest.issue();
  let data = null, error = null;
  try{ data = await api.datasetSummary(id); }
  catch(err){ error = S.errorText(err.message); }
  /* A newer request has taken over: this answer is for one nobody is waiting on.
     (And finishing a task that is for another dataset changes nothing.) */
  if (!latest.current(ticket)) return;
  task = error === null ? S.finishTask(task, id, data) : S.failTask(task, id, error);
  commit();
}

function retry(){
  if (!task.id) return;
  task = S.startTask(task.id);
  load(task.id);
  commit();
}

/* ---------------- view ---------------- */
export function render(st){
  const ds = st.dataset.ready;
  $("sum-panel").hidden = !ds;
  if (!ds) return;
  const view = S.panelState(task);
  const ready = view.kind === "ready";
  $("sum-panel").classList.toggle("closed", !open);
  $("sum-n").textContent = ready ? S.count(task.data.populations.source_rows.n) + " rows" : "";
  $("sum-body").setAttribute("aria-busy", view.kind === "loading");
  setHTML($("sum-msg"), view.kind === "loading"
    ? "Computing the summary… the first time for a large dataset takes a moment." : "");
  setHTML($("sum-body"), view.kind === "error" ? failure(view.message) : ready ? answer(task.data) : "");
}

const failure = message =>
  "<div class='sfail' role='alert'><span>" + esc(message) + "</span>" +
  "<button class='btn' type='button' id='sum-retry'>Try again</button></div>";

function answer(s){
  const ex = S.exclusionView(s), target = S.targetView(s.target);
  return "<div class='sgrid'>" +
    section("Rows", table(S.populationRows(s)) +
      (ex.shown ? "<h4>" + esc(ex.heading) + "</h4>" + table(ex.rows) + "<p class='snote'>" + esc(ex.note) + "</p>" : "")) +
    section(target.heading, targetBlock(target)) +
    section("Duplicates", table(S.duplicateRows(s)) +
      "<p class='snote'>Same structure under the dataset's identity: RDKit canonical isomeric SMILES, " +
      "nothing stripped or normalised.</p>") +
    section("Scaffolds", table(S.scaffoldRows(s)) + "<p class='snote'>" + esc(S.scaffoldPolicy(s)) + "</p>") +
    missingSection(S.missingView(s)) +
    "</div>";
}

const section = (title, body, cls) =>
  "<section class='ssec" + (cls ? " " + cls : "") + "'><h3 class='eyebrow'>" + esc(title) + "</h3>" + body + "</section>";

/* [{label, value, note}] as a table: what it is, the number, what the number is of. */
const table = rows => "<table class='stab'>" + rows.map(r =>
  "<tr><th scope='row'>" + esc(r.label) + "</th><td class='mono'>" + esc(r.value) + "</td><td>" + esc(r.note) + "</td></tr>"
).join("") + "</table>";

function targetBlock(v){
  if (v.kind === "regression")
    return "<p class='snote'>" + esc(v.caption) + "</p>" + histogram(v) +
      "<div class='slabel'>" + esc(v.axis) + "</div>" +
      "<dl class='sstats'>" + v.stats.map(x => "<div><dt>" + esc(x[0]) + "</dt><dd class='mono'>" + esc(x[1]) + "</dd></div>").join("") + "</dl>" +
      "<p class='snote'>" + esc(v.ruleNote) + "</p>";
  if (v.kind === "classification")
    return "<p class='snote'>" + esc(v.caption) + "</p>" + "<div class='sbars'>" + v.rows.map(r =>
      "<div class='srow'><span class='sname' title='" + esc(r.label) + "'>" + esc(r.label) + (r.positive ? " (positive)" : "") + "</span>" +
      "<span class='strack'><span class='sfill' style='width:" + (r.frac * 100).toFixed(2) + "%'></span></span>" +
      "<span class='mono'>" + esc(r.value) + "</span><span class='sshare'>" + esc(r.share) + "</span></div>").join("") + "</div>";
  return "<div class='sempty'><b>" + esc(v.message) + "</b><span>" + esc(v.detail) + "</span></div>";
}

/* One rect per bin: at most fifty, whatever the dataset's size. */
function histogram(v){
  const W = 400, H = 120, gap = v.bars.length > 1 ? 1 : 0;
  const rects = v.bars.map(b =>
    "<rect class='sbar' x='" + (b.x + gap / 2).toFixed(2) + "' y='" + b.y.toFixed(2) +
    "' width='" + Math.max(0, b.w - gap).toFixed(2) + "' height='" + b.h.toFixed(2) + "'><title>" + esc(b.label) + "</title></rect>").join("");
  const ticks = v.ticks.map(t =>
    "<text class='stick' x='" + (t.pos * W).toFixed(1) + "' y='" + (H + 15) + "' text-anchor='" +
    (t.pos === 0 ? "start" : t.pos === 1 ? "end" : "middle") + "'>" + esc(t.label) + "</text>").join("");
  return "<svg class='shist' viewBox='0 0 " + W + " " + (H + 20) + "' role='img' aria-label='" +
    esc("Histogram of " + v.axis + " in " + v.bars.length + " bins") + "'>" + rects +
    "<line class='saxis' x1='0' x2='" + W + "' y1='" + (H + .5) + "' y2='" + (H + .5) + "'/>" + ticks + "</svg>";
}

function missingSection(m){
  return section("Missing cells", "<p class='snote'>" + esc(m.heading) + ". " + esc(m.note) + "</p>" +
    "<div class='sbars smissing'>" + m.rows.map(r =>
      "<div class='srow" + (r.n ? "" : " none") + "'><span class='sname' title='" + esc(r.name) + "'>" + esc(r.name) + "</span>" +
      "<span class='strack'><span class='sfill' style='width:" + (r.frac * 100).toFixed(2) + "%'></span></span>" +
      "<span class='mono'>" + esc(r.value) + "</span><span class='sshare'>" + esc(r.share) + "</span></div>").join("") + "</div>",
    "wide");
}

export function bind(){
  $("sum-tog").onclick = () => {
    open = !open;
    $("sum-tog").setAttribute("aria-expanded", open);
    $("sum-tog").setAttribute("aria-label", open ? "Collapse the summary panel" : "Expand the summary panel");
    $("sum-tog").innerHTML = open ? "&#9662;" : "&#9652;";
    commit();
  };
  $("sum-body").onclick = ev => { if (ev.target.closest("#sum-retry")) retry(); };
}
