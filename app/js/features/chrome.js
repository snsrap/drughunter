/* Everything around the stage that only reports state: the overlay naming
   what is on screen, the legend, the empty and all-hidden banners, the
   panel counts, the Undo link and the busy pill. */

import {state, nodeOf, liveRun, liveGen, entity} from "../core/state.js";
import {$, esc, setHTML} from "../core/dom.js";
import {cssVar, entColor, pocketColor} from "../core/colors.js";
import {summarize} from "../model/scope.js";

export function render(st){
  const vis = st.ents.filter(e => e.visible);
  document.querySelector(".ov-tl").hidden = !vis.length;
  $("ov-id").textContent = vis.map(e => e.id).join(" · ");
  $("ov-sub").textContent = vis.length === 1
    ? [vis[0].title, vis[0].sub].filter(Boolean).join(" · ")
    : vis.length + " structures, each in its own deposited frame — nothing is superposed";
  const by = {};
  st.picks.forEach(k => {
    const x = nodeOf(k);
    if (x) (by[x.e.id] = by[x.e.id] || []).push(x.n);
  });
  $("ov-pick").textContent = st.picks.length ? "selected · " +
    Object.keys(by).map(id => id + ": " + summarize(by[id])).join("; ") : "";

  const b = $("banner");
  b.className = vis.length ? "banner" : "banner info on";
  setHTML(b, vis.length ? ""
    : st.ents.length ? "<b>Everything is hidden.</b> Use the eye beside a structure in the Receptor panel to show it."
    : "<b>Nothing loaded.</b> Fetch a PDB entry to start.");

  $("ent-n").textContent = st.ents.length ? st.ents.length + " loaded" : "";
  $("ent-empty").hidden = st.ents.length > 0;
  $("tree-hint").hidden = !st.ents.length;
  $("sel-clear").hidden = !st.picks.length;
  const u = $("undo"), last = st.undo[st.undo.length - 1];
  u.hidden = !last;
  u.title = last ? "Put back " + last.what + " (Ctrl/⌘-Z)" : "";

  $("busy").classList.toggle("on", !!st.busy);
  if (st.busy) $("busy-t").textContent = st.busy;

  renderKey(st, vis);
}

/* Legend for what is drawn. Each pocket has its own colour, shared by the
   molecules generated for it. */
function renderKey(st, vis){
  const run = liveRun(), gen = liveGen(), k = [], idx = run ? run.picks.slice() : [];
  if (gen){
    Object.keys(gen.groups).forEach(r => {
      const j = run.pockets.findIndex(p => String(p.rank) === String(r));
      if (gen.groups[r].visible && j >= 0 && idx.indexOf(j) === -1) idx.push(j);
    });
  }
  vis.forEach(e => k.push([entColor(e), e.id]));
  if (vis.length) k.push([cssVar("--gl-carbon-het"), "hetero"]);
  if (run && entity(run.ent).visible) idx.sort((a, b) => a - b).forEach(i => {
    if (run.pockets[i]) k.push([pocketColor(i), "pocket " + run.pockets[i].rank]);
  });
  if (st.picks.length) k.push([cssVar("--gl-carbon-sel"), "selected"]);
  setHTML($("ov-key"), k.map(p =>
    "<span class='key'><i style='background:" + p[0] + "'></i>" + esc(p[1]) + "</span>").join(""));
}
