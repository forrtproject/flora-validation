/* Run with: node tests/test_manual_records_ui.cjs
 * Records added by hand in the FLoRA tab: the form (type switch, derived outcome,
 * field errors), the duplicate check as a warning that never blocks a save (except
 * an exclusion), the notice saying whether the record is its own or absorbed, Run
 * pipeline now, the "Added by hand" list, the grid's badge and Open entry, the
 * record editor's link to the entry, a frozen entry, withdraw and restore without a
 * browser dialog, and the Source Records chip and panel.
 * All requests are intercepted; no server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const XSS = '<img src=x onerror="window.__xss=1">';
const SOURCE_ID = "7a1c2d4e-0000-4000-8000-000000000009";

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10_000);
  const errors = [];
  const requests = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { errors.push("dialog: " + dialog.message()); dialog.dismiss(); });

  const options = {
    outcomes: ["failed", "mixed", "successful"],
    outcome_computation: ["cannot_be_determined", "computational issues", "computationally reproducible",
                          "not checked", "technical failure"],
    outcome_robustness: ["cannot_be_determined", "not checked", "robust", "robustness challenges"],
    limits: {},
  };
  const state = (code, extra) => Object.assign({ code, editable: code === "awaiting_id" || code === "absorbed",
    flora_id: null, retired: false, into: null, into_flora_id: null, mode: null, message: "" }, extra);
  const entry = (over) => Object.assign({
    manual_id: "11111111-1111-4111-8111-111111111111", display_id: "MAN-000001", type: "replication",
    doi_o: "10.7/orig", url_o: null, ref_o: "Ref " + XSS, study_o: null, alt_identifier_o: null,
    doi_r: "10.7/rep", url_r: null, ref_r: null, year_r: null, abstract_r: null, alt_identifier_r: null,
    outcome: "failed", outcome_quote: "It failed.", out_quote_source: "abstract",
    outcome_computation: null, outcome_computational_quote: null, out_quote_computational_source: null,
    outcome_robustness: null, outcome_robustness_quote: null, out_quote_robust_source: null,
    note: "Why " + XSS, keep_distinct: false, status: "active", version: 1, created_by: "sophie",
    created_at: "2026-11-01T10:00:00Z", updated_by: "sophie", updated_at: "2026-11-01T10:00:00Z",
    events: [{ event_id: 1, occurred_at: "2026-11-01T10:00:00Z", admin_handle: "sophie", action: "create",
               changes: {}, note: null }],
    state: state("awaiting_id", { message: "A record of its own: it is in the FLoRA tab now and gets its FLoRA ID at the next pipeline run (nightly, 03:00 UTC).",
                                  withdraw_effect: "It leaves the FLoRA tab at once. " + XSS }),
  }, over);
  const frozen = entry({ manual_id: "22222222-2222-4222-8222-222222222222", display_id: "MAN-000002",
    state: state("in_flora", { flora_id: "MAN-000002",
      message: "In FLoRA as MAN-000002. The entry is frozen because MAN-000002 has its FLoRA ID: fix it in the FLoRA editor for record MAN-000002." }) });
  // Merged into a record that has its FLoRA ID: frozen too.
  const frozenAbsorbed = entry({ manual_id: "44444444-4444-4444-8444-444444444444", display_id: "MAN-000004",
    state: state("absorbed", { editable: false, flora_id: "REPL-000397", into: "REPL-000397",
      into_flora_id: "REPL-000397", mode: "same_identifiers",
      message: "Merged into REPL-000397: that record keeps its FLoRA ID and values, and this entry only fills its blanks. The entry is frozen because REPL-000397 has its FLoRA ID: fix it in the FLoRA editor for record REPL-000397." }) });
  const absorbedCheck = {
    ready: true, placement: { kind: "absorbed", into: "r1", into_display_id: "REPL-000397", mode: "same_identifiers",
                              into_flora_id: "REPL-000397" },
    message: "REPL-000397 has the same identifiers, so the build merges this entry into that record: REPL-000397 keeps its FLoRA ID and values, and this entry only fills what it lacks. No new FLoRA ID will appear. " + XSS,
    matches: { same_identifiers: [{ record_id: "r1", display_id: "REPL-000397", source: "replications" + XSS,
                                    type: "replication", flora_id: "REPL-000397", duplicate_status: null }],
               same_dois_one_link: [], same_replication: [], same_pair_other_type: [] },
    hidden: [{ display_id: "VAL-000004", source: "validated", type: "replication", deleted: true, duplicate_status: null }],
    in_validation: [{ record_id: "33333333-3333-4333-8333-333333333333", validation_status: "need_review",
                      same_original: true, validated: false }],
    excluded: null,
  };
  let createResponse = null;
  let patchStatus = 200;
  let dispatchStatus = 202;
  let current = entry();

  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg",
                    "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    const method = request.method();
    const body = request.postData() ? JSON.parse(request.postData()) : null;
    if (url.pathname.startsWith("/api/admin/")) requests.push({ method, path: url.pathname, search: url.search, body });
    const p = url.pathname;
    if (p === "/api/admin/flora/manual/options") return route.fulfill({ json: options });
    if (p === "/api/admin/flora/manual/counts") return route.fulfill({ json: { active: 2, withdrawn: 1 } });
    if (p === "/api/admin/flora/manual/check") {
      const doi = (body.fields || {}).doi_r || "";
      if (!doi && !(body.fields || {}).url_r) return route.fulfill({ json: { ready: false } });
      if (doi.includes("excluded")) {
        return route.fulfill({ json: { ...absorbedCheck, placement: { kind: "excluded", reason: "not a replication" },
                                       excluded: "not a replication", message: "" } });
      }
      if (body.keep_distinct) {
        return route.fulfill({ json: { ...absorbedCheck, placement: { kind: "own", absorbs: [] },
                                       message: "A record of its own, ruled a different study from REPL-000397." } });
      }
      return route.fulfill({ json: absorbedCheck });
    }
    if (p === "/api/admin/flora/manual" && method === "POST") {
      const [status, json] = createResponse || [201, { ...current, notice: "Saved as MAN-000001. A record of its own.", run_pipeline: true, repeat: false }];
      return route.fulfill({ status, json });
    }
    if (p === "/api/admin/flora/manual" && method === "GET") {
      const all = url.searchParams.get("status") === "all";
      const list = [entry({ state: state("absorbed", { into: "REPL-000397", into_flora_id: "REPL-000397",
                                                        message: "Merged " + XSS }) }), frozen, frozenAbsorbed];
      if (all) list.push(entry({ display_id: "MAN-000003", status: "withdrawn", state: state("withdrawn", { message: "Withdrawn by luke: wrong" }) }));
      return route.fulfill({ json: { entries: list, counts: { active: 2, withdrawn: 1 } } });
    }
    if (p === "/api/admin/flora/manual/MAN-000002") return route.fulfill({ json: frozen });
    if (p === "/api/admin/flora/manual/MAN-000004") return route.fulfill({ json: frozenAbsorbed });
    if (p.startsWith("/api/admin/flora/manual/MAN-000001") || p.startsWith("/api/admin/flora/manual/11111111")) {
      if (method === "PATCH") {
        if (patchStatus === 409) {
          patchStatus = 200;
          return route.fulfill({ status: 409, headers: { "X-Current-Version": "2" },
                                 json: { detail: { code: "version_conflict", message: "Changed", current_version: 2 } } });
        }
        current = entry({ version: current.version + 1, year_r: body.fields.year_r });
        return route.fulfill({ json: { ...current, notice: "Saved as MAN-000001. A record of its own.", run_pipeline: true } });
      }
      if (p.endsWith("/withdraw")) {
        current = entry({ status: "withdrawn", version: current.version + 1,
                          state: state("withdrawn", { message: "Withdrawn by sophie: " + body.reason }) });
        return route.fulfill({ json: { ...current, notice: "Withdrew MAN-000001. Withdrawn.", run_pipeline: false } });
      }
      if (p.endsWith("/restore")) {
        current = entry({ version: current.version + 1 });
        return route.fulfill({ json: { ...current, notice: "Restored MAN-000001. A record of its own.", run_pipeline: true } });
      }
      return route.fulfill({ json: current });
    }
    if (p === "/api/admin/source-sync/dispatch") {
      if (dispatchStatus === 409) return route.fulfill({ status: 409, json: { detail: { message: "A pipeline run is already active" } } });
      dispatchStatus = 409;
      return route.fulfill({ status: 202, json: { status: "queued", job_id: "j1" } });
    }
    if (p === "/api/admin/flora/MAN-000002") {
      return route.fulfill({ json: { flora_id: "MAN-000002", export_id: "MAN-000002", source_display_id: "MAN-000002",
        source_record_id: SOURCE_ID, source: "manual", type: "replication", doi_o: "10.7/orig",
        merged_sources: [{ display_id: "MAN-000005", source: "manual", doi_o: "10.7/orig", doi_r: "10.7/rep", outcome: "failed" }],
        editing: { version: 0, fields: [{ field: "doi_o", label: "doi_o", group: "Original", long: false, choices: null, value: "10.7/orig" }],
                   history: [], source_edits: [] }, check_findings: [], check_runs: {} } });
    }
    if (p === "/api/admin/flora") {
      return route.fulfill({ json: {
        records: [
          { flora_id: null, source_display_id: "MAN-000001", type: "replication", source: "manual", doi_o: "10.7/orig",
            doi_r: "10.7/rep", outcome: "failed" },
          { flora_id: null, source_display_id: "REPL-000009", type: "replication", source: "replications",
            doi_o: "10.7/x", doi_r: "10.7/y", outcome: "failed" },
          { flora_id: "MAN-000002", export_id: "MAN-000002", source_display_id: "MAN-000002", type: "replication",
            source: "manual", doi_o: "10.7/a", doi_r: "10.7/b", outcome: "successful" },
        ],
        total: 3, page: 1, per_page: 50,
        counts: { all_records: 3, replications: 3, reproductions: 0, unregistered: 2, sources: [], outcomes: [] } } });
    }
    if (p === "/api/admin/source-records/" + SOURCE_ID) {
      return route.fulfill({ json: { record_id: SOURCE_ID, display_id: "MAN-000001", type: "replication", source: "manual",
        sheet_row_id: "11111111-1111-4111-8111-111111111111", doi_o: "10.7/orig", duplicates: [], history: [], flora: [],
        raw: { created_by: "sophie" + XSS, created_at: "2026-11-01 10:00:00.1+00:00", note: "Because " + XSS },
        neighbours: { prev_id: null, next_id: null, pos: 1, total: 1 } } });
    }
    if (p === "/api/admin/source-records") {
      return route.fulfill({ json: { records: [{ record_id: SOURCE_ID, display_id: "MAN-000001", source: "manual",
        type: "replication", ref_o: "R", ref_r: "R", outcome: "failed" }], total: 1, page: 1, per_page: 50,
        counts: { all_records: 1, manual: 1 } } });
    }
    if (p === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    return route.fulfill({ json: { records: [], sources: [], jobs: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });

  const posts = (suffix) => requests.filter(r => r.method === "POST" && r.path === "/api/admin/flora/manual" + suffix);

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      switchAdminTab("flora");
    });

    // The grid: a badge on rows added by hand, the chip's count from the entries, and
    // an entry to open until a record has its ID (another source's row stays disabled).
    await page.locator("#flora-table-body .flora-manual-badge").first().waitFor();
    assert.equal(await page.locator("#flora-table-body .flora-manual-badge").count(), 2);
    await page.waitForFunction(() => document.querySelector("#ffc-manual").textContent === "2");
    assert.equal(await page.locator("#flora-table-body .flora-manual-btn").count(), 1);
    assert.equal(await page.locator('#flora-table-body .flora-view-btn[data-flora-id=""]').isDisabled(), true);

    // ── a new record ──
    await page.locator("#flora-add-btn").click();
    await page.locator("#flora-manual-modal:not(.hidden) #flora-manual-save").waitFor();
    assert.equal(await page.locator("#flora-manual-title").textContent(), "Add a record by hand");
    assert.equal(posts("/check").length, 0, "nothing to check before an identifier is typed");
    assert.equal(await page.locator('[data-outcome-for="replication"]').isVisible(), true);
    assert.equal(await page.locator('[data-outcome-for="reproduction"]').first().isVisible(), false);
    await page.locator('input[name="flora-manual-type"][value="reproduction"]').check();
    assert.equal(await page.locator('[data-outcome-for="replication"]').isVisible(), false);
    assert.equal(await page.locator("#flora-manual-side").textContent(), "Reproduction");
    await page.locator("#fm-outcome_computation").selectOption("computationally reproducible");
    await page.locator("#fm-outcome_robustness").selectOption("robust");
    assert.equal(await page.locator("#flora-manual-derived").innerText(), "computationally reproducible, robust");
    await page.locator("#fm-outcome_robustness").selectOption("cannot_be_determined");
    assert.equal(await page.locator("#flora-manual-derived").innerText(), "cannot_be_determined");
    await page.locator('input[name="flora-manual-type"][value="replication"]').check();

    // A field the server refuses is marked.
    createResponse = [422, { detail: { code: "original_required", field: "doi_o", message: "The original needs a DOI." } }];
    await page.locator("#flora-manual-save").click();
    await page.locator('[data-error-for="doi_o"]:not([hidden])').waitFor();
    assert.match(await page.locator('[data-error-for="doi_o"]').innerText(), /needs a DOI/);
    assert.equal(await page.locator("#fm-doi_o").evaluate(el => el.classList.contains("flora-manual-invalid")), true);
    assert.equal(posts("").at(-1).body.fields.outcome_computation, null, "the other type's fields are sent blank");

    // Typing a DOI runs one debounced check; a match is a warning, never a block.
    const before = posts("/check").length;
    await page.locator("#fm-doi_r").pressSequentially("10.7/rep", { delay: 20 });
    await page.locator("#flora-manual-dups .src-dup-warn").waitFor();
    await new Promise(resolve => setTimeout(resolve, 600));
    assert.equal(posts("/check").length - before, 1, "one check per pause in typing");
    const dups = await page.locator("#flora-manual-dups").innerText();
    assert.match(dups, /REPL-000397 keeps its FLoRA ID and values/);
    assert.match(dups, /<img src=x onerror="window.__xss=1">/, "shown as text");
    assert.match(dups, /VAL-000004.*deleted/);
    assert.match(dups, /being validated/);
    assert.equal(await page.locator("#flora-manual-save").isDisabled(), false, "warned, not refused");
    assert.equal(await page.locator("#flora-manual-keep-row").isVisible(), true);
    assert.equal(await page.locator("#flora-manual-dups .flora-manual-open-record").count(), 1);

    // An exclusion is the one refusal.
    await page.locator("#fm-doi_r").fill("10.7/excluded");
    await page.locator("#flora-manual-dups .flora-manual-dups-error").waitFor();
    assert.equal(await page.locator("#flora-manual-save").isDisabled(), true);
    await page.locator("#fm-doi_r").fill("10.7/rep");
    await page.locator("#flora-manual-dups .src-dup-warn").waitFor();
    assert.equal(await page.locator("#flora-manual-save").isDisabled(), false);

    // Kept as its own record: the check and the save carry the tick.
    await page.locator("#flora-manual-keep").check();
    await page.locator("#flora-manual-dups .flora-manual-dups-ok").waitFor();
    assert.equal(posts("/check").at(-1).body.keep_distinct, true);
    await page.locator("#fm-note").fill("From a review " + XSS);
    createResponse = null;
    await page.locator("#flora-manual-save").click();
    await page.locator("#flora-manual-notice:not(.hidden)").waitFor();
    const created = posts("").at(-1).body;
    assert.equal(created.keep_distinct, true);
    assert.equal(created.fields.doi_r, "10.7/rep");
    assert.equal(created.note, "From a review " + XSS);
    assert.match(created.client_token, /^[0-9a-f-]{36}$/);
    assert.match(await page.locator("#flora-manual-notice").innerText(), /Saved as MAN-000001\. A record of its own\./);
    assert.equal(await page.locator("#flora-manual-title").textContent(), "MAN-000001 · added by hand");

    // Run pipeline now: queued, then a run already in progress.
    await page.locator("#flora-manual-run").click();
    await page.waitForFunction(() => /Queued/.test(document.querySelector("#flora-manual-run-msg").textContent));
    assert.equal(requests.filter(r => r.path === "/api/admin/source-sync/dispatch").length, 1);
    await page.evaluate(() => showManualNotice({ notice: "Saved again.", run_pipeline: true }));
    await page.locator("#flora-manual-run").click();
    await page.waitForFunction(() => /already in progress/.test(document.querySelector("#flora-manual-run-msg").textContent));

    // An absorbed save offers no pipeline run: no new ID would come of it.
    await page.evaluate(() => showManualNotice({ notice: "Saved as MAN-000004. REPL-000397 has the same identifiers.", run_pipeline: false }));
    assert.equal(await page.locator("#flora-manual-run").count(), 0);

    // The entry, now open, is changed with its version; a stale version reloads it.
    patchStatus = 409;
    await page.locator("#fm-year_r").fill("2021");
    await page.locator("#flora-manual-save").click();
    await page.waitForFunction(() => /changed this entry/.test(document.querySelector("#flora-manual-msg")?.textContent || ""));
    const stale = requests.filter(r => r.method === "PATCH").at(-1);
    assert.equal(stale.body.version, 1);
    await page.locator("#fm-year_r").fill("2021");
    await page.locator("#flora-manual-save").click();
    await page.waitForFunction(() => document.querySelector("#flora-manual-title").textContent.startsWith("MAN-000001"));
    await page.waitForFunction(() => !document.querySelector("#flora-manual-notice").classList.contains("hidden"));
    assert.equal(requests.filter(r => r.method === "PATCH").at(-1).body.fields.year_r, "2021");

    // Withdraw: a reason, asked in the form, never a browser dialog.
    await page.locator("#flora-manual-withdraw-start").click();
    // What withdrawing does comes from the server, which knows the registry.
    assert.match(await page.locator("#flora-manual-withdraw-box").innerText(),
                 /It leaves the FLoRA tab at once\. <img src=x/);
    await page.locator("#flora-manual-withdraw").click();
    await page.locator('[data-error-for="reason"]:not([hidden])').waitFor();
    assert.equal(requests.filter(r => r.path.endsWith("/withdraw")).length, 0);
    await page.locator("#flora-manual-reason").fill("Entered twice");
    await page.locator("#flora-manual-withdraw").click();
    await page.locator("#flora-manual-restore").waitFor();
    const withdrawn = requests.filter(r => r.path.endsWith("/withdraw")).at(-1);
    assert.deepEqual(withdrawn.body, { version: current.version - 1, reason: "Entered twice" });
    assert.equal(await page.locator("#flora-manual-save").count(), 0, "a withdrawn entry is not edited");
    await page.locator("#flora-manual-restore-note").fill("It was right");
    await page.locator("#flora-manual-restore").click();
    await page.locator("#flora-manual-withdraw-start").waitFor();
    assert.equal(requests.filter(r => r.path.endsWith("/restore")).at(-1).body.reason, "It was right");
    await page.locator("#flora-manual-cancel").click();
    assert.equal(await page.locator("#flora-manual-modal").isHidden(), true);

    // ── the entries, under Added by hand ──
    await page.locator('#flora-filters .admin-filter-btn[data-filter="manual"]').click();
    await page.locator("#flora-manual-list .flora-manual-open").first().waitFor();
    assert.equal(await page.locator("#admin-tab-flora .admin-table-wrap").first().isVisible(), false, "the grid steps aside");
    const listed = await page.locator("#flora-manual-list").innerText();
    assert.match(listed, /Merged into REPL-000397/);
    assert.match(listed, /In FLoRA as MAN-000002/);
    assert.match(await page.locator("#flora-manual-summary").innerText(), /2 active · 1 withdrawn/);
    await page.locator("#flora-manual-show-withdrawn").check();
    await page.waitForFunction(() => document.querySelectorAll("#flora-manual-list .flora-manual-open").length === 4);
    assert(requests.some(r => r.path === "/api/admin/flora/manual" && r.search.includes("status=all")));
    assert.match(await page.locator("#flora-manual-list").innerText(), /Merged into REPL-000397 · frozen/);

    // Merged into a record with its FLoRA ID: frozen like its own, withdraw still there.
    await page.locator('#flora-manual-list .flora-manual-open[data-manual-id="MAN-000004"]').click();
    await page.locator("#flora-manual-modal:not(.hidden) .flora-manual-state-absorbed").waitFor();
    assert.equal(await page.locator("#flora-manual-save").count(), 0);
    assert.equal(await page.locator("#fm-doi_r").isDisabled(), true);
    assert.equal(await page.locator("#flora-manual-keep").isDisabled(), true);
    assert.match(await page.locator(".flora-manual-state").innerText(),
                 /fix it in the FLoRA editor for record REPL-000397/);
    assert.match(await page.locator(".flora-manual-state .flora-manual-open-record").textContent(),
                 /Fix it in the FLoRA editor for REPL-000397/);
    assert.equal(await page.locator("#flora-manual-withdraw-start").count(), 1);
    await page.locator("#flora-manual-close").click();

    // A frozen entry is read-only and points at the record to correct.
    await page.locator('#flora-manual-list .flora-manual-open[data-manual-id="MAN-000002"]').click();
    await page.locator("#flora-manual-modal:not(.hidden) .flora-manual-state-in_flora").waitFor();
    assert.equal(await page.locator("#flora-manual-save").count(), 0);
    assert.equal(await page.locator("#fm-doi_o").isDisabled(), true);
    assert.match(await page.locator(".flora-manual-state").innerText(), /Fix it in the FLoRA editor for MAN-000002/i);
    await page.locator(".flora-manual-state .flora-manual-open-record").click();

    // The record editor names the entries behind it and opens them.
    await page.locator("#flora-detail-modal:not(.hidden) .flora-manual-provenance").waitFor();
    assert.equal(await page.locator("#flora-manual-modal").isHidden(), true);
    const provenance = await page.locator(".flora-manual-provenance").innerText();
    assert.match(provenance, /Added by hand as MAN-000002/);
    assert.match(provenance, /MAN-000005, is merged into it/);
    await page.locator('.flora-manual-provenance .flora-manual-open[data-manual-id="MAN-000002"]').click();
    await page.locator("#flora-manual-modal:not(.hidden) .flora-manual-state").waitFor();
    await page.locator("#flora-manual-close").click();
    assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden", "the editor is still open");
    await page.evaluate(() => closeFloraRecord());

    // Back to all records: the grid returns; Open entry on an unassigned row added by hand.
    await page.locator('#flora-filters .admin-filter-btn[data-filter="all"]').click();
    await page.locator("#flora-table-body .flora-manual-btn").waitFor();
    assert.equal(await page.locator("#flora-manual-view").isHidden(), true);
    await page.locator("#flora-table-body .flora-manual-btn").click();
    await page.locator("#flora-manual-modal:not(.hidden) #flora-manual-save").waitFor();
    assert(requests.some(r => r.method === "GET" && r.path === "/api/admin/flora/manual/MAN-000001"));
    assert.match(await page.locator("#fm-ref_o").inputValue(), /<img src=x/);
    await page.locator("#flora-manual-cancel").click();

    // ── Source Records ──
    await page.evaluate(() => switchAdminTab("sources"));
    await page.locator('#src-filters .admin-filter-btn[data-filter="manual"]').click();
    await page.waitForFunction(() => document.querySelector("#sfc-manual").textContent === "1");
    assert(requests.some(r => r.path === "/api/admin/source-records" && r.search.includes("source=manual")));
    await page.evaluate(id => openSourceRecord(id), SOURCE_ID);
    await page.locator("#src-detail-body .flora-manual-provenance").waitFor();
    assert.match(await page.locator("#src-detail-body .flora-manual-provenance").innerText(),
                 /Added by hand by sophie<img src=x onerror="window.__xss=1"> on 2026-11-01 10:00\. Why: Because/);
    await page.locator("#src-detail-body .src-manual-open").click();
    await page.locator("#flora-manual-modal:not(.hidden) #flora-manual-save").waitFor();
    assert.equal(await page.locator("#admin-tab-flora").isVisible(), true);

    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(errors, []);
    console.log("Manual records UI checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
