/* Offline browser verification and PNG rendering. No app/DB is started. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');

(async () => {
  const root = __dirname;
  let browser;
  try { browser = await chromium.launch({ headless: true }); }
  catch (error) {
    if (!String(error).includes("Executable doesn't exist")) throw error;
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
  }
  const context = await browser.newContext({ viewport: { width: 1600, height: 1150 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [], network = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', req => { if (/^https?:/.test(req.url())) network.push(req.url()); });
  try {
    const url = pathToFileURL(path.join(root, 'index.html')).href;
    await page.goto(url);
    const data = await page.locator('#guide-data').textContent().then(JSON.parse);
    assert.equal(data.nodes.length, 101);
    assert.equal(data.nodes.filter(n => n.group === 'admin' && !n.parent).length, 11);
    assert.equal(data.nodes.filter(n => n.parent).length, 59);
    await page.locator('#zoom-map').click();
    assert.equal(await page.locator('#zoom-map').getAttribute('aria-pressed'), 'true');
    await page.locator('#zoom-map').click();
    assert.equal(await page.locator('#zoom-map').getAttribute('aria-pressed'), 'false');
    const known = new Set(data.nodes.map(n => n.id));
    for (const node of data.nodes) {
      assert(node.sources.length, `${node.id}: missing source evidence`);
      await fs.access(path.join(root, 'readmes', `${node.id}.md`));
      for (const source of node.sources) {
        assert(source.excerpt.includes(String(source.line)), `${node.id}: no excerpt`);
        await fs.access(path.resolve(root, '../..', source.file));
      }
    }
    for (const view of ['overview', 'comparison', 'inputs', 'processing', 'outputs', 'admin', 'timing', 'code']) {
      await page.locator(`[data-view="${view}"]`).click();
      await page.waitForFunction(v => document.querySelector(`[data-view="${v}"]`).getAttribute('aria-current') === 'page', view);
      assert((await page.locator('#view-title').textContent()).length > 5);
      const diagramIds = await page.locator('.diagram-shell a[data-node]').evaluateAll(els => els.map(el => el.dataset.node));
      for (const id of new Set(diagramIds)) {
        assert(known.has(id), `Unknown diagram node ${id}`);
        await page.locator(`.diagram-shell a[data-node="${id}"]`).first().click();
        await page.waitForFunction(id => document.querySelector('#detail-content h2')?.textContent === JSON.parse(document.querySelector('#guide-data').textContent).nodes.find(n => n.id === id).title, id);
      }
      if (diagramIds.length) {
        await page.locator('#close-detail').click();
        await page.waitForFunction(() => document.querySelector('#detail-panel').hidden);
      }
    }
    await page.locator('[data-view="comparison"]').click();
    await page.waitForSelector('.comparison-table');
    assert.equal(await page.locator('.comparison-table tbody tr').count(), 8);
    for (const scenario of data.comparison.scenarios) {
      await page.locator(`[data-scenario="${scenario.id}"]`).click();
      assert.equal(await page.locator(`[data-scenario="${scenario.id}"]`).getAttribute('aria-pressed'), 'true');
      const detail=await page.locator('#scenario-detail').textContent();
      for(const key of ['source','flora','stored','github'])assert(detail.includes(scenario[key]));
      await page.locator('.scenario-code').click();
      await page.waitForFunction(title => document.querySelector('#detail-title')?.textContent===title, data.nodes.find(n=>n.id===scenario.node).title);
      assert.equal(await page.locator('#detail-title').textContent(), data.nodes.find(n=>n.id===scenario.node).title);
      await page.locator('#close-detail').click();
      await page.waitForFunction(()=>document.querySelector('#detail-panel').hidden);
    }
    await page.locator('[data-scenario="correction"]').click();
    await page.screenshot({path:path.join(root,'comparison-preview.png'),fullPage:true});
    await page.locator('[data-view="admin"]').click();
    await page.waitForSelector('.catalog-tabs');
    assert.equal(await page.locator('.node-card').count(), 11);
    for (const tab of data.nodes.filter(n => n.group === 'admin' && !n.parent)) {
      await page.locator(`[data-tab="${tab.tab}"]`).click();
      const actions = data.nodes.filter(n => n.parent === tab.id);
      assert.equal(await page.locator('.node-card').count(), actions.length + 1);
      for (const action of actions) {
        await page.locator(`.node-card[data-node="${action.id}"]`).click();
        await page.waitForFunction(title => document.querySelector('#detail-title')?.textContent === title, action.title);
        assert((await page.locator('#detail-content').textContent()).includes(action.saves[0]));
        assert((await page.locator('#detail-content .code-ref').first().getAttribute('href')).includes(data.commit));
      }
    }
    await page.locator('#search').fill('Save Note');
    assert(await page.locator('#search-results [data-node]').count() >= 1);
    await page.locator('#search-results [data-node="admin-entries--save-note"]').click();
    await page.waitForFunction(() => document.querySelector('#detail-title')?.textContent === 'Save Note');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#detail-panel').hidden);
    await page.locator('#search').fill('no-match-0123456789');
    assert.equal(await page.locator('#search-results .no-results').count(), 1);
    await page.locator('#search').fill('');
    await page.locator('[data-view="overview"]').click();
    await page.locator('#tour-start').click();
    for (let i = 0; i < data.tour.length; i++) {
      if (i) await page.locator('#tour-next').click();
      await page.waitForFunction(id => location.hash.includes(id), data.tour[i]);
      assert.equal(await page.locator('#tour-step').textContent(), `${i+1} / ${data.tour.length}`);
    }
    // Every deep link can open directly, including nodes outside the main diagrams.
    for (const node of data.nodes) {
      await page.goto(`${url}#view=overview&node=${node.id}`);
      await page.waitForFunction(title => document.querySelector('#detail-title')?.textContent === title, node.title);
    }
    // Browser back restores the previously selected element.
    await page.goto(`${url}#view=inputs&node=input-extractor`);
    await page.goto(`${url}#view=outputs&node=output-api`);
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('#detail-title')?.textContent.includes('Extractor CSV'));
    await page.goto(`${url}#view=overview`);
    await page.screenshot({ path: path.join(root, 'preview.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const view of ['overview','comparison','inputs','processing','outputs','admin','timing','code']) {
      await page.goto(`${url}#view=${view}`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${view}: page overflows mobile viewport`);
    }
    await page.goto(`${url}#view=overview&node=process-save`);
    await page.locator('#detail-panel').scrollIntoViewIfNeeded();
    assert(await page.locator('#detail-title').isVisible());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.deepEqual(errors, [], 'Browser script errors');
    assert.deepEqual(network, [], 'Guide unexpectedly depends on the network');
    // Export diagrams as high-resolution PNGs, and catch text outside its box.
    const art = await browser.newContext({ deviceScaleFactor: 2 });
    const artPage = await art.newPage();
    for (const [name, svg] of Object.entries(data.diagrams)) {
      const [, width, height] = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
      await artPage.setViewportSize({ width: +width, height: +height });
      await artPage.goto(pathToFileURL(path.join(root, 'diagrams', `${name}.svg`)).href);
      const overflow = await artPage.evaluate(() => [...document.querySelectorAll('a[data-node]')].flatMap(a => {
        const rect = a.querySelector('rect').getBBox();
        return [...a.querySelectorAll('text')].filter(t => { const box=t.getBBox(); return box.x < rect.x || box.x+box.width > rect.x+rect.width-7 || box.y+box.height > rect.y+rect.height-2; }).map(t => `${a.dataset.node}: ${t.textContent}`);
      }));
      assert.deepEqual(overflow, [], `${name}: text does not fit diagram box`);
      await artPage.screenshot({ path: path.join(root, 'diagrams', `${name}.png`) });
    }
    await art.close();
    console.log(`PASS: ${data.nodes.length} deep links, 59 actions, 11 tabs, 8 views, 3 comparison scenarios, walkthrough, search, history, mobile layout; 6 high-resolution diagrams rendered. No external requests.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
