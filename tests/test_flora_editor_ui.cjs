/* Run with: node tests/test_flora_editor_ui.cjs
 * FLoRA records are changed in the FLoRA tab, every save is a row of the record's
 * change log (closed by default) and any row can be restored; Source Records is
 * read-only and points at the FLoRA record a row feeds. All requests are
 * intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const SOURCE_ID = "6f1c2d4e-0000-4000-8000-000000000001";

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const patches = [];
  const restores = [];
  const confirmations = [];
  let patchStatus = 200;
  let patchDelay = 0;
  const editing = {
    version: 3,
    fields: [
      { field: "title_o", label: "title_o", group: "Original", long: true, choices: null,
        value: "A corrected title",
        edit: { derived: "The derived title", edited_by: "ana", edited_at: "2026-10-01T10:00:00Z",
                note: "typo", change_id: 3 } },
      { field: "doi_o", label: "doi_o", group: "Original", long: false, choices: null, value: "10.1/a" },
      { field: "outcome", label: "outcome", group: "Outcome", long: false,
        choices: ["failed", "mixed", "successful"], value: "successful",
        edit: { derived: "descriptive only", edited_by: "ana", edited_at: "2026-10-01T10:00:00Z",
                note: null, change_id: 3 } },
      // Stored line breaks a form control rewrites: an untouched field must not read
      // as changed.
      { field: "outcome_quote_source", label: "outcome_quote_source", group: "Outcome", long: false,
        choices: null, value: "page 3\nparagraph 2" },
      { field: "bibtex_ref_o", label: "bibtex_ref_o", group: "Original", long: true, choices: null,
        value: "@article{x,\r\n  title={y}\r}" },
    ],
    history: [
      { change_id: 3, changed_at: "2026-10-01T10:00:00Z", admin_handle: "ana", action: "edit", note: null,
        notes: { title_o: "typo" },
        changes: { title_o: ["First title", "A corrected title"] }, corrections: { title_o: "A corrected title" },
        restored_from: null },
      { change_id: 2, changed_at: "2026-09-30T10:00:00Z", admin_handle: "luke", action: "restore",
        note: "undo the outcome", notes: {},
        changes: { outcome: ["failed", "successful"] }, corrections: { title_o: "First title" }, restored_from: 1 },
      { change_id: 1, changed_at: "2026-09-29T10:00:00Z", admin_handle: "luke", action: "edit", note: null,
        notes: { title_o: "from the paper", outcome: "per table 2" },
        changes: { title_o: ["The derived title", "First title"], outcome: ["successful", "failed"] },
        corrections: { title_o: "First title", outcome: "failed" }, restored_from: null },
    ],
    source_edits: [{ display_id: "REPL-000001", field: "outcome", old_value: "mixed", new_value: "successful",
                     edited_by: "kim", edited_at: "2026-09-01T10:00:00Z", note: "sheet typo" }],
  };
  const record = () => ({
    flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
    source_record_id: SOURCE_ID, type: "replication", source: "replications",
    title_o: "A corrected title", doi_o: "10.1/a", outcome: "successful", oa_work_id_o: "W2168190474",
    edited_fields: "title_o", merged_sources: [], editing,
    outcome_conflict: "successful | uninformative",
  });
  page.on("pageerror", error => errors.push(error.message));
  const gridQueries = [];
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001/confirm-outcome") {
      confirmations.push(JSON.parse(request.postData()));
      return route.fulfill({ json: { export_id: "FLORA-000001", change_id: 5, changed_fields: ["outcome"],
                                     reverted_fields: [], version: 5, published: true } });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001/restore") {
      restores.push(JSON.parse(request.postData()));
      return route.fulfill({ json: { export_id: "FLORA-000001", change_id: 4, changed_fields: ["title_o"],
                                     reverted_fields: [], version: 4, published: true } });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001") {
      if (request.method() === "PATCH") {
        patches.push(JSON.parse(request.postData()));
        if (patchDelay) await new Promise(resolve => setTimeout(resolve, patchDelay));
        if (patchStatus === 409) return route.fulfill({ status: 409, json: { detail: "This record was changed by someone else." } });
        return route.fulfill({ json: { export_id: "FLORA-000001", change_id: 4, changed_fields: ["outcome"],
                                       reverted_fields: ["title_o"], version: 4, published: true,
                                       warnings: ["REPL-000009 has the same original and replication identifiers."] } });
      }
      return route.fulfill({ json: record() });
    }
    if (url.pathname === "/api/admin/flora") {
      gridQueries.push(url.search);
      return route.fulfill({ json: {
        records: [{ flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
                    type: "replication", source: "replications", doi_o: "10.1/a", ref_o: "Ref O",
                    doi_r: "10.1/b", ref_r: "Ref R", outcome: "successful", edited_fields: "title_o outcome",
                    outcome_conflict: "failed | uninformative" }],
        total: 1, page: 1, per_page: 50,
        counts: { all_records: 1, replications: 1, reproductions: 0, edited: 1, conflicts: 1,
                  sources: [], outcomes: [] } } });
    }
    if (url.pathname === "/api/admin/source-records/" + SOURCE_ID) {
      return route.fulfill({ json: {
        record_id: SOURCE_ID, display_id: "REPL-000001", type: "replication", source: "replications",
        sheet_row_id: "sheet-1", ref_o: "Ref O", doi_o: "10.1/a", outcome: "successful",
        outcome_quote: "It replicated.", duplicates: [],
        history: [{ field: "outcome", old_value: "mixed", new_value: "successful", edited_by: "kim",
                    edited_at: "2026-09-01T10:00:00Z", note: "sheet typo" }],
        flora: [{ flora_id: "REPL-000001", export_id: "FLORA-000001", role: "primary", retired_at: null }],
        neighbours: { prev_id: null, next_id: null, pos: 1, total: 1 } } });
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

    // The grid marks corrected records and counts them under their own chip.
    await page.locator("#flora-table-body .flora-edited-badge").waitFor();
    assert.match(await page.locator("#flora-table-body .flora-edited-badge").getAttribute("title"), /title_o, outcome/);
    assert.equal(await page.locator("#ffc-edited").innerText(), "1");

    // An outcome its duplicate source entries disagree on is marked, counted and
    // filterable, so a person can settle it with a correction.
    const conflict = page.locator("#flora-table-body .src-dup-badge-warn", { hasText: "conflict" });
    assert.match(await conflict.getAttribute("title"), /failed \| uninformative/);
    assert.equal(await page.locator("#ffc-conflict").innerText(), "1");
    await page.locator('#admin-tab-flora .admin-filter-btn[data-filter="conflict"]').click();
    await page.waitForFunction(() => document.querySelector('#admin-tab-flora .admin-filter-btn[data-filter="conflict"]').classList.contains("active"));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert(gridQueries.some(query => query.includes("conflict=true")), "the filter asks the server for conflicts");
    await page.locator('#admin-tab-flora .admin-filter-btn[data-filter="all"]').click();
    await page.locator("#flora-table-body .flora-view-btn").waitFor();

    // The editor: editable fields as controls, identity as read-only context.
    await page.locator(".flora-view-btn").click();
    await page.locator("#flora-detail-modal:not(.hidden) #flora-save-btn").waitFor();
    assert.equal(await page.locator("#flora-detail-title").innerText(), "REPL-000001");
    assert.equal(await page.locator('#flora-detail-body [data-field="title_o"]').inputValue(), "A corrected title");
    assert.equal(await page.locator('#flora-detail-body [data-field="flora_id"]').count(), 0);
    assert.equal(await page.locator('#flora-detail-body [data-field="type"]').count(), 0);
    assert.match(await page.locator(".flora-field-edited .flora-derived").first().innerText(), /The derived title/);
    assert.match(await page.locator(".flora-field-edited .flora-edit-mark").first().innerText(), /change #3/);
    assert.match(await page.locator(".flora-detail-table").innerText(), /W2168190474/);

    // The change log: a table, closed until opened, one row per save.
    const log = page.locator("#flora-change-log");
    assert.equal(await log.evaluate(el => el.open), false);
    assert.equal(await log.locator("summary").innerText(), "Changes (3)");
    assert.equal(await log.locator("table").isVisible(), false);
    await log.locator("summary").click();
    const rows = log.locator("tbody tr");
    assert.equal(await rows.count(), 4);                       // three changes and the original
    assert.match(await rows.nth(0).innerText(), /#3[\s\S]*ana[\s\S]*First title → A corrected title[\s\S]*typo[\s\S]*Current/);
    assert.match(await rows.nth(1).innerText(), /Restored to #1[\s\S]*undo the outcome[\s\S]*\(derived value\)/);
    // Each field's note sits with that field's change.
    const fieldOfFirst = name => rows.nth(2).locator(".flora-change-field", { hasText: name });
    assert.match(await fieldOfFirst("outcome").innerText(), /successful → failed\s*per table 2/);
    assert.match(await fieldOfFirst("title_o").innerText(), /First title\s*from the paper/);
    assert.match(await rows.nth(3).innerText(), /Original/);
    assert.equal(await rows.nth(0).locator(".flora-restore-btn").count(), 0);
    // Earlier edits to the source rows: their own table, also closed.
    const earlier = page.locator("details.flora-changes", { hasText: "Earlier edits to its source records (1)" });
    assert.equal(await earlier.evaluate(el => el.open), false);
    assert.match(await earlier.innerText(), /Earlier edits to its source records \(1\)/);

    // Restoring a row asks first, then names the change and the loaded version.
    await rows.nth(2).locator(".flora-restore-btn").click();
    await page.locator("#dialog-buttons button", { hasText: "Cancel" }).click();
    assert.equal(restores.length, 0);
    // Closing the confirmation leaves the page behind the open editor locked.
    assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden");
    await page.locator("#flora-restore-note").fill("back to the paper's title");
    await rows.nth(2).locator(".flora-restore-btn").click();
    await page.locator("#dialog-buttons button", { hasText: "Confirm" }).click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Restored"));
    assert.deepEqual(restores[0], { change_id: 1, version: 3, note: "back to the paper's title" });
    assert.match(await page.locator("#toast").innerText(), /FLoRA table is updated/);
    await page.locator("#flora-change-log summary").click();
    await page.locator("#flora-change-log .flora-change-original .flora-restore-btn").click();
    await page.locator("#dialog-buttons button", { hasText: "Confirm" }).click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("original values are back"));
    await page.locator("#flora-detail-body #flora-save-btn").waitFor();

    // Nothing touched: nothing sent — line breaks the controls rewrote included. A
    // value with a line break gets a textarea, so the break is not lost either.
    assert.equal(await page.locator('[data-field="outcome_quote_source"]').evaluate(el => el.tagName), "TEXTAREA");
    await page.locator("#flora-save-btn").click();
    assert.equal(await page.locator("#flora-save-msg").innerText(), "Nothing changed.");
    assert.equal(patches.length, 0);

    // Reverting a dropdown to a derived value off its list shows that value.
    await page.locator('[data-revert-field="outcome"]').click();
    assert.equal(await page.locator('#flora-detail-body [data-field="outcome"]').inputValue(), "descriptive only");

    // A field's note box opens once the field holds a change, and closes again
    // when the change is undone.
    const doiNote = page.locator('[data-note-for="doi_o"]');
    assert.equal(await doiNote.isVisible(), false);
    await page.locator('#flora-detail-body [data-field="doi_o"]').fill("10.1/b");
    assert.equal(await doiNote.isVisible(), true);
    await page.locator('#flora-detail-body [data-field="doi_o"]').fill("10.1/a");
    assert.equal(await doiNote.isVisible(), false);

    // Only the changed field and the revert are sent, each with its own note.
    await page.locator('#flora-detail-body [data-field="outcome"]').selectOption("failed");
    await page.locator('[data-note-for="outcome"]').fill("checked the paper");
    await page.locator('[data-revert-field="title_o"]').click();
    assert.equal(await page.locator('#flora-detail-body [data-field="title_o"]').inputValue(), "The derived title");
    await page.locator('[data-note-for="title_o"]').fill("the derived title was right");
    await page.locator("#flora-save-btn").click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Saved"));
    assert.deepEqual(patches[0], { fields: { outcome: "failed" }, revert: ["title_o"], version: 3,
                                   notes: { outcome: "checked the paper", title_o: "the derived title was right" } });
    // The outcome stays in the editor after the reload, warnings included, and the
    // toast is drawn above the editor rather than under it.
    await page.waitForFunction(() => document.querySelector("#flora-save-msg .flora-notice"));
    assert.match(await page.locator("#flora-save-msg").innerText(), /Saved — 1 field corrected, 1 reverted\. The FLoRA table is updated\./);
    assert.match(await page.locator("#flora-save-msg .flora-warning").innerText(), /REPL-000009 has the same/);
    const layers = await page.evaluate(() => [getComputedStyle(document.querySelector("#toast")).zIndex,
                                               getComputedStyle(document.querySelector("#flora-detail-modal")).zIndex]);
    assert(Number(layers[0]) > Number(layers[1]), `toast ${layers[0]} under the editor ${layers[1]}`);

    // Typing after "revert" cancels the revert and sends the typed value instead.
    await page.locator('#flora-detail-body [data-field="title_o"]').waitFor();
    await page.locator('[data-revert-field="title_o"]').click();
    await page.locator('#flora-detail-body [data-field="title_o"]').fill("Typed instead");
    assert.equal(await page.locator('[data-revert-field="title_o"]').isEnabled(), true);
    patchStatus = 409;
    await page.locator("#flora-save-btn").click();
    await page.waitForFunction(() => /someone else/.test(document.querySelector("#flora-save-msg")?.textContent || ""));
    assert.deepEqual(patches[1].fields, { title_o: "Typed instead" });
    assert.deepEqual(patches[1].revert, []);
    // Closed before the 409's reload fires: it must not reopen the editor.
    await page.evaluate(() => closeFloraRecord());
    await page.waitForTimeout(1500);
    assert.equal(await page.locator("#flora-detail-modal").isHidden(), true);
    // Closed while a save is in flight: its reload must not reopen it either.
    patchStatus = 200;
    patchDelay = 600;
    await page.locator(".flora-view-btn").click();
    await page.locator("#flora-detail-modal:not(.hidden) #flora-save-btn").waitFor();
    await page.locator('#flora-detail-body [data-field="doi_o"]').fill("10.1/changed");
    await page.locator("#flora-save-btn").click();
    await page.evaluate(() => closeFloraRecord());
    await page.waitForTimeout(1200);
    assert.equal(patches.length, 3);
    assert.equal(await page.locator("#flora-detail-modal").isHidden(), true);
    assert.equal(await page.evaluate(() => document.body.style.overflow), "");
    patchDelay = 0;
    assert.equal(restores.length, 2);
    assert.deepEqual(restores[1], { change_id: 0, version: 3, note: "" });
    await page.evaluate(() => closeFloraRecord());

    // Source Records: read-only, earlier edits in a closed table, and it sends the
    // reviewer to the FLoRA record.
    await page.evaluate(id => { switchAdminTab("sources"); openSourceRecord(id); }, SOURCE_ID);
    await page.locator("#src-detail-body .src-flora-open").waitFor();
    assert.equal(await page.locator("#src-detail-body input, #src-detail-body textarea, #src-detail-body select").count(), 0);
    assert.equal(await page.locator("#src-detail-body .btn-primary").count(), 0);
    assert.match(await page.locator(".src-flora-link").innerText(), /Feeds FLoRA record REPL-000001/);
    const sourceEdits = page.locator("#src-detail-body details.flora-changes");
    assert.equal(await sourceEdits.evaluate(el => el.open), false);
    assert.match(await sourceEdits.innerText(), /Earlier edits \(1\)/);
    assert.equal(await page.locator('#src-filters [data-filter="unreviewed"]').count(), 0);
    await page.locator(".src-flora-open").click();
    await page.locator("#flora-detail-modal:not(.hidden) #flora-save-btn").waitFor();
    assert.equal(await page.locator("#src-detail-modal").isHidden(), true);
    assert.equal(await page.locator("#admin-tab-flora").isVisible(), true);

    // An outcome its duplicates disagree on: confirming the one shown settles it,
    // with an optional note, and nothing has to be typed into the outcome field.
    const conflictBox = page.locator("#flora-detail-body .flora-conflict");
    assert.match(await conflictBox.innerText(), /disagree on the outcome: successful \| uninformative/);
    assert.equal(await page.locator('#flora-detail-body [data-field="outcome_conflict"]').count(), 0);
    await page.locator("#flora-confirm-note").fill("Checked the report");
    await page.locator("#flora-confirm-btn").click();
    await page.waitForFunction(() => document.querySelector("#flora-save-msg")
      && /Outcome confirmed/.test(document.querySelector("#flora-save-bar, .src-save-bar").innerText));
    assert.deepEqual(confirmations, [{ version: 3, note: "Checked the report" }]);
    // In the change log, a confirmation reads as one, with its note.
    const logHtml = await page.evaluate(() => floraChangeLog({ fields: [], history: [{
      change_id: 5, action: "confirm", note: "Checked the report", admin_handle: "luke",
      changed_at: "2026-10-20T10:00:00Z", changes: { outcome: ["failed", "failed"] },
      corrections: { outcome: "failed" }, notes: {} }] }));
    assert.match(logHtml, /Confirmed the outcome<div class="flora-change-note">Checked the report/);

    assert.deepEqual(errors, []);
    console.log("FLoRA editor UI checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
