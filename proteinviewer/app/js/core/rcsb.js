/* Entries from the RCSB PDB: coordinates plus title, method and resolution. */

const cache = {};

export function isPdbId(id){ return /^[0-9][A-Z0-9]{3}$/.test(id); }

async function getText(url){
  const r = await fetch(url);
  if (!r.ok){ const e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
  return r.text();
}

/* {id, data, fmt, title, sub}. A 404 rejects with err.status === 404. */
export function fetchEntry(id){
  if (cache[id]) return Promise.resolve(cache[id]);
  const base = "https://files.rcsb.org/download/" + id;
  /* Large entries have no legacy .pdb file, so fall back to mmCIF. */
  const coords = getText(base + ".pdb").then(t => ({data: t, fmt: "pdb"}),
    () => getText(base + ".cif").then(t => ({data: t, fmt: "cif"})));
  /* Metadata is decoration only; a failure here must not fail the fetch. */
  const meta = getText("https://data.rcsb.org/rest/v1/core/entry/" + id)
    .then(t => JSON.parse(t)).catch(() => null);
  return Promise.all([coords, meta]).then(([c, m]) => {
    m = m || {};
    const info = m.rcsb_entry_info || {},
          res = (info.resolution_combined || [])[0],
          method = ((m.exptl || [])[0] || {}).method;
    const entry = {id, data: c.data, fmt: c.fmt,
      title: (m.struct && m.struct.title) || "",
      sub: [method ? method.toLowerCase() : null, res ? res.toFixed(2) + " Å" : null]
             .filter(Boolean).join(" · ") || "deposited coordinates"};
    cache[id] = entry;
    return entry;
  });
}
