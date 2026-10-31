/* Run with: node tests/test_extra_originals_validator_ui.cjs
 * "This study has more than one original" on the validation screen: the panel
 * appears once Gate II is answered (not for "neither", not in onboarding), "+"
 * collapses earlier entries to one line, ✕ removes one, a reload restores the
 * draft, and /api/judge receives the complete entries only.
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
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { errors.push("dialog: " + dialog.message()); dialog.dismiss(); });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/judge") return route.fulfill({ status: 200, json: { points_earned: 1 } });
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ status: 200, json: [] });
    if (url.pathname === "/api/next-pairs") return route.fulfill({ status: 200, json: { pairs: [] } });
    return route.fulfill({ status: 200, json: { records: [], points: 0, rank: 1 } });
  });
  const pair = {
    pair_id: "xo-pair", record_id: "xo-pair", queue_id: 1,
    type: "replication", outcome: "failed", title_r: "A replication of two studies",
    title_o: "The first original", doi_r: "10.1234/replication", doi_o: "10.1234/original",
    abstract_r: "We replicate two studies. Neither replicated.",
    outcome_phrase: "Neither replicated.",
    outcome_computation: null, outcome_robustness: null,
  };
  const showPair = data => page.evaluate(p => {
    document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
    document.querySelector("#game-screen").classList.remove("hidden");
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    state.coder = { coder_id: 1, handle: "Test validator" };
    state.assignment = null;
    state.mode = "normal";
    _showActivePair(p);
  }, data);
  const panel = () => page.locator("#extra-originals");
  const fill = async (field, value) => page.locator(`.xo-open [data-xo-field="${field}"]`).fill(value);

  try {
    await page.goto("http://flora.test/");
    await page.clock.install();
    await showPair(pair);

    // Hidden until Gate II is answered; "neither" hides it again.
    assert.equal(await panel().isHidden(), true);
    await page.locator('[data-type="replication"]').click();
    assert.equal(await panel().isHidden(), true);
    await page.locator('[data-original="correct"]').click();
    assert.equal(await panel().isVisible(), true);
    assert.equal(await page.locator("#xo-body").isHidden(), true);

    // Ticking opens one empty entry; "+" collapses it to a one-line summary.
    await page.locator("#xo-has-more").check();
    assert.equal(await page.locator(".xo-item").count(), 1);
    await fill("doi_or_url", "https://doi.org/10.5555/second");
    await fill("title", "The second original");
    await fill("study", "2");
    await page.locator("#xo-add-btn").click();
    assert.equal(await page.locator(".xo-item").count(), 2);
    assert.equal(await page.locator(".xo-collapsed").count(), 1);
    assert.match(await page.locator(".xo-collapsed .xo-summary").textContent(),
                 /#1 · The second original — https:\/\/doi\.org\/10\.5555\/second · Study 2/);
    await fill("title", '<img src=x onerror="window.__xss=1"> Third');
    await page.locator("#xo-add-btn").click();
    assert.equal(await page.locator(".xo-collapsed").count(), 2);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    // The newest (empty) entry is marked incomplete and will not be sent.
    assert.equal(await page.locator(".xo-open .xo-incomplete").isVisible(), true);
    assert.equal(await page.locator('.xo-open [data-xo-field="study"]').getAttribute("maxlength"), "50");

    // A summary expands its entry and collapses the others.
    await page.locator('[data-xo-open="0"]').click();
    assert.equal(await page.locator('.xo-open [data-xo-field="title"]').inputValue(), "The second original");
    assert.equal(await page.locator(".xo-collapsed").count(), 2);

    // ✕ removes one entry.
    await page.locator('.xo-collapsed [data-xo-remove="1"]').click();
    assert.equal(await page.locator(".xo-item").count(), 2);
    let saved = await page.evaluate(() => state.judgement.extra_originals.map(e => e.title));
    assert.deepEqual(saved, ["The second original", ""]);

    // A reload restores the entries from the draft.
    await page.clock.fastForward(10_001);
    const draft = await page.evaluate(() => JSON.parse(localStorage.getItem("flora.draft.xo-pair")));
    assert.equal(draft.has_extra_originals, true);
    await page.reload();
    await showPair({ ...pair, resumed: true });
    assert.equal(await panel().isVisible(), true);
    assert.equal(await page.locator("#xo-has-more").isChecked(), true);
    assert.equal(await page.locator(".xo-item").count(), 2);

    // The judgement carries the complete entries only.
    await page.locator('[data-outcome="correct"]').first().click();
    await page.clock.fastForward(18_000);
    const submitted = page.waitForRequest(request => new URL(request.url()).pathname === "/api/judge");
    await page.locator("#submit-btn").click();
    const payload = (await submitted).postDataJSON();
    assert.deepEqual(payload.extra_originals, [{
      doi_or_url: "https://doi.org/10.5555/second", title: "The second original", study: "2", note: "",
    }]);
    // Submitting moves on by itself (loadNextPair: an empty queue ends on the done
    // screen, hiding the card). Let that finish, or it can hide the next pair shown here.
    await page.locator("#done-screen").waitFor({ state: "visible" });

    // Neither type sends none, even with entries drafted.
    await showPair({ ...pair, pair_id: "xo-neither", record_id: "xo-neither" });
    await page.locator('[data-type="replication"]').click();
    await page.locator('[data-original="correct"]').click();
    await page.locator("#xo-has-more").check();
    await fill("title", "Some paper");
    // An answered gate folds 300 ms later, unless a quicker next answer cancels it
    // (one shared timer). Let it settle, then reopen Gate I if it folded.
    await page.clock.runFor(400);
    if (!(await page.locator('[data-type="not_validation"]').isVisible())) {
      await page.locator("#gate-1 .gate-header-row").click();
    }
    await page.locator('[data-type="not_validation"]').click();
    assert.equal(await panel().isHidden(), true);
    await page.clock.fastForward(18_000);
    const neither = page.waitForRequest(request => new URL(request.url()).pathname === "/api/judge");
    await page.locator("#submit-btn").click();
    assert.equal((await neither).postDataJSON().extra_originals, null);

    // Onboarding has no panel.
    const onboardingHasPanel = await page.evaluate(p => {
      const box = document.createElement("div");
      renderPairInto(box, p, { onboarding: true, judgeCount: 0 });
      return !!box.querySelector("#extra-originals");
    }, pair);
    assert.equal(onboardingHasPanel, false);

    assert.deepEqual(errors, []);
    console.log("Extra originals validator checks passed: visibility, add/collapse/remove, draft reload, payload, neither, onboarding.");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
