/* The experiment registry: register a completed run from the training panel, list
   the registered experiments, open one, rename it and edit its notes, and delete it
   for good.

   Registry records outlive the dataset, so the panel does not follow it: it loads
   once, and again after every change it makes. Each request carries a ticket
   (model/dataset.js's makeLatest), so an answer that arrives after a newer one paints
   nothing. What a state means, what may be sorted and how a deletion is worded is
   model/registry.js's, and testable. Everything the server says goes through esc(),
   and through attr() inside a quoted attribute. Typing never re-renders: the
   inputs keep their own values and the page reads them when it needs them. */

import {state, commit} from "../core/state.js";
import {$, esc, attr, setHTML} from "../core/dom.js";
import * as api from "../core/api.js";
import * as R from "../model/registry.js";
import * as T from "../model/training.js";
import {makeLatest} from "../model/dataset.js";
import * as training from "./training.js";

const listTicket = makeLatest(), detailTicket = makeLatest();

let list = {status: "idle", items: [], error: ""};   /* idle | loading | ready | error | unavailable */
let sort = {key: "created_utc", dir: "desc"};
let open = true;
let detail = null;      /* {id, status: loading|ready|error, record, error, draft, msg, saving} */
let del = null;         /* {exp, phase: confirming|deleting, outcome} */
const registrations = new Map();   /* run id -> R.idleRegistration */
const names = new Map();           /* run id -> the name as typed */

const info = err => ({message: err && err.message, status: err && err.status, stale: err instanceof api.StaleServerError, report: err && err.report});

/* ---------------- loading ---------------- */

export async function load(){
  const ticket = listTicket.issue();
  list = Object.assign({}, list, {status: list.items.length ? "ready" : "loading", error: ""});
  commit();
  try{
    const j = await api.experiments(true);
    if (!listTicket.current(ticket)) return;
    list = {status: "ready", items: j.experiments || [], error: ""};
  }catch(err){
    if (!listTicket.current(ticket)) return;
    const e = info(err);
    list = {status: e.status === 503 ? "unavailable" : "error", items: [], error: e.stale ? "This server predates the experiment registry — restart it." : e.message || "unknown error"};
  }
  commit();
}

/* ---------------- registering ---------------- */

async function register(runId){
  const typed = R.validateName(names.get(runId));
  let reg = registrations.get(runId) || R.idleRegistration(runId);
  if (!typed.ok || (reg.phase === "registering" || reg.phase === "registered")) return;
  reg = R.registering(reg);
  registrations.set(runId, reg);
  commit();
  try{
    const rec = await api.registerExperiment(runId, typed.value);
    registrations.set(runId, R.registered(registrations.get(runId), rec, typed.value));
    load();
  }catch(err){
    registrations.set(runId, R.registrationError(registrations.get(runId), info(err)));
  }
  commit();
}

/* The Register control inside the completed-run panel: built once per run, then only its
   parts are updated, so the name being typed is never replaced. */
function renderRegister(){
  const box = $("trn-register");
  if (!box) return;
  const run = training.completedRun();
  if (!run){ box.innerHTML = ""; delete box.dataset.run; return; }
  if (box.dataset.run !== run.id){
    box.dataset.run = run.id;
    if (!names.has(run.id)) names.set(run.id, R.suggestName(run.record));
    box.innerHTML = "<form class='regform' novalidate>" +
      "<label class='fld'><span class='eyebrow'>Register this model</span>" +
      "<input type='text' class='reg-name' maxlength='200' autocomplete='off' spellcheck='false' aria-describedby='reg-msg-" + attr(run.id) + "'></label>" +
      "<button class='btn solid reg-go' type='submit'>Register</button></form>" +
      "<p class='snote'>Registering copies the run to the registry so it survives a restart. The initiator and time are set by the server.</p>" +
      "<p class='pdbq-msg' id='reg-msg-" + attr(run.id) + "' aria-live='polite'></p>";
    const input = box.querySelector(".reg-name");
    input.value = names.get(run.id);
    input.oninput = () => { names.set(run.id, input.value); sync(box, run.id); };
    box.querySelector("form").onsubmit = ev => { ev.preventDefault(); register(run.id); };
  }
  sync(box, run.id);
}

function sync(box, runId){
  const reg = registrations.get(runId) || R.idleRegistration(runId), v = R.registrationView(reg);
  const name = R.validateName(names.get(runId));
  const input = box.querySelector(".reg-name"), go = box.querySelector(".reg-go"), msg = box.querySelector(".pdbq-msg");
  if (!input) return;
  const locked = reg.phase === "registering" || reg.phase === "registered";
  input.disabled = locked;
  input.setAttribute("aria-invalid", !name.ok && (names.get(runId) || "") !== "");
  go.disabled = locked || !v.canSubmit || !name.ok;
  go.textContent = reg.phase === "registering" ? "Registering…" : reg.phase === "registered" ? "Registered" : reg.phase === "error" && v.retry ? "Try again" : "Register";
  msg.className = "pdbq-msg" + (v.kind === "error" ? " err" : "");
  msg.textContent = v.text || (!name.ok && (names.get(runId) || "") !== "" ? name.error : "");
}

/* ---------------- opening one ---------------- */

async function openDetail(id){
  const ticket = detailTicket.issue();
  detail = {id, status: "loading", record: null, error: "", draft: null, msg: null, saving: false};
  commit();
  try{
    const rec = await api.experiment(id);
    if (!detailTicket.current(ticket)) return;
    detail = Object.assign({}, detail, {status: "ready", record: rec, draft: draftOf(rec)});
  }catch(err){
    if (!detailTicket.current(ticket)) return;
    detail = Object.assign({}, detail, {status: "error", error: info(err).message || "unknown error"});
  }
  commit();
}
const draftOf = rec => ({name: rec.name, notes: Object.keys(rec.annotations || {}).map(k => ({key: k, value: rec.annotations[k]}))});
function closeDetail(){ detailTicket.issue(); detail = null; commit(); }

/* Read what is typed into the detail's inputs into its draft. */
function readDraft(){
  if (!detail || !detail.draft) return;
  const n = $("reg-d-name");
  if (n) detail.draft.name = n.value;
  const rows = [...document.querySelectorAll("#reg-detail .note-row")];
  if (rows.length || $("reg-d-notes")) detail.draft.notes = rows.map(r => ({key: r.querySelector(".note-k").value, value: r.querySelector(".note-v").value}));
}

async function save(){
  if (!detail || detail.status !== "ready" || detail.saving) return;
  readDraft();
  const rec = detail.record, b = R.updateBody(rec, detail.draft);
  if (!b.ok){ detail = Object.assign({}, detail, {msg: {err: !b.unchanged, text: b.error}}); return commit(); }
  const id = rec.id;
  detail = Object.assign({}, detail, {saving: true, msg: null});
  commit();
  try{
    const updated = await api.updateExperiment(b.body);
    if (detail && detail.id === id) detail = Object.assign({}, detail, {saving: false, record: Object.assign({}, detail.record, updated),
      draft: draftOf(updated), msg: {err: false, text: "Saved. Only the name and notes can change; everything else is a fact of the run."}});
    load();
  }catch(err){
    if (detail && detail.id === id) detail = Object.assign({}, detail, {saving: false, msg: {err: true, text: "Could not save: " + (info(err).message || "unknown error")}});
  }
  commit();
}

/* ---------------- deleting ---------------- */

function askDelete(id){
  const exp = list.items.find(e => e.id === id) || (detail && detail.record && detail.record.id === id ? detail.record : null);
  if (!exp) return;
  del = {exp, phase: "confirming", outcome: null};
  commit();
}

async function confirmDelete(){
  if (!del || del.phase === "deleting") return;
  const exp = del.exp;
  del = Object.assign({}, del, {phase: "deleting", outcome: null});
  commit();
  let out;
  try{ out = R.deleteOutcome(await api.deleteExperiment(exp.id)); }
  catch(err){ out = R.deleteOutcome(null, info(err)); }
  if (out.done){
    del = null;
    if (detail && detail.id === exp.id){ detailTicket.issue(); detail = null; }
  }else del = {exp, phase: "confirming", outcome: out};
  load();
  commit();
}

/* ---------------- view ---------------- */

export function render(){
  renderRegister();
  const panel = $("reg-panel");
  panel.classList.toggle("closed", !open);
  const rows = list.items, sortable = R.metricSortable(rows);
  $("reg-n").textContent = list.status === "ready" ? rows.length + (rows.length === 1 ? " experiment" : " experiments") : "";
  const msg = $("reg-msg");
  msg.className = "pdbq-msg" + (list.status === "error" || list.status === "unavailable" ? " err" : "");
  msg.textContent = list.status === "loading" ? "Reading the registry…" :
    list.status === "unavailable" ? "The experiment registry is not available: " + list.error :
    list.status === "error" ? "Could not read the registry: " + list.error : "";
  $("reg-body").setAttribute("aria-busy", list.status === "loading");
  setHTML($("reg-body"), list.status === "ready" ? table(rows, sortable) : "");
  const d = $("reg-detail");
  d.hidden = !detail;
  setHTML(d, detail ? detailView(detail) : "");
  renderDialog();
}

function table(rows, sortable){
  if (!rows.length) return "<p class='snote'>Nothing is registered yet. Train a model, then register its run.</p>";
  const sorted = R.sortRows(rows, sort.key, sort.dir, sortable);
  const heads = R.COLUMNS.map(c => {
    const blocked = c.metric && !sortable.ok, active = sort.key === c.key && !blocked;
    return "<th scope='col' aria-sort='" + (active ? (sort.dir === "asc" ? "ascending" : "descending") : "none") + "'>" +
      "<button type='button' class='tsort' data-sort='" + attr(c.key) + "'" + (blocked ? " disabled title='" + attr(sortable.reason) + "'" : "") + ">" +
      esc(c.label) + (active ? (sort.dir === "asc" ? " ▲" : " ▼") : "") + "</button></th>";
  }).join("") + "<th scope='col'>Status</th>";
  const body = sorted.map(e => {
    const h = R.health(e), c = R.headlineCell(e.headline, e.target);
    return "<tr data-id='" + attr(e.id) + "'" + (detail && detail.id === e.id ? " class='sel'" : "") + ">" +
      "<th scope='row'><button type='button' class='tlink' data-open='" + attr(e.id) + "'>" + esc(e.name) + "</button></th>" +
      "<td>" + esc(e.model_family || "") + "</td><td>" + esc(e.initiator || "") + "</td>" +
      "<td class='mono'>" + esc((e.created_utc || "").replace("T", " ").replace("Z", " UTC")) + "</td>" +
      "<td class='mono'>" + esc(e.dataset_id || "—") + "</td><td>" + esc(R.targetText(e)) + "</td>" +
      "<td>" + esc(e.production_strategy ? T.strategyLabel(e.production_strategy) : "—") + "</td>" +
      "<td class='mono" + (c.status === "ok" ? "" : " tund") + "' title='" + attr(c.title) + "'>" + esc(c.text) +
        (c.label ? "<span class='twhy'>" + esc(c.label) + (c.fallback ? " — fallback" : "") + "</span>" : "") + "</td>" +
      "<td><span class='tstate " + (h.ok ? "ok" : "bad") + "' title='" + attr(h.reason) + "'>" + esc(h.label) + "</span></td></tr>";
  }).join("");
  return "<div class='tscroll'><table class='ttab rtab'><thead><tr>" + heads + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
    (sortable.ok ? "" : "<p class='snote'>" + esc(sortable.reason) + "</p>");
}

/* The record route has no `selectable` flag (only the listing checks that the files are in place),
   so the detail takes it from the listing's row for the same experiment. */
function withSelectable(rec){
  const row = list.items.find(e => e.id === rec.id);
  return Object.assign({}, rec, {selectable: row ? row.selectable : rec.state === "ready"});
}

function detailView(d){
  if (d.status === "loading") return "<p class='tline'>Reading the experiment…</p>";
  if (d.status === "error") return "<div class='tfail' role='alert'><span>" + esc(d.error) + "</span><span class='tbtns'>" +
    "<button class='btn' type='button' data-act='close'>Close</button></span></div>";
  const rec = d.record, h = R.health(withSelectable(rec)), run = rec.run, editable = rec.state === "ready";
  const notes = d.draft.notes.map((n, i) =>
    "<div class='note-row'><input class='note-k' type='text' maxlength='64' aria-label='Note name' value='" + attr(n.key) + "'>" +
    "<input class='note-v' type='text' maxlength='2000' aria-label='Note' value='" + attr(n.value) + "'>" +
    "<button class='btn' type='button' data-act='del-note' data-i='" + i + "' aria-label='Remove this note'>&times;</button></div>").join("");
  const m = run ? T.metricsView(run) : null;
  return "<div class='dhd'><h3>" + esc(rec.name) + "</h3><span class='tstate " + (h.ok ? "ok" : "bad") + "'>" + esc(h.label) + "</span>" +
      "<button class='btn' type='button' data-act='close' aria-label='Close the detail'>Close</button></div>" +
    (h.ok ? "" : "<p class='pdbq-msg err'>" + esc(h.reason) + "</p>") +
    "<section class='ssec'><h4 class='eyebrow'>Name and notes <span class='aside'>(the only editable parts)</span></h4>" +
      "<label class='fld'><span class='eyebrow'>Name</span><input type='text' id='reg-d-name' maxlength='200' autocomplete='off' value='" + attr(d.draft.name) + "'" + (editable ? "" : " disabled") + "></label>" +
      "<div id='reg-d-notes'>" + notes + "</div>" +
      "<div class='tbtns'><button class='btn' type='button' data-act='add-note'" + (editable ? "" : " disabled") + ">Add note</button>" +
      "<button class='btn solid' type='button' data-act='save'" + (editable && !d.saving ? "" : " disabled") + ">" + (d.saving ? "Saving…" : "Save") + "</button>" +
      "<button class='btn danger' type='button' data-act='delete' data-id='" + attr(rec.id) + "'>Delete…</button></div>" +
      "<p class='pdbq-msg" + (d.msg && d.msg.err ? " err" : "") + "' id='reg-d-msg' aria-live='polite'>" + esc(d.msg ? d.msg.text : "") + "</p>" +
      "<p class='snote'>" + esc(R.FIXED_NOTE) + "</p></section>" +
    (run ? "<div class='tgrid2'>" +
      "<section class='ssec'><h4 class='eyebrow'>Selected production model</h4>" + training.kv(T.productionView(run)) + "</section>" +
      "<section class='ssec'><h4 class='eyebrow'>Target and features</h4>" + training.kv(R.schemaRows(rec)) + "</section>" +
      "<section class='ssec wide'><h4 class='eyebrow'>Both evaluation strategies</h4>" + training.metricsTable(m) + "</section>" +
      m.repeats.map(training.repeatTable).join("") +
      "<p class='snote'>" + esc(R.REPEAT_NOTE) + "</p>" +
      "<section class='ssec'><h4 class='eyebrow'>Provenance and artifacts</h4>" + training.kv(R.provenanceRows(rec)) + "</section></div>"
      : "<p class='snote'>This record has no run details.</p>");
}

function renderDialog(){
  const dlg = $("regdlg");
  if (!del){ if (dlg.open) dlg.close(); return; }
  const c = R.deleteConfirmation(del.exp), busy = del.phase === "deleting", o = del.outcome;
  $("rd-title").textContent = c.title;
  $("rd-body").textContent = c.body;
  $("rd-go").textContent = o ? "Delete again" : c.confirm;
  $("rd-go").disabled = busy;
  $("rd-cancel").disabled = busy;
  const msg = $("rd-msg");
  msg.className = "pdbq-msg" + (o ? " err" : "");
  setHTML(msg, busy ? "Deleting…" : o ? esc(o.message) + " " + esc(o.note || "") +
    (o.parts.length ? "<ul class='tlines'>" + o.parts.map(p => "<li>" + esc(p.label) + ": " + esc(p.status) + "</li>").join("") + "</ul>" : "") : "");
  if (!dlg.open) dlg.showModal();
}

/* ---------------- controls ---------------- */

export function bind(){
  $("reg-tog").onclick = () => {
    open = !open;
    $("reg-tog").setAttribute("aria-expanded", open);
    $("reg-tog").setAttribute("aria-label", open ? "Collapse the registry panel" : "Expand the registry panel");
    $("reg-tog").innerHTML = open ? "&#9662;" : "&#9652;";
    commit();
  };
  $("reg-refresh").onclick = load;
  $("reg-body").onclick = ev => {
    const s = ev.target.closest("[data-sort]"), o = ev.target.closest("[data-open]");
    if (s && !s.disabled){
      sort = {key: s.dataset.sort, dir: sort.key === s.dataset.sort && sort.dir === "asc" ? "desc" : "asc"};
      commit();
    }else if (o) openDetail(o.dataset.open);
  };
  $("reg-detail").onclick = ev => {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "close") closeDetail();
    else if (act === "save") save();
    else if (act === "delete") askDelete(b.dataset.id);
    else if (act === "add-note" || act === "del-note"){
      readDraft();
      if (act === "add-note") detail.draft.notes.push({key: "", value: ""});
      else detail.draft.notes.splice(+b.dataset.i, 1);
      commit();
    }
  };
  $("rd-go").onclick = confirmDelete;
  $("rd-cancel").onclick = () => { if (del && del.phase !== "deleting"){ del = null; commit(); } };
  $("regdlg").addEventListener("cancel", ev => { if (del && del.phase === "deleting") ev.preventDefault(); else del = null; });
  load();
}
