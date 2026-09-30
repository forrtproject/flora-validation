/* Run with: node tests/test_extractor_pipeline_ui.cjs
 * The Extractor Pipeline admin tab: run cards, the grouped log viewer, and the
 * absence of the removed orphan-cleanup stage and attention badge.
 * All requests are intercepted. No web server, database, or remote service runs.
 * FLORA_UI_SCREENSHOTS=<dir> saves desktop and mobile screenshots.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const RUN_ID = "cd9302ef-2130-4844-b344-2909c3f1db4a";
const FAILED_ID = "0fea72e0-37f0-4767-b32c-613135253484";

const completed = {
  run_id: RUN_ID, trigger: "admin", requested_stage: "full", requested_by: "hamid", status: "warning",
  created_at: "2026-09-30T19:12:39Z", started_at: "2026-09-30T19:12:50Z", finished_at: "2026-09-30T19:48:21Z",
  stage_status: { sync_csv: "SUCCESS", find_orphans: "SUCCESS", retire_superseded: "SUCCESS" },
  safety_report: {
    warning_codes: ["new_resolved_pair_ids"], import_completed: true, part1_completed: true,
    previous_resolved_count: 2999, candidate_resolved_count: 3653,
    added_count: 1137, removed_count: 483, removed_percent: 16.1054,
    retire: { status: "applied", retire: 550, flag: 10, retire_limit: 720 },
    orphan_report: { orphan_count: 1153, unvalidated_count: 552 },
    stage_seconds: { sync_csv: 2026.0, find_orphans: 3.4, retire_superseded: 84.7 },
  },
};
const failed = {
  run_id: FAILED_ID, trigger: "admin", requested_stage: "full", requested_by: "hamid", status: "failed",
  created_at: "2026-09-30T19:05:17Z", started_at: "2026-09-30T19:05:17Z", finished_at: "2026-09-30T19:05:20Z",
  stage_status: { sync_csv: "FAILED", find_orphans: "SKIPPED", retire_superseded: "SKIPPED" },
  safety_report: { error_code: "extractor_pipeline_error", warning_codes: [], stage_seconds: { sync_csv: 1.2 } },
};
const blocked = {
  run_id: "11111111-2222-3333-4444-555555555555", trigger: "scheduled", requested_stage: "full", requested_by: null,
  status: "blocked", created_at: "2026-09-29T02:00:01Z", started_at: "2026-09-29T02:00:02Z",
  finished_at: "2026-09-29T02:00:40Z",
  stage_status: { sync_csv: "FAILED", find_orphans: "SKIPPED", retire_superseded: "SKIPPED" },
  safety_report: { error_code: "baseline_snapshot_unavailable", warning_codes: [] },
};
const history = {
  days: 7, runs: [completed, failed, blocked],
  auto_retire: true, max_retire_percent: 15, config_errors: [],
};
const LIVE_ID = "77777777-0000-0000-0000-000000000000";
let liveRun = {
  run_id: LIVE_ID, trigger: "scheduled", requested_stage: "full", requested_by: null, status: "running",
  created_at: "2026-10-01T02:00:00Z", started_at: "2026-10-01T02:00:01Z", finished_at: null,
  stage_status: { sync_csv: "PENDING", find_orphans: "PENDING", retire_superseded: "PENDING" },
  safety_report: {},
};
let liveLog = "[2026-10-01T02:00:01Z] [pipeline] START run_id=live trigger=scheduled requested_stage=full\n";

const progress = Array.from({ length: 110 }, (_, i) => `  … imported ${(i + 1) * 10} records`);
const plan = Array.from({ length: 30 }, (_, i) =>
  `  ${i % 10 === 9 ? "FLAG  " : "RETIRE"} 0000${i}-uuid  status=unvalidated  set_aside  pair_id=p${i}`);
const LOG = [
  "=".repeat(88),
  `[2026-09-30T19:12:50Z] [pipeline] START run_id=${RUN_ID} trigger=admin requested_stage=full data_dir=/app/data auto_retire=on`,
  "[2026-09-30T19:12:51Z] [snapshot] restored the last import extracted_20260912T142657Z_88d4c0c8.csv from the database",
  "[2026-09-30T19:12:53Z] [sync_csv] START python3.12 sync_csv.py --data-dir /app/data",
  "[sync_csv] Snapshot safety: previous=2999 candidate=3653 added=1137 removed=483 removed_percent=16.11%",
  "[sync_csv] WARNING new_resolved_pair_ids: 1137 new resolved pair_id(s); sample: <script>unexpected()</script>",
  ...progress,
  "[sync_csv] Import complete; promotion verified → /app/data/extracted_latest.csv",
  "[2026-09-30T19:46:39Z] [sync_csv] SUCCESS (2026.0s)",
  "[2026-09-30T19:46:40Z] [sync_csv] snapshot bound extracted_20260930T191256Z_cd9302ef.csv sha256=02f32ade",
  "[2026-09-30T19:46:41Z] [snapshot] extracted_20260930T191256Z_cd9302ef.csv stored in the database",
  "[2026-09-30T19:46:41Z] [find_orphans] START python3.12 find_orphans.py --input x.csv",
  "Records this CSV does not list:   1,153",
  "[2026-09-30T19:46:44Z] [find_orphans] SUCCESS (3.4s)",
  "[2026-09-30T19:46:45Z] [retire_superseded] START python3.12 csv_to_db.py --retire github",
  ...plan,
  "Retired and archived: 550 record(s)",
  "[2026-09-30T19:48:10Z] [retire_superseded] SUCCESS (84.7s)",
  "[2026-09-30T19:48:12Z] [pipeline] WARNING sync_csv=SUCCESS find_orphans=SUCCESS retire_superseded=SUCCESS",
  "=".repeat(88),
].join("\n");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  // Numbers are formatted with the browser's locale; pin it so "1,137" is stable.
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 }, acceptDownloads: true, locale: "en-US",
  });
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  let runs = history;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    requests.push(`${route.request().method()} ${url.pathname}`);
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/maintenance/runs") return route.fulfill({ status: 200, json: runs });
    if (url.pathname === `/api/admin/maintenance/runs/${RUN_ID}`) {
      return route.fulfill({ status: 200, json: { ...completed, log_text: LOG } });
    }
    if (url.pathname === `/api/admin/maintenance/runs/${LIVE_ID}`) {
      return route.fulfill({ status: 200, json: { ...liveRun, log_text: liveLog } });
    }
    if (url.pathname === "/api/admin/maintenance/run") {
      return route.fulfill({ status: 202, json: { run_id: "99999999-0000-0000-0000-000000000000", status: "queued", stage: "full" } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    return route.fulfill({ status: 200, json: { records: [], sources: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#admin-tab-entries").classList.add("hidden");
      document.querySelector("#admin-tab-maintenance").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
    });
    await page.evaluate(() => fetchMaintenanceRuns());

    // The removed stage and the unexplained attention badge are gone.
    assert.equal(await page.locator("#admin-maintenance-badge").count(), 0);
    assert.equal(await page.locator('[data-stage="cleanup"]').count(), 0);
    assert.equal(await page.locator('.admin-tab-btn[data-tab="maintenance"]').innerText(), "Extractor Pipeline");

    // Settings shown in words.
    assert.equal(await page.locator("#pipeline-settings").innerText(), "Automatic retire on, at most 15% of records per run");
    // One way to run it from the page: the whole routine.
    assert.deepEqual(await page.locator(".pipeline-run-btn").evaluateAll(buttons => buttons.map(b => b.dataset.stage)), ["full"]);
    assert.equal(await page.locator("#pipeline-actions details").count(), 0);
    // No removal limit any more: a drop in the CSV never stops a run.
    const steps = await page.locator(".pipeline-step").allInnerTexts();
    assert.match(steps[0], /^1\s+Download and compare/);
    assert.match(steps[0], /A dropped pair is not deleted here/);
    assert.doesNotMatch(steps.join(" "), /stops before anything changes|removal limit/i);
    assert.equal(await page.locator("#pipeline-config-alert").isHidden(), true);

    // The heading says what Refresh does and when the list was last loaded.
    assert.match(await page.locator("#pipeline-next-run").innerText(), /^02:00 UTC/);
    assert.match(await page.locator("#pipeline-updated").innerText(), /\d{2}:\d{2}:\d{2}\s+Press Refresh history/);
    assert.match(await page.locator("#pipeline-refresh-hint").innerText(), /Reloads the runs listed below/);
    const listRequests = () => requests.filter(r => r === "GET /api/admin/maintenance/runs").length;
    const before = listRequests();
    await page.locator("#pipeline-refresh-btn").click();
    await page.waitForFunction(() => !document.querySelector("#pipeline-refresh-btn").disabled);
    assert.equal(listRequests(), before + 1);
    assert.equal((await page.locator("#pipeline-refresh-btn").textContent()).trim(), "Refresh history");

    // A run whose only warning is "new pairs arrived" reads as completed.
    const card = page.locator(`.pipeline-run-card[data-run-id="${RUN_ID}"]`);
    assert.equal(await card.locator(".pipeline-status").innerText(), "Completed");
    assert.match(await card.locator(".pipeline-run-headline").innerText(),
      /1,137 new pairs imported · 483 no longer listed · 550 retired · 10 flagged for review\./);
    assert.deepEqual(await card.locator(".pipeline-stat dd").allInnerTexts(), ["3,653", "+1,137", "−483", "550", "10"]);
    assert.deepEqual(await card.locator(".pipeline-tl-label").allInnerTexts(), ["Download & import", "Orphan report", "Retire withdrawn"]);
    assert.match(await card.locator(".pipeline-timeline").innerText(), /33m 46s/);

    // Failures explain themselves in words.
    const failedCard = page.locator(`.pipeline-run-card[data-run-id="${FAILED_ID}"]`);
    assert.equal(await failedCard.locator(".pipeline-status").innerText(), "Failed");
    assert.match(await failedCard.locator(".pipeline-alert").innerText(), /GITHUB_TOKEN was refused/);
    assert.equal(await failedCard.locator(".pipeline-tl-skipped").count(), 2);
    const blockedCard = page.locator(".pipeline-run-card").nth(2);
    assert.match(await blockedCard.innerText(), /Nightly run/);
    assert.match(await blockedCard.locator(".pipeline-alert").innerText(), /last import's CSV could not be found/);

    // The complete log, grouped by step, loaded only when asked for.
    assert(!requests.some(r => r.endsWith(`/runs/${RUN_ID}`)));
    await card.locator(".pipeline-log-btn").click();
    await card.locator(".log-section").first().waitFor();
    assert(requests.includes(`GET /api/admin/maintenance/runs/${RUN_ID}`));
    assert.deepEqual(await card.locator(".log-section summary b").allInnerTexts(),
      ["Run", "Download & import", "Orphan report", "Retire withdrawn", "Run"]);
    assert.equal(await card.locator(".log-section-done").count(), 3);
    // Collapsed sections hide their lines from innerText; read the text itself.
    assert.match(await card.locator(".log-fold").textContent(), /imported 1100 records\s*110 progress lines folded/);
    assert.match(await card.locator(".log-group summary").textContent(), /30 plan lines: 27 retire, 3 flag/);
    assert.equal(await card.locator(".pipeline-log-panel script").count(), 0, "log text is escaped");
    assert.equal(await card.locator(".log-line.is-ok").filter({ hasText: "stored in the database" }).count(), 1);
    // Notes a stage writes after finishing stay with that stage.
    assert.match(await card.locator(".log-section").nth(1).textContent(), /snapshot bound/);

    await card.locator('[data-log-action="raw"]').click();
    assert.equal(await card.locator(".pipeline-log-raw").isVisible(), true);
    assert.equal(await card.locator(".pipeline-log-sections").isHidden(), true);
    await card.locator('[data-log-action="raw"]').click();
    const download = page.waitForEvent("download");
    await card.locator('[data-log-action="download"]').click();
    assert.equal((await download).suggestedFilename(), "extractor-run-cd9302ef.log");

    // An open log survives the automatic refresh.
    await page.evaluate(() => fetchMaintenanceRuns());
    assert.equal(await card.locator(".pipeline-log-panel").isVisible(), true);
    assert.equal((await card.locator(".pipeline-log-btn").textContent()).trim(), "Hide log");

    if (process.env.FLORA_UI_SCREENSHOTS) {
      await fs.mkdir(process.env.FLORA_UI_SCREENSHOTS, { recursive: true });
      await page.locator("#admin-tab-maintenance").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "extractor-desktop.png") });
    }
    await card.locator(".pipeline-log-btn").click();
    assert.equal(await card.locator(".pipeline-log-panel").isHidden(), true);

    // Starting a run asks for no confirmation and sends only the stage.
    let posted = null;
    page.on("request", request => {
      if (request.url().endsWith("/api/admin/maintenance/run")) posted = request.postDataJSON();
    });
    runs = { ...history, runs: [{ ...completed, run_id: "99999999-0000-0000-0000-000000000000", status: "running",
      finished_at: null, stage_status: { sync_csv: "PENDING", find_orphans: "PENDING", retire_superseded: "PENDING" },
      safety_report: {} }, ...history.runs] };
    await page.locator('.pipeline-run-btn[data-stage="full"]').click();
    await page.waitForFunction(() => !document.querySelector("#pipeline-live-status").classList.contains("hidden"));
    assert.deepEqual(posted, { stage: "full" });
    assert.match(await page.locator("#pipeline-live-status").innerText(), /Running: full sync started by hamid/);
    assert.equal(await page.locator('.pipeline-run-btn[data-stage="full"]').isDisabled(), true);
    assert.equal(await page.locator(".pipeline-tl-running").count(), 1);
    await page.evaluate(() => clearTimeout(_maintenancePollTimer));

    // An open log of a running run follows the run and keeps its view.
    const liveHistory = () => ({ ...history, runs: [liveRun, ...history.runs] });
    await page.evaluate(data => renderMaintenanceRuns(data), liveHistory());
    const live = page.locator(`.pipeline-run-card[data-run-id="${LIVE_ID}"]`);
    await live.locator(".pipeline-log-btn").click();
    await live.locator(".pipeline-log-toolbar").waitFor();
    assert.match(await live.locator(".pipeline-log-toolbar span").innerText(), /^1 line so far$/);
    await live.locator('[data-log-action="raw"]').click();
    const detailRequests = () => requests.filter(r => r === `GET /api/admin/maintenance/runs/${LIVE_ID}`).length;
    assert.equal(detailRequests(), 1);
    // Same state: the poll neither refetches nor resets the view.
    await page.evaluate(data => renderMaintenanceRuns(data), liveHistory());
    assert.equal(detailRequests(), 1);
    assert.equal(await live.locator(".pipeline-log-raw").isVisible(), true);
    // The sync finished: the log is fetched again, still shown as plain text.
    liveRun = { ...liveRun, stage_status: { ...liveRun.stage_status, sync_csv: "SUCCESS" } };
    liveLog += "[2026-10-01T02:00:02Z] [sync_csv] START python sync_csv.py\n"
      + "[2026-10-01T02:30:02Z] [sync_csv] SUCCESS (1800.0s)\n";
    await page.evaluate(data => renderMaintenanceRuns(data), liveHistory());
    await page.waitForFunction(id => /sync_csv\] SUCCESS/.test(
      document.querySelector(`.pipeline-log-panel[data-run-id="${id}"] .pipeline-log-raw`)?.textContent || ""), LIVE_ID);
    assert.equal(detailRequests(), 2);
    assert.equal(await live.locator(".pipeline-log-raw").isVisible(), true);
    assert.equal(await live.locator('[data-log-action="raw"]').getAttribute("aria-pressed"), "true");
    assert.match(await live.locator(".pipeline-log-toolbar span").innerText(), /^3 lines so far$/);
    await live.locator(".pipeline-log-btn").click();

    // Counts from a run that imported nothing are not reported as imported.
    const headlines = await page.evaluate(() => [
      maintenanceHeadline({ status: "blocked", safety_report: {
        candidate_resolved_count: 80, added_count: 2, removed_count: 20, previous_resolved_count: 98 } }),
      maintenanceHeadline({ status: "success", safety_report: {
        candidate_resolved_count: 30000, added_count: 0, removed_count: 0, previous_resolved_count: null,
        import_completed: true } }),
      maintenanceHeadline({ status: "warning", safety_report: {
        candidate_resolved_count: 3653, previous_resolved_count: null, warning_codes: ["baseline_unavailable"],
        import_completed: true } }),
    ]);
    assert.deepEqual(headlines, [
      "Nothing was imported. The new CSV lists 80 pairs (2 new, 20 no longer listed).",
      "Imported 30,000 pairs; there was no earlier import to compare with.",
      "Imported 3,653 pairs, with no comparison to the last import.",
    ]);
    // Log sections agree with the timeline, and a cut-off step says so.
    const states = await page.evaluate(() => [
      parseRunLog("[2026-10-01T02:00:02Z] [sync_csv] START x\n[2026-10-01T02:00:03Z] [sync_csv] SUCCESS (1.0s)\n"
        + "[2026-10-01T02:00:04Z] [sync_csv] BLOCKED part1_completion_unverified\n", true).map(s => s.status),
      parseRunLog("[2026-10-01T02:00:02Z] [find_orphans] START x\nhalf a report\n", true).map(s => s.status),
      parseRunLog("[2026-10-01T02:00:02Z] [find_orphans] START x\nhalf a report\n", false).map(s => s.status),
    ]);
    assert.deepEqual(states, [["blocked"], ["unknown"], ["running"]]);

    // Automatic retire switched off, and a misconfigured cap, are both visible.
    await page.evaluate(data => renderMaintenanceRuns(data),
      { ...history, auto_retire: false, config_errors: ["EXTRACTOR_MAX_RETIRE_PERCENT must be a finite number."] });
    assert.match(await page.locator("#pipeline-settings").innerText(), /Automatic retire off/);
    assert.equal(await page.locator(".pipeline-step.is-off").count(), 1);
    assert.equal(await page.locator("#pipeline-config-alert").isVisible(), true);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(data => renderMaintenanceRuns(data), history);
    const bounds = await page.locator(".pipeline-console").boundingBox();
    assert(bounds.width <= 390 && bounds.x >= 0, "the pipeline tab fits a phone");
    assert.equal(await page.locator('.pipeline-run-btn[data-stage="full"]').isVisible(), true);
    // The phone layout hides .ghost-btn elsewhere; the log and Refresh must stay.
    assert.equal(await page.locator(".pipeline-log-btn").first().isVisible(), true);
    assert.equal(await page.locator("#pipeline-refresh-btn").isVisible(), true);
    if (process.env.FLORA_UI_SCREENSHOTS) {
      await page.locator("#admin-tab-maintenance").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "extractor-mobile.png") });
    }
    assert.deepEqual(errors, []);
    console.log("extractor pipeline UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
