// Drive the viewer through a whole session -- fetch, select, hide, remove,
// undo, both detectors, and with --slow also Prepare and generation -- and
// record what every panel says, plus a screenshot of the 3D stage, per step.
//
//   npm install                      # playwright-core, once
//   node tests/e2e/ui_snapshot.mjs http://127.0.0.1:8000 OUTDIR [--slow]
//
// Exits non-zero if the page logged an error. To check that a refactor kept
// behaviour, run it against the old and the new server and diff the two
// OUTDIR/snapshots.json files (fpocket volumes and generated molecules are
// random, so those fields differ between any two runs). Needs network access
// (RCSB, the 3Dmol CDN). CHROME_BIN picks the browser; otherwise Playwright's
// own (npx playwright install chromium).
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = process.argv[2], OUT = process.argv[3], SLOW = process.argv.includes("--slow");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN || undefined,
  args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: "light" });
const page = await ctx.newPage();
const problems = [];
// Failed loads are caught by status below, where the URL is known: the
// favicon 404 is the browser asking, not the page.
page.on("console", m => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) problems.push(m.text()); });
page.on("response", r => { if (r.status() >= 400 && !r.url().endsWith("/favicon.ico")) problems.push(r.status() + " " + r.url()); });
page.on("pageerror", e => problems.push("pageerror: " + e.message));

const snaps = [];
let step = 0;
const T = s => s.replace(/\d+(\.\d+)?\s?s\b/g, "<t>s").replace(/\s+/g, " ").trim();

async function snap(label, { shot = true } = {}) {
  await page.waitForTimeout(700);           // let 3Dmol's zoom animations settle
  const s = await page.evaluate(() => {
    const $ = id => document.getElementById(id);
    const txt = el => el ? el.textContent.replace(/\s+/g, " ").trim() : null;
    const vis = el => !!el && !el.hidden && !el.closest("[hidden]");
    return {
      tree: [...document.querySelectorAll("#tree .tn")].map(r => [
        r.dataset.key, r.getAttribute("aria-level"), r.getAttribute("aria-selected"),
        r.getAttribute("aria-expanded"), r.classList.contains("off") ? "off" : "",
        txt(r.querySelector(".tl")), r.querySelector(".eye").getAttribute("title"),
        r.querySelector(".eye").className]),
      treeVisible: vis($("tree")),
      entN: txt($("ent-n")), entEmpty: vis($("ent-empty")), hint: vis($("tree-hint")),
      undo: vis($("undo")) ? $("undo").title : null, selClear: vis($("sel-clear")),
      scope: vis($("scope")) ? [$("scope").className, txt($("scope"))] : null,
      prepBtn: [txt($("prep-btn")), $("prep-btn").disabled],
      prepMsg: [$("prep-msg").className, txt($("prep-msg"))],
      prepLog: vis($("prep-log")),
      pkBtn: [txt($("find-pockets")), $("find-pockets").disabled],
      pkMsg: [$("pk-msg").className, txt($("pk-msg"))], pkN: txt($("pk-n")),
      pkList: [...document.querySelectorAll("#pk-list [data-pk]")].map(b =>
        [b.getAttribute("aria-checked"), b.querySelector(".dot").getAttribute("style"), txt(b)]),
      pkRun: vis($("pk-runlog")) ? txt($("pk-run")) : null,
      spheres: vis($("pk-sph-wrap")), genWrap: vis($("gen-wrap")),
      lig: vis($("pk-lig-wrap")) ? [txt($("pk-lig-names")), $("c-nolig").checked] : null,
      downloads: [vis($("ent-dl")) ? txt($("ent-dl")) : null,
                  vis($("pk-dl")) ? txt($("pk-dl")) : null],
      sessMsg: [$("sess-msg").className, txt($("sess-msg"))],
      saveBtn: $("sess-save").disabled,
      // The project directory's path is this run's temp tree, so only
      // whether there is one is comparable between runs.
      project: [txt($("pj-n")), !!$("pj-where").querySelector(".mono"),
                $("pj-save").disabled, [$("pj-msg").className, txt($("pj-msg"))]],
      genMsg: txt($("gen-msg")), genBtn: $("gen-run").disabled,
      genMod: vis($("mod-gen")) ? [txt($("gen-count")),
        [...document.querySelectorAll("#gen-list [data-gen]")].map(b => [b.getAttribute("aria-checked"), b.querySelector(".dot").getAttribute("style"), txt(b)]),
        vis($("gen-runlog"))] : null,
      table: vis($("gtab")) ? [txt($("gt-n")),
        [...document.querySelectorAll("#gt th")].map(th => th.getAttribute("aria-sort")),
        [...document.querySelectorAll("#gt-body tr")].map(tr => [tr.dataset.rank, tr.dataset.i, tr.getAttribute("aria-selected")])] : null,
      ovTl: vis(document.querySelector(".ov-tl")) ? [txt($("ov-id")), txt($("ov-sub")), txt($("ov-pick"))] : null,
      key: [...document.querySelectorAll("#ov-key .key")].map(k => [k.querySelector("i").getAttribute("style"), txt(k)]),
      banner: [$("banner").className, txt($("banner"))],
      busy: $("busy").classList.contains("on"),
      pdbMsg: [$("pdb-msg").className, txt($("pdb-msg"))],
      theme: [document.documentElement.getAttribute("data-theme"),
        [...document.querySelectorAll("#theme button")].map(b => b.getAttribute("aria-pressed"))],
      focus: document.activeElement ? [document.activeElement.className,
        (document.activeElement.closest("[data-key]") || {dataset:{}}).dataset.key || null] : null,
    };
  });
  for (const k of ["prepMsg", "pkMsg", "genMsg", "pkRun", "pdbMsg", "sessMsg"]) {
    if (Array.isArray(s[k])) s[k] = s[k].map(v => typeof v === "string" ? T(v) : v);
    else if (typeof s[k] === "string") s[k] = T(s[k]);
  }
  if (s.genMod) s.genMod[1] = s.genMod[1].map(r => [r[0], r[1], T(r[2])]);
  const id = String(++step).padStart(2, "0") + "-" + label.replace(/[^a-z0-9]+/gi, "_");
  if (shot) await page.locator("#gl").screenshot({ path: `${OUT}/${id}.png` });
  snaps.push({ step: id, ...s });
  console.error("snap", id);
}
const row = key => page.locator(`#tree .tn[data-key="${key}"]`);
async function btn(key, cls){ await row(key).hover(); await row(key).locator(cls).click(); }
const waitIdle = async (sel, t = 600000) => page.waitForFunction(s => !document.querySelector(s).disabled, sel, { timeout: t });
async function fetchId(id) {
  await page.fill("#pdb-id", id);
  await page.click("#pdbq button");
  await page.waitForFunction(() => !document.querySelector("#pdbq button").disabled, null, { timeout: 60000 });
}

await page.goto(BASE + "/");
await page.waitForFunction(() => window.$3Dmol && document.getElementById("find-pockets"));
await page.waitForTimeout(1500);                 // status probe
await snap("empty");

await fetchId("XYZ"); await snap("bad id", { shot: false });
await fetchId("1HSG"); await snap("1hsg loaded");

await row("e1/chain:A").locator(".tw").click(); await snap("chain A open", { shot: false });
await row("e1/A:25:ASP").locator(".tl").click(); await snap("pick ASP25");
await row("e1/A:27:GLY").locator(".tl").click({ modifiers: ["Control"] }); await snap("ctrl GLY27");
await row("e1/A:30:ASP").locator(".tl").click({ modifiers: ["Shift"] }); await snap("shift to ASP30");
await page.keyboard.press("Escape"); await snap("esc clears");

await btn("e1/chain:B", ".eye"); await snap("hide chain B");
await btn("e1/chain:B", ".eye");
await row("e1/ligs").locator(".tw").click();
await snap("ligands open", { shot: false });
await row("e1/ligs").locator(".tl").click(); await snap("pick ligands");
await page.keyboard.press("Escape");
await btn("e1/waters", ".eye"); await snap("waters shown");
await btn("e1/waters", ".eye");

// remove a residue then undo it
await btn("e1/A:25:ASP", ".rm"); await snap("removed ASP25");
await page.click("#undo"); await snap("undo");

// keyboard: focus a row, arrow down, H hides
await row("e1/chain:A").locator(".tl").focus();
await page.keyboard.press("ArrowDown"); await page.keyboard.press("h");
await snap("kbd hide", { shot: false });
await page.keyboard.press("h");
await row("e1/chain:A").locator(".tl").focus();
await page.keyboard.press("ArrowLeft"); await snap("kbd fold", { shot: false });

// pockets with fpocket on the whole structure
await page.click("#find-pockets"); await waitIdle("#find-pockets"); await snap("fpocket");
await page.locator("#pk-list [data-pk='1']").click(); await snap("tick pocket 2");
await page.check("#c-spheres"); await snap("spheres on");
await page.locator("#pk-list [data-pk='0']").click(); await snap("untick pocket 1");

// a selection-scoped run
await row("e1/chain:A").locator(".tl").click();
await page.click("#find-pockets"); await waitIdle("#find-pockets"); await snap("fpocket chain A");
await page.keyboard.press("Escape");

await page.selectOption("#pk-method", "lacuna:nma"); await snap("method lacuna", { shot: false });
await page.click("#find-pockets"); await waitIdle("#find-pockets"); await snap("lacuna nma");
await page.selectOption("#pk-method", "fpocket");

// second structure: the scope needs a pick
await fetchId("1CRN"); await snap("1crn loaded");
await fetchId("1HSG"); await snap("1hsg refetch shows", { shot: false });
await btn("e1/root", ".eye"); await snap("hide 1hsg");
await btn("e1/root", ".eye");
await row("e1/root").locator(".tl").click({ modifiers: ["Control"] });
await row("e2/root").locator(".tl").click({ modifiers: ["Control"] });
await snap("two-structure pick", { shot: false });
await page.keyboard.press("Escape");

if (SLOW) {
  await row("e2/root").locator(".tl").click();
  await page.click("#prep-btn");
  await page.waitForFunction(() => !/Preparing/.test(document.getElementById("prep-btn").textContent), null, { timeout: 900000 });
  await snap("prepared 1crn");
  await row("e3/root").locator(".tl").click(); await snap("pick prepared", { shot: false });
  await page.keyboard.press("Escape");

  // generate for 1HSG pockets: run fpocket again on 1HSG, tick two, n=2
  await btn("e2/root", ".eye"); await btn("e3/root", ".eye");
  await row("e1/root").locator(".tl").click();
  await page.click("#find-pockets"); await waitIdle("#find-pockets");
  await page.locator("#pk-list [data-pk='1']").click();
  await page.fill("#gen-n", "2");
  await page.click("#gen-run");
  await page.waitForFunction(() => /Done|failed|Lost|Could not/.test(document.getElementById("gen-msg").textContent), null, { timeout: 900000 });
  await snap("generated");
  await page.click("#gt th[data-sort='mw']"); await snap("sort mw", { shot: false });
  await page.click("#gt th[data-sort='mw']"); await snap("sort mw desc", { shot: false });
  await page.locator("#gt-body tr").first().click(); await snap("select molecule");
  await page.locator("#gen-list [data-gen]").first().click(); await snap("hide gen group");
  await page.check("#gt-only"); await snap("visible only", { shot: false });
  await page.fill("#gen-show", "1"); await page.dispatchEvent("#gen-show", "input"); await snap("show 1");
  await page.click("#gt-tog"); await snap("table collapsed", { shot: false });
  // removing a part of the pocket structure clears pockets and generation
  await row("e1/chain:A").locator(".tw").click();
  await btn("e1/A:1:PRO", ".rm"); await snap("edit clears pockets");
}

await page.click("#theme [data-theme-set='dark']"); await snap("dark theme");

fs.writeFileSync(`${OUT}/snapshots.json`, JSON.stringify({ snaps, problems }, null, 1));
console.error("problems:", problems.length ? problems : "none");
await browser.close();
process.exitCode = problems.length ? 1 : 0;
