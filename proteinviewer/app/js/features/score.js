/* Score poses: the button on the molecule table, the window of options behind
   it, and the job it starts.

   Scoring is a run of its own, asked for once the molecules are in the table —
   generation never waits on it, so a run whose scorer is missing or broken
   still has its molecules. When the scores come back they are joined onto the
   rows already on screen by (rank, i), and the table's own columns show them. */

import {state, commit, liveRun, liveGen} from "../core/state.js";
import {$, esc, setMsg, note, fault} from "../core/dom.js";
import * as api from "../core/api.js";

const POLL_MS = 1200;
let pollTimer = 0;

/* ---------------- the window ---------------- */
function dlg(){ return $("scoredlg"); }

function mode(){
  const on = document.querySelector("input[name=sd-mode]:checked");
  return on ? on.value : "minimize";
}

function ranksWanted(){
  const gen = liveGen();
  if (!gen) return [];
  const all = Object.keys(gen.groups);
  const on = document.querySelector("input[name=sd-ranks]:checked");
  const pick = on && on.value === "visible" ? all.filter(r => gen.groups[r].visible) : all;
  return pick.map(Number).filter(r => !isNaN(r));
}

/* The box padding only means anything to the mode that uses a box. */
function syncMode(){
  const boxed = mode() === "minimize";
  $("sd-pad-wrap").hidden = !boxed;
  if (!boxed) closeTip($("sd-tip-pad"));   /* its field is gone; so is its note */
  estimate();
}

function closeTip(tip){
  tip.hidden = true;
  const b = document.querySelector("button.info[aria-controls=" + tip.id + "]");
  if (b) b.setAttribute("aria-expanded", "false");
}

function estimate(){
  const gen = liveGen();
  if (!gen) return;
  const ranks = ranksWanted();
  const n = gen.table.filter(r => ranks.indexOf(+r.rank) !== -1).length;
  const secs = Math.max(1, Math.round(n * (mode() === "rescore" ? 0.5 : 2)));
  $("sd-est").textContent = n + " pose" + (n === 1 ? "" : "s") + " · about " +
    (secs < 90 ? secs + " s" : Math.round(secs / 60) + " min") + " on CPU";
}

function open(){
  const gen = liveGen(), run = liveRun();
  if (!gen || !run) return;
  const ranks = Object.keys(gen.groups).join(", ");
  $("sd-sub").textContent = gen.table.length + " molecules · pocket" +
    (Object.keys(gen.groups).length > 1 ? "s " : " ") + ranks +
    " · " + (state.server.scorers[0] || "no scorer");
  $("sd-flag").value = state.scoreTask.flag;
  syncMode();
  dlg().showModal();
}

/* ---------------- the run ---------------- */
async function go(){
  const gen = liveGen(), run = liveRun();
  if (!gen || !run) return;
  const ranks = ranksWanted();
  if (!ranks.length) return finish(fault("No pockets selected to score."));
  const flag = +$("sd-flag").value;
  state.scoreTask = {busy: true, msg: note("Scoring…"), flag: isNaN(flag) ? 2 : flag};
  commit();
  try{
    const r = await api.startScoring({job: gen.job, token: run.token, ranks,
                                      mode: mode(), padding: +$("sd-padding").value,
                                      project: state.project.id || ""});
    poll(r.job, gen.job, r.mode);
  }catch(err){
    finish(fault("Could not start scoring: " + err.message));
  }
}

function poll(id, forJob, mode){
  if (pollTimer) clearTimeout(pollTimer);
  const started = Date.now();
  const tick = async () => {
    let s;
    try{ s = await api.scoreJob(id); }
    catch(err){ return finish(fault("Lost the scoring job: " + err.message)); }
    if (s.state === "done") return done(s, forJob, mode);
    if (s.state === "error")
      return finish(fault("Scoring failed: " + (s.error || "unknown error")));
    const secs = Math.round((Date.now() - started) / 1000);
    state.scoreTask.msg = note("<b>" + esc(s.state || "scoring") + "</b> · " +
      (s.done || 0) + "/" + (s.total || "?") + " pockets" +
      (s.current ? " · pocket " + esc(String(s.current)) : "") + " · " + secs + " s");
    commit();
    pollTimer = setTimeout(tick, POLL_MS);
  };
  tick();
}

/* The scores onto the rows they belong to. A row is found by (rank, i): i is
   the molecule's place in its pocket's SDF, which is the order the scorer was
   handed and the order it answered in. */
export function merge(table, scores, scorers){
  let hit = 0;
  (table || []).forEach(row => {
    const recs = scores[String(row.rank)];
    const rec = recs && typeof row.i === "number" ? recs[row.i] : null;
    /* One scoring run owns the score columns: a row this run did not cover
       goes back to unscored rather than keeping a number from the last run,
       which may have been another mode or another tool. The server rewrites
       its own copy the same way. */
    if (!rec){
      ["score", "le", "rmsd_min", "scorer", "scoreError"].forEach(k => delete row[k]);
      return;
    }
    row.score = rec.score;
    row.le = rec.le;
    row.rmsd_min = rec.rmsd_min;
    row.scorer = (scorers[String(row.rank)] || {}).scorer || "";
    if (rec.error) row.scoreError = rec.error; else delete row.scoreError;
    if (rec.score !== null && rec.score !== undefined) hit++;
  });
  return hit;
}

function done(s, forJob, mode){
  const gen = liveGen();
  /* The scores belong to the run they were asked for: if another generation
     run replaced it meanwhile, they are not shown against the new one. */
  if (!gen || gen.job !== forJob)
    return finish(fault("The molecules these scores belong to are gone."));
  const scores = s.scores || {}, scorers = s.scorers || {};
  const scored = merge(gen.table, scores, scorers);
  gen.scored = (gen.scored || 0) + 1;      /* makes the table rebuild its rows */
  gen.scoreMeta = scorers;
  const tool = (scorers[Object.keys(scorers)[0]] || {}).scorer || "the scorer";
  const warnings = [];
  Object.keys(scorers).forEach(r => (scorers[r].warnings || []).forEach(w => {
    if (warnings.indexOf(w) === -1) warnings.push(w);
  }));
  const failed = gen.table.length - scored;
  finish(note("<b>Scored.</b> " + scored + " of " + gen.table.length + " poses, " +
    esc(tool) + ", " + esc(mode) + "." +
    (failed ? " " + failed + " could not be scored." : "") +
    (s.error ? " " + esc(s.error) + "." : "") +
    (warnings.length ? " " + esc(warnings.join(" ")) : "") +
    " Sort by Score or LE to rank them."));
}

function finish(msg){
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = 0;
  state.scoreTask.busy = false;
  state.scoreTask.msg = msg;
  commit();
}

/* ---------------- view ---------------- */
export function render(st){
  const btn = $("gt-score"), gen = liveGen();
  if (!btn) return;
  const can = !!gen && !!st.server.scorers.length && !st.scoreTask.busy;
  btn.disabled = !can;
  btn.textContent = st.scoreTask.busy ? "Scoring…" : "Score poses";
  setMsg($("gt-msg"), st.scoreTask.msg);
  btn.title = !gen ? "Generate molecules first"
    : !st.server.scorers.length
      ? "No pose scorer found on the server: install smina or gnina, set SMINA_BIN or " +
        "GNINA_BIN, and restart serve.py"
      : "Score each pose in the pocket it was generated for";
}

export function bind(){
  const d = dlg();
  $("gt-score").onclick = open;
  $("sd-cancel").onclick = () => d.close();
  /* The form's own submit closes the window; scoring starts from there, so
     Enter in a field does what the button does. */
  $("sd-form").addEventListener("submit", () => { go(); });
  document.querySelectorAll("input[name=sd-mode]").forEach(r => { r.onchange = syncMode; });
  document.querySelectorAll("input[name=sd-ranks]").forEach(r => { r.onchange = estimate; });
  /* Each note opens on click or keyboard, never on hover alone: a hover-only
     note cannot be reached on a touch screen or by a screen reader. */
  d.querySelectorAll("button.info").forEach(b => {
    b.onclick = () => {
      const tip = $(b.getAttribute("aria-controls")), on = tip.hidden;
      tip.hidden = !on;
      b.setAttribute("aria-expanded", String(on));
    };
  });
}
