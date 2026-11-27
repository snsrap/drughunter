// The registry's own logic -- naming, registration outcomes, lifecycle health, the
// table's deterministic sorting and its refusal to rank unlike scores, rename bodies
// and delete wording: run with `npm test`.
import {test} from "node:test";
import assert from "node:assert/strict";

import {
  validateName, suggestName, idleRegistration, registering, registered, registrationError, registrationView,
  health, headlineCell, groupsOf, metricSortable, sortRows, updateBody, normalizeNotes,
  deleteConfirmation, deleteOutcome, provenanceRows, schemaRows, schemaValue, targetText, REPEAT_NOTE,
} from "../../proteinviewer/app/js/model/registry.js";

const H = (value, group = "regression|pKi|nM|scaffold|rmse", extra = {}) =>
  Object.assign({metric: "rmse", strategy: "scaffold", value, n_defined: 5, n_total: 5, status: "ok", group}, extra);
const E = (id, over = {}) => Object.assign({
  id, name: "model " + id, state: "ready", selectable: true, model_family: "xgboost", initiator: "sina",
  created_utc: "2026-11-01T10:00:00Z", dataset_id: "ds-1", task: "regression", target_column: "pKi",
  target: {units: "nM", transform: "pki"}, production_strategy: "scaffold", headline: H(0.7),
}, over);

test("a name is 1-200 characters on one line", () => {
  assert.deepEqual(validateName("  my model "), {ok: true, value: "my model"});
  for (const bad of ["", "   ", null, "a\nb", "x".repeat(201)]) assert.equal(validateName(bad).ok, false);
  assert.equal(validateName("x".repeat(200)).ok, true);
});

test("a suggested name uses the target and the run's date, not its id", () => {
  assert.equal(suggestName({model_family: "xgboost", target: {column: "pKi"}, created_utc: "2026-11-01T10:00:00Z"}), "xgboost pKi 2026-11-01");
  assert.equal(suggestName(null), "xgboost model");
});

test("registration: success, an idempotent repeat that keeps the first name, and each refusal", () => {
  let r = registering(idleRegistration("run1"));
  assert.equal(registrationView(r).canSubmit, false);
  const ok = registered(r, {id: "e1", name: "first"}, "first");
  assert.equal(registrationView(ok).kind, "registered");
  assert.match(registrationView(ok).text, /Registered as “first”/);
  const again = registered(r, {id: "e1", name: "first"}, "second");
  assert.match(registrationView(again).text, /Already registered as “first”.*does not rename/);

  const expired = registrationError(r, {status: 404, message: "no such run"});
  assert.match(registrationView(expired).text, /no longer available/);
  assert.equal(registrationView(expired).canSubmit, false);
  const running = registrationError(r, {status: 409, message: "the run is running"});
  assert.match(registrationView(running).text, /cannot be registered/);
  const down = registrationError(r, {status: 503, message: "locked"});
  assert.equal(registrationView(down).kind, "error");
  const failed = registrationError(r, {status: 500, message: "copy failed"});
  assert.equal(registrationView(failed).canSubmit, true);   // a failed copy leaves the run registrable
  // an answer that arrives when nothing was in flight changes nothing
  const idle = idleRegistration("run1");
  assert.equal(registered(idle, {id: "x", name: "n"}, "n"), idle);
  assert.equal(registrationError(idle, {status: 500}), idle);
});

test("only a ready record whose files are present is healthy", () => {
  assert.equal(health(E("a")).ok, true);
  assert.equal(health(E("a", {selectable: false})).ok, false);
  assert.match(health(E("a", {selectable: false})).label, /missing/);
  const d = health(E("a", {state: "deleting", selectable: false}));
  assert.equal(d.ok, false); assert.match(d.reason, /retried/);
  assert.equal(health(E("a", {state: "failed", selectable: false, state_detail: "files gone"})).reason.includes("files gone"), true);
  assert.equal(health(E("a", {state: "pending", selectable: false})).ok, false);
});

test("a headline is a labelled number, and says when it is a fallback, undefined or unavailable", () => {
  const ok = headlineCell(H(0.71234), {task: "regression"});
  assert.equal(ok.text, "0.7123"); assert.match(ok.label, /RMSE \(scaffold test\)/);
  const fb = headlineCell(H(0.5, "g", {strategy: "random"}), {});
  assert.equal(fb.fallback, true); assert.match(fb.label, /random test/); assert.match(fb.title, /scaffold was not available/);
  const und = headlineCell(H(null, "g", {status: "undefined", n_defined: 0}), {});
  assert.equal(und.text, "undefined"); assert.match(und.title, /0 of 5/);
  assert.equal(headlineCell(H(null, null, {status: "unavailable", strategy: null}), {}).text, "unavailable");
  assert.equal(headlineCell(null, {}).text, "—");
  const acc = headlineCell({metric: "balanced_accuracy", strategy: "scaffold", value: 0.8, n_defined: 5, n_total: 5, status: "ok", group: "x"}, {task: "classification", classes: [0, 1]});
  assert.match(acc.label, /balanced accuracy/);
});

test("metric sorting is allowed only within one comparable group", () => {
  assert.equal(metricSortable([E("a"), E("b")]).ok, true);
  assert.equal(metricSortable([E("a")]).ok, false);
  const nm = E("a"), pki = E("b", {headline: H(0.4, "regression|pKi||scaffold|rmse")});
  assert.equal(metricSortable([nm, pki]).ok, false);
  assert.match(metricSortable([nm, pki]).reason, /not ranked against each other/);
  const rand = E("c", {headline: H(0.4, "regression|pKi|nM|random|rmse", {strategy: "random"})});
  assert.equal(metricSortable([nm, rand]).ok, false);          // a different strategy is a different score
  const none = E("d", {headline: H(null, null, {status: "unavailable", strategy: null})});
  assert.equal(metricSortable([nm, none]).ok, false);
  assert.deepEqual(groupsOf([nm, nm]), [nm.headline.group]);
});

test("ordinary columns break ties by id, so the order is the same on every load", () => {
  const rows = [E("c", {name: "same"}), E("a", {name: "same"}), E("b", {name: "same"})];
  assert.deepEqual(sortRows(rows, "name", "asc", {ok: false}).map(e => e.id), ["a", "b", "c"]);
  assert.deepEqual(sortRows(rows, "name", "desc", {ok: false}).map(e => e.id), ["a", "b", "c"]);
  assert.deepEqual(sortRows(rows.slice().reverse(), "name", "asc", {ok: false}).map(e => e.id), ["a", "b", "c"]);
  const named = [E("1", {name: "Zeta"}), E("2", {name: "alpha"})];
  assert.deepEqual(sortRows(named, "name", "asc", {ok: false}).map(e => e.id), ["2", "1"]);   // case-blind
});

test("sorting by the metric works within a group and is refused across groups", () => {
  const rows = [E("a", {headline: H(0.9)}), E("b", {headline: H(0.3)}), E("c", {headline: H(null, undefined, {status: "undefined"})})];
  const s = metricSortable(rows);
  assert.deepEqual(sortRows(rows, "headline", "asc", s).map(e => e.id), ["b", "a", "c"]);   // missing last
  assert.deepEqual(sortRows(rows, "headline", "desc", s).map(e => e.id), ["a", "b", "c"]);
  const mixed = [E("a", {headline: H(0.9)}), E("b", {headline: H(0.3, "other")})];
  const refused = sortRows(mixed, "headline", "asc", metricSortable(mixed));
  assert.deepEqual(refused.map(e => e.id), sortRows(mixed, "created_utc", "asc", {ok: false}).map(e => e.id));
});

test("an edit sends only what changed and never a scientific field", () => {
  const exp = E("a", {annotations: {note: "x"}});
  assert.deepEqual(updateBody(exp, {name: "renamed", notes: [{key: "note", value: "x"}]}), {ok: true, body: {id: "a", name: "renamed"}});
  assert.deepEqual(updateBody(exp, {name: exp.name, notes: [{key: "note", value: "y"}]}).body, {id: "a", annotations: {note: "y"}});
  assert.equal(updateBody(exp, {name: exp.name, notes: [{key: "note", value: "x"}]}).unchanged, true);
  assert.equal(updateBody(exp, {name: "  ", notes: []}).ok, false);
  const body = updateBody(exp, {name: "n", notes: []}).body;
  assert.deepEqual(Object.keys(body).sort(), ["annotations", "id", "name"]);
});

test("notes are checked as the server checks them", () => {
  assert.deepEqual(normalizeNotes([{key: " a ", value: "1"}, {key: "", value: ""}]), {ok: true, value: {a: "1"}});
  assert.equal(normalizeNotes([{key: "", value: "x"}]).ok, false);
  assert.equal(normalizeNotes([{key: "a", value: "1"}, {key: "a", value: "2"}]).ok, false);
  assert.equal(normalizeNotes([{key: "k".repeat(65), value: ""}]).ok, false);
  assert.equal(normalizeNotes([{key: "k", value: "v".repeat(2001)}]).ok, false);
  assert.equal(normalizeNotes(Array.from({length: 33}, (_, i) => ({key: "k" + i, value: ""}))).ok, false);
});

test("the delete confirmation names the experiment and says what is lost for good", () => {
  const c = deleteConfirmation(E("a", {name: "my model"}));
  assert.match(c.title, /my model/);
  assert.match(c.body, /permanently/); assert.match(c.body, /metadata/); assert.match(c.body, /model/); assert.match(c.body, /snapshot/);
});

test("a partial deletion is never reported as a success, and stays retryable", () => {
  assert.equal(deleteOutcome({complete: true, row: "removed"}).done, true);
  const busy = deleteOutcome(null, Object.assign(new Error("in use"), {status: 409, report: {artifact_dir: "skipped", row: "retained", complete: false}}));
  assert.equal(busy.done, false); assert.equal(busy.retryable, true); assert.match(busy.message, /still in use/);
  assert.deepEqual(busy.parts.map(p => p.status), ["skipped", "retained"]);
  const half = deleteOutcome(null, Object.assign(new Error("EPERM"), {status: 500, report: {artifact_dir: "failed", staging_dir: "already_absent", row: "retained"}}));
  assert.equal(half.done, false); assert.equal(half.parts.find(p => p.label === "Model and artifacts").bad, true);
  assert.match(half.note, /Delete it again/);
  assert.equal(deleteOutcome({complete: false, row: "retained"}).done, false);
});

test("the detail names where the files are without a server path, and the repeat caveat is fixed wording", () => {
  const rows = provenanceRows({id: "e1", initiator: "sina", created_utc: "t", source_run_id: "r", dataset_id: "ds-1", dataset_hash: "ab".repeat(32),
    artifact_path: "artifacts/e1", versions: {rdkit: "2025.03.1", xgboost: "3.2.0"}});
  assert.ok(rows.some(r => r[0] === "Artifacts" && r[1] === "<registry>/artifacts/e1"));
  assert.ok(rows.some(r => r[0] === "rdkit"));
  assert.ok(!rows.some(r => String(r[1]).startsWith("/")));
  const s = schemaRows({task: "classification", target_column: "active", target: {classes: [0, 1], positive_class: 1}, feature_schema: {radius: 2, n_bits: 2048}});
  assert.ok(s.some(r => r[0] === "Classes" && /positive: 1/.test(r[1])));
  assert.ok(s.some(r => r[0] === "feature n_bits" && r[1] === "2048"));
  assert.match(REPEAT_NOTE, /not an independent test of the final model/);
  assert.equal(targetText(E("a")), "pKi · regression · nM");
});

test("a long list of feature columns is shown as its count and ends, not in full", () => {
  const cols = Array.from({length: 2048}, (_, i) => "morgan_" + String(i).padStart(4, "0"));
  assert.equal(schemaValue(cols), "2048 items: morgan_0000 … morgan_2047");
  assert.equal(schemaValue(["a", "b"]), "a, b");
  assert.equal(schemaValue(2), "2");
  const rows = schemaRows({task: "regression", feature_schema: {columns: cols}});
  assert.ok(rows.every(r => String(r[1]).length < 80));
});
