/* Run with: node tests/test_withdraw_entry_ui.cjs
 * "Withdraw from validation" on the admin entry screen: shown only while the entry is
 * open for validation, disabled for an admin who may not decide it, a scope and a
 * reason required before anything is sent, what a whole-article withdrawal takes
 * along, the pairs left for review afterwards, a refusal naming the blocked pairs, and
 * the same entry shown again, withdrawn, with its scope.
 * All requests are intercepted; no server, database, or remote service runs. What the
 * route does is tested against PostgreSQL in tests/test_decided_records.py.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const A = "11111111-1111-4111-8111-111111111111";
const S1 = "22222222-2222-4222-8222-222222222222";
const S2 = "33333333-3333-4333-8333-333333333333";
const XSS = '<img src=x onerror="window.__xss=1">';

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  const withdrawals = [];
  const previews = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });

  const record = {
    record_id: A, origin: "extractor", type: "replication", outcome: "failed",
    validation_status: "validation_inprogress", admin_checked: false,
    title_r: "A replication", doi_r: "10.1234/rep", study_r: "1", title_o: "The wrong original",
    doi_o: "10.1234/wrong", study_o: "", abstract_r: "An abstract.", outcome_quote: "It failed.",
    admin_notes: "Earlier note",
  };
  const preview = {
    record_id: A, scope: "not_replication",
    open: [{ record_id: S1, title_o: "Open sibling", doi_o: "10.1/s1", validation_status: "unvalidated" }],
    review: [{ record_id: S2, title_o: XSS, doi_o: "10.1/s2", validation_status: "consensus_reached" }],
    blocked: [],
  };
  // app.py WITHDRAW_SCOPES: the label the server puts in the note.
  const SERVER_LABELS = { wrong_original: "wrong original", duplicate: "correct pair already in FLoRA",
                          not_replication: "not a replication or reproduction" };
  const B = "44444444-4444-4444-8444-444444444444";
  let listed = [];          // what the entries list returns
  let gate = null;          // holds the withdraw answer back while set
  let previewGate = null, previewFails = false;
  let withdrawAnswer = [200, { withdrawn: true, record_id: A, scope: "not_replication",
                               withdrawn_records: [A, S1], review_records: preview.review }];

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === `/api/admin/entries/${A}/withdraw-preview`) {
      previews.push(url.searchParams.get("scope"));
      if (previewGate) await previewGate;
      if (previewFails) return route.fulfill({ status: 500, json: { detail: "Lookup failed" } });
      return route.fulfill({ status: 200, json: preview });
    }
    if (url.pathname === `/api/admin/entries/${A}/withdraw` && request.method() === "POST") {
      withdrawals.push(request.postDataJSON());
      if (gate) await gate;
      const [status, json] = withdrawAnswer;
      return route.fulfill({ status, json });
    }
    if (url.pathname === `/api/admin/entries/${A}`) {
      // As the server stores it: the note and the logged scope come from the request sent.
      const sent = withdrawals.at(-1) || {};
      const note = `Withdrawn before validation (${SERVER_LABELS[sent.scope]}): ${sent.reason}\n\nEarlier note`;
      return route.fulfill({ status: 200, json: {
        record: { ...record, validation_status: "rejected", admin_checked: true, admin_notes: note },
        extra_originals_summary: { open_suggestions: 0, children: 0 },
        decisions: [{ action: "withdrawn", admin_handle: "boss", decided_at: "2026-11-13T10:00:00Z",
                      changes: { admin_notes: ["Earlier note", note] },
                      detail: { scope: sent.scope, reason: sent.reason } }],
      } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    if (url.pathname === "/api/admin/entries") {
      return route.fulfill({ status: 200, json: { entries: listed, counts: {}, total: listed.length, page: 1 } });
    }
    return route.fulfill({ status: 200, json: { records: [], entries: [], counts: {}, total: 0 } });
  });

  const review = (rec, data = {}) => page.evaluate(([r, d]) => {
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    document.querySelector("#admin-screen").classList.remove("hidden");
    document.querySelector("#admin-detail-modal").classList.remove("hidden");
    renderAdminDetail({ record: r, extra_originals_summary: { open_suggestions: 0, children: 0 }, ...d });
  }, [rec, data]);
  const button = page.locator("#admin-withdraw-btn");
  const dialogButton = (label) => page.locator("#dialog-buttons button", { hasText: label });
  const opened = () => page.locator("#dialog-modal:not(.hidden)").waitFor();
  const closed = () => page.locator("#dialog-modal").waitFor({ state: "hidden" });
  const title = () => page.locator("#dialog-title").textContent();
  const scope = (value) => page.locator(`input[name="withdraw-scope"][value="${value}"]`);
  const shot = async (name) => {
    if (process.env.SHOT_DIR) await page.screenshot({ path: path.join(process.env.SHOT_DIR, name) });
  };
  const refused = async (pattern) => {
    await opened();
    assert.match(await page.locator("#dialog-message").innerText(), pattern);
    await dialogButton("OK").click();
    await opened();   // the withdraw dialog again, as it was
    assert.equal(await title(), "Withdraw from validation");
  };

  try {
    await page.goto("http://flora.test/");

    // ---- Only an entry still open for validation offers it.
    for (const status of ["need_review", "consensus_reached", "validated"]) {
      await review({ ...record, validation_status: status });
      assert.equal(await button.count(), 0, status);
    }
    await review({ ...record, validation_status: "unvalidated" });
    assert.equal(await button.isEnabled(), true);

    // ---- An admin who may not decide it sees why, and cannot press it.
    await review(record, { self_approval: { mine: true, allowed: false, reason: "Another admin decides this." } });
    assert.equal(await button.isDisabled(), true);
    assert.match(await button.locator("xpath=..").innerText(), /Another admin decides this\./);

    // ---- Three scopes, none chosen; cancelling sends nothing.
    await review(record);
    await button.click();
    await opened();
    assert.equal(await title(), "Withdraw from validation");
    assert.equal(await page.locator('input[name="withdraw-scope"]').count(), 3);
    assert.equal(await page.locator('input[name="withdraw-scope"]:checked').count(), 0);
    const labels = await page.locator(".withdraw-scope strong").allTextContents();
    assert.deepEqual(labels, ["Wrong original: withdraw this pair",
                              "Correct pair already in FLoRA: withdraw this pair permanently",
                              "Not a replication/reproduction: withdraw the whole article"]);
    await dialogButton("Cancel").click();
    await closed();
    assert.equal(withdrawals.length, 0);

    // ---- Scope and reason are both required; a refusal keeps what was entered.
    await button.click();
    await opened();
    await page.locator("#admin-withdraw-reason").fill("Paired with the wrong original");
    await dialogButton("Withdraw").click();
    await refused(/Choose what kind of withdrawal/);
    assert.equal(await page.locator("#admin-withdraw-reason").inputValue(), "Paired with the wrong original");
    await scope("duplicate").check();
    await page.locator("#admin-withdraw-reason").fill("   ");
    await dialogButton("Withdraw").click();
    await refused(/Give a reason/);
    assert.equal(await scope("duplicate").isChecked(), true);
    assert.equal(withdrawals.length, 0);
    // A single pair needs no preview.
    assert.equal(await page.locator("#admin-withdraw-preview").innerHTML(), "");
    assert.deepEqual(previews, []);

    // ---- The whole article: how many pairs go along, and which need review.
    await scope("not_replication").check();
    await page.locator("#admin-withdraw-preview p").first().waitFor();
    await page.locator("#admin-withdraw-preview", { hasText: "will also be withdrawn" }).waitFor();
    const previewText = await page.locator("#admin-withdraw-preview").innerText();
    assert.match(previewText, /1 other pair of this article still open for validation will also be withdrawn/);
    assert.match(previewText, /1 other pair already decided or waiting for an admin will not be changed/);
    assert.match(previewText, /consensus reached/);
    assert.deepEqual(previews, ["not_replication"]);
    assert.ok(!(await page.locator("#admin-withdraw-preview").innerHTML()).includes("<img"), "titles are escaped");
    await scope("wrong_original").check();
    assert.equal(await page.locator("#admin-withdraw-preview").innerHTML(), "");
    await scope("not_replication").check();
    await page.locator("#admin-withdraw-preview", { hasText: "will also be withdrawn" }).waitFor();
    await page.locator("#admin-withdraw-reason").fill("  A commentary, not a replication  ");
    await shot("withdraw_dialog.png");

    // An article that cannot be told apart does not claim to have no other pairs.
    assert.match(await page.evaluate(() => _withdrawPreviewHtml({ article_key: null, open: [], review: [], blocked: [] })),
                 /cannot be found\. Only this pair will be withdrawn/);

    // ---- Sent; the pairs left for review are listed; the entry shows its scope.
    await dialogButton("Withdraw").click();
    await page.locator("#dialog-title", { hasText: "Review these pairs" }).waitFor();
    assert.deepEqual(withdrawals, [{ scope: "not_replication", reason: "A commentary, not a replication" }]);
    const reviewText = await page.locator("#dialog-message").innerText();
    assert.match(reviewText, /1 other pair of this article was already decided or waiting for an admin/);
    assert.match(reviewText, /consensus reached/);
    assert.ok(!(await page.locator("#dialog-message").innerHTML()).includes("<img"), "titles are escaped");
    await shot("withdraw_review.png");
    await dialogButton("OK").click();
    await page.locator(".not-val-decision").waitFor();
    const notice = await page.locator(".not-val-decision .not-val-who").innerText();
    assert.match(notice, /This entry was withdrawn before validation \(whole article: not a replication or reproduction\)/);
    assert.doesNotMatch(notice, /comes back/);
    assert.match(await page.locator(".admin-decisions li").textContent(),
                 /Withdrawn before validation \(whole article: not a replication or reproduction\)/);
    assert.equal(await button.count(), 0);
    assert.equal(await page.locator("#admin-note-text").inputValue(),
                 "Withdrawn before validation (not a replication or reproduction): A commentary, not a replication\n\nEarlier note");
    await shot("withdraw_after.png");

    // ---- A wrong original says it can come back.
    withdrawAnswer = [200, { withdrawn: true, record_id: A, scope: "wrong_original",
                             withdrawn_records: [A], review_records: [] }];
    await review(record);
    await button.click();
    await opened();
    await scope("wrong_original").check();
    await page.locator("#admin-withdraw-reason").fill("Wrong original");
    await dialogButton("Withdraw").click();
    await page.locator(".not-val-decision").waitFor();
    assert.match(await page.locator(".not-val-decision .not-val-who").innerText(),
                 /\(wrong original\).*comes back for validation if the extractor pairs it with a different original/s);
    assert.deepEqual(withdrawals.at(-1), { scope: "wrong_original", reason: "Wrong original" });
    assert.equal(await page.locator("#admin-note-text").inputValue(),
                 "Withdrawn before validation (wrong original): Wrong original\n\nEarlier note");
    assert.match(await page.locator(".admin-decisions li").textContent(), /\(wrong original\)/);

    // ---- A preview that fails after the admin chose another scope leaves no stale error.
    let answerPreview;
    previewGate = new Promise((resolve) => { answerPreview = resolve; });
    previewFails = true;
    await review(record);
    await button.click();
    await opened();
    await scope("not_replication").check();
    await scope("duplicate").check();
    answerPreview();
    await page.waitForTimeout(300);
    assert.equal(await page.locator("#admin-withdraw-preview").innerHTML(), "");
    await scope("not_replication").check();     // chosen, the error does show
    await page.locator("#admin-withdraw-preview", { hasText: "Lookup failed" }).waitFor();
    await dialogButton("Cancel").click();
    await closed();
    previewGate = null;
    previewFails = false;

    // ---- Withdrawn, the entry leaves the list: Skip must not then act on another.
    listed = [{ ...record, record_id: B, title_r: "Another entry", validation_status: "unvalidated" }];
    withdrawAnswer = [200, { withdrawn: true, record_id: A, scope: "duplicate", withdrawn_records: [A], review_records: [] }];
    await review(record);
    await page.evaluate(([a, b]) => { _adminEntryIds = [a, b]; _adminCurrentIdx = 0; }, [A, B]);
    await button.click();
    await opened();
    await scope("duplicate").check();
    await page.locator("#admin-withdraw-reason").fill("Duplicate");
    await dialogButton("Withdraw").click();
    await page.locator(".not-val-decision").waitFor();
    assert.deepEqual(await page.evaluate(() => [_adminEntryIds, _adminCurrentIdx]), [[B], -1]);

    // ---- Closed while the request ran: the entry is not opened again.
    let release;
    gate = new Promise((resolve) => { release = resolve; });
    await review(record);
    await button.click();
    await opened();
    await scope("duplicate").check();
    await page.locator("#admin-withdraw-reason").fill("Duplicate");
    await dialogButton("Withdraw").click();
    await closed();
    await page.evaluate(() => closeAdminDetail());
    const sent = withdrawals.length;
    release();
    await page.waitForTimeout(500);
    assert.equal(withdrawals.length, sent);
    assert.equal(await page.locator("#admin-detail-modal").isHidden(), true);

    // ---- Moved to another entry meanwhile: that one stays, with what was typed.
    gate = new Promise((resolve) => { release = resolve; });
    await review(record);
    await button.click();
    await opened();
    await scope("duplicate").check();
    await page.locator("#admin-withdraw-reason").fill("Duplicate");
    await dialogButton("Withdraw").click();
    await closed();
    await review({ ...record, record_id: B, title_r: "Another entry", validation_status: "need_review" });
    await page.locator("#admin-note-text").fill("Typed, not saved");
    release();
    await page.waitForTimeout(500);
    assert.equal(await page.locator("#admin-note-text").inputValue(), "Typed, not saved");
    assert.equal(await page.evaluate(() => _adminShownId), B);
    assert.equal(await page.evaluate(() => _adminCurrentIdx), 0, "Skip points at the entry on screen");
    gate = null;
    listed = [];

    // ---- A blocked pair: nothing withdrawn, and the dialog names it.
    withdrawAnswer = [409, { detail: {
      code: "withdraw_blocked_by_siblings",
      message: "1 other pair of this article cannot be withdrawn, so nothing was withdrawn. Deal with it first, or withdraw this pair alone.",
      blocked: [{ record_id: S1, title_o: "Open sibling", doi_o: "10.1/s1", validation_status: "unvalidated",
                  code: "withdraw_blocked_by_added_originals", reason: "This entry has 1 added original, published from it." }],
    } }];
    await review(record);
    await button.click();
    await opened();
    await scope("not_replication").check();
    await page.locator("#admin-withdraw-reason").fill("Not a replication");
    await dialogButton("Withdraw").click();
    await page.locator("#dialog-title", { hasText: "Nothing was withdrawn" }).waitFor();
    const blockedText = await page.locator("#dialog-message").innerText();
    assert.match(blockedText, /cannot be withdrawn, so nothing was withdrawn/);
    assert.match(blockedText, /Open sibling[\s\S]*1 added original/);
    await dialogButton("OK").click();
    await closed();
    assert.equal(await button.isEnabled(), true);

    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(errors, []);
    console.log("withdraw entry UI: all checks passed");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
