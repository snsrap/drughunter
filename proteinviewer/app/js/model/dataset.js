/* A ligand dataset, as the page reasons about it: what the resolution dialog
   asks and checks, what a row's card and detail say, and the query a page of
   rows is asked for. Pure -- no DOM, no fetch -- so it is testable without a
   browser (`npm test`).

   Columns are named by index everywhere a form value or a request needs them,
   never by name: a header can be any text, an index cannot collide with the
   words "none" or "choose". */

export const DEFAULT_LIMIT = 50;
export const MAX_LIMIT = 100;
export const PAGE_SIZES = [25, 50, 100];

/* What each way a row cannot train means, in the words of the row's detail. */
export const REASONS = {
  no_target: "the dataset has no target",
  invalid_structure: "the structure did not parse",
  missing_target: "the target is blank",
  invalid_target: "the target is not a number",
  censored: "the target is a bound (<, >), not a measurement",
  unknown_qualifier: "the qualifier is not one of =, <, >, <=, >=",
  conflicting_qualifiers: "the qualifier column and the value disagree",
  nonpositive_for_pki: "a pKi needs a value above zero",
};
export const reasonText = code => REASONS[code] || String(code);

/* The units a target can be in. "" is a real answer -- the column has none, or
   they do not matter -- and is not the same as not having answered. */
export const UNITS = [
  {value: "nM", label: "nM"}, {value: "uM", label: "µM"}, {value: "pM", label: "pM"},
  {value: "mM", label: "mM"}, {value: "M", label: "M"},
  {value: "", label: "none / not applicable"},
];

/* ---------------- the resolution dialog ---------------- */

/* The form as it starts. A structure column is chosen for the user only when
   exactly one column looks like one; everything else is left for them to say,
   because a column's numbers do not say which task is meant and its name does
   not say what its units are. */
export function newForm(staged){
  const cands = staged.structure_candidates || [];
  const only = cands.length === 1 ? staged.columns.find(c => c.name === cands[0]) : null;
  return {structure: only ? only.index : null, target: null, kind: null, positive: null,
          qualifier: null, units: null, transform: "identity"};
}

const col = (staged, i) => (i === null || i === undefined || i < 0) ? null : staged.columns[i] || null;
export const NO_TARGET = -1;
export const NO_QUALIFIER = -1;

/* Which task a column most likely is. Only a suggestion: 0 and 1 are numbers,
   and may be classes. */
export function suggestKind(c){ return c && c.type === "number" ? "regression" : "classification"; }

/* "HIV-1 PR FRET (Q7K): Ki (nM)" -> nM. */
export function suggestUnits(name){
  const m = /\((nM|uM|µM|pM|mM|M)\)\s*$/.exec(name || "");
  return m ? (m[1] === "µM" ? "uM" : m[1]) : null;
}

/* The column that qualifies a target: one whose name begins like the target's
   (minus its units) and which is called a qualifier or holds only qualifiers. */
export function suggestQualifier(staged, target){
  const t = col(staged, target);
  if (!t) return null;
  const stem = t.name.replace(/\s*\([^)]*\)\s*$/, "");
  const hit = staged.columns.find(c => c.index !== target && c.name.indexOf(stem) === 0 &&
    (/qualifier/i.test(c.name) || c.qualifier_like));
  return hit ? hit.index : null;
}

/* Make the form consistent with itself after a change: a pKi needs nM, so
   changing the units away from nM puts the transform back; a changed target
   forgets what was chosen about the old one. */
export function settle(staged, form, changed){
  const f = Object.assign({}, form);
  if (changed === "target"){
    f.kind = null; f.positive = null; f.qualifier = null; f.units = null; f.transform = "identity";
  }
  if (changed === "kind"){ f.positive = null; f.qualifier = null; f.units = null; f.transform = "identity"; }
  if (f.transform === "pki" && f.units !== "nM") f.transform = "identity";
  if (f.qualifier !== null && (f.qualifier === f.structure || f.qualifier === f.target)) f.qualifier = null;
  return f;
}

/* What the dialog shows for a form, and what is still wrong with it. */
export function describeForm(staged, form){
  const cands = staged.structure_candidates || [];
  const target = form.target !== null && form.target >= 0 ? col(staged, form.target) : null;
  const errors = [];
  const classes = target && form.kind === "classification" ? target.values : null;
  const vm = {
    structureNote: cands.length === 0
      ? "No column looks like it holds structures (the columns are: " +
        staged.columns.map(c => c.name).join("; ") + "). Choose the one that does; every value in it is read as SMILES."
      : cands.length > 1 ? "Several columns look like structures: choose one." : "",
    structureOptions: staged.columns.map(c => ({index: c.index, name: c.name,
      suggested: cands.indexOf(c.name) !== -1 && cands.length > 1})),
    targetOptions: staged.columns.filter(c => c.index !== form.structure),
    hasTarget: !!target,
    suggestedKind: target ? suggestKind(target) : null,
    qualifierShown: !!target && form.kind === "regression",
    qualifierOptions: staged.columns.filter(c => c.index !== form.structure && c.index !== form.target),
    suggestedQualifier: target ? suggestQualifier(staged, form.target) : null,
    unitsShown: !!target && form.kind === "regression",
    suggestedUnits: target ? suggestUnits(target.name) : null,
    pkiEnabled: form.units === "nM",
    classes: classes ? classes.map(v => v[0]) : null,
    positiveShown: !!classes && classes.length === 2,
    errors,
  };

  if (form.structure === null) errors.push("Choose the column that holds the structures.");
  if (form.target === null) errors.push("Choose the target column, or “No target — prediction only”.");
  else if (target){
    if (target.index === form.structure) errors.push("The target cannot be the structure column.");
    if (form.kind === null) errors.push("Say whether “" + target.name + "” is regression or classification.");
    if (form.kind === "regression"){
      if (form.qualifier === null) errors.push("Confirm the qualifier column, or choose “None”.");
      if (form.units === null) errors.push("Confirm the units of the target.");
    }
    if (form.kind === "classification"){
      if (!classes) errors.push("“" + target.name + "” has more than 20 different values: that looks like a number to predict, not classes.");
      else if (classes.length < 2) errors.push("“" + target.name + "” has only one value, so there is nothing to tell apart.");
      else if (classes.length === 2 && form.positive === null) errors.push("Choose which of the two classes is the positive one.");
    }
  }
  return vm;
}

/* The request that resolves a staged dataset, or null while the form is
   incomplete. The qualifier is always sent -- null is "none", said on purpose. */
export function buildResolveRequest(staged, form){
  if (describeForm(staged, form).errors.length) return null;
  const body = {dataset_id: staged.dataset_id, structure_column: col(staged, form.structure).name};
  if (form.target === NO_TARGET){ body.target_column = null; return body; }
  const t = col(staged, form.target);
  body.target_column = t.name;
  body.target_kind = form.kind;
  if (form.kind === "regression"){
    body.qualifier_column = form.qualifier === NO_QUALIFIER ? null : col(staged, form.qualifier).name;
    body.source_units = form.units;
    body.transform = form.transform;
  }else{
    body.qualifier_column = null;
    const classes = t.values.map(v => v[0]);
    if (classes.length === 2) body.positive_class = form.positive;
  }
  return body;
}

/* ---------------- what a resolved dataset says about itself ---------------- */

export function exclusionLine(exclusions){
  const parts = Object.keys(exclusions || {}).sort((a, b) => exclusions[b] - exclusions[a])
    .map(k => exclusions[k] + " " + (k === "invalid_structure" ? "with no structure" : reasonText(k)));
  return parts.join(" · ");
}

/* [label, value] pairs for the rail: what was read, and what can be used. */
export function summaryRows(meta){
  const t = meta.target;
  const target = !t ? "none — prediction only"
    : t.column + " · " + t.kind + (t.kind === "regression"
      ? (t.transform === "pki" ? " (pKi from nM)" : t.output_units ? " (" + t.output_units + ")" : "")
      : t.positive_class ? " (positive: " + t.positive_class + ")" : "");
  const failed = meta.n_parse_failures
    ? meta.n_parse_failures + " — records " + meta.parse_failures.map(f => f.record).join(", ") +
      (meta.n_parse_failures > meta.parse_failures.length ? ", …" : "")
    : "none";
  const rows = [
    ["File", meta.name], ["Rows", String(meta.n_rows)], ["Structure", meta.structure_column],
    ["Target", target], ["Parse failures", failed],
    ["Train on", meta.trainable ? meta.n_training_eligible + " of " + meta.n_rows : "— (no target)"],
    ["Predict on", meta.n_prediction_eligible + " of " + meta.n_rows],
  ];
  const why = exclusionLine(meta.exclusions);
  if (why && meta.trainable) rows.push(["Left out", why]);
  const d = meta.duplicates;
  if (d && d.n_structures) rows.push(["Duplicates", d.n_structures + " molecules in " + d.n_rows + " rows" +
    (d.n_conflicting ? " · " + d.n_conflicting + " with conflicting labels" : "")]);
  return rows;
}

/* ---------------- rows: cards and detail ---------------- */

const fmt = v => String(Number((+v).toFixed(3)));

/* The target as a card shows it. A bound keeps its qualifier and never
   becomes a number; a transformed value says what it was made from. */
export function targetText(row, meta){
  if (!meta.target) return "";
  const t = meta.target;
  if (row.censored){
    const src = row.target_source || "";
    return /^[<>=]/.test(src) ? src : (row.qualifier || "") + src;
  }
  if (row.target === null || row.target === undefined) return "—";
  if (t.kind === "classification") return String(row.target);
  return t.transform === "pki" ? "pKi " + fmt(row.target)
    : fmt(row.target) + (t.output_units ? " " + t.output_units : "");
}

/* Everything the detail shows for one row: every source column as it was
   written, the target the model would see beside the value it came from, and
   why the row cannot train, if it cannot. */
export function detail(row, meta){
  const t = meta.target;
  const model = [];
  if (t){
    const transformed = t.kind === "regression" && t.transform === "pki";
    model.push({label: "Model target", value: targetText(row, meta)});
    if (transformed && row.target_source)
      model.push({label: "Source value", value: row.target_source + " " + t.source_units});
    if (row.qualifier && row.qualifier !== "=")
      model.push({label: "Qualifier", value: row.qualifier});
  }
  return {
    title: row.label,
    record: row.record, rowId: row.row_id,
    columns: meta.columns.map(c => ({name: c.name, value: row.cells[c.index],
      isStructure: c.name === meta.structure_column, isTarget: c.name === meta.target_column})),
    smiles: row.smiles, structureError: row.structure_error,
    model,
    trains: row.training_eligible,
    reasons: row.exclusions.map(reasonText),
  };
}

/* ---------------- the query for a page of rows ---------------- */

export const newQuery = limit => ({offset: 0, limit: limit || DEFAULT_LIMIT,
                                   sort: {key: "row_id", dir: "asc"}, filters: []});

/* Anything that changes which rows match, or their order, starts again at the
   first page: an offset into the old result means nothing in the new one. */
export function withSort(q, key, dir){
  const flip = q.sort.key === key && q.sort.dir === "asc" ? "desc" : "asc";
  return Object.assign({}, q, {offset: 0, sort: {key, dir: dir || (q.sort.key === key ? flip : "asc")}});
}
export function withFilter(q, f){
  return Object.assign({}, q, {offset: 0, filters: q.filters.concat([f])});
}
export function withoutFilter(q, i){
  return Object.assign({}, q, {offset: 0, filters: q.filters.filter((_, j) => j !== i)});
}
export function withLimit(q, n){
  const limit = Math.max(1, Math.min(MAX_LIMIT, Math.floor(+n) || DEFAULT_LIMIT));
  return Object.assign({}, q, {offset: 0, limit});
}
export function withOffset(q, offset){
  return Object.assign({}, q, {offset: Math.max(0, Math.floor(+offset) || 0)});
}

export function queryBody(q, datasetId){
  return {dataset_id: datasetId, offset: q.offset, limit: q.limit, sort: q.sort, filters: q.filters};
}

export function pageInfo(total, offset, limit){
  if (!total) return {from: 0, to: 0, total: 0, prev: null, next: null, page: 0, pages: 0};
  const last = Math.max(0, Math.floor((total - 1) / limit) * limit);
  const at = Math.min(offset, last);
  return {
    from: at + 1, to: Math.min(total, at + limit), total,
    prev: at > 0 ? Math.max(0, at - limit) : null,
    next: at + limit < total ? at + limit : null,
    page: Math.floor(at / limit) + 1, pages: Math.floor((total - 1) / limit) + 1,
  };
}

/* What a filter box can be asked, from the server's own schema. */
export function operatorsFor(column, operators){ return column ? operators[column.type] || [] : []; }
export const OP_LABELS = {eq: "=", lt: "<", lte: "≤", gt: ">", gte: "≥", contains: "contains", missing: "is missing"};

/* A filter from what was typed, or {error}. Numbers are checked here so a typo
   is said at once; the server checks every filter again. */
export function parseFilter(column, op, text){
  if (!column) return {error: "Choose a column."};
  if (op === "missing") return {filter: {key: column.key, op, value: true}};
  const s = String(text).trim();
  if (column.type === "number"){
    if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s)) return {error: "“" + s + "” is not a number."};
    return {filter: {key: column.key, op, value: Number(s)}};
  }
  if (!s) return {error: "Type the text to look for."};
  return {filter: {key: column.key, op, value: s}};
}

export function filterText(f, schema){
  const c = schema.find(x => x.key === f.key);
  const name = c ? c.label : f.key;
  return f.op === "missing" ? name + (f.value === false ? " is present" : " is missing")
    : name + " " + (OP_LABELS[f.op] || f.op) + " " + f.value;
}

/* Responses arrive in any order. Each request takes a ticket; only the newest
   ticket's response may change what is on screen. */
export function makeLatest(){
  let n = 0;
  return {issue(){ return ++n; }, current(ticket){ return ticket === n; }};
}
