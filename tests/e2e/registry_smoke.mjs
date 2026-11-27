// The experiment registry on the Ligands page, in a browser, against the real backend:
// upload -> train (the CI search) -> register -> the table -> open one -> rename and a note
// -> a restart keeps it -> a retry of the registration -> confirmed delete (and Cancel keeps it).
//
//   npm install                                  # playwright-core, once
//   node tests/e2e/registry_smoke.mjs [OUTDIR]
//
// Starts its own server (python serve.py, a free port, fresh dataset and registry directories),
// restarts it once on the same directories, and stops it. PYTHON picks the interpreter that has
// drughunter installed; CHROME_BIN the browser. Exits non-zero if anything fails or the page logged
// an error.
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
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dh-registry-smoke-"));
const CDD = path.join(ROOT, "tests/ligandbench/data/cdd_export_hiv1_protease.csv");

const freePort = () => new Promise(res => {
  const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
let server = null, BASE = "";
async function start() {
  const port = await freePort();
  server = spawn(process.env.PYTHON || "python", ["serve.py", String(port), "--no-open"], {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, PV_DATASET_DIR: path.join(tmp, "datasets"), PV_PROJECT_DIR: path.join(tmp, "projects"), PV_EXPERIMENT_DIR: path.join(tmp, "experiments") },
  });
  BASE = `http://127.0.0.1:${port}`;
  for (let i = 0; ; i++) {
    try { if ((await fetch(BASE + "/api/status")).ok) return; } catch {}
    if (i > 240) { server.kill(); throw new Error("the server did not start"); }
    await new Promise(r => setTimeout(r, 500));
  }
}
const stop = () => new Promise(r => { server.once("exit", r); server.kill(); });
await start();

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width: 1400, height: 1200 }, colorScheme: "light" });
const page = await context.newPage();
const problems = [], failures = [], external = [];
page.on("console", m => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) problems.push(m.text()); });
page.on("pageerror", e => problems.push("pageerror: " + e.message));
page.on("response", r => { const u = r.url(); if (r.status() >= 400 && !u.endsWith("/favicon.ico") && !u.includes("/api/dataset?")) problems.push(r.status() + " " + u); });
await context.route(u => !u.href.startsWith("http://127.0.0.1"), route => {
  if (!/(^|\.)(googleapis|gstatic)\.com$/.test(new URL(route.request().url()).hostname)) external.push(route.request().url());
  return route.abort();
});
const check = (name, ok, detail = "") => { if (!ok) failures.push(name + (detail ? " -- " + detail : "")); console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + detail)); };
const tc = sel => page.locator(sel).first().textContent();
const shot = async (name, sel) => { if (OUT) await page.locator(sel).screenshot({ path: path.join(OUT, "registry-" + name + ".png") }); };
const hasText = (sel, t, ms = 30000) => page.waitForFunction(([s, x]) => (document.querySelector(s) || {}).textContent?.includes(x), [sel, t], { timeout: ms });
const listed = () => fetch(BASE + "/api/experiments?all=true").then(r => r.json());

console.log("the registry panel");
await page.goto(BASE + "/ligands?profile=tiny");
await page.waitForFunction(() => document.getElementById("version").textContent.startsWith("v"), null, { timeout: 60000 });
await hasText("#reg-body", "Nothing is registered yet");
check("an empty registry says so, with no table", (await page.locator("#reg-body table").count()) === 0);

console.log("train -> register");
await page.setInputFiles("#ds-file", CDD);
await page.waitForSelector("#datadlg[open]");
await page.selectOption("#dd-target", "4");
await page.check("input[name=dd-kind][value=regression]");
await page.selectOption("#dd-qualifier", "5");
await page.selectOption("#dd-units", "u:nM");
await page.check("input[name=dd-transform][value=pki]");
await page.click("#dd-go");
await page.waitForFunction(() => !document.getElementById("datadlg").open, null, { timeout: 60000 });
await page.waitForSelector("#trn-panel:not([hidden])");
check("a run that has not finished offers no Register", (await page.locator("#trn-register .reg-name").count()) === 0);
await page.click("#trn-go");
await page.waitForSelector("#trn-run-id", { timeout: 120000 });
await page.waitForSelector("#trn-register .reg-name");
const runId = (await tc("#trn-run-id")).trim();
check("the finished run offers a name to register under, suggested from its target", (await page.inputValue("#trn-register .reg-name")).includes("Ki"));
await page.fill("#trn-register .reg-name", "");
check("an empty name cannot be sent", await page.locator("#trn-register .reg-go").isDisabled());
await page.fill("#trn-register .reg-name", "HIV protease Ki");
await shot("register", "#trn-register");
await page.click("#trn-register .reg-go");
await hasText("#trn-register .pdbq-msg", "Registered as “HIV protease Ki”");
check("registering says it worked and locks the control", await page.locator("#trn-register .reg-go").isDisabled());
await page.waitForSelector("#reg-body table");
check("the table has the experiment, with the name, model, a local initiator and the target",
  await (async () => { const t = await tc("#reg-body tbody tr"); return t.includes("HIV protease Ki") && t.includes("xgboost") && t.includes("Ki") && t.includes("regression") && t.includes("pKi"); })());
check("the headline metric is labelled as RMSE of the scaffold test", (await tc("#reg-body tbody tr")).includes("RMSE (scaffold test)"));
check("it is Ready", (await tc("#reg-body tbody tr .tstate")).trim() === "Ready");
check("with one experiment, metric sorting is off with a reason", await page.locator("[data-sort=headline]").isDisabled());
const recs = (await listed()).experiments;
check("the server has exactly one record, for that run", recs.length === 1 && recs[0].source_run_id === runId);

console.log("open, rename, annotate");
await page.click("[data-open]");
await page.waitForSelector("#reg-d-name");
const det = await tc("#reg-detail");
check("the detail shows both strategies, the production model, the target, provenance and artifact path",
  ["Both evaluation strategies", "Scaffold", "Random", "Selected production model", "Target and features", "Provenance and artifacts", "<registry>/artifacts/"].every(s => det.includes(s)), det.slice(0, 300));
check("a healthy record is not called damaged", !det.includes("Files missing") && !det.includes("cannot be used") && (await tc("#reg-detail .tstate")).trim() === "Ready");
check("the feature columns are summarised, not dumped", !det.includes("morgan_0001"));
check("repeat metrics are labelled as evaluating the procedure, not the final model", det.includes("not an independent test of the final model"));
check("it says metrics and configuration cannot be edited", det.includes("cannot be edited"));
check("the detail never prints the server's absolute directory", !det.includes(tmp));
await page.fill("#reg-d-name", "Renamed model");
await page.click("[data-act=add-note]");
await page.fill(".note-k", "purpose");
await page.fill(".note-v", "smoke test");
await page.click("[data-act=save]");
await hasText("#reg-d-msg", "Saved");
await hasText("#reg-body", "Renamed model");
const after = (await listed()).experiments[0];
check("rename and the note reached the server; the metrics did not change",
  after.name === "Renamed model" && after.annotations.purpose === "smoke test" && after.headline.value === recs[0].headline.value);
await shot("detail", "#reg-panel");

console.log("a repeat registration of the same run");
check("registering the same run again does not make a second record", await (async () => {
  const r = await fetch(BASE + "/api/experiments", { method: "POST", headers: { "content-type": "application/json", origin: BASE }, body: JSON.stringify({ run_id: runId, name: "another" }) });
  const j = await r.json();
  return r.status === 200 && j.id === after.id && j.name === "Renamed model" && (await listed()).experiments.length === 1;
})());

console.log("a restart");
await stop();
await start();
await page.goto(BASE + "/ligands?profile=tiny");
await page.waitForSelector("#reg-body table", { timeout: 60000 });
check("the record is there after a restart, with its new name", (await tc("#reg-body tbody tr")).includes("Renamed model"));
await page.click("[data-open]");
await page.waitForSelector("#reg-d-name");
check("its notes are there too", (await page.inputValue(".note-v")) === "smoke test");

console.log("delete");
await page.click("[data-act=delete]");
await page.waitForSelector("#regdlg[open]");
const ask = await tc("#regdlg");
check("the confirmation names the experiment and says metadata, model and snapshot go for good",
  ask.includes("Renamed model") && ask.includes("permanently") && ask.includes("metadata") && ask.includes("model") && ask.includes("snapshot"));
await shot("delete", "#regdlg");
await page.click("#rd-cancel");
await page.waitForFunction(() => !document.getElementById("regdlg").open);
check("Cancel keeps it", (await listed()).experiments.length === 1);
await page.click("[data-act=delete]");
await page.waitForSelector("#regdlg[open]");
await page.click("#rd-go");
await page.waitForFunction(() => !document.getElementById("regdlg").open, null, { timeout: 30000 });
await hasText("#reg-body", "Nothing is registered yet");
check("the confirmed delete removed the record and closed the detail", (await listed()).experiments.length === 0 && await page.locator("#reg-detail").isHidden());
const art = path.join(tmp, "experiments", "artifacts");
check("its files are gone from disk", !fs.existsSync(art) || fs.readdirSync(art).length === 0);

check("the page asked for nothing outside its own server", external.length === 0, external.join(", "));
await browser.close();
await stop();
fs.rmSync(tmp, { recursive: true, force: true });
if (problems.length) console.log("page problems:\n  " + problems.join("\n  "));
if (failures.length || problems.length) { console.log(`\n${failures.length} check(s) failed, ${problems.length} page problem(s)`); process.exit(1); }
console.log("\nall checks passed");
