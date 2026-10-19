/* Boot: the page's composition root.

   Registers every view with the store, in the order they must render (the
   scene first: parsing a structure is what builds its tree), binds each
   feature's controls, and asks the server what it can run. */

import {state, subscribe, commit} from "./core/state.js";
import {$} from "./core/dom.js";
import * as api from "./core/api.js";
import * as scene from "./scene.js";
import * as chrome from "./features/chrome.js";
import * as fetchPanel from "./features/fetch.js";
import * as tree from "./features/tree.js";
import * as prep from "./features/prep.js";
import * as superposePanel from "./features/superpose.js";
import * as pockets from "./features/pockets.js";
import * as generate from "./features/generate.js";
import * as table from "./features/table.js";
import * as project from "./features/project.js";
import * as theme from "./features/theme.js";

const FEATURES = [fetchPanel, tree, prep, superposePanel, pockets, generate, table, project];

/* Ask once, on load, so a missing tool or a stale server is reported before
   anyone presses a button. */
async function probeServer(){
  const s = state.server;
  try{
    const j = await api.status();
    const d = j.detectors || {};
    s.fpocket = !!(d.fpocket && d.fpocket.available);
    /* Only the backends lacuna can actually run: its MD backend needs OpenMM
       in lacuna's own environment, and offering it otherwise would fail a
       minute into a run instead of now. */
    s.lacuna = d.lacuna && d.lacuna.available ? (d.lacuna.backends || []) : [];
    s.prep = !!(j.prep && j.prep.available);
    s.neat = !!(j.neat && j.neat.available);
    s.projects = !!(j.projects && j.projects.available);
    s.projectRoot = (j.projects && j.projects.root) || "";
  }catch(err){
    if (err instanceof api.StaleServerError) s.stale = true;
    else s.error = err.message;
  }
  s.known = true;
  commit();
}

function start(){
  scene.init($("gl"));
  [scene, chrome].concat(FEATURES).forEach(m => subscribe(m.render));
  FEATURES.forEach(m => m.bind());
  theme.bind();
  $("fitall").onclick = () => scene.fitAll(false);
  /* The stage is sized by the layout, so follow the box itself: the window
     and the table panel both change it. */
  let raf = 0;
  const onResize = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(scene.resize);
  };
  if (window.ResizeObserver) new ResizeObserver(onResize).observe($("gl"));
  else window.addEventListener("resize", onResize);

  commit({fit: true});
  probeServer();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
