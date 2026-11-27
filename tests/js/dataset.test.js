// The ligand dataset's own logic -- the dialog's rules, the cards' text, the
// query for a page of rows: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {
  newForm, describeForm, settle, buildResolveRequest, suggestKind, suggestUnits, suggestQualifier,
  summaryRows, targetText, detail, exclusionLine, NO_TARGET, NO_QUALIFIER,
  newQuery, withSort, withFilter, withoutFilter, withLimit, withOffset, queryBody, pageInfo,
  parseFilter, filterText, makeLatest, operatorsFor, MAX_LIMIT,
  datasetFromPath, ligandsPath, isCsvName, LIGANDS,
} from "../../proteinviewer/app/js/model/dataset.js";

const KI = "HIV-1 PR FRET (Q7K): Ki (nM)", QUAL = "HIV-1 PR FRET (Q7K): Ki Qualifier";

/* What upload answers for the CDD fixture. */
function cdd(){
  const c = (index, name, type, extra) => Object.assign(
    {index, key: "c" + index, name, type, n_filled: 106, examples: [], values: null, qualifier_like: false}, extra);
  return {
    dataset_id: "ds-aaaaaaaaaaaa", state: "staged", name: "cdd.csv", n_rows: 106,
    structure_candidates: ["SMILES"],
    columns: [c(0, "Molecule Name", "text"), c(1, "CDD Number", "text"), c(2, "Batch Name", "text"),
      c(3, "SMILES", "text"), c(4, KI, "number"),
      c(5, QUAL, "text", {n_filled: 4, values: [[">", 4]], qualifier_like: true}),
      c(6, "Run Date", "text", {values: [["2009-02-15", 106]]}),
      c(7, "Project", "text", {values: [["HIV protease", 106]]})],
  };
}

function classes(){
  const s = cdd();
  s.columns.push({index: 8, key: "c8", name: "Active", type: "number", n_filled: 106, examples: [],
                  values: [["0", 60], ["1", 46]], qualifier_like: false});
  return s;
}

// ---- the dialog ----

test("the one column that looks like a structure is chosen for the user; nothing else is", () => {
  assert.deepEqual(newForm(cdd()),
    {structure: 3, target: null, kind: null, positive: null, qualifier: null, units: null, transform: "identity"});
  const many = cdd(); many.structure_candidates = ["SMILES", "Molecule Name"];
  assert.equal(newForm(many).structure, null);
  const none = cdd(); none.structure_candidates = [];
  assert.equal(newForm(none).structure, null);
  assert.match(describeForm(none, newForm(none)).structureNote,
    /No column looks like.*the columns are: Molecule Name; CDD Number; Batch Name; SMILES; HIV-1/);
  assert.match(describeForm(many, newForm(many)).structureNote, /Several columns/);
  assert.equal(describeForm(cdd(), newForm(cdd())).structureNote, "");
});

test("the dialog asks for the target, the task, the qualifier and the units, one after another", () => {
  const s = cdd();
  let f = newForm(s);
  assert.deepEqual(describeForm(s, f).errors, ["Choose the target column, or “No target — prediction only”."]);
  f = settle(s, Object.assign({}, f, {target: 4}), "target");
  assert.deepEqual(describeForm(s, f).errors, [`Say whether “${KI}” is regression or classification.`]);
  f = settle(s, Object.assign({}, f, {kind: "regression"}), "kind");
  assert.deepEqual(describeForm(s, f).errors,
    ["Confirm the qualifier column, or choose “None”.", "Confirm the units of the target."]);
  f = Object.assign({}, f, {qualifier: 5, units: "nM"});
  assert.deepEqual(describeForm(s, f).errors, []);
});

test("a numeric column is suggested as regression, but never decided", () => {
  const s = cdd();
  assert.equal(suggestKind(s.columns[4]), "regression");
  assert.equal(suggestKind(s.columns[7]), "classification");
  const f = settle(s, Object.assign(newForm(s), {target: 4}), "target");
  assert.equal(f.kind, null, "the task is still the user's to confirm");
  assert.equal(describeForm(s, f).suggestedKind, "regression");
});

test("zero and one can be classes: a numeric column may be classification", () => {
  const s = classes();
  let f = settle(s, Object.assign(newForm(s), {target: 8}), "target");
  f = settle(s, Object.assign(f, {kind: "classification"}), "kind");
  const vm = describeForm(s, f);
  assert.deepEqual(vm.classes, ["0", "1"]);
  assert.equal(vm.positiveShown, true);
  assert.deepEqual(vm.errors, ["Choose which of the two classes is the positive one."]);
  assert.equal(describeForm(s, Object.assign(f, {positive: "1"})).errors.length, 0);
});

test("classification needs two to twenty classes", () => {
  const s = cdd();
  let f = settle(s, Object.assign(newForm(s), {target: 7}), "target");
  f = settle(s, Object.assign(f, {kind: "classification"}), "kind");
  assert.match(describeForm(s, f).errors[0], /only one value/);
  f = settle(s, Object.assign(newForm(s), {target: 0}), "target");   // 106 different names
  f = settle(s, Object.assign(f, {kind: "classification"}), "kind");
  assert.match(describeForm(s, f).errors[0], /more than 20 different values/);
});

test("the structure column cannot be the target or its qualifier", () => {
  const s = cdd();
  const vm = describeForm(s, Object.assign(newForm(s), {target: 4, kind: "regression", qualifier: 5, units: "nM"}));
  assert.ok(vm.targetOptions.every(c => c.index !== 3));
  assert.ok(vm.qualifierOptions.every(c => c.index !== 3 && c.index !== 4));
  const f = settle(s, {structure: 3, target: 4, kind: "regression", qualifier: 4, units: "nM", positive: null, transform: "identity"}, "qualifier");
  assert.equal(f.qualifier, null, "a qualifier equal to the target is dropped");
});

test("a pKi is only offered for nM, and goes back to identity when the units change", () => {
  const s = cdd();
  let f = Object.assign(newForm(s), {target: 4, kind: "regression", qualifier: 5, units: "nM", transform: "pki"});
  assert.equal(describeForm(s, f).pkiEnabled, true);
  assert.equal(settle(s, f, "transform").transform, "pki");
  f = settle(s, Object.assign(f, {units: "uM"}), "units");
  assert.equal(f.transform, "identity");
  assert.equal(describeForm(s, f).pkiEnabled, false);
});

test("changing the target forgets what was said about the old one", () => {
  const s = cdd();
  const f = settle(s, {structure: 3, target: 4, kind: "regression", qualifier: 5, units: "nM", positive: null, transform: "pki"}, "target");
  assert.deepEqual([f.kind, f.qualifier, f.units, f.transform], [null, null, null, "identity"]);
});

test("units, qualifier and task are suggested from the column names", () => {
  const s = cdd();
  assert.equal(suggestUnits(KI), "nM");
  assert.equal(suggestUnits("IC50 (µM)"), "uM");
  assert.equal(suggestUnits("LogP"), null);
  assert.equal(suggestQualifier(s, 4), 5);
  assert.equal(suggestQualifier(s, 7), null);
  const vm = describeForm(s, Object.assign(newForm(s), {target: 4}));
  assert.deepEqual([vm.suggestedQualifier, vm.suggestedUnits], [5, "nM"]);
});

test("the resolve request names columns, states the qualifier even when there is none", () => {
  const s = cdd();
  const f = {structure: 3, target: 4, kind: "regression", qualifier: 5, units: "nM", positive: null, transform: "pki"};
  assert.deepEqual(buildResolveRequest(s, f), {
    dataset_id: "ds-aaaaaaaaaaaa", structure_column: "SMILES", target_column: KI,
    target_kind: "regression", qualifier_column: QUAL, source_units: "nM", transform: "pki"});
  const none = buildResolveRequest(s, Object.assign({}, f, {qualifier: NO_QUALIFIER, units: "", transform: "identity"}));
  assert.ok("qualifier_column" in none && none.qualifier_column === null);
  assert.equal(none.source_units, "");
});

test("no target means a prediction-only dataset", () => {
  const s = cdd();
  assert.deepEqual(buildResolveRequest(s, Object.assign(newForm(s), {target: NO_TARGET})),
    {dataset_id: "ds-aaaaaaaaaaaa", structure_column: "SMILES", target_column: null});
});

test("classification sends the positive class for two classes and no qualifier or units", () => {
  const s = classes();
  const f = {structure: 3, target: 8, kind: "classification", positive: "1", qualifier: null, units: null, transform: "identity"};
  assert.deepEqual(buildResolveRequest(s, f), {
    dataset_id: "ds-aaaaaaaaaaaa", structure_column: "SMILES", target_column: "Active",
    target_kind: "classification", qualifier_column: null, positive_class: "1"});
});

test("an incomplete form builds no request", () => {
  const s = cdd();
  assert.equal(buildResolveRequest(s, newForm(s)), null);
  assert.equal(buildResolveRequest(s, Object.assign(newForm(s), {structure: null, target: NO_TARGET})), null);
});

// ---- what a resolved dataset says ----

function meta(extra){
  return Object.assign({
    dataset_id: "ds-1", name: "cdd.csv", n_rows: 106, structure_column: "SMILES", target_column: KI,
    target_kind: "regression", trainable: true, n_parse_failures: 0, parse_failures: [],
    n_prediction_eligible: 106, n_training_eligible: 102, exclusions: {censored: 4},
    duplicates: {n_structures: 0, n_rows: 0, n_conflicting: null, examples: []},
    columns: cdd().columns,
    target: {column: KI, kind: "regression", transform: "pki", source_units: "nM", output_units: "pKi",
             qualifier_column: QUAL, positive_class: null},
  }, extra || {});
}

test("the summary says what was read and what can be used", () => {
  const rows = Object.fromEntries(summaryRows(meta()));
  assert.equal(rows["File"], "cdd.csv");
  assert.equal(rows["Rows"], "106");
  assert.equal(rows["Structure"], "SMILES");
  assert.match(rows["Target"], /regression \(pKi from nM\)/);
  assert.equal(rows["Parse failures"], "none");
  assert.equal(rows["Train on"], "102 of 106");
  assert.equal(rows["Left out"], "4 the target is a bound (<, >), not a measurement");
});

test("parse failures are listed by record number, and a target-free dataset says it cannot train", () => {
  const m = meta({target: null, target_column: null, target_kind: null, trainable: false,
    n_training_eligible: 0, exclusions: {no_target: 106}, n_parse_failures: 7,
    parse_failures: [2, 3, 5, 8, 9].map(record => ({record}))});
  const rows = Object.fromEntries(summaryRows(m));
  assert.equal(rows["Parse failures"], "7 — records 2, 3, 5, 8, 9, …");
  assert.equal(rows["Target"], "none — prediction only");
  assert.equal(rows["Train on"], "— (no target)");
  assert.ok(!("Left out" in rows));
});

test("conflicting duplicate labels are reported", () => {
  const rows = Object.fromEntries(summaryRows(meta({duplicates: {n_structures: 2, n_rows: 5, n_conflicting: 1}})));
  assert.equal(rows["Duplicates"], "2 molecules in 5 rows · 1 with conflicting labels");
  assert.equal(exclusionLine({invalid_structure: 2, missing_target: 5}),
    "5 the target is blank · 2 with no structure");
});

function row(extra){
  return Object.assign({row_id: 0, record: 1, label: "DH-0001", cells: ["DH-0001", "CDD1", "B", "CCO", "0.0008", "", "d", "p"],
    smiles: "CCO", structure_error: null, target: 12.0969, target_source: "0.0008", qualifier: "=",
    censored: false, prediction_eligible: true, training_eligible: true, exclusions: []}, extra || {});
}

test("a card shows the model's target; a pKi says what it was made from", () => {
  const m = meta();
  assert.equal(targetText(row(), m), "pKi 12.097");
  assert.equal(targetText(row({target: null, target_source: null, exclusions: ["missing_target"]}), m), "—");
  assert.equal(targetText(row(), meta({target: Object.assign({}, m.target, {transform: "identity", output_units: "nM"})})), "12.097 nM");
  assert.equal(targetText(row(), meta({target: null})), "");
});

test("a bound keeps its qualifier and is never shown as a number", () => {
  const m = meta();
  assert.equal(targetText(row({censored: true, target: null, target_source: ">10000.0", qualifier: ">"}), m), ">10000.0");
  assert.equal(targetText(row({censored: true, target: null, target_source: "10000", qualifier: ">"}), m), ">10000");
});

test("class labels are shown as written", () => {
  const m = meta({target: {column: "Active", kind: "classification", positive_class: "1"}});
  assert.equal(targetText(row({target: "1"}), m), "1");
});

test("the detail has every source column, the transformed target beside its source, and why a row cannot train", () => {
  const d = detail(row(), meta());
  assert.equal(d.columns.length, 8);
  assert.deepEqual(d.columns.find(c => c.isStructure), {name: "SMILES", value: "CCO", isStructure: true, isTarget: false});
  assert.ok(d.columns.find(c => c.isTarget).name === KI);
  assert.deepEqual(d.model, [{label: "Model target", value: "pKi 12.097"}, {label: "Source value", value: "0.0008 nM"}]);
  assert.equal(d.trains, true);
  const bad = detail(row({smiles: null, structure_error: "not a valid SMILES", target: null, training_eligible: false,
    exclusions: ["invalid_structure", "missing_target"]}), meta());
  assert.deepEqual(bad.reasons, ["the structure did not parse", "the target is blank"]);
  assert.equal(bad.structureError, "not a valid SMILES");
  assert.deepEqual(detail(row({censored: true, target: null, qualifier: ">", target_source: ">5", training_eligible: false,
    exclusions: ["censored"]}), meta()).model.map(m => m.label), ["Model target", "Source value", "Qualifier"]);
});

// ---- the query ----

test("the first query is source order, fifty at a time", () => {
  assert.deepEqual(newQuery(), {offset: 0, limit: 50, sort: {key: "row_id", dir: "asc"}, filters: []});
});

test("sorting by a column again reverses it; a different column starts ascending", () => {
  let q = newQuery();
  q = withSort(q, "c4");
  assert.deepEqual(q.sort, {key: "c4", dir: "asc"});
  q = withSort(q, "c4");
  assert.deepEqual(q.sort, {key: "c4", dir: "desc"});
  q = withSort(q, "c0");
  assert.deepEqual(q.sort, {key: "c0", dir: "asc"});
  assert.deepEqual(withSort(q, "c0", "desc").sort, {key: "c0", dir: "desc"});
});

test("changing the sort, a filter or the page size goes back to the first page", () => {
  const far = withOffset(newQuery(), 150);
  assert.equal(far.offset, 150);
  assert.equal(withSort(far, "c4").offset, 0);
  assert.equal(withFilter(far, {key: "c4", op: "gt", value: 1}).offset, 0);
  assert.equal(withoutFilter(withFilter(far, {key: "c4", op: "gt", value: 1}), 0).offset, 0);
  assert.equal(withLimit(far, 25).offset, 0);
  assert.equal(withOffset(far, 200).offset, 200, "paging itself keeps the offset it was given");
});

test("a query never asks for more than a hundred rows or a negative offset", () => {
  assert.equal(withLimit(newQuery(), 5000).limit, MAX_LIMIT);
  assert.equal(withLimit(newQuery(), 0).limit, 50);
  assert.equal(withLimit(newQuery(), "abc").limit, 50);
  assert.equal(withOffset(newQuery(), -10).offset, 0);
});

test("filters are added and removed without disturbing the others", () => {
  let q = withFilter(newQuery(), {key: "a", op: "eq", value: 1});
  q = withFilter(q, {key: "b", op: "missing", value: true});
  assert.deepEqual(withoutFilter(q, 0).filters, [{key: "b", op: "missing", value: true}]);
  assert.equal(q.filters.length, 2, "the query it came from is unchanged");
});

test("the request body names the dataset and carries the whole query", () => {
  const q = withSort(withFilter(newQuery(), {key: "c4", op: "gte", value: 5}), "c4", "desc");
  assert.deepEqual(queryBody(q, "ds-1"), {dataset_id: "ds-1", offset: 0, limit: 50,
    sort: {key: "c4", dir: "desc"}, filters: [{key: "c4", op: "gte", value: 5}]});
});

test("paging arithmetic", () => {
  assert.deepEqual(pageInfo(106, 0, 50), {from: 1, to: 50, total: 106, prev: null, next: 50, page: 1, pages: 3});
  assert.deepEqual(pageInfo(106, 50, 50), {from: 51, to: 100, total: 106, prev: 0, next: 100, page: 2, pages: 3});
  assert.deepEqual(pageInfo(106, 100, 50), {from: 101, to: 106, total: 106, prev: 50, next: null, page: 3, pages: 3});
  assert.deepEqual(pageInfo(106, 5000, 50).from, 101, "an offset past the end shows the last page");
  assert.deepEqual(pageInfo(0, 0, 50), {from: 0, to: 0, total: 0, prev: null, next: null, page: 0, pages: 0});
  assert.equal(pageInfo(100, 0, 100).next, null);
  assert.equal(pageInfo(101, 0, 100).next, 100);
});

const SCHEMA = [{key: "c0", label: "Name", type: "text"}, {key: "c4", label: "Ki", type: "number"}];
const OPS = {number: ["eq", "lt", "lte", "gt", "gte", "missing"], text: ["contains", "eq", "missing"]};

test("a filter is parsed from what was typed, and a bad number is said at once", () => {
  assert.deepEqual(parseFilter(SCHEMA[1], "gte", " 5.5 "), {filter: {key: "c4", op: "gte", value: 5.5}});
  assert.deepEqual(parseFilter(SCHEMA[1], "missing", ""), {filter: {key: "c4", op: "missing", value: true}});
  assert.deepEqual(parseFilter(SCHEMA[0], "contains", " abc "), {filter: {key: "c0", op: "contains", value: "abc"}});
  assert.match(parseFilter(SCHEMA[1], "eq", "abc").error, /not a number/);
  assert.match(parseFilter(SCHEMA[1], "eq", "1_000").error, /not a number/);
  assert.match(parseFilter(SCHEMA[0], "contains", "  ").error, /Type the text/);
  assert.match(parseFilter(null, "eq", "1").error, /Choose a column/);
});

test("operators come from the server's schema, by type", () => {
  assert.deepEqual(operatorsFor(SCHEMA[0], OPS), ["contains", "eq", "missing"]);
  assert.deepEqual(operatorsFor(null, OPS), []);
  assert.equal(filterText({key: "c4", op: "gte", value: 5}, SCHEMA), "Ki ≥ 5");
  assert.equal(filterText({key: "c0", op: "missing", value: true}, SCHEMA), "Name is missing");
});

test("only the newest request may change what is on screen", () => {
  const latest = makeLatest();
  const first = latest.issue(), second = latest.issue();
  assert.equal(latest.current(first), false, "a slow answer to an old query is ignored");
  assert.equal(latest.current(second), true);
  const third = latest.issue();
  assert.equal(latest.current(second), false);
  assert.equal(latest.current(third), true);
});

// ---- where a dataset lives ----

test("a dataset is addressed by its id under /ligands", () => {
  assert.equal(LIGANDS, "/ligands");
  assert.equal(ligandsPath("ds-0123456789ab"), "/ligands/ds-0123456789ab");
  assert.equal(ligandsPath(null), "/ligands");
  assert.deepEqual(datasetFromPath("/ligands/ds-0123456789ab"), {id: "ds-0123456789ab"});
  assert.deepEqual(datasetFromPath("/ligands/ds-0123456789ab/"), {id: "ds-0123456789ab"});
});

test("the page with no dataset open has no id, and a path that is not an id is said to be wrong", () => {
  for (const p of ["/ligands", "/ligands/", "/", "", null]) assert.deepEqual(datasetFromPath(p), {id: null}, String(p));
  for (const bad of ["abc", "ds-xyz", "DS-0123456789AB", "ds-0123456789abc"])
    assert.match(datasetFromPath("/ligands/" + bad).error, /is not a dataset id/, bad);
  assert.deepEqual(datasetFromPath("/ligands/ds-0123456789ab/extra"), {id: null}, "not a path this page owns");
});

test("the ligands page reads CSV, plain or gzipped, whatever the case", () => {
  for (const ok of ["a.csv", "A.CSV", "export.2026.csv.gz", "x y.csv"]) assert.equal(isCsvName(ok), true, ok);
  for (const no of ["a.pdb", "a.csv.txt", "csv", "", null, "a.csvx"]) assert.equal(isCsvName(no), false, String(no));
});
