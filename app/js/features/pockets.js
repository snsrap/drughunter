/* Find pockets: run a detector on the scoped structure, list what comes back,
   and light up the residues of the ticked pockets.

   Detectors are command line programs, so serve.py runs them. The scoped
   structure is posted once and buffered server-side; runs read it from that
   copy rather than re-posting coordinates. Pockets come back resolved to
   whole residues around the detector's site points -- the same cut
   NEAT-POCKET conditions on. */

import {state, commit, liveRun, clearRun} from "../core/state.js";
import {$, esc, setHTML, setMsg, note, fault} from "../core/dom.js";
import {pocketColor} from "../core/colors.js";
import * as api from "../core/api.js";
import {scope, scopeStructure} from "../model/scope.js";
import * as scene from "../scene.js";

/* Twenty is lacuna's measured sweet spot: more conformers find more sites
   and surface none of them. */
const CONFORMERS = 20;
const CUTOFF = 6.0;

/* The select's value is "method" or "method:backend": lacuna's two backends
   differ in what they can open, so the backend travels with the method. */
function parseMethod(value){
  const parts = (value || "fpocket").split(":");
  return {method: parts[0], backend: parts[1] || "nma"};
}

/* true / false / null while the status probe is still out. */
function toolReady(st){
  if (!st.server.known) return null;
  if (st.method.method === "lacuna") return st.server.lacuna.indexOf(st.method.backend) !== -1;
  return st.server.fpocket;
}
function unavailable(st){
  if (st.method.method !== "lacuna")
    return "fpocket not found on the server — pocket detection unavailable.";
  if (!st.server.lacuna.length)
    return "lacuna not found on the server. Install it in its own environment " +
           "(pip install lacuna-pockets) or set LACUNA_BIN, then restart serve.py.";
  return "lacuna's MD backend needs OpenMM and PDBFixer in its environment. " +
         "Available here: " + st.server.lacuna.join(", ") + ".";
}
function rankedBy(meta){
  return meta && meta.tool === "lacuna"
    ? " · ranked by lacuna over " + meta.conformers + " conformers"
    : " · ranked by fpocket score";
}

/* ---------------- view ---------------- */
export function render(st){
  const sc = scope(st), run = liveRun(), btn = $("find-pockets");
  btn.textContent = sc.e ? "Find pockets in " + (sc.whole ? sc.e.id : "selection") : "Find pockets";
  btn.disabled = !sc.e || toolReady(st) === false || !!st.pocketTask.busy;
  setMsg($("pk-msg"), message(st, sc, run));

  const pockets = run ? run.pockets : [];
  $("pk-sph-wrap").hidden = !pockets.length;
  $("c-spheres").checked = st.spheres;
  $("pk-runlog").hidden = !(pockets.length && run.meta);
  $("pk-n").textContent = pockets.length ? pockets.length + " found" : "";
  setHTML($("pk-list"), pockets.map((p, i) => pocketRow(p, i, run.picks.indexOf(i) !== -1)).join(""));
  setHTML($("pk-run"), pockets.length && run.meta ? runLine(run.meta) : "");
}

function message(st, sc, run){
  const task = st.pocketTask;
  if (task.busy) return note(task.busy);
  if (run){
    return run.pockets.length
      ? note("<b>" + run.pockets.length + "</b> pockets in " + esc(run.label) +
             rankedBy(run.meta) + (run.cached ? " · cached" : ""))
      : note("No pockets found in " + esc(run.label) + ".");
  }
  /* A failure is about what was searched; select something else and it no
     longer applies. */
  if (task.error && sc.e && task.error.label === sc.label) return fault(task.error.message);
  if (!sc.e) return st.ents.length ? null : note(esc(sc.err));
  if (st.server.stale)
    return {html: "Restart <span class='mono'>./serve.py</span> — the running server predates this page.", err: true};
  if (st.server.error) return fault("Could not reach the server: " + st.server.error);
  if (toolReady(st) === false) return fault(unavailable(st));
  return null;
}

function pocketRow(p, i, on){
  const chains = p.sel.map(g => g.chain).join("/");
  return "<button type='button' class='opt' role='checkbox' data-pk='" + i +
    "' aria-checked='" + on + "'><span class='dot' style='" +
    (on ? "background:" + pocketColor(i) + ";border-color:" + pocketColor(i) : "") +
    "'></span><span>Pocket " + p.rank + "<small>" + p.nResidues +
    " residues · chain " + esc(chains || "?") +
    /* fpocket reports a score; lacuna ranks with a fitted model and reports
       persistence instead -- the fraction of conformers the pocket is open
       in, which is the number that says whether a high rank is worth
       trusting. Either field can be null, so neither is formatted blind. */
    (p.score != null
      ? " · " + p.nSpheres + " spheres · score " + p.score.toFixed(2)
      : (p.persistence != null
          ? " · open in " + Math.round(p.persistence * 100) + "% of conformers"
          : "") +
        (p.crypticity != null ? " · crypticity " + p.crypticity.toFixed(2) : "")) +
    "</small></span><span class='tag'>" +
    (p.volume != null ? Math.round(p.volume) + " Å³" : "—") +
    "</span></button>";
}

/* Say plainly what was executed, so the numbers in the list are traceable. */
function runLine(m){
  return "Ran <b>" + (m.tool === "lacuna"
      ? "lacuna</b> (" + esc(m.backend) + " backend, " + m.conformers +
        " conformers, " + m.nCryptic + " cryptic)"
      : "fpocket</b>") + " on " + m.receptorAtoms + " receptor atoms" +
    " (of " + m.totalAtoms + "; waters and additives dropped, hetero written as ATOM)" +
    (m.ligandRemoved && m.ligandRemoved.length
      ? ", ligand " + esc(m.ligandRemoved.join(", ")) + " deleted" : "") +
    " &rarr; " + m.nPockets + " pockets in " + m.elapsed + " s." +
    " Residues = whole standard residues within " + m.cutoff + " Å of " +
    (m.tool === "lacuna" ? "a pocket pseudoatom." : "an alpha-sphere centre.") +
    /* Measured on this machine: three MD runs over one input ranked the
       same site 9th, 7th and 10th. Saying so beside the ranks stops a
       single run being read as a measurement. */
    (m.tool === "lacuna" && m.backend === "openmm"
      ? " <b>MD is not bit-reproducible — ranks shift between runs; repeat" +
        " before trusting one.</b>" : "") +
    "<br><span class='mono' style='word-break:break-all'>$ " + esc(m.command) + "</span>";
}

/* ---------------- actions ---------------- */
async function find(){
  const sc = scope(state), sel = state.method;
  if (!sc.e || toolReady(state) === false || state.pocketTask.busy) return;
  let st;
  try{ st = scopeStructure(sc); }
  catch(err){ state.pocketTask.error = {label: sc.label, message: err.message}; return commit(); }
  /* The MD backend runs a short simulation before it detects anything, so it
     takes tens of seconds where the others take one or two. Say so, rather
     than leaving the panel looking hung. */
  state.pocketTask = {error: null, busy: "Detecting pockets in <b>" + esc(sc.label) + "</b>" +
    (sel.method === "lacuna"
      ? " over a " + esc(sel.backend) + " ensemble" +
        (sel.backend === "openmm" ? " — this runs MD and takes a minute" : "")
      : "") + "…"};
  commit();
  try{
    const buf = await api.bufferStructure(st);
    const body = {token: buf.token, cutoff: CUTOFF, method: sel.method};
    if (sel.method === "lacuna"){ body.backend = sel.backend; body.conformers = CONFORMERS; }
    const r = await api.findPockets(body);
    const pockets = r.pockets || [];
    state.run = {id: sc.e.uid + "#" + (++state.runSerial), ent: sc.e.uid, token: buf.token,
                 label: sc.label, pockets, meta: r.meta || null, cached: !!r.cached,
                 picks: pockets.length ? [0] : []};
  }catch(err){
    clearRun();
    state.pocketTask.error = {label: sc.label, message: "Pocket detection failed: " + err.message};
  }
  state.pocketTask.busy = null;
  commit();
  scene.zoomPocket();
}

/* Selection is multi: generation runs on every pocket ticked here. */
function toggle(i){
  const run = liveRun();
  if (!run) return;
  const at = run.picks.indexOf(i);
  if (at === -1) run.picks.push(i); else run.picks.splice(at, 1);
  run.picks.sort((a, b) => a - b);
  commit();
  scene.zoomPocket();
}

export function bind(){
  const method = $("pk-method");
  state.method = parseMethod(method.value);
  /* Switching detector changes what is available and how results are
     described, so the panel re-renders rather than waiting for the next run. */
  method.onchange = () => { state.method = parseMethod(method.value); commit(); };
  $("find-pockets").onclick = find;
  $("c-spheres").onchange = ev => { state.spheres = ev.target.checked; commit(); };
  $("pk-list").onclick = ev => {
    const b = ev.target.closest("[data-pk]");
    if (b) toggle(+b.dataset.pk);
  };
}
