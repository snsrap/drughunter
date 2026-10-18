/* The structure tree: one root per loaded structure, then its chains with
   their residues, its ligands and its waters.

   Pure functions over the atoms 3Dmol parsed, so each node's selection picks
   exactly what was drawn for it. */

export const WATERS = ["HOH", "WAT", "DOD"];
/* Chain-end caps (ACE, NME, NH2: what Prepare puts at a gap it does not
   build) carry no CA, but they belong to the chain, not among the ligands. */
const CAPS = ["ACE", "NME", "NH2"];

export function polymerLike(names, resn){
  return (names.N && names.CA && names.C) || (names.P && names["C1'"]) || CAPS.indexOf(resn) !== -1;
}

/* Build e.tree, e.nodes, e.open, e.hidden and (once) e.orig from atoms
   ({chain, resi, icode, resn, hetflag, atom}). */
export function buildTree(e, atoms){
  const seen = {}, list = [], u = e.uid + "/", prevOpen = e.open, prevHidden = e.hidden;
  atoms.forEach(a => {
    const ic = (a.icode || "").trim(),
          k = a.chain + "|" + a.resi + "|" + ic + "|" + a.resn;
    let r = seen[k];
    if (!r){
      r = seen[k] = {chain: a.chain, resi: a.resi, ic, resn: a.resn, het: !!a.hetflag, names: {}};
      list.push(r);
    }
    r.names[a.atom] = true;
  });
  const root = {key: u + "root", kind: "root", sel: {}, kids: []}, chains = {}, ligs = [], waters = [];
  list.forEach(r => {
    const id = r.resn + " " + r.resi + r.ic,
          n = {key: u + r.chain + ":" + r.resi + r.ic + ":" + r.resn, name: id, chain: r.chain, resi: r.resi,
               sel: {chain: r.chain, resi: r.resi, resn: r.resn}, label: id + " · " + r.chain};
    if (WATERS.indexOf(r.resn) !== -1){ n.kind = "water"; waters.push(n); return; }
    /* A HETATM residue with a backbone is a modified residue in the chain
       (MSE, SEP, ...), not a ligand. */
    if (r.het && !polymerLike(r.names, r.resn)){ n.kind = "lig"; ligs.push(n); return; }
    n.kind = "res";
    let c = chains[r.chain];
    if (!c){
      c = chains[r.chain] = {key: u + "chain:" + r.chain, kind: "chain", chain: r.chain,
                             name: "Chain " + (r.chain || "?"),
                             sel: {chain: r.chain, hetflag: false}, kids: []};
      root.kids.push(c);
    }
    c.kids.push(n);
  });
  const nc = root.kids.length;
  root.kids.forEach(c => { c.count = c.kids.length + " res"; });
  root.count = nc + (nc === 1 ? " chain" : " chains");
  if (ligs.length) root.kids.push({key: u + "ligs", kind: "ligs", name: "Ligands", count: ligs.length,
    sel: {or: ligs.map(n => n.sel)}, kids: ligs});
  if (waters.length) root.kids.push({key: u + "waters", kind: "waters", name: "Water", count: waters.length,
    sel: {resn: WATERS}, kids: waters});
  e.tree = root;
  e.nodes = {};
  (function index(n, p){
    n.parent = p; e.nodes[n.key] = n;
    (n.kids || []).forEach(k => index(k, n));
  })(root, null);
  /* Everything folded but the structure itself: its chains, ligands and
     waters are listed, their contents wait for a click. Parsed again after
     a removal, the tree keeps whatever folding and hiding still applies. */
  e.open = {}; e.hidden = {};
  e.open[root.key] = true;
  Object.keys(prevOpen || {}).forEach(k => { if (e.nodes[k]) e.open[k] = prevOpen[k]; });
  if (prevHidden) Object.keys(prevHidden).forEach(k => { if (e.nodes[k]) e.hidden[k] = prevHidden[k]; });
  /* Deposited files are full of waters: they start hidden, and the eye on
     Water shows them. */
  else if (waters.length) e.hidden[u + "waters"] = true;
  /* The polymer residues as first loaded: what Prepare must not rebuild
     once they are removed. */
  if (!e.orig){
    e.orig = {};
    root.kids.forEach(c => {
      if (c.kind === "chain") e.orig[c.chain] = c.kids.map(n => n.resi);
    });
  }
}

/* What hides a node: the node itself, a folder above it, or its structure. */
export function hider(e, n){
  for (let x = n; x; x = x.parent)
    if (x.kind === "root" ? !e.visible : e.hidden[x.key]) return x;
  return null;
}
