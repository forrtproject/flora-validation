/* Run with: node tests/test_approval_view_ui.cjs
 * The Pending Approval view: lazy panels, the gates, every server refusal, races,
 * keeping typed notes through refreshes, what opens next, the head and pagination,
 * and the link to a row from the other filters.
 * All requests are intercepted; no server, database, or remote service runs. The
 * server's refusals here are the mock's: tests/test_approval_preview_parity.py ties
 * what the panel says approving publishes to consensus_engine.approval_values.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const XSS = '<img src=x onerror="window.__xss=1">';
const id = (n) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const PLAIN = id(1), SELF = id(2), SUGGEST = id(3), DISSENT = id(4), PAPER = id(5),
      DECIDED = id(6), TIE = id(7), DUP = id(8), MOVES = id(9), LAST = id(10);

const human = (name, vid, extra = {}) => ({
  validator_id: vid, validator_name: name, validator_tier: 0, type_check: "correct",
  original_check: "correct", outcome_check: "correct", validated_at: "2026-10-01T10:00:00Z", ...extra,
});
const agreeingLlm = { type_check: "correct", original_check: "correct", outcome_check: "correct", context: "sanity_check", model: "m1" };
const approvedBy = (admin) => [{ action: "approved", admin_handle: admin, decided_at: "2026-11-03T10:00:00Z", changes: {} }];

function baseRecord(rid, over = {}) {
  const n = rid.slice(0, 8).replace(/^0+/, "");
  return {
    record_id: rid, origin: "extractor", validation_status: "consensus_reached", admin_checked: false,
    type: "replication", final_type: "replication", outcome: "failed", final_outcome: "failed",
    title_r: `Replication ${n}`, final_title_r: `Replication ${n}`,
    doi_r: `10.5555/r${n}`, study_r: "1", year_r: "2020", url_r: "https://example.test/r",
    final_url_r: "https://example.test/r", abstract_r: "An abstract.", title_o: "The original",
    final_title_o: "The original", doi_o: "10.5555/o", final_doi_o: "10.5555/o",
    outcome_quote: "It failed.", final_outcome_quote: "It failed.", out_quote_source: "abstract",
    final_out_quote_source: "abstract", validator_1: human("ana", 11), validator_2: human("ben", 12),
    llm_validator: agreeingLlm, quality_flags: [], admin_notes: null, note_saved_by: null, note_saved_at: null,
    is_tiebreaker: false, ...over,
  };
}
function rowOf(rec, extra = {}) {
  return {
    record_id: rec.record_id, title_r: rec.title_r, final_title_r: rec.final_title_r, doi_r: rec.doi_r,
    study_r: rec.study_r, type: rec.type, final_type: rec.final_type, outcome: rec.outcome,
    final_outcome: rec.final_outcome, validation_status: rec.validation_status,
    has_v1: !!rec.validator_1, has_v2: !!rec.validator_2, has_llm: !!rec.llm_validator,
    v1_handle: rec.validator_1?.validator_name, v2_handle: rec.validator_2?.validator_name,
    agreement_pct: 100, is_tiebreaker: rec.is_tiebreaker, quality_flag_count: (rec.quality_flags || []).length,
    effective_title_o: rec.final_title_o, effective_doi_o: rec.final_doi_o, admin_notes: rec.admin_notes,
    note_saved_by: rec.note_saved_by, open_extra_suggestions: 0,
    trusted_validator_count: 0, mine: false, skip_event_count: 0, skip_validator_count: 0,
    skip_issue_validator_count: 0, skip_review_required: false, ...extra,
  };
}

function freshDb() {
  const db = new Map();
  const put = (rid, recOver, rowOver = {}, extra = {}) => {
    const record = baseRecord(rid, recOver);
    db.set(rid, { record, row: rowOf(record, rowOver), self: { linked: true, mine: false, allowed: true, reason: null },
                  summary: { open_suggestions: 0, children: 0 }, decisions: [], paper: [], ...extra });
  };
  put(PLAIN, {});
  put(SELF, { is_tiebreaker: true, validator_2: human("me", 99, { outcome_check: "incorrect", corrected_outcome: "successful" }) },
      { mine: true, is_tiebreaker: true, agreement_pct: 67, v2_handle: "me" },
      { self: { linked: true, mine: true, allowed: false, reason: "You validated this entry, and the other validator did not agree with you (or nobody else validated it), so another admin needs to decide it." } });
  put(SUGGEST, {}, { open_extra_suggestions: 1 }, { summary: { open_suggestions: 1, children: 0 } });
  put(DISSENT, { llm_validator: { ...agreeingLlm, outcome_check: "incorrect", corrected_outcome: "mixed" } }, { llm_dissent: "outcome" });
  put(PAPER, {}, {}, { paper: [
    { record_id: PAPER, is_current: true, title_o: "First original", final_title_o: null, authors_o: "Ann, A.; Bo, B.", year_o: "2001",
      doi_o: "10.5555/o1", final_doi_o: null, validation_status: "consensus_reached", outcome: "failed", final_outcome: "failed",
      original_checks: ["correct", "correct"], outcome_checks: ["correct", "correct"], shared_check: true },
    { record_id: PLAIN, is_current: false, title_o: "Second original " + XSS, final_title_o: null, authors_o: "Cy, C.", year_o: "2002",
      doi_o: "10.5555/o2", final_doi_o: null, validation_status: "validated", outcome: "successful", final_outcome: "successful",
      original_checks: ["correct", "incorrect"], outcome_checks: ["not_applicable", "correct"], shared_check: false },
  ] });
  put(DECIDED, {});
  put(TIE, { is_tiebreaker: true, validator_2: human("ben", 12, { outcome_check: "incorrect", corrected_outcome: "successful", doi_r_published: "10.5555/pub" }) },
      { is_tiebreaker: true, agreement_pct: 67 });
  put(DUP, { title_r: "Title " + XSS, final_title_r: "Title " + XSS, admin_notes: "Note " + XSS, note_saved_by: "cy" + XSS }, {}, {
    // A plain 409, as admin_approve raises it for a duplicate of a validated entry.
    approve: [409, { detail: "This entry matches one that is already validated. Open it and use Mark as Resolved to merge the two." }],
  });
  put(MOVES, {});
  put(LAST, {});
  return db;
}

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  const calls = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });

  let db = freshDb();
  let failNext = null;     // (method, path) → true: abort that request
  let holdList = null;     // a promise the next entry-list answer waits for
  let holdNote = null;     // the same for the next note POST
  let holdXo = null;       // and for the next read of an entry's suggestions

  const FILES = {
    "/": ["text/html", "index.html"],
    "/app.js": ["text/javascript", "app.js"],
    "/style.css": ["text/css", "style.css"],
    "/adjudication.js": ["text/javascript", "adjudication.js"],
    "/adjudication.css": ["text/css", "adjudication.css"],
  };
  const listAnswer = (url) => {
    const filter = url.searchParams.get("filter");
    const hideMine = url.searchParams.get("hide_mine") === "true";
    const pageNo = Number(url.searchParams.get("page") || 1);
    const per = Number(url.searchParams.get("per_page") || 50);
    const all = [...db.values()];
    const live = all.filter((d) => d.row.listed !== false);
    const waiting = live.filter((d) => d.row.validation_status === "consensus_reached");
    const inFilter = (filter === "pending_approval" ? waiting
      : filter === "extra_originals" ? live.filter((d) => d.row.open_extra_suggestions)
      : live).filter((d) => !(hideMine && d.row.mine));
    const start = (pageNo - 1) * per;
    return {
      entries: inFilter.slice(start, start + per).map((d) => d.row), total: inFilter.length, page: pageNo,
      per_page: per, viewer_linked: true,
      counts: { all: all.length, pending_approval: waiting.length,
                needs_review: all.filter((d) => d.record.validation_status === "need_review").length,
                validated: all.filter((d) => d.record.validation_status === "validated").length },
    };
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    if (FILES[url.pathname]) {
      const [contentType, file] = FILES[url.pathname];
      return route.fulfill({ status: 200, contentType, body: await fs.readFile(path.join(root, "docs", file)) });
    }
    const p = url.pathname;
    if (!p.startsWith("/api/")) return route.fulfill({ status: 200, body: "" });
    if (p === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    if (!p.startsWith("/api/admin/")) return route.fulfill({ json: {} });
    const method = request.method();
    calls.push({ method, path: p.replace("/api/admin", ""), search: url.search, body: request.postData() });
    if (failNext?.(method, p)) { failNext = null; return route.abort("failed"); }

    if (p === "/api/admin/entries") {
      if (holdList) { const h = holdList; holdList = null; await h; }
      return route.fulfill({ json: listAnswer(url) });
    }
    const m = p.match(/^\/api\/admin\/entries\/([^/]+)(?:\/(.+))?$/);
    if (!m) return route.fulfill({ json: { records: [], entries: [], counts: {}, total: 0 } });
    const d = db.get(m[1]);
    if (!d) return route.fulfill({ status: 404, json: { detail: "Record not found" } });
    const tail = m[2];
    if (!tail && method === "GET") {
      d.row.validation_status = d.record.validation_status;     // the list catches up
      return route.fulfill({ json: { record: d.record, queue_slots: [], self_approval: d.self, decisions: d.decisions,
        abstract_only_conflict: false, original_removal: false, paper_originals: d.paper, paper_differs_on: d.differs,
        validator_stats: { 11: { judged: 40, flags: 1, approved: 30 } }, duplicate_merge: null,
        skip_history: [], skip_summary: {}, submission_failure_history: [], extra_originals_summary: d.summary } });
    }
    if (tail === "extra-originals") {
      if (holdXo) { const h = holdXo; holdXo = null; await h; }
      return route.fulfill({ json: { parent: { record_id: m[1], mode: "draft" }, children: [],
        suggestions: [{ key: "k", doi_o: "10.5555/s", url_o: "", title_o: "A suggested original " + XSS, study_o: "2", notes: ["n " + XSS],
                        open_suggestion_ids: [7], status: "open", suggested_by: [{ suggestion_id: 7, validator_name: "ana" + XSS, status: "open" }] }] } });
    }
    if (tail === "note") {
      if (holdNote) { const h = holdNote; holdNote = null; await h; }
      d.record.admin_notes = JSON.parse(request.postData()).note;
      d.record.note_saved_by = "me";
      d.row.admin_notes = d.record.admin_notes;
      return route.fulfill({ json: { saved: true } });
    }
    if (tail === "approve") {
      if (d.record.validation_status !== "consensus_reached") return route.fulfill({ status: 404, json: { detail: "Record not found or not awaiting approval" } });
      if (!d.self.allowed) return route.fulfill({ status: 403, json: { detail: d.self.reason } });
      if (d.approve) return route.fulfill({ status: d.approve[0], json: d.approve[1] });
      d.record.validation_status = "validated";
      d.row.validation_status = "validated";
      return route.fulfill({ json: { approved: true, record_id: m[1] } });
    }
    if (tail === "flag-review") {
      d.record.validation_status = "need_review";
      d.row.validation_status = "need_review";
      return route.fulfill({ json: { flagged: true, record_id: m[1] } });
    }
    return route.fulfill({ json: {} });
  });

  const row = (rid) => page.locator(`tr.ap-row[data-id="${rid}"]`);
  const panel = (rid) => page.locator(`#ap-panel-${rid}`);
  const btn = (rid, action) => panel(rid).locator(`[data-ap="${action}"]`);
  const toggle = (rid) => row(rid).locator(".ap-toggle");
  const posts = (tail) => calls.filter((c) => c.method === "POST" && c.path.endsWith(tail));
  const gets = (p) => calls.filter((c) => c.method === "GET" && c.path === p).length;
  const ready = (rid) => panel(rid).locator(".ap-buttons").first().waitFor();
  const quiet = () => page.waitForTimeout(60);
  const pastDoubleClick = () => page.waitForTimeout(750);
  const openRow = async (rid) => { await toggle(rid).click(); await ready(rid); };
  const filterTo = (name) => page.evaluate((f) => document.querySelector(`#admin-filters [data-filter="${f}"]`).click(), name);
  const pending = async () => {
    await filterTo("pending_approval");
    await page.locator("tr.ap-row").first().waitFor();
  };
  const fresh = async () => {
    db = freshDb();
    await filterTo("all");
    await page.locator("#admin-table-body .admin-review-btn").first().waitFor();
    await pending();
    await page.evaluate(() => {
      _apLastDecisionAt = 0;                                 // the double-click guard
      Object.keys(_apNoteDrafts).forEach((k) => delete _apNoteDrafts[k]);   // these outlive a list
    });
    calls.length = 0;
  };
  const addRows = async (from, to, rowOver = {}) => {
    for (let n = from; n <= to; n++) {
      const rec = baseRecord(id(n));
      db.set(id(n), { record: rec, row: rowOf(rec, rowOver), self: { allowed: true }, summary: {}, decisions: [], paper: [] });
    }
  };

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      _adminHandle = "me";
      fetchAdminEntries();
    });
    await page.locator("#admin-table-body .admin-review-btn").first().waitFor();

    // ---- Every other filter is the table it was, with a link in place of Approve.
    assert.equal(await page.locator("#admin-entries-head th").count(), 11);
    assert.equal(await page.locator("#admin-table-body .admin-approve-btn").count(), 0);
    assert.equal(await page.locator("#admin-table-body .admin-to-pending").count() > 0, true);
    assert.equal(await page.evaluate(() => typeof quickApprove), "undefined");

    // ---- A sort this view has no column for is dropped on entering it.
    await page.evaluate(() => document.querySelector('#admin-entries-head [data-sort="status"]').click());
    await quiet();
    calls.length = 0;
    await pending();
    assert.doesNotMatch(calls.find((c) => c.path === "/entries").search, /sort=/);

    // ---- Collapsed: its own head, no Approve, no fetching ahead of the admin.
    assert.equal(await page.locator("#admin-entries-head th").count(), 8);
    assert.deepEqual((await page.locator("#admin-entries-head th").allTextContents()).map((s) => s.trim()),
      ["#", "Study", "Type", "Outcome", "Validators", "Agreement", "Needs a look", "Full review"]);
    assert.equal(await page.locator("#admin-table-body .admin-approve-btn").count(), 0);
    assert.equal(calls.filter((c) => /^\/entries\/./.test(c.path)).length, 0, "nothing preloaded");
    assert.equal(await toggle(PLAIN).getAttribute("aria-expanded"), "false");
    assert.match(await row(DISSENT).textContent(), /LLM ✗ outcome/);
    assert.match(await row(TIE).textContent(), /TIE/);
    assert.match(await row(SELF).textContent(), /YOU/);
    assert.match(await row(SUGGEST).textContent(), /\+1 suggested/);
    assert.doesNotMatch(await row(PAPER).textContent(), /check set|originals/);

    // ---- Lazy: the first expand reads the entry, once, and nothing it does not need.
    calls.length = 0;
    await openRow(PLAIN);
    assert.equal(await toggle(PLAIN).getAttribute("aria-expanded"), "true");
    assert.equal(await toggle(PLAIN).getAttribute("aria-controls"), `ap-panel-${PLAIN}`);
    assert.equal(gets(`/entries/${PLAIN}`), 1);
    assert.equal(gets(`/entries/${PLAIN}/extra-originals`), 0);
    assert.equal(await panel(PLAIN).locator(".admin-paper-set").count(), 0, "no list, no section");
    assert.match(await panel(PLAIN).textContent(), /what gets published to FLoRA if approved/);
    await toggle(PLAIN).click();
    assert.equal(await panel(PLAIN).isHidden(), true);
    assert.equal(await toggle(PLAIN).getAttribute("aria-expanded"), "false");
    await toggle(PLAIN).click();
    assert.equal(gets(`/entries/${PLAIN}`), 1, "reopening does not re-read");

    // ---- Plain approve: re-read, POST with no body, next row opens with focus on its toggle.
    calls.length = 0;
    const waiting = Number(await page.locator("#fc-pending-approval").textContent());
    await btn(PLAIN, "approve").click();
    await row(PLAIN).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);
    assert.equal(posts("/approve")[0].body, null);
    assert.equal(gets(`/entries/${PLAIN}`), 1, "read again before approving");
    await ready(SELF);
    assert.equal(await page.evaluate(() => document.activeElement.className), "ap-toggle");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), SELF);
    await quiet();
    assert.equal(await page.locator("#fc-pending-approval").textContent(), String(waiting - 1));

    // ---- Self-approval: Approve and Flag off with the reason; the note and full review stay.
    assert.equal(await btn(SELF, "approve").isDisabled(), true);
    assert.equal(await btn(SELF, "flag").isDisabled(), true);
    assert.equal(await btn(SELF, "note").isDisabled(), false);
    assert.equal(await btn(SELF, "full").isDisabled(), false);
    assert.match(await panel(SELF).locator(".ap-block-list").textContent(), /another admin needs to decide/);
    assert.match(await panel(SELF).locator(".ap-compare .ap-diff").first().textContent(), /differs/);
    assert.match(await row(SELF).textContent(), /not your decision/);
    await page.evaluate(() => { _apLastDecisionAt = 0; });   // or the double-click guard answers instead
    await btn(SELF, "approve").evaluate((b) => { b.disabled = false; b.click(); });
    await btn(SELF, "flag").evaluate((b) => { b.disabled = false; b.click(); });
    await quiet();
    assert.equal(posts("/approve").length, 1, "a forced click still sends nothing");
    assert.equal(posts("/flag-review").length, 0, "nor on Flag, which the server would accept");

    // ---- A warning asks once more; a double click cannot confirm it; Go back and Esc undo it.
    await fresh();
    await openRow(DISSENT);
    assert.match(await panel(DISSENT).locator(".ap-flags").textContent(), /LLM disagrees with both validators on: outcome/);
    assert.match(await panel(DISSENT).locator(".ap-llm-off").textContent(), /dissents/);
    await btn(DISSENT, "approve").click();
    await btn(DISSENT, "confirm").evaluate((b) => b.click());     // the double click's second half
    await quiet();
    assert.equal(posts("/approve").length, 0);
    await btn(DISSENT, "cancel").click();
    await btn(DISSENT, "approve").click();
    await page.keyboard.press("Escape");
    assert.equal(await btn(DISSENT, "approve").isVisible(), true);
    assert.equal(await btn(DISSENT, "confirm").count(), 0);
    await pastDoubleClick();
    await btn(DISSENT, "approve").click();
    await pastDoubleClick();
    await btn(DISSENT, "confirm").click();
    await row(DISSENT).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);

    // ---- A confirm pressed straight after another decision waits, and can be pressed again.
    await fresh();
    await openRow(DISSENT);
    await btn(DISSENT, "approve").click();
    await panel(DISSENT).locator(".ap-confirm").waitFor();
    await pastDoubleClick();
    await page.evaluate(() => { _apLastDecisionAt = Date.now() + 60_000; });
    await btn(DISSENT, "confirm").click();
    await quiet();
    assert.equal(posts("/approve").length, 0);
    await page.evaluate(() => { _apLastDecisionAt = 0; });
    await btn(DISSENT, "confirm").click();
    await row(DISSENT).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);

    // ---- A changed entry is drawn before its suggestions are read; the notice stays over the confirm.
    await fresh();
    await openRow(SUGGEST);
    await btn(SUGGEST, "approve").click();
    await panel(SUGGEST).locator(".ap-confirm").waitFor();
    await pastDoubleClick();
    db.get(SUGGEST).record.final_title_r = "Changed meanwhile";
    let releaseXo;
    holdXo = new Promise((r) => { releaseXo = r; });
    await btn(SUGGEST, "confirm").click();
    await panel(SUGGEST).locator(".ap-notice").waitFor();
    assert.match(await panel(SUGGEST).locator(".ap-body").textContent(), /Changed meanwhile/);
    assert.equal(posts("/approve").length, 0);
    releaseXo();
    await quiet();
    await btn(SUGGEST, "approve").click();
    await panel(SUGGEST).locator(".ap-confirm").waitFor();
    assert.equal(await panel(SUGGEST).locator(".ap-notice").count(), 1);

    // ---- Every kind of warning takes that second press, whatever else is on the entry.
    await fresh();
    Object.assign(db.get(PLAIN).record, { quality_flags: [{ code: "year", label: "Year looks wrong" }] });
    Object.assign(db.get(MOVES).record, { llm_validator: null });
    Object.assign(db.get(LAST).record, { llm_validator: { error: "timeout", context: "sanity_check" } });
    Object.assign(db.get(DECIDED).record, { validator_1: human("ana", 11, { additional_checks: { quote_not_in_abstract: true } }) });
    Object.assign(db.get(DECIDED).row, { skip_review_required: true, skip_validator_count: 6 });
    for (const rid of [PLAIN, MOVES, LAST, DECIDED, TIE, SUGGEST]) {
      await openRow(rid);
      await btn(rid, "approve").click();
      await btn(rid, "confirm").waitFor();
      await btn(rid, "cancel").click();
    }
    assert.equal(posts("/approve").length, 0);

    // ---- Suggestions: shown, escaped, read-only, and the toast says where they went.
    await fresh();
    await openRow(SUGGEST);
    assert.match(await panel(SUGGEST).textContent(), /A suggested original/);
    assert.equal(await panel(SUGGEST).locator("[data-xo-use], [data-xo-dismiss]").count(), 0);
    await btn(SUGGEST, "approve").click();
    await pastDoubleClick();
    await btn(SUGGEST, "confirm").click();
    await page.locator("#toast").filter({ hasText: "under Suggested originals" }).waitFor({ state: "attached" });

    // ---- The paper's originals: the full screen's columns, read-only, with an open action.
    await fresh();
    await openRow(PAPER);
    const paperPanel = panel(PAPER).locator(".admin-paper-set");
    assert.match(await paperPanel.textContent(), /This paper has 2 coded originals · 1 awaiting approval/);
    assert.deepEqual(await paperPanel.locator("thead th").allTextContents(),
      ["Original", "Status", "Right paper?", "Outcome check", "Outcome", ""]);
    assert.match(await paperPanel.locator("tbody tr").nth(0).textContent(), /First original[\s\S]*Ann, A\.; Bo, B\. · 2001[\s\S]*consensus reached[\s\S]*✓ ✓[\s\S]*shared[\s\S]*this entry/);
    assert.match(await paperPanel.locator("tbody tr").nth(1).textContent(), /<img src=x[\s\S]*validated[\s\S]*✓ ✗[\s\S]*– ✓[\s\S]*successful[\s\S]*open/);
    assert.doesNotMatch(await paperPanel.locator("tbody tr").nth(1).textContent(), /shared/);
    assert.equal(await page.locator("#admin-approve-paper-btn").count(), 0, "no dead button in the panel");
    assert.equal(await panel(PAPER).locator("[data-paper-open]").count(), 0);
    assert.equal(calls.filter((c) => /replication-set/.test(c.path)).length, 0);
    await paperPanel.locator('[data-ap="open"]').click();
    await page.locator("#admin-detail-modal:not(.hidden)").waitFor();
    await page.locator("#admin-detail-body .final-preview-card").waitFor();
    assert.equal(gets(`/entries/${PLAIN}`), 1, "open goes to that entry's own review");
    assert.match(await page.locator("#admin-detail-body").textContent(), /Replication 1/);
    await page.evaluate(() => closeAdminDetail());
    await quiet();
    assert.equal(await panel(PAPER).isVisible(), true, "coming back keeps the open row");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), PAPER, "and returns to it");

    // ---- Two entries of one paper can be open together: no id appears twice.
    await fresh();
    db.get(MOVES).paper = db.get(PAPER).paper.map((o) => ({ ...o, is_current: false }));
    await openRow(PAPER);
    await openRow(MOVES);
    assert.deepEqual(await page.evaluate(() => {
      const ids = [...document.querySelectorAll("#admin-table-body [id]")].map((el) => el.id);
      return ids.filter((x, i) => ids.indexOf(x) !== i);
    }), []);

    // ---- A sibling that moved on after the panel opened counts as a change to this entry.
    await fresh();
    await openRow(PAPER);
    db.get(PAPER).paper[1].validation_status = "need_review";
    await btn(PAPER, "approve").click();
    await panel(PAPER).locator(".ap-notice").waitFor();
    assert.equal(posts("/approve").length, 0);
    assert.match(await panel(PAPER).locator(".admin-paper-set").textContent(), /need review/);
    await pastDoubleClick();
    await btn(PAPER, "approve").click();
    await row(PAPER).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1, "and the next press decides on what was redrawn");

    // ---- Another of its originals approved is the ordinary way through a paper: no stop.
    await fresh();
    db.get(PAPER).paper[1].validation_status = "consensus_reached";
    await openRow(PAPER);
    db.get(PAPER).paper[1].validation_status = "validated";
    await btn(PAPER, "approve").click();
    await row(PAPER).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);

    // ---- Originals that would publish the paper two ways: seen, a second press, never a block.
    await fresh();
    db.get(PAPER).differs = ["url_r", "abstract_r"];
    await openRow(PAPER);
    const twoWays = "These originals would publish the paper two ways: they differ on the link and abstract.";
    assert.equal(await panel(PAPER).locator(".paper-differs").textContent(), twoWays);
    assert.equal(await panel(PAPER).locator(".paper-differs + .admin-paper-set").count(), 1, "right above the originals");
    assert.equal(await panel(PAPER).locator(".ap-flags .ap-warn-list").textContent(), twoWays);
    assert.match(await panel(PAPER).locator(".ap-buttons").textContent(), /1 to check first/);
    assert.equal(await btn(PAPER, "approve").isDisabled(), false, "it does not stop the approval");
    await btn(PAPER, "approve").click();
    await btn(PAPER, "confirm").waitFor();
    assert.match(await panel(PAPER).locator(".ap-confirm").textContent(), /would publish the paper two ways/);
    await quiet();
    assert.equal(posts("/approve").length, 0);
    await pastDoubleClick();
    await btn(PAPER, "confirm").click();
    await row(PAPER).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);

    // ---- It names only what differs, and it is escaped.
    await fresh();
    db.get(PAPER).differs = ["abstract_r"];
    db.get(MOVES).paper = db.get(PAPER).paper;
    db.get(MOVES).differs = [XSS];
    await openRow(PAPER);
    await openRow(MOVES);
    assert.equal(await panel(PAPER).locator(".paper-differs").textContent(),
                 "These originals would publish the paper two ways: they differ on the abstract.");
    assert.match(await panel(MOVES).locator(".paper-differs").textContent(), /differ on the <img src=x/);
    assert.equal(await panel(MOVES).locator(".paper-differs img, .ap-flags img").count(), 0);
    await btn(MOVES, "approve").click();
    await btn(MOVES, "confirm").waitFor();
    assert.equal(await panel(MOVES).locator(".ap-confirm img").count(), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);

    // ---- A new difference while the panel is open stops the decision; the next press asks.
    await fresh();
    await openRow(PAPER);
    assert.equal(await panel(PAPER).locator(".paper-differs").count(), 0, "no key in the payload, no warning");
    db.get(PAPER).differs = ["abstract_r"];
    await btn(PAPER, "approve").click();
    await panel(PAPER).locator(".ap-notice").waitFor();
    assert.equal(posts("/approve").length, 0);
    assert.match(await panel(PAPER).locator(".paper-differs").textContent(), /differ on the abstract\./);
    assert.match(await panel(PAPER).locator(".ap-flags").textContent(), /would publish the paper two ways/);
    await pastDoubleClick();
    await btn(PAPER, "approve").click();
    await btn(PAPER, "confirm").waitFor();
    assert.equal(posts("/approve").length, 0);

    // ---- No difference before or after is no change, with or without the key.
    await fresh();
    await openRow(PAPER);
    db.get(PAPER).differs = [];
    await btn(PAPER, "approve").click();
    await row(PAPER).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1);

    // ---- The full review says it too.
    await fresh();
    db.get(PAPER).differs = ["url_r"];
    await openRow(PAPER);
    await btn(PAPER, "full").click();
    await page.locator("#admin-detail-modal:not(.hidden)").waitFor();
    assert.equal(await page.locator("#admin-detail-body .paper-differs").textContent(),
                 "These originals would publish the paper two ways: they differ on the link.");
    await page.evaluate(() => closeAdminDetail());
    await quiet();

    // ---- A check nobody needed reads as on the full screen, and the LLM cannot dissent from it.
    await fresh();
    const noOutcome = { original_check: "incorrect", outcome_check: "not_applicable" };
    Object.assign(db.get(MOVES).record, {
      validator_1: human("ana", 11, noOutcome), validator_2: human("ben", 12, noOutcome),
      llm_validator: { ...agreeingLlm, original_check: "incorrect", outcome_check: "correct" },
    });
    await openRow(MOVES);
    const compare = await panel(MOVES).locator(".ap-compare").textContent();
    assert.match(compare, /not needed: ana marked the wrong paper, with no replacement/);
    assert.match(compare, /not needed: ben marked the wrong paper, with no replacement/);
    assert.equal(await panel(MOVES).locator(".ap-llm-off").count(), 0);
    assert.doesNotMatch(await panel(MOVES).locator(".ap-flags").textContent(), /LLM disagrees/);

    // ---- The list controls work on this view: sort, search, hide mine; a typed note outlives them.
    await fresh();
    await openRow(PAPER);
    await panel(PAPER).locator("textarea").fill("kept");
    calls.length = 0;
    await page.evaluate(() => document.querySelector('#admin-entries-head [data-sort="agreement"]').click());
    await page.waitForFunction(() => document.querySelector('#admin-entries-head [data-sort="agreement"]').classList.contains("th-sort-active"));
    assert.match(calls.find((c) => c.path === "/entries").search, /sort=agreement&dir=asc/);
    await page.locator("tr.ap-row").first().waitFor();
    assert.equal(await page.locator(".ap-panel-row").count(), 0, "a new list starts collapsed");
    assert.match(await row(PAPER).textContent(), /note not saved/);
    await openRow(PAPER);
    assert.equal(await panel(PAPER).locator("textarea").inputValue(), "kept");
    await page.locator("#admin-search-input").fill("Replication 5");
    await page.waitForResponse((r) => r.url().includes("search=Replication"));
    calls.length = 0;
    await page.evaluate(() => { const box = document.querySelector("#admin-hide-mine"); box.checked = true; box.dispatchEvent(new Event("change", { bubbles: true })); });
    await page.waitForResponse((r) => r.url().includes("hide_mine=true"));
    await page.evaluate(() => { const box = document.querySelector("#admin-hide-mine"); box.checked = false; box.dispatchEvent(new Event("change", { bubbles: true })); });
    await page.evaluate(() => { _adminSort = ""; _adminSearch = ""; document.querySelector("#admin-search-input").value = ""; });

    // ---- Already decided on opening: no decision offered, no POST, dismissed by hand.
    await fresh();
    Object.assign(db.get(DECIDED).record, { validation_status: "validated", admin_checked: true, admin_name: "cy" });
    db.get(DECIDED).decisions = approvedBy("cy");
    await toggle(DECIDED).click();
    await panel(DECIDED).locator(".ap-decided").waitFor();
    assert.match(await panel(DECIDED).locator(".ap-decided").textContent(), /approved by cy/);
    assert.equal(await btn(DECIDED, "approve").count(), 0);
    assert.equal(await btn(DECIDED, "flag").count(), 0);
    await quiet();
    assert.equal(await row(DECIDED).isVisible(), true, "the refresh keeps it until dismissed");
    await btn(DECIDED, "dismiss").click();
    await row(DECIDED).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length + posts("/flag-review").length, 0);

    // ---- Decided by another admin while open: approve and flag both stop at the re-read.
    await fresh();
    await openRow(MOVES);
    db.get(MOVES).record.validation_status = "validated";
    db.get(MOVES).record.admin_checked = true;
    db.get(MOVES).decisions = approvedBy("cy");
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-decided").waitFor();
    assert.equal(posts("/approve").length, 0);

    await fresh();
    await openRow(MOVES);
    db.get(MOVES).record.validation_status = "need_review";    // flag-review alone would accept this
    await btn(MOVES, "flag").click();
    await panel(MOVES).locator(".ap-decided").waitFor();
    assert.equal(posts("/flag-review").length, 0);

    // ---- The values changed under an open panel: stop, show them, decide on the next press.
    await fresh();
    await openRow(MOVES);
    db.get(MOVES).record.final_outcome = "successful";
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    assert.match(await panel(MOVES).locator(".final-preview-card").textContent(), /successful/);
    assert.equal(posts("/approve").length, 0);

    // ---- A note conflict's read is drawn in full, so nothing it carried goes through unseen.
    await fresh();
    await openRow(MOVES);
    Object.assign(db.get(MOVES).record, { admin_notes: "theirs", note_saved_by: "cy",
      llm_validator: { ...agreeingLlm, outcome_check: "incorrect", corrected_outcome: "mixed" } });
    await panel(MOVES).locator("textarea").fill("mine");
    await btn(MOVES, "note").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    const clash = await panel(MOVES).locator(".ap-notice").textContent();
    assert.match(clash, /changed while it was open/);
    assert.match(clash, /cy saved a note while you were typing: “theirs”/);
    assert.match(await panel(MOVES).locator(".ap-compare").textContent(), /dissents/, "the body shows the new LLM answer");
    assert.equal(await panel(MOVES).locator("textarea").inputValue(), "mine", "and the typed note");
    assert.equal(posts("/note").length, 0);

    // Compared with what was drawn, not the last read: a later change still stops it.
    db.get(MOVES).record.final_outcome = "successful";
    await pastDoubleClick();
    await btn(MOVES, "approve").click();
    await pastDoubleClick();
    await btn(MOVES, "confirm").click();
    await panel(MOVES).locator(".ap-notice").filter({ hasText: "changed while it was open" }).waitFor();
    assert.match(await panel(MOVES).locator(".final-preview-card").textContent(), /successful/);
    assert.equal(posts("/approve").length, 0);

    // ---- A saved note with nothing adopted: the compare before deciding still sees the change.
    await fresh();
    await openRow(MOVES);
    db.get(MOVES).record.final_outcome = "mixed";
    await panel(MOVES).locator("textarea").fill("no clash");
    await btn(MOVES, "note").click();
    await page.waitForFunction((rid) => document.querySelector(`#ap-panel-${rid} [data-ap="note"]`).textContent.includes("Saved"), MOVES);
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    assert.equal(posts("/approve").length, 0);

    // ---- The answer was lost, nothing landed, and the entry moved on: same stop, full redraw.
    await fresh();
    await openRow(MOVES);
    await page.route(`**/api/admin/entries/${MOVES}/approve`, async (route) => {
      db.get(MOVES).record.final_outcome = "mixed";
      await route.abort("failed");
    });
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    await page.unroute(`**/api/admin/entries/${MOVES}/approve`);
    assert.match(await panel(MOVES).locator(".faq-error").textContent(), /Could not reach the server, and the entry is still waiting/);
    assert.match(await panel(MOVES).locator(".final-preview-card").textContent(), /mixed/);
    await pastDoubleClick();
    await btn(MOVES, "approve").click();
    await row(MOVES).waitFor({ state: "detached" });
    assert.equal(posts("/approve").length, 1, "only the press after the redraw reached the server");

    // ---- The server has the last word: 404 after the re-read passed.
    await fresh();
    await openRow(MOVES);
    await page.route(`**/api/admin/entries/${MOVES}/approve`, async (route) => {
      db.get(MOVES).record.validation_status = "validated";
      db.get(MOVES).decisions = approvedBy("cy");
      await route.fulfill({ status: 404, json: { detail: "Record not found or not awaiting approval" } });
    });
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-decided").waitFor();
    await page.unroute(`**/api/admin/entries/${MOVES}/approve`);

    // ---- 403: whatever the panel believed, Approve goes off with the server's reason.
    await fresh();
    await openRow(MOVES);
    db.get(MOVES).self = { linked: true, mine: true, allowed: false, reason: "Another admin decides this one." };
    await page.route(`**/api/admin/entries/${MOVES}`, (route) => route.request().method() === "GET"
      ? route.fulfill({ json: { record: db.get(MOVES).record, self_approval: { allowed: true }, extra_originals_summary: { open_suggestions: 0, children: 0 }, paper_originals: [] } })
      : route.fallback());
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-block-list").waitFor();
    assert.match(await panel(MOVES).locator(".ap-flags").textContent(), /Another admin decides this one/);
    assert.equal(await btn(MOVES, "approve").isDisabled(), true);
    await page.unroute(`**/api/admin/entries/${MOVES}`);

    // ---- 409 as a plain string: stays, says so, Approve off, Flag and full review on, escaped.
    await fresh();
    await openRow(DUP);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.match(await row(DUP).textContent(), /<img src=x/);
    await btn(DUP, "approve").click();
    await panel(DUP).locator(".ap-block-list").waitFor();
    assert.match(await panel(DUP).locator(".ap-block-list").textContent(), /use Mark as Resolved to merge/);
    assert.equal(await btn(DUP, "approve").isDisabled(), true);
    assert.equal(await btn(DUP, "flag").isDisabled(), false);
    assert.equal(await row(DUP).isVisible(), true);

    // ---- A failed request: the row stays, a retry works.
    await fresh();
    await openRow(LAST);
    failNext = (m, p) => m === "POST" && p.endsWith("/approve");
    await btn(LAST, "approve").click();
    await panel(LAST).locator(".faq-error").waitFor();
    assert.match(await panel(LAST).locator(".faq-error").textContent(), /Could not reach the server/);
    assert.equal(await row(LAST).isVisible(), true);
    await btn(LAST, "approve").click();
    await row(LAST).waitFor({ state: "detached" });

    // ---- The read before deciding failed: nothing was sent, and the panel says so.
    await fresh();
    await openRow(LAST);
    failNext = (m, p) => m === "GET" && p.endsWith(`/entries/${LAST}`);
    await btn(LAST, "approve").click();
    await panel(LAST).locator(".faq-error").waitFor();
    assert.match(await panel(LAST).locator(".faq-error").textContent(), /nothing was sent/);
    assert.equal(posts("/approve").length, 0);

    // ---- The approve landed and only the answer was lost: it counts as done.
    await fresh();
    await openRow(MOVES);
    await page.route(`**/api/admin/entries/${MOVES}/approve`, async (route) => {
      db.get(MOVES).record.validation_status = "validated";
      db.get(MOVES).row.validation_status = "validated";
      db.get(MOVES).decisions = approvedBy("me");
      await route.abort("failed");
    });
    await btn(MOVES, "approve").click();
    await row(MOVES).waitFor({ state: "detached" });
    await page.unroute(`**/api/admin/entries/${MOVES}/approve`);

    // ---- A failed load: the error, Retry, full review; Approve is not offered.
    await fresh();
    failNext = (m, p) => m === "GET" && p.endsWith(`/entries/${PLAIN}`);
    await toggle(PLAIN).click();
    await panel(PLAIN).locator(".faq-error").waitFor();
    assert.equal(await btn(PLAIN, "approve").count(), 0);
    await btn(PLAIN, "retry").click();
    await ready(PLAIN);
    assert.equal(await btn(PLAIN, "approve").isDisabled(), false);

    // ---- Typing in one panel survives a decision in another, and the refresh it causes.
    await fresh();
    await openRow(PLAIN);
    await openRow(LAST);
    await panel(LAST).locator("textarea").fill("half a thought");
    await page.evaluate(() => { document.querySelector(".fp-abstract").open = true; });
    await btn(PLAIN, "approve").click();
    await row(PLAIN).waitFor({ state: "detached" });
    await quiet();
    assert.equal(await panel(LAST).locator("textarea").inputValue(), "half a thought");
    assert.match(await row(LAST).textContent(), /note not saved/);

    // ---- A typed note goes first; the comparison is the last thing before the decision.
    calls.length = 0;
    await pastDoubleClick();
    await btn(LAST, "approve").click();
    await row(LAST).waitFor({ state: "detached" });
    assert.deepEqual(calls.filter((c) => c.path.startsWith(`/entries/${LAST}`))
      .map((c) => `${c.method} ${c.path.slice(`/entries/${LAST}`.length)}`),
      ["GET ", "POST /note", "GET ", "POST /approve"]);
    assert.equal(JSON.parse(posts("/note")[0].body).note, "half a thought");

    // ---- A change landing while the note is saved stops the decision; the note stays saved.
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("noted");
    await page.route(`**/api/admin/entries/${MOVES}/note`, async (route) => {
      db.get(MOVES).record.final_outcome = "mixed";
      await route.fallback();
    });
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    await page.unroute(`**/api/admin/entries/${MOVES}/note`);
    assert.equal(posts("/note").length, 1);
    assert.equal(posts("/approve").length, 0);
    assert.equal(await page.evaluate((rid) => !!_apNoteDrafts[rid], MOVES), false);
    assert.equal(await panel(MOVES).locator("textarea").inputValue(), "noted");

    // ---- A failed note decides nothing, and the text stays.
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("try again");
    failNext = (m, p) => m === "POST" && p.endsWith("/note");
    await btn(MOVES, "approve").click();
    await panel(MOVES).locator(".faq-error").waitFor();
    assert.match(await panel(MOVES).locator(".faq-error").textContent(), /Note not saved/);
    assert.equal(posts("/approve").length, 0);
    assert.equal(await btn(MOVES, "approve").isDisabled(), false);

    // ---- Typing on while a note saves: that part stays unsaved, and says so.
    await fresh();
    await openRow(PLAIN);
    const typing = panel(PLAIN).locator("textarea");
    await typing.fill("first");
    let releaseNote;
    holdNote = new Promise((r) => { releaseNote = r; });
    await btn(PLAIN, "note").click();
    while (!posts("/note").length) await page.waitForTimeout(10);
    await typing.fill("first and more");
    releaseNote();
    await panel(PLAIN).locator("[data-ap-note-meta]").filter({ hasText: "just now" }).waitFor();
    await quiet();
    assert.equal(db.get(PLAIN).record.admin_notes, "first");
    assert.equal(await typing.inputValue(), "first and more");
    assert.equal((await btn(PLAIN, "note").textContent()).trim(), "Save Note");
    assert.match(await panel(PLAIN).locator(".ap-note-hint").textContent(), /Not saved yet/);
    assert.match(await row(PLAIN).textContent(), /note not saved/);
    await btn(PLAIN, "note").click();
    await btn(PLAIN, "note").filter({ hasText: "Saved" }).waitFor();
    assert.equal(db.get(PLAIN).record.admin_notes, "first and more");
    assert.doesNotMatch(await row(PLAIN).textContent(), /note not saved/);

    // ---- While a decision goes the note is read-only, as the row leaves with it...
    await fresh();
    await openRow(PLAIN);
    await panel(PLAIN).locator("textarea").fill("last words");
    holdNote = new Promise((r) => { releaseNote = r; });
    await btn(PLAIN, "approve").click();
    while (!posts("/note").length) await page.waitForTimeout(10);
    assert.equal(await panel(PLAIN).locator("textarea").evaluate((t) => t.readOnly), true);
    await btn(PLAIN, "note").click();
    await quiet();
    assert.equal(posts("/note").length, 1, "Save Note cannot start a second run under the decision");
    releaseNote();
    await row(PLAIN).waitFor({ state: "detached" });
    assert.equal(db.get(PLAIN).record.admin_notes, "last words");

    // ...and takes typing again when the decision stops.
    await fresh();
    await openRow(PLAIN);
    await panel(PLAIN).locator("textarea").fill("kept");
    db.get(PLAIN).record.final_title_r = "Changed meanwhile";
    await btn(PLAIN, "approve").click();
    await panel(PLAIN).locator(".ap-notice").waitFor();
    assert.equal(await panel(PLAIN).locator("textarea").evaluate((t) => t.readOnly), false);

    // ---- Another admin's note is not overwritten unseen.
    await fresh();
    await openRow(PLAIN);
    await panel(PLAIN).locator("textarea").fill("mine");
    db.get(PLAIN).record.admin_notes = "theirs";
    db.get(PLAIN).record.note_saved_by = "cy";
    await btn(PLAIN, "note").click();
    await panel(PLAIN).locator(".ap-notice").waitFor();
    assert.match(await panel(PLAIN).locator(".ap-notice").textContent(), /cy saved a note while you were typing: “theirs”/);
    assert.equal(posts("/note").length, 0);
    await btn(PLAIN, "note").click();
    await page.waitForFunction(() => document.querySelector('[data-ap="note"]').textContent.includes("Saved"));
    assert.equal(posts("/note").length, 1);
    assert.equal(await panel(PLAIN).locator(".ap-notice").count(), 0, "saving it clears the warning");

    // ---- "Remove from this list" saves a typed note rather than drop it.
    const decideUnderPanel = async (rid) => {
      Object.assign(db.get(rid).record, { validation_status: "validated", admin_checked: true });
      db.get(rid).row.validation_status = "validated";
      db.get(rid).decisions = approvedBy("cy");
      await page.evaluate(() => fetchAdminEntries(false));
      await panel(rid).locator(".ap-decided").waitFor();
    };
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("for the record");
    await decideUnderPanel(MOVES);
    assert.equal(await panel(MOVES).locator("textarea").inputValue(), "for the record");
    assert.match(await panel(MOVES).locator(".ap-note-hint").textContent(), /Removing this row saves it first/);
    await btn(MOVES, "dismiss").click();
    await row(MOVES).waitFor({ state: "detached" });
    assert.equal(JSON.parse(posts("/note")[0].body).note, "for the record");

    // And it stops for another admin's note, as Save does, on the first press only.
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("mine");
    await decideUnderPanel(MOVES);
    Object.assign(db.get(MOVES).record, { admin_notes: "theirs", note_saved_by: "cy" });
    await btn(MOVES, "dismiss").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    assert.match(await panel(MOVES).locator(".ap-notice").textContent(), /cy saved a note while you were typing: “theirs”/);
    assert.equal(posts("/note").length, 0);
    assert.equal(await row(MOVES).isVisible(), true);
    await btn(MOVES, "dismiss").click();
    await row(MOVES).waitFor({ state: "detached" });
    assert.equal(posts("/note").length, 1);

    // ---- Open full review checks the note the same way.
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("mine");
    Object.assign(db.get(MOVES).record, { admin_notes: "theirs", note_saved_by: "cy" });
    await btn(MOVES, "full").click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    assert.equal(posts("/note").length, 0);
    assert.equal(await page.locator("#admin-detail-modal").isHidden(), true);
    await btn(MOVES, "full").click();
    await page.locator("#admin-detail-modal:not(.hidden)").waitFor();
    assert.equal(posts("/note").length, 1);
    await page.locator("#admin-detail-body .final-preview-card").waitFor();
    await page.evaluate(() => closeAdminDetail());
    await quiet();

    // From the collapsed row's Review too: the panel opens, so the stop is seen.
    await fresh();
    await openRow(MOVES);
    await panel(MOVES).locator("textarea").fill("mine");
    await toggle(MOVES).click();
    Object.assign(db.get(MOVES).record, { admin_notes: "theirs", note_saved_by: "cy" });
    await row(MOVES).locator('[data-ap="full"]').click();
    await panel(MOVES).locator(".ap-notice").waitFor();
    assert.equal(await panel(MOVES).isVisible(), true);
    assert.equal(await page.locator("#admin-detail-modal").isHidden(), true);

    // ---- Panels leave nothing in the full screen's cache: the next entry there is read fresh.
    await fresh();
    await openRow(LAST);
    assert.equal(await page.evaluate((rid) => !!_adminDetailCache[rid], LAST), false);
    Object.assign(db.get(LAST).record, { final_title_r: "Renamed under the panel" });
    await row(MOVES).locator('[data-ap="full"]').click();
    await page.locator("#admin-detail-body .final-preview-card").waitFor();
    await page.evaluate(() => advanceToNextAdminEntry());
    await page.locator("#admin-detail-body").filter({ hasText: "Renamed under the panel" }).waitFor();
    await page.evaluate(() => closeAdminDetail());
    await quiet();
    assert.deepEqual(await page.evaluate(() => Object.keys(_adminDetailCache)), [], "and the close empties it, as a reset would");

    // ---- Drafted originals: approving would strand them.
    await fresh();
    await page.evaluate((r) => { _adminXoDrafts[r] = { on: true, entries: [{}], mode: "draft" }; }, PLAIN);
    await openRow(PLAIN);
    assert.equal(await btn(PLAIN, "approve").isDisabled(), true);
    assert.match(await panel(PLAIN).locator(".ap-block-list").textContent(), /You drafted 1 additional original/);
    assert.equal(await btn(PLAIN, "flag").isDisabled(), false);
    await page.evaluate((r) => { delete _adminXoDrafts[r]; }, PLAIN);

    // ---- A tiebreak: the loser's proposal is named as not published.
    await fresh();
    await openRow(TIE);
    const flags = await panel(TIE).locator(".ap-flags").textContent();
    assert.match(flags, /The validators disagreed and the LLM broke the tie/);
    assert.match(flags, /ben proposed a different outcome: successful\. Approving does not publish it/);
    assert.match(flags, /ben proposed a different published DOI: 10\.5555\/pub/);
    assert.doesNotMatch(await panel(TIE).locator(".final-preview-card").textContent(), /10\.5555\/pub/);

    // ---- Flag: POST {}; the next row opens; Needs Review counts it.
    await fresh();
    await openRow(PLAIN);
    await btn(PLAIN, "flag").click();
    await row(PLAIN).waitFor({ state: "detached" });
    assert.equal(posts("/flag-review")[0].body, "{}");
    await ready(SELF);
    await quiet();
    assert.equal(await page.locator("#fc-needs-review").textContent(), "1");

    // ---- Keyboard: Enter opens, Escape from the panel closes and focus returns.
    await fresh();
    await toggle(PLAIN).focus();
    await page.keyboard.press("Enter");
    await ready(PLAIN);
    await btn(PLAIN, "approve").focus();
    await page.keyboard.press("Escape");
    assert.equal(await panel(PLAIN).isHidden(), true);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.ap), "toggle");

    // ---- The bottom of a page: the next page's first row moves up and opens.
    await fresh();
    await addRows(11, 51);
    await page.evaluate(() => { _adminPage = 1; fetchAdminEntries(); });
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 50);
    assert.match(await page.locator(".admin-page-info").textContent(), /Page 1 of 2 \(51 entries\)/);
    await openRow(id(50));
    await btn(id(50), "approve").click();
    await row(id(50)).waitFor({ state: "detached" });
    await ready(id(51));
    assert.equal(await toggle(id(51)).getAttribute("aria-expanded"), "true");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), id(51));
    assert.equal(await page.locator(".admin-page-info").count(), 0, "one page left");

    // ---- The only row of the last page, one page left: back to it, focus kept, no pager.
    await addRows(52, 52);
    await page.evaluate(() => { _adminPage = 2; _apLastDecisionAt = 0; fetchAdminEntries(); });
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 1);
    await openRow(id(52));
    await btn(id(52), "approve").click();
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 50);
    assert.equal(await page.evaluate(() => _adminPage), 1);
    assert.equal(await page.locator("#admin-empty").isHidden(), true);
    assert.equal(await page.locator(".admin-page-info").count(), 0);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), id(51));
    assert.equal(await toggle(id(51)).getAttribute("aria-expanded"), "false", "focus, but nothing opens by itself");

    // ---- The same with pages left: "Page 2 of 2" with its rows, never "Page 3 of 2" and no table.
    await fresh();
    await addRows(11, 101);
    await page.evaluate(() => { _adminPage = 3; fetchAdminEntries(); });
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 1);
    assert.match(await page.locator(".admin-page-info").textContent(), /Page 3 of 3 \(101 entries\)/);
    await openRow(id(101));
    await btn(id(101), "approve").click();
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 50);
    assert.match(await page.locator(".admin-page-info").textContent(), /Page 2 of 2 \(100 entries\)/);
    assert.equal(await page.locator("#admin-next").isDisabled(), true);
    assert.equal(await page.locator("#admin-empty").isHidden(), true);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), id(100));

    // ---- From another filter: the link lands on the row on its own page, and opens it.
    await fresh();
    await addRows(11, 60);
    db.get(id(55)).row.open_extra_suggestions = 1;
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${id(55)}"]`).waitFor();
    calls.length = 0;
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${id(55)}"]`).click();
    await ready(id(55));
    assert.equal(await page.evaluate(() => _adminFilter), "pending_approval");
    assert.equal(await page.locator('#admin-filters .admin-filter-btn.active').getAttribute("data-filter"), "pending_approval");
    assert.equal(await page.evaluate(() => _adminPage), 2);
    assert.match(await page.locator(".admin-page-info").textContent(), /Page 2 of 2/);
    assert.equal(await page.locator("#admin-entries-head th").count(), 8);
    assert.equal(await toggle(id(55)).getAttribute("aria-expanded"), "true");
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), id(55));

    // Even when "Hide entries I validated" would hide it, and the box then says so.
    await fresh();
    db.get(SUGGEST).row.mine = true;
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).waitFor();
    await page.evaluate(() => { _adminHideMine = true; document.querySelector("#admin-hide-mine").checked = true; });
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).click();
    await ready(SUGGEST);
    assert.equal(await page.evaluate(() => _adminHideMine), false);
    assert.equal(await page.locator("#admin-hide-mine").isChecked(), false);
    await page.locator("#toast").filter({ hasText: "Hide entries I validated" }).waitFor({ state: "attached" });
    assert.equal(await page.evaluate(() => document.activeElement.dataset.id), SUGGEST);

    // A row that is not hidden leaves the box alone.
    await fresh();
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).waitFor();
    await page.evaluate(() => { _adminHideMine = true; document.querySelector("#admin-hide-mine").checked = true; });
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).click();
    await ready(SUGGEST);
    assert.equal(await page.evaluate(() => _adminHideMine), true);
    await page.evaluate(() => { _adminHideMine = false; document.querySelector("#admin-hide-mine").checked = false; });

    // Decided since that list was read: says so, and leaves a working first page.
    await fresh();
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).waitFor();
    db.get(SUGGEST).row.validation_status = "validated";
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).click();
    await page.locator("#toast").filter({ hasText: "no longer waiting for approval" }).waitFor({ state: "attached" });
    await page.locator("tr.ap-row").first().waitFor();
    assert.equal(await page.evaluate(() => _adminPage), 1);
    assert.equal(await page.locator(".ap-panel-row").count(), 0);

    // Decided, with the box ticked: the box stays, and the pages are walked once.
    await fresh();
    await addRows(11, 60);
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).waitFor();
    await page.evaluate(() => { _adminHideMine = true; document.querySelector("#admin-hide-mine").checked = true; });
    db.get(SUGGEST).row.validation_status = "validated";
    calls.length = 0;
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).click();
    await page.locator("tr.ap-row").first().waitFor();
    assert.equal(await page.evaluate(() => _adminHideMine), true);
    assert.equal(calls.filter((c) => c.method === "GET" && c.path === "/entries").length, 3, "page 1, page 2, back to 1");
    await page.evaluate(() => { _adminHideMine = false; document.querySelector("#admin-hide-mine").checked = false; });

    // A later click on the way replaces the search: nothing opens under the admin.
    await fresh();
    await filterTo("extra_originals");
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).waitFor();
    let releaseSeek;
    holdList = new Promise((r) => { releaseSeek = r; });
    await page.locator(`#admin-table-body .admin-to-pending[data-id="${SUGGEST}"]`).click();
    await page.waitForTimeout(30);
    await filterTo("all");
    await page.locator("#admin-table-body .admin-review-btn").first().waitFor();
    releaseSeek();
    await page.waitForTimeout(120);
    assert.equal(await page.evaluate(() => _adminFilter), "all");
    assert.equal(await page.locator("tr.ap-row").count(), 0);

    // ---- A dropped reset whose soft replacement failed: Retry, not "Loading…" for ever.
    await fresh();
    let releaseReset;
    holdList = new Promise((r) => { releaseReset = r; });
    await page.evaluate(() => { fetchAdminEntries(); });
    await page.waitForTimeout(30);
    assert.match(await page.locator("#admin-table-body").textContent(), /Loading…/);
    failNext = (m, p) => m === "GET" && p === "/api/admin/entries";
    await page.evaluate(() => { fetchAdminEntries(false); });
    await page.locator("#admin-retry-btn").waitFor();
    releaseReset();
    await page.waitForTimeout(120);
    await page.locator("#admin-retry-btn").click();
    await page.locator("tr.ap-row").first().waitFor();

    // ---- A slow answer for the previous filter must not paint over the next one.
    await fresh();
    let release;
    holdList = new Promise((r) => { release = r; });
    await page.evaluate(() => { _adminPage = 1; fetchAdminEntries(); });
    await page.waitForTimeout(30);
    await filterTo("all");
    await page.locator("#admin-table-body .admin-review-btn").first().waitFor();
    release();
    await page.waitForTimeout(120);
    assert.equal(await page.locator("#admin-entries-head th").count(), 11);
    assert.equal(await page.locator("tr.ap-row").count(), 0);
    assert.equal(await page.locator(".ap-table").count(), 0);

    // ---- Nothing left: says so, and focus does not fall off the page.
    await fresh();
    for (const d of db.values()) if (d.record.record_id !== PLAIN) d.row.listed = false;
    await page.evaluate(() => fetchAdminEntries());
    await page.waitForFunction(() => document.querySelectorAll("tr.ap-row").length === 1);
    await openRow(PLAIN);
    await btn(PLAIN, "approve").click();
    await page.locator("#admin-empty:not(.hidden)").waitFor();
    assert.match(await page.locator("#admin-empty").textContent(), /Nothing is waiting for approval/);
    assert.equal(await page.evaluate(() => document.activeElement.id), "admin-empty");

    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(errors, []);
    console.log("Approval view checks passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
