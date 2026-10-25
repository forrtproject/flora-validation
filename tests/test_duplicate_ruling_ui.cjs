/* Run with: node tests/test_duplicate_ruling_ui.cjs
 * The reason dialog on a duplicate ruling in Source Records: the reason is optional,
 * Cancel sends nothing, the cross-type confirmation still comes first, and a stored
 * note is shown escaped. All requests are intercepted. No web server, database, or
 * remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const NOTE = '<img src=x onerror=alert(1)> "quoted" & more';
const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";

async function until(test, ms = 3000) {
  const end = Date.now() + ms;
  while (!test()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const posts = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => { errors.push("dialog: " + dialog.message()); dialog.dismiss(); });
  const member = (id, display, type, extra = {}) => ({
    record_id: id, display_id: display, type, title: "T", duplicate_status: null,
    duplicate_note: null, ...extra,
  });
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname.endsWith("/duplicate") && request.method() === "POST") {
      posts.push(JSON.parse(request.postData()));
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/api/admin/source-records/duplicates") {
      return route.fulfill({ json: { groups: [{
        mixed_types: true, outcomes_differ: false, cross_sheet: false, resolved: false,
        members: [member(A, "REPL-000001", "replication", { duplicate_status: "distinct",
                    duplicate_reviewed_by: "luke", duplicate_note: NOTE }),
                  member(B, "REPR-000002", "reproduction")],
      }] } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    return route.fulfill({ json: { records: [], sources: [], jobs: [], counts: {}, total: 0, page: 1, per_page: 50 } });
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelectorAll(".screen").forEach(el => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      switchAdminTab("sources");
      showSourceDuplicates();
    });
    await page.locator(".src-dup-card").first().waitFor();

    // The stored note is text, never markup.
    const shown = page.locator(".src-dup-why-note");
    assert.equal(await shown.count(), 1);
    assert.equal(await shown.innerText(), NOTE);
    assert.equal(await page.locator("#src-dup-body img").count(), 0);

    const dialog = page.locator("#dialog-modal");
    const reason = page.locator("#src-dup-why");

    // Cancel sends nothing.
    await page.locator(".src-dup-distinct").first().click();
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await page.locator("#dialog-title").innerText(), "Keep REPL-000001 as distinct");
    await reason.fill("typed, then thought better of it");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await page.waitForTimeout(150);
    assert.equal(posts.length, 0);

    // The reason is optional: Save with nothing typed sends a ruling with no note.
    await page.locator(".src-dup-distinct").first().click();
    await dialog.getByRole("button", { name: "Save" }).click();
    await until(() => posts.length === 1);
    assert.deepEqual(posts[0], { status: "distinct", duplicate_of: null, note: null });

    // With a reason: trimmed, and sent as typed text (markup is not interpreted).
    await page.locator(".src-dup-distinct").first().click();
    await reason.fill("  " + NOTE + "  ");
    await dialog.getByRole("button", { name: "Save" }).click();
    await until(() => posts.length === 2);
    assert.deepEqual(posts[1], { status: "distinct", duplicate_of: null, note: NOTE });
    assert.equal(await page.locator("#dialog-message img").count(), 0);

    // A cross-type duplicate asks for the confirmation first, then the reason.
    await page.locator(".src-dup-dupe").first().click();
    assert.match(await page.locator("#dialog-message").innerText(), /replication and REPR-000002 is a reproduction/);
    assert.equal(await reason.count(), 0);
    await dialog.getByRole("button", { name: "Cancel" }).click();     // declines: nothing more
    await page.waitForTimeout(150);
    assert.equal(posts.length, 2);
    assert.equal(await dialog.isVisible(), false);

    await page.locator(".src-dup-dupe").first().click();
    const confirmation = await page.locator("#dialog-buttons button").allInnerTexts();
    await page.locator("#dialog-buttons button.btn-primary").click();   // confirms
    await reason.waitFor();
    assert.equal(await page.locator("#dialog-title").innerText(), "Mark REPL-000001 a duplicate of REPR-000002");
    await reason.fill("same report");
    await dialog.getByRole("button", { name: "Save" }).click();
    await until(() => posts.length === 3);
    assert.deepEqual(posts[2], { status: "duplicate", duplicate_of: B, note: "same report" });
    assert.ok(confirmation.length >= 2);

    assert.deepEqual(errors, []);
    console.log("OK test_duplicate_ruling_ui");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
