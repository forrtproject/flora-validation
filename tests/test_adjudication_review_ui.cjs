/* Run with: node tests/test_adjudication_review_ui.cjs
 * The admins' review of the adjudication (fred-data PR #143), phases 4 and 5:
 * the review list with both judges by name, the review window, approving
 * (prefilled with what the judges agreed on), publishing to FLoRA, withdrawing,
 * changing an answer, a publish the FLoRA build would refuse, and the CSV links;
 * confirmations inside the window, late answers, a slow filter, Escape and focus.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const shots = process.env.FLORA_UI_SCREENSHOTS;
const ID = "11111111-1111-1111-1111-111111111111";

const record = {
  record_id: ID, kind: "different original", status: "awaiting_approval", imported_from: "x",
  doi_r: "10.1234/rep-1", title_r: "Replicating the anchoring effect", year_r: "2019",
  abstract_r: "We replicated it.", abstract_source: "openalex",
  flora: { doi_o: "10.1234/orig-a", title_o: "Anchoring, the original", outcome: "failed",
    outcome_quote: "The effect did not replicate.", quote_source: "abstract" },
  observatory: { doi_o: null, outcome: "failure", replication_type: "direct" },
  original_choices: ["flora", "neither", "cannot_tell"], outcome_choices: [], doi_required: false,
};
const judgements = [
  { handle: "ana", tier: 1, original_choice: "flora", suggested_doi_o: null, outcome: "failed", note: "Checked", points: 15, submitted_at: "2026-10-01T10:00:00Z" },
  { handle: "ben", tier: 2, original_choice: "flora", suggested_doi_o: null, outcome: "failed", note: null, points: 14, submitted_at: "2026-10-01T11:00:00Z" },
];
const outcomes = ["cannot_be_determined", "computational issues, robust", "descriptive only", "failed", "mixed",
  "not_a_replication", "statistically successful but flawed", "successful", "uninformative"];

function detail(status, final = null, extra = {}) {
  return { record: { ...record, status }, judgements, skipped: 0, agreement: "agree", final,
    publish_problem: null, outcomes,
    in_flora: [{ display_id: "REPRO-000009", source: "reproductions", type: "reproduction", doi_o: "10.1234/orig-a", outcome: null, deleted: false }],
    ...extra };
}
function finalOf(body, more = {}) {
  return { record_id: ID, doi_r: record.doi_r, basis: "flora", approved_by: "Hamid",
    approved_at: "2026-10-02T09:00:00Z", published_at: null, published_record_id: null, withdrawn_at: null,
    ...body, ...more };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  page.setDefaultTimeout(10_000);
  const errors = [];
  const posts = [];
  const listQueries = [];
  let current = detail("awaiting_approval");
  let awaiting = null;               // null: follow the record's status
  const slow = {};                   // path -> milliseconds, to hold an answer back
  page.on("pageerror", error => errors.push(error.message));
  const handler = async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = {
      "/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
      "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css",
    };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    const p = url.pathname;
    const wait = slow[p + url.search] || slow[p];
    if (wait) await new Promise(resolve => setTimeout(resolve, wait));
    if (p === "/api/admin/disagreements/status") {
      const st = current.record.status;
      return route.fulfill({ json: { enabled: true, ready: true, error: null, source: "x", source_url: "https://example.org/x.csv",
        counts: { records: 159, open: 150, awaiting_approval: awaiting ?? Number(st === "awaiting_approval"),
          approved: Number(st === "approved"), published: Number(st === "published"), judgements: 20 },
        by_kind: { "different original": 50 } } });
    }
    if (p === "/api/admin/disagreements/records") {
      listQueries.push(url.searchParams.get("status"));
      if (url.searchParams.get("status") === "approved") return route.fulfill({ json: { records: [] } });
      const r = current.record;
      return route.fulfill({ json: { records: [{ record_id: ID, kind: r.kind, doi_r: r.doi_r, title_r: r.title_r,
        status: r.status, final_doi_o: current.final?.doi_o, final_outcome: current.final?.outcome,
        published_record_id: current.final?.published_record_id, withdrawn_at: null,
        judgements: judgements.map(({ handle, tier, original_choice, suggested_doi_o, outcome }) =>
          ({ handle, tier, original_choice, suggested_doi_o, outcome })), agreement: "agree" }] } });
    }
    if (p === `/api/admin/disagreements/records/${ID}`) return route.fulfill({ json: current });
    const action = p.startsWith(`/api/admin/disagreements/records/${ID}/`) && p.split("/").pop();
    if (action) {
      const body = route.request().postDataJSON();
      posts.push({ action, body });
      if (action === "approve") current = detail("approved", finalOf(body));
      if (action === "publish") current = detail("published", finalOf(current.final, { published_record_id: "ADJ-000001", published_by: "Hamid", published_at: "2026-10-02T09:05:00Z" }));
      if (action === "withdraw") current = detail("approved", finalOf(current.final, { withdrawn_at: "2026-10-02T09:10:00Z", withdrawn_by: "Hamid" }));
      if (action === "undo") current = detail("awaiting_approval");
      return route.fulfill({ json: current });
    }
    return route.fulfill({ json: { records: [], entries: [], total: 0, runs: [], counts: {} } });
  };
  await page.route("**/*", handler);

  // Confirmations are inside the review window (app.js's dialog hides its
  // Cancel button on phones).
  const confirm = async () => {
    await page.locator('#adj-decision-area [data-action="confirm"]').click();
  };

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      window.Adjudication.initAdmin();
      switchAdminTab("disagreements");
    });

    // The review card: filters with counts, both judges by name, the CSV links.
    const list = page.locator("#adj-records-list");
    await list.locator("table").waitFor();
    assert.deepEqual(listQueries, ["awaiting_approval"]);
    assert.deepEqual((await page.locator(".adj-filter").allInnerTexts()).map(t => t.replace(/\s+/g, " ").trim()),
      ["Awaiting approval 1", "Approved 0", "In FLoRA 0", "Waiting for judgements 150", "All 159"]);
    assert.match(await list.innerText(), /Agree[\s\S]*ana: FLoRA's original · Failed[\s\S]*ben: FLoRA's original · Failed/);
    assert.equal(await page.locator('.adj-download').nth(0).getAttribute("href"), "/api/admin/disagreements/export/final.csv");
    assert.equal(await page.locator('.adj-download').nth(1).getAttribute("href"), "/api/admin/disagreements/export/judgements.csv");
    // A slow filter answered after a newer one does not replace it.
    slow["/api/admin/disagreements/records?status=approved"] = 600;
    await page.locator('.adj-filter[data-filter="approved"]').click();
    await page.locator('.adj-filter[data-filter=""]').click();
    await page.waitForFunction(() => document.querySelector('.adj-filter[data-filter=""]').getAttribute("aria-pressed") === "true");
    await new Promise(resolve => setTimeout(resolve, 900));
    // Both were asked (the slow one is logged when it answers, so last).
    assert.ok(listQueries.slice(-2).includes("approved") && listQueries.slice(-2).includes(null));
    assert.equal(await list.locator("table").count(), 1, "the newer filter's list stays");
    delete slow["/api/admin/disagreements/records?status=approved"];
    if (shots) {
      await fs.mkdir(shots, { recursive: true });
      await page.locator("#admin-tab-disagreements").screenshot({ path: path.join(shots, "review-list.png") });
    }

    // A record's answer that arrives after its window was closed is dropped.
    slow[`/api/admin/disagreements/records/${ID}`] = 500;
    await list.locator("[data-review]").click();
    await page.locator("#adj-review-close").click();
    await new Promise(resolve => setTimeout(resolve, 700));
    assert.equal(await page.locator("#adj-review-modal").isHidden(), true);
    delete slow[`/api/admin/disagreements/records/${ID}`];

    // The review window, prefilled with what both judges said.
    const importCard = await page.locator(".adj-import").elementHandle();
    await list.locator("[data-review]").click();
    const body = page.locator("#adj-review-body");
    await body.locator("#adj-decision").waitFor();
    // Escape works after opening with the mouse, and focus goes back to "Review".
    await page.keyboard.press("Escape");
    await page.locator("#adj-review-modal").waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.review), ID);
    assert.equal(await importCard.evaluate(el => el.isConnected), true, "closing keeps the tab as it was");
    await list.locator("[data-review]").click();
    await body.locator("#adj-decision").waitFor();
    assert.equal(await body.locator("[data-suggest]").count(), 0, "admins are not asked to suggest");
    assert.match(await body.locator(".adj-in-flora").innerText(), /already in Source Records[\s\S]*REPRO-000009/);
    assert.match(await body.locator(".adj-judgements").innerText(), /ana[\s\S]*Trusted[\s\S]*Checked[\s\S]*ben[\s\S]*Senior/);
    assert.equal(await body.locator('input[name="adj-doi-choice"][value="10.1234/orig-a"]').isChecked(), true);
    assert.equal(await page.locator("#adj-outcome").inputValue(), "failed");
    assert.equal(await page.locator("#adj-title-o").inputValue(), "Anchoring, the original");
    assert.equal(await page.locator("#adj-quote").inputValue(), "The effect did not replicate.");
    assert.equal(await page.locator("#adj-quote-source").inputValue(), "abstract");
    // Typing another DOI selects that option and clears the side's title.
    await page.locator("#adj-other-doi").click();
    assert.equal(await body.locator('input[name="adj-doi-choice"][value="other"]').isChecked(), true);
    assert.equal(await page.locator("#adj-title-o").inputValue(), "");
    await page.locator("#adj-approve-btn").click();
    assert.match(await page.locator("#adj-review-error").innerText(), /Type the DOI/);
    await body.locator('input[name="adj-doi-choice"][value="10.1234/orig-a"]').check();
    assert.equal(await page.locator("#adj-title-o").inputValue(), "Anchoring, the original");
    await page.locator("#adj-admin-note").fill("Both judges agree.");
    if (shots) await page.screenshot({ path: path.join(shots, "review-approve.png") });
    await page.locator("#adj-approve-btn").click();

    await body.locator('[data-action="publish"]').waitFor();
    assert.deepEqual(posts[0], { action: "approve", body: { doi_o: "10.1234/orig-a", title_o: "Anchoring, the original",
      outcome: "failed", outcome_quote: "The effect did not replicate.", quote_source: "abstract", admin_note: "Both judges agree." } });
    assert.match(await body.locator(".adj-final").innerText(), /Failed[\s\S]*FLoRA's answer[\s\S]*Both judges agree\.[\s\S]*by Hamid/);

    // Change, then cancel: back to the approved answer.
    await body.locator('[data-action="edit"]').click();
    assert.equal(await page.locator("#adj-approve-btn").textContent(), "Save the answer");
    await body.locator('[data-action="cancel-edit"]').click();

    // A cancelled confirmation does nothing.
    let before = posts.length;
    await body.locator('[data-action="publish"]').click();
    assert.match(await body.locator(".adj-confirm").innerText(), /Add this answer to FLoRA\?/);
    await body.locator('[data-action="cancel-confirm"]').click();
    assert.equal(posts.length, before);
    assert.equal(await body.locator(".adj-confirm").count(), 0);

    // Publish, after a confirmation; nothing else can start while it is on its way.
    slow[`/api/admin/disagreements/records/${ID}/publish`] = 500;
    await body.locator('[data-action="publish"]').click();
    await confirm();
    assert.equal(await body.locator('[data-action="confirm"]').isDisabled(), true);
    assert.equal(await body.locator('[data-action="cancel-confirm"]').isDisabled(), true);
    await body.locator(".is-published").waitFor();
    delete slow[`/api/admin/disagreements/records/${ID}/publish`];
    assert.equal(posts.at(-1).action, "publish");
    assert.match(await body.locator(".is-published").innerText(), /In FLoRA as ADJ-000001/);
    assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden", "the page stays locked behind the window");
    if (shots) await page.screenshot({ path: path.join(shots, "review-published.png") });

    // Withdraw, after a confirmation: approved again, with a note. An answer that
    // has been in FLoRA can be changed but not undone.
    await body.locator('[data-action="withdraw"]').click();
    await confirm();
    await body.locator('[data-action="publish"]').waitFor();
    assert.match(await body.innerText(), /Withdrawn from FLoRA by Hamid; publishing again brings back ADJ-000001/);
    assert.equal(await body.locator('[data-action="undo"]').count(), 0);

    // The status card follows, in place, when the window closes.
    await page.locator("#adj-review-close").click();
    await page.waitForFunction(() => document.querySelector('[data-stat="approved"]').textContent === "1");
    assert.equal(await importCard.evaluate(el => el.isConnected), true);

    // A pair FLoRA already holds is pointed out before publishing.
    current = detail("approved", finalOf({ doi_o: "10.1234/orig-a", outcome: "failed" }),
      { pair_conflicts: [{ display_id: "VAL-000004", source: "validated", type: "replication", doi_o: "10.1234/orig-a" }] });
    await list.locator("[data-review]").click();
    await body.locator(".adj-problem").waitFor();
    assert.match(await body.locator(".adj-problem").innerText(),
      /already holds this replication–original pair as VAL-000004 \(validated\)[\s\S]*rule one of them a duplicate/);
    assert.equal(await body.locator('[data-action="publish"]').isEnabled(), true);
    await page.locator("#adj-review-close").click();

    // While a record waits for its second judgement, the first answer is hidden.
    current = { ...detail("open"), agreement: null,
      judgements: [{ handle: "ana", tier: 1, state: "submitted", submitted_at: "2026-10-01T10:00:00Z" }] };
    await list.locator("[data-review]").click();
    await body.locator(".adj-decision-note").waitFor();
    assert.match(await body.locator(".adj-judgements").innerText(), /ana[\s\S]*Hidden until both judgements are in/);
    await page.locator("#adj-review-close").click();

    // An answer the FLoRA build would refuse cannot be published, and says why.
    current = detail("approved", finalOf({ doi_o: "10.1234/orig-a", outcome: "cannot_be_determined" }),
      { publish_problem: "FLoRA's build does not accept the outcome \"cannot_be_determined\"" });
    await list.locator("[data-review]").click();
    await body.locator(".adj-problem").waitFor();
    assert.equal(await body.locator('[data-action="publish"]').isDisabled(), true);
    assert.match(await body.locator(".adj-problem").innerText(), /does not accept the outcome/);

    // A refused action shows the server's reason.
    await page.route(`**/api/admin/disagreements/records/${ID}/undo`, route =>
      route.fulfill({ status: 409, json: { detail: "Only an approved answer that is not in FLoRA can be undone" } }));
    await body.locator('[data-action="undo"]').click();
    await confirm();
    await page.locator("#adj-review-error:not(:empty)").waitFor();
    assert.match(await page.locator("#adj-review-error").innerText(), /can be undone/);
    assert.deepEqual(errors, []);

    // A phone: the decision form fits without sideways scrolling, long DOIs included.
    await page.setViewportSize({ width: 390, height: 844 });
    current = detail("awaiting_approval", null, { judgements: [
      { ...judgements[0], original_choice: "neither", suggested_doi_o: "10.1234/" + "a-very-long-suggested-doi-".repeat(3) },
      judgements[1]] });
    await page.locator("#adj-review-close").click();
    await list.locator("[data-review]").click();
    await body.locator("#adj-decision").waitFor();
    assert.ok(await page.evaluate(() => {
      const panel = document.querySelector("#adj-review-modal .faq-panel");
      const bodyEl = document.querySelector("#adj-review-body");
      return panel.getBoundingClientRect().right <= window.innerWidth + 1 && bodyEl.scrollWidth <= bodyEl.clientWidth + 1;
    }), "nothing in the window is wider than the screen");
    if (shots) await page.screenshot({ path: path.join(shots, "review-phone.png") });

    // Nothing awaiting approval: the list opens on everything instead of an empty filter.
    awaiting = 0;
    const fresh = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
    fresh.on("pageerror", error => errors.push(error.message));
    await fresh.route("**/*", handler);
    await fresh.goto("http://flora.test/");
    listQueries.length = 0;
    await fresh.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      window.Adjudication.initAdmin();
      switchAdminTab("disagreements");
    });
    await fresh.locator("#adj-records-list table").waitFor();
    assert.deepEqual(listQueries, [null]);
    assert.equal(await fresh.locator('.adj-filter[data-filter=""]').getAttribute("aria-pressed"), "true");
    assert.deepEqual(errors, []);
    console.log("adjudication review UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
