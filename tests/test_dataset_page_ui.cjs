/* Run with: node tests/test_dataset_page_ui.cjs
 * (PW_CHANNEL=msedge or chrome to drive an installed browser instead of
 * Playwright's own Chromium.)
 * The public dataset-growth page: summary figures, the period filter, the chart
 * series it hands to Chart.js, the daily-figures table, the CSV, and the states for
 * a failed request and a blocked chart library. All requests are intercepted; Chart.js
 * is replaced by a stub that records its configuration. No server or network runs.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const DAY = 86400000;
const iso = ms => new Date(ms).toISOString().slice(0, 10);

// Ten months of history: source records grow by 5 every fourth day, the pipeline
// first measures the dataset on 1 Jul 2026, and skips the 15th of each month after
// that (a carried day). The server caps `daily` at 120 days; earlier months come
// only from `monthly`.
function makeHistory() {
  const start = Date.parse("2025-12-01T00:00:00Z");
  const end = Date.parse("2026-10-02T00:00:00Z");
  const all = [];
  let source = 1000;
  let flora = null;
  for (let ms = start, i = 0; ms <= end; ms += DAY, i++) {
    const date = iso(ms);
    if (i % 4 === 0) source += 5;
    let measured = false;
    if (date >= "2026-07-01" && !date.endsWith("-15")) {
      flora = source - 50;
      measured = true;
    }
    all.push({ date, total_rows: date >= "2026-07-01" ? flora : null, source_rows: source, measured });
  }
  const byMonth = {};
  for (const point of all) byMonth[point.date.slice(0, 7)] = point;
  const last = all[all.length - 1];
  return {
    generated_at: "2026-10-02T10:07:31+00:00",
    latest: {
      date: last.date, total_rows: last.total_rows, replications: last.total_rows - 40,
      reproductions: 40, source_rows: last.source_rows, measured_on: last.date,
    },
    daily: all.slice(-120),
    monthly: Object.keys(byMonth).sort().map(month => ({ month, ...byMonth[month] })),
  };
}

const CHART_STUB = `
  window.__charts = [];
  window.Chart = function (canvas, config) {
    this.config = config; this.data = config.data; this.options = config.options;
    window.__charts.push(this);
  };
  Chart.prototype.destroy = function () { this.destroyed = true; };
  Chart.prototype.update = function () {};
`;

async function openPage(browser, history, { viewport, blockChart, failFirst } = {}) {
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 900 }, locale: "en-US", acceptDownloads: true });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const root = path.resolve(__dirname, "..");
  let requests = 0;
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname === "cdn.jsdelivr.net") {
      return blockChart
        ? route.fulfill({ status: 503, body: "" })
        : route.fulfill({ status: 200, contentType: "text/javascript", body: CHART_STUB });
    }
    if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" });
    if (url.pathname === "/dataset.html") {
      return route.fulfill({ status: 200, contentType: "text/html", body: await fs.readFile(path.join(root, "docs", "dataset.html")) });
    }
    if (url.pathname === "/api/flora/history") {
      requests += 1;
      if (failFirst && requests === 1) return route.fulfill({ status: 500, body: "" });
      return route.fulfill({ json: history });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  await page.goto("http://flora.test/dataset.html");
  return { page, errors };
}

const lastChart = page => page.evaluate(() => {
  const chart = window.__charts[window.__charts.length - 1];
  const sets = chart.data.datasets;
  return {
    count: window.__charts.length,
    labels: sets.map(s => s.label),
    points: sets[0].data.length,
    tension: sets.map(s => s.tension),
    axes: Object.keys(chart.options.scales),
    firstFlora: sets[0].data.findIndex(p => p.y !== null),
    lastX: sets[0].data[sets[0].data.length - 1].x,
    firstX: sets[0].data[0].x,
  };
});

(async () => {
  const history = makeHistory();
  const daily = history.daily;
  const latest = history.latest;
  const launch = { headless: true };
  if (process.env.PW_CHANNEL) launch.channel = process.env.PW_CHANNEL;
  const browser = await chromium.launch(launch);

  try {
    // ── summary ────────────────────────────────────────────────────────────
    let { page, errors } = await openPage(browser, history);
    await page.locator("body:not(.is-loading)").waitFor();

    assert.equal(await page.locator("#t-total").innerText(), latest.total_rows.toLocaleString("en-US"));
    assert.equal(await page.locator("#t-repro").innerText(), "40");
    const thirtyAgo = daily[daily.length - 31];
    const delta = latest.total_rows - thirtyAgo.total_rows;
    assert.equal(await page.locator("#d-total").innerText(), `+${delta} in the last 30 days`);
    assert.equal(await page.locator("#l-source").innerText(), latest.source_rows.toLocaleString("en-US"));
    assert.equal(await page.locator("#l-removed").innerText(), "−50", "source minus dataset, signed");
    assert.equal(await page.locator("#l-total").innerText(), await page.locator("#t-total").innerText(),
      "the ledger lands on the headline figure");
    assert.equal(await page.locator("#updated").innerText(), "Updated 2 Oct 2026, 10:07 UTC");

    // ── chart: all time reaches past the daily window with month ends ──────
    const months = history.monthly.filter(m => m.date < daily[0].date).length;
    assert.equal(months, 6);
    let chart = await lastChart(page);
    assert.deepEqual(chart.labels, ["FLoRA dataset", "Source records"]);
    assert.equal(chart.points, months + daily.length);
    assert.deepEqual(chart.axes, ["x", "y"], "one y-axis, shared by both series");
    assert.deepEqual(chart.tension, [0, 0], "no curve: it would invent values between days");
    assert.equal(chart.firstFlora, months + daily.findIndex(d => d.total_rows !== null),
      "the dataset line starts at its first measurement, not before");
    assert.equal(chart.lastX - chart.firstX, 275, "x is real time: 31 Dec 2025 to 2 Oct 2026");
    const footnote = await page.locator("#chart-footnote").innerText();
    assert.match(footnote, /Before 5 Jun 2026, one point per month end\./);
    assert.match(footnote, /starts on 1 Jul 2026, the first day the pipeline measured it/);
    assert.equal(await page.locator('[data-range="all"]').getAttribute("aria-pressed"), "true");

    // ── the period filter scopes the chart and the table ───────────────────
    await page.click('[data-range="30"]');
    chart = await lastChart(page);
    assert.equal(chart.points, 30);
    assert.equal(await page.locator('[data-range="30"]').getAttribute("aria-pressed"), "true");
    assert(await page.locator("#chart-footnote").isHidden(), "nothing to footnote inside the last 30 days");
    await page.click('[data-range="90"]');
    assert.equal((await lastChart(page)).points, 90);

    // ── table: changed days by default, eight at a time ────────────────────
    await page.click('[data-range="30"]');
    const window30 = daily.slice(-30);
    const changedDays = window30.filter((d, i) => {
      const prev = i ? window30[i - 1] : daily[daily.length - 31];
      return d.source_rows !== prev.source_rows || d.total_rows !== prev.total_rows;
    });
    assert.equal(await page.locator("#table-body tr").count(), Math.min(8, changedDays.length));
    assert.equal(await page.locator("#table-more").innerText(), `Show all ${changedDays.length} days with a change`);
    await page.click("#table-more");
    assert.equal(await page.locator("#table-body tr").count(), changedDays.length);
    assert(await page.locator("#table-more").isHidden());
    const newest = changedDays[changedDays.length - 1].date.split("-");
    const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    assert.equal(await page.locator("#table-body tr th").first().innerText(),
      `${+newest[2]} ${MONTHS[newest[1] - 1]} ${newest[0]}`, "most recent first");

    await page.check("#all-days");
    await page.click("#table-more");
    assert.equal(await page.locator("#table-body tr").count(), 30);
    const carried = page.locator("#table-body tr", { hasText: "15 Sep 2026" });
    assert.equal(await carried.locator(".tag").innerText(), "CARRIED");
    assert.match(await carried.locator(".tag").getAttribute("title"), /carried forward from 14 Sep 2026/);

    await page.click('[data-range="all"]');
    await page.click("#table-more");
    const first = page.locator("#table-body tr", { hasText: "1 Jul 2026" });
    assert.equal(await first.locator(".tag").innerText(), "FIRST");
    const before = page.locator("#table-body tr", { hasText: "30 Jun 2026" });
    assert.equal(await before.locator("td").first().innerText(), "—", "no figure before the first run");
    assert.match(await page.locator("#table-summary").innerText(), /Daily figures cover the last 120 days/);

    // ── CSV follows the period ─────────────────────────────────────────────
    await page.click('[data-range="30"]');
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
    assert.equal(download.suggestedFilename(), "flora-dataset-growth-2026-10-02.csv");
    const csv = (await fs.readFile(await download.path(), "utf8")).trim().split("\n");
    assert.equal(csv[0], "date,flora_dataset,flora_measured,source_records");
    assert.equal(csv.length, 31);
    assert.equal(csv[csv.length - 1], `2026-10-02,${latest.total_rows},true,${latest.source_rows}`);
    assert.deepEqual(errors, []);
    await page.close();

    // ── a short history offers no window wider than itself ──────────────────
    const short = { ...history, daily: daily.slice(-20), monthly: history.monthly.slice(-1) };
    ({ page, errors } = await openPage(browser, short));
    await page.locator("body:not(.is-loading)").waitFor();
    assert(await page.locator("#controls").isHidden(), "a lone All time button is no choice");
    assert.match(await page.locator("#d-total").innerText(), /since 13 Sep 2026$/);
    await page.close();

    // ── a blocked chart library still leaves every figure readable ──────────
    ({ page, errors } = await openPage(browser, history, { blockChart: true }));
    await page.locator("body:not(.is-loading)").waitFor();
    assert(await page.locator("#chart-fallback").isVisible());
    assert(await page.locator("#plot").isHidden());
    assert.equal(await page.locator("#table-body tr").count(), 8);
    await page.close();

    // ── a failed request says so, and Try again recovers ────────────────────
    ({ page, errors } = await openPage(browser, history, { failFirst: true }));
    await page.locator("#error").waitFor();
    assert(await page.locator("#report").isHidden());
    await page.click("#retry");
    await page.locator("body:not(.is-loading)").waitFor();
    assert(await page.locator("#error").isHidden());
    assert.equal(await page.locator("#t-total").innerText(), latest.total_rows.toLocaleString("en-US"));
    await page.close();

    // ── phone width: no sideways scroll ─────────────────────────────────────
    ({ page, errors } = await openPage(browser, history, { viewport: { width: 390, height: 844 } }));
    await page.locator("body:not(.is-loading)").waitFor();
    await page.check("#all-days");
    await page.click("#table-more");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.equal(overflow, 0);
    assert.deepEqual(errors, []);
    await page.close();

    console.log("dataset page UI: all checks passed");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
