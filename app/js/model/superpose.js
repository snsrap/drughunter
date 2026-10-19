/* Superposition: moving one structure onto another by their Cα atoms, paired
   up through a sequence alignment -- what PyMOL's `align` does.

   Pure functions over the atoms 3Dmol parsed and over a structure's own text:
   no DOM, no 3Dmol, no server. Three steps:

   - pairing. Each structure's protein chains are read as sequences, every
     chain being moved is aligned to every reference chain (BLOSUM62, affine
     gaps, end gaps free, so a longer construct or a missing tail costs
     nothing), and chains are paired greedily, best alignment first. The
     residues the alignment matches give the Cα pairs.
   - fitting. The rotation minimising the RMSD over those pairs (Horn's
     quaternion method), refined by dropping the pairs more than twice the
     RMSD apart and fitting again, up to five times -- so a loop that moved or
     a domain that hinged does not drag the core out of place.
   - moving. The transform is applied to the structure's text, PDB or mmCIF,
     so what is drawn, downloaded, prepared and searched for pockets from then
     on is the superposed structure. */

import {cifLoops, cifCol, cifTokens, cifSetTokens, ATOM_SITE} from "./subset.js";

/* The standard residues, the modified ones a deposited chain carries, and the
   protonation-state names a prepared structure may be written with. */
const ONE = {ALA: "A", ARG: "R", ASN: "N", ASP: "D", CYS: "C", GLN: "Q", GLU: "E", GLY: "G",
  HIS: "H", ILE: "I", LEU: "L", LYS: "K", MET: "M", PHE: "F", PRO: "P", SER: "S", THR: "T",
  TRP: "W", TYR: "Y", VAL: "V",
  MSE: "M", SEP: "S", TPO: "T", PTR: "Y", CSO: "C", HYP: "P", MLY: "K", KCX: "K",
  HID: "H", HIE: "H", HIP: "H", HSD: "H", HSE: "H", HSP: "H", CYX: "C", CYM: "C",
  ASH: "D", GLH: "E", LYN: "K"};

const AA = "ARNDCQEGHILKMFPSTWYVX";
export const BLOSUM62 = [
  " 4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0  0",
  "-1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3 -1",
  "-2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3 -1",
  "-2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3 -1",
  " 0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1 -2",
  "-1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2 -1",
  "-1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2 -1",
  " 0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3 -1",
  "-2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3 -1",
  "-1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3 -1",
  "-1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1 -1",
  "-1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2 -1",
  "-1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1 -1",
  "-2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1 -1",
  "-1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2 -2",
  " 1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2  0",
  " 0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0  0",
  "-3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3 -2",
  "-2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1 -1",
  " 0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4 -1",
  " 0 -1 -1 -1 -2 -1 -1 -1 -1 -1 -1 -1 -1 -1 -2  0  0 -2 -1 -1 -1",
].map(r => r.trim().split(/\s+/).map(Number));

/* PyMOL's align defaults: a gap costs 10 to open and 0.5 per residue more. */
const OPEN = 10, EXTEND = 0.5;
/* Outlier rejection: up to five rounds, each dropping the pairs further apart
   than twice the RMSD of the last fit. */
const CYCLES = 5, CUTOFF = 2;
/* Past this many alignment cells (a large complex against another), only
   chains with the same id are aligned, so the page does not stall. */
const MAX_CELLS = 6e7;

/* ---------------- residues ---------------- */
/* A residue's identity within its structure. */
export function resKey(a){ return a.chain + "|" + a.resi + "|" + String(a.icode || "").trim(); }

/* The protein chains of a structure: [{chain, seq, res: [{key, resi, aa, ca,
   fit}]}], in file order. A residue counts if it has a Cα and is an amino
   acid -- by name, or by its N-CA-C backbone, which keeps a calcium ion (atom
   CA of residue CA) out. `only`, if given, is the atoms the fit is restricted
   to: residues outside it are still aligned, so the pairing sees the whole
   chain, but are not fitted on; chains with none of it are left out. */
export function chainsOf(atoms, only){
  const fit = only ? {} : null, seen = {}, byChain = {}, chains = [];
  if (only) only.forEach(a => { fit[resKey(a)] = true; });
  atoms.forEach(a => {
    const k = resKey(a);
    let r = seen[k];
    if (!r){
      r = seen[k] = {key: k, resi: a.resi, resn: a.resn, names: {}, ca: null};
      let c = byChain[a.chain];
      if (!c){ c = byChain[a.chain] = {chain: a.chain, res: []}; chains.push(c); }
      c.res.push(r);
    }
    r.names[a.atom] = true;
    if (a.atom === "CA" && !r.ca) r.ca = [a.x, a.y, a.z];
  });
  return chains.map(c => {
    const res = c.res.filter(r => r.ca && (ONE[r.resn] || (r.names.N && r.names.C)))
      .map(r => ({key: r.key, resi: r.resi, aa: ONE[r.resn] || "X", ca: r.ca, fit: !fit || !!fit[r.key]}));
    return {chain: c.chain, res, seq: res.map(r => r.aa).join("")};
  }).filter(c => c.res.length && c.res.some(r => r.fit));
}

/* ---------------- sequence alignment ---------------- */
function codes(s){
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++){
    const k = AA.indexOf(s[i]);
    out[i] = k < 0 ? AA.length - 1 : k;
  }
  return out;
}

/* Global alignment with free end gaps (Gotoh's affine gaps). Returns {score,
   pairs}: pairs are [i, j], the positions in a and b that align. */
export function align(a, b){
  const n = a.length, m = b.length, W = m + 1, NEG = -1e9;
  if (!n || !m) return {score: 0, pairs: []};
  const ca = codes(a), cb = codes(b), tb = new Uint8Array((n + 1) * W);
  /* H: best ending at (i, j); E: ending in a gap in a; F: in a gap in b.
     Two rows of each, since only the traceback needs the whole table: its
     cell holds where H came from (bits 0-1: 0 match, 1 E, 2 F) and whether E
     and F extended a gap (bits 2 and 3). */
  let Hp = new Float64Array(W), Hc = new Float64Array(W),
      Fp = new Float64Array(W).fill(NEG), Fc = new Float64Array(W);
  let best = -Infinity, bi = 0, bj = 0;
  for (let i = 1; i <= n; i++){
    const row = BLOSUM62[ca[i - 1]];
    let E = NEG;
    Hc[0] = 0; Fc[0] = NEG;
    for (let j = 1; j <= m; j++){
      let bits = 0;
      const eo = Hc[j - 1] - OPEN, ee = E - EXTEND;
      if (ee > eo){ E = ee; bits |= 4; } else E = eo;
      const fo = Hp[j] - OPEN, fe = Fp[j] - EXTEND;
      let F;
      if (fe > fo){ F = fe; bits |= 8; } else F = fo;
      Fc[j] = F;
      let h = Hp[j - 1] + row[cb[j - 1]], src = 0;
      if (E > h){ h = E; src = 1; }
      if (F > h){ h = F; src = 2; }
      Hc[j] = h;
      tb[i * W + j] = bits | src;
    }
    /* A trailing stretch of b left over is free: the end can be any row. */
    if (Hc[m] > best){ best = Hc[m]; bi = i; bj = m; }
    let t = Hp; Hp = Hc; Hc = t;
    t = Fp; Fp = Fc; Fc = t;
  }
  /* ... and so is one of a: or any column of the last row. */
  for (let j = 1; j <= m; j++) if (Hp[j] > best){ best = Hp[j]; bi = n; bj = j; }

  const pairs = [];
  let i = bi, j = bj, st = 0;
  while (i > 0 && j > 0){
    const c = tb[i * W + j];
    if (st === 0){
      const src = c & 3;
      if (src === 0){ pairs.push([i - 1, j - 1]); i--; j--; }
      else st = src;
    } else if (st === 1){ st = c & 4 ? 1 : 0; j--; }
    else { st = c & 8 ? 2 : 0; i--; }
  }
  return {score: best, pairs: pairs.reverse()};
}

/* An identical sequence aligns with itself, residue for residue, and need not
   be aligned at all: the usual case of a structure and its own prepared or
   edited copy. */
function selfAlign(s){
  const c = codes(s), pairs = [];
  let score = 0;
  for (let i = 0; i < c.length; i++){ score += BLOSUM62[c[i]][c[i]]; pairs.push([i, i]); }
  return {score, pairs};
}

/* Chains of the structure being moved paired with chains of the reference:
   every pair aligned, then taken best first, each chain used once. A later
   pair must score per residue at least half what the first did, so a
   leftover chain is not forced onto an unrelated one. Returns the residue
   pairs, [{m, t}]. */
function pairChains(M, T){
  let cells = 0;
  M.forEach(m => T.forEach(t => { if (m.seq !== t.seq) cells += m.seq.length * t.seq.length; }));
  const cand = [];
  M.forEach((m, i) => T.forEach((t, j) => {
    const same = m.chain === t.chain;
    if (m.seq !== t.seq && cells > MAX_CELLS && !same) return;
    const al = m.seq === t.seq ? selfAlign(m.seq) : align(m.seq, t.seq);
    if (al.pairs.length) cand.push({i, j, same, score: al.score, per: al.score / al.pairs.length, pairs: al.pairs});
  }));
  cand.sort((a, b) => b.score - a.score || (b.same - a.same) || a.i - b.i || a.j - b.j);
  const usedM = {}, usedT = {}, out = [];
  let first = null;
  cand.forEach(c => {
    if (usedM[c.i] || usedT[c.j] || c.score <= 0) return;
    if (first && c.per < first.per / 2) return;
    first = first || c;
    usedM[c.i] = usedT[c.j] = true;
    c.pairs.forEach(([a, b]) => out.push({m: M[c.i].res[a], t: T[c.j].res[b]}));
  });
  return out;
}

/* ---------------- fitting ---------------- */
/* Eigen-decomposition of a small symmetric matrix by cyclic Jacobi rotations.
   Returns {values, vectors}, the vectors as columns. */
function jacobi(A){
  const n = A.length, a = A.map(r => r.slice()),
        v = A.map((r, i) => r.map((_, j) => (i === j ? 1 : 0)));
  for (let sweep = 0; sweep < 64; sweep++){
    let off = 0;
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) off += a[p][q] * a[p][q];
    if (off < 1e-30) break;
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++){
      if (a[p][q] === 0) continue;
      const th = (a[q][q] - a[p][p]) / (2 * a[p][q]),
            t = (th >= 0 ? 1 : -1) / (Math.abs(th) + Math.sqrt(th * th + 1)),
            c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < n; k++){
        const kp = a[k][p], kq = a[k][q];
        a[k][p] = c * kp - s * kq; a[k][q] = s * kp + c * kq;
      }
      for (let k = 0; k < n; k++){
        const pk = a[p][k], qk = a[q][k];
        a[p][k] = c * pk - s * qk; a[q][k] = s * pk + c * qk;
      }
      for (let k = 0; k < n; k++){
        const kp = v[k][p], kq = v[k][q];
        v[k][p] = c * kp - s * kq; v[k][q] = s * kp + c * kq;
      }
    }
  }
  return {values: a.map((r, i) => r[i]), vectors: v};
}

/* x' = R x + t, R a row-major 3x3. */
export function apply(T, p){
  const R = T.R;
  return [R[0] * p[0] + R[1] * p[1] + R[2] * p[2] + T.t[0],
          R[3] * p[0] + R[4] * p[1] + R[5] * p[2] + T.t[1],
          R[6] * p[0] + R[7] * p[1] + R[8] * p[2] + T.t[2]];
}
function d2(a, b){ const x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2]; return x * x + y * y + z * z; }

/* The rigid transform taking points P onto points Q with the least RMSD:
   {R, t, rmsd}. Horn's closed form: the best rotation is the quaternion of
   the largest eigenvalue of a 4x4 matrix built from P and Q's covariance, so
   it is always a proper rotation, never a reflection. */
export function fit(P, Q){
  const n = P.length, cp = [0, 0, 0], cq = [0, 0, 0];
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++){ cp[k] += P[i][k] / n; cq[k] += Q[i][k] / n; }
  const S = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < n; i++)
    for (let a = 0; a < 3; a++) for (let b = 0; b < 3; b++)
      S[a][b] += (P[i][a] - cp[a]) * (Q[i][b] - cq[b]);
  const [[xx, xy, xz], [yx, yy, yz], [zx, zy, zz]] = S;
  const {values, vectors} = jacobi([
    [xx + yy + zz, yz - zy, zx - xz, xy - yx],
    [yz - zy, xx - yy - zz, xy + yx, zx + xz],
    [zx - xz, xy + yx, -xx + yy - zz, yz + zy],
    [xy - yx, zx + xz, yz + zy, -xx - yy + zz]]);
  let k = 0;
  for (let i = 1; i < 4; i++) if (values[i] > values[k]) k = i;
  const [w, x, y, z] = vectors.map(r => r[k]);
  const R = [w * w + x * x - y * y - z * z, 2 * (x * y - w * z), 2 * (x * z + w * y),
             2 * (x * y + w * z), w * w - x * x + y * y - z * z, 2 * (y * z - w * x),
             2 * (x * z - w * y), 2 * (y * z + w * x), w * w - x * x - y * y + z * z];
  const T = {R, t: [0, 0, 0]}, rc = apply(T, cp);
  T.t = [cq[0] - rc[0], cq[1] - rc[1], cq[2] - rc[2]];
  let ss = 0;
  for (let i = 0; i < n; i++) ss += d2(apply(T, P[i]), Q[i]);
  T.rmsd = Math.sqrt(ss / n);
  return T;
}

/* Whether `ident` identity over `n` aligned residues shows two sequences are
   related at all: Rost's twilight-zone curve (Protein Eng 12:85, 1999), below
   which an alignment is no better than chance and the residue pairing it
   gives need not mean anything. About 47 % over 40 residues, 29 % over 100,
   20 % from 500 on (where the curve flattens; it is read there for longer). */
export function related(ident, n){
  const L = Math.min(n, 500);
  return n > 0 && ident * 100 >= 480 * Math.pow(L, -0.32 * (1 + Math.exp(-L / 1000)));
}

/* Superpose one structure onto another. Each side is {atoms, only}: every
   atom 3Dmol parsed, and the ones the fit is restricted to (or null).
   Returns the transform {R, t} with what it rests on: rmsd over the n pairs
   kept of the `aligned` pairs fitted on, and `ident`, the fraction of
   identical residues over the chains' whole alignment -- a selection narrows
   the fit, not the question of whether the chains are related, which
   `related` answers. Throws an Error saying why it cannot. */
export function superpose(mob, ref){
  const M = chainsOf(mob.atoms, mob.only), T = chainsOf(ref.atoms, ref.only);
  if (!M.length) throw new Error(mob.only ? "the part selected has no protein Cα atoms" : "it has no protein Cα atoms");
  if (!T.length) throw new Error(ref.only ? "the part of the reference selected has no protein Cα atoms"
                                          : "the reference has no protein Cα atoms");
  const all = pairChains(M, T), pairs = all.filter(p => p.m.fit && p.t.fit);
  if (pairs.length < 3)
    throw new Error((pairs.length ? "only " + pairs.length : "no") +
                    " residues pair up with the reference — at least 3 are needed");
  const ident = all.filter(p => p.m.aa === p.t.aa && p.m.aa !== "X").length / all.length;

  let use = pairs, X = fit(use.map(p => p.m.ca), use.map(p => p.t.ca));
  for (let c = 0; c < CYCLES && X.rmsd > 1e-6; c++){
    const lim = CUTOFF * CUTOFF * X.rmsd * X.rmsd,
          keep = use.filter(p => d2(apply(X, p.m.ca), p.t.ca) <= lim);
    if (keep.length === use.length || keep.length < 3) break;
    use = keep;
    X = fit(use.map(p => p.m.ca), use.map(p => p.t.ca));
  }
  return {R: X.R, t: X.t, rmsd: X.rmsd, n: use.length, aligned: pairs.length, ident,
          paired: all.length, related: related(ident, all.length)};
}

/* ---------------- moving the text ---------------- */
/* An anisotropic displacement tensor turns with the atom: U' = R U Rᵀ.
   [U11, U22, U33, U12, U13, U23] in, the same out. */
function rotateU(R, u){
  const U = [[u[0], u[3], u[4]], [u[3], u[1], u[5]], [u[4], u[5], u[2]]],
        r = i => [R[3 * i], R[3 * i + 1], R[3 * i + 2]],
        e = (i, j) => { const a = r(i), b = r(j); let s = 0;
          for (let k = 0; k < 3; k++) for (let l = 0; l < 3; l++) s += a[k] * U[k][l] * b[l];
          return s; };
  return [e(0, 0), e(1, 1), e(2, 2), e(0, 1), e(0, 2), e(1, 2)];
}

/* The structure's own text with every atom moved by T. Only coordinates (and
   anisotropic tensors) change; every other record, header included, is kept
   as it was -- so CRYST1, SCALE and the assembly records still describe the
   deposited frame, as they do in a file PyMOL saves after `align`. */
export function moveText(text, fmt, T){
  return fmt === "cif" ? moveCif(String(text), T) : movePdb(String(text), T);
}

function pdbCoord(v){
  const s = v.toFixed(3);
  if (s.length > 8) throw new Error("a moved coordinate (" + s + ") does not fit a PDB file's columns");
  return s.padStart(8);
}
function movePdb(text, T){
  return text.split("\n").map(l => {
    if (/^(ATOM|HETATM)/.test(l)){
      const p = [30, 38, 46].map(c => parseFloat(l.slice(c, c + 8)));
      if (p.some(isNaN)) return l;
      return l.slice(0, 30) + apply(T, p).map(pdbCoord).join("") + l.slice(54);
    }
    if (/^ANISOU/.test(l)){
      const u = [28, 35, 42, 49, 56, 63].map(c => parseInt(l.slice(c, c + 7), 10));
      if (u.some(isNaN)) return l;
      return l.slice(0, 28) + rotateU(T.R, u).map(v => String(Math.round(v)).padStart(7)).join("") + l.slice(70);
    }
    return l;
  }).join("\n");
}

/* The new value written to as many decimals as the old one had (at least
   `min`), so a file keeps its own precision. */
function like(old, v, min){
  const d = (String(old).split(".")[1] || "").length;
  return v.toFixed(Math.max(min, d));
}
const ANISO = ["[1][1]", "[2][2]", "[3][3]", "[1][2]", "[1][3]", "[2][3]"];
function moveCif(text, T){
  const lines = text.split("\n");
  cifLoops(lines, ATOM_SITE).forEach(L => {
    if (L.cat === "_atom_site."){
      const cols = ["Cartn_x", "Cartn_y", "Cartn_z"].map(c => cifCol(L, c));
      if (cols.some(c => c < 0)) return;
      L.rows.forEach(j => {
        const tok = cifTokens(lines[j]), p = cols.map(c => parseFloat(tok[c]));
        if (p.some(isNaN)) return;
        const q = apply(T, p), set = {};
        cols.forEach((c, k) => { set[c] = like(tok[c], q[k], 3); });
        lines[j] = cifSetTokens(lines[j], set);
      });
      return;
    }
    /* _atom_site_anisotrop: U, or B, which is U scaled and turns the same. */
    ["U", "B"].forEach(pre => {
      const cols = ANISO.map(c => cifCol(L, pre + c));
      if (cols.some(c => c < 0)) return;
      L.rows.forEach(j => {
        const tok = cifTokens(lines[j]), u = cols.map(c => parseFloat(tok[c]));
        if (u.some(isNaN)) return;
        const r = rotateU(T.R, u), set = {};
        cols.forEach((c, k) => { set[c] = like(tok[c], r[k], 4); });
        lines[j] = cifSetTokens(lines[j], set);
      });
    });
  });
  return lines.join("\n");
}
