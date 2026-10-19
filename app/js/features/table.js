/* The generated-molecule table under the stage: one row per molecule, any
   column sorts, a row click focuses that molecule in 3D, CSV downloads what
   is listed. The panel collapses and its grip resizes it. */

import {state, commit, liveRun, liveGen} from "../core/state.js";
import {$, esc} from "../core/dom.js";
import {save} from "../core/files.js";
import {rankColor} from "../core/colors.js";
import * as scene from "../scene.js";

const CSV_COLS = ["rank", "name", "formula", "mw", "heavy", "logp", "tpsa", "qed", "rings", "rotb", "smiles"];
/* Written into the SDF as data tags, so the poses carry what the table shows.
   `i` is in there too: it is the molecule's index in its pocket's SDF, which
   is how a row maps back to a record. */
const SDF_TAGS = ["rank", "i"].concat(CSV_COLS.slice(1));

function rows(st, gen){
  let out = gen.table.slice();
  if (st.table.only) out = out.filter(r => { const g = gen.groups[String(r.rank)]; return g && g.visible; });
  const k = st.table.sort.key, d = st.table.sort.dir;
  out.sort((a, b) => {
    const x = a[k], y = b[k];
    if (x === y) return (a.rank - b.rank) || (a.i - b.i);
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (typeof x === "number" && typeof y === "number")
      ? (x - y) * d : String(x).localeCompare(String(y)) * d;
  });
  return out;
}

const num = (v, dp) => v === null || v === undefined ? "&mdash;" : (dp === undefined ? v : (+v).toFixed(dp));
function rowHTML(r, run, sel){
  const on = sel && sel.rank === r.rank && sel.i === r.i;
  return "<tr data-rank='" + esc(String(r.rank)) + "' data-i='" + r.i + "'" +
    (on ? " aria-selected='true'" : "") + ">" +
    "<td><span class='depict'>" + (r.svg || "") + "</span></td>" +
    "<td><span class='pk'><i style='background:" + rankColor(run, r.rank) + "'></i>" + esc(String(r.rank)) + "</span></td>" +
    "<td class='mono'>" + esc(r.name || "") + "</td>" +
    "<td class='mono'>" + esc(r.formula || "") + "</td>" +
    "<td class='num mono'>" + num(r.mw, 1) + "</td>" +
    "<td class='num mono'>" + num(r.heavy) + "</td>" +
    "<td class='num mono'>" + num(r.logp, 2) + "</td>" +
    "<td class='num mono'>" + num(r.tpsa, 1) + "</td>" +
    "<td class='num mono'>" + num(r.qed, 3) + "</td>" +
    "<td class='num mono'>" + num(r.rings) + "</td>" +
    "<td class='num mono'>" + num(r.rotb) + "</td>" +
    "<td class='smi mono' title='" + esc(r.smiles || "") + "'>" + esc(r.smiles || "") + "</td>" +
    "</tr>";
}

/* ---------------- view ---------------- */
let built = null;   /* what the rows were last built from */
export function render(st){
  const gen = liveGen(), run = liveRun(), body = $("gt-body"),
        ok = !!(gen && gen.table && gen.table.length);
  $("gtab").hidden = !ok;
  if (!ok){ body.innerHTML = ""; $("gt-n").textContent = ""; built = null; return; }

  /* Rows carry depictions, so rebuilding them is the slow part: only when
     what they show changes. Selecting a row only moves the highlight. */
  const key = [gen.job, st.table.only, st.table.sort.key, st.table.sort.dir,
               run.pockets.map(p => p.rank).join(","),
               Object.keys(gen.groups).map(r => r + ":" + gen.groups[r].visible).join(";")].join("|");
  if (key !== built){
    const list = rows(st, gen);
    $("gt-n").textContent = list.length + " of " + gen.table.length;
    body.innerHTML = list.map(r => rowHTML(r, run, st.table.sel)).join("");
    built = key;
  }
  document.querySelectorAll("#gt th[data-sort]").forEach(th => {
    if (st.table.sort.key === th.dataset.sort)
      th.setAttribute("aria-sort", st.table.sort.dir === 1 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });
}

/* ---------------- actions ---------------- */
function select(tr){
  const gen = liveGen();
  if (!gen) return;
  const rank = tr.dataset.rank, i = +tr.dataset.i;
  state.table.sel = {rank: isNaN(+rank) ? rank : +rank, i};
  /* focus it in 3D, and make sure its group is showing */
  Object.keys(gen.groups).forEach(k => { gen.groups[k].focus = String(k) === String(rank) ? i : -1; });
  const g = gen.groups[String(rank)];
  if (g) g.visible = true;
  const was = document.querySelector("#gt-body tr[aria-selected=true]");
  if (was) was.removeAttribute("aria-selected");
  tr.setAttribute("aria-selected", "true");
  commit();
  scene.zoomMolecule(g && g.mols[i]);
}

function csv(){
  const gen = liveGen();
  if (!gen) return "";
  const out = ["pocket," + CSV_COLS.slice(1).join(",")];
  rows(state, gen).forEach(r => {
    out.push(CSV_COLS.map(c => {
      let v = r[c];
      v = (v === null || v === undefined) ? "" : String(v);
      return /[",]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    }).join(","));
  });
  return out.join("\n");
}

/* The molecules themselves: one SDF of the rows the table is showing, in the
   order it is showing them, each record carrying its properties as SD tags.
   The coordinates are the pose in the pocket, which is the whole point of
   generating into one and the one thing the CSV cannot hold. */
function sdf(){
  const gen = liveGen();
  if (!gen) return "";
  const out = [];
  rows(state, gen).forEach(r => {
    const group = gen.groups[String(r.rank)], block = group && group.mols[r.i];
    if (block) out.push(tagged(block, r));
  });
  return out.join("");
}

function tagged(block, row){
  const tags = SDF_TAGS.map(k => row[k] === null || row[k] === undefined
    ? "" : "> <" + k + ">\n" + row[k] + "\n\n").join("");
  return block.replace(/\$\$\$\$\s*$/, "") + tags + "$$$$\n";
}

function downloadCsv(){
  save("generated_molecules.csv", csv(), "text/csv");
}

function downloadSdf(){
  const text = sdf();
  if (text) save("generated_molecules.sdf", text, "chemical/x-mdl-sdfile");
}

export function bind(){
  $("gt-body").onclick = ev => { const tr = ev.target.closest("tr"); if (tr) select(tr); };
  document.querySelectorAll("#gt th[data-sort]").forEach(th => {
    th.onclick = () => {
      const s = state.table.sort, k = th.dataset.sort;
      if (s.key === k) s.dir = -s.dir; else { s.key = k; s.dir = 1; }
      commit();
    };
  });
  $("gt-only").onchange = ev => { state.table.only = ev.target.checked; commit(); };
  $("gt-csv").onclick = downloadCsv;
  $("gt-sdf").onclick = downloadSdf;
  bindPanel();
}

/* Collapse, and drag (or arrow keys on) the grip to resize. Height is a
   per-viewer convenience, kept in localStorage. */
function bindPanel(){
  const gtab = $("gtab"), gsc = $("gscroll"), gtog = $("gt-tog"), grip = $("gt-grip");
  const setHeight = h => {
    const max = Math.max(120, document.querySelector(".center").clientHeight - 230);
    h = Math.round(Math.max(90, Math.min(max, h)));
    gsc.style.height = h + "px";
    try{ localStorage.setItem("pv-gt-h", h); }catch(e){}
  };
  try{ const gh = +localStorage.getItem("pv-gt-h"); if (gh) setHeight(gh); }catch(e){}
  gtog.onclick = () => {
    const open = !gtab.classList.toggle("closed");
    gtog.setAttribute("aria-expanded", open);
    gtog.setAttribute("aria-label", open ? "Collapse the molecule table" : "Expand the molecule table");
    gtog.innerHTML = open ? "&#9662;" : "&#9652;";
  };
  grip.addEventListener("pointerdown", e => {
    const y0 = e.clientY, h0 = gsc.offsetHeight;
    grip.setPointerCapture(e.pointerId);
    const move = ev => setHeight(h0 - (ev.clientY - y0));
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    grip.addEventListener("pointercancel", up);
    e.preventDefault();
  });
  grip.addEventListener("keydown", e => {
    if (e.key === "ArrowUp"){ setHeight(gsc.offsetHeight + 30); e.preventDefault(); e.stopPropagation(); }
    if (e.key === "ArrowDown"){ setHeight(gsc.offsetHeight - 30); e.preventDefault(); e.stopPropagation(); }
  });
}
