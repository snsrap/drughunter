/* A structure from the user's own file: a predicted model, a docked pose, a
   snapshot out of a trajectory, a structure prepared in an earlier session --
   anything that is not a deposited entry with a four-character id.

   Pure: text in, values out (the File API is in core/files.js). The server
   takes coordinates as text rather than a PDB id, so nothing here needs it;
   but the page has to read a file the way the server's parser does
   (proteinviewer/structure.py), or the tree would list atoms the detectors
   never see. That is what firstModel is for. */

import {cifLoops, cifCol, cifTokens, ATOM_SITE} from "./subset.js";

/* .pdb1 and friends are the assemblies RCSB generates; .ent and .brk are what
   older archives call a PDB file. */
const PDB_EXT = ["pdb", "ent", "brk", "pdb1", "pdb2", "pdb3", "pdb4"];
const CIF_EXT = ["cif", "mmcif", "pdbx"];

/* The file's own name: no directory, and no .gz -- the format is decided by
   what is under the compression. */
function basename(name){
  return (String(name || "").split(/[\\/]/).pop() || "").replace(/\.gz$/i, "");
}

/* "pdb" or "cif", from the extension when it says and otherwise sniffed the
   way detect_format does server-side: an mmCIF always carries a data_ block. */
export function formatOf(name, text){
  const ext = ((basename(name).match(/\.([A-Za-z0-9]+)$/) || [])[1] || "").toLowerCase();
  if (CIF_EXT.indexOf(ext) !== -1) return "cif";
  if (PDB_EXT.indexOf(ext) !== -1) return "pdb";
  const head = String(text || "").slice(0, 4096);
  return head.indexOf("_atom_site.") !== -1 || /^\s*data_/.test(head) ? "cif" : "pdb";
}

/* What to call the structure in the tree, which is also the id the server
   buffers it under. Only the characters a buffer name keeps
   (buffer.safe_token), so two files whose names differ cannot be rewritten to
   the same one and land on each other's buffered copy. */
export function idFromName(name){
  const id = basename(name)
    .replace(/\.[A-Za-z0-9]+$/, "")
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^[.\-_]+/, "")
    .slice(0, 40)
    .replace(/[.\-_]+$/, "");
  return id || "structure";
}

/* Whether the text holds coordinates at all, so an unreadable file is
   reported here rather than appearing as an empty structure in the tree. */
export function hasAtoms(text, fmt){
  return fmt === "cif" ? String(text).indexOf("_atom_site.") !== -1
                       : /^(ATOM  |HETATM)/m.test(String(text));
}

/* What the file says it is: TITLE records in a PDB, _struct.title in an
   mmCIF. Decoration, like the title RCSB serves beside an entry. */
export function titleOf(text, fmt){
  const lines = String(text || "").split("\n");
  if (fmt === "cif") return cifTitle(lines);
  const parts = [];
  for (const l of lines){
    if (/^TITLE /.test(l)) parts.push(l.slice(10));
    else if (/^(ATOM|HETATM|MODEL|SEQRES|ANISOU)/.test(l)) break;
  }
  return tidy(parts.join(" "));
}

function cifTitle(lines){
  for (let i = 0; i < lines.length; i++){
    if (lines[i].trim().indexOf("_struct.title") !== 0) continue;
    const rest = lines[i].trim().slice("_struct.title".length).trim();
    if (rest) return tidy(unquote(rest));
    /* A long value is written as a semicolon text block on the lines after. */
    if (!/^;/.test(lines[i + 1] || "")) return "";
    const out = [lines[i + 1].slice(1)];
    for (let j = i + 2; j < lines.length && !/^;/.test(lines[j]); j++) out.push(lines[j]);
    return tidy(out.join(" "));
  }
  return "";
}

function unquote(s){
  return /^'.*'$/.test(s) || /^".*"$/.test(s) ? s.slice(1, -1) : s;
}
function tidy(s){
  return String(s).replace(/\s+/g, " ").trim().slice(0, 200);
}

/* The first model only, which is what the server's parser reads and therefore
   what Prepare and the detectors will see. An NMR ensemble or a trajectory
   holds dozens of models, and showing all of them while operating on one
   would misrepresent what is on screen. Returns {text, models}. */
export function firstModel(text, fmt){
  return fmt === "cif" ? cifFirstModel(text) : pdbFirstModel(text);
}

function pdbFirstModel(text){
  const out = [];
  let models = 0, keep = true;
  String(text).split("\n").forEach(l => {
    if (/^MODEL /.test(l)){ models++; keep = models <= 1; return; }
    if (/^ENDMDL/.test(l)) return;
    /* Only coordinates belong to a model: the header, SEQRES and the trailer
       describe the file, so they stay whichever model they sit between. */
    if (!keep && /^(ATOM  |HETATM|ANISOU|SIGATM|SIGUIJ|TER)/.test(l)) return;
    out.push(l);
  });
  return {text: models > 1 ? out.join("\n") : String(text), models};
}

function cifFirstModel(text){
  const lines = String(text).split("\n"),
        loops = cifLoops(lines, ATOM_SITE),
        site = loops.filter(L => L.cat === "_atom_site.")[0];
  if (!site) return {text: String(text), models: 0};
  const cm = cifCol(site, "pdbx_PDB_model_num"), cid = cifCol(site, "id");
  if (cm < 0) return {text: String(text), models: 1};
  const seen = [], drop = {}, gone = {};
  site.rows.forEach(j => {
    const tok = cifTokens(lines[j]), m = tok[cm];
    if (seen.indexOf(m) === -1) seen.push(m);
    if (m !== seen[0]){
      drop[j] = true;
      if (cid >= 0) gone[tok[cid]] = true;
    }
  });
  if (seen.length <= 1) return {text: String(text), models: seen.length};
  /* An atom's anisotropic row follows it out, by id. */
  loops.forEach(L => {
    const ci = L === site ? -1 : cifCol(L, "id");
    if (ci < 0) return;
    L.rows.forEach(j => { if (gone[cifTokens(lines[j])[ci]]) drop[j] = true; });
  });
  return {text: lines.filter((l, j) => !drop[j]).join("\n"), models: seen.length};
}

/* The line under a file-loaded structure's name: where it came from, in place
   of the method and resolution an RCSB entry carries. */
export function fileSub(o){
  const bytes = o.bytes || 0, mb = bytes / 1048576;
  return [o.fmt === "cif" ? "mmCIF file" : "PDB file",
          o.models > 1 ? o.models + " models · first only" : null,
          bytes ? (mb >= 1 ? mb.toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " kB") : null,
         ].filter(Boolean).join(" · ");
}
