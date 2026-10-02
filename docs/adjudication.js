/* Adjudication of the FLoRA / Metascience Observatory disagreements
   (forrtproject/fred-data PR #143).

   An isolated feature: loaded after app.js, it uses only a few of its helpers
   (api(), adminApi(), escapeHtml(), showToast(), onBackdropClick() and the
   signed-in state.coder), and app.js reaches it only through
   window.Adjudication?.…, so either file can fail without breaking the other.
   The server side lives in adjudication/ with its own PostgreSQL schema. */
(function () {
  "use strict";

  const PATH = "/disagreements/status";   // adminApi() adds /api/admin
  const IMPORT_PATH = "/disagreements/import";
  const KIND_LABELS = {
    "same original, different outcome": "Same original, different outcome",
    "different original": "Different original",
    "we found no original": "FLoRA found no original",
    "MO names no original DOI": "The Observatory gives no original DOI",
  };

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
      ["Imported", "records", "rows from the PR"],
      ["Waiting for judgements", "open", "fewer than two judgements"],
      ["Awaiting admin approval", "awaiting_approval", "two judgements in"],
      ["Approved", "approved", "final answer agreed"],
      ["Published", "published", "in Source Records"],
    ];
    body.innerHTML = intro + `
      <div class="adj-card">
        <p class="adj-ready"><span class="adj-dot" aria-hidden="true"></span>Set up and ready</p>
        <dl class="adj-stats">
          ${stages.map(([label, key, note]) => `
            <div><dt>${escapeHtml(label)}</dt><dd data-stat="${key}">${count(c[key])}</dd><small>${escapeHtml(note)}</small></div>
          `).join("")}
        </dl>
        <p class="adj-meta">${count(c.judgements)} judgement${Number(c.judgements) === 1 ? "" : "s"} submitted so far.</p>
        ${kindList(status.by_kind)}
      </div>
      ${Number(c.records) ? recordsCard(c) : ""}
      ${importCard(status)}`;
    document.getElementById("adj-preview-btn")?.addEventListener("click", () => runImport(false));
    if (Number(c.records)) bindRecordsCard();
  }

  function kindList(byKind) {
    const entries = Object.entries(byKind || {});
    if (!entries.length) return "";
    return `<ul class="adj-kinds">${entries.map(([kind, n]) =>
      `<li><b>${count(n)}</b> ${escapeHtml(KIND_LABELS[kind] || kind)}</li>`).join("")}</ul>`;
  }

  function importCard(status) {
    const imported = Number((status.counts || {}).records);
    const link = status.source_url
      ? `<a href="${escapeHtml(status.source_url)}" target="_blank" rel="noopener">${escapeHtml(status.source || "")}</a>`
      : escapeHtml(status.source || "");
    return `
      <div class="adj-card adj-import">
        <h3>${imported ? "Import again" : "Import the disagreements"}</h3>
        <p>From ${link}, read at a fixed commit so the rows cannot change while they are judged.</p>
        <p class="adj-hint">${imported
          ? "Adds rows that are missing and refreshes rows nobody has judged yet. Judged rows are never changed, and nothing is deleted."
          : "Each row keeps FLoRA's answer and the Observatory's. Abstracts and titles come from OpenAlex and Europe PMC."}
          The preview takes about half a minute and changes nothing.</p>
        <div class="adj-actions">
          <button type="button" class="btn-outline" id="adj-preview-btn">Preview import</button>
        </div>
        <div id="adj-import-result" aria-live="polite"></div>
      </div>`;
  }

  function importSummary(result) {
    const a = result.actions || {};
    const e = result.enrichment || {};
    const kinds = Object.entries(result.by_kind || {}).map(([kind, n]) =>
      `<li><b>${count(n)}</b> ${escapeHtml(KIND_LABELS[kind] || kind)}</li>`).join("");
    const writes = Number(a.new || 0) + Number(a.updated || 0);
    const verb = result.applied ? "Imported" : "An import would";
    const actions = result.applied
      ? `${count(a.new)} new, ${count(a.updated)} updated, ${count(a.unchanged)} unchanged, ${count(a.kept)} kept as judged.`
      : `add ${count(a.new)} new, update ${count(a.updated)}, leave ${count(a.unchanged)} unchanged, and keep ${count(a.kept)} already judged.`;
    const missing = Number(e.replications || 0) - Number(e.abstracts || 0);
    const failures = Number(e.failed_batches || 0) + Number(e.failed_abstracts || 0);
    return `
      <div class="adj-result${result.applied ? " is-done" : ""}">
        <p><b>${count(result.rows)} rows in the file.</b> ${verb} ${actions}</p>
        <ul class="adj-kinds">${kinds}</ul>
        <p class="adj-hint">Abstracts for ${count(e.abstracts)} of ${count(e.replications)} papers
          (${count(e.abstracts_from_europepmc)} from Europe PMC)${missing ? `; ${count(missing)} without one` : ""}.
          Titles for ${count(e.observatory_titles)} of ${count(e.observatory_originals)} Observatory originals
          and ${count(e.flora_titles)} of ${count(e.flora_originals)} FLoRA originals.
          ${failures ? `<b>${count(failures)} lookup${failures === 1 ? "" : "s"} failed</b>; those rows are imported without that detail.` : ""}</p>
        ${!result.applied && writes ? `<div class="adj-actions">
          <button type="button" class="btn-primary" id="adj-apply-btn">Import ${count(writes)} row${writes === 1 ? "" : "s"}</button>
        </div>` : ""}
        ${!result.applied && !writes ? `<p class="adj-hint"><b>Everything is up to date.</b></p>` : ""}
      </div>`;
  }

  // Preview (apply=false) or import. The preview's lookups are cached on the
  // server for an hour, so an import right after it is quick.
  async function runImport(apply) {
    const out = document.getElementById("adj-import-result");
    const buttons = [document.getElementById("adj-preview-btn"), document.getElementById("adj-apply-btn")];
    buttons.forEach(b => { if (b) b.disabled = true; });
    if (out && !apply) out.innerHTML = '<p class="admin-loading">Reading the file and looking up abstracts and titles…</p>';
    const applyBtn = document.getElementById("adj-apply-btn");
    if (applyBtn) applyBtn.textContent = "Importing…";
    try {
      const result = await adminApi(IMPORT_PATH, "POST", { apply });
      if (apply) {
        await openAdminTab();                     // fresh counts
        const fresh = document.getElementById("adj-import-result");
        if (fresh) fresh.innerHTML = importSummary(result);
        if (typeof showToast === "function") showToast(`Imported ${count(result.actions?.new)} new rows.`);
        return;
      }
      if (out) out.innerHTML = importSummary(result);
      document.getElementById("adj-apply-btn")?.addEventListener("click", () => runImport(true));
    } catch (error) {
      if (out) out.innerHTML = `<p class="faq-error">${escapeHtml(error.message)}</p>`;
    } finally {
      document.getElementById("adj-preview-btn")?.removeAttribute("disabled");
    }
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

  // ---------------------------------------------------------------------------
  // Windows (judging and review): focus, Escape, Tab and the page's scroll lock.
  // ---------------------------------------------------------------------------

  const windows = [];      // open windows, newest last: {modal, close, opener}

  function isOpen(modal) { return !!modal && !modal.classList.contains("hidden"); }

  // app.js's own dialogs release the scroll lock when they close; re-assert it.
  function lockScroll() { if (windows.length) document.body.style.overflow = "hidden"; }

  function focusWindow(modal) {
    const panel = modal.querySelector(".faq-panel");
    if (panel && !panel.contains(document.activeElement)) panel.focus({ preventScroll: true });
  }

  function openWindow(modal, close) {
    if (!isOpen(modal)) windows.push({ modal, close, opener: document.activeElement });
    modal.classList.remove("hidden");
    lockScroll();
    focusWindow(modal);
  }

  function closeWindow(modal) {
    modal?.classList.add("hidden");
    const at = windows.findIndex(w => w.modal === modal);
    const entry = at >= 0 ? windows.splice(at, 1)[0] : null;
    if (!windows.length) document.body.style.overflow = "";
    if (entry && entry.opener && document.contains(entry.opener)) entry.opener.focus({ preventScroll: true });
  }

  // Escape closes a window, but not from a text field: typing there and pressing
  // Escape must not throw away a note or a DOI.
  function closesOnEscape(event) {
    return event.key === "Escape" && !event.target.closest("input, textarea, select");
  }

  // Tab and Shift+Tab stay inside the window.
  function trapTab(event, modal) {
    const panel = modal.querySelector(".faq-panel");
    const items = [...modal.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary')]
      .filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!modal.contains(active)) { event.preventDefault(); first.focus(); }
    else if (event.shiftKey && (active === first || active === panel)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
  }

  document.addEventListener("keydown", event => {
    const top = windows[windows.length - 1];
    if (!top || !isOpen(top.modal)) return;
    if (isOpen(document.getElementById("dialog-modal"))) return;    // app.js's dialog is on top
    if (closesOnEscape(event)) { event.preventDefault(); top.close(); return; }
    if (event.key === "Tab") trapTab(event, top.modal);
  });

  function windowShell({ id, titleId, title, bodyId, closeId, extraHeader = "" }) {
    const modal = document.createElement("div");
    modal.id = id;
    modal.className = "faq-overlay adj-judge-overlay hidden";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", titleId);
    modal.innerHTML = `
      <div class="faq-panel adj-judge-panel" tabindex="-1">
        <div class="faq-panel-header">
          <span class="faq-panel-title" id="${titleId}">${title}</span>
          ${extraHeader}
          <button type="button" class="faq-close" id="${closeId}" title="Close" aria-label="Close">×</button>
        </div>
        <div class="adj-judge-body" id="${bodyId}"></div>
      </div>`;
    document.body.appendChild(modal);
    return modal;
  }

  // ---------------------------------------------------------------------------
  // Validators: a bar at the top of the work area ("⚖ 12 Observatory
  // disagreements left for you to judge") and the judging window. Trusted and
  // Senior validators only; the server checks the tier too. A bar rather than a
  // header button: the header has no room left, and one more button there pushed
  // "Sign out" off the screen.
  // ---------------------------------------------------------------------------

  const JUDGE_PATH = "/disagreements";     // api() adds /api
  const SUMMARY_MINUTES = 5;
  const KIND_HELP = {
    "same original, different outcome": "Both name the same original study, but disagree about the outcome.",
    "different original": "They name different original studies.",
    "we found no original": "FLoRA's pipeline found no original study for this replication.",
    "MO names no original DOI": "The Observatory gives no DOI for the original study.",
  };
  const OUTCOME_LABELS = {
    successful: "Successful",
    failed: "Failed",
    mixed: "Mixed",
    "statistically successful but flawed": "Successful but flawed",
    uninformative: "Uninformative",
    "descriptive only": "Descriptive only",
    cannot_be_determined: "Cannot be determined",
    not_a_replication: "Not a replication",
    cannot_tell: "Can't tell",
  };
  const MAIN_OUTCOMES = ["successful", "failed", "mixed"];
  const OBSERVATORY_OUTCOMES = {
    success: "Success", failure: "Failure", inconclusive: "Inconclusive",
    reversal: "Reversal (the opposite effect)",
  };

  let summaryTimer = null;
  let judging = null;      // the /next answer being shown
  let answer = null;       // {original_choice, suggested_doi_o, outcome, note}
  let judgeSeq = 0;        // bumped by each load and by closing: late answers are dropped
  let saving = false;      // a submit or skip is on its way; nothing else may start
  let notice = "";         // said once, at the top of the next record

  function online() {
    return typeof api === "function" && !(typeof API_MODE !== "undefined" && API_MODE === "static");
  }
  function coder() {
    return typeof state !== "undefined" && state ? state.coder : null;
  }
  function judgeBar() { return document.getElementById("adj-validator-bar"); }
  function judgeModal() { return document.getElementById("adj-judge-modal"); }
  function judgeBody() { return document.getElementById("adj-judge-body"); }

  function ensureJudgeBar() {
    if (judgeBar()) return judgeBar();
    const area = document.querySelector("#game-screen .game-area");
    if (!area) return null;
    const bar = document.createElement("div");
    bar.id = "adj-validator-bar";
    bar.className = "adj-validator-bar hidden";
    bar.innerHTML = `
      <span class="adj-bar-text"><span aria-hidden="true">⚖</span>
        <b id="adj-validator-count">0 Observatory disagreements</b> left for you to judge</span>
      <button type="button" class="adj-bar-btn" id="adj-validator-btn">Judge</button>`;
    bar.querySelector("#adj-validator-btn").addEventListener("click", openJudging);
    area.insertBefore(bar, area.firstChild);
    return bar;
  }

  // Called when a validator enters the app. Regular validators never ask.
  async function initValidator() {
    const me = coder();
    if (!online() || !me || Number(me.validator_tier || 0) < 1) return;
    ensureJudgeBar();
    await refreshSummary();
    if (!summaryTimer) {
      summaryTimer = setInterval(() => {
        if (document.visibilityState === "visible" && coder()) refreshSummary();
      }, SUMMARY_MINUTES * 60_000);
    }
  }

  async function refreshSummary() {
    let summary = null;
    try {
      summary = await api(JUDGE_PATH + "/summary");
    } catch (_) {
      return;                    // keep what is shown on a passing failure
    }
    const left = summary && summary.available ? Number(summary.left || 0) : 0;
    const label = document.getElementById("adj-validator-count");
    if (label) label.textContent = `${count(left)} Observatory disagreement${left === 1 ? "" : "s"}`;
    judgeBar()?.classList.toggle("hidden", left === 0);
  }

  function ensureJudgeModal() {
    if (judgeModal()) return judgeModal();
    const modal = windowShell({
      id: "adj-judge-modal", titleId: "adj-judge-title", title: "⚖ Observatory disagreements",
      bodyId: "adj-judge-body", closeId: "adj-judge-close",
      extraHeader: '<span class="adj-judge-progress" id="adj-judge-progress"></span>',
    });
    modal.querySelector("#adj-judge-close").addEventListener("click", closeJudging);
    if (typeof onBackdropClick === "function") onBackdropClick(modal, closeJudging);
    return modal;
  }

  function openJudging() {
    openWindow(ensureJudgeModal(), closeJudging);
    notice = "";
    loadNext();
  }

  function closeJudging() {
    judgeSeq += 1;               // answers still on their way are dropped
    saving = false;
    judging = null;
    answer = null;
    closeWindow(judgeModal());
    refreshSummary();
  }

  function setProgress(data) {
    const el = document.getElementById("adj-judge-progress");
    if (el && data) el.textContent = `${count(data.left)} left · ${count(data.judged)} judged by you`;
  }

  function noticeLine() {
    const text = notice;
    notice = "";
    return text ? `<p class="adj-notice" role="status">${escapeHtml(text)}</p>` : "";
  }

  async function loadNext() {
    const body = judgeBody();
    if (!body) return;
    const seq = ++judgeSeq;
    saving = false;
    body.innerHTML = '<p class="admin-loading">Loading…</p>';
    let next;
    try {
      next = await api(JUDGE_PATH + "/next", "POST");
    } catch (error) {
      if (seq !== judgeSeq) return;
      body.innerHTML = `<div class="adj-judge-message">${noticeLine()}<p class="faq-error">${escapeHtml(error.message)}</p>
        <button type="button" class="btn-outline" id="adj-retry-btn">Try again</button></div>`;
      document.getElementById("adj-retry-btn")?.addEventListener("click", loadNext);
      focusWindow(judgeModal());
      return;
    }
    if (seq !== judgeSeq || !isOpen(judgeModal())) return;
    judging = next;
    setProgress(judging);
    lockScroll();
    if (!judging.record) {
      body.innerHTML = `<div class="adj-judge-message">${noticeLine()}
        <h3>Nothing left for you right now</h3>
        <p>Each disagreement is judged by two validators, and the rest are taken or done.
          You have judged ${count(judging.judged)}. Thank you!</p>
        <button type="button" class="btn-outline" id="adj-done-btn">Close</button></div>`;
      document.getElementById("adj-done-btn")?.addEventListener("click", closeJudging);
      focusWindow(judgeModal());
      return;
    }
    answer = { original_choice: null, suggested_doi_o: "", outcome: null, note: "" };
    renderRecord(judging.record);
    body.scrollTop = 0;
    focusWindow(judgeModal());
  }

  // A DOI links to doi.org, a web address to itself; anything else is plain text.
  function doiLink(value) {
    if (!value) return "";
    const text = String(value);
    let href = null;
    if (/^10\.\d+\/\S+$/.test(text)) {
      href = "https://doi.org/" + encodeURI(text);
    } else {
      try {
        const url = new URL(text);
        if (url.protocol === "https:" || url.protocol === "http:") href = url.href;
      } catch (_) { /* not a link */ }
    }
    return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(text)}</a>`
      : escapeHtml(text);
  }

  function outcomeLabel(value) {
    if (!value) return "";
    if (OUTCOME_LABELS[value]) return OUTCOME_LABELS[value];
    const text = String(value);
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function originalBlock(side, judgingMode, missing) {
    if (!side.doi_o) {
      return `<div class="adj-none">
        <p>${escapeHtml(missing)}</p>
        ${judgingMode ? '<button type="button" class="adj-link-btn" data-suggest="1">Suggest one</button>' : ""}
      </div>`;
    }
    return `<p class="adj-original-title">${escapeHtml(side.title_o || "Title not found")}</p>
      <p class="adj-small">${doiLink(side.doi_o)}</p>`;
  }

  function floraSide(f, judgingMode = true) {
    const outcome = !f.outcome || f.outcome === "pending"
      ? '<span class="adj-outcome is-none">Not determined (pending)</span>'
      : `<span class="adj-outcome">${escapeHtml(outcomeLabel(f.outcome))}</span>`;
    const quote = f.outcome_quote
      ? `<blockquote class="adj-quote">${escapeHtml(f.outcome_quote)}</blockquote>
         ${f.quote_source ? `<p class="adj-small">Quoted from the ${escapeHtml(f.quote_source)}</p>` : ""}`
      : "";
    const how = [f.link_method, f.link_confidence && `${f.link_confidence} confidence`].filter(Boolean).join(" · ");
    const why = f.doi_o && (how || f.link_evidence)
      ? `<details class="adj-why"><summary>Why FLoRA linked this original</summary>
          ${how ? `<p class="adj-small">${escapeHtml(how)}</p>` : ""}
          ${f.link_evidence ? `<p>${escapeHtml(f.link_evidence)}</p>` : ""}
        </details>`
      : "";
    return `<section class="adj-side adj-side-flora" aria-label="FLoRA's answer">
      <p class="adj-side-name">FLoRA</p>
      <p class="adj-label">Original</p>${originalBlock(f, judgingMode, "We couldn't find the original.")}
      <p class="adj-label">Outcome</p>${outcome}${quote}${why}
    </section>`;
  }

  function observatorySide(o, judgingMode = true) {
    const outcome = o.outcome
      ? `<span class="adj-outcome">${escapeHtml(OBSERVATORY_OUTCOMES[o.outcome] || outcomeLabel(o.outcome))}</span>`
      : '<span class="adj-outcome is-none">None given</span>';
    const facts = [
      o.replication_type && `${o.replication_type} replication`,
      o.discipline,
      o.confidence && `${o.confidence} confidence`,
    ].filter(Boolean);
    return `<section class="adj-side adj-side-mo" aria-label="The Observatory's answer">
      <p class="adj-side-name">Metascience Observatory</p>
      <p class="adj-label">Original</p>${originalBlock(o, judgingMode, "The Observatory couldn't find the original.")}
      <p class="adj-label">Outcome</p>${outcome}
      ${facts.length ? `<p class="adj-small">${escapeHtml(facts.join(" · "))}</p>` : ""}
    </section>`;
  }

  function originalChoiceLabel(choice, record) {
    const same = record.original_choices[0] === "both";
    if (choice === "flora") return "FLoRA's original";
    if (choice === "observatory") return "The Observatory's original";
    if (choice === "both") return same ? "Yes, this is the original" : "Both (it replicates both)";
    if (choice === "neither") {
      if (record.doi_required) return "I found it: give the DOI";
      return same ? "No: suggest the right one" : "Neither: suggest the right one";
    }
    return record.doi_required ? "I can't find it either" : "Can't tell";
  }

  function optionButton(group, value, label) {
    return `<button type="button" class="adj-option" data-group="${group}"
      data-value="${escapeHtml(value)}" aria-pressed="false">${escapeHtml(label)}</button>`;
  }

  function renderRecord(record) {
    const body = judgeBody();
    const same = record.original_choices[0] === "both";
    const lessCommon = record.outcome_choices.filter(v =>
      OUTCOME_LABELS[v] && !MAIN_OUTCOMES.includes(v) && v !== "cannot_tell");
    // FLoRA's own answer when it is a reproduction outcome (two axes).
    const reproduction = record.outcome_choices.filter(v => !OUTCOME_LABELS[v]);
    const abstract = record.abstract_r
      ? `<div class="adj-abstract">${escapeHtml(record.abstract_r)}</div>
         <p class="adj-small">Abstract from ${record.abstract_source === "europepmc" ? "Europe PMC" : "OpenAlex"}</p>`
      : '<p class="adj-small adj-no-abstract">No abstract was found; open the paper to judge it.</p>';
    body.innerHTML = `${noticeLine()}
      <div class="adj-kind"><span class="adj-chip">${escapeHtml(KIND_LABELS[record.kind] || record.kind)}</span>
        <span>${escapeHtml(KIND_HELP[record.kind] || "")}</span></div>
      <section class="adj-rep" aria-label="The replication">
        <p class="adj-label">The replication</p>
        <h3>${escapeHtml(record.title_r || "Untitled")}</h3>
        <p class="adj-small">${doiLink(record.doi_r)}${record.year_r ? ` · ${escapeHtml(record.year_r)}` : ""}</p>
        ${abstract}
      </section>
      <div class="adj-sides">${floraSide(record.flora)}${observatorySide(record.observatory)}</div>
      <fieldset class="adj-q">
        <legend>1. ${same ? "Is this the right original?" : "Which original is right?"}</legend>
        <div class="adj-options">${record.original_choices.map(c =>
          optionButton("original", c, originalChoiceLabel(c, record))).join("")}</div>
        <label class="adj-doi hidden" id="adj-doi-row">DOI of the right original
          <input type="text" id="adj-doi" placeholder="10.xxxx/…" autocomplete="off" spellcheck="false">
        </label>
      </fieldset>
      <fieldset class="adj-q">
        <legend>2. What is the replication's outcome?</legend>
        <div class="adj-options">${MAIN_OUTCOMES.map(v => optionButton("outcome", v, outcomeLabel(v))).join("")}</div>
        <div class="adj-options adj-options-quiet">
          ${lessCommon.map(v => optionButton("outcome", v, outcomeLabel(v))).join("")}
          ${reproduction.map(v => optionButton("outcome", v, `As FLoRA: ${outcomeLabel(v)}`)).join("")}
          ${optionButton("outcome", "cannot_tell", "Can't tell")}
        </div>
      </fieldset>
      <label class="adj-note"><span>Note <span class="adj-hint-inline">(optional, +1 point)</span></span>
        <textarea id="adj-note" rows="2" maxlength="2000" placeholder="Anything the admin should know"></textarea>
      </label>
      <p class="adj-error" id="adj-judge-error" role="alert"></p>
      <div class="adj-judge-actions">
        <button type="button" class="btn-outline" id="adj-skip-btn"
          title="Pass on this one; it will not be shown to you again">Skip</button>
        <span class="adj-worth" id="adj-worth"></span>
        <button type="button" class="btn-primary" id="adj-submit-btn" disabled>Submit</button>
      </div>`;

    body.querySelectorAll(".adj-option").forEach(button =>
      button.addEventListener("click", () => choose(button.dataset.group, button.dataset.value)));
    body.querySelectorAll("[data-suggest]").forEach(button =>
      button.addEventListener("click", () => {
        choose("original", "neither");
        if (!saving) document.getElementById("adj-doi")?.focus();
      }));
    document.getElementById("adj-doi").addEventListener("input", event => {
      answer.suggested_doi_o = event.target.value;
      updateJudgeActions();
    });
    document.getElementById("adj-note").addEventListener("input", event => {
      answer.note = event.target.value;
      updateJudgeActions();
    });
    document.getElementById("adj-skip-btn").addEventListener("click", skipRecord);
    document.getElementById("adj-submit-btn").addEventListener("click", submitRecord);
    updateJudgeActions();
  }

  function choose(group, value) {
    if (saving || !answer) return;          // the answer on its way is the one shown
    if (group === "original") answer.original_choice = value;
    else answer.outcome = value;
    judgeBody().querySelectorAll(`.adj-option[data-group="${group}"]`).forEach(button =>
      button.setAttribute("aria-pressed", String(button.dataset.value === value)));
    if (group === "original") {
      const neither = value === "neither";
      document.getElementById("adj-doi-row")?.classList.toggle("hidden", !neither);
      if (!neither) {
        answer.suggested_doi_o = "";
        const input = document.getElementById("adj-doi");
        if (input) input.value = "";
      }
    }
    updateJudgeActions();
  }

  function answered() {
    if (!judging || !answer || !answer.original_choice || !answer.outcome) return false;
    return !(answer.original_choice === "neither" && judging.record.doi_required
             && !answer.suggested_doi_o.trim());
  }

  // Mirrors judging.points_for on the server.
  function worth() {
    let points = Number(judging.points_base || 0);
    if (answer.original_choice !== "cannot_tell") points += 2;
    if (answer.outcome !== "cannot_tell") points += 2;
    if (answer.note.trim()) points += 1;
    return points;
  }

  function updateJudgeActions() {
    const done = answered();
    const submit = document.getElementById("adj-submit-btn");
    if (submit) {
      submit.disabled = saving || !done;
      submit.textContent = saving ? "Saving…" : "Submit";
    }
    const skip = document.getElementById("adj-skip-btn");
    if (skip) skip.disabled = saving;
    const doi = document.getElementById("adj-doi");
    if (doi) doi.readOnly = saving;
    const note = document.getElementById("adj-note");
    if (note) note.readOnly = saving;
    const worthEl = document.getElementById("adj-worth");
    if (worthEl) worthEl.textContent = done ? `Worth ${worth()} points` : "Answer both questions";
  }

  function judgeError(message) {
    const el = document.getElementById("adj-judge-error");
    if (el) el.textContent = message || "";
  }

  function setPoints(result) {
    if (typeof showToast === "function") showToast(result.points, "points");
    const points = document.getElementById("stat-points");
    if (points && result.total_points !== undefined) points.textContent = result.total_points;
  }

  async function submitRecord() {
    if (saving || !answered()) return;
    const seq = judgeSeq;
    const record = judging.record;
    const sent = {
      original_choice: answer.original_choice,
      suggested_doi_o: answer.original_choice === "neither" ? answer.suggested_doi_o.trim() || null : null,
      outcome: answer.outcome,
      note: answer.note.trim() || null,
    };
    saving = true;
    judgeError("");
    updateJudgeActions();
    try {
      const result = await api(`${JUDGE_PATH}/${encodeURIComponent(record.record_id)}/submit`, "POST", sent);
      setPoints(result);                   // saved, whether or not the window is still open
      if (seq !== judgeSeq || !isOpen(judgeModal())) { refreshSummary(); return; }   // closed meanwhile
      notice = `Saved: +${result.points} points.`;
      loadNext();
    } catch (error) {
      if (seq !== judgeSeq || !isOpen(judgeModal())) return;
      saving = false;
      if (error.status === 409) {
        notice = `${error.message}. Your answer to it was not saved; here is the next one.`;
        loadNext();
        return;
      }
      judgeError(error.message);
      updateJudgeActions();
    }
  }

  async function skipRecord() {
    if (saving || !judging || !judging.record) return;
    const seq = judgeSeq;
    const record = judging.record;
    saving = true;
    judgeError("");
    updateJudgeActions();
    try {
      await api(`${JUDGE_PATH}/${encodeURIComponent(record.record_id)}/skip`, "POST");
      if (seq !== judgeSeq || !isOpen(judgeModal())) { refreshSummary(); return; }
      loadNext();
    } catch (error) {
      if (seq !== judgeSeq || !isOpen(judgeModal())) return;
      saving = false;
      if (error.status === 409) { loadNext(); return; }
      judgeError(error.message);
      updateJudgeActions();
    }
  }

  // ---------------------------------------------------------------------------
  // Admins: review the judged records, approve the final answer, publish it to
  // FLoRA (Source Records) or withdraw it, and download the CSVs.
  // ---------------------------------------------------------------------------

  const RECORDS_PATH = "/disagreements/records";
  const EXPORT_URL = "/api/admin/disagreements/export/";
  const STATUS_TABS = [
    ["awaiting_approval", "Awaiting approval", "awaiting_approval"],
    ["approved", "Approved", "approved"],
    ["published", "In FLoRA", "published"],
    ["open", "Waiting for judgements", "open"],
    ["", "All", "records"],
  ];
  const STATUS_LABELS = {
    open: "Waiting for judgements",
    awaiting_approval: "Awaiting approval",
    approved: "Approved",
    published: "In FLoRA",
  };
  const TIER_MARKS = { 1: "Trusted", 2: "Senior" };
  const QUOTE_SOURCES = ["abstract", "discussion", "results", "title", "full text"];
  const SAME_KIND = "same original, different outcome";
  let recordsFilter = null;    // the admin's choice; until then, what needs doing
  let recordsSeq = 0;          // the latest list request; older answers are dropped
  let listedRecords = [];
  let review = null;           // the detail being shown
  let reviewSeq = 0;           // bumped by opening another record and by closing
  let reviewBusy = false;      // an approve or action is on its way
  let editing = false;         // changing an approved answer
  let confirming = null;       // the action waiting for its confirmation

  function recordsCard(counts) {
    // Awaiting approval first, or everything when nothing is waiting.
    if (recordsFilter === null) recordsFilter = Number((counts || {}).awaiting_approval) ? "awaiting_approval" : "";
    const tabs = STATUS_TABS.map(([value, label, key]) => `
      <button type="button" class="adj-filter" data-filter="${value}" aria-pressed="${value === recordsFilter}">
        ${escapeHtml(label)} <span data-filter-count="${key}">${count((counts || {})[key])}</span></button>`).join("");
    return `
      <div class="adj-card adj-records">
        <div class="adj-records-head">
          <h3>Review</h3>
          <div class="adj-exports">
            <a class="adj-download" href="${EXPORT_URL}final.csv" download
               title="Approved answers, in the column order of the FLoRA build">Final answers (CSV)</a>
            <a class="adj-download" href="${EXPORT_URL}judgements.csv" download
               title="Every record with both judges' answers, for the analysis">All judgements (CSV)</a>
          </div>
        </div>
        <div class="adj-filters">${tabs}</div>
        <div id="adj-records-list" aria-live="polite"><p class="admin-loading">Loading…</p></div>
      </div>`;
  }

  function bindRecordsCard() {
    document.querySelectorAll(".adj-filter").forEach(button => button.addEventListener("click", () => {
      recordsFilter = button.dataset.filter;
      document.querySelectorAll(".adj-filter").forEach(b =>
        b.setAttribute("aria-pressed", String(b === button)));
      loadRecords();
    }));
    loadRecords();
  }

  async function loadRecords() {
    if (!document.getElementById("adj-records-list")) return;
    const seq = ++recordsSeq;
    let records;
    try {
      const query = recordsFilter ? `?status=${encodeURIComponent(recordsFilter)}` : "";
      records = (await adminApi(RECORDS_PATH + query)).records || [];
    } catch (error) {
      const list = document.getElementById("adj-records-list");
      if (seq === recordsSeq && list) {
        list.innerHTML = `<p class="faq-error">Could not load the records (${escapeHtml(error.message)}).</p>`;
      }
      return;
    }
    const list = document.getElementById("adj-records-list");
    if (seq !== recordsSeq || !list) return;     // a newer filter was chosen meanwhile
    listedRecords = records;
    if (!records.length) {
      list.innerHTML = '<p class="adj-hint">Nothing here.</p>';
      return;
    }
    list.innerHTML = `<div class="adj-table-wrap"><table class="adj-table">
      <thead><tr><th>Replication</th><th>Kind</th><th>Judges</th><th>Answer</th><th></th></tr></thead>
      <tbody>${records.map(recordRow).join("")}</tbody></table></div>`;
    list.querySelectorAll("[data-review]").forEach(button =>
      button.addEventListener("click", () => openReview(button.dataset.review)));
  }

  // Counts and list again, in place: the tab keeps its scroll position and any
  // import preview still running.
  async function refreshTab() {
    try {
      const status = await adminApi(PATH);
      const counts = status && status.counts;
      if (counts) {
        document.querySelectorAll("[data-stat]").forEach(el => { el.textContent = count(counts[el.dataset.stat]); });
        document.querySelectorAll("[data-filter-count]").forEach(el => {
          el.textContent = count(counts[el.dataset.filterCount]);
        });
      }
    } catch (_) { /* the list below still refreshes */ }
    loadRecords();
  }

  // An answer withheld while the record waits for its second judgement.
  function hidden(j) { return j.original_choice === undefined; }

  function judgeSummary(j, same) {
    if (hidden(j)) return `<li><b>${escapeHtml(j.handle)}</b>: answered</li>`;
    return `<li><b>${escapeHtml(j.handle)}</b>: ${escapeHtml(choiceText(j.original_choice, j.suggested_doi_o, same))}
      · ${escapeHtml(outcomeLabel(j.outcome))}</li>`;
  }

  function agreementBadge(value) {
    if (!value) return "";
    return value === "agree"
      ? '<span class="adj-badge is-agree">Agree</span>'
      : '<span class="adj-badge is-disagree">Disagree</span>';
  }

  function answerCell(r) {
    if (r.status === "published") return `<span class="adj-badge is-flora">${escapeHtml(r.published_record_id || "In FLoRA")}</span>`;
    if (r.status === "approved") {
      return `${escapeHtml(outcomeLabel(r.final_outcome))}${r.final_doi_o ? `<br><small>${escapeHtml(r.final_doi_o)}</small>` : ""}
        ${r.withdrawn_at ? '<br><small class="adj-muted">withdrawn from FLoRA</small>' : ""}`;
    }
    return `<span class="adj-muted">${escapeHtml(STATUS_LABELS[r.status] || r.status)}</span>`;
  }

  function recordRow(r) {
    const same = r.kind === SAME_KIND;
    const judges = r.judgements.length
      ? `${agreementBadge(r.agreement)}<ul class="adj-judges">${r.judgements.map(j => judgeSummary(j, same)).join("")}</ul>`
      : '<span class="adj-muted">None yet</span>';
    return `<tr>
      <td><span class="adj-row-title">${escapeHtml(r.title_r || r.doi_r)}</span><small>${escapeHtml(r.doi_r)}</small></td>
      <td>${escapeHtml(KIND_LABELS[r.kind] || r.kind)}</td>
      <td>${judges}</td>
      <td>${answerCell(r)}</td>
      <td><button type="button" class="btn-outline adj-small-btn" data-review="${escapeHtml(r.record_id)}">Review</button></td>
    </tr>`;
  }

  function choiceText(choice, suggested, same) {
    switch (choice) {
      case "flora": return "FLoRA's original";
      case "observatory": return "the Observatory's original";
      case "both": return same ? "the shared original" : "both originals";
      case "neither": return suggested ? `neither; suggests ${suggested}` : "neither";
      case "cannot_tell": return "can't tell";
      default: return choice || "";
    }
  }

  // The review window --------------------------------------------------------

  function reviewModal() {
    let modal = document.getElementById("adj-review-modal");
    if (modal) return modal;
    modal = windowShell({
      id: "adj-review-modal", titleId: "adj-review-title", title: "Review a disagreement",
      bodyId: "adj-review-body", closeId: "adj-review-close",
      extraHeader: '<button type="button" class="ghost-btn adj-next-btn hidden" id="adj-review-next">Next →</button>',
    });
    modal.querySelector("#adj-review-close").addEventListener("click", closeReview);
    modal.querySelector("#adj-review-next").addEventListener("click", () => {
      const next = nextListed();
      if (next && !reviewBusy) openReview(next);
    });
    if (typeof onBackdropClick === "function") onBackdropClick(modal, closeReview);
    return modal;
  }

  function nextListed() {
    if (!review) return null;
    const ids = listedRecords.map(r => r.record_id);
    const at = ids.indexOf(review.record.record_id);
    return at >= 0 && at + 1 < ids.length ? ids[at + 1] : null;
  }

  async function openReview(recordId) {
    const modal = reviewModal();
    openWindow(modal, closeReview);
    const seq = ++reviewSeq;
    reviewBusy = false;
    editing = false;
    confirming = null;
    const body = document.getElementById("adj-review-body");
    body.innerHTML = '<p class="admin-loading">Loading…</p>';
    try {
      const detail = await adminApi(`${RECORDS_PATH}/${encodeURIComponent(recordId)}`);
      if (seq !== reviewSeq || !isOpen(modal)) return;     // another record, or closed
      showReview(detail);
    } catch (error) {
      if (seq !== reviewSeq) return;
      body.innerHTML = `<p class="faq-error">${escapeHtml(error.message)}</p>`;
    }
  }

  function closeReview() {
    reviewSeq += 1;              // answers still on their way are dropped
    reviewBusy = false;
    review = null;
    editing = false;
    confirming = null;
    closeWindow(document.getElementById("adj-review-modal"));
    refreshTab();
  }

  function showReview(detail) {
    review = detail;
    const record = detail.record;
    const body = document.getElementById("adj-review-body");
    document.getElementById("adj-review-next")?.classList.toggle("hidden", !nextListed());
    const abstract = record.abstract_r
      ? `<details class="adj-why"><summary>Abstract</summary><div class="adj-abstract">${escapeHtml(record.abstract_r)}</div></details>`
      : '<p class="adj-small">No abstract was found.</p>';
    body.innerHTML = `
      <div class="adj-kind"><span class="adj-chip">${escapeHtml(KIND_LABELS[record.kind] || record.kind)}</span>
        <span>${escapeHtml(KIND_HELP[record.kind] || "")}</span>
        <span class="adj-status">${escapeHtml(STATUS_LABELS[record.status] || record.status)}</span></div>
      <section class="adj-rep" aria-label="The replication">
        <p class="adj-label">The replication</p>
        <h3>${escapeHtml(record.title_r || "Untitled")}</h3>
        <p class="adj-small">${doiLink(record.doi_r)}${record.year_r ? ` · ${escapeHtml(record.year_r)}` : ""}</p>
        ${abstract}
      </section>
      <div class="adj-sides">${floraSide(record.flora, false)}${observatorySide(record.observatory, false)}</div>
      ${inFloraBox(detail.in_flora)}
      ${judgementsTable(detail)}
      <div id="adj-decision-area">${decisionArea(detail)}</div>`;
    bindDecisionArea();
    body.scrollTop = 0;
    lockScroll();
    focusWindow(document.getElementById("adj-review-modal"));
  }

  function inFloraBox(rows) {
    if (!rows || !rows.length) return "";
    return `<div class="adj-in-flora">
      <p><b>This replication is already in Source Records.</b> Publishing adds the answer as a
        separate row; Source Records' duplicate review then shows both, and you pick which FLoRA keeps.</p>
      <ul>${rows.map(r => `<li>${escapeHtml(r.display_id || "")} (${escapeHtml(r.source)}, ${escapeHtml(r.type)})
        · original ${escapeHtml(r.doi_o || "none")} · ${escapeHtml(outcomeLabel(r.outcome) || "no outcome")}
        ${r.ruled_duplicate ? " · ruled a duplicate" : ""}${r.deleted ? " · deleted" : ""}</li>`).join("")}</ul></div>`;
  }

  function judgementsTable(detail) {
    const rows = detail.judgements;
    if (!rows.length) {
      return `<p class="adj-label">Judgements</p><p class="adj-small">None submitted yet${detail.skipped ? ` (${detail.skipped} skipped)` : ""}.</p>`;
    }
    const same = detail.record.original_choices[0] === "both";
    const verdict = detail.agreement === "agree"
      ? "The two judges agree."
      : detail.agreement === "disagree" ? "The two judges disagree."
      : "One judgement so far; its answer is shown once the second is in, so the two stay independent.";
    return `<p class="adj-label">Judgements ${agreementBadge(detail.agreement)}</p>
      <div class="adj-table-wrap"><table class="adj-table adj-judgements">
        <thead><tr><th>Judge</th><th>Original</th><th>Outcome</th><th>Note</th><th>Points</th></tr></thead>
        <tbody>${rows.map(j => `<tr>
          <td><b>${escapeHtml(j.handle)}</b><small>${escapeHtml(TIER_MARKS[j.tier] || "")}${j.submitted_at ? ` · ${escapeHtml(new Date(j.submitted_at).toLocaleDateString())}` : ""}</small></td>
          ${hidden(j) ? '<td colspan="4" class="adj-muted">Hidden until both judgements are in</td>' : `
          <td>${escapeHtml(choiceText(j.original_choice, null, same))}${j.suggested_doi_o ? `<br>${doiLink(j.suggested_doi_o)}` : ""}</td>
          <td>${escapeHtml(outcomeLabel(j.outcome))}</td>
          <td>${escapeHtml(j.note || "")}</td>
          <td>${escapeHtml(j.points)}</td>`}</tr>`).join("")}</tbody></table></div>
      <p class="adj-small">${escapeHtml(verdict)}${detail.skipped ? ` ${detail.skipped} skipped.` : ""}</p>`;
  }

  const CONFIRM = {
    publish: "Add this answer to FLoRA? It becomes a Source Records row (source \"adjudicated\") and goes into the next FLoRA build.",
    withdraw: "Take this answer out of FLoRA? Its Source Records row is marked deleted, not removed, and the answer stays approved.",
    undo: "Undo the approval? The record goes back to \"awaiting approval\".",
  };
  const CONFIRM_LABELS = { publish: "Yes, publish", withdraw: "Yes, withdraw", undo: "Yes, undo" };

  // The buttons, or the confirmation of the one just pressed. Inside the window
  // rather than app.js's dialog, whose Cancel button the phone layout hides.
  function actionsBlock(buttons) {
    if (confirming) {
      return `<div class="adj-confirm" role="group" aria-label="Confirm">
        <p>${escapeHtml(CONFIRM[confirming])}</p>
        <div class="adj-actions">
          <button type="button" class="btn-primary" data-action="confirm">${escapeHtml(CONFIRM_LABELS[confirming])}</button>
          <button type="button" class="btn-outline" data-action="cancel-confirm">Cancel</button>
        </div></div>`;
    }
    return `<div class="adj-actions">${buttons}</div>`;
  }

  function pairWarning(detail) {
    const rows = detail.pair_conflicts || [];
    if (!rows.length) return "";
    const names = rows.map(r => `${r.display_id} (${r.source})`).join(", ");
    return `<p class="adj-problem">FLoRA already holds this replication–original pair as ${escapeHtml(names)}.
      The FLoRA build keeps one row per pair, and a row from the website (source "validated") imposes its
      outcome on the row it keeps. After publishing, rule one of them a duplicate in Source Records.</p>`;
  }

  function decisionArea(detail) {
    const record = detail.record;
    const final = detail.final;
    if (record.status === "open") {
      return `<div class="adj-decision-note">Waiting for two judgements (${detail.judgements.length} so far).
        You can approve an answer once both are in.</div>`;
    }
    if (record.status === "awaiting_approval" || editing) return decisionForm(detail);
    const facts = `
      <dl class="adj-final">
        <div><dt>Original</dt><dd>${final.doi_o ? `${escapeHtml(final.title_o || "")} ${doiLink(final.doi_o)}` : "No original"}</dd></div>
        <div><dt>Outcome</dt><dd>${escapeHtml(outcomeLabel(final.outcome))}</dd></div>
        ${final.outcome_quote ? `<div><dt>Quote</dt><dd>${escapeHtml(final.outcome_quote)}${final.quote_source ? ` <small>(${escapeHtml(final.quote_source)})</small>` : ""}</dd></div>` : ""}
        <div><dt>Based on</dt><dd>${escapeHtml({ flora: "FLoRA's answer", observatory: "the Observatory's answer", admin: "the admin's own answer" }[final.basis] || final.basis)}</dd></div>
        ${final.admin_note ? `<div><dt>Note</dt><dd>${escapeHtml(final.admin_note)}</dd></div>` : ""}
        <div><dt>Approved</dt><dd>by ${escapeHtml(final.approved_by)}, ${escapeHtml(new Date(final.approved_at).toLocaleString())}</dd></div>
      </dl>`;
    if (record.status === "published") {
      return `<div class="adj-decision-done is-published">
        <p><b>In FLoRA as ${escapeHtml(final.published_record_id)}</b> (Source Records, source "adjudicated"),
          published by ${escapeHtml(final.published_by || "")}. It goes into the next FLoRA build.</p>
        ${facts}
        ${pairWarning(detail)}
        <p class="adj-error" id="adj-review-error" role="alert"></p>
        ${actionsBlock('<button type="button" class="btn-outline" data-action="withdraw">Withdraw from FLoRA</button>')}
      </div>`;
    }
    const problem = detail.publish_problem;
    const everPublished = !!final.published_record_id;
    return `<div class="adj-decision-done">
      ${final.withdrawn_at ? `<p class="adj-small">Withdrawn from FLoRA by ${escapeHtml(final.withdrawn_by || "")};
        publishing again brings back ${escapeHtml(final.published_record_id || "its row")}.</p>` : ""}
      ${facts}
      ${problem ? `<p class="adj-problem">${escapeHtml(problem)}</p>` : pairWarning(detail)}
      <p class="adj-error" id="adj-review-error" role="alert"></p>
      ${actionsBlock(`
        <button type="button" class="btn-primary" data-action="publish" ${problem ? "disabled" : ""}>Publish to FLoRA</button>
        <button type="button" class="btn-outline" data-action="edit">Change the answer</button>
        ${everPublished ? "" : '<button type="button" class="btn-outline" data-action="undo">Undo approval</button>'}`)}
    </div>`;
  }

  // The DOI options the admin chooses from: each side's, each judge's suggestion.
  // A side's value that is not a DOI (a web link) cannot go into FLoRA, so it is
  // not offered; "Another DOI" takes the real one.
  function doiOptions(detail) {
    const record = detail.record;
    const options = [];
    const seen = new Set();
    const add = (doi, label, title) => {
      const key = String(doi || "").toLowerCase();
      if (!doi || !key.startsWith("10.") || seen.has(key)) return;
      seen.add(key);
      options.push({ doi, label, title: title || "" });
    };
    const same = record.original_choices[0] === "both";
    add(record.flora.doi_o, same ? "The original both name" : "FLoRA's original", record.flora.title_o);
    add(record.observatory.doi_o, "The Observatory's original", record.observatory.title_o);
    detail.judgements.forEach(j => add(j.suggested_doi_o, `Suggested by ${j.handle}`, ""));
    return options;
  }

  // What the judges agreed on, as a starting point; nothing where they did not.
  function prefill(detail) {
    if (detail.final) {
      return { doi_o: detail.final.doi_o, title_o: detail.final.title_o, outcome: detail.final.outcome,
        outcome_quote: detail.final.outcome_quote, quote_source: detail.final.quote_source,
        admin_note: detail.final.admin_note };
    }
    const record = detail.record;
    const out = { doi_o: undefined, title_o: "", outcome: "", outcome_quote: "", quote_source: "", admin_note: "" };
    if (detail.agreement === "agree") {
      const j = detail.judgements[0];
      const pick = { flora: record.flora, observatory: record.observatory }[j.original_choice]
        || (j.original_choice === "both" && record.original_choices[0] === "both" ? record.flora : null);
      if (pick && String(pick.doi_o || "").startsWith("10.")) { out.doi_o = pick.doi_o; out.title_o = pick.title_o || ""; }
      if (j.original_choice === "neither" && j.suggested_doi_o) out.doi_o = j.suggested_doi_o;
      out.outcome = j.outcome;
    }
    if (out.outcome && out.outcome === record.flora.outcome && record.flora.outcome_quote) {
      out.outcome_quote = record.flora.outcome_quote;
      out.quote_source = record.flora.quote_source || "";
    }
    return out;
  }

  function decisionForm(detail) {
    const values = prefill(detail);
    const options = doiOptions(detail);
    const known = options.some(o => o.doi.toLowerCase() === String(values.doi_o || "").toLowerCase());
    const chosen = values.doi_o === undefined ? "" : values.doi_o === null ? "none" : known ? values.doi_o.toLowerCase() : "other";
    const radio = (value, label, extra = "") => `
      <label class="adj-radio"><input type="radio" name="adj-doi-choice" value="${escapeHtml(value)}"
        ${chosen === value ? "checked" : ""}> <span>${label}</span>${extra}</label>`;
    const outcomes = detail.outcomes.map(o =>
      `<option value="${escapeHtml(o)}" ${values.outcome === o ? "selected" : ""}>${escapeHtml(outcomeLabel(o))}</option>`).join("");
    return `<form class="adj-decision" id="adj-decision" novalidate>
      <h4>${editing ? "Change the approved answer" : "Approve the final answer"}</h4>
      <fieldset class="adj-q"><legend>Original</legend>
        ${options.map(o => radio(o.doi.toLowerCase(),
          `${escapeHtml(o.label)}: <span class="adj-mono">${escapeHtml(o.doi)}</span>${o.title ? ` <small>${escapeHtml(o.title)}</small>` : ""}`)).join("")}
        ${radio("other", "Another DOI", ` <input type="text" id="adj-other-doi" placeholder="10.xxxx/…"
          value="${chosen === "other" ? escapeHtml(values.doi_o) : ""}" autocomplete="off" spellcheck="false">`)}
        ${radio("none", "No original (not publishable)")}
      </fieldset>
      <label class="adj-field">Title of the original
        <input type="text" id="adj-title-o" value="${escapeHtml(values.title_o || "")}"></label>
      <label class="adj-field">Outcome
        <select id="adj-outcome"><option value="">Choose…</option>${outcomes}</select></label>
      <label class="adj-field"><span>Outcome quote <span class="adj-hint-inline">(optional)</span></span>
        <textarea id="adj-quote" rows="2">${escapeHtml(values.outcome_quote || "")}</textarea></label>
      <label class="adj-field"><span>Quote source <span class="adj-hint-inline">(optional)</span></span>
        <input type="text" id="adj-quote-source" list="adj-quote-sources" value="${escapeHtml(values.quote_source || "")}">
        <datalist id="adj-quote-sources">${QUOTE_SOURCES.map(s => `<option value="${s}">`).join("")}</datalist></label>
      <label class="adj-field"><span>Note <span class="adj-hint-inline">(optional, kept with the answer)</span></span>
        <textarea id="adj-admin-note" rows="2">${escapeHtml(values.admin_note || "")}</textarea></label>
      <p class="adj-error" id="adj-review-error" role="alert"></p>
      <div class="adj-actions">
        <button type="submit" class="btn-primary" id="adj-approve-btn">${editing ? "Save the answer" : "Approve"}</button>
        ${editing ? '<button type="button" class="btn-outline" data-action="cancel-edit">Cancel</button>' : ""}
      </div>
    </form>`;
  }

  function rerenderDecision() {
    const area = document.getElementById("adj-decision-area");
    if (!area || !review) return;
    area.innerHTML = decisionArea(review);
    bindDecisionArea();
  }

  function bindDecisionArea() {
    const area = document.getElementById("adj-decision-area");
    if (!area) return;
    area.querySelectorAll("[data-action]").forEach(button =>
      button.addEventListener("click", () => reviewAction(button.dataset.action)));
    const form = document.getElementById("adj-decision");
    if (!form) return;
    // Choosing a side's original fills in its title.
    const titles = new Map(doiOptions(review).map(o => [o.doi.toLowerCase(), o.title]));
    form.querySelectorAll('input[name="adj-doi-choice"]').forEach(input =>
      input.addEventListener("change", () => {
        reviewError("");
        if (titles.has(input.value)) document.getElementById("adj-title-o").value = titles.get(input.value) || "";
        if (input.value === "none" || input.value === "other") document.getElementById("adj-title-o").value = "";
        if (input.value === "other") document.getElementById("adj-other-doi")?.focus();
      }));
    document.getElementById("adj-other-doi")?.addEventListener("focus", () => {
      const other = form.querySelector('input[name="adj-doi-choice"][value="other"]');
      if (other && !other.checked) { other.checked = true; document.getElementById("adj-title-o").value = ""; }
    });
    form.addEventListener("submit", event => { event.preventDefault(); approve(); });
  }

  function reviewError(message) {
    const el = document.getElementById("adj-review-error");
    if (el) el.textContent = message || "";
  }

  // While a request is on its way, nothing else in the window can start one.
  function setReviewBusy(busy) {
    reviewBusy = busy;
    document.querySelectorAll("#adj-decision-area button, #adj-decision-area input, #adj-decision-area select, #adj-decision-area textarea")
      .forEach(el => { el.disabled = busy; });
    const next = document.getElementById("adj-review-next");
    if (next) next.disabled = busy;
  }

  // A request that finished after its window was closed: the list may be stale.
  function finishedLate() {
    if (!isOpen(document.getElementById("adj-review-modal"))) refreshTab();
  }

  async function approve() {
    if (reviewBusy || !review) return;
    const form = document.getElementById("adj-decision");
    const choice = form.querySelector('input[name="adj-doi-choice"]:checked');
    if (!choice) { reviewError("Choose the original."); return; }
    const outcome = document.getElementById("adj-outcome").value;
    if (!outcome) { reviewError("Choose the outcome."); return; }
    const doi = choice.value === "none" ? null
      : choice.value === "other" ? document.getElementById("adj-other-doi").value.trim()
      : doiOptions(review).find(o => o.doi.toLowerCase() === choice.value).doi;
    if (choice.value === "other" && !doi) { reviewError("Type the DOI of the original."); return; }
    const body = {
      doi_o: doi,
      title_o: document.getElementById("adj-title-o").value,
      outcome,
      outcome_quote: document.getElementById("adj-quote").value,
      quote_source: document.getElementById("adj-quote-source").value,
      admin_note: document.getElementById("adj-admin-note").value,
    };
    const seq = reviewSeq;
    reviewError("");
    setReviewBusy(true);
    try {
      const detail = await adminApi(`${RECORDS_PATH}/${encodeURIComponent(review.record.record_id)}/approve`, "POST", body);
      if (seq !== reviewSeq) { finishedLate(); return; }
      reviewBusy = false;
      editing = false;
      showReview(detail);
      if (typeof showToast === "function") showToast("Answer approved.");
    } catch (error) {
      if (seq !== reviewSeq) return;
      setReviewBusy(false);
      reviewError(error.message);
    }
  }

  async function reviewAction(action) {
    if (reviewBusy || !review) return;
    if (action === "edit" || action === "cancel-edit") {
      editing = action === "edit";
      confirming = null;
      rerenderDecision();
      return;
    }
    if (action === "cancel-confirm") {
      confirming = null;
      rerenderDecision();
      return;
    }
    if (action !== "confirm") {
      confirming = action;
      rerenderDecision();
      document.querySelector('#adj-decision-area [data-action="confirm"]')?.focus();
      return;
    }
    const chosen = confirming;
    confirming = null;
    const seq = reviewSeq;
    reviewError("");
    setReviewBusy(true);
    try {
      const detail = await adminApi(`${RECORDS_PATH}/${encodeURIComponent(review.record.record_id)}/${chosen}`, "POST");
      if (seq !== reviewSeq) { finishedLate(); return; }
      reviewBusy = false;
      editing = false;
      showReview(detail);
      const done = { publish: `Published as ${detail.final?.published_record_id || ""}.`,
        withdraw: "Withdrawn from FLoRA.", undo: "Approval undone." }[chosen];
      if (typeof showToast === "function") showToast(done);
    } catch (error) {
      if (seq !== reviewSeq) return;
      reviewBusy = false;
      rerenderDecision();
      reviewError(error.message);
    }
  }

  window.Adjudication = { initAdmin, openAdminTab, initValidator };

  // app.js restores a session over the network, and that answer can arrive
  // before this file has finished downloading; its call to initAdmin() or
  // initValidator() then found nothing to call. If the admin panel or the game
  // is already showing, do it now. Calling either twice is harmless.
  const adminScreen = document.getElementById("admin-screen");
  if (adminScreen && !adminScreen.classList.contains("hidden")) initAdmin();
  const gameScreen = document.getElementById("game-screen");
  if (gameScreen && !gameScreen.classList.contains("hidden")) initValidator();
})();
