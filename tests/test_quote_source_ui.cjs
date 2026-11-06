/* Run with: node tests/test_quote_source_ui.cjs
 * The quote source fields of the admin resolve form: no "Auto-detect" to pick, the
 * system's suggestion shown in the field and kept in step with the quote and abstract,
 * a stored source never replaced, the hint when a stored one is contradicted, what is
 * sent (null for an untouched suggestion, so nobody is credited with it), the Final
 * Preview, the reproduction axes, which the server does not detect on resolve, and a
 * drafted original's own outcome, where it does.
 * All requests are intercepted; no server, database, or remote service runs. That the
 * suggestion is what the server detects is tests/test_quote_source.py's job.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const XSS = '<img src=x onerror="window.__xss=1">';
const RID = "11111111-1111-4111-8111-111111111111";
const ABSTRACT = "We ran it again. In the end it failed, plainly, in every sample.";
const IN_ABSTRACT = "It failed — plainly.";     // the punctuation differs, as validators' edits do
const NOT_IN_ABSTRACT = "The methods section says something else entirely.";
const IN_THE_ABSTRACT_HINT = "The quote appears in the abstract.";
const NOT_IN_THE_ABSTRACT_HINT = "The quote is not in the abstract, so it is taken as coming from the full text.";

const entry = (over = {}) => ({
  record_id: RID, origin: "extractor", validation_status: "need_review", admin_checked: false,
  type: "replication", outcome: "failed", title_r: "A replication", doi_r: "10.5555/r",
  title_o: "The original", doi_o: "10.5555/o", abstract_r: ABSTRACT, outcome_quote: IN_ABSTRACT,
  out_quote_source: null, final_out_quote_source: null, out_quote_source_by: null, ...over,
});

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });

  const FILES = {
    "/": ["text/html", "index.html"],
    "/app.js": ["text/javascript", "app.js"],
    "/style.css": ["text/css", "style.css"],
    "/adjudication.js": ["text/javascript", "adjudication.js"],
    "/adjudication.css": ["text/css", "adjudication.css"],
  };
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    if (FILES[url.pathname]) {
      const [contentType, file] = FILES[url.pathname];
      return route.fulfill({ status: 200, contentType, body: await fs.readFile(path.join(root, "docs", file)) });
    }
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    if (url.pathname.endsWith("/resolve")) return route.fulfill({ json: { resolved: true, rejected: false, record_id: RID, extra_originals_created: [] } });
    if (url.pathname.endsWith("/extra-originals")) return route.fulfill({ json: {
      parent: { record_id: RID, origin: "extractor", validation_status: "need_review", admin_checked: false, can_add: false,
                published: null, mode: "draft", mode_code: null, mode_reason: null, missing_judgements: [],
                current: { study_r: "1", abstract_r: ABSTRACT, type: "replication" } },
      parent_record_id: null, children: [], suggestions: [],
    } });
    return route.fulfill({ json: { records: [], entries: [], counts: {}, total: 0 } });
  });

  const review = (rec) => page.evaluate((r) => {
    document.querySelector("#admin-detail-modal").classList.remove("hidden");
    renderAdminDetail({ record: r, extra_originals_summary: { open_suggestions: 0, children: 0 } });
  }, rec);
  const source = page.locator("#ar-quote-source");
  const hint = page.locator("#ar-quote-source-hint");
  const quote = page.locator("#ar-quote");
  const abstract = page.locator("#ar-abstract-r");
  const label = (select) => select.evaluate((s) => s.selectedOptions[0].textContent);
  const labels = (select) => select.evaluate((s) => [...s.options].map((o) => o.textContent));
  const marked = async () => (await labels(source)).filter((text) => text.includes("(suggested)"));
  const preview = () => page.locator(".final-preview-card .fp-row", { hasText: "Quote source" }).locator(".fp-value");
  // Mark as Resolved, and what it sent.
  const resolve = async () => {
    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith("/resolve")),
      page.locator("#admin-resolve-btn").click(),
    ]);
    await page.locator("#admin-detail-modal").waitFor({ state: "hidden" });
    return request.postDataJSON();
  };
  const noHint = async () => assert.equal(await hint.isVisible(), false);

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      _adminHandle = "me";
    });

    // ---- Nothing stored, and the quote is in the abstract: suggested, not a thing to pick.
    await review(entry());
    assert.equal(await source.inputValue(), "abstract");
    assert.equal(await label(source), "Abstract (suggested)");
    assert.deepEqual(await marked(), ["Abstract (suggested)"]);
    assert.equal(await source.locator('option[value=""]').count(), 0, "a quote to place has no blank");
    assert.equal(await hint.textContent(), IN_THE_ABSTRACT_HINT);
    assert.doesNotMatch(await page.locator("#admin-detail-body").textContent(), /auto-?detect/i);
    assert.equal(await preview().textContent(), "Abstract (suggested)");
    assert.deepEqual((await labels(source)).slice(0, 3), ["Abstract (suggested)", "Full text", "Introduction"]);
    assert.equal((await labels(source)).filter((text) => text === "Full text").length, 1, "one Full text");

    // Left alone it is not the admin's choice: null, so the server detects and credits nobody.
    assert.equal((await resolve()).out_quote_source, null);

    // ---- Not in the abstract: suggested as full text.
    await review(entry({ outcome_quote: NOT_IN_ABSTRACT }));
    assert.equal(await source.inputValue(), "full_text");
    assert.equal(await label(source), "Full text (suggested)");
    assert.equal(await hint.textContent(), NOT_IN_THE_ABSTRACT_HINT);
    assert.equal(await preview().textContent(), "Full text (suggested)");

    // ---- It follows the quote and the abstract while nobody has chosen.
    await quote.fill(IN_ABSTRACT);
    assert.equal(await source.inputValue(), "abstract");
    assert.deepEqual(await marked(), ["Abstract (suggested)"]);
    assert.equal(await hint.textContent(), IN_THE_ABSTRACT_HINT);
    await abstract.fill("Some other abstract.");
    assert.equal(await source.inputValue(), "full_text");
    assert.deepEqual(await marked(), ["Full text (suggested)"]);
    assert.equal(await hint.textContent(), NOT_IN_THE_ABSTRACT_HINT);
    await abstract.fill("Some other abstract, where it failed plainly.");
    assert.equal(await source.inputValue(), "abstract");
    assert.equal((await resolve()).out_quote_source, null, "still nobody's choice");

    // ---- A blank is not saved as blank, so it reads the stored quote and abstract as the handler does.
    await review(entry());
    await abstract.fill("");
    assert.equal(await source.inputValue(), "abstract");
    await abstract.fill("Some other abstract.");
    assert.equal(await source.inputValue(), "full_text");
    await quote.fill("");
    assert.equal(await source.inputValue(), "full_text", "the stored quote against the edited abstract");

    // ---- A validators' proposal is what the form holds, so it is what is checked.
    await review(entry({ outcome_quote: NOT_IN_ABSTRACT, validator_1: { corrected_outcome_quote: IN_ABSTRACT } }));
    assert.equal(await quote.inputValue(), IN_ABSTRACT);
    assert.equal(await source.inputValue(), "abstract");
    await review(entry({ validator_1: { corrected_abstract: "A corrected abstract, longer than the one extracted so it wins." } }));
    assert.equal(await source.inputValue(), "full_text");

    // ---- Choosing ends the suggesting, and the choice is sent.
    await review(entry());
    await source.selectOption("results");
    await noHint();
    assert.deepEqual(await marked(), []);
    assert.equal(await label(source), "Results");
    await quote.fill(NOT_IN_ABSTRACT);
    await abstract.fill("Another abstract.");
    assert.equal(await source.inputValue(), "results", "live edits leave a choice alone");
    await noHint();
    assert.equal((await resolve()).out_quote_source, "results");

    // Choosing what the suggestion would not have said, and its own value after moving off it.
    await review(entry());
    await source.selectOption("full_text");
    assert.equal((await resolve()).out_quote_source, "full_text");
    await review(entry());
    await source.selectOption("results");
    await source.selectOption("abstract");
    assert.equal((await resolve()).out_quote_source, "abstract", "picked is picked");

    // ---- No quote: the one case for a blank, and nothing to suggest or send.
    await review(entry({ outcome_quote: null }));
    assert.equal(await source.inputValue(), "");
    assert.equal(await label(source), "— no quote yet —");
    await noHint();
    assert.equal(await page.locator(".final-preview-card .fp-row", { hasText: "Quote source" }).count(), 0);
    await quote.fill(NOT_IN_ABSTRACT);
    assert.equal(await source.inputValue(), "full_text");
    assert.equal(await source.locator('option[value=""]').count(), 0);
    await quote.fill("...");
    assert.equal(await source.inputValue(), "", "punctuation is no quote");
    assert.equal((await resolve()).out_quote_source, null);

    await review(entry({ outcome_quote: "..." }));
    assert.equal(await preview().textContent(), "not set");

    // ---- A stored source stays, and goes as it does today.
    await review(entry({ out_quote_source: "abstract" }));
    assert.equal(await source.inputValue(), "abstract");
    assert.equal(await label(source), "Abstract");
    await noHint();
    assert.deepEqual(await marked(), []);
    assert.equal(await preview().textContent(), "Abstract");
    assert.equal((await resolve()).out_quote_source, "abstract");

    await review(entry({ out_quote_source: "abstract", final_out_quote_source: "full_text", out_quote_source_by: "cy" + XSS }));
    assert.equal(await source.inputValue(), "full_text", "the final value wins");
    assert.match(await preview().textContent(), /^Full text · set by cy<img/);

    // ---- A stored abstract or full text the quote contradicts: kept, flagged, one click away.
    await review(entry({ outcome_quote: NOT_IN_ABSTRACT, out_quote_source: "abstract" }));
    assert.equal(await source.inputValue(), "abstract");
    assert.equal(await hint.isVisible(), true);
    assert.match(await hint.textContent(), /^This entry says Abstract, but the quote is not in the abstract\.\s+Use Full text$/);
    assert.deepEqual(await marked(), ["Full text (suggested)"]);
    assert.equal(await preview().textContent(), "Abstract");
    assert.equal((await resolve()).out_quote_source, "abstract", "ignored, it stays");

    await review(entry({ outcome_quote: NOT_IN_ABSTRACT, out_quote_source: "abstract" }));
    await hint.locator("button").click();
    assert.equal(await source.inputValue(), "full_text");
    await noHint();
    assert.deepEqual(await marked(), []);
    assert.equal((await resolve()).out_quote_source, "full_text", "the click is a choice");

    await review(entry({ out_quote_source: "full_text" }));
    assert.equal(await source.inputValue(), "full_text");
    assert.match(await hint.textContent(), /^This entry says Full text, but the quote appears in the abstract\.\s+Use Abstract$/);
    assert.deepEqual(await marked(), ["Abstract (suggested)"]);

    // The validators' other spelling is the same claim.
    await review(entry({ out_quote_source: "fulltext" }));
    assert.equal(await source.inputValue(), "fulltext");
    assert.match(await hint.textContent(), /^This entry says Full text, but the quote appears in the abstract/);
    assert.equal((await labels(source)).filter((text) => text === "Full text").length, 1);

    // ---- The contradiction comes and goes with the edits.
    await review(entry({ out_quote_source: "abstract" }));
    await noHint();
    await quote.fill(NOT_IN_ABSTRACT);
    assert.equal(await hint.isVisible(), true);
    assert.equal(await source.inputValue(), "abstract");
    await quote.fill(IN_ABSTRACT);
    await noHint();
    await abstract.fill("Some other abstract.");
    assert.equal(await hint.isVisible(), true);

    // Choosing, even the same value by way of another, settles it.
    await source.selectOption("results");
    await source.selectOption("abstract");
    await noHint();
    assert.equal((await resolve()).out_quote_source, "abstract");

    // ---- Granular values: never touched, never flagged, shown properly.
    for (const [stored, shown, text] of [
      ["discussion", "Discussion", IN_ABSTRACT],
      ["discussion", "Discussion", NOT_IN_ABSTRACT],
      ["abstract|discussion", "Abstract + Discussion", NOT_IN_ABSTRACT],
      ["abstract|discussion", "Abstract + Discussion", IN_ABSTRACT],
      ["title|abstract", "Title + Abstract", NOT_IN_ABSTRACT],
    ]) {
      await review(entry({ out_quote_source: stored, outcome_quote: text }));
      assert.equal(await source.inputValue(), stored);
      assert.equal(await label(source), shown);
      await noHint();
      assert.deepEqual(await marked(), []);
      assert.equal(await preview().textContent(), shown);
      await quote.fill(text === IN_ABSTRACT ? NOT_IN_ABSTRACT : IN_ABSTRACT);
      assert.equal(await source.inputValue(), stored, "an edit does not replace it");
      await noHint();
      assert.equal((await resolve()).out_quote_source, stored);
    }

    // ---- A stored value from outside the vocabulary stays, escaped.
    await review(entry({ out_quote_source: XSS }));
    assert.equal(await source.inputValue(), XSS);
    assert.equal(await page.locator("#ar-normal-form img, .final-preview-card img").count(), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);

    // ---- The axes: the server detects nothing for them, so nothing is suggested.
    const computational = page.locator("#ar-computational-source");
    const robustness = page.locator("#ar-robustness-source");
    const repro = entry({ type: "reproduction", outcome: "computationally reproducible, robust",
      outcome_computation: "computationally reproducible", outcome_robustness: "robust",
      outcome_computational_quote: IN_ABSTRACT, out_quote_computational_source: "results",
      outcome_robustness_quote: IN_ABSTRACT, out_quote_robust_source: null });
    await review(repro);
    assert.equal(await computational.inputValue(), "results");
    assert.equal(await computational.locator('option[value=""]').count(), 0, "a blank would change nothing");
    assert.equal(await robustness.inputValue(), "", "no quote source is invented for it");
    assert.equal(await label(robustness), "— not set —");
    for (const text of [...await labels(computational), ...await labels(robustness)]) {
      assert.doesNotMatch(text, /auto-?detect|suggested/i);
    }
    let body = await resolve();
    assert.equal(body.corrected_computational_source, "results");
    assert.equal(body.corrected_robustness_source, null);

    await review(repro);
    await robustness.selectOption("full_text");
    body = await resolve();
    assert.equal(body.corrected_robustness_source, "full_text");

    // ---- A drafted original's own outcome: the server detects a blank there, axes
    // included, so each source is suggested against its own quote and the draft keeps
    // only what the admin picked.
    const xoField = (name) => page.locator(`#xo-add-list .xo-open [data-xoa-field="${name}"]`);
    const xoHint = (name) => page.locator(`#xo-add-list .xo-open [data-xoa-field="${name}"] + .admin-source-hint`);
    const draftOf = () => page.evaluate((id) => _xoAdminPayload(_adminXoDrafts[id].entries[0]), RID);
    const draftOriginal = async (rec) => {
      await page.evaluate(() => {
        for (const k of Object.keys(_adminXoCache)) delete _adminXoCache[k];
        for (const k of Object.keys(_adminXoDrafts)) delete _adminXoDrafts[k];
      });
      await review(rec);
      await page.locator("#xo-admin > summary").click();
      await page.locator("#xo-add-on").check();
      await page.locator("#xo-add-list .xo-open [data-xoa-own]").check();
    };

    await draftOriginal(entry());
    assert.equal(await label(xoField("out_quote_source")), "— no quote yet —");
    assert.equal(await xoHint("out_quote_source").isVisible(), false);
    await xoField("outcome_quote").fill(IN_ABSTRACT);
    assert.equal(await label(xoField("out_quote_source")), "Abstract (suggested)");
    assert.equal(await xoHint("out_quote_source").textContent(), IN_THE_ABSTRACT_HINT);
    await xoField("outcome_quote").fill(NOT_IN_ABSTRACT);
    assert.equal(await label(xoField("out_quote_source")), "Full text (suggested)");
    assert.equal(await xoHint("out_quote_source").textContent(), NOT_IN_THE_ABSTRACT_HINT);
    await abstract.fill("Say: " + NOT_IN_ABSTRACT);
    assert.equal(await label(xoField("out_quote_source")), "Abstract (suggested)", "it reads the form's abstract");
    assert.equal((await draftOf()).out_quote_source, null, "a suggestion stays out of the draft");
    await xoField("out_quote_source").selectOption("results");
    await xoField("outcome_quote").fill(IN_ABSTRACT);
    assert.equal(await label(xoField("out_quote_source")), "Results");
    assert.equal(await xoHint("out_quote_source").isVisible(), false);
    assert.equal((await draftOf()).out_quote_source, "results");

    await draftOriginal(repro);
    await xoField("outcome_computational_quote").fill(IN_ABSTRACT);
    await xoField("outcome_robustness_quote").fill(NOT_IN_ABSTRACT);
    assert.equal(await label(xoField("out_quote_computational_source")), "Abstract (suggested)");
    assert.equal(await label(xoField("out_quote_robust_source")), "Full text (suggested)");
    assert.equal((await draftOf()).out_quote_computational_source, null);
    assert.equal((await draftOf()).out_quote_robust_source, null);

    assert.deepEqual(errors, []);
    console.log("OK: quote source fields");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
