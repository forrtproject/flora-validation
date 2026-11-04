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
  let claimStatus = 200;
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
      return claimStatus === 200 ? route.fulfill({ status: 200, json: SET })
        : route.fulfill({ status: claimStatus, json: { detail: "no" } });
    }
    if (url.pathname === "/api/judge") { judged.push(route.request().postDataJSON()); return route.fulfill({ status: 200, json: { points_earned: 2 } }); }
    if (url.pathname === "/api/skip") { skips.push(route.request().postDataJSON().record_id); return route.fulfill({ status: 200, json: { skipped: true, reason_recorded: true } }); }
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

    // Skipping the paper releases every open record.
    await show(PAIR);
    await answerType();
    await page.evaluate(() => { showSkipReasonDialog = async () => ({ reason_code: "prefer_another", comment: null }); });
    await page.locator("#skip-btn").click();
    await page.waitForFunction(() => !state.paperReview);
    assert.deepEqual([...skips].sort(), ["a", "b", "c", "d", "e"]);
    await page.locator("#done-screen").waitFor();

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
