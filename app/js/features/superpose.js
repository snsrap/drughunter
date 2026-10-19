/* Superpose: move structures into the frame of one reference, so that entries
   of the same protein -- apo and holo, a prediction and a crystal structure,
   homologues -- can be compared where they overlap.

   The reference stays where it is. Every structure ticked under it is fitted
   onto it by Cα atoms paired through a sequence alignment, and its own text
   is rewritten in the new frame. The fitting is pocketbench's, which the
   server runs (POST /api/superpose); this panel says what to fit on and
   keeps what comes back. That is a real edit, like a removal: what is drawn,
   downloaded, prepared and searched for pockets from then on is the
   superposed structure, and Undo puts it back where it was.

   A selection in the tree narrows the fit: picks in the reference (a chain,
   the residues of a site) fit on those residues only, and picks in a
   structure being moved restrict its side the same way. */

import {state, commit, entity, picksOf, frameOf, liveRun} from "../core/state.js";
import {$, esc, attr, setHTML, setMsg, note, plural} from "../core/dom.js";
import {entColor} from "../core/colors.js";
import * as api from "../core/api.js";
import {scopeAtoms, summarize} from "../model/scope.js";
import {remember, reload} from "./tree.js";

/* The structure the others move onto: the one chosen while it is loaded, else
   the one the live pocket run is on (moving that would clear the run), else
   the first loaded. */
function reference(){
  const run = liveRun();
  return entity(state.sup.ref) || (run && entity(run.ent)) || state.ents[0] || null;
}
/* Ticked by hand, or by default: every structure not already in the
   reference's frame. One that is -- a prepared copy, or one superposed
   before -- moves only if asked to, so pressing the button twice is not a
   second fit. */
function ticked(e, ref){
  const m = state.sup.move[e.uid];
  return m !== undefined ? m : frameOf(e) !== frameOf(ref);
}
function movers(ref){ return state.ents.filter(e => e !== ref && ticked(e, ref)); }

/* The picked nodes of one structure, or none if it is picked whole. */
function pickedNodes(e){
  const nodes = picksOf(e).map(k => e.nodes && e.nodes[k]).filter(Boolean);
  return nodes.some(n => n.kind === "root") ? [] : nodes;
}
/* The residues one side of the fit is restricted to, [chain, number,
   insertion code] -- none for the whole structure. A blank chain id is sent
   as "A", which is how the server's parser reads it. */
function fitOn(e){
  const nodes = pickedNodes(e), seen = {}, out = [];
  if (!nodes.length) return out;
  scopeAtoms({e, whole: false, nodes}).forEach(a => {
    const k = [String(a.chain || "").trim() || "A", a.resi, String(a.icode || "").trim()];
    if (!seen[k.join("|")]){ seen[k.join("|")] = true; out.push(k); }
  });
  return out;
}

/* ---------------- view ---------------- */
export function render(st){
  const mod = $("mod-sup");
  mod.hidden = st.ents.length < 2;
  if (mod.hidden) return;
  const ref = reference(), list = movers(ref), busy = st.sup.busy, sel = $("sup-ref");
  setHTML(sel, st.ents.map(e => "<option value='" + attr(e.uid) + "'>" + esc(e.id) + "</option>").join(""));
  sel.value = ref.uid;
  sel.disabled = busy;

  const same = st.ents.filter(e => frameOf(e) === frameOf(ref)).length;
  $("sup-n").textContent = same > 1 ? same + " of " + st.ents.length + " in one frame" : "";
  /* Ticking a row re-renders the list, so put focus back on the box it was on. */
  const rows = $("sup-list"), act = document.activeElement,
        was = rows.contains(act) && act.dataset.sup;
  if (setHTML(rows, st.ents.filter(e => e !== ref).map(e => rowHTML(e, ref, busy)).join("")) && was){
    const box = rows.querySelector("[data-sup='" + CSS.escape(was) + "']");
    if (box) box.focus();
  }

  const on = [ref].concat(list).filter(e => pickedNodes(e).length)
                  .map(e => "<b>" + esc(e.id) + "</b> · " + esc(summarize(pickedNodes(e)))),
        run = liveRun(), loses = run && list.find(e => e.uid === run.ent);
  setHTML($("sup-scope"), list.length
    ? "Fits Cα pairs matched by sequence" + (on.length ? ", on " + on.join("; ") : "") + "." +
      (loses ? " Moving <b>" + esc(loses.id) + "</b> clears its pockets." : "")
    : "Tick a structure to move onto <b>" + esc(ref.id) + "</b>.");

  const btn = $("sup-run");
  btn.textContent = busy ? "Superposing…"
    : ["Superpose", list.length === 1 ? list[0].id : list.length ? plural(list.length, "structure") : null,
       "onto", ref.id].filter(Boolean).join(" ");
  btn.disabled = busy || !list.length || st.server.known && !st.server.superpose;
  setMsg($("sup-msg"), message(st));
}

function message(st){
  const m = st.sup.msg;
  /* What a run reported holds while those fits stand: an Undo, or a later
     fit, takes it down. */
  if (m && (m.fits || []).every(([uid, fit]) => { const e = entity(uid); return e && e.sup === fit; }))
    return m;
  if (st.server.stale)
    return {html: "Restart <span class='mono'>./serve.py</span> — the running server predates this page.", err: true};
  if (st.server.known && !st.server.superpose)
    return note("Superposition unavailable: the server found no pocketbench package.");
  return null;
}

/* A structure that could move: its tick, its colour, and the last fit that
   put it in the reference's frame, if that is where it is. */
function rowHTML(e, ref, busy){
  const fit = frameOf(e) === frameOf(ref) && e.sup, same = frameOf(e) === frameOf(ref);
  let aside = "", tip = "";
  if (fit){
    aside = (e.sup.onto === ref.id ? "" : "onto " + e.sup.onto + " · ") + e.sup.rmsd.toFixed(2) + " Å · " + e.sup.n + " Cα";
    tip = "Fitted onto " + e.sup.onto + ": RMSD " + e.sup.rmsd.toFixed(2) + " Å over " + e.sup.n + " of " +
          e.sup.aligned + " aligned Cα, " + Math.round(e.sup.ident * 100) + " % identical";
  } else if (same){
    aside = "in its frame"; tip = e.id + " is already in " + ref.id + "'s frame";
  }
  return "<label class='chk'" + (tip ? " title='" + attr(tip) + "'" : "") + "><input type='checkbox' data-sup='" +
    attr(e.uid) + "'" + (ticked(e, ref) ? " checked" : "") + (busy ? " disabled" : "") + ">" +
    "<i class='sw' style='background:" + entColor(e) + "'></i><span class='mono'>" + esc(e.id) + "</span>" +
    (aside ? "<span class='aside'>" + esc(aside) + "</span>" : "") + "</label>";
}

/* ---------------- actions ---------------- */
/* Each structure as the server should hold it: buffered under its own name,
   as the other panels buffer a whole structure. */
function buffer(e){
  return api.bufferStructure({token: e.id, data: e.data, fmt: e.fmt});
}

async function run(){
  const ref = reference(), list = ref ? movers(ref) : [];
  if (!list.length || state.sup.busy) return;
  state.sup.busy = true; state.sup.msg = null;
  state.busy = "superposing…";
  commit();

  const moved = [], errors = [], low = [], refData = ref.data;
  try{
    const target = await buffer(ref), on = fitOn(ref);
    for (const e of list){
      const sent = e.data;
      let r;
      try{
        const buf = await buffer(e);
        r = await api.superpose({token: buf.token, reference: target.token, on, mobileOn: fitOn(e)});
      }catch(err){ errors.push(e.id + ": " + err.message); continue; }
      /* Each answer takes a moment; something removed or edited meanwhile,
         or a reference that changed under the fit, keeps what it has now. */
      if (state.ents.indexOf(e) === -1 || e.data !== sent || ref.data !== refData){
        errors.push(e.id + ": it changed while it was being superposed — try again");
        continue;
      }
      const fit = r.fit;
      moved.push({e, prev: {data: e.data, frame: e.frame, sup: e.sup}});
      e.data = r.data;
      e.frame = frameOf(ref);
      e.sup = {onto: ref.id, rmsd: fit.rmsd, n: fit.n, aligned: fit.aligned, ident: fit.ident};
      /* The numbers alone would not say that the pairing they rest on is chance. */
      if (!fit.related) low.push(e.id + " (" + Math.round(fit.ident * 100) + " % over " + fit.paired + ")");
    }
  }catch(err){
    errors.push(ref.id + ": " + err.message);    /* the reference would not buffer */
  }
  if (moved.length){
    const one = moved.length === 1;
    remember({kind: "edits", items: moved,
              what: (one ? moved[0].e.id : plural(moved.length, "structure")) + " where " + (one ? "it was" : "they were")});
  }
  state.sup.busy = false; state.busy = null;
  /* Those just moved are in the reference's frame now, so the defaults
     untick them; a choice made by hand has done its job. */
  state.sup.move = {};
  state.sup.msg = report(ref, moved, errors, low);
  state.sup.msg.fits = moved.map(x => [x.e.uid, x.e.sup]);
  reload(moved.map(x => x.e), {fit: moved.length > 0});
}

function report(ref, moved, errors, low){
  const parts = [];
  if (moved.length === 1){
    const s = moved[0].e.sup;
    parts.push("Superposed <b>" + esc(moved[0].e.id) + "</b> onto <b>" + esc(ref.id) + "</b>: RMSD <b>" +
               s.rmsd.toFixed(2) + " Å</b> over " + s.n + " of " + s.aligned + " Cα, " +
               Math.round(s.ident * 100) + " % identical");
  } else if (moved.length){
    parts.push("Superposed <b>" + moved.length + "</b> structures onto <b>" + esc(ref.id) + "</b>");
  }
  if (low.length) parts.push("identity too low to show " + esc(low.join(", ")) +
                             " related — the residue pairing may be chance");
  if (errors.length) parts.push(esc(errors.join(" · ")));
  return {html: parts.join(" · "), err: !!errors.length};
}

export function bind(){
  $("sup-ref").onchange = ev => {
    state.sup.ref = ev.target.value;
    state.sup.move = {};
    state.sup.msg = null;
    commit();
  };
  $("sup-list").onchange = ev => {
    const uid = ev.target.dataset && ev.target.dataset.sup;
    if (!uid) return;
    state.sup.move[uid] = ev.target.checked;
    commit();
  };
  $("sup-run").onclick = run;
}
