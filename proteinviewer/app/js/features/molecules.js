/* The ligands of a dataset: a page of cards, each a depiction with its name and
   target, and every original column behind a click.

   The server does the work that needs all the rows -- sorting, filtering,
   counting -- and sends one page (50 by default, never over 100) with only that
   page's drawings. This module keeps a query, asks for the page it describes,
   and ignores any answer that is not to the newest question. The logic that can
   be wrong lives in model/dataset.js, where it is tested. */

import {state, commit} from "../core/state.js";
import {$, esc, setMsg, note, fault, setHTML, attr} from "../core/dom.js";
import * as api from "../core/api.js";
import * as M from "../model/dataset.js";
import {safeSvg} from "../model/svg.js";
import * as P from "../model/prediction.js";

const latest = M.makeLatest();
let built = null;            /* what the cards were last built from */
let controlsFor = null;      /* the dataset the sort and filter controls were filled for */

const meta = () => state.dataset.ready;
const schema = () => (meta() ? meta().schema : []);

/* ---------------- loading ---------------- */

/* A dataset has just been resolved: start from its first page, in source order. */
export function showDataset(){
  state.mol = {query: M.newQuery(), page: null, loading: false, error: null, sel: null,
               open: true, serial: state.mol.serial + 1};
  load();
}

async function load(){
  const ds = meta();
  if (!ds) return;
  const ticket = latest.issue(), id = ds.dataset_id, q = state.mol.query;
  state.mol.loading = true; state.mol.error = null;
  commit();
  let page = null, error = null;
  try{ page = await api.datasetRows(M.queryBody(q, id)); }
  catch(err){ error = err.message; }
  /* A newer question, or another dataset, has taken over: this answer is for one
     nobody is waiting on. */
  if (!latest.current(ticket) || !meta() || meta().dataset_id !== id) return;
  state.mol.loading = false;
  if (page){ state.mol.page = page; state.mol.serial++; }
  state.mol.error = error;
  commit();
}

/* The same question again, for a page whose columns changed (a model's predictions arrived or went). */
export function refresh(){ if (meta() && state.mol.query) load(); }

function ask(query){
  state.mol.query = query;
  load();
}

/* ---------------- view ---------------- */
export function render(st){
  const ds = st.dataset.ready, m = st.mol;
  $("mtab").hidden = !ds || !m.query;
  if (!ds || !m.query){ built = null; controlsFor = null; return; }
  if (controlsFor !== P.schemaKey(ds)) fillControls(ds);

  const q = m.query, page = m.page, info = M.pageInfo(page ? page.total : 0, q.offset, q.limit);
  $("mol-n").textContent = !page ? "" : page.total === ds.n_rows ? ds.n_rows + " rows"
    : page.total + " of " + ds.n_rows;
  $("mol-sort-key").value = q.sort.key;
  const desc = q.sort.dir === "desc";
  $("mol-sort-dir").textContent = desc ? "▼ descending" : "▲ ascending";
  $("mol-size").value = String(q.limit);
  $("mol-range").textContent = !page ? "" : info.total
    ? info.from + "–" + info.to + " of " + info.total : "no rows match";
  $("mol-prev").disabled = info.prev === null || m.loading;
  $("mol-next").disabled = info.next === null || m.loading;
  setHTML($("mol-chips"), q.filters.map((f, i) =>
    "<button class='chip' type='button' data-i='" + i + "' title='Remove this filter'>" +
    esc(M.filterText(f, schema())) + "</button>").join(""));
  setMsg($("mol-msg"), m.error ? fault(m.error) : m.loading && !page ? note("Loading…") : null);

  /* Cards cost a depiction each, so they are rebuilt only when the page or the
     selection changes -- not on every commit the 3D view makes. */
  const key = m.serial + "|" + (m.sel ? m.sel.row_id : "") + "|" + ds.dataset_id;
  if (key !== built){
    built = key;
    $("mol-grid").innerHTML = page ? page.rows.map(r => card(r, ds, m.sel)).join("") : "";
    renderDetail(ds, m.sel);
  }
  $("mtab").classList.toggle("closed", !m.open);
  $("mol-grid").setAttribute("aria-busy", m.loading);
}

function card(r, ds, sel){
  const drawing = r.smiles ? safeSvg(r.svg || "") : "";
  const bad = !r.smiles, out = ds.trainable && !r.training_eligible && !bad;
  const inner = bad
    ? "<span class='mdepict'>record " + r.record + "<br>" + esc(r.structure_error || "no structure") + "</span>"
    : "<span class='mdepict'>" + drawing + "</span>";
  return "<button type='button' class='mcard" + (bad ? " bad" : "") + (out ? " out" : "") + "' data-row='" +
    r.row_id + "' aria-pressed='" + (!!sel && sel.row_id === r.row_id) + "' title='" +
    attr(r.label + (r.training_eligible ? "" : " — not used for training")) + "'>" + inner +
    "<span class='mname'>" + esc(r.label) + "</span>" +
    "<span class='mval mono'>" + esc(M.targetText(r, ds)) + "</span>" +
    P.cardPredictions(r, ds.schema).map(p => "<span class='mpred mono' title='" + attr(p.title) + "'>" + esc(p.label) + ": " + esc(p.text) + "</span>").join("") + "</button>";
}

function renderDetail(ds, row){
  const el = $("mol-detail");
  el.hidden = !row;
  if (!row){ el.innerHTML = ""; return; }
  const d = M.detail(row, ds);
  const drawing = row.smiles ? safeSvg(row.svg || "") : "";
  el.innerHTML =
    "<h3><span>" + esc(d.title) + "</span><button class='lnk' type='button' id='mol-close'>Close</button></h3>" +
    "<div class='sub'>record " + d.record + " · row id " + d.rowId + "</div>" +
    (drawing ? "<span class='mdepict big'>" + drawing + "</span>"
             : "<div class='why'>" + esc(d.structureError || "no structure") + "</div>") +
    (d.model.length ? "<table>" + d.model.map(m =>
      "<tr class='key'><th>" + esc(m.label) + "</th><td>" + esc(m.value) + "</td></tr>").join("") + "</table>" : "") +
    predictionTable(row, ds) +
    (d.trains ? "<div class='why ok'>Used for training.</div>"
      : "<div class='why'>Not used for training:<ul>" + d.reasons.map(r => "<li>" + esc(r) + "</li>").join("") + "</ul></div>") +
    "<table>" + (d.smiles ? "<tr><th>Canonical SMILES</th><td class='mono'>" + esc(d.smiles) + "</td></tr>" : "") +
    d.columns.map(c => "<tr" + (c.isStructure || c.isTarget ? " class='key'" : "") + "><th>" + esc(c.name) +
      "</th><td>" + esc(c.value === "" ? "—" : c.value) + "</td></tr>").join("") + "</table>";
  $("mol-close").onclick = () => { state.mol.sel = null; commit(); };
}

/* What each registered model predicted for this row, apart from the measurements: labelled as
   predictions, with a reason where a row could not be predicted. */
function predictionTable(row, ds){
  const rows = P.detailPredictions(row, ds.schema);
  if (!rows.length) return "";
  return "<h4 class='eyebrow'>Predictions (not measurements)</h4><table class='pred'>" + rows.map(r =>
    "<tr><th>" + esc(r.label) + "</th><td class='mono'>" + esc(r.text) + (r.why ? "<span class='twhy'>" + esc(r.why) + "</span>" : "") + "</td></tr>").join("") + "</table>";
}

/* The sort and filter controls offer the dataset's own columns, typed by the
   server, so a number column offers comparisons and a text column offers search. */
function fillControls(ds){
  controlsFor = P.schemaKey(ds);
  const cols = ds.schema;
  $("mol-sort-key").innerHTML = cols.map(c => "<option value='" + attr(c.key) + "'>" + esc(c.label) + "</option>").join("");
  $("mol-f-key").innerHTML = $("mol-sort-key").innerHTML;
  $("mol-size").innerHTML = M.PAGE_SIZES.map(n => "<option value='" + n + "'>" + n + "</option>").join("");
  fillOperators();
}

function fillOperators(){
  const ds = meta();
  if (!ds) return;
  const col = ds.schema.find(c => c.key === $("mol-f-key").value);
  const ops = M.operatorsFor(col, ds.operators);
  $("mol-f-op").innerHTML = ops.map(o => "<option value='" + o + "'>" + esc(M.OP_LABELS[o] || o) + "</option>").join("");
  syncValue();
}

function syncValue(){
  const missing = $("mol-f-op").value === "missing";
  $("mol-f-val").hidden = missing;
  const ds = meta(), col = ds && ds.schema.find(c => c.key === $("mol-f-key").value);
  $("mol-f-val").placeholder = col && col.type === "number" ? "number" : "text";
}

/* ---------------- actions ---------------- */
function addFilter(){
  const ds = meta();
  if (!ds) return;
  const col = ds.schema.find(c => c.key === $("mol-f-key").value);
  const parsed = M.parseFilter(col, $("mol-f-op").value, $("mol-f-val").value);
  if (parsed.error){
    state.mol.error = parsed.error;   /* shown where a server refusal would be */
    return commit();
  }
  $("mol-f-val").value = "";
  ask(M.withFilter(state.mol.query, parsed.filter));
}

export function bind(){
  $("mol-sort-key").onchange = ev => ask(M.withSort(state.mol.query, ev.target.value, "asc"));
  $("mol-sort-dir").onclick = () => {
    const s = state.mol.query.sort;
    ask(M.withSort(state.mol.query, s.key, s.dir === "asc" ? "desc" : "asc"));
  };
  $("mol-size").onchange = ev => ask(M.withLimit(state.mol.query, ev.target.value));
  $("mol-f-key").onchange = fillOperators;
  $("mol-f-op").onchange = syncValue;
  $("mol-f-add").onclick = addFilter;
  $("mol-f-val").onkeydown = ev => { if (ev.key === "Enter"){ ev.preventDefault(); addFilter(); } };
  $("mol-chips").onclick = ev => {
    const b = ev.target.closest("button[data-i]");
    if (b) ask(M.withoutFilter(state.mol.query, +b.dataset.i));
  };
  const page = delta => () => {
    const q = state.mol.query, info = M.pageInfo(state.mol.page ? state.mol.page.total : 0, q.offset, q.limit);
    const to = delta < 0 ? info.prev : info.next;
    if (to !== null) ask(M.withOffset(q, to));
  };
  $("mol-prev").onclick = page(-1);
  $("mol-next").onclick = page(1);
  $("mol-grid").onclick = ev => {
    const b = ev.target.closest(".mcard");
    if (!b || !state.mol.page) return;
    const row = state.mol.page.rows.find(r => r.row_id === +b.dataset.row);
    if (row){ state.mol.sel = row; commit(); }
  };
  $("mol-tog").onclick = () => {
    const open = !state.mol.open;
    state.mol.open = open;
    $("mol-tog").setAttribute("aria-expanded", open);
    $("mol-tog").setAttribute("aria-label", open ? "Collapse the ligand panel" : "Expand the ligand panel");
    $("mol-tog").innerHTML = open ? "&#9662;" : "&#9652;";
    commit();
  };
}
