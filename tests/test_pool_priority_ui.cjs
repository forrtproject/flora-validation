/* Run with: node tests/test_pool_priority_ui.cjs
 * The Pool Priority preview: counts, the time they were counted, and a refresh
 * that recounts without discarding unsaved edits.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  const errors = [];
  const previews = [];
  let counts = { pool_total: 3524, priority_match: 325, rest: 3199 };
  let slow = null;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/serving-config") {
      return route.fulfill({ json: { enabled: true, priority_outcome: "failed", priority_year_min: 2011,
        priority_year_max: 2021, priority_share: 70, updated_by: "Hamid", updated_at: "2026-07-30T04:41:18Z" } });
    }
    if (url.pathname === "/api/admin/serving-config/preview") {
      previews.push(url.search);
      if (slow) await slow;
      return route.fulfill({ json: counts });
    }
    return route.fulfill({ json: {} });
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#admin-tab-entries").classList.add("hidden");
      document.querySelector("#admin-tab-priority").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      fetchServingConfig();
    });
    await page.locator("#sp-preview-refresh").waitFor();
    const list = () => page.locator(".sp-preview-list").innerText();
    assert.match(await list(), /325 waiting records match \(failed · 2011–2021\)/);
    assert.match(await list(), /3,199 other waiting records/);
    assert.match(await list(), /At 70%, ≈ 7 of every 10 served will be priority/);
    assert.match(await page.locator(".sp-preview-foot").innerText(), /Counted at \d{2}:\d{2}:\d{2}/);
    assert.match(await page.locator("#sp-preview").innerText(), /a reviewer slot is still free/);

    // Refresh recounts with the form as edited, and keeps the unsaved edits.
    await page.locator("#sp-ymin").fill("2015");
    await page.waitForFunction(() => /2015–2021/.test(document.querySelector(".sp-preview-list")?.innerText || ""));
    await page.locator("#sp-share").fill("40");
    counts = { pool_total: 3500, priority_match: 120, rest: 3380 };
    const before = previews.length;
    await page.locator("#sp-preview-refresh").click();
    await page.waitForFunction(() => /120 waiting/.test(document.querySelector(".sp-preview-list")?.innerText || ""));
    assert.equal(previews.length, before + 1);
    assert.match(previews.at(-1), /year_min=2015/);
    assert.equal(await page.locator("#sp-ymin").inputValue(), "2015");
    assert.match(await list(), /At 40%, ≈ 4 of every 10/);

    // An older, slower count never overwrites a newer one.
    let release;
    slow = new Promise(resolve => { release = resolve; });
    await page.locator("#sp-preview-refresh").click();          // slow, older
    await page.waitForFunction(() => document.querySelector("#sp-preview-refresh").disabled);
    slow = null;
    counts = { pool_total: 3500, priority_match: 99, rest: 3401 };
    await page.evaluate(() => _refreshPriorityPreview(0));        // fast, newer
    await page.waitForFunction(() => /99 waiting/.test(document.querySelector(".sp-preview-list")?.innerText || ""));
    counts = { pool_total: 1, priority_match: 1, rest: 0 };
    release();
    await page.waitForTimeout(200);
    assert.match(await list(), /99 waiting/);

    // On a phone the refresh button stays visible.
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator("#sp-preview-refresh").isVisible(), true);
    assert.deepEqual(errors, []);
    console.log("pool priority UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
