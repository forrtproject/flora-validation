/* Run with: node tests/test_outcome_review_ui.cjs
 * All requests are intercepted; no server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10_000);
  const errors = [];
  const submissions = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/judge") {
      submissions.push(route.request().postDataJSON());
      return route.fulfill({ status: 200, json: { points_earned: 1 } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ status: 200, json: [] });
    if (url.pathname === "/api/next-pairs") return route.fulfill({ status: 200, json: { pairs: [] } });
    return route.fulfill({ status: 200, json: { records: [], points: 0, rank: 1 } });
  });
  const pair = {
    pair_id: "draft-review", record_id: "draft-review", queue_id: 1,
    type: "replication", outcome: "failed", title_r: "A validation study",
    title_o: "The original study", doi_r: "10.1234/replication", doi_o: "10.1234/original",
    abstract_r: "The analysis supports the original effect. The initial extraction was wrong.",
    outcome_phrase: "The initial extraction was wrong.",
    outcome_computation: null, outcome_robustness: null,
  };
  const showPair = data => page.evaluate(p => {
    document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
    document.querySelector("#game-screen").classList.remove("hidden");
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    state.coder = { coder_id: 1, handle: "Test validator" };
    state.assignment = null;
    state.mode = p.mode || "normal";
    _showActivePair(p);
  }, data);
  const restore = async (draft, changedPair) => {
    await page.evaluate(value => localStorage.setItem("flora.draft.draft-review", JSON.stringify(value)), draft);
    await page.reload();
    await showPair({ ...changedPair, resumed: true });
  };

  try {
    await page.goto("http://flora.test/");
    await page.clock.install();
    await showPair(pair);
    await page.locator('[data-type="replication"]').click();
    await page.locator('[data-original="correct"]').click();
    await page.locator('[data-outcome="wrong"]').click();
    await page.locator("#edit-quote-btn").click();
    await page.locator("#outcome-quote-edit").fill("The analysis supports the original effect.");
    await page.locator("#edit-quote-btn").click();
    await page.locator('[data-correct-outcome="successful"]').click();
    await page.locator("#note-toggle-btn").click();
    await page.locator("#pair-card .comment").fill("Keep this reasoning after reload.");
    assert.equal(await page.locator("#submit-btn").isEnabled(), true);
    await page.clock.fastForward(10_001);
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("flora.draft.draft-review")));
    assert.equal(saved.shown_record.type, "replication");
    assert.equal(saved.shown_record.outcome, "failed");
    assert.equal(saved.corrected_outcome, "successful");

    // Unchanged records preserve the correction and its original shown value
    // through a real reload, and the actual submission retains that intent.
    await restore(saved, pair);
    assert.equal(await page.locator("#submit-btn").isEnabled(), true);
    assert.match(await page.locator('[data-correct-outcome="successful"]').getAttribute("class"), /selected/);
    await page.clock.fastForward(18_000);
    const submitted = page.waitForRequest(request => new URL(request.url()).pathname === "/api/judge");
    await page.locator("#submit-btn").click();
    const payload = (await submitted).postDataJSON();
    assert.equal(payload.outcome_check, "incorrect");
    assert.equal(payload.corrected_outcome, "successful");
    assert.equal(payload.additional_checks.shown_outcome, "failed");
    assert.equal(payload.corrected_outcome_quote, saved.edited_outcome_quote);

    // A refreshed successful extraction must not convert the old correction
    // into agreement, even though the saved improved quote makes it submittable.
    await restore(saved, { ...pair, outcome: "successful" });
    assert.equal(await page.locator("#submit-btn").isDisabled(), true);
    assert.equal(await page.locator("#gate-3 .choice.selected").count(), 0);
    assert.equal(await page.locator("#pair-card .comment").inputValue(), saved.comment);
    assert.match(await page.locator("#outcome-quote-text").textContent(), /supports the original effect/);
    assert.equal(await page.evaluate(() => state.judgement.outcome), null);
    await page.evaluate(() => submitJudgement());
    assert.equal(submissions.length, 1, "Stale draft cannot silently submit a refreshed baseline");

    // The access report bypasses judgement gates, but unchecking it must never
    // revive stale answers that were present before the report was selected.
    await restore({ ...saved, no_access: true }, { ...pair, mode: "hard", outcome: "successful" });
    assert.equal(await page.locator("#no-access-cb").isChecked(), true);
    assert.equal(await page.locator("#submit-btn").isEnabled(), true);
    await page.locator("#no-access-cb").evaluate(checkbox => {
      checkbox.checked = false;
      checkbox.dispatchEvent(new Event("change"));
    });
    assert.equal(await page.locator("#submit-btn").isDisabled(), true);
    assert.equal(await page.evaluate(() => state.judgement.outcome), null);
    assert.equal(await page.locator("#pair-card .comment").inputValue(), saved.comment);
    assert.match(await page.locator("#outcome-quote-text").textContent(), /supports the original effect/);
    await page.evaluate(() => submitJudgement());
    assert.equal(submissions.length, 1, "Unchecking an access report cannot revive stale answers");

    // Old deployments did not persist a shown baseline. Those drafts need the
    // same conservative re-review, while preserving the validator's text.
    const legacy = { ...saved };
    delete legacy.shown_record;
    await restore(legacy, { ...pair, outcome: "successful" });
    assert.equal(await page.locator("#submit-btn").isDisabled(), true);
    assert.equal(await page.locator("#gate-1 .choice.selected").count(), 0);
    assert.equal(await page.locator("#pair-card .comment").inputValue(), saved.comment);

    // Type changes require an explicit new type judgement even if the flat
    // outcome string itself did not change.
    await restore(saved, { ...pair, type: "reproduction" });
    assert.equal(await page.locator("#submit-btn").isDisabled(), true);
    assert.equal(await page.locator("#gate-1 .choice.selected").count(), 0);

    const raw = {
      record_id: "assignment-review", type: "replication", outcome: "failed",
      title_o: "Raw original", doi_o: "10.1234/raw-original", title_r: "Raw replication title",
      url_r: "https://example.test/raw", abstract_r: "Raw abstract", outcome_quote: "Raw quote",
      outcome_computation: "technical failure", outcome_robustness: "robustness challenges",
      validation_status: "validated", validator_2: null,
    };
    const shown = {
      type: "reproduction", outcome: "cannot_be_determined",
      title_o: "Shown original", doi_o: "10.1234/shown-original", title_r: "Shown study title",
      url_r: "https://example.test/shown", abstract_r: "Shown abstract", outcome_quote: "Shown quote",
      outcome_computation: "computationally reproducible", outcome_robustness: "robust",
      outcome_computational_quote: "Shown computation evidence", out_quote_computational_source: "results",
      outcome_robustness_quote: "Shown robustness evidence", out_quote_robust_source: "discussion",
    };
    const judgement = {
      is_assignment: true, shown_record: shown, validator_name: "Reviewer",
      type_check: "correct", original_check: "correct", outcome_check: "correct",
      corrected_outcome_computation: "computationally reproducible", corrected_outcome_robustness: "robust",
      corrected_title_r: "Improved study title", corrected_abstract: "Improved abstract",
      corrected_url_r: "https://example.test/improved",
    };
    const review = record => page.evaluate(rec => {
      clearInterval(_draftInterval);
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#admin-detail-modal").classList.remove("hidden");
      renderAdminDetail({ record: rec });
    }, record);
    await review({ ...raw, validator_1: judgement });
    const card = page.locator("#admin-detail-body .admin-val-card").first();
    const text = await card.textContent();
    assert.match(text, /Type\s*reproduction\s*✓ Reviewer agreed/);
    assert.match(text, /Shown original/);
    assert.match(text, /Shown study title/);
    assert.match(text, /Shown abstract/);
    assert.match(text, /https:\/\/example\.test\/shown/);
    assert.match(text, /Shown computation evidence/);
    assert.match(text, /Shown robustness evidence/);
    assert.doesNotMatch(text, /Raw original|Raw replication title|Raw abstract|Raw quote/);
    assert.equal(await card.locator(".admin-axis-review").count(), 2);
    assert.match(await card.locator(".admin-axis-review").first().textContent(), /✓ Reviewer agreed/);
    assert.doesNotMatch(text, /technical failure|robustness challenges/i);

    await review({ ...raw, validator_1: {
      ...judgement, corrected_computational_quote: "", corrected_computational_source: "",
      corrected_robustness_quote: "", corrected_robustness_source: "",
    } });
    assert.doesNotMatch(await card.textContent(), /Shown computation evidence|Shown robustness evidence/);
    assert.equal(await card.locator(".chk-axis-evidence").count(), 0, "Explicitly cleared evidence does not fall back to the shown quote");

    // Corrections, quote edit classification and “was” values also use the
    // assignment snapshot, including a deliberately cleared original DOI.
    await review({ ...raw, validator_1: {
      ...judgement, shown_record: { ...shown, type: "replication", outcome: "successful", doi_o: "" },
      original_check: "incorrect", corrected_title_o: "A further original correction",
      corrected_doi_o: "10.1234/new-original", outcome_check: "incorrect", corrected_outcome: "failed",
      corrected_outcome_quote: "Shown quote!",
    } });
    const correctionText = await card.textContent();
    assert.match(correctionText, /was\s*Successful/i);
    assert.match(correctionText, /Shown original/);
    assert.doesNotMatch(correctionText, /10\.1234\/raw-original|10\.1234\/shown-original/);
    assert.equal(await card.locator(".admin-axis-review").count(), 0, "Replication cards do not show leftover reproduction axes");

    // Reverse direction: the raw record is reproduction, but the assignment
    // agreed with an effective replication and should show no reproduction axes.
    await review({ ...raw, type: "reproduction", validator_1: {
      ...judgement, shown_record: { ...shown, type: "replication", outcome: "successful" },
      corrected_outcome_quote: "Shown quote!",
    } });
    const reversed = await card.textContent();
    assert.match(reversed, /Type\s*replication\s*✓ Reviewer agreed/);
    assert.match(reversed, /Outcome\s*Successful\s*✓ Reviewer agreed/i);
    assert.match(reversed, /touched up the quote's punctuation/);
    assert.doesNotMatch(reversed, /improved the quote/);
    assert.equal(await card.locator(".admin-axis-review").count(), 0);

    // Legacy summaries retain the raw-record fallback.
    const oldJudgement = { ...judgement };
    delete oldJudgement.shown_record;
    await review({ ...raw, validator_1: oldJudgement });
    assert.match(await card.textContent(), /Type\s*replication\s*✓ Reviewer agreed/);
    assert.match(await card.textContent(), /Raw original/);
    assert.deepEqual(errors, []);
    console.log("Outcome review browser checks passed: persisted draft resume, stale/unknown/type baselines, submission intent, assignment audit baselines and legacy fallback.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
