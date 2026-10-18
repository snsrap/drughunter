/* Fetch from PDB: add an entry from RCSB to what is loaded. */

import {state, commit, addEntity, freeId} from "../core/state.js";
import {$, esc, setMsg, note, fault} from "../core/dom.js";
import {ENT_COLORS} from "../core/colors.js";
import {fetchEntry, isPdbId} from "../core/rcsb.js";

export function render(st){
  $("pdbq").querySelector("button").disabled = st.fetch.busy;
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

export function bind(){
  $("pdbq").onsubmit = ev => {
    ev.preventDefault();
    load($("pdb-id").value.trim().toUpperCase());
  };
}
