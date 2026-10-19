/* Prepare: the quickprep package (open-source stand-in for MOE QuickPrep)
   runs server-side on the buffered copy of whatever the scope names. The
   result is added as a new structure in the original's frame, and the
   original is hidden rather than removed, so its eye brings it back. */

import {state, commit, addEntity, freeId} from "../core/state.js";
import {$, esc, setMsg, note, fault} from "../core/dom.js";
import {ENT_COLORS} from "../core/colors.js";
import * as api from "../core/api.js";
import {scope, scopeStructure} from "../model/scope.js";
import {ensureProject, touch} from "./project.js";

export function render(st){
  const sc = scope(st), e = sc.e, btn = $("prep-btn"), busy = st.prep.busy;
  btn.textContent = busy ? "Preparing…" : !e ? "Prepare structure"
                  : e.prepared ? "Prepared" : "Prepare " + (sc.whole ? e.id : "selection");
  btn.disabled = !!busy || !e || !!e.prepared || st.server.known && !st.server.prep;
  /* The report of the structure the panel points at, if it is a prepared one. */
  $("prep-log").hidden = !(e && e.prepared) || !!busy;
  if (e && e.prepared && !busy) $("prep-pre").textContent = e.prepSummary;
  setMsg($("prep-msg"), message(st, sc));
}

function message(st, sc){
  const e = sc.e, err = st.prep.error;
  if (st.prep.busy)
    return note("Preparing <b>" + esc(st.prep.busy) + "</b> — fixing, protonating at pH 7, then a tethered " +
                "minimisation. This takes a few seconds to a minute.");
  if (e && e.prepared) return note(e.prepLine);
  /* A failure is about what was being prepared; select something else and
     it no longer applies. */
  if (err && err.label === sc.label) return fault(err.message);
  if (st.server.stale)
    return {html: "Restart <span class='mono'>./serve.py</span> — the running server predates this page.", err: true};
  if (st.server.known && !st.server.prep)
    return note("Preparation unavailable: the server found no quickprep package.");
  return null;
}

/* One line saying what preparation changed, from quickprep's report. */
export function prepLine(r){
  const p = r.protonation || {}, w = r.water || {}, lp = r.loops || {}, f = r.refinement || {},
        bits = [], s = (n, one, many) => n === 1 ? one : many;
  bits.push("pH " + (p.method || "").replace(/.*pH /, ""));
  if (w.deleted) bits.push(w.deleted + " distant water" + s(w.deleted, "", "s") + " removed");
  if ((lp.built || []).length) bits.push(lp.built.length + " gap" + s(lp.built.length, "", "s") + " built");
  if ((lp.capped || []).length) bits.push(lp.capped.length + " capped");
  if ((r.missingAtoms || []).length) bits.push(r.missingAtoms.length + " side chains completed");
  Object.keys(p.nonDefault || {}).forEach(k => bits.push(k + " " + p.nonDefault[k].join(", ")));
  if ((p.flipped || []).length) bits.push(p.flipped.length + " flip" + s(p.flipped.length, "", "s"));
  if ((p.metalSites || []).length)
    bits.push(p.metalSites.length + " metal-site fix" + s(p.metalSites.length, "", "es"));
  if (f.rmsdA != null) bits.push("heavy-atom RMSD <b>" + f.rmsdA.toFixed(2) + " Å</b>");
  const nw = (r.warnings || []).length;
  if (nw) bits.unshift("<b>" + nw + " warning" + s(nw, "", "s") + "</b> in the report");
  return "Prepared in " + r.seconds + " s · " + bits.map(b => b.indexOf("<b>") < 0 ? esc(b) : b).join(" · ");
}

async function run(){
  const sc = scope(state);
  if (!sc.e || state.prep.busy) return;
  let st;
  try{ st = scopeStructure(sc); }
  catch(err){ state.prep.error = {label: sc.label, message: err.message}; return commit(); }
  const src = sc.e;
  state.prep = {busy: sc.label, error: null};
  state.busy = "preparing " + sc.label + "…";
  commit();
  let fit = false;
  const project = await ensureProject();
  try{
    const buf = await api.bufferStructure(st);
    const r = await api.prepare({token: buf.token, ph: 7.0, noRebuild: st.noRebuild, project});
    const rep = r.report || {}, out = rep.output || {};
    addEntity({id: freeId(src.id + "-prep"), data: r.data, fmt: "pdb", prepared: true,
      title: "prepared from " + sc.label,
      sub: "QuickPrep-style · pH 7 · " + (out.hydrogens || 0) + " H",
      prepLine: prepLine(rep),
      prepSummary: (r.summary || "").replace(/^input\s+\S+/m, "input      " + sc.label)},
      ENT_COLORS.length);
    /* Same frame and largely the same atoms: show the prepared copy in its place. */
    src.visible = false;
    state.picks = []; state.pickAnchor = null;
    fit = true;
  }catch(err){
    state.prep.error = {label: sc.label, message: err.message};
  }
  state.prep.busy = null; state.busy = null;
  commit({fit});
  touch();
}

export function bind(){
  $("prep-btn").onclick = run;
}
