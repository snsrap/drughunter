/* The Receptor panel's structure tree: selecting, folding, hiding, removing
   and undoing, plus the line saying what the operations will run on.

   Selection works as in a file manager: click picks one row (again: none),
   Ctrl/Cmd-click adds or drops a row, Shift-click takes every row between
   the last pick and this one. */

import {state, commit, nodeOf, clearRun} from "../core/state.js";
import {$, esc, attr, setHTML} from "../core/dom.js";
import {entColor} from "../core/colors.js";
import {hider} from "../model/tree.js";
import {leafSels, pdbSubset, cifSubset} from "../model/subset.js";
import {scope} from "../model/scope.js";
import * as scene from "../scene.js";

const UNDO_DEPTH = 20;

/* ---------------- view ---------------- */
const EYE = "<svg viewBox='0 0 16 16' aria-hidden='true'><path d='M1.2 8S3.7 3.4 8 3.4 14.8 8 14.8 8 12.3 12.6 8 12.6 1.2 8 1.2 8z' " +
            "fill='none' stroke='currentColor' stroke-width='1.3'/><circle cx='8' cy='8' r='2.1' fill='currentColor'/></svg>",
      EYE_OFF = "<svg viewBox='0 0 16 16' aria-hidden='true'><path d='M1.2 8S3.7 3.4 8 3.4 14.8 8 14.8 8 12.3 12.6 8 12.6 1.2 8 1.2 8z' " +
            "fill='none' stroke='currentColor' stroke-width='1.3' opacity='.55'/><path d='M2.5 13.5l11-11' stroke='currentColor' stroke-width='1.4'/></svg>";

/* Every row on show, in display order: [entity, node, depth]. */
function rows(){
  const out = [];
  state.ents.forEach(e => {
    if (!e.tree) return;
    (function walk(n, depth){
      out.push([e, n, depth]);
      if (n.kids && e.open[n.key]) n.kids.forEach(k => walk(k, depth + 1));
    })(e.tree, 0);
  });
  return out;
}

function rowHTML(e, n, depth){
  const folder = !!n.kids, open = folder && !!e.open[n.key],
        on = state.picks.indexOf(n.key) !== -1, root = n.kind === "root",
        label = root ? e.id : n.name, by = hider(e, n), own = by === n,
        eyeTip = by && !own ? "Hidden with " + (by.kind === "root" ? e.id : by.name)
                            : (own ? "Show " : "Hide ") + label;
  const name = root
    ? "<i class='sw' style='background:" + entColor(e) + "'></i><span class='nm fd'>" + esc(e.id) +
      "</span><span class='as'>" + (e.edited ? "edited · " : "") + esc(e.title || e.sub || "") + "</span>"
    : "<span class='nm" + (folder ? " fd" : "") + "'>" + esc(n.name) + "</span>" +
      (n.kind === "lig" || n.kind === "water" ? "<span class='as'>chain " + esc(n.chain || "?") + "</span>" : "");
  return "<div class='tn" + (root ? " root" : "") + (by ? " off" : "") +
    "' role='treeitem' aria-level='" + (depth + 1) + "' data-key='" + attr(n.key) + "'" +
    (folder ? " aria-expanded='" + open + "'" : "") + " aria-selected='" + on + "' style='--d:" + depth + "'>" +
    "<button type='button' class='tw" + (folder ? "" : " leaf") + "' tabindex='-1'" +
    (folder ? " aria-label='" + (open ? "Collapse " : "Expand ") + attr(label) + "'" : " aria-hidden='true'") + ">" +
    (folder ? (open ? "&#9662;" : "&#9656;") : "") + "</button>" +
    "<button type='button' class='tl'>" + name +
    (n.count != null ? "<span class='ct'>" + esc(String(n.count)) + "</span>" : "") + "</button>" +
    /* Below the structure row the buttons stay out of the tab order: the
       H and Delete keys do the same from the row itself. */
    "<button type='button' class='ib eye" + (own ? " on" : "") + (by && !own ? " inh" : "") + "'" +
      (root ? "" : " tabindex='-1'") + " aria-pressed='" + !by + "' title='" + attr(eyeTip) +
      "' aria-label='" + attr(eyeTip) + "'>" + (by ? EYE_OFF : EYE) + "</button>" +
    "<button type='button' class='ib rm'" + (root ? "" : " tabindex='-1'") + " title='Remove " +
      attr(label) + "' aria-label='Remove " + attr(label) + "'>&times;</button>" +
    "</div>";
}

export function render(st){
  const host = $("tree");
  host.hidden = !st.ents.length;
  /* Re-rendering replaces the rows, so put focus back where it was. */
  const act = document.activeElement, row = host.contains(act) && act.closest("[data-key]"),
        part = row && ["tw", "tl", "eye", "rm"].find(c => act.classList.contains(c));
  if (setHTML(host, rows().map(r => rowHTML(...r)).join("")) && row)
    focusRow(row.dataset.key, part ? "." + part : ".tl");

  const sc = scope(st), box = $("scope");
  box.hidden = !st.ents.length;
  box.className = sc.e || sc.soft ? "scope" : "scope err";
  setHTML(box, sc.e ? "Prepare and Find pockets run on <b>" + esc(sc.label) + "</b>" : esc(sc.err || ""));
}

export function focusRow(key, part){
  const el = document.querySelector("#tree [data-key='" + CSS.escape(key) + "'] " + (part || ".tl"));
  if (el) el.focus();
}

/* ---------------- actions ---------------- */
function pick(x, mode){
  const k = x.n.key;
  if (mode === "toggle"){
    const at = state.picks.indexOf(k);
    if (at === -1) state.picks.push(k); else state.picks.splice(at, 1);
    state.pickAnchor = k;
  } else if (mode === "range" && state.pickAnchor){
    const keys = rows().map(r => r[1].key),
          a = keys.indexOf(state.pickAnchor), b = keys.indexOf(k);
    if (a === -1){ state.picks = [k]; state.pickAnchor = k; }
    else state.picks = keys.slice(Math.min(a, b), Math.max(a, b) + 1);
  } else {
    state.picks = (state.picks.length === 1 && state.picks[0] === k) ? [] : [k];
    state.pickAnchor = k;
  }
  commit();
  if (state.picks.length) scene.zoomPicks();
  focusRow(k);
}

export function clearPicks(){
  if (!state.picks.length) return;
  state.picks = []; state.pickAnchor = null;
  commit();
}

function fold(x, open, part){
  if (!x.n.kids) return;
  x.e.open[x.n.key] = open;
  commit();
  focusRow(x.n.key, part);
}

/* Show or hide one node. A node hidden with a folder above it waits for that
   folder: showing it alone would break what the folder's eye says. */
function toggleHidden(x){
  const e = x.e, n = x.n, by = hider(e, n);
  if (by && by !== n) return;
  if (n.kind === "root") e.visible = !e.visible;
  else e.hidden[n.key] = !e.hidden[n.key];
  commit();
  focusRow(n.key, ".eye");
}

function remember(h){
  state.undo.push(h);
  if (state.undo.length > UNDO_DEPTH) state.undo.shift();
}

function forget(e){
  state.picks = state.picks.filter(k => k.indexOf(e.uid + "/") !== 0);
  if (state.pickAnchor && state.pickAnchor.indexOf(e.uid + "/") === 0) state.pickAnchor = null;
  if (state.run && state.run.ent === e.uid) clearRun();
}

function removeEntity(e){
  const at = state.ents.indexOf(e);
  state.ents.splice(at, 1);
  remember({kind: "structure", e, at, what: e.id});
  forget(e);
  commit();
}

/* Remove part of a structure. The atoms go from the entry's own text, not just
   from the view, so Prepare, Find pockets and the rest work on what is left. */
function removeNode(e, n){
  if (n.kind === "root"){ removeEntity(e); return; }
  const gone = {};
  e.model.selectedAtoms({or: leafSels(n.sel)}).forEach(a => { gone[a.index] = true; });
  const keep = e.model.selectedAtoms({}).filter(a => !gone[a.index]);
  if (!keep.length){ removeEntity(e); return; }
  remember({kind: "part", e, what: n.name + " from " + e.id,
            data: e.data, edited: !!e.edited, open: e.open, hidden: e.hidden});
  e.data = e.fmt === "cif" ? cifSubset(e.data, keep) : pdbSubset(e.data, keep);
  e.edited = true;
  reload(e);
  focusRow(n.parent.key);
}

/* Parse an entry again after its text changed. Folding, hiding and picks
   carry over wherever their node still exists; pocket results described the
   atoms as they were, so they go. */
function reload(e){
  e.tree = null; e.rev = (e.rev || 0) + 1;
  if (state.run && state.run.ent === e.uid) clearRun();
  scene.ensureModels();
  state.picks = state.picks.filter(k => nodeOf(k));
  if (state.pickAnchor && !nodeOf(state.pickAnchor)) state.pickAnchor = null;
  commit();
}

export function undo(){
  const h = state.undo.pop();
  if (!h) return;
  if (h.kind === "structure"){
    state.ents.splice(Math.min(h.at, state.ents.length), 0, h.e);
    commit();
  } else if (state.ents.indexOf(h.e) !== -1){
    Object.assign(h.e, {data: h.data, edited: h.edited, open: h.open, hidden: h.hidden});
    reload(h.e);
  } else {
    commit();   /* its structure is gone; the Undo link still has to update */
  }
}

/* ---------------- wiring ---------------- */
export function bind(){
  const tree = $("tree");
  $("sel-clear").onclick = clearPicks;
  $("undo").onclick = undo;
  /* the caret folds, the row picks, the eye shows or hides, the cross removes */
  tree.onclick = ev => {
    const row = ev.target.closest("[data-key]"), x = row && nodeOf(row.dataset.key);
    if (!x) return;
    if (ev.target.closest(".rm")) return removeNode(x.e, x.n);
    if (ev.target.closest(".eye")) return toggleHidden(x);
    if (ev.target.closest(".tw")) return fold(x, !x.e.open[x.n.key], ".tw");
    if (ev.target.closest(".tl"))
      pick(x, ev.shiftKey ? "range" : (ev.ctrlKey || ev.metaKey) ? "toggle" : "one");
  };
  /* Keys as in a file tree: up and down move, right and left unfold and fold,
     Escape clears the selection, H hides, Delete removes, Ctrl/Cmd-Z undoes. */
  tree.onkeydown = ev => {
    if (ev.key === "Escape"){ clearPicks(); ev.preventDefault(); return; }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "z" || ev.key === "Z")){ undo(); ev.preventDefault(); return; }
    const row = ev.target.closest("[data-key]"), x = row && nodeOf(row.dataset.key);
    if (!x) return;
    if (ev.key === "Delete" || ev.key === "Backspace"){ removeNode(x.e, x.n); ev.preventDefault(); return; }
    if ((ev.key === "h" || ev.key === "H") && !ev.ctrlKey && !ev.metaKey){ toggleHidden(x); ev.preventDefault(); return; }
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp"){
      const to = ev.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling;
      if (to) to.querySelector(".tl").focus();
      ev.preventDefault();
      return;
    }
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    const want = ev.key === "ArrowRight";
    if (!x.n.kids || !!x.e.open[x.n.key] === want) return;
    fold(x, want);
    ev.preventDefault();
  };
}
