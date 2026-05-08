const state = {
  coder: null,
  currentPair: null,
  judgement: blankJudgement(),
  mode: "normal",          // "normal" | "hard"
  onboardingPairs: [],
  onboardingIdx: 0,
  onboardingResults: [],
};

function blankJudgement() {
  return {
    type: null,
    original: null,
    outcome: null,
    comment: "",
    edited_abstract: null,
    edited_outcome_quote: null,
    hard_entry: null,
  };
}

const $ = (sel) => document.querySelector(sel);
const STORAGE = {
  CODER: "flora.coder",
  CODERS: "flora.coders",
  JUDGEMENTS: "flora.judgements",
};

let API_MODE = "online"; // "online" | "static"
let STATIC_DATA = null;  // {normal: [...], hard: [...], onboarding: [...]}

async function detectMode() {
  try {
    const r = await fetch("./api/leaderboard", { method: "GET" });
    if (r.ok) {
      API_MODE = "online";
      return;
    }
  } catch {}
  API_MODE = "static";
  $("#banner").classList.remove("hidden");
  const [normal, hard, onb] = await Promise.all([
    fetch("./pairs.json").then((r) => r.json()),
    fetch("./hard_pairs.json").then((r) => r.json()).catch(() => []),
    fetch("./onboarding.json").then((r) => r.json()).catch(() => ({ pairs: [] })),
  ]);
  STATIC_DATA = { normal, hard, onboarding: onb.pairs || onb };
}

async function api(path, method = "GET", body = null) {
  if (API_MODE === "static") return staticApi(path, method, body);
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch("/api" + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

/* ---------- Static (localStorage) backend ---------- */
function readJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch { return fallback; }
}
function writeJSON(key, val) { localStorage.setItem(key, JSON.stringify(val)); }

function pointsForStatic(req) {
  if (req.type_judgement === "skip") return 0;
  if (req.hard_mode) {
    let p = 25;
    if (req.comment && req.comment.trim()) p += 3;
    return p;
  }
  if (req.type_judgement === "not_validation") return 10;
  let p = 10;
  if (req.original_judgement) p += 5;
  if (req.outcome_judgement) p += 5;
  if (req.comment && req.comment.trim()) p += 3;
  if ((req.edited_abstract && req.edited_abstract.trim()) ||
      (req.edited_outcome_quote && req.edited_outcome_quote.trim())) p += 2;
  return p;
}

function staticRank(points) {
  const totals = {};
  readJSON(STORAGE.JUDGEMENTS, []).forEach((j) => {
    if (j.type_judgement === "skip") return;
    totals[j.coder_id] = (totals[j.coder_id] || 0) + j.points;
  });
  return 1 + Object.values(totals).filter((p) => p > points).length;
}

async function staticApi(path, method, body) {
  const url = new URL("http://x" + path);
  const route = url.pathname;
  const params = url.searchParams;
  const coders = readJSON(STORAGE.CODERS, {});

  if (route === "/login") {
    const code = (body.code || "").trim();
    const email = (body.email || "").trim().toLowerCase();
    const handle = (body.handle || "").trim();
    const key = email || code;
    if (!key || !handle) throw new Error("Handle and email or code required");
    if (coders[key] && coders[key].handle !== handle) {
      const method = email ? "email" : "code";
      throw new Error(`This ${method} is already linked to handle '${coders[key].handle}'.`);
    }
    if (!coders[key]) {
      const id = Date.now() + Math.floor(Math.random() * 1000);
      coders[key] = { coder_id: id, code: code || null, email: email || null, handle, onboarded: false };
      writeJSON(STORAGE.CODERS, coders);
    }
    return coders[key];
  }

  if (route === "/onboarding") {
    return { pairs: STATIC_DATA.onboarding };
  }

  if (route === "/onboarding/complete") {
    const cid = body.coder_id;
    for (const k of Object.keys(coders)) {
      if (coders[k].coder_id === cid) coders[k].onboarded = true;
    }
    writeJSON(STORAGE.CODERS, coders);
    return { onboarded: true };
  }

  if (route === "/next-pair") {
    const cid = +params.get("coder_id");
    const mode = params.get("mode") || "normal";
    const pool = mode === "hard" ? STATIC_DATA.hard : STATIC_DATA.normal;
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    const judged = new Set(judgements.filter((j) => j.coder_id === cid).map((j) => j.pair_id));
    const remaining = pool.filter((p) => !judged.has(p.pair_id));
    const total = pool.length;
    const done = pool.length - remaining.length;
    if (!remaining.length) return { pair: null, done, total };
    const pair = remaining[Math.floor(Math.random() * remaining.length)];
    return { pair, judge_count: 0, done, total };
  }

  if (route === "/judge") {
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    if (judgements.find((j) => j.coder_id === body.coder_id && j.pair_id === body.pair_id)) {
      throw new Error("Already judged this pair");
    }
    const points = pointsForStatic(body);
    judgements.push({ ...body, points, created_at: new Date().toISOString() });
    writeJSON(STORAGE.JUDGEMENTS, judgements);
    const totalPts = judgements
      .filter((j) => j.coder_id === body.coder_id && j.type_judgement !== "skip")
      .reduce((a, b) => a + b.points, 0);
    return { points_earned: points, total_points: totalPts, rank: staticRank(totalPts) };
  }

  if (route === "/leaderboard") {
    const idToHandle = {};
    Object.values(coders).forEach((c) => (idToHandle[c.coder_id] = c.handle));
    const agg = {};
    readJSON(STORAGE.JUDGEMENTS, []).forEach((j) => {
      if (j.type_judgement === "skip") return;
      if (!agg[j.coder_id]) agg[j.coder_id] = { points: 0, pairs: 0 };
      agg[j.coder_id].points += j.points;
      agg[j.coder_id].pairs += 1;
    });
    return Object.entries(agg)
      .map(([id, v]) => ({ name: idToHandle[id] || "anon", points: v.points, pairs: v.pairs }))
      .sort((a, b) => b.points - a.points || b.pairs - a.pairs);
  }

  if (route === "/stats") {
    const cid = +params.get("coder_id");
    const judgements = readJSON(STORAGE.JUDGEMENTS, []).filter((j) => j.coder_id === cid);
    const scoring = judgements.filter((j) => j.type_judgement !== "skip");
    const points = scoring.reduce((a, b) => a + b.points, 0);
    return {
      done: scoring.length,
      points,
      skipped: judgements.length - scoring.length,
      total: STATIC_DATA.normal.length + STATIC_DATA.hard.length,
      normal_total: STATIC_DATA.normal.length,
      hard_total: STATIC_DATA.hard.length,
      rank: staticRank(points),
    };
  }

  throw new Error("Unknown route: " + route);
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function showToast(num, label) {
  const toast = $("#toast");
  toast.innerHTML = `<span class="num">+${num}</span>${label}`;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1800);
}

/* ---------- Auth ---------- */
let loginMode = "email";
document.addEventListener("DOMContentLoaded", () => {
  $("#toggle-label-email").classList.add("toggle-active");
});

function getCode() {
  return ["#cp1", "#cp2", "#cp3", "#cp4"].map((id) => $(id).value.trim()).join("");
}

document.querySelectorAll(".code-part").forEach((input, i, arr) => {
  input.addEventListener("input", () => {
    const code = getCode();
    $("#code-preview").textContent = code || "——";
    if (input.value.length === 2 && i + 1 < arr.length) arr[i + 1].focus();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      if (i + 1 < arr.length) arr[i + 1].focus();
      else doLogin();
    }
  });
});

$("#email-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
$("#login-btn").onclick = doLogin;
$("#handle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    if (loginMode === "email") $("#email-input").focus();
    else $("#cp1").focus();
  }
});

$("#login-mode-toggle").addEventListener("change", (e) => {
  loginMode = e.target.checked ? "code" : "email";
  const isEmail = loginMode === "email";
  $("#email-section").classList.toggle("hidden", !isEmail);
  $("#code-section").classList.toggle("hidden", isEmail);
  $("#code-preview-wrap").classList.toggle("hidden", isEmail);
  $("#auth-sep-label").textContent = isEmail
    ? "// sign in with email"
    : "// personal code · stays constant";
  $("#toggle-label-email").classList.toggle("toggle-active", isEmail);
  $("#toggle-label-code").classList.toggle("toggle-active", !isEmail);
  setTimeout(() => (isEmail ? $("#email-input") : $("#cp1")).focus(), 50);
});

async function doLogin() {
  const handle = $("#handle-input").value.trim();
  if (!handle) { alert("Please enter a handle."); return; }
  let body;
  if (loginMode === "email") {
    const email = $("#email-input").value.trim();
    if (!email || !email.includes("@")) {
      alert("Please enter a valid email address.");
      return;
    }
    body = { handle, email };
  } else {
    const code = getCode();
    if (code.length < 6) {
      alert("Please fill in all four parts of your code.");
      return;
    }
    body = { handle, code };
  }
  try {
    const resp = await api("/login", "POST", body);
    state.coder = resp;
    localStorage.setItem(STORAGE.CODER, JSON.stringify(resp));
    routeAfterLogin();
  } catch (e) {
    alert(e.message);
  }
}

async function startup() {
  await detectMode();
  const stored = localStorage.getItem(STORAGE.CODER);
  if (stored) {
    try {
      state.coder = JSON.parse(stored);
      routeAfterLogin();
    } catch {}
  }
}
startup();

function routeAfterLogin() {
  $("#login-screen").classList.add("hidden");
  if (!state.coder.onboarded) {
    enterOnboarding();
  } else {
    enterGame();
  }
}

const logout = () => {
  localStorage.removeItem(STORAGE.CODER);
  location.reload();
};
$("#logout-btn").onclick = logout;
$("#onb-logout-btn").onclick = logout;

/* ---------- Onboarding ---------- */
async function enterOnboarding() {
  $("#onboarding-screen").classList.remove("hidden");
  const resp = await api("/onboarding");
  state.onboardingPairs = resp.pairs;
  state.onboardingIdx = 0;
  state.onboardingResults = [];
  updateOnbProgress();
}

$("#onb-start-btn").onclick = () => {
  $("#onb-intro").classList.add("hidden");
  showOnboardingPair();
};

$("#onb-finish-btn").onclick = async () => {
  await api("/onboarding/complete", "POST", { coder_id: state.coder.coder_id });
  state.coder.onboarded = true;
  localStorage.setItem(STORAGE.CODER, JSON.stringify(state.coder));
  $("#onboarding-screen").classList.add("hidden");
  enterGame();
};

function updateOnbProgress() {
  const total = state.onboardingPairs.length || 5;
  $("#onb-counter").textContent = `${Math.min(state.onboardingIdx + 1, total)} / ${total}`;
  $("#onb-progress-fill").style.width = (state.onboardingIdx / total) * 100 + "%";
}

function showOnboardingPair() {
  const pair = state.onboardingPairs[state.onboardingIdx];
  state.currentPair = pair;
  state.judgement = blankJudgement();
  $("#onb-feedback").classList.add("hidden");
  $("#onb-card").classList.remove("hidden");
  renderPairInto($("#onb-card"), pair, { onboarding: true, judgeCount: 0 });
  updateOnbProgress();
}

function evaluateOnboarding(pair, j) {
  const exp = pair.expected;
  const errors = [];

  // Type check
  if (exp.type === "validation") {
    if (j.type !== "replication" && j.type !== "reproduction") {
      errors.push({ key: "type_wrong", text: pair.feedback.type_wrong });
    }
  } else if (exp.type === "not_validation") {
    if (j.type !== "not_validation") {
      errors.push({ key: "type_wrong", text: pair.feedback.type_wrong });
    }
  }

  // Only check original/outcome if the expected case requires them
  if (exp.original && j.type !== "not_validation") {
    if (j.original !== exp.original) {
      errors.push({ key: "original_wrong", text: pair.feedback.original_wrong });
    }
  }
  if (exp.outcome && j.type !== "not_validation") {
    if (j.outcome !== exp.outcome) {
      errors.push({ key: "outcome_wrong", text: pair.feedback.outcome_wrong });
    }
  }

  return errors;
}

function showOnboardingFeedback(pair, errors) {
  const correct = errors.length === 0;
  const fb = $("#onb-feedback");
  fb.classList.remove("hidden");
  fb.innerHTML = `
    <div class="feedback-card ${correct ? "correct" : "incorrect"}">
      <div class="feedback-header">
        ${correct ? "✓ Spot on" : "✗ Not quite"}
      </div>
      <p class="feedback-intro">${escapeHtml(pair.feedback.intro)}</p>
      ${
        correct
          ? ""
          : `<ul class="feedback-list">${errors
              .map((e) => `<li>${escapeHtml(e.text)}</li>`)
              .join("")}</ul>`
      }
      <div class="feedback-actions">
        <button class="btn-primary" id="onb-next-btn">${
          state.onboardingIdx === state.onboardingPairs.length - 1
            ? "Finish onboarding →"
            : "Next example →"
        }</button>
      </div>
    </div>
  `;
  $("#onb-next-btn").onclick = () => {
    state.onboardingResults.push({ correct, idx: state.onboardingIdx });
    state.onboardingIdx += 1;
    if (state.onboardingIdx >= state.onboardingPairs.length) {
      $("#onb-card").classList.add("hidden");
      $("#onb-feedback").classList.add("hidden");
      $("#onb-done").classList.remove("hidden");
      $("#onb-progress-fill").style.width = "100%";
      $("#onb-counter").textContent = `${state.onboardingPairs.length} / ${state.onboardingPairs.length}`;
    } else {
      showOnboardingPair();
    }
  };
  fb.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- Game ---------- */
async function enterGame() {
  $("#game-screen").classList.remove("hidden");
  $("#stat-name").textContent = state.coder.handle;
  await refreshAll();
}

$("#mode-toggle").onclick = async () => {
  state.mode = state.mode === "normal" ? "hard" : "normal";
  $("#mode-toggle").textContent = state.mode === "hard" ? "Hard mode" : "Normal mode";
  $("#mode-toggle").classList.toggle("active", state.mode === "hard");
  await loadNextPair();
  await refreshStats();
};

$("#sidebar-toggle").onclick = () => {
  document.body.classList.toggle("sidebar-collapsed");
  const collapsed = document.body.classList.contains("sidebar-collapsed");
  $("#sidebar-toggle").textContent = collapsed ? "Leaderboard ◂" : "Leaderboard ▸";
};
$("#sidebar-close").onclick = () => {
  document.body.classList.add("sidebar-collapsed");
  $("#sidebar-toggle").textContent = "Leaderboard ◂";
};

async function refreshAll() {
  await Promise.all([refreshStats(), refreshLeaderboard(), loadNextPair()]);
}

async function refreshStats() {
  const s = await api(`/stats?coder_id=${state.coder.coder_id}`);
  $("#stat-points").textContent = s.points;
  $("#stat-rank").textContent = "#" + s.rank;
  $("#stat-progress").textContent = `${s.done} / ${s.total}`;
  $("#progress-fill").style.width = (s.total ? (s.done / s.total) * 100 : 0) + "%";
}

async function refreshLeaderboard() {
  const lb = await api("/leaderboard");
  const list = $("#leaderboard-list");
  list.innerHTML = "";
  if (!lb.length || lb.every((e) => e.points === 0)) {
    list.innerHTML = '<li style="opacity:0.5;font-style:italic">No scores yet</li>';
    return;
  }
  lb.filter((e) => e.points > 0).slice(0, 10).forEach((entry) => {
    const li = document.createElement("li");
    if (entry.name === state.coder.handle) li.classList.add("me");
    li.innerHTML = `<span class="lb-name">${escapeHtml(entry.name)}</span>
      <span class="lb-pts">${entry.points} · ${entry.pairs}p</span>`;
    list.appendChild(li);
  });
}

async function loadNextPair() {
  const resp = await api(`/next-pair?coder_id=${state.coder.coder_id}&mode=${state.mode}`);
  if (!resp.pair) {
    $("#pair-card").classList.add("hidden");
    $("#done-screen").classList.remove("hidden");
    return;
  }
  $("#done-screen").classList.add("hidden");
  $("#pair-card").classList.remove("hidden");
  state.currentPair = resp.pair;
  state.judgement = blankJudgement();
  if (state.mode === "hard") {
    renderHardPair($("#pair-card"), resp.pair);
  } else {
    renderPairInto($("#pair-card"), resp.pair, { onboarding: false, judgeCount: resp.judge_count });
  }
}

/* ---------- Pair rendering (normal + onboarding) ---------- */
function renderPairInto(container, p, { onboarding, judgeCount }) {
  const outcomeLabel = (p.outcome || "uninformative").toLowerCase();
  const doiUrl = p.doi_r ? `https://doi.org/${p.doi_r}` : null;
  const oaUrl = p.oa_url_r || (p.url_r && p.url_r !== doiUrl ? p.url_r : null);
  const scholarQuery = encodeURIComponent(`${p.title_r || ""} ${p.authors_r || ""}`);
  const scholarUrl = `https://scholar.google.com/scholar?q=${scholarQuery}`;
  const oUrl = p.doi_o ? `https://doi.org/${p.doi_o}` : null;
  const oOaUrl = p.oa_url_o || null;
  const hasQuote = p.outcome_phrase && p.outcome_phrase.trim();
  const lockIcon = (open) => `<span class="oa-lock ${open ? "open" : "gated"}" aria-label="${open ? "open access" : "gated"}">${open ? "🔓" : "🔒"}</span>`;

  container.innerHTML = `
    <div class="pair-header">
      <div class="pair-meta">
        <span class="meta-item">id <b>${escapeHtml(String(p.pair_id).slice(0, 8))}</b></span>
        ${onboarding
          ? `<span class="meta-item onboarding-tag">onboarding</span>`
          : `<span class="meta-item">${judgeCount} other coder${judgeCount === 1 ? "" : "s"}</span>`}
        ${onboarding ? "" : `<button class="skip-btn" id="skip-btn">Skip — broken / unclear</button>`}
      </div>

      <h2>${escapeHtml(p.title_r || "(untitled)")}</h2>
      <div class="authors">${escapeHtml(p.authors_r || "?")} · ${escapeHtml(String(p.year_r || "?"))}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}</div>

      <div class="abstract-block">
        <div class="abstract" id="abstract-text">${escapeHtml(p.abstract_r || "(no abstract available)")}</div>
        <textarea class="abstract-edit hidden" id="abstract-edit" rows="6"></textarea>
        <div class="abstract-tools">
          <button class="link-btn" id="edit-abstract-btn">edit abstract</button>
          <span class="abstract-tools-spacer"></span>
          ${oaUrl ? `<a href="${escapeHtml(oaUrl)}" target="_blank" rel="noopener" title="Open access PDF">${lockIcon(true)} OA full text</a>` : ""}
          ${doiUrl ? `<a href="${escapeHtml(doiUrl)}" target="_blank" rel="noopener" title="${oaUrl ? "DOI page" : "Publisher page (likely paywalled)"}">${oaUrl ? "" : lockIcon(false) + " "}DOI</a>` : ""}
          <a href="${escapeHtml(scholarUrl)}" target="_blank" rel="noopener" title="Search for an alternate copy">Scholar</a>
        </div>
      </div>
    </div>

    <div class="pair-body">
      <div class="gate" id="gate-1">
        <h3><span class="gate-num">i.</span>Type check</h3>
        <p class="question">Is this paper actually validating a previous finding?</p>
        <div class="choices">
          <button class="choice success" data-type="replication">Replication<small>different data</small></button>
          <button class="choice success" data-type="reproduction">Reproduction<small>same data</small></button>
          <button class="choice danger" data-type="not_validation">Neither<small>not a validation</small></button>
        </div>
      </div>

      <div class="gate hidden" id="gate-2">
        <h3><span class="gate-num">ii.</span>Original check</h3>
        <p class="question">Does this match the paper actually being validated?</p>
        <div class="original-info">
          <div class="title">${escapeHtml(p.title_o || "(no title)")}</div>
          <div class="meta">
            ${escapeHtml(p.authors_o || "?")} · ${escapeHtml(String(p.year_o || "?"))}
            ${oOaUrl ? ` · <a href="${escapeHtml(oOaUrl)}" target="_blank" rel="noopener" title="Open access PDF">${lockIcon(true)} OA</a>` : ""}
            ${oUrl ? ` · <a href="${escapeHtml(oUrl)}" target="_blank" rel="noopener" title="${oOaUrl ? "DOI page" : "Publisher page (likely paywalled)"}">${oOaUrl ? "" : lockIcon(false) + " "}${escapeHtml(p.doi_o)}</a>` : ""}
          </div>
          ${p.link_evidence ? `<div class="evidence">Evidence: ${escapeHtml(p.link_evidence)}</div>` : ""}
        </div>
        <div class="choices">
          <button class="choice success" data-original="correct">Correct match</button>
          <button class="choice danger" data-original="wrong">Wrong paper</button>
          <button class="choice warn" data-original="unsure">Can't tell</button>
        </div>
      </div>

      <div class="gate hidden" id="gate-3">
        <h3><span class="gate-num">iii.</span>Outcome check</h3>
        <p class="question">Does the system's outcome judgement match the authors' actual conclusion?</p>
        <div class="outcome-info">
          <span class="outcome-label ${escapeHtml(outcomeLabel)}">${escapeHtml(outcomeLabel)}</span>
          <div class="outcome-quote-wrap">
            ${hasQuote
              ? `<div class="outcome-quote" id="outcome-quote-text">"${escapeHtml(p.outcome_phrase)}"</div>`
              : '<p style="margin:0.4rem 0"><em>No outcome quote was extracted.</em></p>'}
            <textarea class="outcome-quote-edit hidden" id="outcome-quote-edit" rows="3"></textarea>
            <button class="link-btn small" id="edit-quote-btn">edit / extend quote</button>
          </div>
        </div>
        <div class="choices">
          <button class="choice success" data-outcome="correct">Looks right</button>
          <button class="choice danger" data-outcome="wrong">Mischaracterised</button>
          <button class="choice warn" data-outcome="unsure">Can't tell</button>
        </div>
      </div>

      <textarea class="comment hidden" placeholder="Optional notes / why? (+3 pts)"></textarea>

      <div class="actions">
        <span class="shortcut-hint">↵ submit · ⌘↵ from notes</span>
        <button class="btn-primary" id="submit-btn" disabled>Submit</button>
      </div>
    </div>
  `;

  container.querySelectorAll(".choice").forEach((b) => (b.onclick = () => onChoice(b)));
  container.querySelector(".comment").oninput = (e) => (state.judgement.comment = e.target.value);
  container.querySelector("#submit-btn").onclick = onboarding ? submitOnboarding : submitJudgement;

  if (!onboarding) {
    container.querySelector("#skip-btn").onclick = onSkip;
  }

  wireEditButtons(container, p);
}

function wireEditButtons(container, p) {
  const editAbstractBtn = container.querySelector("#edit-abstract-btn");
  const abstractText = container.querySelector("#abstract-text");
  const abstractEdit = container.querySelector("#abstract-edit");
  editAbstractBtn.onclick = () => {
    if (abstractEdit.classList.contains("hidden")) {
      abstractEdit.value = p.abstract_r || "";
      abstractText.classList.add("hidden");
      abstractEdit.classList.remove("hidden");
      editAbstractBtn.textContent = "save edited abstract";
    } else {
      const v = abstractEdit.value.trim();
      state.judgement.edited_abstract = v && v !== (p.abstract_r || "").trim() ? v : null;
      abstractText.textContent = v || "(no abstract available)";
      abstractText.classList.remove("hidden");
      abstractEdit.classList.add("hidden");
      editAbstractBtn.textContent = state.judgement.edited_abstract ? "edit abstract (✓ edited)" : "edit abstract";
    }
  };

  const editQuoteBtn = container.querySelector("#edit-quote-btn");
  if (editQuoteBtn) {
    const quoteText = container.querySelector("#outcome-quote-text");
    const quoteEdit = container.querySelector("#outcome-quote-edit");
    editQuoteBtn.onclick = () => {
      if (quoteEdit.classList.contains("hidden")) {
        quoteEdit.value = p.outcome_phrase || "";
        if (quoteText) quoteText.classList.add("hidden");
        quoteEdit.classList.remove("hidden");
        editQuoteBtn.textContent = "save edited quote";
      } else {
        const v = quoteEdit.value.trim();
        state.judgement.edited_outcome_quote = v && v !== (p.outcome_phrase || "").trim() ? v : null;
        if (quoteText) {
          quoteText.textContent = v ? `"${v}"` : "";
          quoteText.classList.remove("hidden");
        }
        quoteEdit.classList.add("hidden");
        editQuoteBtn.textContent = state.judgement.edited_outcome_quote ? "edit quote (✓ edited)" : "edit / extend quote";
      }
    };
  }
}

function onChoice(btn) {
  const pairBody = btn.closest(".pair-body");
  const parent = btn.parentElement;
  parent.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
  btn.classList.add("selected");

  if (btn.dataset.type) {
    state.judgement.type = btn.dataset.type;
    if (btn.dataset.type === "not_validation") {
      pairBody.querySelector("#gate-2").classList.add("hidden");
      pairBody.querySelector("#gate-3").classList.add("hidden");
      state.judgement.original = null;
      state.judgement.outcome = null;
    } else {
      pairBody.querySelector("#gate-2").classList.remove("hidden");
    }
    pairBody.querySelector(".comment").classList.remove("hidden");
  } else if (btn.dataset.original) {
    state.judgement.original = btn.dataset.original;
    pairBody.querySelector("#gate-3").classList.remove("hidden");
  } else if (btn.dataset.outcome) {
    state.judgement.outcome = btn.dataset.outcome;
  }
  updateSubmitState(pairBody);
}

function updateSubmitState(pairBody) {
  const j = state.judgement;
  const ready = j.type === "not_validation" || (j.type && j.original && j.outcome);
  const btn = (pairBody || document).querySelector("#submit-btn");
  if (btn) btn.disabled = !ready;
}

async function onSkip() {
  if (!confirm("Skip this pair? You won't get points and it'll be re-served to others.")) return;
  try {
    await api("/judge", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: state.currentPair.pair_id,
      type_judgement: "skip",
    });
    showToast(0, "skipped");
    await refreshAll();
  } catch (e) {
    alert(e.message);
  }
}

async function submitJudgement() {
  const btn = $("#submit-btn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  try {
    const resp = await api("/judge", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: state.currentPair.pair_id,
      type_judgement: state.judgement.type,
      original_judgement: state.judgement.original,
      outcome_judgement: state.judgement.outcome,
      comment: state.judgement.comment,
      edited_abstract: state.judgement.edited_abstract,
      edited_outcome_quote: state.judgement.edited_outcome_quote,
    });
    showToast(resp.points_earned, "points");
    if (typeof confetti !== "undefined") {
      confetti({
        particleCount: 60,
        spread: 60,
        origin: { y: 0.5 },
        colors: ["#b54614", "#1a1612", "#d6a87e", "#4a6b3e"],
        scalar: 0.9,
      });
    }
    await refreshAll();
  } catch (e) {
    alert(e.message);
    btn.disabled = false;
  }
}

function submitOnboarding() {
  const j = state.judgement;
  const pair = state.currentPair;
  const ready = j.type === "not_validation" || (j.type && j.original && j.outcome);
  if (!ready) return;
  const errors = evaluateOnboarding(pair, j);
  showOnboardingFeedback(pair, errors);
}

/* ---------- Hard-mode entry ---------- */
function renderHardPair(container, p) {
  const doiUrl = p.doi_r ? `https://doi.org/${p.doi_r}` : null;
  const oaUrl = p.oa_url_r || (p.url_r && p.url_r !== doiUrl ? p.url_r : null);
  const scholarQuery = encodeURIComponent(`${p.title_r || ""} ${p.authors_r || ""}`);
  const scholarUrl = `https://scholar.google.com/scholar?q=${scholarQuery}`;
  const lockIcon = (open) => `<span class="oa-lock ${open ? "open" : "gated"}" aria-label="${open ? "open access" : "gated"}">${open ? "🔓" : "🔒"}</span>`;

  container.innerHTML = `
    <div class="pair-header">
      <div class="pair-meta">
        <span class="meta-item">id <b>${escapeHtml(String(p.pair_id).slice(0, 8))}</b></span>
        <span class="meta-item hard-tag">hard mode · 25 pts</span>
        <button class="skip-btn" id="skip-btn">Skip</button>
      </div>
      <h2>${escapeHtml(p.title_r || "(untitled)")}</h2>
      <div class="authors">${escapeHtml(p.authors_r || "?")} · ${escapeHtml(String(p.year_r || "?"))}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}</div>
      <div class="abstract-block">
        <div class="abstract muted-empty">No abstract available — fetch it from the source.</div>
        <div class="abstract-tools">
          ${oaUrl ? `<a href="${escapeHtml(oaUrl)}" target="_blank" rel="noopener" title="Open access PDF">${lockIcon(true)} OA full text</a>` : ""}
          ${doiUrl ? `<a href="${escapeHtml(doiUrl)}" target="_blank" rel="noopener" title="${oaUrl ? "DOI page" : "Publisher page (likely paywalled)"}">${oaUrl ? "" : lockIcon(false) + " "}DOI</a>` : ""}
          <a href="${escapeHtml(scholarUrl)}" target="_blank" rel="noopener" title="Search for an alternate copy">Scholar</a>
        </div>
      </div>
    </div>

    <div class="pair-body">
      <div class="gate">
        <h3><span class="gate-num">★</span>Hard-mode entry</h3>
        <p class="question">Find the linked original and the outcome ourselves. If you've gotten this far, it's a real validation by definition.</p>

        <div class="hard-form">
          <div class="hard-row">
            <label>Original DOI <small>(strongly recommended)</small></label>
            <input type="text" id="hard-doi-o" placeholder="10.xxxx/xxxxx">
          </div>
          <div class="hard-row">
            <label>Original title</label>
            <input type="text" id="hard-title-o" placeholder="Full title of the original study">
          </div>
          <div class="hard-row two-col">
            <div>
              <label>Original authors</label>
              <input type="text" id="hard-authors-o" placeholder="Last, F.; Last, F.">
            </div>
            <div>
              <label>Year</label>
              <input type="text" id="hard-year-o" placeholder="2018">
            </div>
          </div>

          <div class="hard-row">
            <label>Outcome category</label>
            <div class="choices small-choices">
              <button class="choice success" data-hard-outcome="success">Success</button>
              <button class="choice warn" data-hard-outcome="mixed">Mixed</button>
              <button class="choice danger" data-hard-outcome="failure">Failure</button>
              <button class="choice" data-hard-outcome="uninformative">Uninformative</button>
            </div>
          </div>

          <div class="hard-row">
            <label>Outcome quote <small>(verbatim sentence(s) supporting your category)</small></label>
            <textarea id="hard-outcome-quote" rows="3" placeholder="Paste the sentence(s) from the paper..."></textarea>
          </div>

          <textarea class="comment" placeholder="Optional notes / source you used (+3 pts)"></textarea>
        </div>
      </div>

      <div class="actions">
        <span class="shortcut-hint">⌘↵ submit</span>
        <button class="btn-primary" id="submit-btn" disabled>Submit (+25)</button>
      </div>
    </div>
  `;

  const submitBtn = container.querySelector("#submit-btn");
  const updateHardSubmit = () => {
    const e = collectHardEntry(container);
    submitBtn.disabled = !(e && e.outcome && e.outcome_phrase && e.outcome_phrase.length > 5);
  };

  container.querySelectorAll(".choice").forEach((b) => {
    b.onclick = () => {
      container.querySelectorAll(`.choice[data-hard-outcome]`).forEach((x) => x.classList.remove("selected"));
      b.classList.add("selected");
      updateHardSubmit();
    };
  });
  container.querySelector(".comment").oninput = (e) => (state.judgement.comment = e.target.value);
  ["hard-doi-o", "hard-title-o", "hard-authors-o", "hard-year-o", "hard-outcome-quote"].forEach((id) => {
    container.querySelector("#" + id).oninput = updateHardSubmit;
  });
  container.querySelector("#skip-btn").onclick = onSkip;
  submitBtn.onclick = () => submitHard(container);
}

function collectHardEntry(container) {
  const selectedOutcome = container.querySelector(".choice[data-hard-outcome].selected");
  return {
    doi_o: container.querySelector("#hard-doi-o").value.trim() || null,
    title_o: container.querySelector("#hard-title-o").value.trim() || null,
    authors_o: container.querySelector("#hard-authors-o").value.trim() || null,
    year_o: container.querySelector("#hard-year-o").value.trim() || null,
    outcome: selectedOutcome ? selectedOutcome.dataset.hardOutcome : null,
    outcome_phrase: container.querySelector("#hard-outcome-quote").value.trim(),
  };
}

async function submitHard(container) {
  const entry = collectHardEntry(container);
  if (!entry.outcome || !entry.outcome_phrase) return;
  const submitBtn = container.querySelector("#submit-btn");
  submitBtn.disabled = true;
  try {
    const resp = await api("/judge", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: state.currentPair.pair_id,
      type_judgement: "replication",
      comment: state.judgement.comment,
      hard_mode: true,
      hard_mode_entry: entry,
    });
    showToast(resp.points_earned, "points");
    if (typeof confetti !== "undefined") {
      confetti({ particleCount: 90, spread: 75, origin: { y: 0.5 }, scalar: 1.0 });
    }
    await refreshAll();
  } catch (e) {
    alert(e.message);
    submitBtn.disabled = false;
  }
}

/* ---------- Keyboard ---------- */
document.addEventListener("keydown", (e) => {
  const onb = !$("#onboarding-screen").classList.contains("hidden");
  const game = !$("#game-screen").classList.contains("hidden");
  if (!onb && !game) return;
  if (e.key === "Enter") {
    if (e.target.tagName === "TEXTAREA" && !(e.metaKey || e.ctrlKey)) return;
    if (e.target.tagName === "INPUT" && !(e.metaKey || e.ctrlKey)) return;
    e.preventDefault();
    const btn = $("#submit-btn");
    if (btn && !btn.disabled) btn.click();
  }
});
