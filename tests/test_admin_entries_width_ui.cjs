/* Run with: node tests/test_admin_entries_width_ui.cjs
 * The Entries table fits its frame on a laptop with the widest rows there are: long
 * text is cut with the full value in its title, a long title leaves the study label and
 * every marker in sight, Skips and the validators are compact, there is no LLM badge,
 * the status pill links to Pending Approval, and Approved by shows only on the lists of
 * approved entries.
 * All requests are intercepted; no server, database, or remote service runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const XSS = '<img src=x onerror="window.__xss=1">';
const id = (n) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const LONG_OUTCOME = "Statistically significant in the same direction but with a far smaller effect " + XSS;
const LONG_ADMIN = "a-very-long-admin-handle-indeed";
const LONG_TITLE = "A very long replication title that keeps going well past sixty characters in all, " +
  "then a colon and a subtitle that goes on for at least as long again: " + XSS;
const LONG_ORIGINAL = "An original with a long title of its own, past sixty characters too, and then some more";
const LONG_LABEL = Array.from({ length: 30 }, (_, n) => `${n + 1}a`).join(", ");
const WAITING = id(1), VALIDATED = id(2), REVIEW = id(3), AUTO = id(4), BUSY = id(5), BLANK = id(6), PLAIN = id(7), NO_LABEL = id(8);

// As wide as a row gets: every flag, a two-digit Skips count with the issue marker,
// Agreement with the mark, and text longer than any column.
const worst = (rid, over = {}) => ({
  record_id: rid, title_r: LONG_TITLE, final_title_r: LONG_TITLE, doi_r: "10.5555/r", study_r: "12", type: "reproduction", final_type: "reproduction",
  outcome: LONG_OUTCOME, final_outcome: LONG_OUTCOME, validation_status: "consensus_reached",
  is_tiebreaker: true, admin_checked: true, admin_name: LONG_ADMIN, admin_notes: "A note", note_saved_by: "cy",
  auto_validated: false, auto_validated_rule: null, mine: true, has_v1: true, has_v2: true, has_llm: true,
  v1_handle: "ana", v2_handle: "ben", has_llm_error: true, quality_flag_count: 3,
  agreement_pct: 100, llm_dissent: "type, original, outcome", trusted_validator_count: 2,
  skip_event_count: 14, skip_validator_count: 12, skip_issue_validator_count: 4, skip_review_required: true,
  origin: "admin_extra_original", effective_title_o: LONG_ORIGINAL, effective_doi_o: "10.5555/o", has_extra_children: true, open_extra_suggestions: 2, ...over,
});
const ROWS = [
  worst(WAITING),
  worst(VALIDATED, { validation_status: "validated", final_outcome: "Computationally reproducible", agreement_pct: 67 }),
  worst(REVIEW, { validation_status: "need_review", final_type: "not_validation", final_outcome: "Cannot be determined",
                  trusted_validator_count: 0, mine: false }),
  worst(AUTO, { validation_status: "validated", auto_validated: true, auto_validated_rule: "trusted", admin_checked: false,
                admin_name: null, final_outcome: "Successful but flawed" }),
  worst(BUSY, { validation_status: "validation_inprogress", has_v2: false, v2_handle: null, final_outcome: "failed",
                agreement_pct: null, llm_dissent: null, skip_event_count: 1, skip_validator_count: 1,
                skip_issue_validator_count: 0, skip_review_required: false, admin_name: null, has_llm_error: false }),
  worst(BLANK, { validation_status: "unvalidated", has_v1: false, has_v2: false, has_llm: false, v1_handle: null, v2_handle: null,
                 final_outcome: null, outcome: null, agreement_pct: null, llm_dissent: null, skip_event_count: 0,
                 skip_validator_count: 0, skip_issue_validator_count: 0, skip_review_required: false, admin_name: null,
                 has_llm_error: false }),
];
// A row with nothing to flag, as long as a title gets; and the same with no label.
const unflagged = (rid, over = {}) => worst(rid, {
  validation_status: "validated", final_outcome: "failed", agreement_pct: 100, llm_dissent: null, is_tiebreaker: false,
  admin_checked: false, admin_name: null, admin_notes: null, note_saved_by: null, mine: false, has_llm_error: false,
  quality_flag_count: 0, trusted_validator_count: 0, skip_event_count: 0, skip_validator_count: 0,
  skip_issue_validator_count: 0, skip_review_required: false, origin: "extractor", has_extra_children: false,
  open_extra_suggestions: 0, ...over });
ROWS.push(unflagged(PLAIN, { study_r: "1, 2" }), unflagged(NO_LABEL, { study_r: "" }));
ROWS[ROWS.findIndex((r) => r.record_id === BLANK)].study_r = LONG_LABEL;

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(8_000);
  const errors = [];
  const lists = [];        // the search of each entry-list request
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
    const p = url.pathname;
    if (!p.startsWith("/api/")) return route.fulfill({ status: 200, body: "" });
    if (p === "/api/me") return route.fulfill({ json: { kind: "anonymous" } });
    if (p === "/api/admin/entries") {
      lists.push(url.search);
      const filter = url.searchParams.get("filter");
      const entries = filter === "pending_approval" ? ROWS.filter((r) => r.validation_status === "consensus_reached")
        : filter === "auto_validated" ? ROWS.filter((r) => r.auto_validated)
        : ROWS;
      // Far enough in that the row numbers have five digits, as they do on the real list.
      return route.fulfill({ json: { entries, total: 10_200, page: 204, per_page: 50, viewer_linked: true,
        counts: { all: 10_200, pending_approval: 1, needs_review: 1, validated: 2, auto_validated: 1 } } });
    }
    return route.fulfill({ json: { records: [], entries: [], counts: {}, total: 0 } });
  });

  const heads = () => page.locator("#admin-entries-head th").allInnerTexts();
  const hasColumn = (labels, name) => labels.some((t) => t.trim().toUpperCase() === name.toUpperCase());
  const rowOf = (rid) => page.locator(`#admin-table-body tr:has(.admin-review-btn[data-id="${rid}"])`);
  const filterTo = async (name) => {
    await page.evaluate((f) => document.querySelector(`#admin-filters [data-filter="${f}"]`).click(), name);
    await page.waitForFunction(() => document.querySelector("#admin-table-body .admin-review-btn"));
    await page.waitForTimeout(60);
  };
  const frame = () => page.evaluate(() => {
    const wrap = document.querySelector("#admin-tab-entries .admin-table-wrap");
    return { scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth,
             table: wrap.querySelector("table").getBoundingClientRect().width };
  });
  // Cells that lay their whole content out, none of it cut: those that are not
  // meant to clip, with the sort arrow shown on every head. Study clips in its lines.
  const overflowing = () => page.evaluate(() => {
    document.querySelectorAll("#admin-entries-head .th-sort").forEach((th) => th.setAttribute("data-arrow", " ▼"));
    const cells = [...document.querySelectorAll("#admin-entries-head th, #admin-table-body > tr > td")];
    const found = cells.filter((c) => c.scrollWidth > c.clientWidth
      && !c.matches(".admin-cell-clip")).map((c) => `${c.tagName} ${c.cellIndex}: ${c.textContent.trim()}`);
    document.querySelectorAll("#admin-entries-head .th-sort").forEach((th) => th.setAttribute("data-arrow", ""));
    return found;
  });

  // A Study cell as laid out: is each element inside the cell's visible box, the height
  // of each line, and the row's height with and without anything in this cell.
  const studyCell = (rid) => rowOf(rid).locator(".admin-cell-study");
  const study = (rid) => studyCell(rid).evaluate((cell) => {
    const box = cell.getBoundingClientRect();
    const inside = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.left >= box.left - 0.5 && r.right <= box.right + 0.5
        && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5;
    };
    const line = cell.querySelector(".admin-study-line");
    const title = cell.querySelector(".admin-study-title");
    const label = cell.querySelector(".admin-study-label");
    const marks = [...cell.querySelectorAll(".admin-study-marks > *")];
    const original = cell.querySelector(".admin-xo-original");
    const fontSize = parseFloat(getComputedStyle(title).fontSize);
    const mid = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; };
    const row = cell.closest("tr");
    const seen = {
      lines: cell.children.length,
      titleCut: title.scrollWidth > title.clientWidth,
      titleEllipsis: getComputedStyle(title).textOverflow,
      titleText: title.textContent,
      titleWidth: title.getBoundingClientRect().width,
      titleInside: inside(title),
      labelInside: label ? inside(label) : null,
      labelCut: label ? label.scrollWidth > label.clientWidth : null,
      sameLine: label ? Math.abs(mid(title) - mid(label)) < 4 : null,
      lineHeight: line.getBoundingClientRect().height,
      fontSize,
      marks: marks.map((m) => [m.textContent.trim() || m.title, inside(m)]),
      marksRows: new Set(marks.map((m) => Math.round(m.getBoundingClientRect().top))).size,
      originalInside: original ? inside(original) : null,
      originalCut: original ? original.scrollWidth > original.clientWidth : null,
      originalEllipsis: original ? getComputedStyle(original).textOverflow : null,
      originalHeight: original ? original.getBoundingClientRect().height : null,
      height: row.getBoundingClientRect().height,
    };
    const content = cell.innerHTML;
    cell.innerHTML = "";
    seen.emptied = row.getBoundingClientRect().height;
    cell.innerHTML = content;
    return seen;
  });

  try {
    await page.goto("http://flora.test/");
    await page.evaluate(() => {
      document.querySelector("#mobile-warning")?.classList.add("dismissed");
      document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
      document.querySelector("#admin-screen").classList.remove("hidden");
      _adminHandle = "me";
      _adminPage = 204;
      _adminFilter = "validated";   // the widest head: it alone has Approved by
      fetchAdminEntries();
    });
    await page.locator("#admin-table-body .admin-review-btn").first().waitFor();

    // ---- Fits at laptop widths; every column but the clipped ones shows all it holds.
    for (const width of [1440, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      const { scrollWidth, clientWidth, table } = await frame();
      assert.ok(scrollWidth <= clientWidth, `${width}px: scrolls, ${scrollWidth} > ${clientWidth}`);
      assert.ok(table <= clientWidth, `${width}px: table ${table} > frame ${clientWidth}`);
      assert.deepEqual(await overflowing(), [], `${width}px`);

      // ---- A long title hides nothing else in the Study cell.
      const flagged = await study(WAITING);
      assert.deepEqual(flagged.marks.map(([name]) => name),
                       ["\u26A0 3", "LLM", "TIE", "\u2713", "YOU", "+2 suggested", "added original", "\u2B50\u2B50", "\u{1F4DD}"], `${width}px`);
      assert.deepEqual(flagged.marks.filter(([, seen]) => !seen), [], `${width}px: cut off`);
      assert.equal(flagged.titleInside, true);
      assert.equal(flagged.titleCut, true, `${width}px: the title should need cutting`);
      assert.equal(flagged.titleEllipsis, "ellipsis");
      assert.equal(flagged.titleText, LONG_TITLE, "CSS cuts it, not a character count");
      assert.equal(flagged.labelInside, true, `${width}px`);
      assert.equal(flagged.labelCut, false, `${width}px: the title gives way, not the label`);
      assert.equal(flagged.sameLine, true, `${width}px: the label sits beside the title`);
      assert.ok(flagged.lineHeight < flagged.fontSize * 1.7, `${width}px: one line, ${flagged.lineHeight}px`);
      assert.equal(flagged.lines, 3);

      // More markers than one line holds: they wrap rather than clip.
      assert.ok(flagged.marksRows >= 2, `${width}px: ${flagged.marksRows} line(s) of markers`);
      assert.equal(await studyCell(WAITING).locator(".admin-study-marks .admin-flag").first().evaluate((f) => getComputedStyle(f).marginLeft), "0px");

      // The original is one line, cut, with the whole of it in its title.
      assert.equal(flagged.originalInside, true, `${width}px`);
      assert.equal(flagged.originalCut, true, `${width}px`);
      assert.equal(flagged.originalEllipsis, "ellipsis");
      assert.ok(flagged.originalHeight < flagged.fontSize * 2, `${width}px: one line, ${flagged.originalHeight}px`);

      // ---- Nothing to flag: one line, and the row as tall as its other cells make it.
      for (const rid of [PLAIN, NO_LABEL]) {
        const plain = await study(rid);
        assert.equal(plain.lines, 1, `${width}px`);
        assert.deepEqual(plain.marks, []);
        assert.equal(plain.originalInside, null);
        assert.ok(plain.lineHeight < plain.fontSize * 1.7, `${width}px: one line, ${plain.lineHeight}px`);
        assert.equal(plain.height, plain.emptied, `${width}px: the Study cell adds height`);
        assert.equal(plain.titleCut, true);
      }
      assert.equal((await study(PLAIN)).labelInside, true);
      assert.equal((await study(PLAIN)).labelCut, false);
      assert.equal((await study(PLAIN)).sameLine, true);
      assert.equal((await study(NO_LABEL)).labelInside, null);

      // ---- A label too long for the line is cut as well, and leaves the title room.
      const longLabel = await study(BLANK);
      assert.equal(longLabel.labelInside, true, `${width}px`);
      assert.equal(longLabel.labelCut, true, `${width}px`);
      assert.ok(longLabel.titleWidth > 100, `${width}px: title ${longLabel.titleWidth}px`);
      assert.deepEqual(longLabel.marks.filter(([, seen]) => !seen), []);
    }
    assert.match(await rowOf(WAITING).locator("td").first().textContent(), /^\d{5}$/);

    // ---- The full values are in the titles.
    assert.equal(await studyCell(WAITING).getAttribute("title"), LONG_TITLE);
    assert.equal(await studyCell(WAITING).locator(".admin-xo-original").getAttribute("title"), LONG_ORIGINAL);
    assert.equal(await studyCell(WAITING).locator(".admin-xo-original").textContent(), `\u21B3 ${LONG_ORIGINAL}`);
    assert.equal(await studyCell(BLANK).locator(".admin-study-label").getAttribute("title"), `Study ${LONG_LABEL}`);

    // ---- Too narrow for the columns: the frame scrolls rather than crushing them.
    await page.setViewportSize({ width: 900, height: 900 });
    const narrow = await frame();
    assert.ok(narrow.scrollWidth > narrow.clientWidth);
    assert.equal(await page.locator("#admin-tab-entries .admin-table-wrap").evaluate((el) => getComputedStyle(el).overflowX), "auto");
    await page.setViewportSize({ width: 1280, height: 900 });

    // ---- Only this table is fixed; the others that share .admin-table are not.
    assert.equal(await page.locator("#admin-tab-entries table").evaluate((t) => getComputedStyle(t).tableLayout), "fixed");
    assert.equal(await page.locator("#admin-tab-stats table.admin-table").first().evaluate((t) => getComputedStyle(t).tableLayout), "auto");

    // ---- Outcome and Approved by are cut, with the full text on hover.
    const cells = rowOf(WAITING).locator("td");
    const outcome = cells.nth(3);
    assert.equal(await outcome.getAttribute("title"), LONG_OUTCOME);
    assert.equal(await outcome.textContent(), LONG_OUTCOME);
    assert.deepEqual(await outcome.evaluate((c) => {
      const s = getComputedStyle(c);
      return [s.textOverflow, s.whiteSpace, c.scrollWidth > c.clientWidth];
    }), ["ellipsis", "nowrap", true]);
    const approvedBy = cells.nth(8);
    assert.equal(await approvedBy.getAttribute("title"), LONG_ADMIN);
    assert.equal(await approvedBy.evaluate((c) => getComputedStyle(c).textOverflow), "ellipsis");
    assert.equal(await rowOf(BUSY).locator("td").nth(8).getAttribute("title"), null);   // "—"

    // ---- Skips: the number, with the rest in the tooltip.
    const skips = cells.nth(6);
    assert.equal((await skips.textContent()).trim(), "12!");
    const metric = skips.locator(".admin-skip-metric");
    assert.equal(await metric.getAttribute("title"), "14 skip events from 12 distinct validators, 4 issue reports");
    assert.match(await metric.getAttribute("class"), /\bescalated\b/);
    assert.equal(await skips.locator(".admin-skip-issue").getAttribute("title"), "4 issue reports");
    const oneSkip = rowOf(BUSY).locator("td").nth(6);
    assert.equal((await oneSkip.textContent()).trim(), "1");
    assert.equal(await oneSkip.locator(".admin-skip-metric").getAttribute("title"), "1 skip event from 1 distinct validator");
    assert.doesNotMatch(await oneSkip.locator(".admin-skip-metric").getAttribute("class"), /\bescalated\b/);
    assert.equal(await oneSkip.locator(".admin-skip-issue").count(), 0);
    assert.equal(await page.locator("#admin-table-body").evaluate((b) => /validators?\b/.test(
      [...b.querySelectorAll(".admin-skip-metric")].map((m) => m.textContent).join(" "))), false);

    // ---- Validators: 1 and 2 with the handles in tooltips, and no LLM badge.
    const validators = cells.nth(5);
    assert.equal((await validators.textContent()).trim(), "12");
    assert.deepEqual(await validators.locator(".val-badge").evaluateAll((b) => b.map((x) => x.title)), ["ana", "ben"]);
    assert.equal(await page.locator("#admin-table-body .val-badge-llm").count(), 0);
    assert.equal(await page.locator("#admin-table-body .admin-cell-validators").evaluateAll(
      (c) => c.some((x) => /LLM/.test(x.textContent))), false);
    assert.equal(await page.locator("#admin-table-body .admin-cell-validators").evaluateAll(
      (c) => c.map((x) => x.querySelectorAll(".val-badge").length).every((n) => n === 2)), true);
    assert.equal((await rowOf(BUSY).locator(".admin-cell-validators").textContent()).trim(), "1—");
    assert.equal((await rowOf(BLANK).locator(".admin-cell-validators").textContent()).trim(), "——");

    // The LLM's other signals stay.
    assert.equal(await rowOf(WAITING).locator('.admin-flag.flag-llm[title="LLM error"]').count(), 1);
    assert.equal(await rowOf(WAITING).locator(".admin-cell-agree .agree-llm-dissent").getAttribute("title"),
                 "LLM disagrees with the validators on: type, original, outcome");

    // ---- Shorter heads, the full word in the title and for screen readers.
    const validatorsHead = page.locator('#admin-entries-head [data-sort="validators"]');
    const agreementHead = page.locator('#admin-entries-head [data-sort="agreement"]');
    assert.match(await validatorsHead.getAttribute("title"), /^Validators/);
    assert.equal(await validatorsHead.getAttribute("aria-label"), "Validators");
    assert.match(await agreementHead.getAttribute("title"), /^Agreement/);
    assert.equal(await agreementHead.getAttribute("aria-label"), "Agreement");

    // ---- The sorts on the shortened heads still reach the server.
    lists.length = 0;
    await validatorsHead.click();
    await page.waitForTimeout(60);
    assert.match(lists.at(-1), /sort=validators/);
    assert.match(await page.locator('#admin-entries-head [data-sort="validators"]').getAttribute("class"), /th-sort-active/);

    // ---- No Approve column; the Pending approval pill is the link, and only that one.
    assert.equal(hasColumn(await heads(), "Approve"), false);
    assert.equal((await heads()).length, 10);
    assert.equal(await page.locator("#admin-table-body .admin-cell-approve").count(), 0);
    assert.equal(await page.locator("#admin-table-body .admin-to-pending").count(), 1);
    const pill = rowOf(WAITING).locator(".admin-to-pending");
    assert.equal(await pill.textContent(), "Pending approval");
    assert.equal(await pill.evaluate((b) => b.tagName), "BUTTON");
    assert.match(await pill.getAttribute("class"), /\badmin-status\b.*\bstatus-consensus\b/);
    assert.equal(await pill.getAttribute("data-id"), WAITING);
    assert.equal(await pill.getAttribute("data-mine"), "");
    assert.match(await pill.getAttribute("title"), /Pending Approval/);
    assert.equal(await rowOf(REVIEW).locator("span.admin-status").textContent(), "Needs review");
    assert.equal(await rowOf(AUTO).locator("span.admin-status").textContent(), "Auto-validated");
    assert.match(await rowOf(AUTO).locator("span.admin-status").getAttribute("title"), /No admin reviewed it/);

    // ---- Approved by: only on Validated; every other list goes without it.
    for (const filter of ["all", "needs_review", "skipped", "admin_comments", "quality_flagged", "extra_originals",
                          "original_removal", "shared_outcome", "auto_validated", "rejected"]) {
      await filterTo(filter);
      assert.equal(hasColumn(await heads(), "Approved by"), false, filter);
      assert.equal((await heads()).length, 9, filter);
    }

    // Leaving Validated takes the rows' cell too, and drops any sort on the column.
    await filterTo("validated");
    assert.equal(hasColumn(await heads(), "Approved by"), true);
    assert.equal((await heads()).length, 10);
    await page.locator('#admin-entries-head [data-sort="approved_by"]').click();
    await page.waitForTimeout(60);
    assert.match(lists.at(-1), /sort=approved_by/);
    await filterTo("all");
    assert.equal(hasColumn(await heads(), "Approved by"), false);
    assert.equal(await page.locator("#admin-table-body > tr").first().locator("td").count(), 9);
    assert.doesNotMatch(lists.at(-1), /sort=/);
    assert.equal(await page.evaluate(() => _adminSort), "");
    assert.deepEqual(await overflowing(), []);

    // Back again, and by way of Pending Approval, whose own head does not have it either.
    await filterTo("validated");
    assert.equal(hasColumn(await heads(), "Approved by"), true);
    await filterTo("all");
    await filterTo("pending_approval");
    assert.equal(hasColumn(await heads(), "Approved by"), false);
    assert.equal((await heads()).length, 8);
    await filterTo("validated");
    assert.equal(hasColumn(await heads(), "Approved by"), true);
    assert.equal((await heads()).length, 10);
    assert.equal(await page.locator("#admin-table-body > tr").first().locator("td").count(), 10);
    await filterTo("pending_approval");
    await filterTo("all");
    assert.equal(hasColumn(await heads(), "Approved by"), false);
    assert.equal(await page.locator(".ap-table").count(), 0);
    assert.equal(await page.locator("#admin-table-body > tr").first().locator("td").count(), 9);

    // ---- Pending Approval fits as well. Where the pill lands is in test_approval_view_ui.
    await filterTo("pending_approval");
    const approval = await frame();
    assert.ok(approval.scrollWidth <= approval.clientWidth, `${approval.scrollWidth} > ${approval.clientWidth}`);

    // Its flags are in their own wrapping column, and its title wraps: a long title
    // hides nothing here, so the Study cell keeps its own layout.
    const chips = await page.locator("#admin-table-body tr.ap-row .ap-cell-look").first().evaluate((cell) => {
      const box = cell.getBoundingClientRect();
      return [...cell.children].filter((c) => {
        const r = c.getBoundingClientRect();
        return r.left < box.left - 0.5 || r.right > box.right + 0.5 || r.bottom > box.bottom + 0.5;
      }).map((c) => c.textContent);
    });
    assert.deepEqual(chips, []);
    assert.equal(await page.locator("#admin-table-body .admin-study-marks").count(), 0);
    assert.equal(await page.locator("#admin-table-body tr.ap-row .ap-title").first().evaluate(
      (t) => t.scrollWidth <= t.clientWidth && t.getBoundingClientRect().height > parseFloat(getComputedStyle(t).lineHeight) * 1.5), true);

    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(errors, []);
    console.log("Entries table width checks passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
