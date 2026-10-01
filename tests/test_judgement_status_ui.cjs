/* Run with: node tests/test_judgement_status_ui.cjs
 * "My Judgements": four plain statuses (agreed and admin-to-settle entries share
 * "Awaiting admin approval"), each with a tooltip, and a legend explaining them.
 * All requests are intercepted. No web server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const STATUSES = ["unvalidated", "validation_inprogress", "consensus_reached", "need_review", "validated", "rejected"];

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const judgements = STATUSES.map((status, i) => ({
    queue_id: `q${i}`, title_r: `Study ${i}`, year_r: "2019", validation_status: status,
    validated_at: `2026-09-2${i}T10:00:00Z`, points: 10,
    type_check: "correct", original_check: "correct", outcome_check: "correct",
  }));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === "/api/my-judgements") return route.fulfill({ json: { judgements } });
    return route.fulfill({ json: {} });
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      openHistory();
    });
    await page.locator(".hist-item").first().waitFor();

    // List badges: new labels, each with its explanation as a tooltip.
    const badges = await page.locator(".hist-item .hd-status-badge").evaluateAll(els =>
      els.map(el => ({ label: el.textContent, title: el.getAttribute("title") })));
    const labels = badges.map(b => b.label);
    // In list order (newest first): rejected, validated, need_review,
    // consensus_reached, validation_inprogress, unvalidated.
    assert.deepEqual(labels, [
      "Not added to FLoRA", "Approved", "Awaiting admin approval", "Awaiting admin approval",
      "Waiting for the second validator", "Waiting for the second validator",
    ]);
    for (const old of ["Pending 2nd", "Pending approval", "In review", "Admin to decide", "Excluded"]) {
      assert(!labels.includes(old), `old label still shown: ${old}`);
    }
    assert(badges.every(b => b.title && b.title.length > 20), "every badge explains itself");
    const awaiting = badges.find(b => b.label === "Awaiting admin approval");
    assert.match(awaiting.title, /routine and doesn't mean you did something wrong/);
    // An assigned entry, or one the extractor changed, can wait here with one judgement.
    assert.doesNotMatch(awaiting.title, /both judgements/i);
    assert.match(badges[0].title, /duplicates an entry that is already in FLoRA/);

    // The legend: collapsed, then lists the four statuses in lifecycle order.
    const legend = page.locator(".hist-legend");
    assert.equal(await legend.getAttribute("open"), null);
    await legend.locator("summary").click();
    assert.deepEqual(await legend.locator("dt .hd-status-badge").allTextContents(),
      ["Waiting for the second validator", "Awaiting admin approval", "Approved", "Not added to FLoRA"]);
    assert.match(await legend.innerText(), /automatically when both validators and the AI check agree/);

    // The detail view's badge uses the same wording, including its fallback.
    const detail = await page.evaluate(() => [
      _histStatusBadge("need_review"),
      _histStatusBadge(undefined),
    ]);
    assert.match(detail[0], />Awaiting admin approval</);
    assert.match(detail[1], />Waiting for the second validator</);

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await legend.isVisible(), true);
    // The longest label must not push the list sideways on a phone.
    const overflow = await page.locator("#history-body").evaluate(el => el.scrollWidth - el.clientWidth);
    assert(overflow <= 0, `My Judgements scrolls sideways by ${overflow}px on a phone`);
    if (process.env.FLORA_UI_SCREENSHOTS) {
      await fs.mkdir(process.env.FLORA_UI_SCREENSHOTS, { recursive: true });
      await page.setViewportSize({ width: 900, height: 900 });
      await page.locator(".hist-panel").screenshot({ path: path.join(process.env.FLORA_UI_SCREENSHOTS, "judgement-statuses.png") });
    }
    assert.deepEqual(errors, []);
    console.log("judgement status UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
