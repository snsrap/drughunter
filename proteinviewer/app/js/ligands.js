/* Boot for the ligands page, /ligands and /ligands/<dataset id>: the prompt to
   load a dataset, the dialog that asks what is in it, and the panel of ligands.

   Registers the views with the store, binds their controls, asks the server
   whether it can read ligand files, and then opens whatever dataset the address
   names. The 3D viewer is another page; nothing of it is loaded here. */

import {state, subscribe, commit} from "./core/state.js";
import {$} from "./core/dom.js";
import * as api from "./core/api.js";
import * as dataset from "./features/dataset.js";
import * as molecules from "./features/molecules.js";
import * as summary from "./features/summary.js";
import * as training from "./features/training.js";
import * as registry from "./features/registry.js";
import * as theme from "./features/theme.js";

/* The registry does not follow the dataset: its records outlive it. The summary, the training panel and the ligand panel each follow the open dataset on
   their own; none reads anything of another. */
const FEATURES = [dataset, summary, training, registry, molecules];

/* Ask once, on load, so a server that cannot read ligand files is said so before
   anyone chooses a file. */
async function probeServer(){
  const s = state.server;
  try{
    const j = await api.status();
    if (j.version) $("version").textContent = "v" + j.version;
    s.datasets = !!(j.datasets && j.datasets.available);
  }catch(err){
    if (err instanceof api.StaleServerError) s.stale = true;
    else s.error = err.message;
  }
  s.known = true;
  commit();
}

function start(){
  subscribe(summary.follow);   /* ahead of the views, so the one after it draws the request it starts */
  subscribe(training.follow);  /* likewise: a new dataset is a new panel before it is drawn */
  FEATURES.forEach(m => subscribe(m.render));
  FEATURES.forEach(m => m.bind());
  theme.bind();
  commit();
  window.addEventListener("popstate", dataset.openFromUrl);
  probeServer().then(dataset.openFromUrl);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
