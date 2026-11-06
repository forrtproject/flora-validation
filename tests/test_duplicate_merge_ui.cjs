/* Run with: node tests/test_duplicate_merge_ui.cjs
 * The window Mark as Resolved opens when a record would match a validated one: what
 * it says matched, the two records side by side, what a merge does, what to do
 * instead, that only its confirm button merges, that everything is escaped, how it
 * stacks on a narrow screen, the banner and message around it, and what is left of
 * the Admin Resolution section on a record already merged.
 * All requests are intercepted; no server, database, or remote service runs. What
 * the 409 carries is tied to the real handler by tests/test_extra_originals.py and
 * tests/test_extra_originals_database.py, which also checks what the window claims.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const XSS = '<img src=x onerror="window.__xss=1">';
const LONG_DOI = "10.1234/" + "a-very-long-unbroken-identifier-".repeat(6);

const identity = {
  title_r: "A replication", doi_r: "10.1234/rep", study_r: "1",
  title_o: "Its own original", doi_o: "10.1234/own", original_key: "10.1234/own", study_o: "",
};
const conflict = (over = {}) => ({
  code: "validated_duplicate_conflict",
  message: "This resolution matches an existing validated record. Confirm an explicit duplicate merge; no record has been overwritten.",
  duplicate_record_id: A,
  survivor_record_id: B,
  survivor: { ...identity, type: "replication", outcome: "successful" },
  duplicate: { ...identity, type: "replication", outcome: "failed" },
  matched_fields: ["title_r", "doi_r", "study_r", "title_o", "original_key"],
  duplicate_published: false,
  added_originals: 0,
  open_suggestions: 0,
  ...over,
});

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  const resolves = [];
  const notes = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => { errors.push("dialog: " + d.message()); d.dismiss(); });

  let firstAnswer = [409, { detail: conflict() }];
  let mergeAnswer = [200, { resolved: true, merged: true, record_id: A, survivor_record_id: B, extra_originals_created: [] }];

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    const files = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css" };
    if (files[url.pathname]) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", files[url.pathname])) });
    }
    if (url.pathname === `/api/admin/entries/${A}/resolve` && request.method() === "POST") {
      const body = request.postDataJSON();
      resolves.push(body);
      const [status, json] = body.merge_into_record_id ? mergeAnswer : firstAnswer;
      return route.fulfill({ status, json });
    }
    if (url.pathname === `/api/admin/entries/${B}`) {
      return route.fulfill({ status: 200, json: { record: { ...record, record_id: B, title_r: "The kept record", validation_status: "validated", admin_checked: true },
                                                  extra_originals_summary: { open_suggestions: 0, children: 0 } } });
    }
    if (url.pathname === `/api/admin/entries/${A}/note`) {
      notes.push(request.postDataJSON());
      return route.fulfill({ status: 200, json: { saved: true } });
    }
    if (url.pathname === "/api/me") return route.fulfill({ status: 200, json: { kind: "anonymous" } });
    return route.fulfill({ status: 200, json: { records: [], entries: [], counts: {}, total: 0 } });
  });

  const record = {
    record_id: A, origin: "extractor", type: "replication", final_type: "replication", outcome: "failed",
    final_outcome: "failed", validation_status: "need_review", admin_checked: false,
    title_r: "A replication", doi_r: "10.1234/old", study_r: "1", title_o: "Its own original",
    doi_o: "10.1234/own", study_o: "", abstract_r: "An abstract.", outcome_quote: "It failed.",
  };
  const review = (data = {}, rec = record) => page.evaluate(([r, d]) => {
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    document.querySelector("#admin-screen").classList.remove("hidden");
    document.querySelector("#admin-detail-modal").classList.remove("hidden");
    renderAdminDetail({ record: r, extra_originals_summary: { open_suggestions: 0, children: 0 }, ...d });
  }, [rec, data]);
  const dialog = page.locator("#dialog-modal");
  const opened = () => page.locator("#dialog-modal:not(.hidden)").waitFor();
  const message = () => page.locator("#dialog-message").innerText();
  const mergeBtn = page.locator("#dialog-buttons button", { hasText: "Merge into the existing record" });
  const backBtn = page.locator("#dialog-buttons button", { hasText: "Go back to editing" });
  const merges = () => resolves.filter((b) => b.merge_into_record_id);
  const compare = () => page.locator(".merge-compare tbody tr").evaluateAll((rows) => rows.map((row) => {
    const [label, mine, kept, status] = row.children;
    return [label.textContent, mine.lastChild.textContent, kept.lastChild.textContent, status.textContent];
  }));
  // The window as a confirm with these arguments opens it, for what the form cannot reach here.
  const show = (detail, drafted = 0, changed = {}) => page.evaluate(([d, n, c]) => {
    window.__answer = confirmValidatedDuplicateMerge(d, n, c);
  }, [detail, drafted, changed]);
  const dismiss = async () => { await backBtn.click(); await dialog.waitFor({ state: "hidden" }); };

  try {
    await page.goto("http://flora.test/");

    // ---- Saying what was found and why: the admin's change is what caused it.
    await review();
    await page.locator("#ar-doi-r").fill("10.1234/rep");
    await page.locator("#admin-note-text").fill("My note");
    await page.locator("#admin-resolve-btn").click();
    await opened();
    assert.equal(await page.locator("#dialog-title").textContent(), "This record matches one that is already validated");
    assert.equal(await page.locator("#dialog-icon").isHidden(), true);
    const lead = (await page.locator(".merge-lead").textContent()).replace(/\s+/g, " ").trim();
    assert.equal(lead, "With the changes in this form, this record has the same replication title, replication DOI, " +
      "replication study label, original title and original DOI as the validated record below, and the original " +
      "study label is blank on both. Two validated records cannot share all six fields, so this needs your " +
      "decision. Nothing has been saved yet.");

    // ---- The two records, side by side, with what differs marked.
    const heads = await page.locator(".merge-compare thead th").evaluateAll((ths) => ths.map((t) => t.textContent.replace(/\s+/g, " ").trim()));
    assert.equal(heads[1], `This record (will be merged away) ${A}`);
    assert.equal(heads[2], `Existing validated record (kept) ${B}`);
    assert.deepEqual(await compare(), [
      ["Replication title", "A replication", "A replication", "Same"],
      ["Replication DOI", "changed in this form", "10.1234/rep", "Same"],
      ["Replication study label", "1", "1", "Same"],
      ["Original title", "Its own original", "Its own original", "Same"],
      ["Original DOI", "10.1234/own", "10.1234/own", "Same"],
      ["Original study label", "blank", "blank", "Both blank"],
      ["Type", "Replication", "Replication", "Same"],
      ["Outcome", "Failed", "Successful", "Differs"],
    ]);
    assert.match(await page.locator(".merge-compare tbody tr").nth(1).locator("td").first().innerText(), /10\.1234\/rep\s*changed in this form/);
    assert.equal(await page.locator(".merge-changed").count(), 1, "only the field edited");
    const tint = (selector) => page.locator(selector).first().evaluate((el) => getComputedStyle(el).backgroundColor);
    assert.notEqual(await tint(".merge-differs"), await tint(".merge-same"));
    assert.match(await message(), /Type and outcome are not part of the match, so they can differ\. If you merge, the existing record’s values stay\./);
    // The record ids are there, but small.
    const idSize = await page.locator(".merge-id").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    assert.ok(idSize < 11, `record id at ${idSize}px`);

    // ---- What a merge does, what to do instead, and no leftover jargon.
    const items = await page.locator(".merge-choices li").allInnerTexts();
    assert.equal(items.length, 4);
    assert.match(items[0], /marked rejected and linked to the existing record/);
    assert.match(items[0], /Validator assignments still open on it close/);
    assert.match(items[1], /not published, so nothing leaves FLoRA, and it will never be published/);
    assert.match(items[2], /existing record’s published values and FLoRA ID do not change/);
    assert.match(items[3], /Your corrections and note are not carried over\./);
    assert.match(items[3], /They stay on this record for audit, with its extracted data and the validators’ judgements/);
    assert.match(items[3], /None of it is published or copied to the existing record/);
    const text = await message();
    assert.match(text, /If they are different records/);
    assert.match(text, /correct the field that is wrong: Replication Title, Replication DOI, Original Title or Original DOI/);
    assert.match(text, /Study labels cannot be changed in this form/);
    assert.match(text, /A merge cannot be undone/);
    assert.doesNotMatch(text, /\bmerge A\b|\bA into B\b|\bB’s\b|survivor|authoritative|\bidentity\b/i);
    assert.deepEqual(await page.locator("#dialog-buttons button").allTextContents(),
                     ["Go back to editing", "Merge into the existing record"]);

    // ---- Wider than the shared dialog, on request.
    const wideBox = await page.locator("#dialog-modal .faq-panel").boundingBox();
    assert.ok(wideBox.width > 800, `panel ${wideBox.width}px wide`);
    assert.equal(await page.locator("#dialog-modal .faq-panel").evaluate((el) => getComputedStyle(el).textAlign), "left");

    // ---- As the shared dialog: no key and no click on the backdrop answers it.
    assert.equal(await page.evaluate(() => document.querySelector("#dialog-modal").contains(document.activeElement)), false,
                 "the shared dialog takes no focus");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Escape");
    await page.locator("#dialog-modal").click({ position: { x: 5, y: 5 } });
    await page.waitForTimeout(60);
    assert.equal(await dialog.isHidden(), false);
    assert.equal(merges().length, 0, "nothing but the button merges");

    // ---- Going back sends nothing and leaves the form as it was.
    assert.equal(resolves.length, 1);
    await dismiss();
    assert.equal(resolves.length, 1);
    assert.equal(await page.locator("#ar-doi-r").inputValue(), "10.1234/rep");
    assert.equal(await page.locator("#admin-note-text").inputValue(), "My note");
    assert.equal(await page.locator("#admin-resolve-btn").isDisabled(), false);
    assert.match(await page.locator("#admin-resolve-btn").textContent(), /Mark as Resolved/);
    assert.equal(await page.locator("#admin-detail-modal").isHidden(), false);

    // ---- The next dialog is the ordinary one again.
    await page.evaluate(() => { showAlert("Something else."); });
    await opened();
    const plainBox = await page.locator("#dialog-modal .faq-panel").boundingBox();
    assert.ok(plainBox.width <= 420, `panel ${plainBox.width}px wide`);
    assert.equal(await page.locator("#dialog-modal .faq-panel").evaluate((el) => el.classList.contains("dialog-wide")), false);
    assert.equal(await page.locator("#dialog-modal .faq-panel").evaluate((el) => getComputedStyle(el).textAlign), "center");
    await page.locator("#dialog-buttons .btn-primary").click();

    // ---- Confirming repeats the request, naming the record to keep.
    await page.locator("#admin-resolve-btn").click();
    await opened();
    await mergeBtn.click();
    await page.waitForFunction(() => document.querySelector("#toast")?.textContent.includes("Duplicate merged"));
    assert.equal(merges().length, 1);
    assert.equal(merges()[0].merge_into_record_id, B);
    assert.equal(merges()[0].corrected_doi_r, "10.1234/rep");
    assert.equal(merges()[0].admin_notes, "My note");
    assert.deepEqual({ ...merges()[0], merge_into_record_id: undefined }, { ...resolves.at(-2), merge_into_record_id: undefined },
                     "the same request, with the target");
    assert.match(await page.locator("#toast").textContent(), /existing validated record kept/);

    // ---- A merge the server refuses: said plainly, and the form is untouched.
    mergeAnswer = [409, { detail: { code: "duplicate_merge_target_changed", message: "The proposed identity no longer matches the selected merge target. Nothing was merged.",
                                    duplicate_record_id: A, survivor_record_id: B } }];
    await review();
    await page.locator("#ar-doi-r").fill("10.1234/rep");
    await page.locator("#admin-resolve-btn").click();
    await opened();
    await mergeBtn.click();
    await page.locator("#dialog-buttons button", { hasText: "OK" }).waitFor();
    assert.equal(await message(), "Merge failed. The existing record changed while this window was open, so nothing was merged. " +
      "Mark as Resolved again to see the current match. Your edits are still in the form.");
    await page.locator("#dialog-buttons button", { hasText: "OK" }).click();
    assert.equal(await page.locator("#admin-resolve-btn").isDisabled(), false);
    assert.equal(await page.locator("#ar-doi-r").inputValue(), "10.1234/rep");

    mergeAnswer = [409, { detail: { code: "survivor_not_eligible", message: "Nothing was saved. That entry cannot take originals." } }];
    await page.locator("#admin-resolve-btn").click();
    await opened();
    await mergeBtn.click();
    await page.locator("#dialog-buttons button", { hasText: "OK" }).waitFor();
    assert.equal(await message(), "Merge failed. Nothing was saved. That entry cannot take originals. Your edits are still in the form.");
    await page.locator("#dialog-buttons button", { hasText: "OK" }).click();

    // ---- Nothing edited: the record already matched as it stood.
    await show(conflict());
    await opened();
    assert.match(await page.locator(".merge-lead").innerText(), /^This record has the same/);
    assert.equal(await page.locator(".merge-changed").count(), 0);
    await dismiss();

    // ---- Published now, with originals on it, and more drafted.
    await show(conflict({ duplicate_published: true, added_originals: 2, open_suggestions: 1 }), 3);
    await opened();
    const fuller = await page.locator(".merge-choices li").allInnerTexts();
    assert.equal(fuller.length, 6);
    assert.match(fuller[1], /published now, so it leaves validated output at once and FLoRA at the next nightly sync/);
    assert.match(fuller[1], /FLoRA ID that rests on it alone is retired, never reused/);
    assert.match(fuller[4], /^2 added originals and 1 open suggestion on this record move to the existing record\./);
    assert.match(fuller[5], /The 3 other originals you drafted go to the existing record instead, checked against its originals\./);
    assert.match(fuller[5], /If it cannot take them, nothing is merged or saved\./);
    await dismiss();

    await show(conflict({ added_originals: 1 }), 1);
    await opened();
    const one = await page.locator(".merge-choices li").allInnerTexts();
    assert.match(one[4], /^1 added original on this record moves to the existing record\./);
    assert.match(one[5], /The 1 other original you drafted goes/);
    assert.match(one[5], /If it cannot take it, nothing is merged or saved\./);
    await dismiss();

    // ---- A type that differs, and an original known by its OpenAlex id.
    const noDoi = { ...identity, doi_o: "", original_key: "W2041234567" };
    await show(conflict({
      survivor: { ...noDoi, type: "reproduction", outcome: "computationally reproducible" },
      duplicate: { ...noDoi, type: "replication", outcome: "cannot_be_determined" },
    }));
    await opened();
    const rows = await compare();
    assert.deepEqual(rows[4], ["Original OpenAlex ID", "W2041234567", "W2041234567", "Same"]);
    assert.deepEqual(rows[6], ["Type", "Replication", "Reproduction", "Differs"]);
    assert.deepEqual(rows[7], ["Outcome", "Cannot be determined", "Computationally reproducible", "Differs"]);
    assert.equal(await page.locator(".merge-differs").count(), 2);
    assert.match(await page.locator(".merge-lead").innerText(), /and original OpenAlex ID as the validated record/);
    await dismiss();

    // ---- A title full of HTML stays text, wherever it lands.
    const hostile = { ...identity, title_r: "Title " + XSS, title_o: "Original " + XSS, doi_r: XSS, study_r: XSS, original_key: XSS, doi_o: XSS, study_o: XSS };
    await show(conflict({
      survivor: { ...hostile, type: XSS, outcome: XSS },
      duplicate: { ...hostile, type: "replication", outcome: XSS },
      duplicate_record_id: XSS,
      survivor_record_id: XSS,
      matched_fields: ["title_r", XSS],
      added_originals: XSS,
      open_suggestions: XSS,
    }));
    await opened();
    assert.equal(await page.locator("#dialog-message img").count(), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    const escaped = await page.locator(".merge-compare tbody").textContent();
    assert.ok(escaped.includes("Title " + XSS));
    assert.ok(escaped.includes("Original " + XSS));
    assert.ok((await page.locator(".merge-compare thead").textContent()).includes(XSS));
    await dismiss();

    // ---- Long titles and DOIs wrap, in the panel, at full width.
    const long = { ...identity, title_r: "A very long replication title ".repeat(14), doi_r: LONG_DOI, title_o: "Word".repeat(60) };
    const longDetail = conflict({ survivor: { ...long, type: "replication", outcome: "successful" },
                                  duplicate: { ...long, type: "replication", outcome: "failed" } });
    const overflow = () => page.locator("#dialog-modal .faq-body").evaluate((el) => el.scrollWidth - el.clientWidth);
    await show(longDetail);
    await opened();
    assert.equal(await overflow(), 0);
    const [mineCell, keptCell] = await page.locator(".merge-compare tbody tr").nth(1).locator("td").elementHandles();
    assert.ok((await mineCell.boundingBox()).x + (await mineCell.boundingBox()).width <= (await keptCell.boundingBox()).x + 1, "side by side");
    await dismiss();

    // ---- On a narrow screen the two columns stack, and the buttons stay reachable.
    await page.setViewportSize({ width: 390, height: 800 });
    await show(longDetail, 1);
    await opened();
    assert.equal(await overflow(), 0);
    const [narrowMine, narrowKept] = await page.locator(".merge-compare tbody tr").nth(1).locator("td").elementHandles();
    const mineBox = await narrowMine.boundingBox();
    const keptBox = await narrowKept.boundingBox();
    assert.ok(mineBox.y + mineBox.height <= keptBox.y + 1, "stacked");
    assert.ok(Math.abs(mineBox.x - keptBox.x) < 1, "aligned");
    assert.equal(await page.locator(".merge-side").first().isVisible(), true, "each value says which record it is");
    assert.deepEqual(await page.locator("#dialog-buttons").evaluate((el) => getComputedStyle(el).flexDirection), "column");
    assert.equal(await backBtn.isVisible(), true, "the phone layout hides .ghost-btn elsewhere");
    assert.equal(await mergeBtn.isVisible(), true);
    assert.equal(await page.locator("#dialog-modal .faq-body").evaluate((el) => {
      const [back, merge] = [...document.querySelectorAll("#dialog-buttons button")].map((b) => b.getBoundingClientRect());
      const box = el.getBoundingClientRect();
      return merge.bottom <= box.bottom + 1 && back.top >= box.top - 1;
    }), true, "both buttons in the scroll box");
    await dismiss();
    await page.setViewportSize({ width: 1440, height: 1000 });

    // ---- After the merge the record says what happened to it, escaped.
    await review({ duplicate_merge: { duplicate_record_id: A, survivor_record_id: B, merged_by: "sophie" + XSS, merged_at: "2026-11-05T10:00:00Z" } });
    const banner = await page.locator(".admin-override-banner", { hasText: "Merged duplicate" }).innerText();
    assert.ok(banner.includes("sophie" + XSS));
    assert.match(banner, new RegExp(`merged this record into validated record ${B}, which was kept\\.`));
    assert.match(banner, /This record is not published\./);
    assert.match(banner, /extracted data, judgements and the corrections saved at the merge stay here for audit/);
    assert.equal(await page.evaluate(() => window.__xss), undefined);

    // ---- A merged record: saying what happened, and nothing the server would refuse
    // with record_already_merged. It is stored as rejected, but was not rejected.
    const merge = { duplicate_record_id: A, survivor_record_id: B, merged_by: "sophie", merged_at: "2026-11-05T10:00:00Z" };
    const mergedRecord = { ...record, validation_status: "rejected", admin_checked: true, admin_name: "sophie" };
    const section = page.locator(".admin-resolve-form");
    const CANNOT_SUCCEED = ["#ar-normal-form", ".not-val-decision", "#confirm-is-rep-btn", "#notval-is-repro-btn",
                            "#admin-other-type-btn", "#admin-resolve-btn", "#admin-reject-btn", "#admin-flag-review-btn"];
    const offered = async () => (await Promise.all(CANNOT_SUCCEED.map(async (s) => [s, await page.locator(s).count()])))
      .filter(([, n]) => n).map(([s]) => s);

    // Whatever its status says: the merge decides this, not validation_status.
    for (const rec of [mergedRecord, { ...record, validation_status: "validated" }, { ...record, validation_status: "need_review" }]) {
      await review({ duplicate_merge: merge }, rec);
      assert.deepEqual(await offered(), [], rec.validation_status);
      assert.equal((await page.locator(".admin-merged-note .not-val-who").innerText()).replace(/\s+/g, " ").trim(),
                   `This record was merged into validated record ${B}. Nothing is left to resolve here.`);
    }
    const sectionText = await section.innerText();
    assert.doesNotMatch(sectionText, /rejected|neither a replication nor a reproduction|Mark as Resolved|Review \/ edit fields/i);
    // The banner keeps the rest: this does not repeat it.
    assert.doesNotMatch(await page.locator(".admin-merged-note").innerText(), /sophie|not published|audit/);
    assert.equal(await page.locator(".admin-override-banner", { hasText: "Merged duplicate" }).count(), 1);
    assert.equal(await page.locator(".admin-merged-note code").textContent(), B);
    const noteBox = await page.locator(".admin-merged-note").evaluate((el) => {
      const code = el.querySelector("code");
      return [el.clientWidth, el.scrollWidth, code.getBoundingClientRect().right <= el.getBoundingClientRect().right];
    });
    assert.ok(noteBox[1] <= noteBox[0] && noteBox[2], `the id stays in the box: ${noteBox}`);

    // A note is not a resolve, and still saves.
    await page.locator("#admin-note-text").fill("Merged by mistake?");
    await page.locator("#admin-save-note-btn").click();
    await page.waitForFunction(() => document.querySelector("#admin-save-note-btn").textContent.includes("Saved"));
    assert.deepEqual(notes, [{ note: "Merged by mistake?" }]);

    // The kept record opens, though it is on no list here.
    assert.equal(await page.evaluate((id) => _adminEntryIds.includes(id), B), false);
    await page.locator("#admin-open-survivor-btn").click();
    await page.locator("#admin-resolve-btn").waitFor();
    assert.equal(await page.locator("#admin-resolve-btn").getAttribute("data-id"), B);
    assert.equal(await page.locator(".admin-merged-note").count(), 0);
    assert.equal(await page.locator("#admin-detail-title").textContent(), "The kept record");

    // Skip moves on as it does everywhere; off the list that means closing.
    await review({ duplicate_merge: merge }, mergedRecord);
    await page.locator("#admin-skip-btn").click();
    await page.locator("#admin-detail-modal").waitFor({ state: "hidden" });

    // The id is the server's, but it lands in markup.
    await review({ duplicate_merge: { ...merge, survivor_record_id: XSS } }, mergedRecord);
    assert.equal(await page.locator(".admin-merged-note code").textContent(), XSS);
    assert.equal(await page.locator("#admin-open-survivor-btn").getAttribute("data-id"), XSS);
    assert.equal(await page.locator(".admin-merged-note img").count(), 0);
    assert.equal(await page.evaluate(() => window.__xss), undefined);

    // ---- An entry rejected for what it is keeps its way back.
    await review({}, { ...record, validation_status: "rejected" });
    assert.match(await page.locator(".not-val-decision").innerText(), /This record was rejected — neither a replication nor a reproduction/);
    assert.equal(await page.locator("#ar-normal-form").isHidden(), true);
    await page.locator("#confirm-is-rep-btn").click();
    assert.equal(await page.locator("#ar-normal-form").isHidden(), false);
    assert.equal(await page.locator("#admin-resolve-btn").isVisible(), true);
    assert.equal(await page.locator(".admin-merged-note").count(), 0);

    assert.deepEqual(errors, []);
    console.log("duplicate merge window: all checks passed");
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
