/* Adjudication of the FLoRA / Metascience Observatory disagreements
   (forrtproject/fred-data PR #143).

   An isolated feature: loaded after app.js, it uses only adminApi() and
   escapeHtml() from there, and app.js reaches it only through
   window.Adjudication?.…, so either file can fail without breaking the other.
   The server side lives in adjudication/ with its own PostgreSQL schema. */
(function () {
  "use strict";

  const PATH = "/disagreements/status";   // adminApi() adds /api/admin

  function tabButton() { return document.getElementById("admin-disagreements-tab"); }
  function panel() { return document.getElementById("admin-tab-disagreements"); }

  // Called once an admin has signed in: the tab appears only when the feature is
  // switched on. Any failure keeps it hidden.
  async function initAdmin() {
    let status = null;
    try {
      status = await adminApi(PATH);
    } catch (_) {
      status = null;
    }
    tabButton()?.classList.toggle("hidden", !(status && status.enabled));
  }

  function count(value) {
    return Number(value || 0).toLocaleString();
  }

  function render(status) {
    const body = panel();
    if (!body) return;
    // A <div>, not <header>: style.css styles every <header> as the site header.
    const intro = `
      <div class="adj-head">
        <p class="adj-kicker">Observatory disagreements</p>
        <h2>Disagreements</h2>
        <p>Rows where FLoRA's pipeline and the Metascience Observatory give different answers
          about the same replication (fred-data PR #143). Trusted and Senior validators judge
          each row twice; an admin approves the final answer, which can then be published to
          FLoRA through Source Records.</p>
      </div>`;

    if (!status.enabled) {
      body.innerHTML = intro + `<div class="adj-card"><p>This feature is switched off
        (<code>ADJUDICATION_ENABLED</code>).</p></div>`;
      return;
    }
    if (!status.ready) {
      body.innerHTML = intro + `
        <div class="adj-card adj-card-failed" role="alert">
          <b>The feature is switched on, but its setup failed.</b>
          <p>The rest of the app is unaffected; this tab stays inactive until it is fixed.</p>
          ${status.error ? `<pre>${escapeHtml(status.error)}</pre>` : ""}
        </div>`;
      return;
    }

    const c = status.counts || {};
    const stages = [
      ["Imported", c.records, "rows from the PR"],
      ["Waiting for judgements", c.open, "fewer than two judgements"],
      ["Awaiting admin approval", c.awaiting_approval, "two judgements in"],
      ["Approved", c.approved, "final answer agreed"],
      ["Published", c.published, "in Source Records"],
    ];
    body.innerHTML = intro + `
      <div class="adj-card">
        <p class="adj-ready"><span class="adj-dot" aria-hidden="true"></span>Set up and ready</p>
        <dl class="adj-stats">
          ${stages.map(([label, value, note]) => `
            <div><dt>${escapeHtml(label)}</dt><dd>${count(value)}</dd><small>${escapeHtml(note)}</small></div>
          `).join("")}
        </dl>
        <p class="adj-meta">${count(c.judgements)} judgement${Number(c.judgements) === 1 ? "" : "s"} submitted so far.</p>
        ${Number(c.records) ? "" : `<p class="adj-next">Nothing imported yet. Importing the rows is the next step.</p>`}
      </div>`;
  }

  async function openAdminTab() {
    const body = panel();
    if (!body) return;
    body.innerHTML = '<p class="admin-loading">Loading…</p>';
    try {
      render(await adminApi(PATH));
    } catch (error) {
      body.innerHTML = `<p class="faq-error">Could not load the disagreements (${escapeHtml(error.message)}).</p>`;
    }
  }

  window.Adjudication = { initAdmin, openAdminTab };

  // app.js restores an admin session over the network, and that answer can
  // arrive before this file has finished downloading; its call to initAdmin()
  // then found nothing to call. If the admin panel is already showing, do it now.
  // Calling initAdmin() twice is harmless.
  const adminScreen = document.getElementById("admin-screen");
  if (adminScreen && !adminScreen.classList.contains("hidden")) initAdmin();
})();
