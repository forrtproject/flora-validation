/* Run with: node tests/test_pipeline_ui.cjs
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const requests = [];
  const jobId = "2bb9fa81-98bf-4bcc-a204-5a35a2f3a7ae";
  const job = {
    job_id: jobId, status: "success", trigger: "admin", requested_by: "Test admin",
    created_at: "2026-09-15T10:00:00Z", started_at: "2026-09-15T10:00:00Z",
    finished_at: "2026-09-15T10:01:00Z", has_csv: true, has_report: true,
    log_tail: "Final CSV and reports ready. <script>unexpected()</script>", log_length: 60,
  };
  const preview = { ...job, report_rows: 3572, log_tail: "Final CSV and reports ready.", log_length: 28 };
  let status = { jobs: [job], active: false, sources: [] };
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    requests.push(url.pathname + url.search);
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname.includes("/artifacts/") || url.pathname === "/api/admin/flora/export.csv") {
      return route.fulfill({ status: 200, contentType: "text/plain", body: "id,id_md5\n1,hash\n" });
    }
    if (url.pathname === "/api/admin/source-sync/dispatch") {
      return route.fulfill({ status: 202, json: { status: "queued", job_id: jobId } });
    }
    if (url.pathname === "/api/admin/source-sync/status") return route.fulfill({ status: 200, json: status });
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ status: 200, json: [] });
    return route.fulfill({ status: 200, json: { records: [], sources: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });
  try {
    await page.goto("http://flora.test/");
    await page.evaluate(data => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#admin-tab-entries").classList.add("hidden");
      document.querySelector("#admin-tab-sources").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      renderSourceSync(data);
    }, status);
    assert.equal(await page.locator(".src-sync-artifact-btn").count(), 3);
    assert.equal(await page.locator("#src-sync-log script").count(), 0);
    assert.equal(await page.locator("#src-sync-run-btn").isEnabled(), true);
    const csvDownload = page.waitForEvent("download");
    await page.locator('[data-artifact="flora.csv"]').click();
    assert.equal((await csvDownload).suggestedFilename(), "flora.csv");
    assert(requests.includes(`/api/admin/source-sync/jobs/${jobId}/artifacts/flora.csv`));
    const reportDownload = page.waitForEvent("download");
    await page.locator('[data-artifact="report.md"]').click();
    assert.match((await reportDownload).suggestedFilename(), /report\.md$/);

    await page.evaluate(data => renderSourceSync(data), {
      jobs: [{ ...job, report_status: "needs_attention", report_rows: 3572, warning_count: 2, failure_count: 1 }],
      active: false,
    });
    assert.equal(await page.locator(".src-sync-badge-needs_attention").innerText(), "NEEDS REVIEW");
    assert.match(await page.locator(".src-sync-artifact-counts").innerText(), /3,572 rows · 2 warnings · 1 error/);
    assert.equal(await page.locator('[data-artifact="flora.csv"]').count(), 1);

    await page.evaluate(data => renderSourceSync(data), {
      jobs: [{ ...job, status: "running", finished_at: null, has_csv: false, has_report: false }],
      active: true, active_job_id: jobId,
    });
    assert.equal(await page.locator("#src-sync-run-btn").isDisabled(), true);
    assert.equal(await page.locator(".src-sync-artifact-btn").count(), 0);

    await page.evaluate(data => renderSourceSync(data), status);
    await page.locator("#src-sync-run-btn").click();
    await page.waitForFunction(() => document.querySelector("#src-sync-run-btn").disabled);
    assert(requests.includes("/api/admin/source-sync/dispatch"));
    await page.evaluate(() => clearTimeout(_srcSyncPollTimer));
    status = { jobs: [{ ...job, status: "failed", has_csv: false, has_report: true }], active: false, sources: [] };
    await page.evaluate(() => fetchSourceSync());
    assert.match(await page.locator("body").innerText(), /Pipeline failed\. Review the report and log\./);
    assert.equal(await page.locator('[data-artifact="flora.csv"]').count(), 0);
    assert.equal(await page.locator('[data-artifact="report.md"]').count(), 1);

    await page.evaluate(data => renderSourceSync(data), {
      jobs: [{ ...job, status: "failed", has_csv: false, has_recovery_csv: true, has_report: true }],
      active: false, sources: [],
    });
    assert.equal(await page.locator('[data-artifact="flora.csv"]').count(), 0);
    assert.equal(await page.locator('[data-artifact="recovery.csv"]').textContent(), "Download recovery CSV");
    assert.match(await page.locator(".src-sync-artifact-note").innerText(), /committed CSV is available for recovery/);
    const recoveryDownload = page.waitForEvent("download");
    await page.locator('[data-artifact="recovery.csv"]').click();
    assert.match((await recoveryDownload).suggestedFilename(), /recovery\.csv$/);
    assert(requests.includes(`/api/admin/source-sync/jobs/${jobId}/artifacts/recovery.csv`));

    await page.evaluate(() => {
      document.querySelector("#admin-tab-sources").classList.add("hidden");
      document.querySelector("#admin-tab-flora").classList.remove("hidden");
      _floraSearch = "example";
    });
    const fullDownload = page.waitForEvent("download");
    await page.locator("#flora-export-all-btn").click();
    await fullDownload;
    assert(requests.includes("/api/admin/flora/export.csv"));
    const filteredDownload = page.waitForEvent("download");
    await page.locator("#flora-export-btn").click();
    await filteredDownload;
    assert(requests.some(request => request.startsWith("/api/admin/flora/export.csv?") && request.includes("search=example")));

    await page.evaluate(data => {
      document.querySelector("#admin-tab-flora").classList.add("hidden");
      document.querySelector("#admin-tab-sources").classList.remove("hidden");
      renderSourceSync(data);
    }, { jobs: [preview], active: false, sources: [] });
    if (process.env.FLORA_UI_SCREENSHOTS) {
      await fs.mkdir(process.env.FLORA_UI_SCREENSHOTS, { recursive: true });
      await page.locator("#src-sync-panel").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "pipeline-desktop.png") });
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(data => {
      document.querySelector("#admin-tab-flora").classList.add("hidden");
      document.querySelector("#admin-tab-sources").classList.remove("hidden");
      renderSourceSync(data);
    }, { jobs: [preview], active: false, sources: [] });
    const bounds = await page.locator("#src-sync-panel").boundingBox();
    assert(bounds.width <= 390 && bounds.x >= 0, "Pipeline panel fits narrow viewports");
    assert.equal(await page.locator('[data-artifact="flora.csv"]').isVisible(), true);
    assert.equal(await page.locator('[data-artifact="report.md"]').isVisible(), true);
    assert.equal(await page.locator("#src-sync-refresh-btn").isVisible(), true);
    if (process.env.FLORA_UI_SCREENSHOTS) {
      await page.locator("#src-sync-panel").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "pipeline-mobile.png") });
    }
    assert.deepEqual(errors, []);
    console.log("Pipeline browser checks passed: run, active/failure states, retained downloads, full/filtered exports, mobile panel.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
