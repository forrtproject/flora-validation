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

async function openAdmin(browser, status, { scriptMissing = false } = {}) {
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
    if (url.pathname === "/api/admin/disagreements/status") return route.fulfill({ json: status });
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
      assert.match(await page.locator(".adj-next").innerText(), /Nothing imported yet/);
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
