/* Run with: node tests/test_dataset_page_ui.cjs
 * (PW_CHANNEL=msedge or chrome to drive an installed browser instead of
 * Playwright's own Chromium.)
 * The public dataset-growth page: summary figures, the period filter, the source
 * records switch (off by default), the chart series it hands to Chart.js, the
 * daily-figures table, the CSV, the flora.csv counts from before the pipeline, and
 * the states for a failed request and a blocked chart library. All requests are intercepted; Chart.js
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
// that (a carried day). Reproductions gain one every tenth day, the rest are
// replications. The server caps `daily` at 120 days; earlier months come only from
// `monthly`.
function makeHistory() {
  const start = Date.parse("2025-12-01T00:00:00Z");
  const end = Date.parse("2026-10-02T00:00:00Z");
  const all = [];
  let source = 1000;
  let flora = null;
  let repros = null;
  for (let ms = start, i = 0; ms <= end; ms += DAY, i++) {
    const date = iso(ms);
    if (i % 4 === 0) source += 5;
    let measured = false;
    if (date >= "2026-07-01" && !date.endsWith("-15")) {
      flora = source - 50;
      repros = 40 + Math.floor(i / 10);
      measured = true;
    }
    const known = date >= "2026-07-01";
    all.push({
      date, total_rows: known ? flora : null,
      replications: known ? flora - repros : null, reproductions: known ? repros : null,
      source_rows: source, measured,
    });
  }
  const byMonth = {};
  for (const point of all) byMonth[point.date.slice(0, 7)] = point;
  const last = all[all.length - 1];
  return {
    generated_at: "2026-10-02T10:07:31+00:00",
    latest: {
      date: last.date, total_rows: last.total_rows, replications: last.replications,
      reproductions: last.reproductions, source_rows: last.source_rows, measured_on: last.date,
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

// The newest chart drawn for a series: every period change redraws all three.
const lastChart = (page, label = "FLoRA dataset") => page.evaluate(label => {
  const chart = window.__charts.filter(c => c.data.datasets[0].label === label).pop();
  const sets = chart.data.datasets;
  return {
    labels: sets.map(s => s.label),
    values: sets[0].data.map(p => p.y),
    points: sets[0].data.length,
    tension: sets.map(s => s.tension),
    axes: Object.keys(chart.options.scales),
    firstFlora: sets[0].data.findIndex(p => p.y !== null),
    lastX: sets[0].data[sets[0].data.length - 1].x,
    firstX: sets[0].data[0].x,
  };
}, label);

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
    const thirtyAgo = daily[daily.length - 31];
    const delta = latest.total_rows - thirtyAgo.total_rows;
    assert.equal(await page.locator("#d-total").innerText(), `+${delta} in the last 30 days`);
    assert(await page.locator("#d-launch").isHidden(), "no launch date in this payload");
    assert.equal(await page.locator("#updated").innerText(), "Updated 2 Oct 2026, 10:07 UTC");
    // Source records are off until asked for.
    assert.equal(await page.locator("#show-sources").isChecked(), false);
    for (const id of ["#ledger", "#legend-source", "#th-source", "#def-source"]) {
      assert(await page.locator(id).isHidden(), `${id} hidden by default`);
    }
    assert(await page.locator("#def-earlier").isHidden(), "no flora.csv counts in this payload");

    // ── chart: all time reaches past the daily window with month ends ──────
    const months = history.monthly.filter(m => m.date < daily[0].date).length;
    assert.equal(months, 6);
    let chart = await lastChart(page);
    assert.deepEqual(chart.labels, ["FLoRA dataset"], "the dataset alone by default");
    assert.equal(chart.points, months + daily.length);
    assert.deepEqual(chart.axes, ["x", "y"]);
    assert.deepEqual(chart.tension, [0], "no curve: it would invent values between days");
    assert.equal(chart.firstFlora, months + daily.findIndex(d => d.total_rows !== null),
      "the dataset line starts at its first measurement, not before");
    assert.equal(chart.lastX - chart.firstX, 275, "x is real time: 31 Dec 2025 to 2 Oct 2026");
    const footnote = await page.locator("#chart-footnote").innerText();
    assert.match(footnote, /Before 5 Jun 2026, one point per month end\./);
    assert.match(footnote, /The dataset line starts on 1 Jul 2026, its first count\./);
    assert.equal(await page.locator('[data-range="all"]').getAttribute("aria-pressed"), "true");

    // ── replications and reproductions: a figure and a chart each ──────────
    const firstRun = daily.findIndex(d => d.total_rows !== null);
    const firstRunDay = daily[firstRun];
    for (const [id, label, key] of [["rep", "Replications", "replications"],
                                    ["repro", "Reproductions", "reproductions"]]) {
      assert.equal(await page.locator(`#${id}-value`).innerText(), latest[key].toLocaleString("en-US"));
      assert.equal(await page.locator(`#${id}-share`).innerText(),
        `${Math.round(latest[key] / latest.total_rows * 100)}% of the dataset`.toUpperCase());
      assert.equal(await page.locator(`#${id}-delta`).innerText(),
        `+${latest[key] - firstRunDay[key]} since it was first measured on 1 Jul`,
        "All time measures the change from the first reading");
      const typed = await lastChart(page, label);
      assert.deepEqual(typed.labels, [label], "one series, on its own scale");
      assert.equal(typed.points, daily.length - firstRun,
        "starts at the first measurement instead of a blank stretch before it");
      assert.equal(typed.values[0], firstRunDay[key]);
      assert.equal(typed.values[typed.values.length - 1], latest[key]);
    }
    assert.equal(await page.locator("#types-note").innerText(),
      "Both charts start on 1 Jul 2026, the first count.");

    // ── the period filter scopes the charts and the table ──────────────────
    await page.click('[data-range="30"]');
    chart = await lastChart(page);
    assert.equal(chart.points, 30);
    assert.equal(await page.locator('[data-range="30"]').getAttribute("aria-pressed"), "true");
    assert(await page.locator("#chart-footnote").isHidden(), "nothing to footnote inside the last 30 days");
    assert.equal((await lastChart(page, "Reproductions")).points, 30);
    assert.equal(await page.locator("#repro-delta").innerText(),
      `+${latest.reproductions - thirtyAgo.reproductions} in the last 30 days`);
    assert(await page.locator("#types-note").isHidden());
    await page.click('[data-range="90"]');
    assert.equal((await lastChart(page)).points, 90);
    assert.equal((await lastChart(page, "Replications")).points, 90);

    // ── table: changed days by default, eight at a time ────────────────────
    await page.click('[data-range="30"]');
    const window30 = daily.slice(-30);
    const changedDays = window30.filter((d, i) => {
      const prev = i ? window30[i - 1] : daily[daily.length - 31];
      return ["total_rows", "replications", "reproductions"].some(k => d[k] !== prev[k]);
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
    assert.equal(csv[0], "date,flora_dataset,replications,reproductions,flora_measured,flora_counted_by");
    assert.equal(csv.length, 31);
    assert.equal(csv[csv.length - 1], [
      "2026-10-02", latest.total_rows, latest.replications, latest.reproductions, true, "",
    ].join(","));

    // ── the switch brings source records back everywhere ──────────────────
    await page.check("#show-sources");
    for (const id of ["#ledger", "#legend-source", "#th-source", "#def-source"]) {
      assert(await page.locator(id).isVisible(), `${id} shown with the switch on`);
    }
    assert.equal(await page.locator("#l-source").innerText(), latest.source_rows.toLocaleString("en-US"));
    assert.equal(await page.locator("#l-removed").innerText(), "−50", "source minus dataset, signed");
    assert.equal(await page.locator("#l-total").innerText(), await page.locator("#t-total").innerText(),
      "the ledger lands on the headline figure");
    chart = await lastChart(page);
    assert.deepEqual(chart.labels, ["FLoRA dataset", "Source records"]);
    assert.deepEqual(chart.tension, [0, 0]);
    assert.equal(await page.locator("#table-body tr").first().locator("td").count(), 4);
    const [withSources] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
    const csvSources = (await fs.readFile(await withSources.path(), "utf8")).trim().split("\n");
    assert.equal(csvSources[0],
      "date,flora_dataset,replications,reproductions,flora_measured,flora_counted_by,source_records");
    assert(csvSources[csvSources.length - 1].endsWith("," + latest.source_rows));
    await page.uncheck("#show-sources");
    assert(await page.locator("#ledger").isHidden());
    assert.deepEqual((await lastChart(page)).labels, ["FLoRA dataset"]);
    assert.deepEqual(errors, []);
    await page.close();

    // ── flora.csv counts before the pipeline, from the validator's launch ──
    const launched = "2026-06-10";
    const backfilled = { ...history, latest: { ...latest, launched_on: launched } };
    let csvCount = 900;
    backfilled.daily = daily.map(d => {
      if (d.date < launched) return { ...d };
      if (d.date < "2026-07-01") {
        csvCount += d.date.endsWith("0") ? 0 : 3;       // a day ending in 0 has no new version
        return { ...d, total_rows: csvCount, replications: csvCount - 40, reproductions: 40,
                 measured: !d.date.endsWith("0"), measured_by: "flora.csv" };
      }
      return { ...d, measured_by: d.total_rows === null ? null : "pipeline" };
    });
    backfilled.daily.find(d => d.date === launched).measured = true;
    ({ page, errors } = await openPage(browser, backfilled));
    await page.locator("body:not(.is-loading)").waitFor();
    const atLaunch = backfilled.daily.find(d => d.date === launched).total_rows;
    assert.equal(await page.locator("#d-launch").innerText(),
      `+${(latest.total_rows - atLaunch).toLocaleString("en-US")} since the validator launched on 10 Jun 2026`);
    assert.match(await page.locator("#chart-footnote").innerText(),
      /Before 1 Jul 2026, counts of the flora\.csv published each day; since then, this site’s pipeline\./);
    assert(await page.locator("#def-earlier").isVisible());
    assert.equal(await page.locator("#pipeline-start").innerText(), "1 Jul 2026");
    const [csvDownload] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
    const csvBackfilled = (await fs.readFile(await csvDownload.path(), "utf8")).trim().split("\n");
    assert(csvBackfilled.some(line => line.startsWith("2026-06-11,") && line.endsWith(",true,flora.csv")));
    assert(csvBackfilled.some(line => line.startsWith("2026-07-02,") && line.endsWith(",true,pipeline")));
    assert.deepEqual(errors, []);
    await page.close();

    // ── the launch and the handover beyond the daily window: the server's figures ──
    const backfilledMonths = {};
    for (const m of history.monthly) backfilledMonths[m.month] = m;
    for (const d of backfilled.daily) backfilledMonths[d.date.slice(0, 7)] = { month: d.date.slice(0, 7), ...d };
    const windowed = {
      ...backfilled,
      daily: backfilled.daily.filter(d => d.date >= "2026-08-01"),
      monthly: Object.keys(backfilledMonths).sort().map(month => backfilledMonths[month]),
      latest: { ...backfilled.latest, launch_total_rows: atLaunch, pipeline_from: "2026-07-01",
                first_counted_on: launched },
    };
    ({ page, errors } = await openPage(browser, windowed));
    await page.locator("body:not(.is-loading)").waitFor();
    assert.equal(await page.locator("#d-launch").innerText(),
      `+${(latest.total_rows - atLaunch).toLocaleString("en-US")} since the validator launched on 10 Jun 2026`);
    assert.equal(await page.locator("#pipeline-start").innerText(), "1 Jul 2026");
    assert(await page.locator("#def-earlier").isVisible());
    // The line starts on a month end, which is not the day of the first count.
    assert.match(await page.locator("#chart-footnote").innerText(),
      /The dataset line starts at the end of Jun 2026; the first count was on 10 Jun 2026\./);
    assert.equal(await page.locator("#types-note").innerText(),
      "Both charts start at the end of Jun 2026; the first count was on 10 Jun 2026.");
    await page.click("#table-more");
    assert.equal(await page.locator("#table-body .tag", { hasText: /^first$/i }).count(), 0,
      "the window's first row is not the first count");
    await page.click('[data-range="30"]');
    assert.doesNotMatch(await page.locator("#chart-footnote").innerText(), /flora\.csv/,
      "a period wholly after the handover does not mention it");
    assert.deepEqual(errors, []);
    await page.close();

    // ── no source records: nothing is "left out" of them ──
    ({ page, errors } = await openPage(browser, { ...history, latest: { ...latest, source_rows: null } }));
    await page.locator("body:not(.is-loading)").waitFor();
    await page.check("#show-sources");
    assert.equal(await page.locator("#l-removed").innerText(), "—");
    assert.deepEqual(errors, []);
    await page.close();

    // ── the first count on the window's first day still reads as the first ──
    const firstInWindow = { ...windowed, latest: { ...windowed.latest, first_counted_on: "2026-08-01" } };
    ({ page, errors } = await openPage(browser, firstInWindow));
    await page.locator("body:not(.is-loading)").waitFor();
    await page.click("#table-more");
    const firstRow = page.locator("#table-body tr")
      .filter({ has: page.locator("th", { hasText: /^1 Aug 2026$/ }) });
    assert.equal(await firstRow.locator(".tag").innerText(), "FIRST");
    assert.deepEqual(errors, []);
    await page.close();

    // ── an older server with no per-day split: figures stay, no invented zeros ─
    const strip = point => {
      const { replications, reproductions, ...rest } = point;
      return rest;
    };
    const older = { ...history, daily: daily.map(strip), monthly: history.monthly.map(strip) };
    ({ page, errors } = await openPage(browser, older));
    await page.locator("body:not(.is-loading)").waitFor();
    assert.equal(await page.locator("#rep-value").innerText(), latest.replications.toLocaleString("en-US"));
    assert(await page.locator("#plot-rep").isHidden());
    assert.equal(await page.locator("#fallback-rep").innerText(), "No history has been recorded for this figure yet.");
    assert.equal(await page.locator("#rep-delta").innerText(), "");
    assert.deepEqual(errors, []);
    await page.close();

    // ── a short history offers no window wider than itself ──────────────────
    const short = { ...history, daily: daily.slice(-20), monthly: history.monthly.slice(-1) };
    ({ page, errors } = await openPage(browser, short));
    await page.locator("body:not(.is-loading)").waitFor();
    assert(await page.locator("#period").isHidden(), "a lone All time button is no choice");
    assert(await page.locator("#show-sources").isVisible(), "the source switch stays");
    assert.match(await page.locator("#d-total").innerText(), /since 13 Sep 2026$/);
    await page.close();

    // ── a blocked chart library still leaves every figure readable ──────────
    ({ page, errors } = await openPage(browser, history, { blockChart: true }));
    await page.locator("body:not(.is-loading)").waitFor();
    for (const [plot, fallback] of [["#plot", "#chart-fallback"], ["#plot-rep", "#fallback-rep"],
                                    ["#plot-repro", "#fallback-repro"]]) {
      assert(await page.locator(fallback).isVisible());
      assert(await page.locator(plot).isHidden());
    }
    assert.match(await page.locator("#fallback-repro").innerText(), /figure it would show is in the table/);
    assert.equal(await page.locator("#repro-value").innerText(), latest.reproductions.toLocaleString("en-US"));
    assert.equal(await page.locator("#table-body tr").count(), 8);
    await page.close();

    // ── a failed request says so, and Try again recovers ────────────────────
    ({ page, errors } = await openPage(browser, history, { failFirst: true }));
    await page.locator("#error").waitFor();
    assert(await page.locator("#report").isHidden());
    assert.notEqual(await page.locator("#controls").getAttribute("hidden"), null,
      "the controls wait for the figures: a click before them would have nothing to draw");
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
