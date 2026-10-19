/* What Prepare and Find pockets run on: one structure, whole or in part.

   Pure over a {ents, picks} state. Several structures in the selection is a
   question only the user can settle, so that is an error, not a guess. */

import {leafSels, pdbSubset, cifSubset, leftOut} from "./subset.js";

/* {e, whole, nodes, label} or {err, soft}. */
export function scope(st){
  const ents = st.ents, byUid = uid => ents.find(e => e.uid === uid);
  if (!ents.length) return {err: "Fetch a PDB entry, or open a file, above."};
  const uids = [];
  st.picks.forEach(k => {
    const u = k.split("/")[0];
    if (uids.indexOf(u) === -1) uids.push(u);
  });
  if (uids.length > 1)
    return {err: "The selection spans " + uids.map(u => byUid(u).id).join(" and ") +
                 ". Operations run on one structure at a time — select parts of one."};
  let e = uids.length ? byUid(uids[0]) : null;
  if (!e){
    const vis = ents.filter(x => x.visible);
    e = ents.length === 1 ? ents[0] : vis.length === 1 ? vis[0] : null;
    if (!e) return {err: "Several structures are loaded — select one, or parts of one, in the tree.", soft: true};
  }
  const nodes = st.picks.filter(k => k.indexOf(e.uid + "/") === 0).map(k => e.nodes[k]).filter(Boolean),
        whole = !nodes.length || nodes.some(n => n.kind === "root");
  return {e, whole, nodes: whole ? [] : nodes,
          label: whole ? e.id : e.id + " · " + summarize(nodes)};
}

/* "chain A, ILE 3, MK1 902" -- short enough for a button line. */
export function summarize(nodes){
  const by = {}, parts = [];
  nodes.forEach(n => { (by[n.kind] = by[n.kind] || []).push(n); });
  const names = (list, many) =>
    list.length <= 2 ? list.map(n => n.name).join(", ") : list.length + " " + many;
  if (by.root) parts.push("whole structure");
  if (by.chain) parts.push((by.chain.length === 1 ? "chain " : "chains ") +
                           by.chain.map(n => n.chain).join(", "));
  if (by.res) parts.push(names(by.res, "residues"));
  if (by.ligs) parts.push("all ligands");
  if (by.lig) parts.push(names(by.lig, "ligands"));
  if (by.waters) parts.push("all waters");
  if (by.water) parts.push(names(by.water, "waters"));
  return parts.join(", ");
}

/* The atoms the scope names, as 3Dmol parsed them. */
export function scopeAtoms(sc){
  if (!sc.e || !sc.e.model) return [];
  if (sc.whole) return sc.e.model.selectedAtoms({});
  const sel = {or: []};
  sc.nodes.forEach(n => { sel.or = sel.or.concat(leafSels(n.sel)); });
  return sc.e.model.selectedAtoms(sel);
}

/* The scoped structure as text the server can buffer:
   {token, data, fmt, noRebuild}. A part is posted as its own structure
   (<id>.sel-<hash>), so pocket results and caching stay separate per
   selection. */
export function scopeStructure(sc){
  const e = sc.e, atoms = scopeAtoms(sc);
  if (sc.whole){
    return {token: e.id, data: e.data, fmt: e.fmt, noRebuild: leftOut(e, atoms)};
  }
  if (!atoms.length) throw new Error("the selection has no atoms");
  const data = e.fmt === "cif" ? cifSubset(e.data, atoms) : pdbSubset(e.data, atoms);
  const keys = sc.nodes.map(n => n.key).sort().join(",");
  let h = 5381;
  for (let i = 0; i < keys.length; i++) h = ((h * 33) ^ keys.charCodeAt(i)) >>> 0;
  return {token: e.id + ".sel-" + h.toString(36), data, fmt: e.fmt, noRebuild: leftOut(e, atoms)};
}
