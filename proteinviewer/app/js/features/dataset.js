/* The ligands page's datasets: the prompt to load one, the dialog that asks what
   is in it, the summary of what was read, and the address each one lives at.

   Upload is two stages. The file is sent as it is and the server answers with
   its columns; nothing can train on it yet. The dialog then asks -- which
   column holds the structures, which (if any) the target, whether the target is
   regression or classification, what qualifies it and what its units are --
   and the answers go back to be resolved. Cancelling, or closing the dialog any
   other way, deletes the staged upload. A refusal (a column that holds no
   SMILES, a pKi from the wrong units) leaves the dialog open to change the
   answer. The rules the form follows are model/dataset.js's, and testable.

   The page's address says which dataset is open -- /ligands/<id> -- so a
   dataset, even one still waiting for its columns to be chosen, is reopened by
   its link, after a reload, and with Back. */

import {state, commit} from "../core/state.js";
import {$, esc, setMsg, note, fault, setHTML} from "../core/dom.js";
import {readText, hasFiles} from "../core/files.js";
import * as api from "../core/api.js";
import * as M from "../model/dataset.js";
import {showDataset} from "./molecules.js";

const dlg = () => $("datadlg");
const opt = (value, label, selected, extra) =>
  "<option value='" + esc(String(value)) + "'" + (selected ? " selected" : "") + ">" + esc(label) +
  (extra ? " — " + extra : "") + "</option>";

/* ---------------- the page ---------------- */
export function render(st){
  const d = st.dataset, meta = d.ready;
  /* Until a dataset is open the centre asks for one. */
  const prompting = !meta && !d.staged;
  const unavailable = st.server.known && !st.server.datasets;
  $("ds-prompt").hidden = !prompting;
  $("mod-data").hidden = !meta;
  $("ds-n").textContent = meta ? meta.n_rows + " rows" : "";
  setHTML($("ds-sum"), meta ? M.summaryRows(meta).map(r =>
    "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>").join("") : "");
  const said = unavailable ? fault(NO_LIGANDBENCH) : d.msg;
  setMsg($("ds-prompt-msg"), prompting ? said : null);
  setMsg($("ds-msg"), prompting ? null : said);
  $("ds-open").disabled = $("ds-open-big").disabled = d.busy || !!d.staged || unavailable;
  document.title = meta ? meta.name + " · Ligands · DrugHunter" : "Ligands · DrugHunter";
}

const NO_LIGANDBENCH = "This server cannot read ligand files: ligandbench or RDKit is missing. " +
                       "The server's start-up lines say which.";

/* ---------------- the address ---------------- */
function setPath(id, replace){
  const path = M.ligandsPath(id);
  if (location.pathname === path) return;
  history[replace ? "replaceState" : "pushState"](null, "", path);
}

/* The page's address says which dataset to open; follow it. Runs on load and on
   Back and Forward. */
const routing = M.makeLatest();
export async function openFromUrl(){
  const d = state.dataset, want = M.datasetFromPath(location.pathname);
  const ticket = routing.issue();
  leaveDialog();
  if (want.error || !want.id){
    d.ready = null; d.busy = false;
    d.msg = want.error ? fault(want.error) : null;
    return commit();
  }
  if (d.ready && d.ready.dataset_id === want.id){ d.msg = null; return commit(); }
  d.ready = null; d.busy = true;
  d.msg = note("Opening <b>" + esc(want.id) + "</b>…");
  commit();
  try{
    const found = await api.dataset(want.id);
    if (!routing.current(ticket)) return;
    d.busy = false; d.msg = null;
    if (found.state === "ready"){
      d.ready = found;
      showDataset();
    }else{
      /* An upload that was left before its columns were chosen: pick it up there. */
      d.staged = found; d.form = M.newForm(found);
      commit();
      openDialog();
    }
  }catch(err){
    if (!routing.current(ticket)) return;
    d.busy = false;
    d.msg = fault(err.message + " — load the file again.");
    commit();
  }
}

/* ---------------- loading a file ---------------- */

/* A file the user chose or dropped. */
async function openFile(file){
  const d = state.dataset;
  if (!M.isCsvName(file.name)){
    d.msg = fault(file.name + " is not a .csv file. This page reads ligand CSVs; " +
                  "structures open in the viewer.");
    return commit();
  }
  let text;
  try{ text = await readText(file); }
  catch(err){ d.msg = fault(file.name + ": " + err.message); return commit(); }
  openDataset(file.name, text);
}

/* The text of a CSV: stage it, then ask about it. */
export async function openDataset(name, text){
  const d = state.dataset;
  if (d.busy || d.staged) return;
  d.busy = true; d.msg = note("Reading <b>" + esc(name) + "</b>…");
  commit();
  try{
    const staged = await api.datasetUpload(name, text);
    d.staged = staged; d.form = M.newForm(staged); d.msg = null; d.busy = false;
    setPath(staged.dataset_id);   /* the upload has an address already */
    commit();
    openDialog();
  }catch(err){
    d.busy = false;
    d.msg = fault(name + ": " + err.message);
    commit();
  }
}

/* ---------------- the dialog ---------------- */
function openDialog(){
  $("dd-msg").textContent = "";
  $("dd-busy").textContent = "";
  syncDialog();
  const d = dlg();
  if (!d.open) d.showModal();
}

/* Everything in the dialog, written from the form: the selects are rebuilt, so
   what the form holds is what is shown and nothing else is. */
function syncDialog(){
  const {staged, form} = state.dataset;
  if (!staged) return;
  const vm = M.describeForm(staged, form);
  $("dd-sub").textContent = staged.name + " · " + staged.n_rows + " rows · " +
                            staged.columns.length + " columns";

  $("dd-structure").innerHTML = opt("", "— choose —", form.structure === null) +
    vm.structureOptions.map(c => opt(c.index, c.name, form.structure === c.index,
                                     c.suggested ? "looks like SMILES" : "")).join("");
  const note_ = $("dd-struct-note");
  note_.textContent = vm.structureNote; note_.hidden = !vm.structureNote;

  $("dd-target").innerHTML = opt("", "— choose —", form.target === null) +
    opt(M.NO_TARGET, "No target — prediction only", form.target === M.NO_TARGET) +
    vm.targetOptions.map(c => opt(c.index, c.name, form.target === c.index)).join("");

  $("dd-kind-wrap").hidden = !vm.hasTarget;
  document.querySelectorAll("input[name=dd-kind]").forEach(r => { r.checked = r.value === form.kind; });
  $("dd-kind-reg").textContent = vm.suggestedKind === "regression" ? "suggested: the cells are numbers" : "";
  $("dd-kind-cls").textContent = vm.suggestedKind === "classification" ? "suggested: the cells are text" : "";

  $("dd-reg").hidden = !vm.qualifierShown;
  $("dd-qualifier").innerHTML = opt("", "— choose —", form.qualifier === null) +
    opt(M.NO_QUALIFIER, "None — every value is exact", form.qualifier === M.NO_QUALIFIER) +
    vm.qualifierOptions.map(c => opt(c.index, c.name, form.qualifier === c.index,
      c.index === vm.suggestedQualifier ? "suggested" : "")).join("");
  $("dd-units").innerHTML = opt("", "— choose —", form.units === null).replace("value=''", "value='?'") +
    M.UNITS.map(u => opt("u:" + u.value, u.label, form.units === u.value,
      u.value && u.value === vm.suggestedUnits ? "suggested by the column name" : "")).join("");
  document.querySelectorAll("input[name=dd-transform]").forEach(r => {
    r.checked = r.value === form.transform;
    if (r.value === "pki") r.disabled = !vm.pkiEnabled;
  });
  $("dd-pki-aside").textContent = vm.pkiEnabled ? "" : "needs the units confirmed as nM";

  $("dd-cls").hidden = !vm.positiveShown;
  $("dd-positive").innerHTML = opt("", "— choose —", form.positive === null) +
    (vm.classes || []).map(c => opt(c, c, form.positive === c)).join("");

  $("dd-errors").textContent = vm.errors[0] || "";
  $("dd-go").disabled = vm.errors.length > 0 || !!state.dataset.busy;
}

/* One change in the dialog: read it into the form, let the form settle, redraw. */
function onChange(ev){
  const {staged, form} = state.dataset;
  if (!staged) return;
  const t = ev.target, id = t.id, f = Object.assign({}, form);
  const idx = v => v === "" ? null : +v;
  let changed = id;
  if (id === "dd-structure") f.structure = idx(t.value);
  else if (id === "dd-target"){ f.target = idx(t.value); changed = "target"; }
  else if (t.name === "dd-kind"){ f.kind = t.value; changed = "kind"; }
  else if (id === "dd-qualifier") f.qualifier = idx(t.value);
  else if (id === "dd-units") f.units = t.value === "?" ? null : t.value.slice(2);
  else if (t.name === "dd-transform") f.transform = t.value;
  else if (id === "dd-positive") f.positive = t.value === "" ? null : t.value;
  else return;
  state.dataset.form = M.settle(staged, f, changed);
  $("dd-msg").textContent = "";
  syncDialog();
}

async function submit(){
  const d = state.dataset, body = d.staged && M.buildResolveRequest(d.staged, d.form);
  if (!body) return;
  d.busy = true; $("dd-go").disabled = true; $("dd-msg").textContent = "";
  $("dd-busy").textContent = "resolving…";
  try{
    const meta = await api.datasetResolve(body);
    d.ready = meta; d.staged = null; d.form = null; d.busy = false;
    d.msg = null;   /* the summary in the rail says what was read */
    dlg().close();
    setPath(meta.dataset_id);   /* the same id for a staged upload; a new one if resolved again */
    showDataset();
    commit();
  }catch(err){
    /* Recoverable: the upload is still staged, and the dialog says what to change. */
    d.busy = false;
    $("dd-busy").textContent = "";
    $("dd-msg").textContent = err.message;
    syncDialog();
  }
}

/* However the dialog closes without a dataset -- Cancel, Esc -- the staged
   upload goes with it. */
function abandon(){
  const d = state.dataset;
  if (!d.staged) return;
  const id = d.staged.dataset_id;
  d.staged = null; d.form = null; d.busy = false;
  api.datasetCancel(id).catch(() => {});   /* an expired upload is already gone */
  if (location.pathname === M.ligandsPath(id)) setPath(null, true);
  commit();
}

/* Going Back or Forward away from an upload's address closes its dialog but
   leaves the upload on the server: it is waiting there, and Forward finds it. */
function leaveDialog(){
  const d = state.dataset;
  d.staged = null; d.form = null;
  if (dlg().open) dlg().close();
}

export function bind(){
  const pick = $("ds-file");
  const choose = () => pick.click();
  $("ds-open").onclick = choose;
  $("ds-open-big").onclick = choose;
  pick.onchange = () => {
    /* The files are taken out first: clearing the value empties the live list,
       and clears it so choosing the same file twice is two opens. */
    const files = Array.prototype.slice.call(pick.files);
    pick.value = "";
    if (files[0]) openFile(files[0]);
  };
  bindDrop();
  const d = dlg();
  d.addEventListener("change", onChange);
  $("dd-form").addEventListener("submit", ev => { ev.preventDefault(); submit(); });
  $("dd-cancel").onclick = () => d.close();
  d.addEventListener("close", abandon);
  /* The round i beside the qualifier: a note, opened by click, as in Score poses. */
  d.querySelectorAll("button.info").forEach(b => {
    b.onclick = () => {
      const tip = $(b.getAttribute("aria-controls")), open = tip.hidden;
      tip.hidden = !open;
      b.setAttribute("aria-expanded", open);
    };
  });
}

/* Dropping anywhere on the page, not on a target: a file dropped beside it would
   navigate the browser away. */
function bindDrop(){
  const zone = $("dropzone");
  let depth = 0;
  const show = on => { zone.hidden = !on; };
  const idle = () => !state.dataset.busy && !state.dataset.staged;
  window.addEventListener("dragenter", ev => { if (hasFiles(ev) && idle()){ depth++; show(true); } });
  window.addEventListener("dragover", ev => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = idle() ? "copy" : "none";
  });
  window.addEventListener("dragleave", ev => {
    if (!hasFiles(ev)) return;
    if (--depth <= 0){ depth = 0; show(false); }
  });
  window.addEventListener("drop", ev => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    depth = 0; show(false);
    if (!idle()) return;
    const files = Array.prototype.slice.call(ev.dataTransfer.files);
    if (files[0]) openFile(files[0]);   /* one dataset at a time */
  });
}
