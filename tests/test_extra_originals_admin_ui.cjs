/* Run with: node tests/test_extra_originals_admin_ui.cjs
 * The "Additional originals" section of an admin entry: collapsed unless a
 * suggestion waits, loaded only when opened, suggestions shown escaped with safe
 * links and their badges, Dismiss, the banner on an added original, and the form
 * that adds originals (by hand or from a suggestion).
 * All requests are intercepted; no server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const P = "11111111-1111-4111-8111-111111111111";
const C = "22222222-2222-4222-8222-222222222222";
const XSS = '<img src=x onerror="window.__xss=1">';

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10_000);
  const errors = [];
  const requests = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { errors.push("dialog: " + dialog.message()); dialog.dismiss(); });

  const published = {
    doi_r: "10.1234/rep", study_r: "1", title_r: "A replication", year_r: "2020",
    url_r: "https://example.test/rep", ref_r: "Ref R", abstract_r: "The replication abstract.",
    doi_o: "10.1234/own", study_o: "", title_o: "Its own original", original_key: "10.1234/own",
    type: "replication", outcome: "failed", outcome_quote: "It failed.", out_quote_source: "abstract",
    outcome_computation: null, outcome_computational_quote: null, out_quote_computational_source: null,
    outcome_robustness: null, outcome_robustness_quote: null, out_quote_robust_source: null,
  };
  let view = {
    parent: { record_id: P, origin: "extractor", validation_status: "validated", admin_checked: true,
              type: "replication", can_add: true, block_code: null, block_reason: null, published },
    parent_record_id: null,
    children: [{ record_id: C, doi_o: "10.5555/added", title_o: "An added original", study_o: "2",
                 study_r: "1", year_o: "2001", ref_o: null, url_o: null, inherits_outcome: true,
                 type: "replication", outcome: "failed", validation_status: "validated",
                 admin_name: "sophie", created_at: null }],
    suggestions: [
      { key: "doi:10.9/new", doi_o: "10.9/new", url_o: "", title_o: "A suggested original " + XSS,
        study_o: "3", notes: ["Study 3 " + XSS], open_suggestion_ids: [7, 8], status: "open",
        suggested_by: [{ suggestion_id: 7, validator_name: "ana" + XSS, status: "open" },
                       { suggestion_id: 8, validator_name: "ben", status: "open" }],
        is_own_original: false, coded_match: null, resolved_record_id: null },
      { key: "url:javascript:alert(1)", doi_o: "", url_o: "javascript:alert(1)", title_o: "",
        study_o: "", notes: [], open_suggestion_ids: [9], status: "open",
        suggested_by: [{ suggestion_id: 9, validator_name: "cy", status: "open" }],
        is_own_original: false, coded_match: null, resolved_record_id: null },
      { key: "doi:10.1234/own", doi_o: "10.1234/own", url_o: "", title_o: "", study_o: "",
        notes: [], open_suggestion_ids: [10], status: "open",
        suggested_by: [{ suggestion_id: 10, validator_name: "ana", status: "open" }],
        is_own_original: true, coded_match: null, resolved_record_id: null },
      { key: "doi:10.1234/sib", doi_o: "10.1234/sib", url_o: "", title_o: "Coded sibling", study_o: "",
        notes: [], open_suggestion_ids: [11], status: "open",
        suggested_by: [{ suggestion_id: 11, validator_name: "ben", status: "open" }],
        is_own_original: false, resolved_record_id: null,
        coded_match: { record_id: "33333333-3333-4333-8333-333333333333", origin: "extractor", validation_status: "need_review" } },
    ],
  };
  let createResponse = null;
  let failNextGet = false;

  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname.startsWith("/api/admin/")) {
      requests.push({ method: request.method(), path: url.pathname, body: request.postDataJSON?.() ?? null });
    }
    if (url.pathname === `/api/admin/entries/${P}/extra-originals` && request.method() === "GET") {
      if (failNextGet) {
        failNextGet = false;
        return route.fulfill({ status: 503, json: { detail: "Service unavailable" } });
      }
      return route.fulfill({ status: 200, json: view });
    }
    if (url.pathname === `/api/admin/entries/${P}/extra-originals` && request.method() === "POST") {
      const [status, json] = createResponse || [200, { created: [], parent_record_id: P }];
      return route.fulfill({ status, json });
    }
    if (url.pathname.includes("/dismiss")) return route.fulfill({ status: 200, json: { dismissed: 2 } });
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    return route.fulfill({ status: 200, json: { records: [], entries: [], counts: {}, total: 0 } });
  });

  const record = {
    record_id: P, origin: "extractor", type: "replication", outcome: "failed",
    validation_status: "validated", admin_checked: true, title_r: "A replication",
    doi_r: "10.1234/rep", title_o: "Its own original", doi_o: "10.1234/own",
  };
  const review = (rec, summary) => page.evaluate(([r, s]) => {
    document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
    document.querySelector("#admin-screen").classList.remove("hidden");
    document.querySelector("#admin-detail-modal").classList.remove("hidden");
    renderAdminDetail({ record: r, extra_originals_summary: s });
  }, [rec, summary]);
  const gets = () => requests.filter(r => r.method === "GET" && r.path.endsWith("/extra-originals")).length;

  try {
    await page.goto("http://flora.test/");

    // Nothing waiting: collapsed, and nothing is fetched until it is opened.
    await review(record, { open_suggestions: 0, children: 1 });
    const section = page.locator("#xo-admin");
    assert.equal(await section.evaluate(d => d.open), false);
    assert.match(await section.locator("summary").textContent(), /Additional originals · 1 added/);
    assert.equal(gets(), 0);
    await section.locator("summary").click();
    await page.locator(".admin-xo-item").first().waitFor();
    assert.equal(gets(), 1);

    // A failed load is shown, and opening the section again retries it.
    await review(record, { open_suggestions: 0, children: 1 });
    failNextGet = true;
    await section.locator("summary").click();
    await page.locator("#xo-admin-body .faq-error").waitFor();
    assert.equal(gets(), 2);
    await section.locator("summary").click();
    await section.locator("summary").click();
    await page.locator(".admin-xo-item").first().waitFor();
    assert.equal(gets(), 3);

    // A suggestion waiting: open from the start.
    await page.evaluate(() => { delete _adminXoCache[Object.keys(_adminXoCache)[0]]; });
    await review(record, { open_suggestions: 5, children: 1 });
    assert.equal(await section.evaluate(d => d.open), true);
    await page.locator("[data-xo-dismiss]").first().waitFor();
    const text = await page.locator("#xo-admin-body").textContent();
    assert.match(text, /An added original/);
    assert.match(text, /outcome inherited/);
    assert.match(text, /suggested by ana<img src=x onerror="window.__xss=1">, ben/);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.equal(await page.locator('#xo-admin-body a[href^="javascript"]').count(), 0);
    assert.equal(await page.locator('#xo-admin-body a[href="https://doi.org/10.9/new"]').count(), 1);
    assert.match(text, /this entry's own original/);
    assert.match(text, /already coded/);
    // Usable only when it is neither the entry's own original nor already coded.
    const use = page.locator("[data-xo-use]");
    assert.deepEqual(await use.evaluateAll(bs => bs.map(b => b.disabled)), [false, false, true, true]);

    // Dismiss posts for the group and reloads.
    await page.locator("[data-xo-dismiss]").first().click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("dismissed"));
    const dismiss = requests.find(r => r.path.endsWith("/dismiss"));
    assert.equal(dismiss.path, `/api/admin/entries/${P}/extra-originals/suggestions/7/dismiss`);

    // An added original: a banner instead of the section.
    await review({ ...record, record_id: C, origin: "admin_extra_original", parent_record_id: P,
                   admin_name: "sophie" + XSS, inherits_outcome: false }, {});
    assert.equal(await page.locator("#xo-admin").count(), 0);
    const banner = await page.locator(".admin-xo-banner").textContent();
    assert.match(banner, /Additional original/);
    assert.match(banner, /it has an outcome of its own/);
    assert.equal(await page.evaluate(() => window.__xss), undefined);

    // ── The add form ───────────────────────────────────────────────────────────
    const fresh = async (v, summary = { open_suggestions: 0, children: 0 }) => {
      view = v;
      await page.evaluate(() => {
        for (const k of Object.keys(_adminXoCache)) delete _adminXoCache[k];
        for (const k of Object.keys(_adminXoDrafts)) delete _adminXoDrafts[k];
      });
      await review(record, summary);
      await page.locator("#xo-admin").evaluate(d => { d.open = true; });   // fires the lazy load
      await page.locator("#xo-add-on").waitFor();
    };
    const okDialog = async () => {
      await page.locator("#dialog-modal:not(.hidden)").waitFor();
      const message = await page.locator("#dialog-message").textContent();
      await page.locator("#dialog-buttons button").first().click();
      return message;
    };
    const field = (name) => page.locator(`#xo-add-list .xo-open [data-xoa-field="${name}"]`);

    // Off by default, even with no suggestions at all.
    await fresh({ ...view, suggestions: [], children: [] });
    assert.equal(await page.locator("#xo-add-on").isChecked(), false);
    assert.equal(await page.locator("#xo-add-body").isHidden(), true);
    await page.locator("#xo-add-on").check();
    assert.equal(await page.locator("#xo-add-list .xo-item").count(), 1);
    assert.equal(await field("study_r").inputValue(), "1", "the replication study defaults to the entry's");
    assert.match(await page.locator("#xo-add-list .xo-abstract").textContent(), /The replication abstract\./);
    assert.equal(await page.locator('#xo-add-list [data-xoa-field="abstract_r"]').count(), 0, "the abstract is read-only");
    await field("doi_o").fill("10.5555/first");
    await field("title_o").fill("First added");
    await field("study_o").fill("2");

    // "+" folds the first entry to one line; the own-outcome toggle shows the
    // replication fields.
    await page.locator("#xo-add-more").click();
    assert.equal(await page.locator("#xo-add-list .xo-collapsed").count(), 1);
    assert.match(await page.locator("#xo-add-list .xo-collapsed .xo-summary").textContent(),
                 /#1 · First added · 10\.5555\/first · Study 2 · inherits outcome/);
    await field("title_o").fill("A book");
    await field("ref_o").fill("Smith, J. (1990). A book.");
    await page.locator("#xo-add-list .xo-open [data-xoa-own]").check();
    assert.equal(await field("outcome").count(), 1);
    assert.equal(await field("outcome_computation").count(), 0);
    await field("outcome").selectOption("mixed");
    await field("outcome_quote").fill("Partly replicated.");
    assert.match(await page.locator("#xo-add-save").textContent(), /Add these 2 originals/);

    // The server names the entry at fault: that entry opens and the message shows.
    createResponse = [409, { detail: { code: "extra_original_published", index: 0,
                                       message: "Original #1 is an original already published for this replication." } }];
    await page.locator("#xo-add-save").click();
    assert.match(await okDialog(), /Original #1 is an original already published/);
    assert.equal(await field("title_o").inputValue(), "First added");
    const sent = requests.filter(r => r.method === "POST" && r.path.endsWith("/extra-originals")).at(-1).body;
    assert.deepEqual(sent.originals.map(o => [o.doi_o, o.title_o, o.study_o, o.study_r, o.own_outcome]),
                     [["10.5555/first", "First added", "2", "1", false], [null, "A book", null, "1", true]]);
    assert.equal(sent.originals[0].outcome, undefined, "an inheriting entry sends no outcome");
    assert.deepEqual([sent.originals[1].outcome, sent.originals[1].outcome_quote, sent.originals[1].ref_o],
                     ["mixed", "Partly replicated.", "Smith, J. (1990). A book."]);

    // ✕ removes an entry; success clears the form and reloads the section.
    await page.locator('#xo-add-list .xo-collapsed [data-xoa-remove="1"]').click();
    assert.equal(await page.locator("#xo-add-list .xo-item").count(), 1);
    createResponse = [200, { created: [{ record_id: C }], parent_record_id: P }];
    const reloads = gets();
    await page.locator("#xo-add-save").click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Added 1 original"));
    assert.ok(gets() > reloads);

    // A reproduction entry asks for both axes, each with its quote.
    await fresh({ ...view, parent: { ...view.parent, type: "reproduction" } });
    await page.locator("#xo-add-on").check();
    await page.locator("#xo-add-list .xo-open [data-xoa-own]").check();
    assert.equal(await field("outcome").count(), 0);
    assert.equal(await field("outcome_computation").count(), 1);
    assert.equal(await field("outcome_robustness_quote").count(), 1);

    // "Use as additional original" opens a pre-filled entry carrying the suggestions.
    await fresh({ ...view, parent: { ...view.parent, type: "replication" }, suggestions: [{
      key: "doi:10.9/new", doi_o: "10.9/new", url_o: "", title_o: "Suggested", study_o: "3, 4",
      notes: [], open_suggestion_ids: [7, 8], status: "open", is_own_original: false, coded_match: null,
      suggested_by: [{ suggestion_id: 7, validator_name: "ana", status: "open" }],
    }] }, { open_suggestions: 1, children: 0 });
    await page.locator("[data-xo-use]").click();
    assert.equal(await page.locator("#xo-add-on").isChecked(), true);
    assert.equal(await field("doi_o").inputValue(), "10.9/new");
    assert.equal(await field("title_o").inputValue(), "Suggested");
    assert.equal(await field("study_o").inputValue(), "3");
    createResponse = [200, { created: [{ record_id: C }], parent_record_id: P }];
    await page.locator("#xo-add-save").click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Added 1 original"));
    const used = requests.filter(r => r.method === "POST" && r.path.endsWith("/extra-originals")).at(-1).body;
    assert.deepEqual(used.originals[0].from_suggestion_ids, [7, 8]);

    // Not addable (not admin-reviewed yet): the reason shows and there is no form.
    await page.evaluate(() => { for (const k of Object.keys(_adminXoCache)) delete _adminXoCache[k]; });
    view = { ...view, parent: { ...view.parent, can_add: false, block_code: "parent_not_admin_checked",
                                block_reason: "No admin has reviewed this entry yet." } };
    await review(record, { open_suggestions: 1, children: 0 });
    await page.locator(".admin-xo-notice").waitFor();
    assert.match(await page.locator(".admin-xo-notice").textContent(), /No admin has reviewed/);
    assert.equal(await page.locator("#xo-add-on").count(), 0);
    assert.equal(await page.locator("[data-xo-use]").isDisabled(), true);

    assert.deepEqual(errors, []);
    console.log("Extra originals admin checks passed: lazy section, escaping, safe links, badges, dismiss, child banner, add form, errors by entry, suggestion prefill.");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
