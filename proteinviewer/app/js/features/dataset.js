/* A ligand CSV: the dialog that asks what is in it, and the summary of what was
   read.

   Upload is two stages. The file is sent as it is and the server answers with
   its columns; nothing can train on it yet. The dialog then asks -- which
   column holds the structures, which (if any) the target, whether the target is
   regression or classification, what qualifies it and what its units are --
   and the answers go back to be resolved. Cancelling, or closing the dialog any
   other way, deletes the staged upload. A refusal (a column that holds no
   SMILES, a pKi from the wrong units) leaves the dialog open to change the
   answer. The rules the form follows are model/dataset.js's, and testable. */

import {state, commit} from "../core/state.js";
import {$, esc, setMsg, note, fault, setHTML} from "../core/dom.js";
import * as api from "../core/api.js";
import * as M from "../model/dataset.js";
import {showDataset} from "./molecules.js";

const dlg = () => $("datadlg");
const opt = (value, label, selected, extra) =>
  "<option value='" + esc(String(value)) + "'" + (selected ? " selected" : "") + ">" + esc(label) +
  (extra ? " — " + extra : "") + "</option>";

/* ---------------- the rail ---------------- */
export function render(st){
  const d = st.dataset, meta = d.ready;
  $("mod-data").hidden = !(meta || d.msg);
  $("ds-n").textContent = meta ? meta.n_rows + " rows" : "";
  setHTML($("ds-sum"), meta ? M.summaryRows(meta).map(r =>
    "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>").join("") : "");
  setMsg($("ds-msg"), d.msg);
  const picking = !!d.staged;
  $("open-file").disabled = st.fetch.busy || picking;
}

/* ---------------- upload ---------------- */

/* The text of a CSV the user chose or dropped: stage it, then ask about it. */
export async function openDataset(name, text){
  const d = state.dataset;
  if (!state.server.datasets && state.server.known){
    d.msg = fault("This server cannot read ligand files: ligandbench or RDKit is missing. " +
                  "The server's start-up lines say which.");
    return commit();
  }
  state.busy = "reading " + name + "…";
  d.busy = true; d.msg = note("Reading <b>" + esc(name) + "</b>…");
  commit();
  try{
    const staged = await api.datasetUpload(name, text);
    d.staged = staged; d.form = M.newForm(staged); d.msg = null;
    state.busy = null; d.busy = false;
    commit();
    openDialog();
  }catch(err){
    state.busy = null; d.busy = false;
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
    showDataset(meta);
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
  commit();
}

export function bind(){
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
