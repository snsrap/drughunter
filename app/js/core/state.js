/* The page's state: one object, changed only by the features' actions.

   An action changes `state` and then calls commit(); every view registered
   with subscribe() re-renders from it, in registration order (the 3D scene
   first, since parsing a structure is what builds its tree). Views never
   change state, and nothing outside this object holds state another module
   needs -- so "what has to be repainted after X" is never a question. */

export const state = {
  /* Everything loaded, in load order. Each entry is drawn in its own
     deposited frame; nothing is superposed. See addEntity for the fields. */
  ents: [], entSerial: 0,
  /* Tree selection: node keys ("<entity uid>/<node>"), in the order picked. */
  picks: [], pickAnchor: null,
  /* Removals, newest last, so a stray click on a cross can be taken back. */
  undo: [],

  /* What the server can run; filled in by the status probe. */
  server: {known: false, stale: false, error: null,
           fpocket: false, lacuna: [], prep: false, neat: false,
           projects: false, projectRoot: ""},
  fetch: {busy: false, msg: null},          /* the PDB fetch form, and files */
  session: {msg: null},                     /* saving and opening a session */
  /* This session's output directory on the server, where each run's
     artifacts are collected: {id, path} once there is one, dest the path the
     user last asked for a copy at. */
  project: {id: null, path: null, files: 0, bytes: 0, dest: "", busy: false, msg: null},
  prep: {busy: null, error: null},          /* busy: the label being prepared */

  /* Which detector the Pockets panel is set to. */
  method: {method: "fpocket", backend: "nma"},
  /* Whether to delete the bound ligands before detecting: one fills its own
     site, so that site is ranked low or missed while it is there. */
  noLigand: false,
  /* The one live pocket run, or null:
       {id, ent, token, label, pockets, meta, cached, picks}
     ent is the structure it ran on, id names the run (generated molecules
     belong to a run), token is the server copy it read, label says which
     part of the structure that was, picks are indices into pockets. */
  run: null, runSerial: 0,
  pocketTask: {busy: null, error: null},
  spheres: false,

  /* Generated molecules for one run: {job, forRun, groups, table, meta}. */
  gen: null,
  genTask: {busy: false, msg: null},
  genShow: 25,
  table: {sort: {key: "rank", dir: 1}, only: false, sel: null},

  busy: null,                               /* text of the busy pill */
};

const views = [];
export function subscribe(view){ views.push(view); }
/* opts.fit: frame the visible structures afresh instead of keeping the view. */
export function commit(opts){ views.forEach(v => v(state, opts || {})); }

/* ---------------- selectors and small shared mutations ---------------- */
export function entity(uid){
  return state.ents.find(e => e.uid === uid) || null;
}
/* Tree node by key, with the structure it belongs to. */
export function nodeOf(key){
  const e = entity(String(key).split("/")[0]), n = e && e.nodes && e.nodes[key];
  return n ? {e, n} : null;
}
export function picksOf(e){
  return state.picks.filter(k => k.indexOf(e.uid + "/") === 0);
}
/* The pocket run, if the structure it ran on is still loaded. */
export function liveRun(){
  return state.run && entity(state.run.ent) ? state.run : null;
}
/* Generated molecules that belong to the live run. */
export function liveGen(){
  const run = liveRun(), gen = state.gen;
  return run && gen && gen.groups && gen.forRun === run.id ? gen : null;
}
/* An id not already on screen: 1HSG-prep, 1HSG-prep-2, ... */
export function freeId(id){
  let n = 1, out = id;
  while (state.ents.some(e => e.id === out)) out = id + "-" + (++n);
  return out;
}
/* A loaded structure: {uid, id, data, fmt, title, sub, visible, ci (colour
   slot), and once parsed: model, tree, nodes, open, hidden, orig, rev}. */
export function addEntity(o, slots){
  const used = state.ents.map(e => e.ci);
  let ci = 0;
  while (used.indexOf(ci) !== -1 && ci < slots) ci++;
  o.uid = "e" + (++state.entSerial); o.ci = ci % slots; o.visible = true;
  state.ents.push(o);
  return o;
}
/* Pocket results describe atoms as they were; when those change, the run
   and everything generated from it go. */
export function clearRun(){
  state.run = null; state.gen = null; state.table.sel = null;
}
