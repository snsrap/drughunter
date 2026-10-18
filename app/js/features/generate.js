/* Generate binders: NEAT-POCKET on the ticked pockets of the live run.

   Generation takes tens of seconds per pocket, so the server runs it as a
   job and the page polls. Results are grouped by the pocket they were
   generated for, each group drawn in that pocket's colour. */

import {state, commit, liveRun, liveGen} from "../core/state.js";
import {$, esc, setHTML, setMsg, note, fault} from "../core/dom.js";
import {rankColor} from "../core/colors.js";
import * as api from "../core/api.js";

const POLL_MS = 1500;
let pollTimer = null;

/* ---------------- view ---------------- */
export function render(st){
  const run = liveRun(), gen = liveGen();
  $("gen-wrap").hidden = !(run && run.pockets.length);
  $("gen-run").disabled = st.genTask.busy;
  setMsg($("gen-msg"), st.genTask.msg);
  $("lab-genshow").textContent = st.genShow;

  $("mod-gen").hidden = !gen;
  $("gen-runlog").hidden = !(gen && gen.meta);
  if (!gen){
    setHTML($("gen-list"), ""); $("gen-count").textContent = ""; setHTML($("gen-meta"), "");
    return;
  }
  const ranks = Object.keys(gen.groups);
  $("gen-count").textContent = ranks.reduce((a, r) => a + gen.groups[r].mols.length, 0) + " molecules";
  setHTML($("gen-list"), ranks.map(rank => {
    const g = gen.groups[rank], col = rankColor(run, rank);
    return "<button type='button' class='opt' role='checkbox' data-gen='" + esc(rank) +
      "' aria-checked='" + g.visible + "'><span class='dot' style='" +
      (g.visible ? "background:" + col + ";border-color:" + col : "") +
      "'></span><span>Pocket " + esc(rank) + "<small>" + g.mols.length +
      " molecules" + (g.seconds ? " · " + g.seconds + " s" : "") +
      "</small></span><span class='tag'>" + g.mols.length + "</span></button>";
  }).join(""));
  setHTML($("gen-meta"), gen.meta || "");
}

/* ---------------- actions ---------------- */
function finish(msg){
  state.genTask = {busy: false, msg};
  commit();
}

async function run(){
  const live = liveRun();
  if (!live || !live.picks.length) return finish(fault("Tick at least one pocket above first."));
  if (state.server.known && !state.server.neat)
    return finish(fault("NEAT-POCKET is not reachable from the server. Set NEAT_POCKET_DIR " +
                        "and NEAT_PYTHON, then restart serve.py."));
  const ranks = live.picks.map(i => live.pockets[i].rank),
        n = Math.max(1, Math.min(500, +$("gen-n").value || 25));
  state.genTask = {busy: true, msg: note("Generating " + n + " molecules for pocket" +
    (ranks.length > 1 ? "s " : " ") + ranks.join(", ") + "…")};
  commit();
  try{
    const r = await api.startGeneration({token: live.token, ranks, n, batch_size: Math.min(n, 50)});
    poll(r.job, live.id, ranks.length * n);
  }catch(err){
    finish(fault("Could not start generation: " + err.message));
  }
}

/* Poll until the job is done or fails. The molecules belong to the run they
   were generated for: if another pocket run replaced it meanwhile, they are
   kept for that run rather than shown against the new one. */
function poll(id, forRun, expected){
  if (pollTimer) clearTimeout(pollTimer);
  const started = Date.now();
  const tick = async () => {
    let s;
    try{ s = await api.job(id); }
    catch(err){ return finish(fault("Lost the job: " + err.message)); }
    if (s.state === "done") return load(id, forRun);
    if (s.state === "error") return finish(fault("Generation failed: " + (s.error || "unknown error")));
    const secs = Math.round((Date.now() - started) / 1000);
    state.genTask.msg = note("<b>" + esc(s.state || "running") + "</b> · " + (s.done || 0) + "/" +
      (s.total || expected) + " molecules" + (s.current ? " · pocket " + s.current : "") +
      " · " + secs + " s");
    commit();
    pollTimer = setTimeout(tick, POLL_MS);
  };
  tick();
}

async function load(id, forRun){
  let s;
  try{ s = await api.job(id, {molecules: true, table: true}); }
  catch(err){ return finish(fault("Could not read results: " + err.message)); }
  const mols = s.molecules || {}, groups = {}, results = s.results || [];
  results.forEach(r => {
    /* One model per record so each molecule can be styled on its own. */
    const blocks = (mols[String(r.rank)] || "").split(/\$\$\$\$\s*\r?\n/)
      .filter(b => b.trim().length).map(b => b + "$$$$\n");
    groups[String(r.rank)] = {mols: blocks, visible: true, focus: -1,
                              seconds: r.seconds, written: r.written};
  });
  const totalSecs = results.reduce((a, r) => a + (r.seconds || 0), 0);
  state.gen = {job: id, forRun, groups, table: s.table || [],
    meta: "NEAT-POCKET generated " +
      results.map(r => r.written + "/" + r.requested + " for pocket " + r.rank +
                       " (" + r.pocketResidues + " residues, " + r.pocketAtoms + " atoms)").join("; ") +
      " in " + totalSecs.toFixed(1) + " s. " +
      "Pockets were passed as residue selections; no reference ligand was used."};
  state.table.sel = null;
  const ng = Object.keys(groups).length;
  finish(note("<b>Done.</b> " + ng + " group" + (ng > 1 ? "s" : "") + " below."));
}

export function bind(){
  $("gen-run").onclick = run;
  $("gen-show").oninput = ev => { state.genShow = +ev.target.value; commit(); };
  $("gen-list").onclick = ev => {
    const b = ev.target.closest("[data-gen]"), gen = liveGen();
    if (!b || !gen) return;
    const g = gen.groups[b.dataset.gen];
    g.visible = !g.visible;
    commit();
  };
}
