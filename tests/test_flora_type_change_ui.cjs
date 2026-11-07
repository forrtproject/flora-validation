/* Run with: node tests/test_flora_type_change_ui.cjs
 * The FLoRA record editor's Change study type: closed until opened, the other
 * type's fields blank with its outcome required, a confirmation that says what the
 * change does, one POST to /type, and the record reloaded as its new type. All
 * requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const AXIS_FIELDS = [
  ["outcome_computation", "Computational", false, ["computational issues", "computationally reproducible",
                                                   "not checked", "technical failure"]],
  ["outcome_computational_quote", "Computational", true, null],
  ["out_quote_computational_source", "Computational", false, null],
  ["outcome_robustness", "Robustness", false, ["not checked", "robust", "robustness challenges"]],
  ["outcome_robustness_quote", "Robustness", true, null],
  ["out_quote_robust_source", "Robustness", false, null],
];
const REQUIRED_AXES = ["outcome_computation", "outcome_robustness"];

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const posts = [];
  let postStatus = 200;
  let converted = false;
  let recordLoads = 0;
  const spec = (field, group, long, choices, required = false) =>
    ({ field, label: field, group, long, choices, required });
  // A replication with its outcome corrected, which a reproduction cannot hold.
  const replication = () => ({
    flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
    type: "replication", source: "replications", doi_o: "10.1/a", outcome: "failed",
    edited_fields: "outcome", merged_sources: [],
    editing: {
      version: 3,
      fields: [
        { ...spec("doi_o", "Original", false, null), value: "10.1/a" },
        { ...spec("outcome", "Outcome", false, ["failed", "mixed", "successful"]), value: "failed",
          edit: { derived: "successful", edited_by: "ana", edited_at: "2026-10-01T10:00:00Z",
                  note: null, change_id: 3 } },
        { ...spec("outcome_quote", "Outcome", true, null), value: "It did not replicate." },
        { ...spec("outcome_quote_source", "Outcome", false, null), value: "abstract" },
      ],
      history: [
        { change_id: 3, changed_at: "2026-10-01T10:00:00Z", admin_handle: "ana", action: "edit", note: null,
          notes: {}, changes: { outcome: ["successful", "failed"] }, corrections: { outcome: "failed" },
          restored_from: null },
      ],
      source_edits: [],
      type_change: {
        type: "reproduction", drops: ["outcome"], derived_type: null,
        fields: AXIS_FIELDS.map(([field, group, long, choices]) =>
          spec(field, group, long, choices, REQUIRED_AXES.includes(field))),
      },
    },
  });
  // The same record once converted: its type is a correction, the build's is kept.
  const reproduction = () => ({
    flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
    type: "reproduction", source: "replications", doi_o: "10.1/a",
    outcome: "computational issues, robust", edited_fields: "outcome_computation outcome_robustness type",
    merged_sources: [],
    editing: {
      version: 4,
      fields: [
        { ...spec("doi_o", "Original", false, null), value: "10.1/a" },
        ...AXIS_FIELDS.map(([field, group, long, choices]) => ({ ...spec(field, group, long, choices),
          value: { outcome_computation: "computational issues", outcome_robustness: "robust",
                   out_quote_computational_source: "abstract" }[field] || null })),
      ],
      history: [
        { change_id: 4, changed_at: "2026-11-07T10:00:00Z", admin_handle: "luke", action: "edit", note: null,
          notes: { type: "coded in the wrong sheet" },
          changes: { type: ["replication", "reproduction"],
                     outcome: ["failed", "computational issues, robust"],
                     outcome_computation: [null, "computational issues"], outcome_robustness: [null, "robust"] },
          corrections: { type: "reproduction", outcome_computation: "computational issues",
                         outcome_robustness: "robust", out_quote_computational_source: "abstract" },
          restored_from: null },
        replication().editing.history[0],
      ],
      source_edits: [],
      type_change: {
        type: "replication", drops: ["outcome_computation", "outcome_robustness", "out_quote_computational_source"],
        derived_type: "replication",
        fields: [spec("outcome", "Outcome", false, ["failed", "mixed", "successful"], true),
                 spec("outcome_quote", "Outcome", true, null), spec("outcome_quote_source", "Outcome", false, null)],
      },
    },
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001/type") {
      posts.push(JSON.parse(request.postData()));
      if (postStatus === 400) return route.fulfill({ status: 400, json: { detail: "outcome must be one of: failed, mixed" } });
      if (postStatus === 409) return route.fulfill({ status: 409, json: { detail: "This record was changed by someone else." } });
      converted = true;
      return route.fulfill({ json: { export_id: "FLORA-000001", change_id: 4, version: 4, published: true,
                                     changed_fields: ["outcome_computation", "outcome_robustness", "type"],
                                     reverted_fields: ["outcome"], warnings: [] } });
    }
    if (url.pathname === "/api/admin/flora/REPL-000001") {
      recordLoads++;
      return route.fulfill({ json: converted ? reproduction() : replication() });
    }
    if (url.pathname === "/api/admin/flora") {
      return route.fulfill({ json: {
        records: [{ flora_id: "REPL-000001", export_id: "FLORA-000001", source_display_id: "REPL-000001",
                    type: converted ? "reproduction" : "replication", source: "replications", doi_o: "10.1/a",
                    ref_o: "Ref O", doi_r: "10.1/b", ref_r: "Ref R",
                    outcome: converted ? "computational issues, robust" : "failed",
                    edited_fields: converted ? "outcome_computation outcome_robustness type" : "outcome" }],
        total: 1, page: 1, per_page: 50,
        counts: { all_records: 1, replications: converted ? 0 : 1, reproductions: converted ? 1 : 0,
                  edited: 1, conflicts: 0, sources: [], outcomes: [] } } });
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
    await page.locator("#flora-table-body .flora-view-btn").waitFor();
    const typeCell = page.locator("#flora-table-body tr").first().locator("td").nth(2);
    assert.equal(await typeCell.innerText(), "replication");

    // Nothing to render without what the server says a change of type takes.
    assert.deepEqual(await page.evaluate(() => [
      floraTypeChange({ editing: { fields: [] } }), floraTypeChange({ editing: { type_change: null } }),
      floraTypeChange({ editing: null }), floraTypeChange({}),
      /Changed the study type/.test(floraChangeLog({ fields: [], type_change: null, history: [{
        change_id: 1, action: "edit", note: null, notes: {}, changed_at: "2026-11-07T10:00:00Z",
        admin_handle: "luke", changes: { type: ["replication", "reproduction"] }, corrections: {} }] }))]),
      ["", "", "", "", true]);

    await page.locator(".flora-view-btn").click();
    await page.locator("#flora-detail-modal:not(.hidden) #flora-save-btn").waitFor();
    const section = page.locator("#flora-type-change");
    const submit = page.locator("#flora-type-submit");
    const hint = page.locator("#flora-type-hint");
    // A required field's label reads "(required)" too; its accessible name does not.
    const labelled = field => section.getByLabel(
      ["outcome", ...REQUIRED_AXES].includes(field) ? field + " (required)" : field, { exact: true });

    // Closed until opened, under its own summary, below the record's fields.
    assert.equal(await section.evaluate(el => el.open), false);
    assert.equal(await section.locator("summary").innerText(), "Change study type");
    assert.equal(await section.locator("#flora-type-note").isVisible(), false);
    await section.locator("summary").click();
    assert.match(await section.innerText(), /This record is a replication\. Changing it to a reproduction/);
    assert.equal(await section.locator(".flora-edited-badge").count(), 0);
    assert.deepEqual(await section.locator("h4").allTextContents(), ["Computational", "Robustness"]);

    // Every control by its label, blank; the two axes required, without the values
    // the checks would flag; the sources with the manual form's hint.
    for (const [field, , long, choices] of AXIS_FIELDS) {
      const control = labelled(field);
      assert.equal(await control.count(), 1, field);
      assert.equal(await section.getByRole(choices ? "combobox" : "textbox", { name: field, exact: true }).count(), 1, field);
      assert.equal(await control.inputValue(), "", field);
      assert.equal(await control.evaluate(el => el.required), REQUIRED_AXES.includes(field), field);
      assert.equal(await control.evaluate(el => el.tagName),
        choices ? "SELECT" : long ? "TEXTAREA" : "INPUT", field);
      if (choices) {
        assert.deepEqual(await control.locator("option").evaluateAll(options => options.map(o => o.value)),
          ["", ...choices], field);
      }
      if (field.endsWith("_source")) assert.match(await control.getAttribute("placeholder"), /abstract.*full_text/);
      assert.equal(await control.getAttribute("data-field"), null, field);
    }
    assert.equal(await section.locator(".flora-manual-req").count(), 2);

    // The submit waits for both axes, the hint names what is missing and that a
    // stale record reloads.
    assert.equal(await submit.textContent(), "Change to a reproduction");
    assert.equal(await submit.getAttribute("aria-describedby"), "flora-type-hint");
    assert.equal(await submit.isEnabled(), false);
    assert.match(await hint.innerText(), /Choose outcome_computation and outcome_robustness/);
    assert.match(await hint.innerText(), /reloads and what you entered here is lost/);
    await labelled("outcome_computation").selectOption("computational issues");
    assert.equal(await submit.isEnabled(), false);
    assert.match(await hint.innerText(), /Choose outcome_robustness to change/);
    assert.doesNotMatch(await hint.innerText(), /outcome_computation/);

    // Its controls are not the record's: Save corrections sends none of them.
    await page.locator("#flora-save-btn").click();
    assert.equal(await page.locator("#flora-save-msg").innerText(), "Nothing changed.");

    await labelled("outcome_robustness").selectOption("robust");
    assert.equal(await submit.isEnabled(), true);
    assert.doesNotMatch(await hint.innerText(), /Choose/);
    await labelled("out_quote_computational_source").fill("abstract");
    await page.locator("#flora-type-note").fill("coded in the wrong sheet");
    // An unsaved edit to the record, which the reload would discard.
    await page.locator('#flora-detail-body [data-field="doi_o"]').fill("10.1/changed");

    // The confirmation says what the change does; Cancel sends nothing.
    await submit.click();
    const dialog = page.locator("#dialog-message");
    await dialog.waitFor();
    const said = await dialog.innerText();
    assert.match(said, /Change REPL-000001 from a replication to a reproduction\?/);
    assert.match(said, /outcome_computation: computational issues; outcome_robustness: robust\./);
    assert.match(said, /no quote until axis quotes are added/);
    assert.match(said, /These corrections leave the record and stay in the log: outcome\./);
    assert.match(said, /Unsaved edits are discarded\./);
    await page.locator("#dialog-buttons button", { hasText: "Cancel" }).click();
    assert.equal(posts.length, 0);
    assert.equal(await page.evaluate(() => document.activeElement.id), "flora-type-submit");

    // Confirmed: one POST with the required fields and the filled one, blanks left out.
    await submit.click();
    await page.locator("#dialog-buttons button", { hasText: "Confirm" }).click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Changed to a reproduction"));
    assert.deepEqual(posts, [{ type: "reproduction", version: 3,
      fields: { outcome_computation: "computational issues", outcome_robustness: "robust",
                out_quote_computational_source: "abstract" },
      notes: { type: "coded in the wrong sheet" } }]);
    assert.match(await page.locator("#toast").innerText(), /stored dataset is updated/);

    // Reloaded as a reproduction: its axes are the record's fields, its outcome is gone,
    // and the log names the change with its note.
    await page.waitForFunction(() => document.querySelector('#flora-detail-body [data-field="outcome_computation"]'));
    assert.equal(await page.locator('#flora-detail-body [data-field="outcome"]').count(), 0);
    assert.equal(await page.locator('#flora-detail-body [data-field="outcome_robustness"]').inputValue(), "robust");
    assert.match(await page.locator("#flora-save-msg").innerText(), /Changed to a reproduction\./);
    await page.waitForFunction(() => document.activeElement?.matches("#flora-type-change summary"));
    await page.locator("#flora-change-log summary").click();
    const latest = page.locator("#flora-change-log tbody tr").first();
    assert.match(await latest.innerText(), /Changed the study type/);
    assert.match(await latest.locator(".flora-change-field", { hasText: "type" }).first().innerText(),
      /replication → reproduction\s*coded in the wrong sheet/);
    await page.waitForFunction(() =>
      document.querySelector("#flora-table-body tr td:nth-child(3)")?.textContent === "reproduction");

    // The section now offers the way back, and says the type is a correction.
    await section.locator("summary").click();
    assert.match(await section.innerText(), /This record is a reproduction\s*corrected\s*built as replication/);
    assert.equal(await submit.textContent(), "Change to a replication");
    assert.deepEqual(await section.locator("h4").allTextContents(), ["Outcome"]);

    // Refused: the reason by the button, which comes back on.
    postStatus = 400;
    await labelled("outcome").selectOption("mixed");
    await submit.click();
    assert.match(await dialog.innerText(), /outcome: mixed\. Its axes are cleared\. It goes back to the type it was built as/);
    assert.match(await dialog.innerText(), /leave the record and stay in the log: outcome_computation, outcome_robustness/);
    await page.locator("#dialog-buttons button", { hasText: "Confirm" }).click();
    await page.waitForFunction(() => /must be one of/.test(document.querySelector("#flora-type-msg")?.textContent || ""));
    assert.equal(await submit.isEnabled(), true);
    assert.deepEqual(posts[1], { type: "replication", version: 4, fields: { outcome: "mixed" }, notes: {} });

    // Changed by someone else meanwhile: it says so and reloads.
    postStatus = 409;
    const loads = recordLoads;
    await submit.click();
    await page.locator("#dialog-buttons button", { hasText: "Confirm" }).click();
    await page.waitForFunction(() => /someone else/.test(document.querySelector("#flora-type-msg")?.textContent || ""));
    await page.waitForFunction(() => {
      const reloaded = document.querySelector("#flora-type-change");
      return reloaded && !reloaded.open;
    });
    assert.equal(recordLoads, loads + 1);

    // Narrow screens: the open section fits without scrolling sideways.
    await page.setViewportSize({ width: 400, height: 800 });
    await section.locator("summary").click();
    const widths = await page.evaluate(() => ["#flora-type-change", "#flora-detail-body"].map(selector => {
      const el = document.querySelector(selector);
      return [selector, el.scrollWidth, el.clientWidth];
    }));
    for (const [selector, scroll, client] of widths) assert(scroll <= client, `${selector} ${scroll} > ${client}`);

    assert.deepEqual(errors, []);
    console.log("FLoRA type change UI checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
