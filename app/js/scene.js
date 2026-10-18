/* The 3D stage: everything 3Dmol draws, and the camera moves.

   The scene is drawn in layers, each rebuilt only when something it depends
   on changes. A structure is parsed once, when it is loaded (or its text
   changes); showing, hiding, restyling and picking work on that model in
   place. render() is a view like any other; the zoom functions are camera
   moves an action asks for after committing. */

import {state, entity, nodeOf, picksOf, liveRun, liveGen} from "./core/state.js";
import {cssVar, entColor, pocketColor, rankColor} from "./core/colors.js";
import {buildTree, hider, WATERS} from "./model/tree.js";
import {leafSels, inModel} from "./model/subset.js";

let viewer = null;

export function init(el){
  viewer = $3Dmol.createViewer(el, {backgroundColor: cssVar("--viewer")});
  if ($3Dmol.setSyncSurface) { try{ $3Dmol.setSyncSurface(true); }catch(e){} }
}
export function setBackground(){ if (viewer) viewer.setBackgroundColor(cssVar("--viewer")); }
export function resize(){ if (viewer){ viewer.resize(); viewer.render(); } }

function scheme(hex){
  const base = $3Dmol.elementColors.defaultColors, m = {};
  for (const k in base) m[k] = base[k];
  m.C = parseInt(hex.replace("#", ""), 16);
  return {prop: "elem", map: m};
}

/* ---------------- layers ---------------- */
const entLayers = {};
const layers = {spheres: {key: null}, gen: {key: null}, labels: {key: null}};
function clearLayer(L){
  (L.models || []).forEach(m => viewer.removeModel(m));
  (L.shapes || []).forEach(s => viewer.removeShape(s));
  (L.labels || []).forEach(l => viewer.removeLabel(l));
  (L.surfs || []).forEach(id => { try{ viewer.removeSurface(id); }catch(e){} });
  L.models = []; L.shapes = []; L.labels = []; L.surfs = [];
}
/* Rebuild a layer when its key changes; a null key always rebuilds. Returns
   whether it did. */
function layer(L, k, build){
  if (k !== null && L.key === k) return false;
  clearLayer(L); L.key = k; build(L);
  return true;
}

/* Parse every structure not parsed yet, or whose text changed (rev moves on),
   and build its tree from what 3Dmol read. Actions that need the new tree
   before committing -- a removal re-reads picks against it -- call this.
   Returns whether anything was added or removed. */
export function ensureModels(){
  if (!viewer) return false;
  let changed = false;
  Object.keys(entLayers).forEach(uid => {
    if (!entity(uid)){ clearLayer(entLayers[uid]); delete entLayers[uid]; changed = true; }
  });
  state.ents.forEach(e => {
    const L = entLayers[e.uid] || (entLayers[e.uid] = {key: null});
    changed = layer(L, e.uid + "#" + (e.rev || 0), L => {
      e.model = viewer.addModel(e.data, e.fmt); L.models.push(e.model);
      e.styled = null; e.shown = null;
      if (!e.tree) buildTree(e, e.model.selectedAtoms({}));
    }) || changed;
  });
  return changed;
}

/* --viewer differs per theme, so it stands in for every theme colour. */
function styleKey(e){
  const run = liveRun();
  return [cssVar("--viewer"), entColor(e),
          run && e.uid === run.ent ? run.id + ":" + run.picks.join(",") : "",
          picksOf(e).join(","),
          Object.keys(e.hidden || {}).filter(k => e.hidden[k]).join(",")].join("|");
}
function sphereKey(){
  const run = state.run, pe = run && entity(run.ent);
  return [run ? run.id : null, run ? run.picks.join(",") : "", state.spheres,
          !!(pe && pe.visible)].join("|");
}
function genKey(){
  const gen = state.gen;
  if (!gen || !gen.groups) return "";
  const run = liveRun(), pe = run && entity(run.ent);
  return [cssVar("--viewer"), gen.job, gen.forRun, run ? run.id : null, state.genShow,
          !!(pe && pe.visible), run ? run.pockets.map(p => p.rank).join(",") : "",
          Object.keys(gen.groups).map(r => {
            const g = gen.groups[r]; return r + ":" + g.visible + ":" + g.focus;
          }).join(";")].join("|");
}
/* Picked nodes that get a label: past a dozen, labels hide the structure. */
function labelled(){
  return state.picks.map(nodeOf).filter(x =>
    x && x.n.label && x.e.model && !hider(x.e, x.n)).slice(0, 12);
}
function labelKey(){
  return [cssVar("--viewer")].concat(labelled().map(x => x.n.key)).join("|");
}

/* ---------------- the view ---------------- */
/* Most commits change nothing on the stage (a poll tick, a message); then
   the camera is left alone, so an animated zoom in flight is not cut short. */
export function render(st, opts){
  if (!viewer) return;
  const view = viewer.getView();
  let dirty = ensureModels();
  state.ents.forEach(e => {
    const k = styleKey(e);
    if (e.styled !== k){ styleEntity(e); e.styled = k; dirty = true; }
    if (e.shown !== e.visible){
      if (e.visible) e.model.show(); else e.model.hide();
      e.shown = e.visible; dirty = true;
    }
  });
  dirty = layer(layers.spheres, sphereKey(), drawSpheres) || dirty;
  dirty = layer(layers.gen, genKey(), drawGenerated) || dirty;
  dirty = layer(layers.labels, labelKey(), drawLabels) || dirty;
  if (opts.fit) fitAll(true);
  else if (dirty){ viewer.setView(view); viewer.render(); }
}

function styleEntity(e){
  const m = e.model, cartoon = entColor(e), run = liveRun();
  m.setStyle({}, {cartoon: {color: cartoon, thickness: 0.3, opacity: 0.92}});
  m.setStyle({hetflag: true}, {stick: {radius: 0.15, colorscheme: scheme(cssVar("--gl-carbon-het"))}});
  m.setStyle({resn: WATERS}, {sphere: {radius: 0.28}});
  if (run && e.uid === run.ent) stylePocket(m, cartoon, run);
  picksOf(e).forEach(k => { const n = e.nodes[k]; if (n) stylePick(m, n); });
  /* Hidden parts last, so neither a pocket nor a pick brings them back. */
  Object.keys(e.hidden).forEach(k => { if (e.hidden[k] && e.nodes[k]) m.setStyle(e.nodes[k].sel, {}); });
  m.setStyle({elem: "H"}, {});   /* a prepared structure carries every hydrogen */
}
/* What the tree has selected, drawn on top of the structure's own style. */
function stylePick(m, n){
  const col = cssVar("--gl-carbon-sel");
  if (n.kind === "root" || n.kind === "chain")
    m.setStyle(n.sel, {cartoon: {color: col, thickness: 0.34, opacity: 1}}, true);
  else if (n.kind === "water" || n.kind === "waters")
    m.setStyle(n.sel, {sphere: {radius: 0.38, color: col}}, true);
  else
    m.setStyle(n.sel, {stick: {radius: 0.2, colorscheme: scheme(col)}}, true);
}
/* 3Dmol selection for one pocket. Residues are grouped per chain: a pocket can
   span chains, and a flat list of numbers would light that number up on every
   one of them. */
function selOf(p){
  if (!p || !p.sel.length) return null;
  return {or: p.sel.map(g => ({chain: g.chain, resi: g.resi}))};
}
function stylePocket(model, cartoon, run){
  run.picks.forEach(i => {
    const ps = selOf(run.pockets[i]);
    if (!ps) return;
    model.setStyle(ps, {cartoon: {color: cartoon, thickness: 0.3},
      stick: {radius: 0.11, colorscheme: scheme(pocketColor(i))}});
    model.setStyle({and: [ps, {elem: "H"}]}, {});
  });
}
/* The site points are the stand-in ligand: the highlighted residues are
   exactly those within the cutoff of these points. Drawing them makes the
   selection legible instead of arbitrary. */
function drawSpheres(L){
  const run = liveRun();
  if (!state.spheres || !run || !entity(run.ent).visible) return;
  run.picks.forEach(i => {
    const p = run.pockets[i];
    if (!p) return;
    (p.spheres || []).forEach(s => {
      L.shapes.push(viewer.addSphere({center: {x: s[0], y: s[1], z: s[2]}, radius: 0.45,
                                      color: pocketColor(i), opacity: 0.7}));
    });
    const c = p.centroid;
    L.shapes.push(viewer.addSphere({center: {x: c[0], y: c[1], z: c[2]}, radius: 0.9,
                                    color: pocketColor(i), opacity: 0.95}));
  });
}
/* Generated molecules, drawn per pocket in that pocket's colour. */
function drawGenerated(L){
  const gen = liveGen(), run = liveRun();
  if (!gen || !entity(run.ent).visible) return;
  Object.keys(gen.groups).forEach(rank => {
    const grp = gen.groups[rank];
    if (!grp.visible || !grp.mols.length) return;
    const col = rankColor(run, rank), shown = grp.mols.slice(0, state.genShow);
    if (grp.focus >= state.genShow && grp.mols[grp.focus]) shown.push(grp.mols[grp.focus]);
    shown.forEach((block, k) => {
      if (grp.focus >= state.genShow && k === shown.length - 1) k = grp.focus;
      const m = viewer.addModel(block, "sdf");
      L.models.push(m);
      m.setStyle({}, {stick: {radius: k === grp.focus ? 0.17 : 0.075,
        colorscheme: scheme(col), opacity: k === grp.focus ? 1 : 0.85}});
      m.setStyle({elem: "H"}, {});
    });
  });
}
function drawLabels(L){
  labelled().forEach(x => {
    const atoms = x.e.model.selectedAtoms(x.n.sel);
    if (!atoms.length) return;
    const c = [0, 0, 0];
    atoms.forEach(a => { c[0] += a.x; c[1] += a.y; c[2] += a.z; });
    L.labels.push(viewer.addLabel(x.n.label, {
      position: {x: c[0] / atoms.length, y: c[1] / atoms.length, z: c[2] / atoms.length},
      fontSize: 11, fontColor: cssVar("--ink"), backgroundColor: cssVar("--panel"),
      backgroundOpacity: 0.85, borderThickness: 0.4, borderColor: cssVar("--line"),
      inFront: true, alignment: "bottomCenter", screenOffset: {x: 0, y: -14}}));
  });
}

/* ---------------- camera ---------------- */
/* Frame the structures on screen, not the hidden ones. */
export function fitAll(instant){
  if (!viewer) return;
  const ms = state.ents.filter(e => e.visible && e.model).map(e => e.model);
  try{ viewer.zoomTo(ms.length ? {or: ms.map(m => ({model: m}))} : {}, instant ? 0 : 320); }catch(e){}
  viewer.render();
}
/* Frame the union of the ticked pockets. */
export function zoomPocket(){
  const run = liveRun(), pe = run && entity(run.ent);
  if (!run || !run.picks.length || !pe.model || !viewer) return;
  const groups = [];
  run.picks.forEach(i => {
    const p = run.pockets[i];
    if (p) p.sel.forEach(g => groups.push({model: pe.model, chain: g.chain, resi: g.resi}));
  });
  if (!groups.length) return;
  try{ viewer.zoomTo({or: groups}, 260); viewer.render(); }catch(e){}
}
/* Frame the tree selection; one residue or ligand is framed with its
   surroundings. */
export function zoomPicks(){
  if (!viewer) return;
  const leaves = [];
  let last = null;
  state.picks.forEach(k => {
    const x = nodeOf(k);
    if (!x || !x.e.visible || !x.e.model) return;
    leafSels(x.n.sel).forEach(s => leaves.push(inModel(s, x.e.model)));
    last = x;
  });
  if (!leaves.length) return;
  try{
    viewer.zoomTo(state.picks.length === 1 && last.n.label
      ? {model: last.e.model, within: {distance: 8, sel: leaves[0]}}
      : {or: leaves}, 320);
  }catch(err){}
  viewer.render();
}
/* Centre on one generated molecule. The model list is rebuilt by render(),
   so its atoms are found by position in the SDF block. */
export function zoomMolecule(block){
  if (!viewer || !block) return;
  const lines = block.split("\n"), na = parseInt(lines[3], 10);
  if (!na || na < 1) return;
  const xs = [], ys = [], zs = [];
  for (let k = 0; k < na; k++){
    const L = lines[4 + k];
    if (!L) break;
    xs.push(parseFloat(L.slice(0, 10)));
    ys.push(parseFloat(L.slice(10, 20)));
    zs.push(parseFloat(L.slice(20, 30)));
  }
  const mid = a => a.reduce((s, v) => s + v, 0) / a.length;
  try{
    viewer.zoomTo({}, 0);
    viewer.center({x: mid(xs), y: mid(ys), z: mid(zs)}, 260);
    viewer.zoom(2.2, 260);
    viewer.render();
  }catch(e){}
}
