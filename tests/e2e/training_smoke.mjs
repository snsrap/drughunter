// The training panel on the Ligands page, in a browser: upload -> open the page ->
// train -> see the run's progress -> its run id and both strategies' metrics side by
// side; and the paths that go wrong: a refused start, a failed job, a production
// strategy with no valid split (a new run with the other one is offered, never taken),
// a run that expired, a result that could not be read, Cancel, a different dataset
// taking the panel over, and a dataset with no target.
//
//   npm install                                  # playwright-core, once
//   node tests/e2e/training_smoke.mjs [OUTDIR]
//   E2E_TRAINING=real node tests/e2e/training_smoke.mjs
//
// E2E_TRAINING picks what answers /api/training:
//   stub (default)  a small node server in this file, which serves the page and proxies every
//                   other request (the dataset routes, the page's files) to a real proteinviewer
//                   server. Its scenarios are picked by the SEED typed into the panel, so the
//                   walkthrough drives only the page: 0 a run that completes, 1 a job that fails,
//                   2 a scaffold-selected run with no valid scaffold split, 3 a refused start,
//                   4 a run that stays running until it is cancelled, 5 a run the server forgets,
//                   6 a completed run whose scaffold strategy is unavailable (random selected),
//                   8 a result that cannot be read the first time.
//   real            the page talks to the real backend, with ?profile=tiny (the CI search). Only the
//                   paths a real server produces on demand are walked: a completed run, Cancel, the
//                   dataset switch and the target-free dataset; the rest are stub-only and skipped.
//
// Starts its own server (python serve.py, on a free port, with a fresh dataset directory) and stops
// it. PYTHON picks the interpreter that has drughunter installed; CHROME_BIN picks the browser,
// otherwise Playwright's own. The page makes no request outside its own server. Exits non-zero if
// anything fails or the page logged an error.
import { chromium } from "playwright-core";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = process.argv[2];
if (OUT) fs.mkdirSync(OUT, { recursive: true });
const MODE = process.env.E2E_TRAINING || "stub";
if (MODE !== "stub" && MODE !== "real") throw new Error("E2E_TRAINING is stub or real, not " + MODE);
const STUB = MODE === "stub";
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dh-training-smoke-"));
const CDD = path.join(ROOT, "tests/ligandbench/data/cdd_export_hiv1_protease.csv");
const SYN = path.join(ROOT, "tests/ligandbench/data/synthetic_classification.csv");

const freePort = () => new Promise(res => {
  const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});

// ---- the real server ---------------------------------------------------------------------------------
const realPort = await freePort();
const server = spawn(process.env.PYTHON || "python", ["serve.py", String(realPort), "--no-open"], {
  cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PV_DATASET_DIR: path.join(tmp, "datasets"), PV_PROJECT_DIR: path.join(tmp, "projects") },
});
const REAL = `http://127.0.0.1:${realPort}`;
for (let i = 0; ; i++) {
  try { if ((await fetch(REAL + "/api/status")).ok) break; } catch {}
  if (i > 240) { server.kill(); throw new Error("the server did not start"); }
  await new Promise(r => setTimeout(r, 500));
}

// ---- the stub: /api/training answered from here, everything else passed through ------------------------
const jobs = new Map();
let serial = 0;
const stubRequests = [];          // what the page asked the stub, for the checks
const json = (res, status, body) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
const TOTAL = 6;

const progress = j => {
  const p = { id: j.id, state: j.state, stage: j.stage, completed: j.completed, total: TOTAL, elapsed: (Date.now() - j.t0) / 1000 };
  if (j.error) p.error = j.error;
  if (j.error_code) p.error_code = j.error_code;
  if (j.unavailable) p.unavailable = j.unavailable;
  return p;
};

// Each poll moves a run one step: running, running, finishing, then its end (what its seed says).
function advance(j) {
  if (["done", "error", "cancelled"].includes(j.state)) return;
  j.polls++;
  const seed = j.body.seed;
  if (seed === 4) { j.state = "running"; j.stage = "tuning"; j.completed = Math.min(2, j.polls - 1); return; }   // stays
  if (j.polls === 1) Object.assign(j, { state: "running", stage: "featurizing", completed: 0 });
  else if (j.polls === 2) Object.assign(j, { state: "running", stage: "tuning", completed: 3 });
  else if (j.polls === 3 && seed !== 1 && !(seed === 2 && j.body.production_strategy === "scaffold"))
    Object.assign(j, { state: "finishing", stage: "writing the run", completed: TOTAL });
  else if (seed === 1) Object.assign(j, { state: "error", error: "ligandbench exited with status 1: <b>boom</b> \"><img src=x onerror=\"window.__xss=1\">" });
  else if (seed === 2 && j.body.production_strategy === "scaffold") Object.assign(j, {
    state: "error", error: "the production strategy has no valid split", error_code: "production_strategy_unavailable",
    unavailable: { repeat: 0, message: "no valid scaffold split <u>x</u>", candidates_tried: 256, n_rows: 102, n_groups: 8,
      group_size_counts: [[60, 1], [6, 7]], largest_groups: [{ key: "c1ccccc1", n_rows: 60, n_structures: 60, class_counts: {} }],
      class_counts: {}, failed_constraints: [{ constraint: "size_tolerance", subset: "test", candidates_violating: 256,
        detail: "the largest group (60 rows) is larger than any subset may hold" }] },
  });
  else Object.assign(j, { state: "done", stage: "done", completed: TOTAL });
}

function runJson(j) {
  const rep = (spear, why) => [0, 1, 2, 3, 4].map(r => ({
    repeat: r, seed: 1000 + r, config: { max_depth: 3, learning_rate: 0.1, n_estimators: 100 }, validation: [],
    test: { rmse: 0.5 + r / 100, mae: 0.4, r2: 0.7, spearman: spear && (r === 1 || r === 3) ? null : 0.8 },
    undefined: spear && (r === 1 || r === 3) ? { spearman: why } : {},
  }));
  const A = (mean, sd, nd = 5) => ({ mean, sd, n_defined: nd, n_total: 5 });
  const lost = "only one value in the test set <i>x</i>";
  const unavailable = j.body.seed === 6;
  return {
    schema: "qsar-run-v1", status: "completed", model_family: "xgboost", seed: j.body.seed, profile: j.body.profile,
    dataset: { id: j.body.dataset_id, content_hash: "0123456789abcdef0123456789", n_rows: 106, n_eligible: 102 },
    target: { column: "Ki", task: "regression", units: "pKi", transform: "pki", classes: null, positive_class: null },
    production: { strategy: j.body.production_strategy, config: { max_depth: 6, learning_rate: 0.05, n_estimators: 300 },
                  mean_validation_loss: 0.5512, n_refit_rows: 102, tie_break: [] },
    strategies: {
      random: { status: "ok", unavailable: null, repeats: rep(true, lost),
        aggregate: { rmse: A(0.5512, 0.0407), mae: A(0.4, 0), r2: A(0.7, 0), spearman: A(0.8, 0, 3) } },
      scaffold: unavailable
        ? { status: "split_unavailable", repeats: [], aggregate: {}, unavailable: { repeat: 0, message: "no valid scaffold split <u>x</u>",
            candidates_tried: 256, n_rows: 102, n_groups: 8, group_size_counts: [[60, 1], [6, 7]], class_counts: {},
            failed_constraints: [{ constraint: "size_tolerance", subset: "test", candidates_violating: 256,
              detail: "the largest group (60 rows) is larger than any subset may hold" }] } }
        : { status: "ok", unavailable: null, repeats: rep(false), aggregate: { rmse: A(0.7234, 0.0912), mae: A(0.5, 0.01), r2: A(0.41, 0.12), spearman: A(0.61, null, 1) } },
    },
  };
}

async function stubTraining(req, res, url, body) {
  stubRequests.push({ method: req.method, path: url.pathname + url.search, body });
  const id = url.searchParams.get("id");
  if (req.method === "POST" && url.pathname === "/api/training") {
    const b = JSON.parse(body || "{}");
    if (b.seed === 3) return json(res, 409, { error: "the training queue is full" });
    const j = { id: "run-" + (++serial), body: b, t0: Date.now(), polls: 0, state: "queued", stage: "", completed: 0, fetches: 0 };
    jobs.set(j.id, j);
    return json(res, 200, progress(j));
  }
  if (req.method === "POST" && url.pathname === "/api/training/cancel") {
    const j = jobs.get(JSON.parse(body || "{}").id);
    if (!j) return json(res, 404, { error: "unknown run" });
    if (["queued", "running"].includes(j.state)) j.state = "cancelled";
    return json(res, 200, progress(j));
  }
  const j = jobs.get(id);
  if (!j) return json(res, 404, { error: "unknown run " + id });
  if (url.pathname === "/api/training/run") {
    if (j.state !== "done") return json(res, 409, { error: "the run is not done" });
    if (j.body.seed === 8 && ++j.fetches === 1) return json(res, 500, { error: "the run directory could not be read" });
    return json(res, 200, runJson(j));
  }
  if (j.body.seed === 5 && j.polls >= 2) { jobs.delete(id); return json(res, 404, { error: "unknown run " + id }); }
  advance(j);
  return json(res, 200, progress(j));
}

let BASE = REAL, stubServer = null;
if (STUB) {
  stubServer = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://stub");
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = Buffer.concat(chunks);
    try {
      if (url.pathname.startsWith("/api/training")) return await stubTraining(req, res, url, body.toString("utf8"));
      const headers = { ...req.headers }; delete headers.host; delete headers.connection; delete headers["content-length"];
      // The server refuses a request from another origin; this one is the page's own, relayed.
      if (headers.origin) headers.origin = REAL;
      if (headers.referer) headers.referer = REAL + new URL(headers.referer).pathname;
      const r = await fetch(REAL + req.url, { method: req.method, headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : body, redirect: "manual" });
      const out = {}; r.headers.forEach((v, k) => { if (!["content-encoding", "content-length", "transfer-encoding", "connection"].includes(k)) out[k] = v; });
      res.writeHead(r.status, out);
      res.end(Buffer.from(await r.arrayBuffer()));
    } catch (e) { json(res, 502, { error: String(e) }); }
  });
  const stubPort = await freePort();
  await new Promise(r => stubServer.listen(stubPort, "127.0.0.1", r));
  BASE = `http://127.0.0.1:${stubPort}`;
}

// ---- the page ------------------------------------------------------------------------------------------
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN || undefined,
  args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const context = await browser.newContext({ viewport: { width: +(process.env.E2E_W || 1400), height: +(process.env.E2E_H || 1100) }, colorScheme: "light" });
const page = await context.newPage();
const problems = [], failures = [], external = [], requests = [];
page.on("console", m => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) problems.push(m.text()); });
page.on("pageerror", e => problems.push("pageerror: " + e.message));
page.on("request", r => { const u = new URL(r.url()); if (u.pathname.startsWith("/api/training")) requests.push({ method: r.method(), path: u.pathname, body: r.postData() }); });
page.on("response", r => {
  const u = r.url();
  // The faults the stub provokes on purpose, and the 404s of looking for what was cancelled.
  const expected = (STUB && u.includes("/api/training")) || u.includes("/api/dataset?");
  if (r.status() >= 400 && !u.endsWith("/favicon.ico") && !expected) problems.push(r.status() + " " + u);
});
// The page asks nothing of anyone but its own server (its web fonts are the one thing it may ask for).
await context.route(u => !u.href.startsWith(BASE), route => {
  const h = new URL(route.request().url()).hostname;
  if (!/(^|\.)(googleapis|gstatic)\.com$/.test(h)) external.push(route.request().url());
  return route.abort();
});

const check = (name, ok, detail = "") => { if (!ok) failures.push(name + (detail ? " -- " + detail : "")); console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + detail)); };
const tc = sel => page.locator(sel).first().textContent();
const shot = async (name, sel = "#trn-panel") => { if (OUT) await page.locator(sel).screenshot({ path: path.join(OUT, "training-" + name + ".png") }); };
const until = (fn, arg, ms = 30000) => page.waitForFunction(fn, arg, { timeout: ms });
const hasText = (sel, t) => until(([s, x]) => (document.querySelector(s) || {}).textContent?.includes(x), [sel, t]);
const polls = () => requests.filter(r => r.method === "GET" && r.path === "/api/training").length;
const posts = () => requests.filter(r => r.method === "POST" && r.path === "/api/training");
const train = async seed => { await page.fill("#trn-seed", String(seed)); await page.click("#trn-go"); };
const settleDone = async () => { await page.waitForSelector("#trn-run-id", { timeout: 60000 }); };
const quiet = async ms => { const n = polls(); await page.waitForTimeout(ms); return polls() === n; };

async function openCsv(file, pick) {
  await page.setInputFiles("#ds-file", file);
  await page.waitForSelector("#datadlg[open]");
  await pick();
  await page.click("#dd-go");
  await page.waitForFunction(() => !document.getElementById("datadlg").open, null, { timeout: 60000 });
}
const cddChoices = async () => {
  await page.selectOption("#dd-target", "4");
  await page.check("input[name=dd-kind][value=regression]");
  await page.selectOption("#dd-qualifier", "5");
  await page.selectOption("#dd-units", "u:nM");
  await page.check("input[name=dd-transform][value=pki]");
};

// ---- before a dataset is open ---------------------------------------------------------------------------
console.log("the training panel (" + MODE + ")");
await page.goto(BASE + "/ligands?profile=tiny");
await page.waitForFunction(() => document.getElementById("version").textContent.startsWith("v"), null, { timeout: 60000 });
check("there is no training panel until a dataset is open", await page.locator("#trn-panel").isHidden());

// ---- a ready regression dataset ---------------------------------------------------------------------------
await openCsv(CDD, cddChoices);
await page.waitForSelector("#trn-panel:not([hidden])");
const facts = await tc("#trn-ds");
check("the panel is on the page", await page.locator("#trn-panel").isVisible());
check("it names the target, task, transform and the counts",
  ["Ki", "regression", "pKi = 9 − log10(Ki in nM)", "102 of 106", "4 of 106"].every(s => facts.includes(s)), facts);
check("the seed starts at 0 and the strategy at scaffold",
  (await page.inputValue("#trn-seed")) === "0" && await page.locator("input[name=trn-strategy][value=scaffold]").isChecked()
  && !(await page.locator("input[name=trn-strategy][value=random]").isChecked()));
check("it says what the production strategy is, in a sentence", (await tc("#trn-help")).includes("production") && (await tc("#trn-help")).includes("never chosen from test scores"));
check("Train is on offer", await page.locator("#trn-go").isEnabled() && await page.locator("#trn-cancel").isHidden());
await shot("ready");

await page.fill("#trn-seed", "abc");
check("a seed that is not a number is said to be wrong, and cannot be sent", (await tc("#trn-seed-err")).includes("whole number") && await page.locator("#trn-go").isDisabled());
await page.fill("#trn-seed", "4294967296");
check("a seed over 4294967295 cannot be sent", (await tc("#trn-seed-err")).includes("at most 4294967295") && await page.locator("#trn-go").isDisabled());
await page.fill("#trn-seed", "-1");
check("a negative seed cannot be sent", await page.locator("#trn-go").isDisabled());
await page.fill("#trn-seed", "0");
check("a good seed clears it", (await tc("#trn-seed-err")) === "" && await page.locator("#trn-go").isEnabled());

// ---- a run that completes -----------------------------------------------------------------------------------
console.log("a run that completes");
await page.click("#trn-go");
// A real tiny run can be over before the page polls, so the mid-run checks are the stub's (its run is held
// open); a real run is judged by what it must always do: the request, then its completed run.
if (STUB) await hasText("#trn-status", "Running");
else while (!posts().length) await page.waitForTimeout(50);
const sent = posts()[0] && JSON.parse(posts()[0].body);
check("the request is the form's, with the tiny profile the page was opened with",
  sent && sent.seed === 0 && sent.production_strategy === "scaffold" && sent.profile === "tiny" && /^ds-[0-9a-f]{12}$/.test(sent.dataset_id), JSON.stringify(sent));
if (STUB) {
  check("progress is a bar with a stage, a count and the time", await page.locator("#trn-status progress").isVisible()
    && (await tc("#trn-status")).length > 0);
  await hasText("#trn-status", "tuning");
  const st = await tc("#trn-status");
  check("the stage and completed/total are shown", st.includes("tuning") && st.includes("3 of 6 fits"), st);
  check("the bar is half full", (await page.locator("#trn-status progress").evaluate(p => p.value)) === 0.5);
}
if (STUB) check("while it runs, Cancel is there and the form is locked", await page.locator("#trn-cancel").isVisible()
  && await page.locator("#trn-seed").isDisabled() && await page.locator("#trn-go").isDisabled());
await shot("running");
await settleDone();
const runId = (await tc("#trn-run-id")).trim();
check("the completed run's id is shown, selectable and copyable", runId.length > 0 && /^\S+$/.test(runId)
  && (await page.locator("#trn-run-id").evaluate(e => getComputedStyle(e).userSelect)) === "all" && await page.locator("#trn-copy").isVisible(), runId);
const res = await tc("#trn-result");
check("it says a run is temporary until registered", res.includes("temporary until it is registered") && res.includes("makes it durable"));
check("both strategies are side by side, the production one marked",
  (await page.locator("#trn-result th.tcol").count()) === 2 && (await tc("#trn-result th.tcol.prod")).includes("Scaffold") && (await tc("#trn-result th.tcol.prod")).includes("production")
  && (await tc("#trn-result th.tcol")).includes("Random"));
check("every regression metric has a row, labelled as the issue words them",
  ["RMSE", "MAE", "R²", "Spearman"].every(m => res.includes(m)) && !/PR-AUC/i.test(res));
check("each cell is a mean, an SD and a defined count", (await page.locator("#trn-result .tgrid2 tbody tr:first-child td").count()) === 6 && res.includes("5 of 5"));
check("no number reads NaN or undefined where one is not due", !res.includes("NaN") && !res.includes("null"));
check("the caveats are stated", ["not independent replicates", "not a fresh held-out test", "not a guarantee"].every(s => res.includes(s)));
check("the production model's configuration is shown", res.includes("Configuration") && res.includes("max_depth"));
if (STUB) {
  check("the means and SDs are the run's", res.includes("0.5512") && res.includes("0.0407") && res.includes("0.7234") && res.includes("0.0912"));
  check("a metric undefined in two repeats says so, with the reason, as text", res.includes("3 of 5") && res.includes("undefined in 2 of 5")
    && res.includes("only one value in the test set <i>x</i>") && (await page.locator("#trn-result i").count()) === 0);
  check("a single defined value has a mean and an SD of n/a", res.includes("n/a"));
  check("a server's markup in a reason does not run", (await page.evaluate(() => window.__xss)) === undefined);
}
check("polling stopped at the terminal state", await quiet(2600));
check("the run's record was fetched once, for the run polled", requests.filter(r => r.path === "/api/training/run").length === 1);
await shot("done");
await shot("result", "#trn-result");

// ---- the paths that go wrong (the stub makes them on demand) --------------------------------------------------
if (STUB) {
  console.log("a job that fails");
  await train(1);
  await hasText("#trn-status", "boom");
  const err = await tc("#trn-status");
  check("the server's message is shown, as text", err.includes("<b>boom</b>") && (await page.locator("#trn-status b").count()) === 0 && (await page.locator("#trn-status img").count()) === 0);
  check("nothing in the message ran", (await page.evaluate(() => window.__xss)) === undefined);
  check("it offers another run, and the form is open again", await page.locator("#trn-retry").isVisible() && await page.locator("#trn-go").isEnabled() && await page.locator("#trn-seed").isEnabled());
  check("a failed run's results are not shown", (await page.locator("#trn-run-id").count()) === 0);
  check("polling stopped at the error", await quiet(2600));
  await shot("error");

  console.log("a refused start");
  await train(3);
  await hasText("#trn-status", "queue is full");
  check("the server's refusal is shown with a way to retry", (await tc("#trn-status")).includes("cannot start this now") && await page.locator("#trn-retry").isVisible() && await page.locator("#trn-go").isEnabled());
  const before = posts().length;
  await page.click("#trn-retry");
  await hasText("#trn-status", "queue is full");
  check("Try again sends the request again", posts().length === before + 1);

  console.log("a strategy with no valid split");
  await train(2);
  await hasText("#trn-status", "not possible");
  const un = await tc("#trn-status");
  check("it says the scaffold selection is not possible, and why", un.includes("Scaffold selection is not possible") && un.includes("no valid scaffold split <u>x</u>")
    && un.includes("102 eligible rows in 8 groups") && un.includes("the largest group (60 rows)") && (await page.locator("#trn-status u").count()) === 0, un);
  check("nothing is switched for the user", await page.locator("input[name=trn-strategy][value=scaffold]").isChecked() && posts().length === before + 2);
  check("a new run with the other strategy is an explicit button", (await tc("#trn-other")).includes("Train again with Random selection"));
  await shot("unavailable");
  await page.click("#trn-other");
  await settleDone();
  const lastPost = JSON.parse(posts()[posts().length - 1].body);
  check("pressing it starts a new run with the other strategy, same seed", lastPost.production_strategy === "random" && lastPost.seed === 2);
  check("the form now says random", await page.locator("input[name=trn-strategy][value=random]").isChecked());
  check("and that strategy is the one marked production", (await tc("#trn-result th.tcol.prod")).includes("Random"));
  await page.check("input[name=trn-strategy][value=scaffold]");

  console.log("a run that expires");
  await train(5);
  await hasText("#trn-status", "no longer available");
  check("a 404 on the run says to train again", (await tc("#trn-status")).includes("This run is no longer available; train again.") && await page.locator("#trn-retry").isVisible());
  check("polling stopped at the expiry", await quiet(2600));

  console.log("a result that cannot be read");
  await train(8);
  await hasText("#trn-status", "Could not read the run's results");
  check("the failure is shown with a retry", await page.locator("#trn-result-retry").isVisible() && (await page.locator("#trn-run-id").count()) === 0);
  await page.click("#trn-result-retry");
  await settleDone();
  check("retrying reads it", (await tc("#trn-result")).includes("RMSE"));

  console.log("one strategy unavailable, the other completes");
  await page.check("input[name=trn-strategy][value=random]");
  await train(6);
  await settleDone();
  const part = await tc("#trn-result");
  check("the unavailable strategy shows its reason and no numbers", part.includes("Scaffold has no valid split") && part.includes("no valid scaffold split <u>x</u>")
    && part.includes("no valid split") && (await page.locator("#trn-result u").count()) === 0);
  check("the other still shows its metrics, marked production", part.includes("0.5512") && (await tc("#trn-result th.tcol.prod")).includes("Random"));
  await page.check("input[name=trn-strategy][value=scaffold]");
}

// ---- cancel -----------------------------------------------------------------------------------------------------
console.log("cancel");
await train(STUB ? 4 : 0);
// A real tiny run can finish before the page next polls, so only the stub run is certain to be running.
if (STUB) await hasText("#trn-status", "Running");
else await until(() => /Queued|Running|Finishing|Done/.test(document.getElementById("trn-status").textContent), null, 30000);
if (await page.locator("#trn-cancel").isVisible()) {
  await page.click("#trn-cancel");
  await until(() => /Cancelled|Done/.test(document.getElementById("trn-status").textContent), null, 60000);
  const cancelled = (await tc("#trn-status")).includes("Cancelled");
  check("Cancel ends the run, and the form is open again", (STUB ? cancelled : true) && await page.locator("#trn-go").isEnabled());
  check("a cancelled run offers a new one and shows no results", !cancelled || (await page.locator("#trn-retry").isVisible() && (await page.locator("#trn-run-id").count()) === 0));
  check("the server was asked to cancel the run it was running", requests.some(r => r.path === "/api/training/cancel" && r.method === "POST"));
  check("polling stopped", await quiet(2600));
} else {
  check("the run finished before it could be cancelled (a quick real run)", !STUB);
}

// ---- another dataset takes the panel over ----------------------------------------------------------------------------
console.log("another dataset");
if (STUB) {
  await train(4);
  await hasText("#trn-status", "Running");
}
const cancelsBefore = requests.filter(r => r.path === "/api/training/cancel").length;
await openCsv(SYN, async () => {
  await page.selectOption("#dd-target", "2");
  await page.check("input[name=dd-kind][value=classification]");
  await page.selectOption("#dd-positive", "1");
});
await page.waitForFunction(() => document.getElementById("trn-ds").textContent.includes("classification"), null, { timeout: 30000 });
const cf = await tc("#trn-ds");
check("the panel is the new dataset's: its target, task and positive class", cf.includes("Active") && cf.includes("classification (positive class: 1)"), cf);
check("the old run is gone from it", (await tc("#trn-status")).includes("Nothing has been trained") && (await page.locator("#trn-run-id").count()) === 0);
if (STUB) {
  check("the run nobody can see any more was cancelled", requests.filter(r => r.path === "/api/training/cancel").length === cancelsBefore + 1);
  const n = polls();
  await page.waitForTimeout(2600);
  check("and nothing polls for it", polls() === n);
}
await shot("classification");

// ---- a dataset with no target ------------------------------------------------------------------------------------
console.log("a dataset with no target");
await openCsv(SYN, async () => { await page.selectOption("#dd-target", "-1"); });
await page.waitForFunction(() => document.getElementById("trn-reason").textContent.length > 0, null, { timeout: 30000 });
check("training is disabled, with the reason shown", await page.locator("#trn-go").isDisabled() && (await tc("#trn-reason")).includes("no target"), await tc("#trn-reason"));
check("the form is disabled too", await page.locator("#trn-seed").isDisabled());
await shot("no-target");

check("the page asked for nothing outside its own server", external.length === 0, external.join(", "));

await browser.close();
if (stubServer) stubServer.close();
server.kill();
fs.rmSync(tmp, { recursive: true, force: true });
if (problems.length) { console.log("page problems:\n  " + problems.join("\n  ")); }
if (failures.length || problems.length) { console.log(`\n${failures.length} check(s) failed, ${problems.length} page problem(s)`); process.exit(1); }
console.log("\nall checks passed");
