/* Where structures come from: an entry fetched from RCSB by its id, or a file
   the user opens or drops -- coordinates of their own, or a session saved
   here. A file is added exactly as a fetched entry is, in its own frame and
   its own colour, because everything after this point works on the text and
   never on the id. */

import {state, commit, addEntity, freeId} from "../core/state.js";
import {$, esc, setMsg, note, fault, plural} from "../core/dom.js";
import {ENT_COLORS} from "../core/colors.js";
import {fetchEntry, isPdbId} from "../core/rcsb.js";
import {readText, hasFiles} from "../core/files.js";
import {formatOf, idFromName, titleOf, firstModel, hasAtoms, fileSub} from "../model/structurefile.js";
import {isSessionText} from "../model/session.js";
import {openSession} from "./session.js";

export function render(st){
  $("pdbq").querySelector("button").disabled = st.fetch.busy;
  $("open-file").disabled = st.fetch.busy;
  setMsg($("pdb-msg"), st.fetch.msg);
}

async function load(id){
  if (!isPdbId(id)){
    state.fetch.msg = fault(id ? "“" + id + "” is not a PDB ID — expected four characters, e.g. 1HSG."
                               : "Enter a four-character PDB ID.");
    return commit();
  }
  state.fetch = {busy: true, msg: note("Fetching <b>" + id + "</b> from RCSB…")};
  state.busy = "fetching " + id + "…";
  commit();
  let fit = false;
  try{
    const entry = await fetchEntry(id);
    /* Fetching an entry that is already loaded shows it rather than adding a
       copy -- unless parts of it were removed, when a fresh copy is wanted. */
    const e = state.ents.find(x => x.id === id && !x.prepared && !x.edited);
    if (e) e.visible = true;
    else addEntity({id: freeId(id), data: entry.data, fmt: entry.fmt, title: entry.title,
                    sub: entry.sub}, ENT_COLORS.length);
    state.fetch.msg = note("<b>" + id + "</b> " + esc(entry.title || "loaded"));
    fit = true;
  }catch(err){
    state.fetch.msg = fault(err.status === 404 ? "No PDB entry " + id + "."
      : "Could not reach RCSB for " + id + " (" + err.message + ").");
  }
  state.fetch.busy = false; state.busy = null;
  commit({fit});
}

/* One file of coordinates. Returns the line to report it by; throws if the
   text holds no structure, which is better found here than as an empty
   structure in the tree. */
function addFile(name, bytes, text){
  const fmt = formatOf(name, text);
  if (!hasAtoms(text, fmt))
    throw new Error(fmt === "cif" ? "it has no _atom_site loop — is it an mmCIF structure?"
                                  : "it has no ATOM or HETATM records — is it a PDB structure?");
  const m = firstModel(text, fmt), id = freeId(idFromName(name));
  addEntity({id, data: m.text, fmt, source: "file", title: titleOf(text, fmt),
             sub: fileSub({fmt, models: m.models, bytes})}, ENT_COLORS.length);
  return "<b>" + esc(id) + "</b> from " + esc(name) +
         (m.models > 1 ? " · first of " + m.models + " models" : "");
}

/* Whatever the user chose or dropped, in the order they gave it. */
export async function openFiles(files){
  const list = Array.prototype.slice.call(files || []);
  if (!list.length) return;
  state.fetch = {busy: true, msg: note("Reading " + plural(list.length, "file") + "…")};
  state.busy = "reading…";
  commit();
  const added = [], errors = [];
  for (const f of list){
    let text;
    try{ text = await readText(f); }
    catch(err){ errors.push(f.name + ": " + err.message); continue; }
    /* A session replaces everything on screen, so it has the last word: the
       rest of a mixed drop would be loaded and then thrown away again. */
    if (isSessionText(text)){
      state.fetch = {busy: false, msg: list.length > 1
        ? note("Opened the session in <b>" + esc(f.name) + "</b>; the other files were left.")
        : null};
      state.busy = null;
      openSession(text);
      return;
    }
    try{ added.push(addFile(f.name, f.size, text)); }
    catch(err){ errors.push(f.name + ": " + err.message); }
  }
  state.fetch = {busy: false, msg: report(added, errors)};
  state.busy = null;
  commit({fit: !!added.length});
}

function report(added, errors){
  if (errors.length)
    return {html: (added.length ? plural(added.length, "file") + " loaded · " : "") +
                  esc(errors.join(" · ")), err: true};
  if (added.length === 1) return note(added[0]);
  if (added.length) return note("<b>" + added.length + "</b> files loaded");
  return null;
}

export function bind(){
  $("pdbq").onsubmit = ev => {
    ev.preventDefault();
    load($("pdb-id").value.trim().toUpperCase());
  };
  const input = $("file-input");
  $("open-file").onclick = () => input.click();
  input.onchange = () => {
    /* input.files is live, and clearing the value empties it, so the files are
       taken out first. The value is cleared so that picking the same file
       twice is two opens rather than nothing the second time. */
    const files = Array.prototype.slice.call(input.files);
    input.value = "";
    openFiles(files);
  };
  bindDrop();
}

/* Dropping on the window, not on the stage: the rail is where the panels are,
   and a file dropped anywhere else would otherwise navigate the browser away
   from the viewer. */
function bindDrop(){
  const zone = $("dropzone");
  let depth = 0;
  const show = on => { zone.hidden = !on; };
  window.addEventListener("dragenter", ev => { if (hasFiles(ev)){ depth++; show(true); } });
  window.addEventListener("dragover", ev => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  });
  window.addEventListener("dragleave", ev => {
    if (!hasFiles(ev)) return;
    if (--depth <= 0){ depth = 0; show(false); }
  });
  window.addEventListener("drop", ev => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    depth = 0; show(false);
    openFiles(ev.dataTransfer.files);
  });
}
