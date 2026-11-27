// The Ligands page, /ligands: asked to load a dataset, upload a CSV, inspect it,
// sort and filter it -- the connected path through the dialog, the server,
// ligandbench and the grid -- reopen it by its address, and check that a
// 10,000-row dataset is only ever shown a page at a time. Also that the viewer
// hands a dropped CSV to that page rather than opening it over the 3D stage.
//
//   npm install                      # playwright-core, once
//   node tests/e2e/dataset_smoke.mjs [OUTDIR]
//
// Starts its own server (python serve.py, on a free port, with a fresh dataset
// directory) and stops it. PYTHON picks the interpreter that has drughunter
// installed; CHROME_BIN picks the browser, otherwise Playwright's own. Needs the
// 3Dmol CDN, like the other walkthrough. Exits non-zero if anything fails or the
// page logged an error.
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
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dh-dataset-smoke-"));
const CDD = path.join(ROOT, "tests/ligandbench/data/cdd_export_hiv1_protease.csv");
const PDB = path.join(ROOT, "tests/data/1CRN.pdb");

// ---- fixtures: 10,000 distinct molecules, none of them real ------------------
const TEN_K = path.join(tmp, "ten-thousand.csv");
{
  const lines = ["Name,SMILES,Activity"];
  for (let a = 1, n = 0; a <= 100; a++)
    for (let b = 1; b <= 100; b++, n++) lines.push(`m${n},${"C".repeat(a)}N${"C".repeat(b)}O,${n % 97}`);
  fs.writeFileSync(TEN_K, lines.join("\n") + "\n");
}

// ---- the server ------------------------------------------------------------
const port = await new Promise(res => {
  const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
const server = spawn(process.env.PYTHON || "python", ["serve.py", String(port), "--no-open"], {
  cwd: ROOT, stdio: "ignore",
  env: { ...process.env, PV_DATASET_DIR: path.join(tmp, "datasets"), PV_PROJECT_DIR: path.join(tmp, "projects") },
});
const BASE = `http://127.0.0.1:${port}`;
for (let i = 0; ; i++) {
  try { if ((await fetch(BASE + "/api/status")).ok) break; } catch {}
  if (i > 240) { server.kill(); throw new Error("the server did not start"); }
  await new Promise(r => setTimeout(r, 500));
}

// ---- the page ----------------------------------------------------------------
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN || undefined,
  args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 }, colorScheme: "light" })).newPage();
const problems = [], failures = [], pages = [];
let uploaded = null, expectRefusal = false;
page.on("console", m => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) problems.push(m.text()); });
page.on("pageerror", e => problems.push("pageerror: " + e.message));
page.on("response", async r => {
  const u = r.url();
  // The one refusal this walkthrough provokes on purpose, and the 404s of looking for what was cancelled.
  const expected = (expectRefusal && u.endsWith("/api/dataset/resolve")) || u.includes("/api/dataset?");
  if (r.status() >= 400 && !u.endsWith("/favicon.ico") && !expected) problems.push(r.status() + " " + u);
  try {
    if (u.endsWith("/api/dataset/upload")) uploaded = await r.json();
    if (u.endsWith("/api/dataset/rows") && r.ok()) pages.push(await r.json());
  } catch {}
});

const check = (name, ok, detail = "") => { if (!ok) failures.push(name + (detail ? " -- " + detail : "")); console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + detail)); };
const text = sel => page.locator(sel).first().innerText();
const idle = () => page.waitForFunction(() => document.getElementById("mol-grid").getAttribute("aria-busy") === "false");
const cards = () => page.locator("#mol-grid .mcard").count();
const shot = async name => { if (OUT) await page.screenshot({ path: path.join(OUT, name + ".png") }); };
const gridOpens = () => page.waitForSelector("#mol-grid .mcard");

// ---- the page asks for a dataset ---------------------------------------------------------------------
console.log("the ligands page");
await page.goto(BASE + "/ligands");
await page.waitForFunction(() => document.getElementById("version").textContent.startsWith("v"), null, { timeout: 60000 });
check("it opens with a prompt to load a dataset", await page.locator("#ds-prompt").isVisible()
  && (await text("#ds-prompt")).includes("Load your ligand dataset") && await page.locator("#ds-open-big").isEnabled());
check("there is no panel yet, and the tab says Ligands", await page.locator("#mtab").isHidden() && (await page.title()).startsWith("Ligands"));
check("the navigation says where this is", (await page.locator(".nav a[aria-current=page]").innerText()) === "Ligands");
await shot("prompt");
await page.goto(BASE + "/ligands/ds-000000000000");
await page.waitForFunction(() => document.getElementById("ds-prompt-msg").textContent.length > 0);
check("an address that names no dataset says so and still asks for one", (await text("#ds-prompt-msg")).includes("load the file again") && await page.locator("#ds-prompt").isVisible(), await text("#ds-prompt-msg"));
await page.goto(BASE + "/ligands/nonsense");
await page.waitForFunction(() => document.getElementById("ds-prompt-msg").textContent.length > 0);
check("an address that cannot be a dataset's is said to be wrong", (await text("#ds-prompt-msg")).includes("not a dataset id"));

// ---- the viewer hands a CSV over, and still loads a structure ------------------------------------------------
console.log("the viewer");
await page.goto(BASE + "/");
await page.waitForFunction(() => document.getElementById("version").textContent.startsWith("v"), null, { timeout: 60000 });
await page.setInputFiles("#file-input", PDB);
await page.waitForFunction(() => document.getElementById("ent-n").textContent.includes("1 loaded"));
check("1CRN.pdb loads into the tree", true);
check("a structure does not open the dataset dialog", (await page.locator("#datadlg").count()) === 0);
await page.setInputFiles("#file-input", CDD);
await page.waitForSelector("#pdb-msg a[href^='/ligands/ds-']");
const handed = await page.locator("#pdb-msg a").getAttribute("href");
check("a CSV is sent to the Ligands page, not opened over the stage", (await text("#pdb-msg")).includes("cdd_export_hiv1_protease.csv")
  && (await page.locator("#mtab").count()) === 0 && (await page.locator("#ent-n").innerText()).toLowerCase().includes("1 loaded"), handed);
check("the viewer links to the Ligands page", (await page.locator(".nav a[href='/ligands']").count()) === 1);

// ---- the link opens the upload where it was left; cancelling removes it -----------------------------------
console.log("a staged upload, by its address");
const staged = uploaded;
await page.goto(BASE + handed);
await page.waitForSelector("#datadlg[open]");
check("the address is the upload's own", new URL(page.url()).pathname === handed && handed === "/ligands/" + staged.dataset_id);
check("upload answers staged with its columns", staged.state === "staged" && staged.columns.length === 8 && staged.n_rows === 106);
check("the dialog names the file and its size", (await text("#dd-sub")).includes("cdd_export_hiv1_protease.csv · 106 rows · 8 columns"));
check("nothing is behind it but the prompt", await page.locator("#mtab").isHidden());
await page.click("#dd-cancel");
await page.waitForFunction(() => !document.getElementById("datadlg").open);
await page.waitForTimeout(300);
const gone = await fetch(`${BASE}/api/dataset?id=${staged.dataset_id}`);
check("a cancelled upload is gone from the server", gone.status === 404, "HTTP " + gone.status);
check("the address goes back to the page with no dataset", new URL(page.url()).pathname === "/ligands");
check("and the prompt asks again", await page.locator("#ds-prompt").isVisible() && await page.locator("#mtab").isHidden());

// ---- the CDD export: the dialog asks, nothing is assumed ---------------------------------
console.log("the CDD export");
await page.setInputFiles("#ds-file", CDD);
await page.waitForSelector("#datadlg[open]");
check("the one structure column is chosen", (await page.locator("#dd-structure").inputValue()) === "3");
check("the target is left to the user", (await page.locator("#dd-target").inputValue()) === "" && await page.locator("#dd-go").isDisabled());
await page.selectOption("#dd-target", "4");
check("the task is asked, and suggested only", await page.locator("#dd-kind-wrap").isVisible()
  && (await page.locator("input[name=dd-kind]:checked").count()) === 0
  && (await text("#dd-kind-reg")).includes("suggested"));
await page.check("input[name=dd-kind][value=regression]");
check("qualifier and units must be confirmed", await page.locator("#dd-go").isDisabled()
  && (await text("#dd-errors")).includes("qualifier"));
check("the qualifier column is suggested, not chosen", (await page.locator("#dd-qualifier").inputValue()) === ""
  && (await page.locator("#dd-qualifier option[value='5']").innerText()).includes("suggested"));
check("a pKi is not on offer until the units are nM", await page.locator("input[name=dd-transform][value=pki]").isDisabled());
await page.selectOption("#dd-qualifier", "5");
await page.selectOption("#dd-units", "u:nM");
check("nM unlocks the pKi", await page.locator("input[name=dd-transform][value=pki]").isEnabled());
await page.check("input[name=dd-transform][value=pki]");
check("the form is complete", await page.locator("#dd-go").isEnabled());
await shot("dialog");

// A refusal leaves the dialog open and the upload staged.
await page.selectOption("#dd-structure", "7");   // Project: every value is "HIV protease"
expectRefusal = true;
await page.click("#dd-go");
await page.waitForFunction(() => document.getElementById("dd-msg").textContent.length > 0);
expectRefusal = false;
check("a column that holds no SMILES is refused, and said so", (await text("#dd-msg")).includes("none of the 106 values"));
check("the dialog stays open to try again", await page.locator("#datadlg").evaluate(d => d.open));
await page.selectOption("#dd-structure", "3");
await page.click("#dd-go");
await gridOpens(); await idle();

const first = pages[pages.length - 1];
check("the grid shows the first fifty", (await cards()) === 50 && (await text("#mol-range")) === "1–50 of 106", await text("#mol-range"));
check("every card has its drawing", (await page.locator("#mol-grid .mdepict svg").count()) === 50);
check("the page carried 50 rows and no more", first.rows.length === 50 && first.total === 106);
const summary = await text("#ds-sum");
check("the rail says what was read", ["cdd_export_hiv1_protease.csv", "106", "SMILES", "102 of 106", "none"].every(s => summary.includes(s)), summary);
check("a bound is listed among what is left out", summary.includes("bound"));
check("the first card is the first record", (await text("#mol-grid .mcard .mname")) === "DH-0001");
check("the prompt is gone and the page is titled for the dataset", await page.locator("#ds-prompt").isHidden() && (await page.title()).startsWith("cdd_export_hiv1_protease.csv"));
await shot("grid");

// ---- the dataset has an address: reload, Back, and a link -------------------------------------------------------
console.log("its address");
const cddPath = new URL(page.url()).pathname;
check("the address is the dataset's id", /^\/ligands\/ds-[0-9a-f]{12}$/.test(cddPath), cddPath);
await page.reload();
await gridOpens(); await idle();
check("reloading reopens it, with the same rows", (await text("#mol-range")) === "1–50 of 106" && (await text("#mol-grid .mcard .mname")) === "DH-0001");
check("and its summary", (await text("#ds-sum")).includes("102 of 106"));
await page.goto(BASE + "/ligands");
check("the bare page asks for a dataset again", await page.locator("#ds-prompt").isVisible());
await page.goBack();
await gridOpens(); await idle();
check("Back returns to the dataset", new URL(page.url()).pathname === cddPath && (await cards()) === 50);
await page.goto(BASE + cddPath);
await gridOpens(); await idle();
check("a link opens it directly", (await text("#mol-range")) === "1–50 of 106");

// ---- sort ----------------------------------------------------------------------------------------
console.log("sort and filter");
await page.selectOption("#mol-sort-key", "target");
await idle();
await page.click("#mol-sort-dir");
await idle();
const vals = await page.$$eval("#mol-grid .mcard .mval", els => els.map(e => e.textContent.trim()));
const nums = vals.map(v => parseFloat(v.replace("pKi", ""))).filter(v => !isNaN(v));
check("descending by target", nums.length === vals.length && nums.every((v, i) => i === 0 || nums[i - 1] >= v), vals.slice(0, 4).join(" "));
const sortPage = pages[pages.length - 1];
check("sorting kept each row's identity", sortPage.rows.every(r => r.record === r.row_id + 1 && r.cells[0] === "DH-" + String(r.record).padStart(4, "0")));

// ---- filter ---------------------------------------------------------------------------------------
await page.selectOption("#mol-f-key", "c4");
check("a number column offers comparisons", (await page.locator("#mol-f-op option").allInnerTexts()).includes("≥"));
await page.selectOption("#mol-f-op", "gte");
await page.fill("#mol-f-val", "abc");
await page.click("#mol-f-add");
check("a typo is said at once", (await text("#mol-msg")).includes("not a number"));
await page.fill("#mol-f-val", "1000");
await page.click("#mol-f-add");
await idle();
const filtered = pages[pages.length - 1];
check("the filter is a chip and narrows the total", (await page.locator("#mol-chips .chip").count()) === 1 && filtered.total < 106 && filtered.total > 0, "total " + filtered.total);
check("the count says how many matched", (await text("#mol-n")).toLowerCase().includes(`${filtered.total} of 106`), await text("#mol-n"));
await page.click("#mol-chips .chip");
await idle();
check("removing it brings the rows back", (await text("#mol-range")).endsWith("of 106"));

// ---- paging and the detail ------------------------------------------------------------------------------
await page.selectOption("#mol-sort-key", "row_id"); await idle();
await page.click("#mol-next"); await idle();
check("next page is the rest", (await text("#mol-range")) === "51–100 of 106" && (await text("#mol-grid .mcard .mname")) === "DH-0051");
await page.click("#mol-next"); await idle();
check("the last page is short", (await cards()) === 6 && await page.locator("#mol-next").isDisabled());
await page.locator("#mol-grid .mcard").last().click();
const detail = await text("#mol-detail");
check("clicking a card opens every original column", ["DH-0106", "Molecule Name", "CDD Number", "Batch Name", "SMILES", "Run Date", "Project"].every(s => detail.includes(s)), detail.slice(0, 120));
check("a censored value shows its bound and why it is not trained on", detail.includes(">10000") && detail.includes("bound"), detail.slice(0, 200));
await shot("detail");
await page.click("#mol-close");
check("the detail closes", await page.locator("#mol-detail").isHidden());

// ---- a file where some structures do not parse -------------------------------------------------------------
console.log("partial parse failures");
const PARTIAL = path.join(tmp, "partial.csv");
fs.writeFileSync(PARTIAL, "Name,Smiles,Active\nethanol,CCO,1\nbroken,C(C(,0\n,,1\nbenzene,c1ccccc1,0\ntoluene,Cc1ccccc1,1\n");
await page.setInputFiles("#ds-file", PARTIAL);
await page.waitForSelector("#datadlg[open]");
await page.selectOption("#dd-target", "2");   // Active
await page.check("input[name=dd-kind][value=classification]");
check("two classes ask for the positive one", await page.locator("#dd-cls").isVisible() && await page.locator("#dd-go").isDisabled());
await page.selectOption("#dd-positive", "1");
await page.click("#dd-go");
await page.waitForFunction(() => document.getElementById("ds-n").textContent === "5 rows");
await idle();
const partial = await text("#ds-sum");
check("failures are counted with their record numbers", partial.toLowerCase().includes("2 — records 2, 3"), partial);
check("the failed rows stay as placeholders", (await page.locator("#mol-grid .mcard.bad").count()) === 2 && (await cards()) === 5);
await page.locator("#mol-grid .mcard.bad").first().click();
const why = await text("#mol-detail");
check("a placeholder says why", why.includes("not a valid SMILES") && why.includes("the structure did not parse"), why.slice(0, 160));

// ---- ten thousand rows -------------------------------------------------------------------------------------
console.log("ten thousand rows");
pages.length = 0;
await page.setInputFiles("#ds-file", TEN_K);
await page.waitForSelector("#datadlg[open]");
check("a file with no target: the user says so", (await page.locator("#dd-structure").inputValue()) === "1");
await page.selectOption("#dd-target", "-1");
await page.click("#dd-go");
await page.waitForFunction(() => document.getElementById("ds-n").textContent === "10000 rows");
await gridOpens(); await idle();
check("ten thousand rows, one page on screen", (await text("#mol-range")) === "1–50 of 10000" && (await cards()) === 50, await text("#mol-range"));
check("the first request asked for no more than a page", pages[0].rows.length <= 100 && pages[0].rows.every(r => r.svg), "rows " + pages[0].rows.length);
let mostCards = await cards();
await page.selectOption("#mol-size", "100"); await idle();
mostCards = Math.max(mostCards, await cards());
check("a hundred is the most a page holds", (await cards()) === 100 && (await page.locator("#mol-grid .mdepict svg").count()) === 100);
for (let i = 0; i < 3; i++) { await page.click("#mol-next"); await idle(); mostCards = Math.max(mostCards, await cards()); }
check("paging never mounts more than a page", mostCards <= 100, "most " + mostCards);
check("no request carried over a hundred rows", pages.every(p => p.rows.length <= 100), pages.map(p => p.rows.length).join(","));
await shot("ten-thousand");

await browser.close();
server.kill();
fs.rmSync(tmp, { recursive: true, force: true });
if (problems.length) { console.log("page problems:\n  " + problems.join("\n  ")); }
if (failures.length || problems.length) { console.log(`\n${failures.length} check(s) failed, ${problems.length} page problem(s)`); process.exit(1); }
console.log("\nall checks passed");
