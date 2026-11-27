// Applying a registered model to a dataset, in a browser, against the real backend: train and
// register a model on a dataset with a target, open one with no target, pick the model, "Predict
// entire dataset", and sort, filter and inspect by the prediction; a dataset opened while a job
// is being polled is not touched by it; the predictions survive a reload, and outlive a deleted
// model, labelled.
//
//   npm install                                  # playwright-core, once
//   node tests/e2e/predict_smoke.mjs [OUTDIR]
//
// Starts its own server (python serve.py, a free port, fresh dataset and registry directories)
// and stops it. PYTHON picks the interpreter that has drughunter installed; CHROME_BIN the browser.
// Exits non-zero if anything fails or the page logged an error.
import { chromium } from "playwright-core";
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = process.argv[2];
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dh-predict-smoke-"));
const CDD = path.join(ROOT, "tests/ligandbench/data/cdd_export_hiv1_protease.csv");

const freePort = () => new Promise(res => {
  const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
const port = await freePort();
const server = spawn(process.env.PYTHON || "python", ["serve.py", String(port), "--no-open"], {
  cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PV_DATASET_DIR: path.join(tmp, "datasets"), PV_PROJECT_DIR: path.join(tmp, "projects"), PV_EXPERIMENT_DIR: path.join(tmp, "experiments") },
});
const BASE = `http://127.0.0.1:${port}`;
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE + "/api/status")).ok) break; } catch {}
  if (i > 240) { server.kill(); throw new Error("the server did not start"); }
  await new Promise(r => setTimeout(r, 500));
}

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width: 1400, height: 1300 }, colorScheme: "light" });
const page = await context.newPage();
const problems = [], failures = [], external = [], posts = [];
page.on("console", m => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) problems.push(m.text()); });
page.on("pageerror", e => problems.push("pageerror: " + e.message));
page.on("response", r => { const u = r.url(); if (r.status() >= 400 && !u.endsWith("/favicon.ico") && !u.includes("/api/dataset?")) problems.push(r.status() + " " + u); });
page.on("request", r => { if (r.method() === "POST" && /\/api\/dataset\/(predict|rows)$/.test(new URL(r.url()).pathname)) posts.push({ path: new URL(r.url()).pathname, body: JSON.parse(r.postData() || "{}") }); });
await context.route(u => !u.href.startsWith("http://127.0.0.1"), route => {
  if (!/(^|\.)(googleapis|gstatic)\.com$/.test(new URL(route.request().url()).hostname)) external.push(route.request().url());
  return route.abort();
});
const check = (name, ok, detail = "") => { if (!ok) failures.push(name + (detail ? " -- " + detail : "")); console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + detail)); };
const tc = sel => page.locator(sel).first().textContent();
const shot = async (name, sel) => { if (OUT) await page.locator(sel).screenshot({ path: path.join(OUT, "predict-" + name + ".png") }); };
const hasText = (sel, t, ms = 60000) => page.waitForFunction(([s, x]) => (document.querySelector(s) || {}).textContent?.includes(x), [sel, t], { timeout: ms });
const sortOptions = () => page.locator("#mol-sort-key option").allTextContents();
const api = (p, body) => fetch(BASE + p, body ? { method: "POST", headers: { "content-type": "application/json", origin: BASE }, body: JSON.stringify(body) } : {}).then(r => r.json());

async function openCsv(choices) {
  await page.setInputFiles("#ds-file", CDD);
  await page.waitForSelector("#datadlg[open]");
  await choices();
  await page.click("#dd-go");
  await page.waitForFunction(() => !document.getElementById("datadlg").open, null, { timeout: 60000 });
  await page.waitForSelector("#mol-grid .mcard", { timeout: 60000 });
  return page.url().split("/").pop();
}
const withTarget = async () => {
  await page.selectOption("#dd-target", "4");
  await page.check("input[name=dd-kind][value=regression]");
  await page.selectOption("#dd-qualifier", "5");
  await page.selectOption("#dd-units", "u:nM");
  await page.check("input[name=dd-transform][value=pki]");
};
const noTarget = async () => { await page.selectOption("#dd-target", "-1"); };

console.log("train and register a model");
await page.goto(BASE + "/ligands?profile=tiny");
await page.waitForFunction(() => document.getElementById("version").textContent.startsWith("v"), null, { timeout: 60000 });
await openCsv(withTarget);
await page.waitForSelector("#trn-panel:not([hidden])");
await page.click("#trn-go");
await page.waitForSelector("#trn-run-id", { timeout: 120000 });
await page.fill("#trn-register .reg-name", "HIV protease Ki");
await page.click("#trn-register .reg-go");
await hasText("#trn-register .pdbq-msg", "Registered as");

console.log("a dataset with no target");
const B = await openCsv(noTarget);
check("it has no target, and a selector for registered models", await page.locator("#pr-panel").isVisible() && (await tc("#mol-n")).includes("106"));
await page.waitForFunction(() => document.querySelectorAll("#pr-model option").length > 1);
const opt = (await page.locator("#pr-model option").allTextContents()).find(t => t.includes("HIV protease Ki")) || "";
check("the model is listed with its name, id, family, target, units and labelled metric",
  ["HIV protease Ki", "xgboost", "pKi", "regression", "RMSE (scaffold test)"].every(s => opt.includes(s)), opt);
check("nothing is chosen for the user, and Predict is off until a model is", (await page.inputValue("#pr-model")) === "" && await page.locator("#pr-go").isDisabled());
check("the action says it is the whole dataset, and a prediction is not a test", (await tc("#pr-go")) === "Predict entire dataset" && (await tc("#pr-note")).includes("not a test of the model"));

console.log("a job followed while another dataset is opened");
const held = [];
await page.route(u => u.pathname === "/api/dataset/predict" && !u.search.includes("zzz"), async route => {
  if (route.request().method() === "GET") held.push(route); else await route.continue();
});
await page.selectOption("#pr-model", { label: opt });
await page.selectOption("#mol-size", "25");
await page.click("#pr-go");
await page.waitForFunction(() => /Starting|queued|running|finishing/i.test(document.getElementById("pr-status").textContent));
const body = posts.find(p => p.path === "/api/dataset/predict").body;
check("the request names the dataset and the model and nothing about the page (no filter, sort or page)",
  Object.keys(body).sort().join() === "dataset_id,experiment_id" && body.dataset_id === B, JSON.stringify(body));
while (!held.length) await page.waitForTimeout(50);
const C = await openCsv(noTarget);                       // another dataset takes the panel; B's poll is still out
check("the new dataset is another id", C !== B);
for (const r of held.splice(0)) await r.continue();      // B's job answers now
await page.waitForTimeout(2500);
check("the answer for B painted nothing on C: no prediction column, no status, no kept set",
  !(await sortOptions()).some(t => t.includes("predicted")) && (await tc("#pr-status")).trim() === "" && (await page.locator("#pr-sets li").count()) === 0);
await page.unroute(() => true).catch(() => {});

console.log("the predictions are B's, however it is opened");
await page.goto(BASE + "/ligands/" + B + "?profile=tiny");
await page.waitForSelector("#mol-grid .mcard", { timeout: 60000 });
await page.waitForFunction(() => document.querySelectorAll("#pr-sets li").length === 1, null, { timeout: 30000 }).catch(() => {});
const kept = await api("/api/dataset/predictions?dataset_id=" + B);
check("the server kept one set for B, every row predicted but the none unreadable", kept.predictions.length === 1 && kept.predictions[0].n_rows === 106 && kept.predictions[0].n_failed === 0, JSON.stringify(kept.predictions.map(p => [p.n_rows, p.n_failed])));
const opts = await sortOptions();
const predOpt = opts.find(t => t.includes("predicted"));
check("the column is in the sort list, named for the model with its units", !!predOpt && predOpt.includes("HIV protease Ki") && predOpt.includes("pKi"), opts.join(" | "));
check("the kept set is listed and can be removed", (await tc("#pr-sets")).includes("HIV protease Ki") && (await tc("#pr-sets")).includes("every row predicted"));
check("a card shows the model's name beside its prediction", (await page.locator(".mpred").count()) > 0 && (await tc(".mpred")).includes("HIV protease Ki"));

console.log("sort and filter by a prediction, over every row");
const key = kept.schema.find(c => c.kind === "prediction").key;
await page.selectOption("#mol-size", "25");
await page.selectOption("#mol-sort-key", key);
await page.click("#mol-sort-dir");                      // asc -> desc
await page.waitForFunction(() => document.getElementById("mol-sort-dir").textContent.includes("descending"));
await hasText("#mol-range", "1–25 of 106");
const q = posts.filter(p => p.path === "/api/dataset/rows").pop().body;
check("the server was asked to sort by it, across all rows, before it cut a page", q.sort.key === key && q.sort.dir === "desc" && q.limit === 25 && q.dataset_id === B, JSON.stringify(q.sort));
await page.selectOption("#mol-f-key", key);
await page.selectOption("#mol-f-op", "gte");
await page.fill("#mol-f-val", "-1000");
await page.click("#mol-f-add");
await hasText("#mol-range", "of 106");
check("filtering by it is the ordinary filter", (await tc("#mol-chips")).includes("≥"));
await page.click("#mol-chips button");
await page.locator("#mol-grid .mcard").first().click();
await page.waitForSelector("#mol-detail table.pred");
const det = await tc("#mol-detail");
check("the detail shows it as a prediction, apart from measurements, with units", det.includes("Predictions (not measurements)") && det.includes("pKi") && det.includes("HIV protease Ki"));
await shot("table", "#mtab");

console.log("a model that is deleted");
const exp = (await api("/api/experiments")).experiments[0];
const gone = await api("/api/experiments/delete", { id: exp.id });
check("the model is deleted", gone.complete === true);
await page.reload();
await page.waitForSelector("#mol-grid .mcard", { timeout: 60000 });
await page.waitForFunction(() => document.querySelectorAll("#pr-sets li").length === 1);
check("its predictions remain, labelled as from a deleted model", (await tc("#pr-sets")).includes("source model deleted"));
check("and so does its column, in the sort list", (await sortOptions()).some(t => t.includes("source model deleted")));
check("it can no longer be chosen", (await page.locator("#pr-model option").allTextContents()).every(t => !t.includes("HIV protease Ki")));
await page.click("[data-remove]");
await page.waitForFunction(() => document.querySelectorAll("#pr-sets li").length === 0);
check("removing the predictions removes the column", !(await sortOptions()).some(t => t.includes("predicted")) && (await tc("#pr-notice")).includes("Removed"));

check("the page asked for nothing outside its own server", external.length === 0, external.join(", "));
await browser.close();
server.kill();
fs.rmSync(tmp, { recursive: true, force: true });
if (problems.length) console.log("page problems:\n  " + problems.join("\n  "));
if (failures.length || problems.length) { console.log(`\n${failures.length} check(s) failed, ${problems.length} page problem(s)`); process.exit(1); }
console.log("\nall checks passed");
