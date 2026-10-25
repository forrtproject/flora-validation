/* Run with: node tests/test_flora_findings_ui.cjs
 * The entry checks' findings in the FLoRA tab: the Problems and Possible problems
 * filters and counts, the list's chip, and the record view's Check findings, where
 * a finding is marked "Not a problem" with a required comment, and the mark undone.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

// A key holds "|" and the flagged value, any text: it must travel intact, in the body.
const URL_KEY = "invalid_url|REPL-000001|url_r|<img src=x onerror=alert(1)> | https://a.example/?q=1&r=2";
const PAIR_KEY = "reversed_pair|REPL-000001,REPL-000002|doi_o|10.1/a | 10.1/b";
const TITLE_KEY = "file_name_title|REPL-000001|title_r|analysis";

/** Wait for something the page's requests change on this side. */
async function until(test, ms = 3000) {
  const end = Date.now() + ms;
  while (!test()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const gridQueries = [];
  const marks = [];
  const undos = [];
  let markStatus = 200;
  let problemsCount = 4;
  const findings = () => [
    { finding_key: URL_KEY, part: "structural", check_code: "invalid_url", check_heading: "Invalid URLs",
      level: "problem", flora_ids: ["REPL-000001"], column_name: "url_r",
      value: "<img src=x onerror=alert(1)> | https://a.example/?q=1&r=2", detail: "url_r holds <b>two</b> links",
      first_seen_at: "2026-10-20T10:00:00Z", last_seen_at: "2026-10-25T10:00:00Z",
      dismissed_at: null, dismissed_by: null, dismiss_note: null },
    { finding_key: PAIR_KEY, part: "structural", check_code: "reversed_pair",
      check_heading: "Pair entered both ways round", level: "possible_problem",
      flora_ids: ["REPL-000001", "REPL-000002"], column_name: "doi_o", value: "10.1/a | 10.1/b",
      detail: "the paper pair is entered both ways round", first_seen_at: "2026-10-21T10:00:00Z",
      dismissed_at: null, dismissed_by: null, dismiss_note: null },
    { finding_key: TITLE_KEY, part: "structural", check_code: "file_name_title",
      check_heading: "Title looks like a file or project name", level: "possible_problem",
      flora_ids: ["REPL-000001"], column_name: "title_r", value: "Analysis",
      detail: "title_r is a generic name, not a title", first_seen_at: "2026-10-22T10:00:00Z",
      dismissed_at: "2026-10-23T10:00:00Z", dismissed_by: "luke", dismiss_note: "The <i>report</i> is called that" },
  ];
  const record = () => ({
    flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
    type: "replication", source: "replications", title_o: "T", merged_sources: [],
    editing: { version: 1, history: [], source_edits: [],
               fields: [{ field: "title_o", label: "title_o", group: "Original", long: false, choices: null, value: "T" }] },
    check_findings: findings(),
    check_runs: { structural: { run_id: 4, part: "structural", status: "finished",
                                started_at: "2026-10-25T16:35:29Z", finished_at: "2026-10-25T16:35:35Z" },
                  network: null },
  });
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { errors.push("dialog: " + dialog.message()); dialog.dismiss(); });
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/flora/findings/dismiss") {
      const body = JSON.parse(request.postData());
      if (request.method() === "DELETE") {
        undos.push(body);
        return route.fulfill({ json: { finding_key: body.finding_key, withdrawn_by: "test-admin",
                                       withdrawn: { dismissed_by: "luke" } } });
      }
      marks.push(body);
      if (markStatus !== 200) {
        return route.fulfill({ status: markStatus, json: { detail: "luke already marked this finding not a problem." } });
      }
      problemsCount = 3;
      return route.fulfill({ json: { finding_key: body.finding_key, dismissed_at: "2026-10-25T12:00:00Z",
                                     dismissed_by: "test-admin", dismiss_note: body.note.trim() } });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001") return route.fulfill({ json: record() });
    if (url.pathname === "/api/admin/flora") {
      gridQueries.push(url.search);
      return route.fulfill({ json: {
        records: [
          { flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
            type: "replication", source: "replications", doi_o: "10.1/a", ref_o: "Ref O", doi_r: "10.1/b",
            ref_r: "Ref R", outcome: "successful", open_findings: { problem: 1, possible_problem: 2 } },
          { flora_id: "REPL-000002", export_id: "FLORA-000002", source_display_id: "REPL-000002",
            type: "replication", source: "replications", doi_o: "10.1/b", ref_o: "Ref O", doi_r: "10.1/a",
            ref_r: "Ref R", outcome: "failed", open_findings: { problem: 0, possible_problem: 1 } },
          { flora_id: "REPL-000003", export_id: "FLORA-000003", source_display_id: "REPL-000003",
            type: "replication", source: "replications", doi_o: "10.1/c", ref_o: "Ref O", doi_r: "10.1/d",
            ref_r: "Ref R", outcome: "failed", open_findings: null },
        ],
        total: 3, page: 1, per_page: 50,
        counts: { all_records: 3, replications: 3, reproductions: 0, edited: 0, conflicts: 0,
                  problems: problemsCount, possible_problems: 2, sources: [], outcomes: [] } } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ json: [] });
    return route.fulfill({ json: { records: [], sources: [], jobs: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      switchAdminTab("flora");
    });

    // The counts, and a chip on each row with open findings, named by its worst level.
    await page.locator("#flora-table-body .flora-view-btn").first().waitFor();
    assert.equal(await page.locator("#ffc-problems").innerText(), "4");
    assert.equal(await page.locator("#ffc-possible").innerText(), "2");
    const chips = page.locator("#flora-table-body .flora-findings-chip");
    assert.equal(await chips.count(), 2);
    assert.equal(await chips.nth(0).innerText(), "problem");
    assert.equal(await chips.nth(0).getAttribute("title"), "Check findings: 1 problem, 2 possible problems");
    assert.match(await chips.nth(0).getAttribute("class"), /flora-findings-chip-problem/);
    assert.equal(await chips.nth(1).innerText(), "possible problem");
    assert.equal(await chips.nth(1).getAttribute("title"), "Check findings: 1 possible problem");
    for (const filter of ["problems", "possible_problems"]) {
      const button = page.locator(`#admin-tab-flora .admin-filter-btn[data-filter="${filter}"]`);
      assert.match(await button.getAttribute("title"), /entry checks found/);
      const sent = gridQueries.length;
      await button.click();
      await until(() => gridQueries.length > sent);
      assert.match(gridQueries[gridQueries.length - 1], new RegExp("[?&]" + filter + "=true(&|$)"));
      assert.equal(await button.evaluate(el => el.classList.contains("active")), true);
    }
    // One chip at a time: Possible problems alone does not also ask for problems.
    assert.doesNotMatch(gridQueries[gridQueries.length - 1], /[?&]problems=true/);
    const sent = gridQueries.length;
    await page.locator('#admin-tab-flora .admin-filter-btn[data-filter="all"]').click();
    await until(() => gridQueries.length > sent);
    assert.doesNotMatch(gridQueries[gridQueries.length - 1], /problems/);
    await page.locator("#flora-table-body .flora-view-btn").first().waitFor();

    // The record view: each finding with its level, check, column, value and detail,
    // every value escaped.
    await page.locator(".flora-view-btn").first().click();
    await page.locator("#flora-detail-modal:not(.hidden) #flora-findings").waitFor();
    const section = page.locator("#flora-findings");
    assert.equal(await section.locator("h4").textContent(), "Check findings");
    const items = section.locator(".flora-finding");
    assert.equal(await items.count(), 3);
    const first = items.nth(0);
    assert.equal(await first.locator(".flora-level-badge").innerText(), "Problem");
    assert.match(await first.locator(".flora-finding-head").innerText(), /Invalid URLs\s+url_r/);
    assert.equal(await first.locator(".flora-finding-value").evaluate(el => el.tagName), "CODE");
    assert.equal(await first.locator(".flora-finding-value").innerText(),
                 "<img src=x onerror=alert(1)> | https://a.example/?q=1&r=2");
    assert.equal(await first.locator(".flora-finding-detail").innerText(), "url_r holds <b>two</b> links");
    assert.equal(await section.locator("img, i, .flora-finding-detail b").count(), 0);
    const date = iso => page.evaluate(value => new Date(value).toLocaleDateString(), iso);
    assert.match(await first.locator(".flora-finding-meta").innerText(),
                 new RegExp("^First seen " + (await date("2026-10-20T10:00:00Z")).replace(/[.]/g, "\\.") + "$"));
    assert.equal(await items.nth(1).locator(".flora-level-badge").innerText(), "Possible problem");
    assert.match(await items.nth(1).locator(".flora-finding-meta").innerText(), /Also about FLoRA record REPL-000002$/);
    // A finding already marked: who, when and why, with Undo.
    const marked = items.nth(2);
    assert.equal(await marked.locator(".flora-finding-mark").innerText(),
                 "Marked not a problem by luke on " + await date("2026-10-23T10:00:00Z") +
                 ": The <i>report</i> is called that Undo");
    assert.equal(await marked.locator(".flora-dismiss-btn").count(), 0);
    // When the checks ran, and when a correction is checked.
    const runs = await section.locator(".flora-findings-runs").innerText();
    const ran = await page.evaluate(() => new Date("2026-10-25T16:35:35Z").toLocaleString());
    assert.equal(runs, "Checks last ran: " + ran + "; network: not yet. " +
                       "A saved correction is checked again within about 5 minutes.");
    // The findings are not repeated as raw columns in the record's context table.
    assert.doesNotMatch(await page.locator(".flora-detail-table").innerText(), /check_/);

    // "Not a problem" opens a comment box; Cancel closes it, emptied.
    await page.locator('#flora-detail-body [data-field="title_o"]').fill("An unsaved correction");
    await first.locator(".flora-dismiss-btn").click();
    const form = first.locator(".flora-dismiss-form");
    assert.equal(await form.isVisible(), true);
    assert.equal(await first.locator(".flora-dismiss-btn").isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.classList.contains("flora-dismiss-note")), true);
    await form.locator(".flora-dismiss-note").fill("draft");
    await form.locator(".flora-dismiss-cancel").click();
    assert.equal(await form.isVisible(), false);
    assert.equal(await first.locator(".flora-dismiss-btn").isVisible(), true);
    assert.equal(await form.locator(".flora-dismiss-note").inputValue(), "");

    // The comment is required: nothing is sent without one.
    await first.locator(".flora-dismiss-btn").click();
    await form.locator(".flora-dismiss-save").click();
    assert.match(await form.locator(".flora-finding-msg").innerText(), /at least 3 characters/);
    await form.locator(".flora-dismiss-note").fill("  ab  ");
    await form.locator(".flora-dismiss-save").click();
    assert.match(await form.locator(".flora-finding-msg").innerText(), /at least 3 characters/);
    assert.equal(marks.length, 0);

    // A refusal is shown in the box, which stays open to try again.
    markStatus = 400;
    await form.locator(".flora-dismiss-note").fill("The link list is deliberate");
    await form.locator(".flora-dismiss-save").click();
    await page.waitForFunction(() => /already marked/.test(document.querySelector("#flora-findings .flora-finding-msg")?.textContent || ""));
    assert.equal(await form.locator(".flora-dismiss-save").isEnabled(), true);
    markStatus = 200;

    // Saved: the key travels intact in the body, the finding shows the mark, the
    // list's counts are fetched again, and the unsaved correction is still there.
    const before = gridQueries.length;
    await form.locator(".flora-dismiss-save").click();
    await page.waitForFunction(() => document.querySelectorAll("#flora-findings .flora-finding-mark").length === 2);
    assert.deepEqual(marks[1], { finding_key: URL_KEY, note: "The link list is deliberate" });
    assert.equal(await items.nth(0).locator(".flora-finding-mark").innerText(),
                 "Marked not a problem by test-admin on " + await date("2026-10-25T12:00:00Z") +
                 ": The link list is deliberate Undo");
    assert.equal(await items.nth(0).locator(".flora-dismiss-btn").count(), 0);
    await page.waitForFunction(() => document.querySelector("#ffc-problems").textContent === "3");
    assert(gridQueries.length > before, "the list is fetched again");
    assert.equal(await page.locator('#flora-detail-body [data-field="title_o"]').inputValue(), "An unsaved correction");
    assert.match(await page.locator("#toast").innerText(), /Marked not a problem/);

    // Undo withdraws a mark: the finding needs review again.
    await items.nth(2).locator(".flora-undismiss-btn").click();
    await page.waitForFunction(() => document.querySelectorAll("#flora-findings .flora-finding")[2]
      ?.querySelector(".flora-dismiss-btn"));
    assert.deepEqual(undos, [{ finding_key: TITLE_KEY }]);
    assert.equal(await items.nth(2).locator(".flora-finding-mark").count(), 0);
    assert.equal(await items.nth(2).locator(".flora-dismiss-btn").textContent(), "Not a problem");
    assert.match(await page.locator("#toast").innerText(), /needs review again/);

    // A record without findings says so, and still says when the checks ran.
    const empty = await page.evaluate(() => {
      const box = document.createElement("div");
      box.innerHTML = floraFindings({ flora_id: "REPL-000009", check_findings: [],
        check_runs: { structural: { status: "running", started_at: "2026-10-25T16:40:00Z" },
                      network: { status: "incomplete", started_at: "2026-10-25T04:00:00Z",
                                 finished_at: "2026-10-25T04:45:00Z" } } });
      return box.textContent;
    });
    const running = await page.evaluate(() => new Date("2026-10-25T16:40:00Z").toLocaleString());
    const incomplete = await page.evaluate(() => new Date("2026-10-25T04:45:00Z").toLocaleString());
    assert.equal(empty, "Check findingsNo open findings.Checks last ran: " + running + " (running); network: " +
                        incomplete + " (incomplete). A saved correction is checked again within about 5 minutes.");

    assert.deepEqual(errors, []);
    console.log("FLoRA findings UI checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
