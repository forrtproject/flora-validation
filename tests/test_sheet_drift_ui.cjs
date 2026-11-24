/* Run with: node tests/test_sheet_drift_ui.cjs
 * Source Records > Sheet edits: the chip's count, the view's focus and states
 * (loading, error and Try again, empty, never compared), escaped values, "Keep
 * FLoRA value" with its required note, Escape, Undo, and "Use sheet value", which
 * fills the FLoRA editor without saving until Save corrections. Also the editor's
 * own box, which fills one field and leaves other unsaved edits alone, the CSV's
 * URL and Back to grid. All requests are intercepted. No web server, database, or
 * remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const XSS = '<img src=x onerror=alert(1)> "quoted" & more';
const RID = "11111111-1111-4111-8111-111111111111";
const SHEET_ID = "3f2a9c1e-0000-4000-8000-000000000000";
const NOTE = "Taken from Replications row 245, edited in the sheet after import";
const LONG = "A long quote. ".repeat(30).trim();

async function until(test, ms = 3000) {
  const end = Date.now() + ms;
  while (!test()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

const target = (extra) => ({ flora_id: "REPL-000847", export_id: "FLORA-000847", role: "primary", version: 3,
  flora_value: "failed", flora_source: "import", flora_source_text: "as imported", state: "pending",
  suggested_action: "use_sheet_value", sheet_value_for_flora: "successful", sheet_value_error: null, ...extra });

function driftRecord(kept) {
  const at = "2026-11-22T03:02:00Z";
  return {
    record_id: RID, display_id: "REPL-000847", source: "replications", source_label: "Replications",
    type: "replication", sheet_row_id: SHEET_ID, sheet_row_number: 245, ref_o: "O", ref_r: "R",
    state: "conflict", not_in_flora: null, reentered_as: [],
    flora: [{ flora_id: "REPL-000847", export_id: "FLORA-000847", role: "primary", version: 3 }],
    fields: [
      { finding_id: 5, kind: "edited", sheet_column: "outcome", record_column: "outcome",
        flora_field: "outcome", flora_column: "outcome", value_at_import: "failed", value_in_sheet: "successful",
        sheet_row_number: 245, first_seen_at: at, last_seen_at: at, resolved_at: null,
        kept: kept ? { kept_at: "2026-11-22T10:00:00Z", kept_by: "luke", note: "the paper says <b>failed</b>",
                       sheet_value: "successful", current: true } : null,
        state: kept ? "kept" : "pending", counted: !kept,
        targets: [target(kept ? { state: "kept", suggested_action: "none" } : {})] },
      { finding_id: 6, kind: "edited", sheet_column: "doi_r", record_column: "doi_r",
        flora_field: "doi_r", flora_column: "doi_r", value_at_import: "10.1/abc", value_in_sheet: XSS,
        sheet_row_number: 245, first_seen_at: at, last_seen_at: at, resolved_at: null, kept: null,
        state: "conflict", counted: true,
        targets: [target({ flora_value: "10.1/xyz", flora_source: "correction", state: "conflict",
                           flora_source_text: "corrected by luke, 3 Nov 2026: “<b>checked</b>”",
                           suggested_action: "decide", sheet_value_for_flora: null,
                           sheet_value_error: "doi_r must be a DOI starting with 10. (leave it blank for none)" })] },
      { finding_id: 7, kind: "edited", sheet_column: "outcome_quote", record_column: "outcome_quote",
        flora_field: "outcome_quote", flora_column: "outcome_quote", value_at_import: LONG,
        value_in_sheet: "Short.", sheet_row_number: 245, first_seen_at: at, last_seen_at: at,
        resolved_at: null, kept: null, state: "matches", counted: false,
        targets: [target({ flora_value: "Short.", state: "matches", suggested_action: "none",
                           sheet_value_for_flora: "Short." })] },
    ],
  };
}

const CHECKS = [{ source: "replications", label: "Replications", checked_at: "2026-11-22T03:02:00Z",
                  rows_compared: 4100, counts: {}, notes: [], last_sync_status: "ok",
                  last_sync_at: "2026-11-22T03:01:00Z" },
                { source: "score_2025", label: "SCORE 2025 Validation", checked_at: "2026-11-19T03:02:00Z",
                  rows_compared: 300, counts: {}, notes: [], last_sync_status: "failed",
                  last_sync_at: "2026-11-22T03:01:00Z" }];

function floraRecord(withBox) {
  return {
    flora_id: "REPL-000847", export_id: "FLORA-000847", source_display_id: "REPL-000847",
    type: "replication", source: "replications", merged_sources: [], check_findings: [],
    check_runs: { structural: null, network: null },
    editing: { version: 3, history: [], source_edits: [], type_change: null, fields: [
      { field: "doi_r", label: "doi_r", group: "Replication", long: false, choices: null, value: "10.1/abc" },
      { field: "outcome", label: "outcome", group: "Outcome", long: false,
        choices: ["failed", "mixed", "successful"], value: "failed" },
    ] },
    sheet_drift: withBox ? {
      fields: [{ ...driftRecord(false).fields[0], display_id: "REPL-000847", source_label: "Replications",
                 record_id: RID }],
      rows: [{ record_id: RID, display_id: "REPL-000847", source: "replications", source_label: "Replications",
               sheet_row_id: SHEET_ID, sheet_row_number: 245, role: "primary" }],
      last_checks: [CHECKS[0]],
    } : { fields: [], rows: [], last_checks: [] },
  };
}

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const keeps = [];
  const undos = [];
  const patches = [];
  const lists = [];
  const csvs = [];
  let mode = "ok";            // ok | error | empty | never
  let kept = false;
  let box = false;
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
    const base = "/api/admin/source-records/sheet-drift";
    if (url.pathname === base + "/summary") {
      return route.fulfill({ json: { counts: { needs_review: 2 }, last_checks: CHECKS } });
    }
    if (url.pathname === base + "/export.csv") {
      csvs.push(url.search);
      return route.fulfill({ status: 200, contentType: "text/csv",
        headers: { "Content-Disposition": 'attachment; filename="sheet_edits_2026-11-22.csv"' },
        body: "﻿finding_id\n5\n" });
    }
    if (url.pathname === base + "/5/keep") {
      if (request.method() === "POST") {
        keeps.push(JSON.parse(request.postData()));
        kept = true;
      } else {
        undos.push(request.method());
        kept = false;
      }
      return route.fulfill({ json: { finding: { finding_id: 5 }, record: driftRecord(kept) } });
    }
    if (url.pathname === base) {
      lists.push(url.search);
      if (mode === "error") return route.fulfill({ status: 500, json: { detail: "database is resting" } });
      const counts = { needs_review: 1, nothing_to_do: 0, resolved: 2, all: 3 };
      if (mode === "empty" || mode === "never") {
        return route.fulfill({ json: { records: [], total: 0, fields_total: 0, page: 1, per_page: 25,
          counts: { needs_review: 0, nothing_to_do: 0, resolved: 0, all: 0 },
          last_checks: mode === "never" ? CHECKS.map(c => ({ ...c, checked_at: null })) : CHECKS,
          flora_error: null, set_up: true } });
      }
      return route.fulfill({ json: { records: [driftRecord(kept)], total: 1, fields_total: 3, page: 1,
        per_page: 25, counts, last_checks: CHECKS, flora_error: null, set_up: true } });
    }
    if (url.pathname === "/api/admin/flora/REPL-000847") {
      if (request.method() === "PATCH") {
        patches.push(JSON.parse(request.postData()));
        return route.fulfill({ json: { change_id: 9, changed_fields: ["outcome"], published: true, warnings: [] } });
      }
      return route.fulfill({ json: floraRecord(box) });
    }
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    return route.fulfill({ json: { records: [], sources: [], jobs: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });

  try {
    await page.goto("http://flora.test/");
    assert.equal(await page.locator("#sfc-drift").innerText(), "—");      // until loaded
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      switchAdminTab("sources");
    });

    // The chip's count arrives on its own.
    await page.waitForFunction(() => document.querySelector("#sfc-drift").textContent === "2");

    // Opening the view moves the focus to its heading.
    const chip = page.locator('#src-filters [data-filter="sheet-drift"]');
    await chip.click();
    await page.locator(".src-drift-card").waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.id), "src-drift-title");
    assert.equal(await page.locator("#src-drift-view").isVisible(), true);
    assert.equal(await page.locator("table:has(#src-table-body)").isVisible(), false);
    assert.match(lists[0], /state=needs_review/);
    assert.match(await page.locator("#src-drift-count").innerText(), /1 record · 3 fields/);
    assert.match(await page.locator("#src-drift-state option[value=needs_review]").innerText(), /Needs review \(1\)/);
    const checks = await page.locator("#src-drift-checks").innerText();
    assert.match(checks, /Replications/);
    assert.match(checks, /SCORE 2025 Validation: last sync failed, findings as of/);
    // Dates as the rest of the admin panel writes them (en-GB), whatever the browser's locale.
    assert.match(checks, /Replications \d{1,2} Nov, \d\d:\d\d/);
    assert.match(checks, /findings as of \d{1,2} Nov 2026/);

    // Values are text, never markup; the card says where the row is.
    const card = page.locator(".src-drift-card").first();
    assert.equal(await card.getAttribute("aria-labelledby"), "src-drift-h-" + RID);
    assert.equal(await page.locator("#src-drift-body img").count(), 0);
    assert.equal(await page.locator("#src-drift-body b").count(), 0);
    assert.ok((await card.innerText()).includes(XSS));
    assert.match(await card.innerText(), /row 245 at last check/);
    // The record it feeds by its permanent ID: its FLoRA-tab ID is this row's own.
    assert.match(await card.innerText(), /Feeds FLORA-000847/);
    assert.doesNotMatch(await card.innerText(), /Feeds REPL-000847/);
    // The field name (a row header) is padded like the cells beside it.
    const pads = await card.locator("tbody tr").first().evaluate((tr) =>
      [...tr.children].map((cell) => getComputedStyle(cell).paddingTop + " " + getComputedStyle(cell).verticalAlign));
    assert.equal(new Set(pads).size, 1, pads.join(" | "));
    assert.equal(await card.locator("caption").innerText(), "Sheet edits in REPL-000847");
    assert.equal(await card.locator("th[scope=col]").count(), 5);
    // FLoRA's correction is shown with who and why; a sheet value FLoRA would refuse
    // disables its button and says why.
    assert.match(await card.innerText(), /corrected by luke, 3 Nov 2026/);
    const refused = card.locator(".src-drift-use[disabled]");
    assert.equal(await refused.count(), 1);
    assert.match(await page.locator("#" + await refused.getAttribute("aria-describedby")).innerText(), /must be a DOI/);
    // Badges carry words.
    assert.deepEqual(await card.locator(".src-drift-badge").allInnerTexts(),
      ["Sheet changed, FLoRA not yet", "Changed in both places", "FLoRA already has the sheet's value"]);

    // A long value is cut, with a toggle that says whether it is open.
    const more = card.locator(".src-drift-more");
    assert.equal(await more.getAttribute("aria-expanded"), "false");
    await more.click();
    assert.equal(await more.getAttribute("aria-expanded"), "true");
    assert.equal(await card.locator(".src-drift-full").first().isVisible(), true);

    // Keep FLoRA value: the note is required, Escape cancels, Save posts it with the
    // sheet value the page showed, and the focus lands on Undo.
    const keep = page.getByRole("button", { name: "Keep FLoRA value for outcome in REPL-000847" });
    await keep.click();
    const note = page.locator("#src-drift-note-5");
    assert.equal(await page.evaluate(() => document.activeElement.id), "src-drift-note-5");
    // The button gives way to the form it opens.
    assert.equal(await page.locator('.src-drift-keep[data-finding="5"]').getAttribute("aria-expanded"), "true");
    await note.fill("ab");
    await page.locator('.src-drift-keep-save[data-finding="5"]').click();
    assert.match(await page.locator("#src-drift-msg-5").innerText(), /at least 3 characters/);
    assert.equal(keeps.length, 0);
    await note.press("Escape");
    assert.equal(await page.locator("#src-drift-form-5").isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")),
                 "Keep FLoRA value for outcome in REPL-000847");
    await keep.click();
    assert.equal(await note.inputValue(), "");
    await note.fill("  the paper says failed  ");
    await page.locator('.src-drift-keep-save[data-finding="5"]').click();
    await until(() => keeps.length === 1);
    assert.deepEqual(keeps[0], { note: "the paper says failed", sheet_value: "successful" });
    const undo = page.getByRole("button", { name: "Undo keep for outcome in REPL-000847" });
    await undo.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")),
                 "Undo keep for outcome in REPL-000847");
    assert.match(await page.locator(".src-drift-kept").innerText(), /Kept by luke, \d{1,2} Nov 2026: .*the paper says <b>failed<\/b>/);
    assert.equal(await page.locator(".src-drift-kept b").count(), 1);    // the handle, not the note's markup
    await page.waitForFunction(() => /Kept FLoRA's value for outcome/.test(document.querySelector("#src-drift-live").textContent));

    // Undo withdraws it, and the focus goes back to Keep.
    await undo.click();
    await until(() => undos.length === 1);
    assert.deepEqual(undos, ["DELETE"]);
    await keep.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")),
                 "Keep FLoRA value for outcome in REPL-000847");

    // Use sheet value: the editor opens with the value and the note filled in, and
    // nothing is saved until Save corrections.
    const listsBefore = lists.length;
    await page.getByRole("button", { name: "Use sheet value for outcome in REPL-000847" }).click();
    await page.locator("#flora-detail-modal").waitFor({ state: "visible" });
    const outcome = page.locator('#flora-detail-body [data-field="outcome"]');
    await page.waitForFunction(() => document.querySelector('#flora-detail-body [data-field="outcome"]')?.value === "successful");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.field), "outcome");
    const outcomeNote = page.locator('[data-note-for="outcome"]');
    assert.equal(await outcomeNote.isVisible(), true);
    assert.equal(await outcomeNote.inputValue(), NOTE);
    assert.match(await page.locator("#flora-save-msg").innerText(), /Filled in from the sheet: outcome/);
    await page.waitForTimeout(150);
    assert.equal(patches.length, 0);
    await page.locator("#flora-save-btn").click();
    await until(() => patches.length === 1);
    assert.deepEqual(patches[0], { fields: { outcome: "successful" }, revert: [], version: 3,
                                   notes: { outcome: NOTE } });

    // Closing the editor reloads the list, and the focus returns to the button.
    await page.locator("#flora-detail-close").click();
    await until(() => lists.length > listsBefore);
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") ===
                                     "Use sheet value for outcome in REPL-000847");

    // The editor's own box fills one field in place and keeps other unsaved edits.
    box = true;
    await page.getByRole("button", { name: "Open FLORA-000847 in the FLoRA editor" }).click();
    await page.locator("#flora-sheet-drift").waitFor();
    assert.match(await page.locator("#flora-sheet-drift").innerText(), /Sheet edited after import/i);   // h4s are set in capitals
    assert.match(await page.locator("#flora-sheet-drift").innerText(), /REPL-000847, Replications row 245/);
    assert.match(await page.locator("#flora-sheet-drift").innerText(), /compared \d{1,2} Nov, \d\d:\d\d/);
    const doi = page.locator('#flora-detail-body [data-field="doi_r"]');
    await doi.fill("10.1/typed");
    await page.locator(".flora-sheet-drift-use").click();
    assert.equal(await outcome.inputValue(), "successful");
    assert.equal(await doi.inputValue(), "10.1/typed");
    assert.match(await page.locator("#flora-sheet-drift").innerText(), /Filled in below/);
    assert.equal(await page.locator('[data-note-for="outcome"]').inputValue(), NOTE);
    assert.equal(patches.length, 1);
    // The editor's identity table does not list the box's data.
    assert.equal(await page.locator(".flora-detail-table th", { hasText: "sheet_drift" }).count(), 0);
    await page.locator("#flora-detail-close").click();
    box = false;

    // Download CSV takes the filter with it.
    await page.locator("#src-drift-state").selectOption("all");
    await until(() => lists[lists.length - 1].includes("state=all"));
    await page.locator("#src-drift-search").fill("REPL-000847");
    await page.locator("#src-drift-search").press("Enter");
    await until(() => lists[lists.length - 1].includes("search=REPL-000847"));
    await page.locator("#src-drift-csv").click();
    await until(() => csvs.length === 1);
    assert.match(csvs[0], /state=all/);
    assert.match(csvs[0], /search=REPL-000847/);
    assert.doesNotMatch(csvs[0], /page=/);

    // Empty, never compared, and an error with Try again.
    mode = "empty";
    await page.locator("#src-drift-state").selectOption("needs_review");
    await page.locator("#src-drift-search").fill("");
    await page.locator("#src-drift-search").press("Enter");
    await page.locator(".admin-empty", { hasText: "No sheet rows need review" }).waitFor();
    mode = "never";
    await page.evaluate(() => fetchSourceDrift());
    await page.locator(".admin-empty", { hasText: "Not compared yet" }).waitFor();
    mode = "error";
    await page.evaluate(() => fetchSourceDrift());
    await page.locator(".src-drift-error", { hasText: "database is resting" }).waitFor();
    assert.equal(await page.locator(".src-drift-error").getAttribute("role"), "alert");
    mode = "ok";
    await page.getByRole("button", { name: "Try again" }).click();
    await page.locator(".src-drift-card").waitFor();

    // Back to grid: the grid returns, "All" is lit, and the focus is on the chip.
    await page.locator("#src-drift-back").click();
    assert.equal(await page.locator("#src-drift-view").isVisible(), false);
    assert.equal(await page.locator("table:has(#src-table-body)").isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.filter), "sheet-drift");
    assert.equal(await page.locator("#src-filters .admin-filter-btn.active").getAttribute("data-filter"), "all");

    assert.deepEqual(errors, []);
    console.log("OK test_sheet_drift_ui");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
