/* Cutting part of a structure out of its own file.

   Pure functions: no DOM, no 3Dmol. A part is cut from the original text
   rather than re-written from 3Dmol's atoms, so nothing the file says about
   those atoms is lost: PDB keeps its header records (SEQRES drives loop
   building in Prepare) and the atom lines verbatim; mmCIF keeps every
   category and only the atom_site rows of the selected atoms. */

/* A 3Dmol selection flattened to its leaves ({or:[...]} nests). */
export function leafSels(sel){
  return sel.or ? sel.or.reduce((a, s) => a.concat(leafSels(s)), []) : [sel];
}
/* A tree selection restricted to one model: two copies of a structure share
   chain and residue numbers, and generated molecules never count. */
export function inModel(sel, m){
  return Object.assign({model: m}, sel);
}

export function pdbSubset(text, atoms){
  const kept = {}, aniso = {}, head = [], bonds = [], out = [];
  atoms.forEach(a => { if (a.pdbline) kept[a.pdbline.substr(6, 5).trim()] = true; });
  text.split("\n").forEach(l => {
    if (/^ANISOU/.test(l)) aniso[l.substr(6, 5).trim()] = l;
    else if (/^CONECT/.test(l)){
      /* a bond record survives only if every atom it names does */
      const ids = (l.substr(6).match(/.{1,5}/g) || []).map(x => x.trim()).filter(Boolean);
      if (ids.length && ids.every(x => kept[x])) bonds.push(l);
    }
    else if (l.trim() && !/^(ATOM  |HETATM|SIGATM|SIGUIJ|TER|MASTER|END|MODEL )/.test(l)) head.push(l);
  });
  atoms.forEach(a => {
    if (!a.pdbline) return;
    out.push(a.pdbline);
    const an = aniso[a.pdbline.substr(6, 5).trim()];
    if (an) out.push(an);
  });
  return head.concat(out, bonds, ["END"]).join("\n") + "\n";
}

const CIF_TOKEN = /'(?:[^']|'(?=\S))*'|"(?:[^"]|"(?=\S))*"|\S+/g;
/* The tokens of one mmCIF data line, quoted values honoured. */
export function cifTokens(line){ return line.trim().match(CIF_TOKEN) || []; }

/* The looped categories of an mmCIF whose name matches `re`, as
   {cat, cols, rows}: the category prefix, its column names in order, and the
   line indices of its rows. One parser, so every pass over a file reads its
   loops the same way. */
export function cifLoops(lines, re){
  const n = lines.length, loops = [];
  for (let i = 0; i < n; i++){
    const cat = lines[i].trim() === "loop_" && re.exec((lines[i + 1] || "").trim());
    if (!cat) continue;
    const L = {cat: cat[0], cols: [], rows: []};
    let j = i + 1;
    while (j < n && lines[j].trim().indexOf(L.cat) === 0) L.cols.push(lines[j++].trim());
    while (j < n){
      const t = lines[j].trim();
      if (!t || t[0] === "#" || t[0] === "_" || t === "loop_" || t.indexOf("data_") === 0) break;
      L.rows.push(j++);
    }
    loops.push(L);
  }
  return loops;
}
/* Where one column sits in a loop's rows, or -1 if the file omits it. */
export function cifCol(L, name){ return L.cols.indexOf(L.cat + name); }

export const ATOM_SITE = /^_atom_site(_anisotrop)?\./;

/* 3Dmol numbers mmCIF atoms by position, not by _atom_site.id, so rows are
   matched on the coordinates it parsed from them. Anisotropic rows follow
   their atom by id. */
export function cifSubset(text, atoms){
  const lines = text.split("\n"), want = {}, keep = {}, drop = {};
  atoms.forEach(a => { want[a.x + "|" + a.y + "|" + a.z] = true; });
  const loops = cifLoops(lines, ATOM_SITE),
        site = loops.filter(L => L.cat === "_atom_site.")[0];
  if (!site) throw new Error("this mmCIF file has no _atom_site loop");
  const cx = cifCol(site, "Cartn_x"), cy = cifCol(site, "Cartn_y"),
        cz = cifCol(site, "Cartn_z"), cid = cifCol(site, "id");
  site.rows.forEach(j => {
    const tok = cifTokens(lines[j]);
    if (want[parseFloat(tok[cx]) + "|" + parseFloat(tok[cy]) + "|" + parseFloat(tok[cz])]) keep[tok[cid]] = true;
    else drop[j] = true;
  });
  loops.forEach(L => {
    if (L === site) return;
    const ci = cifCol(L, "id");
    L.rows.forEach(j => {
      const tok = cifTokens(lines[j]);
      if (ci < 0 || !keep[tok[ci]]) drop[j] = true;
    });
  });
  return lines.filter((l, j) => !drop[j]).join("\n");
}

/* Residues the structure had when it was loaded that a cut lacks, for each
   chain the cut still has. SEQRES still lists them, so without this Prepare
   would build removed or unselected residues back as loops. */
export function leftOut(e, atoms){
  const kept = {}, out = {};
  let any = false;
  atoms.forEach(a => { (kept[a.chain] = kept[a.chain] || {})[a.resi] = true; });
  Object.keys(e.orig || {}).forEach(c => {
    if (!kept[c]) return;
    const miss = e.orig[c].filter(r => !kept[c][r]);
    if (miss.length){ out[c] = miss; any = true; }
  });
  return any ? out : null;
}
