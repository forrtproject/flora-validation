/* Run with: node tests/test_withdraw_entry_ui.cjs
 * "Withdraw from validation" on the admin entry screen: shown only while the entry is
 * open for validation, disabled for an admin who may not decide it, a reason required
 * before anything is sent, and the same entry shown again, withdrawn, afterwards.
 * All requests are intercepted; no server, database, or remote service runs. What the
 * route does is tested against PostgreSQL in tests/test_decided_records.py.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const A = "11111111-1111-4111-8111-111111111111";

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  const withdrawals = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });

  const record = {
    record_id: A, origin: "extractor", type: "replication", outcome: "failed",
    validation_status: "validation_inprogress", admin_checked: false,
    title_r: "A replication", doi_r: "10.1234/rep", study_r: "1", title_o: "The wrong original",
    doi_o: "10.1234/wrong", study_o: "", abstract_r: "An abstract.", outcome_quote: "It failed.",
    admin_notes: "Earlier note",
  };
  const withdrawn = {
    ...record, validation_status: "rejected", admin_checked: true, admin_name: "boss",
    admin_notes: "Withdrawn before validation: Paired with the wrong original\n\nEarlier note",
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === `/api/admin/entries/${A}/withdraw` && request.method() === "POST") {
      withdrawals.push(request.postDataJSON());
      return route.fulfill({ status: 200, json: { withdrawn: true, record_id: A } });
    }
    if (url.pathname === `/api/admin/entries/${A}`) {
      return route.fulfill({ status: 200, json: {
        record: withdrawn, extra_originals_summary: { open_suggestions: 0, children: 0 },
        decisions: [{ action: "withdrawn", admin_handle: "boss", decided_at: "2026-11-13T10:00:00Z",
                      changes: { admin_notes: ["Earlier note", withdrawn.admin_notes] } }],
      } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
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

    // ---- Cancelling sends nothing.
    await review(record);
    await button.click();
    await opened();
    assert.equal(await page.locator("#dialog-title").textContent(), "Withdraw from validation");
    await dialogButton("Cancel").click();
    await closed();
    assert.equal(withdrawals.length, 0);

    // ---- A blank reason is refused before anything is sent.
    await button.click();
    await opened();
    await page.locator("#admin-withdraw-reason").fill("   ");
    await dialogButton("Withdraw").click();
    await opened();
    assert.match(await page.locator("#dialog-message").innerText(), /Give a reason/);
    await dialogButton("OK").click();
    await closed();
    assert.equal(withdrawals.length, 0);

    // ---- With a reason: sent, then the same entry shown again as withdrawn.
    await button.click();
    await opened();
    if (process.env.SHOT_DIR) await page.screenshot({ path: path.join(process.env.SHOT_DIR, "withdraw_dialog.png") });
    await page.locator("#admin-withdraw-reason").fill("  Paired with the wrong original  ");
    await dialogButton("Withdraw").click();
    await page.locator(".not-val-decision").waitFor();
    assert.deepEqual(withdrawals, [{ reason: "Paired with the wrong original" }]);
    const notice = await page.locator(".not-val-decision .not-val-who").innerText();
    assert.match(notice, /This entry was withdrawn before validation/);
    assert.doesNotMatch(notice, /neither a replication nor a reproduction/);
    assert.match(await page.locator(".admin-decisions summary").innerText(), /Admin decisions \(1\)/);
    assert.match(await page.locator(".admin-decisions li").textContent(), /Withdrawn before validation/);
    assert.match(await page.locator("#admin-note-text").inputValue(), /^Withdrawn before validation: Paired/);
    assert.equal(await button.count(), 0);
    if (process.env.SHOT_DIR) await page.screenshot({ path: path.join(process.env.SHOT_DIR, "withdraw_after.png") });

    assert.deepEqual(errors, []);
    console.log("withdraw entry UI: all checks passed");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
