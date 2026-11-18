/* Run with: node tests/test_adjudication_validator_ui.cjs
 * The validators' side of the adjudication feature (fred-data PR #143), phase 3:
 * the "⚖ … Observatory disagreements" bar for Trusted and Senior validators
 * (which must never push "Sign out" off the header), and the judging window
 * with both answers labelled by source, the two questions, the points, submit
 * and skip; double submits, late answers, Escape, Tab. Regular validators never
 * see or ask for any of it, and the game works as before when adjudication.js
 * cannot load.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const shots = process.env.FLORA_UI_SCREENSHOTS;

const DIFFERENT = {
  record_id: "11111111-1111-1111-1111-111111111111", kind: "different original",
  doi_r: "10.1234/rep-1", title_r: "Replicating the anchoring effect", year_r: "2019",
  abstract_r: "We replicated the anchoring effect (p < .05 and n > 300).", abstract_source: "europepmc",
  flora: { doi_o: "10.1234/orig-a", title_o: "Anchoring, the original", outcome: "failed",
    outcome_quote: "The effect did not replicate.", quote_source: "abstract",
    link_method: "llm_references", link_confidence: "high", link_evidence: "Cites Smith (2001) as the target." },
  observatory: { doi_o: "10.1234/orig-b", title_o: "Another original", outcome: "failure",
    replication_type: "direct", discipline: "psychology", confidence: "high" },
  flora_type: "replication", observatory_type: "replication", type_disputed: false, ask_type: false,
  original_choices: ["flora", "observatory", "both", "neither", "cannot_tell"],
  outcome_choices: ["successful", "failed", "mixed", "statistically successful but flawed", "uninformative",
    "descriptive only", "cannot_be_determined", "not_a_replication", "cannot_tell"],
  computation_choices: ["computationally reproducible", "computational issues", "technical failure",
    "not checked", "cannot_be_determined", "cannot_tell"],
  robustness_choices: ["robust", "robustness challenges", "not checked", "cannot_be_determined", "cannot_tell"],
  doi_required: false,
  // Both say failed (the Observatory's "failure"): that outcome starts selected.
  defaults: { original: null, type: "replication", outcome: "failed" },
};
// Asked the type, "neither" there is "not a replication".
const ASKED_OUTCOMES = DIFFERENT.outcome_choices.filter(v => v !== "not_a_replication");
const NONE = {
  ...DIFFERENT, record_id: "22222222-2222-2222-2222-222222222222", kind: "we found no original",
  title_r: "A <b>bold</b> replication", abstract_r: null, abstract_source: null,
  flora: { doi_o: null, outcome: "pending" },
  observatory: { doi_o: null, outcome: "success" },
  original_choices: ["neither", "cannot_tell"], doi_required: true,
  // FLoRA's outcome is pending: its type is unknown, so it is asked, and nothing is pre-selected.
  flora_type: null, ask_type: true, outcome_choices: ASKED_OUTCOMES,
  defaults: { original: null, type: null, outcome: null },
};
// FLoRA codes a reproduction where the Observatory sees a direct replication.
const SAME = {
  ...DIFFERENT, record_id: "33333333-3333-3333-3333-333333333333", kind: "same original, different outcome",
  flora: { ...DIFFERENT.flora, doi_o: "10.1234/orig-a", outcome: "computational issues, robustness challenges",
    outcome_computation: "computational issues", outcome_robustness: "robustness challenges" },
  observatory: { ...DIFFERENT.observatory, doi_o: "10.1234/orig-a", outcome: "failure" },
  original_choices: ["both", "neither", "cannot_tell"],
  flora_type: "reproduction", type_disputed: true, ask_type: true, outcome_choices: ASKED_OUTCOMES,
  // They disagree about the type, but both name the same original: only that is pre-selected.
  defaults: { original: "both", type: null, outcome: null },
};
// The same original, and the Observatory's "reversal" is FLoRA's "failed".
const AGREED = {
  ...DIFFERENT, record_id: "44444444-4444-4444-4444-444444444444", kind: "same original, different outcome",
  flora: { ...DIFFERENT.flora, doi_o: "10.1234/orig-a" },
  observatory: { ...DIFFERENT.observatory, doi_o: "10.1234/orig-a", outcome: "reversal" },
  original_choices: ["both", "neither", "cannot_tell"],
  defaults: { original: "both", type: "replication", outcome: "failed" },
};

function serveFiles({ delayScript = 0, scriptMissing = false } = {}) {
  return async (route, url) => {
    const files = {
      "/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
      "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css",
    };
    if (url.pathname === "/adjudication.js" && scriptMissing) return route.fulfill({ status: 404, body: "" }), true;
    if (url.pathname === "/adjudication.js" && delayScript) await new Promise(r => setTimeout(r, delayScript));
    if (!files[url.pathname]) return false;
    const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
    await route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    return true;
  };
}

// The game screen with a signed-in validator; `api` answers /api/disagreements/*.
async function openGame(browser, { tier = 1, api = {}, viewport = { width: 1280, height: 900 }, me = null, ...files } = {}) {
  const page = await browser.newPage({ viewport, locale: "en-US" });
  page.setDefaultTimeout(10_000);
  const errors = [];
  const calls = [];
  page.on("pageerror", error => errors.push(error.message));
  const serve = serveFiles(files);
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    if (await serve(route, url)) return;
    if (url.pathname.startsWith("/api/disagreements/")) {
      const request = route.request();
      const name = url.pathname.replace("/api/disagreements/", "").replace(/^[0-9a-f-]{36}\//, "");
      calls.push({ name, path: url.pathname, body: request.method() === "POST" ? request.postDataJSON() : null });
      const answer = api[name] ? await api[name](request.postDataJSON?.(), url) : { available: false };
      if (answer && answer.status) return route.fulfill({ status: answer.status, json: answer.json });
      return route.fulfill({ json: answer });
    }
    if (url.pathname === "/api/me") return route.fulfill({ json: me || { kind: null } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ json: [] });
    if (url.pathname === "/api/next-pairs") return route.fulfill({ json: { pairs: [] } });
    if (url.pathname === "/api/messages") return route.fulfill({ json: [] });
    return route.fulfill({ json: { records: [], points: 0, rank: 1, assignments: [] } });
  });
  await page.goto("http://flora.test/");
  if (!me) {
    await page.evaluate(t => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#game-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      document.querySelector("#mode-toggle")?.classList.remove("hidden");    // as enterGame shows it
      state.coder = { coder_id: 7, handle: "sam", validator_tier: t, onboarded: true };
    }, tier);
  }
  return { page, errors, calls };
}

// "Sign out" must stay on screen: the header has no room for another button.
async function signOutFits(page) {
  return page.evaluate(() => {
    const box = document.querySelector("#logout-btn").getBoundingClientRect();
    return box.width > 0 && box.right <= window.innerWidth;
  });
}

const later = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    // A regular validator: never asks, never sees the button.
    {
      const { page, errors, calls } = await openGame(browser, { tier: 0, api: { summary: () => ({ available: true, left: 5 }) } });
      await page.evaluate(() => window.Adjudication.initValidator());
      assert.deepEqual(calls, []);
      assert.equal(await page.locator("#adj-validator-bar").count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Trusted, nothing left: the button stays hidden.
    {
      const { page, errors } = await openGame(browser, { api: { summary: () => ({ available: true, left: 0, judged: 3 }) } });
      await page.evaluate(() => window.Adjudication.initValidator());
      assert.equal(await page.locator("#adj-validator-bar").count(), 1);
      assert.equal(await page.locator("#adj-validator-bar").isHidden(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // The whole flow: open, judge one, suggest a DOI and say the type on the next,
    // judge a reproduction on its two axes on the third, find the fourth
    // pre-selected and skip it, then the done message.
    {
      const queue = [DIFFERENT, NONE, SAME, AGREED, null];
      let left = 4, judged = 0;
      const api = {
        summary: () => ({ available: true, left, judged }),
        next: () => ({ record: queue[0], points_base: 10, left, judged }),
        submit: () => { queue.shift(); left -= 1; judged += 1; return { points: 15, total_points: 115, record_complete: false, left, judged }; },
        skip: () => { queue.shift(); left -= 1; return { left, judged }; },
      };
      const { page, errors, calls } = await openGame(browser, { api });
      await page.evaluate(() => window.Adjudication.initValidator());
      const button = page.locator("#adj-validator-btn");
      await button.waitFor();
      assert.match(await page.locator("#adj-validator-bar").innerText(), /4 Observatory disagreements left for you to judge/);
      // At the top of the work area, not in the header: "Sign out" stays on screen.
      assert.equal(await page.evaluate(() =>
        document.querySelector("#game-screen .game-area").firstElementChild.id), "adj-validator-bar");
      for (const width of [1280, 1180, 900, 800]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(await signOutFits(page), `Sign out fits at ${width}px`);
      }
      await page.setViewportSize({ width: 1280, height: 900 });

      await button.click();
      const body = page.locator("#adj-judge-body");
      await body.locator(".adj-side-flora").waitFor();
      assert.match(await page.locator("#adj-judge-progress").innerText(), /4 left · 0 judged by you/);
      assert.equal(await body.locator(".adj-side-name").first().innerText(), "FLoRA");
      assert.equal(await body.locator(".adj-side-name").nth(1).innerText(), "Metascience Observatory");
      assert.match(await body.locator(".adj-side-flora").innerText(), /The effect did not replicate\.[\s\S]*Quoted from the abstract/);
      assert.match(await body.locator(".adj-side-mo").innerText(), /Failure[\s\S]*direct replication · psychology · high confidence/);
      assert.match(await body.locator(".adj-abstract").innerText(), /p < \.05 and n > 300/);
      assert.equal(await body.locator("#adj-rep-label").innerText(), "THE REPLICATION");
      assert.deepEqual(await body.locator('.adj-option[data-group="original"]').allInnerTexts(),
        ["FLoRA's original", "The Observatory's original", "Both (it replicates both)", "Neither: suggest the right one", "Can't tell"]);
      // Two questions: the type is not asked where FLoRA and the Observatory agree on it.
      assert.deepEqual(await body.locator(".adj-q > legend").allInnerTexts(),
        ["1. Which original is right?", "2. What did the replication find?"]);
      assert.equal(await body.locator(".adj-chip-type").count(), 0);
      // The outcome both give starts selected, says so, and carries FLoRA's tag.
      const failed = body.locator('.adj-option[data-group="outcome"][data-value="failed"]');
      assert.equal(await failed.getAttribute("aria-pressed"), "true");
      assert.equal(await failed.locator(".adj-flora-tag").innerText(), "FLoRA");
      assert.equal(await body.locator(".adj-flora-tag").count(), 1);
      assert.deepEqual(await body.locator(".adj-agree").allInnerTexts(), ["FLoRA and the Observatory agree"]);
      assert.equal(await body.locator('.adj-option[data-group="original"][aria-pressed="true"]').count(), 0);
      assert.equal(await body.locator(".adj-option", { hasText: "As FLoRA" }).count(), 0);
      const submit = page.locator("#adj-submit-btn");
      assert.equal(await submit.isDisabled(), true);
      assert.equal(await page.locator("#adj-worth").innerText(), "Answer both questions");
      await body.locator('.adj-option[data-value="flora"]').click();
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 14 points", "a pre-selected answer earns as any other");
      await failed.click();
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 14 points");
      await page.locator("#adj-note").fill("Checked the reference list.");
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 15 points");
      // Tab stays inside the window.
      await page.locator("#adj-submit-btn").focus();
      await page.keyboard.press("Tab");
      assert.ok(await page.evaluate(() => document.querySelector("#adj-judge-modal").contains(document.activeElement)));
      // Escape in the note keeps the window and what was typed; elsewhere it closes.
      await page.locator("#adj-note").press("Escape");
      assert.equal(await page.locator("#adj-judge-modal").isVisible(), true);
      assert.equal(await page.locator("#adj-note").inputValue(), "Checked the reference list.");
      assert.equal(await body.locator('.adj-option[data-value="flora"]').getAttribute("aria-pressed"), "true");
      if (shots) {
        await fs.mkdir(shots, { recursive: true });
        await page.screenshot({ path: path.join(shots, "judging-desktop.png"), fullPage: false });
      }
      await submit.click();

      // The toast shows above the window, and the next record says it was saved.
      await page.locator("#toast.show").waitFor();
      assert.ok(await page.evaluate(() => {
        const box = document.querySelector("#toast").getBoundingClientRect();
        return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest("#toast") !== null;
      }), "the toast is not hidden behind the window");

      // The second record: no original on either side.
      await body.locator(".adj-none").first().waitFor();
      assert.equal(await body.locator(".adj-notice").innerText(), "Saved: +15 points.");
      const submitted = calls.find(c => c.name === "submit");
      assert.equal(submitted.path, `/api/disagreements/${DIFFERENT.record_id}/submit`);
      assert.deepEqual(submitted.body, { original_choice: "flora", suggested_doi_o: null, type: "replication",
        outcome: "failed", outcome_computation: null, outcome_robustness: null, note: "Checked the reference list." });
      assert.equal(await page.locator("#stat-points").innerText(), "115");
      assert.equal(await body.locator(".adj-none").count(), 2);
      assert.match(await body.locator(".adj-side-flora").innerText(), /We couldn't find the original\.\s*Suggest one/);
      assert.match(await body.locator(".adj-side-mo").innerText(), /The Observatory couldn't find the original\.\s*Suggest one/);
      assert.equal(await body.locator(".adj-rep h3 b").count(), 0, "titles are escaped");
      assert.match(await body.locator(".adj-rep h3").innerText(), /A <b>bold<\/b> replication/);
      assert.match(await body.innerText(), /No abstract was found/);
      assert.deepEqual(await body.locator('.adj-option[data-group="original"]').allInnerTexts(),
        ["I found it: give the DOI", "I can't find it either"]);
      assert.equal(await page.locator("#adj-doi-row").isHidden(), true);
      await body.locator(".adj-side-flora [data-suggest]").click();
      assert.equal(await page.locator("#adj-doi-row").isVisible(), true);
      assert.equal(await page.evaluate(() => document.activeElement.id), "adj-doi");
      assert.equal(await body.locator('.adj-option[data-value="neither"]').getAttribute("aria-pressed"), "true");
      // FLoRA's outcome is pending, so its type is unknown: asked, with nothing pre-selected.
      assert.deepEqual(await body.locator(".adj-q > legend").allInnerTexts(),
        ["1. Which original is right?", "2. Is it a replication or a reproduction?", "3. What did it find?"]);
      assert.equal(await body.locator(".adj-agree").count(), 0);
      assert.equal(await body.locator("#adj-rep-label").innerText(), "THE PAPER");
      assert.equal(await page.locator("#adj-worth").innerText(), "Answer all three questions");
      await body.locator('.adj-option[data-group="type"][data-value="replication"]').click();
      assert.equal(await body.locator("#adj-rep-label").innerText(), "THE REPLICATION");
      assert.equal(await body.locator('.adj-option[data-group="outcome"][data-value="not_a_replication"]').count(), 0,
        "\"not a replication\" is the type question's \"neither\"");
      await body.locator('.adj-option[data-group="outcome"][data-value="mixed"]').click();
      assert.equal(await submit.isDisabled(), true, "the DOI is required when no side has one");
      await page.locator("#adj-doi").fill("10.5555/found");
      assert.equal(await submit.isEnabled(), true);
      await submit.click();

      // The third: FLoRA's reproduction against the Observatory's replication.
      await body.locator('.adj-option[data-value="both"]').waitFor();
      assert.deepEqual(calls.filter(c => c.name === "submit")[1].body,
        { original_choice: "neither", suggested_doi_o: "10.5555/found", type: "replication", outcome: "mixed",
          outcome_computation: null, outcome_robustness: null, note: null });
      assert.equal(await body.locator(".adj-chip-type").innerText(), "FLoRA: reproduction · Observatory: direct replication");
      assert.equal(await body.locator(".adj-q legend").first().innerText(), "1. Is this the right original?");
      assert.equal(await body.locator('.adj-option[data-value="both"]').innerText(), "Yes, this is the original");
      assert.deepEqual(await body.locator('.adj-option[aria-pressed="true"]').allInnerTexts(),
        ["Yes, this is the original"], "only the shared original is pre-selected");
      assert.deepEqual(await body.locator(".adj-agree").allInnerTexts(), ["FLoRA and the Observatory agree"]);
      assert.equal(await body.locator(".adj-option", { hasText: "As FLoRA" }).count(), 0);
      assert.deepEqual(await body.locator('.adj-option[data-group="type"]').allInnerTexts(),
        ["Replication (new data)", "Reproduction (same data)", "Neither: not in FLoRA's scope"]);
      await body.locator('.adj-option[data-value="both"]').click();
      // Chosen with the keyboard: the outcome question becomes the reproduction's two axes.
      await body.locator('.adj-option[data-group="type"][data-value="reproduction"]').focus();
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => document.activeElement.dataset.value), "reproduction", "focus stays put");
      assert.equal(await body.locator("#adj-rep-label").innerText(), "THE REPRODUCTION");
      assert.equal(await body.locator("#adj-outcome-q .adj-q > legend").innerText(), "3. What did the reproduction find?");
      assert.deepEqual(await body.locator(".adj-axis > legend").allInnerTexts(), ["Computation", "Robustness"]);
      assert.deepEqual(await body.locator('.adj-option[data-group="outcome_computation"]').allInnerTexts(),
        ["Reproducible", "Issues FLoRA", "Technical failure", "Not checked", "Cannot be determined", "Can't tell"]);
      assert.deepEqual(await body.locator('.adj-option[data-group="outcome_robustness"]').allInnerTexts(),
        ["Robust", "Challenges FLoRA", "Not checked", "Cannot be determined", "Can't tell"]);
      assert.equal(await body.locator('#adj-outcome-q .adj-option[aria-pressed="true"]').count(), 0,
        "FLoRA's answer is tagged, not chosen");
      assert.equal(await submit.isDisabled(), true, "both axes are needed");
      await body.locator('.adj-option[data-group="outcome_computation"][data-value="computational issues"]').click();
      await body.locator('.adj-option[data-group="outcome_robustness"][data-value="robust"]').focus();
      await page.keyboard.press("Space");
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 14 points", "1 point for each decided axis");
      await body.locator('.adj-option[data-group="outcome_robustness"][data-value="cannot_tell"]').click();
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 13 points");
      // Back to a replication and again: the axes are kept.
      await body.locator('.adj-option[data-group="type"][data-value="replication"]').click();
      assert.equal(await body.locator("#adj-outcome-q .adj-q > legend").innerText(), "3. What did the replication find?");
      assert.equal(await submit.isDisabled(), true);
      await body.locator('.adj-option[data-group="type"][data-value="reproduction"]').click();
      assert.equal(await body.locator('.adj-option[data-group="outcome_computation"][data-value="computational issues"]')
        .getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 13 points");
      if (shots) await page.screenshot({ path: path.join(shots, "judging-reproduction.png"), fullPage: false });
      await submit.click();

      // The fourth: what FLoRA and the Observatory agree on starts selected; it can still change.
      // (The third had one agree note, for its original; this one has two.)
      await body.locator(".adj-agree").nth(1).waitFor();
      assert.deepEqual(calls.filter(c => c.name === "submit")[2].body,
        { original_choice: "both", suggested_doi_o: null, type: "reproduction", outcome: null,
          outcome_computation: "computational issues", outcome_robustness: "cannot_tell", note: null });
      assert.deepEqual(await body.locator(".adj-agree").allInnerTexts(),
        ["FLoRA and the Observatory agree", "FLoRA and the Observatory agree"]);
      assert.deepEqual(await body.locator('.adj-option[aria-pressed="true"]').evaluateAll(els => els.map(e => e.dataset.value)),
        ["both", "failed"]);
      assert.equal(await submit.isEnabled(), true);
      assert.equal(await page.locator("#adj-worth").innerText(), "Worth 14 points");
      await body.locator('.adj-option[data-group="outcome"][data-value="mixed"]').click();
      assert.equal(await body.locator('.adj-option[data-value="failed"]').getAttribute("aria-pressed"), "false");
      assert.equal(await body.locator('.adj-option[data-value="mixed"]').getAttribute("aria-pressed"), "true");
      await page.locator("#adj-skip-btn").click();

      await body.locator(".adj-judge-message h3").waitFor();
      assert.equal(calls.filter(c => c.name === "skip")[0].path, `/api/disagreements/${AGREED.record_id}/skip`);
      assert.match(await body.innerText(), /Nothing left for you right now[\s\S]*You have judged 3/);
      const summaries = calls.filter(c => c.name === "summary").length;
      await page.locator("#adj-done-btn").click();
      assert.equal(await page.locator("#adj-judge-modal").isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement.id), "adj-validator-btn", "focus goes back to the opener");
      await page.locator("#adj-validator-bar").waitFor({ state: "hidden" });
      assert.ok(calls.filter(c => c.name === "summary").length > summaries, "closing refreshes the count");
      assert.deepEqual(errors, []);
      await page.close();
    }

    // Refusals: a 422 is shown and the answer kept; a 409 moves on to the next record.
    {
      let nexts = 0;
      let refusal = { status: 422, json: { detail: "That does not look like a DOI (10.xxxx/…)" } };
      const api = {
        summary: () => ({ available: true, left: 2, judged: 0 }),
        next: () => { nexts += 1; return { record: nexts === 1 ? DIFFERENT : SAME, points_base: 10, left: 2, judged: 0 }; },
        submit: () => refusal,
      };
      const { page, errors } = await openGame(browser, { api });
      await page.evaluate(() => window.Adjudication.initValidator());
      await page.locator("#adj-validator-btn").click();
      const body = page.locator("#adj-judge-body");
      await body.locator('.adj-option[data-value="neither"]').click();
      await page.locator("#adj-doi").fill("nonsense");
      await body.locator('.adj-option[data-group="outcome"][data-value="failed"]').click();
      await page.locator("#adj-submit-btn").click();
      await page.locator("#adj-judge-error:not(:empty)").waitFor();
      assert.match(await page.locator("#adj-judge-error").innerText(), /does not look like a DOI/);
      assert.equal(await page.locator("#adj-submit-btn").isEnabled(), true);
      assert.equal(await page.locator("#adj-doi").inputValue(), "nonsense");
      assert.equal(nexts, 1);

      refusal = { status: 409, json: { detail: "Two other validators judged this record meanwhile" } };
      await page.locator("#adj-submit-btn").click();
      await body.locator(".adj-q legend", { hasText: "Is this the right original?" }).waitFor();
      assert.equal(nexts, 2);
      assert.match(await body.locator(".adj-notice").innerText(),
        /Two other validators judged this record meanwhile\. Your answer to it was not saved/);
      await page.locator("#adj-judge-modal .adj-side-name").first().click();
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#adj-judge-modal").isHidden(), true, "Escape closes after a mouse click");
      assert.deepEqual(errors, []);
      await page.close();
    }

    // While a submit is on its way, nothing else starts: one POST, Skip disabled,
    // the answer cannot change under it. Closing meanwhile claims nothing new.
    {
      let release;
      const answers = [];
      let nexts = 0;
      const api = {
        summary: () => ({ available: true, left: 2, judged: 0 }),
        next: () => { nexts += 1; return { record: DIFFERENT, points_base: 10, left: 2, judged: 0 }; },
        submit: body => { answers.push(body); return new Promise(resolve => { release = resolve; }); },
        skip: () => ({ left: 1, judged: 0 }),
      };
      const { page, errors, calls } = await openGame(browser, { api });
      await page.evaluate(() => window.Adjudication.initValidator());
      await page.locator("#adj-validator-btn").click();
      const body = page.locator("#adj-judge-body");
      await body.locator('.adj-option[data-value="flora"]').click();
      await body.locator('.adj-option[data-group="outcome"][data-value="failed"]').click();
      await page.locator("#adj-submit-btn").click();
      await page.waitForFunction(() => document.querySelector("#adj-submit-btn").textContent === "Saving…");
      await body.locator('.adj-option[data-group="outcome"][data-value="mixed"]').click();
      assert.equal(await body.locator('.adj-option[data-value="failed"]').getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#adj-submit-btn").isDisabled(), true);
      assert.equal(await page.locator("#adj-skip-btn").isDisabled(), true);
      await page.locator("#adj-note").type("x");
      assert.equal(await page.locator("#adj-submit-btn").isDisabled(), true, "typing does not re-enable Submit");
      assert.equal(answers.length, 1);
      release({ points: 15, total_points: 15, record_complete: false, left: 1, judged: 1 });
      await body.locator(".adj-notice").waitFor();
      assert.equal(answers.length, 1);
      assert.deepEqual(answers[0], { original_choice: "flora", suggested_doi_o: null, type: "replication",
        outcome: "failed", outcome_computation: null, outcome_robustness: null, note: null });

      // Close while the next submit is on its way, then let it finish.
      await body.locator('.adj-option[data-value="flora"]').click();
      await body.locator('.adj-option[data-group="outcome"][data-value="failed"]').click();
      await page.locator("#adj-submit-btn").click();
      await page.waitForFunction(() => document.querySelector("#adj-submit-btn").textContent === "Saving…");
      const nextsBefore = nexts;
      await page.locator("#adj-judge-close").click();
      release({ points: 15, total_points: 30, record_complete: false, left: 0, judged: 2 });
      await later(300);
      assert.equal(nexts, nextsBefore, "no record is claimed in a closed window");
      assert.equal(await page.locator("#stat-points").innerText(), "30", "the saved points still count");
      assert.equal(calls.filter(c => c.name === "submit").length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone: the bar wraps in the work area and the two answers stack.
    {
      const api = {
        summary: () => ({ available: true, left: 1, judged: 0 }),
        next: () => ({ record: { ...DIFFERENT, doi_r: "10.1234/" + "a-very-long-identifier-".repeat(4),
          observatory: { ...DIFFERENT.observatory, doi_o: "https://www.semanticscholar.org/paper/640fcb" + "0".repeat(40) } },
          points_base: 10, left: 1, judged: 0 }),
      };
      const { page, errors } = await openGame(browser, { api, viewport: { width: 390, height: 844 } });
      await page.evaluate(() => window.Adjudication.initValidator());
      const button = page.locator("#adj-validator-btn");
      await button.waitFor();
      assert.equal(await button.isVisible(), true);
      assert.ok(await signOutFits(page), "Sign out fits on a phone");
      await button.click();
      await page.locator(".adj-side-mo").waitFor();
      // Long links break instead of running off the screen.
      assert.ok(await page.evaluate(() => {
        const body = document.querySelector("#adj-judge-body");
        return body.scrollWidth <= body.clientWidth + 1;
      }), "nothing in the window is wider than the window");
      const [flora, mo] = await Promise.all([page.locator(".adj-side-flora").boundingBox(), page.locator(".adj-side-mo").boundingBox()]);
      assert.ok(mo.y >= flora.y + flora.height, "stacked on a phone");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "no sideways scroll");
      // A web link is linked as itself, not as a DOI.
      assert.equal(await page.locator(".adj-side-mo a").getAttribute("href"),
        "https://www.semanticscholar.org/paper/640fcb" + "0".repeat(40));
      if (shots) await page.screenshot({ path: path.join(shots, "judging-phone.png"), fullPage: false });
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A reload with a Trusted validator signed in, where adjudication.js arrives
    // after app.js has restored the session: the button must still appear.
    {
      const me = { kind: "validator", validator: { coder_id: 7, handle: "sam", validator_tier: 2, onboarded: true, last_seen_update: 99, update_version: 1 } };
      const { page } = await openGame(browser, {
        me, delayScript: 1200, api: { summary: () => ({ available: true, left: 4, judged: 0 }) },
      });
      await page.locator("#game-screen").waitFor();
      await page.locator("#adj-validator-btn").waitFor({ timeout: 5000 });
      assert.match(await page.locator("#adj-validator-bar").innerText(), /4 Observatory disagreements/);
      await page.close();
    }

    // adjudication.js cannot be loaded: the game starts exactly as before.
    {
      const me = { kind: "validator", validator: { coder_id: 7, handle: "sam", validator_tier: 2, onboarded: true, last_seen_update: 99, update_version: 1 } };
      const { page, errors } = await openGame(browser, { me, scriptMissing: true });
      await page.locator("#game-screen").waitFor();
      assert.equal(await page.evaluate(() => typeof window.Adjudication), "undefined");
      assert.equal(await page.locator("#adj-validator-bar").count(), 0);
      assert.equal(await page.locator("#stat-name").innerText(), "sam");
      assert.deepEqual(errors.filter(e => /Adjudication/.test(e)), []);
      await page.close();
    }
    console.log("adjudication validator UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
