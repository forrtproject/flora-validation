/* Run with: node tests/test_paper_review_ui.cjs
 * The review table for a paper with several originals, the no-outcome rule on the
 * one-record screen, and the admin paper panel. All requests are intercepted; no
 * server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const QUOTE_A = "We could not replicate the earlier association.";
const QUOTE_B = "We confirmed the association.";
const row = (id, n, outcome, quote, extra = {}) => ({
  record_id: id, pair_id: `pair-${id}`, queue_id: `q-${id}`, judgeable: true, type: "replication",
  title_r: "The paper", doi_r: "10.1234/paper", abstract_r: `Abstract. ${QUOTE_A} ${QUOTE_B}`,
  out_quote_source: "abstract", title_o: `Original ${n}`, authors_o: `Author${n}, A.`, year_o: "2001",
  doi_o: `10.1234/o${n}`, outcome, outcome_quote: quote, outcome_phrase: quote,
  link_evidence: `We replicated study ${n}. | internal note`, ...extra,
});
const SET = {
  doi_r: "10.1234/paper",
  originals: [
    row("a", 1, "failed", QUOTE_A),
    row("b", 2, "failed", QUOTE_A),
    row("c", 3, "successful", QUOTE_B),
    row("d", 4, "failed", ""),
    row("e", 5, "failed", ""),
    { record_id: "f", judgeable: false, title_o: "Original 6", authors_o: "Author6, A.", year_o: "2002", doi_o: "10.1234/o6" },
  ],
};
const PAIR = {
  ...row("a", 1, "failed", QUOTE_A), paper_review: true,
  coded_originals: SET.originals.map(o => ({ record_id: o.record_id, title_o: o.title_o })),
};

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  const errors = [], judged = [], skips = [], approved = [];
  let claimStatus = 200, claimed = SET, claimHeld = null;
  let heldLost = new Set(), skipFails = new Set(), skipGone = new Set();
  const refreshed = [];
  const taken = (...ids) => ({ ...SET, originals: SET.originals.map(o => ids.includes(o.record_id) ? { ...o, judgeable: false } : o) });
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/paper-review/claim") {
      if (claimHeld) await claimHeld;
      return claimStatus === 200 ? route.fulfill({ status: 200, json: claimed })
        : route.fulfill({ status: claimStatus, json: { detail: "no" } });
    }
    if (url.pathname === "/api/paper-review/refresh") {
      const ids = route.request().postDataJSON().record_ids;
      refreshed.push(ids);
      return route.fulfill({ status: 200, json: { held: ids.filter(id => !heldLost.has(id)) } });
    }
    if (url.pathname === "/api/judge") { judged.push(route.request().postDataJSON()); return route.fulfill({ status: 200, json: { points_earned: 2 } }); }
    if (url.pathname === "/api/skip") {
      const id = route.request().postDataJSON().record_id;
      if (skipFails.has(id)) return route.fulfill({ status: 500, json: { detail: "Server error." } });
      if (skipGone.has(id)) return route.fulfill({ status: 409, json: { detail: "This record is no longer assigned to you" } });
      skips.push(id);
      return route.fulfill({ status: 200, json: { skipped: true, reason_recorded: true } });
    }
    if (url.pathname.endsWith("/approve")) { approved.push(url.pathname.split("/")[4]); return route.fulfill({ status: 200, json: {} }); }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ status: 200, json: [] });
    if (url.pathname === "/api/next-pairs") return route.fulfill({ status: 200, json: { pairs: [] } });
    return route.fulfill({ status: 200, json: { records: [], points: 0, rank: 1 } });
  });
  const show = data => page.evaluate(p => {
    localStorage.clear();
    document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
    document.querySelector("#game-screen").classList.remove("hidden");
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    state.coder = { coder_id: 1, handle: "Test validator" };
    state.assignment = null; state.mode = "normal"; state.paperReview = null;
    _submitQueue.length = 0; _pairBuffer = [];
    _pairShownAt = 0;
    _showActivePair(p);
  }, data);
  const pr = sel => page.locator(`#pr-body ${sel}`);
  const orig = (id, value) => pr(`[data-pr="orig"][data-id="${id}"][data-v="${value}"]`);
  const checks = () => pr(".pr-check").count();
  const footer = () => page.locator("#pr-foot .pr-progress").textContent();
  const answerType = () => pr('[data-pr="type"]').first().click();
  const allCorrect = async () => { for (const id of "abcde") await orig(id, "correct").click(); };
  const answerChecks = async () => {
    const n = await checks();
    for (let i = 0; i < n; i++) {
      const button = pr(".pr-check").nth(i).locator('[data-pr="out"][data-v="right"]');
      if (await button.count()) await button.click();
    }
  };
  const submit = async () => {
    await page.evaluate(() => { _pairShownAt = 0; });
    await page.locator("#pr-submit").click();
    await page.waitForFunction(() => _myQueue().length === 0 && !state.paperReview);
  };

  try {
    await page.goto("http://flora.test/");

    // ── A paper with several originals opens in the table, type first.
    await show(PAIR);
    await pr('[data-pr="type"]').first().waitFor();
    assert.equal(await page.locator(".pr-table").count(), 0, "the table waits for the type");
    assert.equal(await page.locator("#pair-card.paper-review.split-layout").count(), 0, "the table is one column");
    await answerType();

    // Originals share a check only when outcome and quote both match, and never without a quote.
    assert.equal(await checks(), 4);
    assert.match(await pr(".pr-check.shared").textContent(), /One check for these 2 originals/);
    assert.equal(await pr("tbody tr").count(), 6);
    assert.match(await pr(".pr-context").textContent(), /no answer needed from you/);
    assert.equal(await pr(".pr-context [data-pr]").count(), 0, "context rows take no answers");

    // The evidence hides the extractor's internal tail.
    await pr('[data-pr="why"][data-id="a"]').click();
    const evidence = await pr(".pr-evidence").textContent();
    assert.match(evidence, /We replicated study 1\./);
    assert.doesNotMatch(evidence, /internal note/);
    await pr('[data-pr="why"][data-id="a"]').click();

    // Nothing can be submitted until every original and check is answered.
    await allCorrect();
    assert.match(await footer(), /4 answers left/);
    assert.equal(await page.locator("#pr-submit").isDisabled(), true);

    // Outcome differs for this one: out of the shared check, and back.
    await pr('[data-pr="pull"][data-id="b"]').click();
    assert.equal(await checks(), 5);
    await pr('[data-pr="unpull"][data-id="b"]').click();
    assert.equal(await checks(), 4);

    // Wrong paper: no outcome, unless the validator adds the right paper.
    await orig("c", "wrong").click();
    assert.match(await pr('tr.pr-wrong').textContent(), /Original 3/);
    assert.match(await pr(".pr-none").textContent(), /no outcome to check/);
    assert.match(await footer(), /3 answers left/);
    await pr('[data-pr-input="fix-doi"]').pressSequentially("10.1/x");
    assert.equal(await pr('[data-pr-input="fix-doi"]').inputValue(), "10.1/x", "typing survives the rerender");
    assert.match(await pr(".pr-table").textContent(), /Outcome for the paper you added/);
    assert.match(await footer(), /4 answers left/);
    await pr('[data-pr-input="fix-doi"]').fill("");
    assert.match(await footer(), /3 answers left/);

    // Mischaracterised needs the right category; a better quote needs different words.
    const shared = pr(".pr-check.shared");
    await shared.locator('[data-pr="out"][data-v="better"]').click();
    await answerChecks();
    await shared.locator('[data-pr="out"][data-v="better"]').click();
    assert.match(await footer(), /1 answer left/);
    await shared.locator("textarea").fill(`${QUOTE_A}!`);
    assert.match(await footer(), /1 answer left/, "punctuation is not a better quote");
    await shared.locator("textarea").fill("The association did not hold in our sample.");
    assert.match(await footer(), /Ready: 5 judgements/);

    // A reload keeps the answers.
    await page.evaluate(() => { _prSaveDraft(); state.paperReview = null; });
    await page.evaluate(p => { _pairBuffer = []; _showActivePair({ ...p }); }, PAIR);
    await page.locator("#pr-foot").waitFor();
    assert.match(await footer(), /Ready: 5 judgements/);
    assert.equal(await orig("c", "wrong").evaluate(el => el.classList.contains("sel")), true);

    // Another original rides on one judgement, once.
    await pr('[data-pr="draft-add"]').click();
    await pr('[data-pr-input="draft"][data-field="doi_or_url"]').fill("10.1234/another");
    assert.match(await footer(), /and 1 suggestion/);

    await submit();
    assert.deepEqual(judged.map(j => j.record_id).sort(), ["a", "b", "c", "d", "e"], "one judgement per record");
    const byId = Object.fromEntries(judged.map(j => [j.record_id, j]));
    assert.equal(byId.c.original_check, "incorrect");
    assert.equal(byId.c.outcome_check, "not_applicable");
    assert.equal(byId.c.corrected_outcome_quote, null);
    assert.equal(byId.c.additional_checks.paper_review.outcome_shared, false);
    assert.equal(byId.a.outcome_check, "correct");
    assert.equal(byId.a.corrected_outcome_quote, "The association did not hold in our sample.");
    assert.equal(byId.a.additional_checks.outcome_quote_disputed, true);
    assert.equal(byId.a.additional_checks.paper_review.outcome_shared, true);
    assert.equal(byId.a.additional_checks.paper_review.outcome_group, byId.b.additional_checks.paper_review.outcome_group);
    assert.notEqual(byId.d.additional_checks.paper_review.outcome_group, byId.e.additional_checks.paper_review.outcome_group);
    assert.equal(judged.filter(j => j.extra_originals?.length).length, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem(_PAPER_DRAFT_KEY("10.1234/paper"))), null);
    judged.length = 0;

    // Not either type answers for the whole paper.
    await show(PAIR);
    await pr('[data-pr="type"][data-v="not_validation"]').click();
    assert.equal(await page.locator(".pr-table").count(), 0);
    assert.match(await footer(), /Rejects the paper for all 5 originals/);
    await submit();
    assert.equal(judged.length, 5);
    assert.ok(judged.every(j => j.corrected_type === "not_validation" && j.outcome_check === "incorrect"));
    judged.length = 0;

    // A reproduction leaves the table: two axes per record, one at a time.
    await show(PAIR);
    await pr('[data-pr="type"][data-v="reproduction"]').click();
    await page.locator("#repro-outcome, #gate-2").first().waitFor();
    assert.equal(await page.locator("#pair-card.paper-review").count(), 0);
    assert.equal(await page.evaluate(() => state.judgement.type), "reproduction");
    assert.equal(await page.evaluate(p => _paperReviewOn(p), PAIR), false);

    // The siblings the table reserved come next, one at a time, each its own record.
    assert.deepEqual(await page.evaluate(() => _pairBuffer.map(b => b.record_id)), ["b", "c", "d", "e"]);
    await page.evaluate(() => loadNextPair(false));
    await page.waitForFunction(() => state.currentPair?.record_id === "b");
    assert.equal(await page.locator("#pair-card.paper-review").count(), 0);
    assert.deepEqual(await page.evaluate(() => [state.currentPair.title_o, state.currentPair.queue_id,
                                                state.currentPair.coded_originals.find(c => c.is_current).record_id]),
                     ["Original 2", "q-b", "b"]);
    await page.evaluate(() => { _pairBuffer = []; localStorage.clear(); });

    // Moving on while the claim is under way strands nothing either.
    let releaseClaim;
    claimHeld = new Promise(resolve => { releaseClaim = resolve; });
    await show(PAIR);
    await page.evaluate(() => { state.currentPair = { record_id: "elsewhere" }; });
    releaseClaim();
    claimHeld = null;
    await page.waitForFunction(() => _pairBuffer.length === 5);
    assert.deepEqual(await page.evaluate(() => _pairBuffer.map(b => b.record_id)), ["a", "b", "c", "d", "e"]);
    assert.equal(await page.evaluate(() => state.paperReview), null, "no table opens behind the other screen");
    // In hard mode the buffer holds hard records, so these short holds just lapse.
    claimHeld = new Promise(resolve => { releaseClaim = resolve; });
    await show(PAIR);
    await page.evaluate(() => { state.currentPair = { record_id: "elsewhere" }; state.mode = "hard"; });
    releaseClaim();
    claimHeld = null;
    await page.evaluate(() => new Promise(r => setTimeout(r, 200)));
    assert.equal(await page.evaluate(() => _pairBuffer.length), 0);
    await page.evaluate(() => { state.mode = "normal"; });

    // Skipping the paper releases every open record.
    await show(PAIR);
    await answerType();
    await page.evaluate(() => { showSkipReasonDialog = async () => ({ reason_code: "prefer_another", comment: null }); });
    await page.locator("#skip-btn").click();
    await page.waitForFunction(() => !state.paperReview);
    assert.deepEqual([...skips].sort(), ["a", "b", "c", "d", "e"]);
    await page.locator("#done-screen").waitFor();

    // A skip that fails partway: what was released leaves the table, so what stays can
    // still be submitted, and nothing is left to be refused as "No open slot".
    await show(PAIR);
    await answerType();
    await orig("c", "correct").click();
    skips.length = 0;
    skipFails = new Set(["c"]);
    await page.locator("#skip-btn").click();
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    assert.match(await page.locator("#dialog-message").textContent(), /could not be released.*stay in this table with your answers/);
    await page.locator("#dialog-buttons .btn-primary").click();
    assert.deepEqual([...skips].sort(), ["a", "b", "d", "e"]);
    assert.deepEqual(await page.evaluate(() => _prLive().map(o => o.record_id)), ["c"]);
    assert.equal(await orig("c", "correct").evaluate(el => el.classList.contains("sel")), true);
    assert.equal(await pr('[data-pr="orig"][data-id="a"]').count(), 0, "a released row takes no answer");
    await answerChecks();
    await submit();
    assert.deepEqual(judged.map(j => j.record_id), ["c"]);
    judged.length = 0;

    // A skip that worked but whose answer was lost: the refresh shows it, and the paper closes.
    await show(PAIR);
    await answerType();
    skips.length = 0;
    skipFails = new Set(["c"]);
    heldLost = new Set(["c"]);
    await page.locator("#skip-btn").click();
    await page.waitForFunction(() => !state.paperReview);
    assert.equal(await page.locator("#dialog-modal").isHidden(), true);
    skipFails = new Set();
    heldLost = new Set();

    // A skip the server answers "no longer assigned to you" has nothing left to release:
    // that row leaves the table, beside a row whose skip really failed.
    await show(PAIR);
    await answerType();
    skips.length = 0;
    skipGone = new Set(["c"]);
    skipFails = new Set(["d"]);
    await page.locator("#skip-btn").click();
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    await page.locator("#dialog-buttons .btn-primary").click();
    assert.deepEqual(await page.evaluate(() => _prLive().map(o => o.record_id)), ["d"]);
    skipGone = new Set();
    skipFails = new Set();

    // A judgement still queued is not offered again.
    await show(PAIR);
    await pr('[data-pr="type"]').first().waitFor();
    await page.evaluate(p => {
      state.paperReview = null;
      _submitQueue.push({ key: "x", payload: { record_id: "b" }, record_id: "b", blocked: true, owner_id: 1 });
      _showActivePair({ ...p });
    }, PAIR);
    await page.waitForFunction(() => state.paperReview?.originals.find(o => o.record_id === "b")?.judgeable === false);
    await page.evaluate(() => { _submitQueue.length = 0; });

    // A saved header correction reaches the draft without another table click.
    await show(PAIR);
    await pr('[data-pr="type"]').first().waitFor();
    await page.evaluate(() => { state.judgement.corrected_url_r = "https://example.org/paper"; });
    await page.locator(".pair-header .abstract").click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem(_PAPER_DRAFT_KEY("10.1234/paper")))?.judgement?.corrected_url_r);

    // The holds are renewed while the table is open, and the beat stops with it.
    await show(PAIR);
    await answerType();
    assert.equal(await page.evaluate(() => _prHoldTimer !== null), true, "the table renews its holds");
    assert.equal(await page.evaluate(() => _PAPER_HOLD_REFRESH_MS < 45 * 60 * 1000 / 3), true, "well inside the reaper's limit");
    refreshed.length = 0;
    await page.evaluate(() => _prHoldBeat());
    assert.deepEqual(refreshed, [["a", "b", "c", "d", "e"]]);

    // A hold that lapsed but is still free comes back, answers and all, without a word.
    await allCorrect();
    heldLost = new Set(["b"]);
    await page.evaluate(() => _prHoldBeat());
    assert.equal(await page.locator("#dialog-modal").isHidden(), true);
    assert.equal(await page.evaluate(() => _prLive().length), 5);
    assert.equal(await orig("b", "correct").evaluate(el => el.classList.contains("sel")), true);

    // One another validator took leaves the table. The validator is told, and the rest stays.
    await answerChecks();
    heldLost = new Set(["c"]);
    claimed = taken("c");
    await page.evaluate(() => { _prHoldBeat(); });
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    assert.match(await page.locator("#dialog-message").textContent(), /Your hold on Author3 2001 ran out.*Your other answers are kept/);
    await page.locator("#dialog-buttons .btn-primary").click();
    assert.deepEqual(await page.evaluate(() => _prLive().map(o => o.record_id)), ["a", "b", "d", "e"]);
    assert.equal(await pr('[data-pr="orig"][data-id="c"]').count(), 0, "it can no longer be answered");
    assert.equal(await orig("a", "correct").evaluate(el => el.classList.contains("sel")), true);
    assert.match(await footer(), /Ready: 4 judgements/);

    // Submitting checks first: a row lost at the last moment never reaches the queue.
    heldLost = new Set(["d"]);
    claimed = taken("c", "d");
    await page.evaluate(() => { _pairShownAt = 0; });
    await page.locator("#pr-submit").click();
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    await page.locator("#dialog-buttons .btn-primary").click();
    assert.equal(judged.length, 0, "nothing was sent");
    assert.equal(await page.evaluate(() => !!state.paperReview), true, "the table stays open");
    assert.match(await footer(), /Ready: 3 judgements/);
    heldLost = new Set();
    claimed = SET;
    await submit();
    assert.deepEqual(judged.map(j => j.record_id).sort(), ["a", "b", "e"]);
    judged.length = 0;

    // Every hold gone: the answers stay as a draft and the validator is told.
    await show(PAIR);
    await answerType();
    await orig("a", "correct").click();
    heldLost = new Set(["a", "b", "c", "d", "e"]);
    await page.evaluate(() => { _prHoldBeat(); });
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    assert.match(await page.locator("#dialog-message").textContent(), /Your answers are saved as a draft/);
    await page.locator("#dialog-buttons .btn-primary").click();
    await page.waitForFunction(() => !state.paperReview);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(_PAPER_DRAFT_KEY("10.1234/paper")))?.orig?.a), "correct");
    heldLost = new Set();

    // A draft answer for a row that is no longer held is not dropped in silence.
    claimed = taken("a");
    await page.evaluate(p => { _showActivePair({ ...p }); }, { ...PAIR, record_id: "b" });
    await page.waitForFunction(() => /another validator has that original/.test(document.querySelector("#toast")?.textContent || ""));
    claimed = SET;

    // Leaving the table ends the beat.
    await show(PAIR);
    await pr('[data-pr="type"]').first().waitFor();
    await page.evaluate(async () => { state.currentPair = { record_id: "other" }; await _prHoldBeat(); });
    assert.equal(await page.evaluate(() => _prHoldTimer), null);

    // A failed claim falls back to the one-record screen.
    claimStatus = 409;
    await show(PAIR);
    await page.locator("#gate-1").waitFor();
    assert.equal(await page.locator("#pr-body").count(), 0);
    claimStatus = 200;

    // ── One-record screen: wrong paper has no outcome unless corrected.
    const single = { ...row("s", 1, "failed", QUOTE_A), paper_review: false, coded_originals: [] };
    await show(single);
    await page.locator('#gate-1 [data-type="replication"]').click();
    await page.locator('[data-original="wrong"]').click();
    assert.equal(await page.locator("#gate-3").isVisible(), false);
    assert.equal(await page.locator("#submit-btn").isDisabled(), false);
    await page.locator("#corrected-doi-input").fill("10.1/right");
    assert.equal(await page.locator("#submit-btn").isDisabled(), true, "a replacement needs its outcome");
    await page.locator("#oc-save-btn").click();
    assert.equal(await page.locator("#gate-3").isVisible(), true);
    await page.locator("#oc-edit-btn").click();
    await page.locator("#corrected-doi-input").fill("");
    await page.locator("#oc-skip-btn").click();
    assert.equal(await page.locator("#gate-3").isVisible(), false);
    assert.equal(await page.locator("#submit-btn").isDisabled(), false);
    assert.equal(await page.evaluate(p => _buildJudgePayload(p, state.judgement).outcome_check, single), "not_applicable");

    // Without the table the suggestion panel stays, under the new name.
    assert.match(await page.locator(".xo-toggle").textContent(), /Add another original/);

    // With it, the button opens the table, keeps the answers and starts a suggestion.
    const one = { ...single, paper_review: true };
    const onlyOne = { doi_r: "10.1234/paper", originals: [row("s", 1, "failed", QUOTE_A)] };
    await page.route("**/api/paper-review/claim", route => route.fulfill({ status: 200, json: onlyOne }));
    await show(one);
    await page.locator('#gate-1 [data-type="replication"]').click();
    await page.locator('[data-original="correct"]').click();
    await page.locator("#pr-add-another").click();
    await page.locator("#pr-body .pr-draft").waitFor();
    assert.equal(await orig("s", "correct").evaluate(el => el.classList.contains("sel")), true);
    assert.equal(await pr('[data-pr-input="draft"][data-field="doi_or_url"]').count(), 1);
    await page.unroute("**/api/paper-review/claim");

    // ── Admin: the paper panel and the removal banner.
    const data = {
      original_removal: true, self_approval: { allowed: true },
      paper_originals: [
        { record_id: "a", title_o: "<b>One</b>", authors_o: "A; B; C; D", year_o: "2001", doi_o: "10.1/a", outcome: "failed",
          validation_status: "consensus_reached", original_checks: ["correct", "correct"], outcome_checks: ["correct", "correct"], shared_check: true, is_current: true },
        { record_id: "b", title_o: "Two", authors_o: "E", year_o: "2002", doi_o: "10.1/b", outcome: "failed",
          validation_status: "consensus_reached", original_checks: ["correct", "correct"], outcome_checks: ["correct", "correct"], shared_check: true },
        { record_id: "c", title_o: "Three", authors_o: "F", year_o: "2003", doi_o: "10.1/c", outcome: "failed",
          validation_status: "need_review", original_checks: ["incorrect", "incorrect"], outcome_checks: ["not_applicable", "not_applicable"], shared_check: false },
      ],
    };
    const panel = await page.evaluate(d => {
      const box = document.createElement("div");
      box.id = "test-admin";
      box.innerHTML = _adminRemovalBanner(d) + _adminPaperOriginals(d);
      document.body.append(box);
      return box.innerHTML;
    }, data);
    assert.match(panel, /Both validators marked this original as the wrong paper/);
    assert.match(panel, /This paper has 3 coded originals · 2 awaiting approval/);
    assert.match(panel, /Approve the 2 awaiting approval/);
    assert.ok(!panel.includes("<b>One</b>"), "titles are escaped");
    assert.match(panel, /A et al\./);
    assert.match(await page.locator("#test-admin tbody tr").nth(2).textContent(), /– –/);
    const blocked = await page.evaluate(d => _adminRemovalBanner({ ...d, self_approval: { allowed: false, reason: "Another admin decides this." } }), data);
    assert.match(blocked, /disabled/);
    assert.match(blocked, /Another admin decides this\./);
    // An added original has to go first, and the banner says so.
    const withAdded = await page.evaluate(d => _adminRemovalBanner({ ...d, extra_originals_summary: { children: 2 } }), data);
    assert.match(withAdded, /id="admin-remove-original-btn"[^>]*disabled/);
    assert.match(withAdded, /This entry has 2 added originals\. Remove them under &quot;Other originals&quot; first./);
    assert.doesNotMatch(await page.evaluate(d => _adminRemovalBanner({ ...d, extra_originals_summary: { children: 0 } }), data),
                        /id="admin-remove-original-btn"[^>]*disabled/);

    // Originals that would publish the paper two ways: above the list, naming what differs.
    const differing = fields => page.evaluate(([d, f]) => _adminPaperOriginals({ ...d, paper_differs_on: f }), [data, fields]);
    assert.doesNotMatch(panel, /two ways/, "a payload without the key shows nothing");
    assert.doesNotMatch(await differing([]), /two ways/);
    const both = await differing(["url_r", "abstract_r"]);
    assert.match(both, /<p class="paper-differs">These originals would publish the paper two ways: they differ on the link and abstract\.<\/p>\s*<details class="admin-paper-set"/);
    assert.match(both, /Approve the 2 awaiting approval/, "the warning leaves the button as it was");
    assert.match(await differing(["url_r"]), /they differ on the link\.</);
    assert.match(await differing(["abstract_r"]), /they differ on the abstract\.</);
    assert.ok(!(await differing(["<img src=x>"])).includes("<img"), "the fields are escaped");

    // Text from the extractor goes through escapeHtml, and only web links become links.
    const meta = await page.evaluate(() => _prOriginalMeta({ authors_o: "A", year_o: "<img src=x>", url_o: "javascript:alert(1)" }));
    assert.ok(!meta.includes("<img"), "the year is escaped");
    assert.ok(!meta.includes("javascript:"), "only http(s) becomes a link");
    assert.match(meta, /no registered DOI/);
    assert.match(await page.evaluate(() => _prOriginalMeta({ url_o: "https://example.org/x" })), /href="https:\/\/example\.org\/x"/);
    const adminMeta = await page.evaluate(() => _adminPaperOriginals({ paper_originals: [
      { record_id: "a", year_o: "<img src=x>", validation_status: "need_review" },
      { record_id: "b", year_o: "2001", validation_status: "need_review" }] }));
    assert.ok(!adminMeta.includes("<img"), "the admin panel escapes the year too");

    // Going one at a time for a paper is this validator's choice, not this browser's.
    assert.deepEqual(await page.evaluate(() => {
      localStorage.clear();
      state.coder = { coder_id: 1 };
      localStorage.setItem(_PAPER_OPT_OUT_KEY("10.1/opt"), "1");
      const mine = _paperReviewOn({ paper_review: true, doi_r: "10.1/opt" });
      state.coder = { coder_id: 2 };
      const theirs = _paperReviewOn({ paper_review: true, doi_r: "10.1/opt" });
      state.coder = { coder_id: 1 };
      return [mine, theirs];
    }), [false, true]);

    // A draft answered about another DOI does not carry over.
    await show(PAIR);
    await answerType();
    await allCorrect();
    await answerChecks();
    await pr('[data-pr="orig"][data-id="a"][data-v="wrong"]').click();
    await page.evaluate(() => { _prSaveDraft(); state.paperReview = null; });
    claimed = { ...SET, originals: SET.originals.map(o => o.record_id === "a" ? { ...o, doi_o: "10.1234/other" } : o) };
    await page.evaluate(p => { _pairBuffer = []; _showActivePair({ ...p }); }, PAIR);
    await pr('[data-pr="orig"][data-id="a"]').first().waitFor();
    assert.equal(await orig("a", "wrong").evaluate(el => el.classList.contains("sel")), false, "that answer was about the old paper");
    assert.equal(await orig("b", "correct").evaluate(el => el.classList.contains("sel")), true, "the others stand");
    assert.equal(await page.evaluate(() => Object.keys(state.paperReview.out).length), 0, "outcomes are asked again");
    claimed = SET;

    // A suggestion stays with a row an admin cannot remove from under it.
    await show(PAIR);
    await answerType();
    await allCorrect();
    await orig("a", "wrong").click();
    await answerChecks();
    await pr('[data-pr="draft-add"]').click();
    await pr('[data-pr-input="draft"][data-field="doi_or_url"]').fill("10.1234/another");
    await submit();
    assert.deepEqual(judged.filter(j => j.extra_originals?.length).map(j => j.record_id), ["b"]);
    judged.length = 0;

    // Enter submits the table, once it is ready, as it submits the one-record screen.
    await show(PAIR);
    await answerType();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Enter");
    assert.equal(judged.length, 0, "a table with questions left does not submit");
    await allCorrect();
    await answerChecks();
    await page.evaluate(() => { _pairShownAt = 0; document.activeElement?.blur(); });
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !state.paperReview);
    await page.waitForFunction(() => _myQueue().length === 0);
    assert.equal(judged.length, 5);
    judged.length = 0;

    // The speed guard is per original read, except for a paper rejected as a whole.
    await show(PAIR);
    await answerType();
    await allCorrect();
    await answerChecks();
    await page.evaluate(() => { _pairShownAt = Date.now() - 30_000; });
    await page.locator("#pr-submit").click();
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    assert.match(await page.locator("#dialog-message").textContent(), /under 85 seconds/);
    await page.locator("#dialog-buttons button", { hasText: "Read it again" }).click();
    assert.equal(judged.length, 0);
    await show(PAIR);
    await pr('[data-pr="type"][data-v="not_validation"]').click();
    await page.evaluate(() => { _pairShownAt = Date.now() - 20_000; });
    await page.locator("#pr-submit").click();
    await page.waitForFunction(() => !state.paperReview);
    await page.waitForFunction(() => _myQueue().length === 0);
    assert.equal(await page.locator("#dialog-modal").isHidden(), true);
    judged.length = 0;

    // Approving together clears every cached entry of the paper, not only the approved.
    const cached = await page.evaluate(async d => {
      document.querySelector("#test-admin").innerHTML = _adminPaperOriginals(d);
      showConfirm = async () => true;
      fetchAdminEntries = () => {};
      openAdminDetail = async () => {};
      for (const id of ["a", "b", "c", "current"]) _adminDetailCache[id] = { stale: true };
      _wireAdminPaperReview({ record_id: "current" }, d);
      document.querySelector("#admin-approve-paper-btn").click();
      await new Promise(r => setTimeout(r, 500));
      return Object.keys(_adminDetailCache);
    }, data);
    assert.deepEqual(cached, []);

    // A late assignment: the validator is told, and the screen moves on.
    await page.route("**/api/assignment-judge", route => route.fulfill({ status: 409, json: { detail: {
      code: "record_already_decided",
      message: "This entry has already been decided, so your answer was not saved and no points were added." } } }));
    await show(single);
    await page.evaluate(() => {
      state.assignment = { record_id: "s" };
      document.querySelector("#assignment-banner").classList.remove("hidden");
      document.querySelector("#submit-btn").disabled = false;
      Object.assign(state.judgement, { type: "replication", original: "correct", outcome: "correct" });
      submitJudgement();
    });
    await page.locator("#dialog-modal:not(.hidden)").waitFor();
    assert.match(await page.locator("#dialog-message").textContent(), /already been decided.*no points were added/);
    await page.locator("#dialog-buttons .btn-primary").click();
    await page.waitForFunction(() => state.assignment === null);
    assert.equal(await page.locator("#assignment-banner").isHidden(), true);

    // In the durable queue that answer closes the item instead of parking it.
    assert.equal(await page.evaluate(() => _slotNoLongerOwned(new Error(
      "This entry has already been decided, so your answer was not saved and no points were added."))), true);

    assert.deepEqual(errors, []);
    console.log("Paper review checks passed: table, merging, wrong paper, replacement, drafts, submit, neither, reproduction, fallback, one-record screen, add another, admin panel.");
  } catch (error) {
    console.error(String(error).slice(0, 1200));
    console.error("page errors:", errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
