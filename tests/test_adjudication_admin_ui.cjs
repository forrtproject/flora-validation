/* Run with: node tests/test_adjudication_admin_ui.cjs
 * The admin "Disagreements" tab (adjudication, fred-data PR #143), phase 1:
 * hidden when the feature is off, a status card when it is on, a clear notice
 * when its setup failed — and the rest of the admin panel unaffected when
 * adjudication.js cannot load at all.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");

async function openAdmin(browser, status, { scriptMissing = false, onImport = null } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    if (url.pathname === "/adjudication.js" && scriptMissing) return route.fulfill({ status: 404, body: "" });
    const files = {
      "/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
      "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css",
    };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/disagreements/status") {
      return route.fulfill({ json: typeof status === "function" ? status() : status });
    }
    if (url.pathname === "/api/admin/disagreements/import" && onImport) {
      return route.fulfill({ json: onImport(route.request().postDataJSON()) });
    }
    return route.fulfill({ json: { records: [], entries: [], total: 0, runs: [], counts: {} } });
  });
  await page.goto("http://flora.test/");
  await page.evaluate(() => {
    document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
    document.querySelector("#admin-screen").classList.remove("hidden");
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
  });
  return { page, errors };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    // Switched off: no tab.
    {
      const { page, errors } = await openAdmin(browser, { enabled: false, ready: false, error: null, counts: null });
      await page.evaluate(() => window.Adjudication.initAdmin());
      assert.equal(await page.locator("#admin-disagreements-tab").isHidden(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Switched on and ready: the tab appears and shows the status card.
    {
      const counts = { records: 0, open: 0, awaiting_approval: 0, approved: 0, published: 0, judgements: 0 };
      const { page, errors } = await openAdmin(browser, { enabled: true, ready: true, error: null, counts });
      await page.evaluate(() => window.Adjudication.initAdmin());
      await page.locator("#admin-disagreements-tab").waitFor();
      await page.locator("#admin-disagreements-tab").click();
      await page.locator(".adj-ready").waitFor();
      assert.equal(await page.locator("#admin-tab-disagreements").isVisible(), true);
      assert.equal(await page.locator("#admin-tab-entries").isHidden(), true);
      assert.match(await page.locator(".adj-ready").innerText(), /Set up and ready/);
      assert.deepEqual(await page.locator(".adj-stats dt").allInnerTexts(),
        ["IMPORTED", "WAITING FOR JUDGEMENTS", "AWAITING ADMIN APPROVAL", "APPROVED", "PUBLISHED"]);
      assert.match(await page.locator(".adj-import h3").innerText(), /Import the disagreements/);
      if (process.env.FLORA_UI_SCREENSHOTS) {
        await fs.mkdir(process.env.FLORA_UI_SCREENSHOTS, { recursive: true });
        await page.locator("#admin-tab-disagreements").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "disagreements-ready.png") });
      }
      // Another tab still works, and hides this one.
      await page.evaluate(() => switchAdminTab("priority"));
      assert.equal(await page.locator("#admin-tab-disagreements").isHidden(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Preview, then import: the preview writes nothing, the import refreshes the counts.
    {
      const requests = [];
      let imported = false;
      const enrichment = { replications: 136, abstracts: 130, abstracts_from_europepmc: 87, years: 132,
        observatory_originals: 146, observatory_titles: 145, flora_originals: 105, flora_titles: 105,
        failed_batches: 0, failed_abstracts: 0 };
      const by_kind = { "we found no original": 54, "different original": 50,
        "same original, different outcome": 47, "MO names no original DOI": 8 };
      const status = () => ({
        enabled: true, ready: true, error: null,
        source: "forrtproject/fred-data@55d6f04:external/metascience-observatory/disagreements.csv",
        source_url: "https://raw.githubusercontent.com/forrtproject/fred-data/55d6f04/x.csv",
        counts: { records: imported ? 159 : 0, open: imported ? 159 : 0, awaiting_approval: 0,
          approved: 0, published: 0, judgements: 0 },
        by_kind: imported ? by_kind : {},
      });
      const onImport = body => {
        requests.push(body);
        if (body.apply) imported = true;
        return { rows: 159, by_kind, enrichment, applied: !!body.apply,
          actions: { new: 159, updated: 0, unchanged: 0, kept: 0 } };
      };
      const { page, errors } = await openAdmin(browser, status, { onImport });
      await page.evaluate(() => { window.Adjudication.initAdmin(); switchAdminTab("disagreements"); });
      await page.locator("#adj-preview-btn").click();
      await page.locator("#adj-apply-btn").waitFor();
      assert.deepEqual(requests, [{ apply: false }]);
      const preview = await page.locator("#adj-import-result").innerText();
      assert.match(preview, /159 rows in the file\. An import would add 159 new/);
      assert.match(preview, /Abstracts for 130 of 136 papers \(87 from Europe PMC\); 6 without one/);
      assert.match(preview, /54 FLoRA found no original/);
      if (process.env.FLORA_UI_SCREENSHOTS) {
        await page.locator("#admin-tab-disagreements").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "disagreements-preview.png") });
      }
      await page.locator("#adj-apply-btn").click();
      await page.waitForFunction(() => /Imported 159 new/.test(document.querySelector("#adj-import-result")?.innerText || ""));
      assert.deepEqual(requests, [{ apply: false }, { apply: true }]);
      assert.deepEqual(await page.locator(".adj-stats dd").allInnerTexts(), ["159", "159", "0", "0", "0"]);
      assert.match(await page.locator(".adj-import h3").innerText(), /Import again/);
      assert.match(await page.locator(".adj-card .adj-kinds").first().innerText(), /47 Same original, different outcome/);
      if (process.env.FLORA_UI_SCREENSHOTS) {
        await page.locator("#admin-tab-disagreements").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "disagreements-imported.png") });
      }
      assert.deepEqual(errors, []);
      await page.close();
    }

    // An import the server refuses shows its message.
    {
      const { page, errors } = await openAdmin(browser,
        { enabled: true, ready: true, counts: { records: 0 }, source: "x", source_url: "https://example.org/x.csv" });
      await page.route("**/api/admin/disagreements/import", route =>
        route.fulfill({ status: 422, json: { detail: "The file cannot be imported: line 7: unknown kind 'x'" } }));
      await page.evaluate(() => { window.Adjudication.initAdmin(); switchAdminTab("disagreements"); });
      await page.locator("#adj-preview-btn").click();
      await page.locator("#adj-import-result .faq-error").waitFor();
      assert.match(await page.locator("#adj-import-result").innerText(), /line 7: unknown kind/);
      assert.equal(await page.locator("#adj-preview-btn").isEnabled(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Switched on, setup failed: the tab says so, and the error text is escaped.
    {
      const { page, errors } = await openAdmin(browser,
        { enabled: true, ready: false, error: "LockNotAvailable: <script>boom()</script>", counts: null });
      await page.evaluate(() => window.Adjudication.initAdmin());
      await page.evaluate(() => switchAdminTab("disagreements"));
      await page.locator(".adj-card-failed").waitFor();
      assert.match(await page.locator(".adj-card-failed").innerText(), /setup failed[\s\S]*rest of the app is unaffected/);
      assert.equal(await page.locator(".adj-card-failed script").count(), 0);
      assert.match(await page.locator(".adj-card-failed pre").innerText(), /LockNotAvailable: <script>boom\(\)<\/script>/);
      if (process.env.FLORA_UI_SCREENSHOTS) {
        await page.locator("#admin-tab-disagreements").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "disagreements-failed.png") });
      }
      assert.deepEqual(errors, []);
      await page.close();
    }

    // adjudication.js cannot be loaded: the admin panel works exactly as before.
    {
      const { page, errors } = await openAdmin(browser, { enabled: true, ready: true, counts: {} }, { scriptMissing: true });
      assert.equal(await page.evaluate(() => typeof window.Adjudication), "undefined");
      await page.evaluate(() => { switchAdminTab("disagreements"); switchAdminTab("priority"); });
      assert.equal(await page.locator("#admin-tab-priority").isVisible(), true);
      assert.equal(await page.locator("#admin-disagreements-tab").isHidden(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A reload with an admin already signed in, where adjudication.js arrives after
    // app.js has restored the session: the tab must still appear.
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.route("**/*", async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
        const files = {
          "/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
          "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css",
        };
        if (url.pathname === "/adjudication.js") await new Promise(resolve => setTimeout(resolve, 1200));
        if (files[url.pathname]) {
          const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
          return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
        }
        if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "admin", admin: { handle: "Hamid", trusted: true } } });
        if (url.pathname === "/api/admin/disagreements/status") return route.fulfill({ json: { enabled: true, ready: true, counts: {} } });
        return route.fulfill({ json: { records: [], entries: [], total: 0, runs: [], counts: {} } });
      });
      await page.goto("http://flora.test/");
      await page.locator("#admin-screen").waitFor();
      await page.locator("#admin-disagreements-tab").waitFor({ timeout: 5000 });
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("adjudication admin UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
