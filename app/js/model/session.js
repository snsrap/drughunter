/* A session saved to a file and opened again: every structure loaded and what
   was done to it, what is folded, hidden and selected, the live pocket run,
   and the molecules generated from it.

   Pure over a state object, so it is testable without a browser. A file
   rather than localStorage because a structure is megabytes of text and a
   generation run is more -- past what a browser will keep -- and because a
   file is something to keep, send and open somewhere else.

   A session file comes off disk like any other document, so restore() treats
   it as untrusted: every value is coerced to the type the page expects, and
   the three fields the page renders as markup are cut back to what the page
   itself writes (see boldOnly and safeSvg). Without that, opening someone
   else's session would run their script against this page's API. */

export const SESSION_APP = "proteinviewer";
export const SESSION_VERSION = 1;

/* An entity's own fields. model, tree and nodes are left out: they are what
   parsing `data` again produces. */
const ENT = ["uid", "id", "data", "fmt", "title", "sub", "source", "visible", "ci",
             "edited", "prepared", "prepLine", "prepSummary", "open", "hidden", "orig"];

/* ---------------- saving ---------------- */
export function snapshot(st, now){
  return {
    app: SESSION_APP, version: SESSION_VERSION,
    saved: now || new Date().toISOString(),
    ents: st.ents.map(e => {
      const o = {};
      ENT.forEach(k => { if (e[k] !== undefined) o[k] = e[k]; });
      return o;
    }),
    entSerial: st.entSerial,
    picks: st.picks.slice(), pickAnchor: st.pickAnchor,
    method: {method: st.method.method, backend: st.method.backend},
    /* Which output directory this session was collecting into, so reopening
       it goes on filling the same one (features/project.js checks that the
       server still has it). */
    project: {id: st.project.id, path: st.project.path, dest: st.project.dest},
    spheres: !!st.spheres, noLigand: !!st.noLigand, genShow: st.genShow,
    table: {sort: {key: st.table.sort.key, dir: st.table.sort.dir}, only: !!st.table.only},
    run: st.run, runSerial: st.runSerial,
    gen: st.gen,
  };
}

/* ---------------- opening ---------------- */
/* A session file, or coordinates? The marker the snapshot writes decides, not
   the file's name. */
export function isSessionText(text){
  const head = String(text).slice(0, 500);
  return /^\s*\{/.test(head) && head.indexOf(SESSION_APP) !== -1;
}

/* Replace everything in `st` with what the file holds. Throws if the file is
   not a session. Returns {ents, pockets, molecules, saved} to report. */
export function restore(st, snap){
  if (!snap || typeof snap !== "object" || Array.isArray(snap) || snap.app !== SESSION_APP)
    throw new Error("this is not a Protein Viewer session file");
  if (int(snap.version, 0) > SESSION_VERSION)
    throw new Error("this session was saved by a newer Protein Viewer (file version " +
                    int(snap.version, 0) + ", this page reads " + SESSION_VERSION + ")");
  const ents = [], uids = [];
  (Array.isArray(snap.ents) ? snap.ents : []).forEach(o => {
    const e = entity(o);
    /* Picks and the pocket run name a structure by uid, so a file with the
       same uid twice would have them point at both. */
    if (e && uids.indexOf(e.uid) === -1){ ents.push(e); uids.push(e.uid); }
  });
  if (!ents.length) throw new Error("this session file holds no structures");

  const run = restoreRun(snap.run, uids),
        gen = restoreGen(snap.gen, run);
  st.ents = ents;
  /* Past every uid in the file, so a structure loaded next cannot collide
     with a restored one. */
  st.entSerial = Math.max(int(snap.entSerial, 0),
                          ...uids.map(u => int(String(u).replace(/^e/, ""), 0)));
  st.picks = (Array.isArray(snap.picks) ? snap.picks : [])
    .filter(k => typeof k === "string" && uids.indexOf(k.split("/")[0]) !== -1);
  st.pickAnchor = st.picks.indexOf(snap.pickAnchor) !== -1 ? snap.pickAnchor : null;
  /* A removal cannot be taken back across a save: the history is not in the
     file, and inventing one would put back atoms this session never had. */
  st.undo = [];
  const m = snap.method || {};
  st.method = {method: str(m.method) || "fpocket", backend: str(m.backend) || "nma"};
  const pj = snap.project || {};
  st.project = {id: str(pj.id) || null, path: str(pj.path) || null, dest: str(pj.dest),
                files: 0, bytes: 0, busy: false, msg: null};
  st.spheres = !!snap.spheres;
  st.noLigand = !!snap.noLigand;
  st.genShow = int(snap.genShow, 25, 1, 100);
  const t = snap.table || {}, sort = t.sort || {};
  st.table = {sort: {key: str(sort.key) || "rank", dir: int(sort.dir, 1) < 0 ? -1 : 1},
              only: !!t.only, sel: null};
  st.run = run;
  st.runSerial = int(snap.runSerial, run ? 1 : 0);
  st.gen = gen;
  st.prep = {busy: null, error: null};
  st.pocketTask = {busy: null, error: null};
  st.genTask = {busy: false, msg: null};
  st.fetch = {busy: false, msg: null};
  st.busy = null;
  return {ents: ents.length, pockets: run ? run.pockets.length : 0,
          molecules: gen ? Object.keys(gen.groups).reduce((a, r) => a + gen.groups[r].mols.length, 0) : 0,
          saved: str(snap.saved)};
}

function entity(o){
  if (!o || typeof o !== "object" || typeof o.data !== "string" || !o.data.trim()) return null;
  return {
    uid: str(o.uid) || "e1", id: str(o.id) || "structure",
    data: o.data, fmt: o.fmt === "cif" ? "cif" : "pdb",
    title: str(o.title), sub: str(o.sub), source: str(o.source) || null,
    visible: o.visible !== false, ci: int(o.ci, 0, 0, 99),
    edited: !!o.edited, prepared: !!o.prepared,
    prepLine: boldOnly(str(o.prepLine)), prepSummary: str(o.prepSummary),
    open: flags(o.open), hidden: flags(o.hidden), orig: residueMap(o.orig),
    /* Parsed on the next commit, which is what rebuilds the tree. */
    model: null, tree: null, nodes: null, rev: 0, styled: null, shown: null,
  };
}

/* The pocket run stays listed and drawn -- its residues and site points are
   in the file -- but the server's copy of the structure went with the
   session, so `restored` marks it for the panels that need that copy. */
function restoreRun(o, uids){
  if (!o || typeof o !== "object" || uids.indexOf(str(o.ent)) === -1) return null;
  const pockets = (Array.isArray(o.pockets) ? o.pockets : []).map(pocket).filter(Boolean);
  if (!pockets.length) return null;
  return {
    id: str(o.id) || str(o.ent) + "#1", ent: str(o.ent), token: str(o.token),
    label: str(o.label) || str(o.ent), pockets, meta: runMeta(o.meta),
    /* `cached` said the server answered that one request from its cache: true
       of the run when it was made, and nothing to report now. */
    cached: false, restored: true,
    picks: (Array.isArray(o.picks) ? o.picks : [])
      .map(i => int(i, -1)).filter(i => i >= 0 && i < pockets.length),
  };
}

function pocket(p){
  if (!p || typeof p !== "object") return null;
  const sel = (Array.isArray(p.sel) ? p.sel : []).map(g => ({
    chain: str(g && g.chain),
    resi: (Array.isArray(g && g.resi) ? g.resi : []).map(r => int(r, 0)),
  })).filter(g => g.resi.length);
  return {
    rank: int(p.rank, 1), nResidues: int(p.nResidues, 0), nAtoms: int(p.nAtoms, 0),
    nSpheres: int(p.nSpheres, 0), score: num(p.score), volume: num(p.volume),
    drug: num(p.drug), persistence: num(p.persistence),
    crypticity: num(p.crypticity), cryptic: !!p.cryptic, sel,
    /* [x, y, z, radius] as the server sends them (pockets.Pocket.as_dict):
       the stage draws the first three, the radius goes out with a saved
       project's spheres file. */
    spheres: (Array.isArray(p.spheres) ? p.spheres : []).map(point).filter(Boolean),
    centroid: point(p.centroid) || [0, 0, 0],
  };
}
function point(a){
  if (!Array.isArray(a) || a.length < 3) return null;
  return a.slice(0, 4).map(v => num(v) || 0);
}

/* Every field the run line prints, typed: it interpolates the numbers
   directly, so a string where a count belongs would be markup on the page. */
function runMeta(m){
  if (!m || typeof m !== "object") return null;
  return {tool: str(m.tool) || "fpocket", backend: str(m.backend),
          conformers: int(m.conformers, 0), nCryptic: int(m.nCryptic, 0),
          receptorAtoms: int(m.receptorAtoms, 0), totalAtoms: int(m.totalAtoms, 0),
          nPockets: int(m.nPockets, 0), elapsed: num(m.elapsed) || 0,
          cutoff: num(m.cutoff) || 0, command: str(m.command),
          ligandRemoved: (Array.isArray(m.ligandRemoved) ? m.ligandRemoved : []).map(str)};
}

function restoreGen(o, run){
  if (!o || typeof o !== "object" || !run || str(o.forRun) !== run.id) return null;
  const src = o.groups && typeof o.groups === "object" ? o.groups : {}, groups = {};
  Object.keys(src).forEach(rank => {
    const g = src[rank] || {}, mols = (Array.isArray(g.mols) ? g.mols : []).filter(b => typeof b === "string");
    if (!mols.length) return;
    groups[String(rank)] = {mols, visible: g.visible !== false,
                            focus: int(g.focus, -1), seconds: num(g.seconds),
                            written: int(g.written, mols.length)};
  });
  if (!Object.keys(groups).length) return null;
  return {job: str(o.job), forRun: run.id, groups, meta: boldOnly(str(o.meta)),
          table: (Array.isArray(o.table) ? o.table : []).map(row).filter(Boolean)};
}

function row(r){
  if (!r || typeof r !== "object") return null;
  return {rank: int(r.rank, 1), i: int(r.i, 0), name: str(r.name), formula: str(r.formula),
          smiles: str(r.smiles), mw: num(r.mw), heavy: num(r.heavy), logp: num(r.logp),
          tpsa: num(r.tpsa), qed: num(r.qed), rings: num(r.rings), rotb: num(r.rotb),
          svg: safeSvg(str(r.svg))};
}

/* ---------------- coercion ---------------- */
function str(v){ return typeof v === "string" ? v : v == null ? "" : String(v); }
function num(v){ const n = typeof v === "number" ? v : parseFloat(v); return isFinite(n) ? n : null; }
function int(v, dflt, lo, hi){
  let n = parseInt(v, 10);
  if (!isFinite(n)) return dflt;
  if (lo !== undefined) n = Math.max(lo, n);
  if (hi !== undefined) n = Math.min(hi, n);
  return n;
}
/* {"<node key>": true} -- what is folded open, and what is hidden. */
function flags(o){
  const out = {};
  if (o && typeof o === "object")
    Object.keys(o).forEach(k => { if (o[k]) out[String(k)] = true; });
  return out;
}
/* {"<chain>": [residue numbers]} -- the residues a structure had when it was
   loaded, which is what Prepare must not rebuild as loops. */
function residueMap(o){
  const out = {};
  if (o && typeof o === "object")
    Object.keys(o).forEach(c => {
      if (Array.isArray(o[c])) out[String(c)] = o[c].map(r => int(r, 0));
    });
  return out;
}

/* The page writes these lines itself, with <b> around the numbers that
   matter. From a file, everything else is escaped rather than rendered. */
function boldOnly(s){
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/&lt;(\/?)b&gt;/g, "<$1b>");
}

/* RDKit's depictions are plain drawings: shapes, text and style attributes.
   Anything else -- a script, an event handler, a reference out to another
   document -- means the row was not written by this page, so the depiction is
   dropped rather than cleaned up and trusted. */
const SVG_TAGS = ["svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
                  "polyline", "polygon", "text", "tspan", "title", "desc"];
function safeSvg(s){
  if (!/^\s*<svg[\s>]/i.test(s)) return "";
  if (/\son[a-z]+\s*=|javascript:|<!--|<!\[/i.test(s)) return "";
  const tags = s.match(/<\/?([A-Za-z][A-Za-z0-9:-]*)/g) || [];
  for (const t of tags)
    if (SVG_TAGS.indexOf(t.replace(/^<\/?/, "").toLowerCase()) === -1) return "";
  return s;
}
