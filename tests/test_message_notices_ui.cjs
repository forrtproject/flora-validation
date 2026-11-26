/* Run with: node tests/test_message_notices_ui.cjs
 * Messages from the review team. Validators: the 30-second poll (badge, toast, the
 * open list), which fetches the whole inbox only when something arrived, never
 * touches an open thread, and never carries over to another sign-in. Admins: the
 * validator's email beside the handle, and a toast that says whether they were
 * emailed. All requests are intercepted. No web server, database, or remote
 * service runs. The page's clock is Playwright's, so no test waits 30 seconds.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const shots = process.env.FLORA_UI_SCREENSHOTS;
const TOAST = "✉ New message from the review team.";
const FILES = {
  "/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
  "/adjudication.js": "adjudication.js", "/adjudication.css": "adjudication.css",
};

async function serveFile(route, url) {
  if (url.hostname !== "flora.test") return route.fulfill({ status: 200, body: "" }), true;
  if (!FILES[url.pathname]) return false;
  const type = url.pathname.endsWith(".js") ? "text/javascript" : url.pathname.endsWith(".css") ? "text/css" : "text/html";
  await route.fulfill({ status: 200, contentType: type, body: await fs.readFile(path.join(root, "docs", FILES[url.pathname])) });
  return true;
}

// A switch a route waits for: closed by close(), so a test can act while a request
// is in flight.
function gate() {
  let open = Promise.resolve(), release = () => {};
  return {
    pass: () => open,
    close() { open = new Promise(resolve => { release = resolve; }); },
    open() { release(); open = Promise.resolve(); },
  };
}

async function until(check, what) {
  for (let i = 0; i < 400; i++) {
    if (await check()) return;
    await new Promise(r => setTimeout(r, 25));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/* ---------- Validator ---------- */

// The server as far as messages go: one inbox per signed-in validator. Each answer
// is taken when the request arrives and sent when its gate lets it through, so an
// answer can be overtaken by what happens meanwhile.
function messageServer(messages = []) {
  const server = {
    who: 7,
    inboxes: { 7: messages, 8: [] },
    calls: { list: 0, unread: 0, read: 0 },
    gates: { list: gate(), unread: gate(), read: gate() },
    fail: { list: false, unread: false, read: false },
    unreadAnswer: null,
    async answer(route, url) {
      const inbox = server.inboxes[server.who];
      const name = url.pathname === "/api/messages" ? "list"
        : url.pathname === "/api/messages/unread" ? "unread"
        : /^\/api\/messages\/\d+\/read$/.test(url.pathname) ? "read"
        : /^\/api\/messages\/\d+\/reply$/.test(url.pathname) ? "reply" : null;
      if (!name) return false;
      if (name === "reply") return route.fulfill({ json: { ok: true, id: 999 } }), true;
      server.calls[name]++;
      const ours = inbox.filter(m => m.direction === "outbound");
      const json = name === "list" ? { messages: structuredClone(inbox) }
        : name === "unread" ? (server.unreadAnswer
          || { unread: ours.filter(m => !m.is_read).length, latest_id: ours.length ? Math.max(...ours.map(m => m.id)) : null })
        : { ok: true };
      const failed = server.fail[name];
      await server.gates[name].pass();
      if (failed) return route.fulfill({ status: 500, json: { detail: "down" } }), true;
      if (name === "read") inbox.find(m => m.id === +url.pathname.split("/")[3]).is_read = true;
      await route.fulfill({ json }).catch(() => {});   // the page may be closed by now
      return true;
    },
  };
  return server;
}

const message = (id, extra = {}) => ({ id, subject: `Subject ${id}`, body: `Body ${id}`, is_read: false, sent_by: "Hamid",
  sent_at: `2026-11-2${id % 10}T10:00:00Z`, direction: "outbound", parent_id: null, queue_id: null, ...extra });
const person = (coder_id, handle) => ({ coder_id, handle, validator_tier: 0, onboarded: true, last_seen_update: 1, update_version: 1 });

// Signed in through /api/me, so the app's own enterGame runs.
async function signIn(browser, server, { waitForList = true } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  page.setDefaultTimeout(10_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.clock.install();
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (await serveFile(route, url)) return;
    if (await server.answer(route, url)) return;
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "validator", validator: person(7, "sam") } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ json: [] });
    if (url.pathname === "/api/next-pairs") return route.fulfill({ json: { pairs: [] } });
    if (url.pathname.startsWith("/api/disagreements/")) return route.fulfill({ json: { available: false } });
    return route.fulfill({ json: { records: [], points: 0, rank: 1, assignments: [] } });
  });
  await page.goto("http://flora.test/");
  await page.evaluate(() => {
    document.querySelector("#mobile-warning")?.classList.add("dismissed");
    // Counts finished polls, so "nothing happened" is checked after the answer, not before.
    window.polls = 0;
    const real = pollMessages;
    pollMessages = async () => { try { await real(); } finally { window.polls++; } };
  });
  await page.locator("#game-screen").waitFor();
  if (waitForList) {
    await until(() => server.calls.list === 1, "the sign-in /messages request");
    await page.waitForFunction(() => _inboxLatestId !== null);
  }
  return { page, errors };
}

// The next 30-second tick, and its answer unless a test is holding it.
async function tick(page, server, { answered = true } = {}) {
  const { calls, polls } = { calls: server.calls.unread, polls: await page.evaluate(() => window.polls) };
  await page.clock.fastForward(30_000);
  await until(() => server.calls.unread > calls, "the poll");
  if (answered) await until(() => page.evaluate(n => window.polls > n, polls), "the poll's answer");
}

const toast = page => page.locator("#toast").textContent();
const clearToast = page => page.evaluate(() => { document.querySelector("#toast").textContent = ""; hideToast(); });
// Null when hidden.
const badge = page => page.evaluate(() => {
  const el = document.querySelector("#inbox-badge");
  return el.classList.contains("hidden") ? null : el.textContent;
});
const subjects = page => page.locator(".inbox-conv-subject").allTextContents();
const dialogOpen = page => page.locator("#dialog-modal").isVisible();

async function validatorTests(browser) {
  // A message arrives: badge, toast, one fetch of the inbox, no dialog.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    assert.equal(await badge(page), null);

    await tick(page, server);
    assert.equal(await toast(page), "", "no toast on the first poll");
    await tick(page, server);
    assert.equal(server.calls.list, 1, "nothing new: the whole inbox is not fetched");

    server.inboxes[7].push(message(2));
    await tick(page, server);
    assert.equal(await toast(page), TOAST);
    assert.equal(await badge(page), "1");
    assert.equal(server.calls.list, 2);
    assert.equal(await dialogOpen(page), false, "no dialog in the middle of a judgement");

    await clearToast(page);
    await tick(page, server);
    assert.equal(await toast(page), "", "one toast per message");
    assert.equal(server.calls.list, 2);

    // Read in another tab: the badge follows without fetching the inbox.
    server.inboxes[7][1].is_read = true;
    await tick(page, server);
    assert.equal(await badge(page), null);
    assert.equal(server.calls.list, 2);

    // Arrived and read in another tab before this one asked: nothing to announce.
    server.inboxes[7].push(message(3, { is_read: true }));
    await tick(page, server);
    assert.equal(await toast(page), "");
    assert.equal(await badge(page), null);

    // The validator's own reply is no news.
    server.inboxes[7].push(message(4, { direction: "inbound", parent_id: 2, is_read: true }));
    await tick(page, server);
    assert.equal(await toast(page), "");
    assert.equal(server.calls.list, 3, "only message 3 was fetched");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Unread at sign-in: the existing pop-up, once, and no toast on top of it.
  {
    const server = messageServer([message(1), message(2)]);
    const { page, errors } = await signIn(browser, server);
    await page.locator("#dialog-modal").waitFor();
    assert.match(await page.locator("#dialog-message").innerText(), /2 unread messages/);
    await tick(page, server);
    assert.equal(await toast(page), "");
    await page.locator("#dialog-buttons button", { hasText: "Later" }).click();
    await tick(page, server);
    assert.equal(await dialogOpen(page), false, "the pop-up is not doubled");
    assert.equal(await toast(page), "");
    assert.equal(await badge(page), "2");
    assert.equal(server.calls.list, 1);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A poll answered before the sign-in request: still no toast, and the pop-up comes.
  {
    const server = messageServer([message(1)]);
    server.gates.list.close();
    const { page, errors } = await signIn(browser, server, { waitForList: false });
    await until(() => server.calls.list === 1, "the sign-in /messages request");
    await tick(page, server);
    assert.equal(await toast(page), "");
    assert.equal(await badge(page), "1", "the badge need not wait for the inbox");
    server.gates.list.open();
    await page.locator("#dialog-modal").waitFor();
    await page.locator("#dialog-buttons button", { hasText: "Later" }).click();
    await tick(page, server);
    assert.equal(await toast(page), "");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // An open thread with a half-typed reply is left alone.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    await page.locator("#inbox-btn").click();
    await page.locator(".inbox-conv").first().click();
    await page.locator(".inbox-thread-textarea").fill("Half a rep");
    server.inboxes[7].push(message(2));
    await tick(page, server);
    assert.equal(await badge(page), "1");
    assert.equal(await toast(page), TOAST);
    assert.equal(await page.locator(".inbox-thread-view").count(), 1);
    assert.equal(await page.locator(".inbox-thread-textarea").inputValue(), "Half a rep");
    await page.locator(".inbox-back-btn").click();
    await page.locator(".inbox-conv").nth(1).waitFor();
    assert.deepEqual(await subjects(page), ["Subject 1", "Subject 2"]);
    assert.equal(await page.locator(".inbox-unread-badge").count(), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // The list is on screen: it shows what arrives.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    await page.locator("#inbox-btn").click();
    await page.locator(".inbox-conv").first().waitFor();
    server.inboxes[7].push(message(2));
    await tick(page, server);
    assert.deepEqual(await subjects(page), ["Subject 1", "Subject 2"]);
    // Closed, the inbox polls no further of its own.
    await page.locator("#inbox-close-btn").click();
    const before = { ...server.calls };
    await tick(page, server);
    assert.deepEqual({ ...server.calls }, { ...before, unread: before.unread + 1 }, "one poller, not two");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Opening the inbox asks the server, and its answer leaves a thread opened
  // meanwhile alone.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    server.inboxes[7].push(message(2, { is_read: true }));
    await page.locator("#inbox-btn").click();
    await page.locator(".inbox-conv").nth(1).waitFor();
    assert.deepEqual(await subjects(page), ["Subject 1", "Subject 2"], "no poll was needed");
    await page.locator("#inbox-close-btn").click();

    server.inboxes[7].push(message(3));
    server.gates.list.close();
    const before = server.calls.list;
    await page.locator("#inbox-btn").click();
    await until(() => server.calls.list > before, "the refresh");
    await page.locator(".inbox-conv").first().click();
    await page.locator(".inbox-thread-textarea").fill("Typing while it loads");
    server.gates.list.open();
    await until(async () => await badge(page) === "1", "the refresh's answer");
    assert.equal(await page.locator(".inbox-thread-textarea").inputValue(), "Typing while it loads");

    // Back, closed and reopened before its answer: the thread chosen next stays.
    server.gates.list.close();
    await page.locator(".inbox-back-btn").click();
    await page.locator("#inbox-close-btn").click();
    await page.locator("#inbox-btn").click();
    await page.locator(".inbox-conv").nth(1).click();
    await page.locator(".inbox-thread-textarea").fill("Another reply");
    const loads = await page.evaluate(() => _inboxLoadShown);
    server.gates.list.open();
    await page.waitForFunction(n => _inboxLoadShown > n, loads);
    await page.waitForTimeout(100);
    assert.equal(await page.locator(".inbox-thread-textarea").inputValue(), "Another reply");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Opened with nothing new: the rows stay, so a click while it loads is not lost.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    const refresh = async () => {
      const loads = await page.evaluate(() => _inboxLoadShown);
      server.gates.list.close();
      await page.locator("#inbox-btn").click();
      await page.locator(".inbox-conv").first().evaluate(el => { el.dataset.drawn = "before"; });
      server.gates.list.open();
      await page.waitForFunction(n => _inboxLoadShown > n, loads);
      return page.locator('.inbox-conv[data-drawn="before"]').count();
    };
    assert.equal(await refresh(), 1, "the same list is not drawn again");
    await page.locator("#inbox-close-btn").click();
    server.inboxes[7][0].is_read = false;   // only its state changed
    assert.equal(await refresh(), 0, "a list that changed is");
    assert.equal(await page.locator(".inbox-unread-badge").count(), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A toast shows over the site banner, also while a disagreement is open.
  {
    const { page, errors } = await signIn(browser, messageServer([]));
    const above = () => page.evaluate(() => {
      document.body.classList.add("has-maint-banner");
      showToast("Hello");
      const z = sel => +getComputedStyle(document.querySelector(sel)).zIndex;
      return z("#toast") > z("#maint-banner");
    });
    assert.equal(await above(), true);
    await page.evaluate(() => document.body.insertAdjacentHTML("beforeend", '<div class="adj-judge-overlay"></div>'));
    assert.equal(await above(), true);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A message just opened does not turn unread again while its /read is on its way.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    server.inboxes[7].push(message(2));
    await tick(page, server);
    assert.equal(await badge(page), "1");
    await page.locator("#inbox-btn").click();
    await page.locator(".inbox-conv").nth(1).waitFor();

    // A poll already on its way when the message is opened…
    server.gates.unread.close();
    server.gates.read.close();
    const polls = await page.evaluate(() => window.polls);
    await tick(page, server, { answered: false });
    await page.locator(".inbox-conv").nth(1).click();
    assert.equal(await badge(page), null);
    server.gates.unread.open();
    await until(() => page.evaluate(n => window.polls > n, polls), "the poll's answer");
    assert.equal(await badge(page), null, "a count from before it was opened");
    // …a poll sent while the /read is on its way…
    await tick(page, server);
    assert.equal(await badge(page), null, "a count from before the /read landed");
    // …and the list.
    await page.locator(".inbox-back-btn").click();
    await page.locator(".inbox-conv").nth(1).waitFor();
    assert.equal(await page.locator(".inbox-unread-badge").count(), 0);
    assert.equal(await badge(page), null);
    server.gates.read.open();
    await until(() => server.inboxes[7][1].is_read, "the /read");
    await tick(page, server);
    assert.equal(await badge(page), null);
    assert.equal(await page.evaluate(() => _readsInFlight), 0);

    // A /read that failed: the server's answer counts again.
    server.inboxes[7].push(message(3));
    await tick(page, server);
    server.fail.read = true;
    await page.locator(".inbox-conv").nth(2).click();
    await page.waitForFunction(() => _readsInFlight === 0);
    await tick(page, server);
    assert.equal(await badge(page), "1");
    await page.locator(".inbox-back-btn").click();
    await page.locator(".inbox-conv").nth(2).waitFor();
    assert.equal(await page.locator(".inbox-unread-badge").count(), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Failures are silent, change nothing, and the next poll makes up for them.
  {
    const server = messageServer([message(1)]);
    const { page, errors } = await signIn(browser, server);
    await page.locator("#dialog-buttons button", { hasText: "Later" }).click();
    server.inboxes[7].push(message(2));
    server.fail.unread = true;
    await tick(page, server);
    assert.equal(await toast(page), "");
    assert.equal(await badge(page), "1");
    server.fail.unread = false;
    server.fail.list = true;
    await tick(page, server);
    assert.equal(await toast(page), "", "not announced before it can be shown");
    assert.equal(await badge(page), "1");
    assert.equal(await page.locator("#game-screen").isVisible(), true, "still signed in");
    server.fail.list = false;
    // A server without the endpoint (or a test that does not stub it) changes nothing.
    const lists = server.calls.list;
    for (const odd of [{ records: [] }, { latest_id: 99 }, []]) {
      server.unreadAnswer = odd;
      await tick(page, server);
      assert.equal(await badge(page), "1");
    }
    assert.equal(server.calls.list, lists);
    server.unreadAnswer = null;
    await tick(page, server);
    assert.equal(await toast(page), TOAST);
    assert.equal(await badge(page), "2");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Signed out, and signed in as someone else: nothing carries over, and an answer
  // on its way is ignored.
  {
    const server = messageServer([message(1, { is_read: true })]);
    server.inboxes[8] = [message(50, { is_read: true })];
    const { page, errors } = await signIn(browser, server);
    server.inboxes[7].push(message(60));   // newer than all of jo's
    server.gates.unread.close();
    const polls = await page.evaluate(() => window.polls);
    await tick(page, server, { answered: false });
    await page.evaluate(() => { state.coder = null; });
    const calls = server.calls.unread;
    await page.clock.fastForward(30_000);
    await page.waitForTimeout(100);
    assert.equal(server.calls.unread, calls, "signed out: no poll");
    server.who = 8;
    await page.evaluate(coder => { state.coder = coder; return enterGame(); }, person(8, "jo"));
    await page.waitForFunction(() => _inboxLatestId === 50);
    server.gates.unread.open();
    await until(() => page.evaluate(n => window.polls > n, polls), "sam's answer");
    assert.equal(await toast(page), "", "sam's message is not jo's");
    assert.equal(await badge(page), null);

    await tick(page, server);
    assert.equal(await toast(page), "", "jo's newest id is higher than sam's, but it is not news");
    const before = server.calls.unread;
    await page.clock.fastForward(30_000);
    await page.waitForTimeout(200);
    assert.equal(server.calls.unread, before + 1, "a second sign-in does not stack timers");
    assert.deepEqual((await page.evaluate(() => _inboxMessages.map(m => m.id))), [50]);

    // The inbox itself on its way while the account changes.
    server.inboxes[8].push(message(51));
    server.gates.list.close();
    const lists = server.calls.list;
    await tick(page, server, { answered: false });
    await until(() => server.calls.list > lists, "jo's inbox");
    server.who = 7;
    server.inboxes[7] = [message(1, { is_read: true })];
    await page.evaluate(coder => { state.coder = coder; startMessagesPoll(); }, person(7, "sam"));
    server.gates.list.open();
    await page.waitForTimeout(200);
    assert.equal(await toast(page), "");
    assert.equal(await badge(page), null);
    assert.deepEqual((await page.evaluate(() => _inboxMessages.map(m => m.id))), []);
    assert.deepEqual(errors, []);
    await page.close();
  }

  // Two list requests answered in the wrong order: the newer list stays.
  {
    const server = messageServer([]);
    const { page, errors } = await signIn(browser, server);
    const ids = await page.evaluate(async ([older, newer]) => {
      const answers = [], real = api;
      api = path => path === "/messages" ? new Promise(resolve => answers.push(resolve)) : real(path);
      const first = _loadInbox(), second = _loadInbox();
      answers[1]({ messages: newer });
      await second;
      answers[0]({ messages: older });
      await first;
      api = real;
      return _inboxMessages.map(m => m.id);
    }, [[message(1)], [message(1), message(2)]]);
    assert.deepEqual(ids, [1, 2]);
    assert.equal(await badge(page), "2");
    assert.deepEqual(errors, []);
    await page.close();
  }

  // A hidden tab does not poll, and catches up once when it is shown again.
  {
    const server = messageServer([message(1, { is_read: true })]);
    const { page, errors } = await signIn(browser, server);
    const show = value => page.evaluate(v => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => v });
      document.dispatchEvent(new Event("visibilitychange"));
    }, value);
    await show("hidden");
    server.inboxes[7].push(message(2));
    for (let i = 0; i < 3; i++) await page.clock.fastForward(30_000);
    await page.waitForTimeout(100);
    assert.equal(server.calls.unread, 0);
    await show("visible");
    await until(async () => await toast(page) === TOAST, "the catch-up");
    assert.equal(server.calls.unread, 1);
    assert.equal(await badge(page), "1");

    // The static demo has no server to ask.
    assert.equal(await page.evaluate(() => {
      clearInterval(_messagesPollTimer);
      _messagesPollTimer = null;
      API_MODE = "static";
      startMessagesPoll();
      return _messagesPollTimer;
    }), null);
    assert.deepEqual(errors, []);
    await page.close();
  }
}

/* ---------- Admin ---------- */

const LONG = `${"a-very-long-mailbox-name".repeat(4)}@${"department".repeat(4)}.example.org`;
const CONVERSATIONS = [
  { thread_id: 40, validator_id: 7, validator_handle: "sam", validator_email: "sam@example.org" },
  { thread_id: 41, validator_id: 8, validator_handle: "jo", validator_email: null },
  { thread_id: 42, validator_id: 9, validator_handle: "a_validator_with_a_long_handle_32", validator_email: LONG },
  { thread_id: 43, validator_id: 10, validator_handle: "x", validator_email: '"<b>x</b>"@example.org' },
].map(c => ({ ...c, subject: `Thread ${c.thread_id}`, admin_name: "Hamid", queue_id: null, unread_count: 0,
  last_activity: "2026-11-25T10:00:00Z", last_direction: "outbound", preview: "Hello" }));
const NOTICES = {
  sent: "Message sent. The validator was emailed.",
  no_email: "Message sent. No email on file, so they will only see it in the app.",
  failed: "Message sent, but the email could not be delivered.",
  off: "Message sent. Email is not set up on this server.",
};

async function openAdmin(browser, answers) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
  page.setDefaultTimeout(10_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (await serveFile(route, url)) return;
    const post = route.request().method() === "POST";
    if (url.pathname === "/api/me") return route.fulfill({ json: { kind: "admin", admin: { handle: "Hamid", trusted: true } } });
    if (url.pathname === "/api/leaderboard") return route.fulfill({ json: [] });
    if (url.pathname === "/api/admin/messages") return route.fulfill({ json: { conversations: CONVERSATIONS } });
    if (url.pathname === "/api/admin/validators") {
      return route.fulfill({ json: { validators: CONVERSATIONS.map(c => ({ id: c.validator_id, handle: c.validator_handle, email: c.validator_email })) } });
    }
    const thread = url.pathname.match(/^\/api\/admin\/thread\/(\d+)$/);
    if (thread) {
      const c = CONVERSATIONS.find(x => x.thread_id === +thread[1]);
      return route.fulfill({ json: { messages: [], handle: c.validator_handle, validator_email: c.validator_email,
        subject: c.subject, admin_name: c.admin_name } });
    }
    if (post && url.pathname === "/api/admin/message") {
      return route.fulfill({ json: route.request().postDataJSON().broadcast
        ? { ok: true, broadcast: true, sent: 12, email_notice: null } : { ok: true, email_notice: answers.notice } });
    }
    if (post && /\/api\/admin\/thread\/\d+\/reply$/.test(url.pathname)) {
      return route.fulfill({ json: { ok: true, id: 1, sent_at: "2026-11-25T10:00:00Z", email_notice: answers.notice } });
    }
    if (post && url.pathname === "/api/admin/queue/q1/flag") return route.fulfill({ json: answers.flag });
    if (url.pathname === "/api/admin/entries/r1") {
      return route.fulfill({ json: {
        record: { record_id: "r1", validation_status: "need_review", type: "replication", title_r: "A study",
          validator_1: { validator_id: 7, validator_name: "sam", type_check: "correct", original_check: "correct", outcome_check: "correct" } },
        queue_slots: [{ validator_slot: "human_1", queue_id: "q1", validator_id: 7, flagged: false }],
        validator_stats: {},
      } });
    }
    return route.fulfill({ json: { records: [], entries: [], total: 0, validators: [], counts: {} } });
  });
  await page.goto("http://flora.test/");
  await page.locator("#admin-screen").waitFor();
  await page.evaluate(() => document.querySelector("#mobile-warning")?.classList.add("dismissed"));
  return { page, errors };
}

async function adminTests(browser) {
  const answers = { notice: "sent", flag: null };
  const { page, errors } = await openAdmin(browser, answers);
  const row = id => page.locator(`.msg-convo-row[data-id="${id}"]`);
  // Waits for the toast to be shown, so an earlier one cannot pass for it.
  const toastAfter = async action => {
    await clearToast(page);
    await action();
    await page.locator("#toast.show").waitFor();
    return toast(page);
  };

  await page.locator('[data-tab="messages"]').click();
  await row(40).waitFor();
  assert.equal(await row(40).locator(".msg-email").innerText(), "sam@example.org");
  assert.equal(await row(41).locator(".msg-email").innerText(), "no email on file");
  assert.equal(await row(43).locator(".msg-email").innerText(), '"<b>x</b>"@example.org', "escaped");
  assert.notEqual(
    await row(41).locator(".msg-email").evaluate(el => getComputedStyle(el).color),
    await row(40).locator(".msg-email").evaluate(el => getComputedStyle(el).color), "a missing address is muted");
  const fits = async (locator, what) => assert.ok(
    await locator.evaluate(el => el.scrollWidth <= el.clientWidth), `${what} is not pushed sideways`);
  await fits(page.locator("#msg-convo-list"), "the list");

  for (const [id, shown] of [[40, "sam@example.org"], [41, "no email on file"], [42, LONG]]) {
    await row(id).click();
    const meta = page.locator(".msg-thread-meta");
    await page.locator(".msg-thread-subject", { hasText: `Thread ${id}` }).waitFor();
    assert.equal(await meta.locator(".msg-email").innerText(), shown);
    await fits(page.locator(".msg-thread-header"), "the thread header");
  }
  if (shots) {
    await fs.mkdir(shots, { recursive: true });
    await page.screenshot({ path: path.join(shots, "admin-messages.png") });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await fits(page.locator("#msg-convo-list"), "the list on a phone");
  await page.setViewportSize({ width: 1280, height: 900 });

  for (const [notice, wording] of Object.entries(NOTICES)) {
    answers.notice = notice;

    // Thread reply
    await row(40).click();
    await page.locator(".msg-thread-subject", { hasText: "Thread 40" }).waitFor();
    assert.equal(await toastAfter(async () => {
      await page.locator(".msg-reply-input").fill("Hello");
      await page.locator(".msg-reply-send").click();
    }), wording);
    if (shots) await page.screenshot({ path: path.join(shots, `admin-toast-${notice}.png`) });

    // New message panel
    await page.locator("#msg-compose-btn").click();
    await page.locator("#msg-validator-search").fill("sam");
    await page.locator(".msg-vdrop-item", { hasText: "sam" }).first().click();
    await page.locator("#msg-compose-subject").fill("Hi");
    await page.locator("#msg-compose-body").fill("Hello");
    assert.equal(await toastAfter(() => page.locator("#msg-compose-send").click()), wording);

    // The compose dialog (Validator Stats)
    await page.evaluate(() => { openComposeDialog(7, "sam"); });
    await page.locator("#compose-subject").fill("Hi");
    await page.locator("#compose-body").fill("Hello");
    assert.equal(await toastAfter(() => page.locator("#dialog-buttons button", { hasText: "Send" }).click()), wording);

    // A flag with a reason
    await page.evaluate(() => { delete _adminDetailCache.r1; return openAdminDetail("r1"); });
    answers.flag = { flagged: true, email_notice: notice };
    await page.locator(".val-flag-btn").first().click();
    await page.locator("#flag-reason-input").fill("Please look again");
    assert.equal(await toastAfter(() => page.locator("#dialog-buttons button", { hasText: "Flag" }).click()), wording);
    assert.equal(await page.locator(".val-flag-btn.flagged").count(), 1);

    // Unflagging sends nothing, so it says nothing.
    answers.flag = { flagged: false, email_notice: null };
    await clearToast(page);
    await page.locator(".val-flag-btn").first().click();
    await page.locator(".val-flag-btn:not(.flagged):not([disabled])").waitFor();
    assert.equal(await toast(page), "");
    await page.locator("#admin-detail-close").click();
  }

  // The notice stays up long enough to read, whatever toast came before it.
  answers.notice = "failed";
  await page.evaluate(() => { showToast("Earlier."); showSentNotice({ email_notice: "failed" }); });
  await page.waitForTimeout(2500);
  assert.equal(await page.locator("#toast.show").textContent(), NOTICES.failed);

  // Broadcast
  await page.locator("#msg-compose-btn").click();
  await page.locator("#msg-validator-search").click();
  await page.locator(".msg-vdrop-all").click();
  await page.locator("#msg-compose-subject").fill("Hi all");
  await page.locator("#msg-compose-body").fill("Hello");
  assert.equal(await toastAfter(() => page.locator("#msg-compose-send").click()),
    "Message sent to 12 validators in the app (no emails).");

  // A server from before the notice: still a plain confirmation.
  assert.equal(await toastAfter(() => page.evaluate(() => showSentNotice({ ok: true }))), "Message sent.");
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    await validatorTests(browser);
    await adminTests(browser);
    console.log("message notices UI: ok");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
