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
    corrected_outcome: null,
    corrected_doi_o: null,
    corrected_study_o: null,
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

  // Show "server waking up" toast if response takes more than 3 seconds
  let slowTimer = setTimeout(() => showToast("Server waking up… hang tight.", 15000), 3000);
  try {
    const res = await fetch("/api" + path, opts);
    clearTimeout(slowTimer);
    hideToast();
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      let msg = res.statusText;
      if (err.detail) {
        if (Array.isArray(err.detail)) {
          msg = err.detail.map((e) => e.msg || JSON.stringify(e)).join("; ");
        } else {
          msg = String(err.detail);
        }
      }
      throw new Error(msg);
    }
    return res.json();
  } catch (e) {
    clearTimeout(slowTimer);
    hideToast();
    throw e;
  }
}

function hideToast() {
  const t = $("#toast");
  t.classList.remove("show");
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

  if (route === "/skip") {
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    judgements.push({
      coder_id: body.coder_id,
      pair_id: body.pair_id,
      type_judgement: "skip",
      points: 0,
      created_at: new Date().toISOString(),
    });
    writeJSON(STORAGE.JUDGEMENTS, judgements);
    return { skipped: true };
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

function fmtYear(y) {
  if (y === null || y === undefined || y === "") return "?";
  const n = parseFloat(y);
  return isNaN(n) ? String(y) : String(Math.round(n));
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
  if (!handle) { await showAlert("Please enter a handle."); return; }

  // Admin path: if the email field has no "@" treat it as a password and attempt admin login
  if (loginMode === "email") {
    const fieldVal = $("#email-input").value.trim();
    if (fieldVal && !fieldVal.includes("@")) {
      try {
        await adminLogin(handle, fieldVal);
        return; // success — entered admin screen
      } catch (e) {
        await showAlert("Admin login failed: " + e.message);
        return; // stop here — don't fall through to validator login
      }
    }
  }

  let body;
  if (loginMode === "email") {
    const email = $("#email-input").value.trim();
    if (!email || !email.includes("@")) {
      await showAlert("Please enter a valid email address.");
      return;
    }
    body = { handle, email };
  } else {
    const code = getCode();
    if (code.length < 6) {
      await showAlert("Please fill in all four parts of your code.");
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
    if (e.message.includes("already registered")) {
      const result = await showDialog({
        title: "Username not found",
        message: e.message,
        layout: "row",
        buttons: [
          { label: "Forgot username →", value: "forgot" },
          { label: "OK", value: "ok", primary: true },
        ],
      });
      if (result === "forgot") openForgotModal();
    } else {
      await showAlert(e.message);
    }
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

function clearSession() {
  clearTimeout(_inactivityTimer);
  _clearInactivityCountdown();
  clearPairTimer();
  localStorage.removeItem(STORAGE.CODER);
  state.coder = null;
}

const logout = () => {
  clearSession();
  location.reload();
};
$("#logout-btn").onclick = logout;
$("#onb-logout-btn").onclick = logout;

/* ---------- Onboarding ---------- */
async function enterOnboarding() {
  $("#onboarding-screen").classList.remove("hidden");
  resetInactivityTimer();
  const resp = await api("/onboarding");
  state.onboardingPairs = resp.pairs;
  state.onboardingIdx = 0;
  state.onboardingResults = [];
  updateOnbProgress();
  // Show guided tour using the first onboarding pair as a live preview
  if (state.onboardingPairs.length > 0) {
    $("#onb-intro").classList.add("hidden");
    startTour(state.onboardingPairs[0]);
  }
}

$("#onb-start-btn").onclick = () => {
  $("#onb-intro").classList.add("hidden");
  showOnboardingPair();
};


$("#welcome-enter-btn").onclick = () => {
  $("#welcome-modal").classList.add("hidden");
  document.body.style.overflow = "";
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
  applySplitLayout();
  updateOnbProgress();
  const header = document.querySelector(".onboarding-header");
  const y = $("#onb-card").getBoundingClientRect().top + window.scrollY - (header?.offsetHeight || 60) - 8;
  window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
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

  // Keep the abstract header visible but collapse the interactive sections
  const card = $("#onb-card");
  const sectionsToHide = splitLayout
    ? [".note-section", ".actions"]          // keep .pair-body so right panel stays filled
    : [".pair-body", ".note-section", ".actions"];
  sectionsToHide.forEach(sel => {
    const el = card.querySelector(sel);
    if (el) el.classList.add("hidden");
  });

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
  fb.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("#onb-next-btn").onclick = async () => {
    state.onboardingResults.push({ correct, idx: state.onboardingIdx });
    state.onboardingIdx += 1;
    if (state.onboardingIdx >= state.onboardingPairs.length) {
      $("#onb-card").classList.add("hidden");
      $("#onb-feedback").classList.add("hidden");
      $("#onb-progress-fill").style.width = "100%";
      $("#onb-counter").textContent = `${state.onboardingPairs.length} / ${state.onboardingPairs.length}`;
      await api("/onboarding/complete", "POST", { coder_id: state.coder.coder_id });
      state.coder.onboarded = true;
      localStorage.setItem(STORAGE.CODER, JSON.stringify(state.coder));
      $("#onboarding-screen").classList.add("hidden");
      $("#welcome-modal").classList.remove("hidden");
      document.body.style.overflow = "hidden";
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
  resetInactivityTimer();
  await refreshAll();
}


$("#mode-toggle").onclick = async () => {
  state.mode = state.mode === "normal" ? "hard" : "normal";
  $("#mode-toggle").textContent = state.mode === "hard" ? "Hard mode" : "Normal mode";
  $("#mode-toggle").classList.toggle("active", state.mode === "hard");
  await loadNextPair();
  await refreshStats();
};

/* ---------- Split-layout toggle ---------- */
let splitLayout = localStorage.getItem("flora.splitLayout") !== "0"; // default on

function applySplitLayout() {
  const cards = [$("#pair-card"), $("#onb-card")].filter(Boolean);
  cards.forEach(c => c.classList.toggle("split-layout", splitLayout));

  const label = splitLayout ? "Column view" : "Split view";
  [$("#layout-toggle"), $("#onb-layout-btn")].forEach(b => {
    if (b) { b.textContent = label; b.classList.toggle("active", splitLayout); }
  });
}

function toggleLayout() {
  splitLayout = !splitLayout;
  localStorage.setItem("flora.splitLayout", splitLayout ? "1" : "0");
  applySplitLayout();
}


// Apply on startup
applySplitLayout();

// Leaderboard hidden by default
document.body.classList.add("sidebar-collapsed");

$("#sidebar-toggle").onclick = () => {
  document.body.classList.toggle("sidebar-collapsed");
  const collapsed = document.body.classList.contains("sidebar-collapsed");
  $("#sidebar-toggle").textContent = collapsed ? "Leaderboard ▸" : "Leaderboard ◂";
};
$("#sidebar-close").onclick = () => {
  document.body.classList.add("sidebar-collapsed");
  $("#sidebar-toggle").textContent = "Leaderboard ▸";
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
  applySplitLayout();
  startPairTimer();
}

/* ---------- Pair timer (30-min auto-skip) ---------- */
let _pairExpireTimer = null;

function startPairTimer() {
  clearPairTimer();
  _pairExpireTimer = setTimeout(async () => {
    showToast("Time's up — entry released to another validator.");
    const btn = $("#skip-btn");
    if (btn) btn.click();
    else await loadNextPair();
  }, 30 * 60 * 1000);
}

function clearPairTimer() {
  clearTimeout(_pairExpireTimer);
  _pairExpireTimer = null;
}

/* ---------- Generic styled dialog (promise-based) ---------- */
function showAlert(message) {
  return showDialog({ message, buttons: [{ label: "OK", value: true, primary: true }] });
}

function showConfirm(message) {
  return showDialog({
    message,
    buttons: [
      { label: "Cancel", value: false },
      { label: "Confirm", value: true, primary: true },
    ],
  });
}

function showDialog({ icon, title, message, buttons, layout = "column" }) {
  return new Promise(resolve => {
    const iconEl  = $("#dialog-icon");
    const titleEl = $("#dialog-title");
    const msgEl   = $("#dialog-message");
    const btnsEl  = $("#dialog-buttons");

    iconEl.textContent  = icon || "";
    iconEl.style.display = icon ? "" : "none";
    titleEl.textContent = title || "";
    titleEl.style.display = title ? "" : "none";
    msgEl.innerHTML     = message || "";

    btnsEl.style.flexDirection = layout === "row" ? "row" : "column";

    btnsEl.innerHTML = "";
    for (const btn of buttons) {
      const el = document.createElement("button");
      el.textContent = btn.label;
      el.className   = btn.primary ? "btn-primary" : "ghost-btn";
      if (btn.muted) el.style.color = "var(--muted)";
      if (layout === "row") el.style.flex = "1";
      el.onclick = () => {
        $("#dialog-modal").classList.add("hidden");
        document.body.style.overflow = "";
        resolve(btn.value);
      };
      btnsEl.appendChild(el);
    }

    $("#dialog-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
  });
}

/* ---------- Inactivity auto-logout (25 min warn + 5 min countdown) ---------- */
const INACTIVITY_WARN_MS    = 25 * 60 * 1000;
const INACTIVITY_COUNTDOWN_S = 5 * 60;
let _inactivityTimer = null;
let _inactivityCountdownInterval = null;
let _inactivityWarningActive = false;

function _clearInactivityCountdown() {
  clearInterval(_inactivityCountdownInterval);
  _inactivityCountdownInterval = null;
  _inactivityWarningActive = false;
  $("#inactivity-warn-modal").classList.add("hidden");
  document.body.style.overflow = "";
}

function _startInactivityCountdown() {
  _inactivityWarningActive = true;
  let remaining = INACTIVITY_COUNTDOWN_S;
  const countdownEl = $("#inactivity-countdown");
  const fmt = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  countdownEl.textContent = fmt(remaining);
  $("#inactivity-warn-modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";

  _inactivityCountdownInterval = setInterval(() => {
    remaining -= 1;
    countdownEl.textContent = fmt(remaining);
    if (remaining <= 0) {
      _clearInactivityCountdown();
      clearSession();
      $("#inactivity-modal").classList.remove("hidden");
      document.body.style.overflow = "hidden";
    }
  }, 1000);
}

function resetInactivityTimer() {
  if (_inactivityWarningActive) return;
  clearTimeout(_inactivityTimer);
  _inactivityTimer = setTimeout(_startInactivityCountdown, INACTIVITY_WARN_MS);
}

$("#inactivity-continue-btn").onclick = () => {
  _clearInactivityCountdown();
  clearTimeout(_inactivityTimer);
  _inactivityTimer = setTimeout(_startInactivityCountdown, INACTIVITY_WARN_MS);
};

$("#inactivity-ok-btn").onclick = () => {
  document.body.style.overflow = "";
  location.reload();
};

["mousemove", "mousedown", "keydown", "touchstart", "scroll"].forEach(evt =>
  document.addEventListener(evt, resetInactivityTimer, { passive: true })
);

/* ---------- Pair rendering (normal + onboarding) ---------- */
let _pairShownAt = null;

function renderPairInto(container, p, { onboarding, judgeCount }) {
  if (!onboarding) _pairShownAt = Date.now();
  const outcomeLabel = (p.outcome || "uninformative").toLowerCase();
  const doiUrl = p.doi_r ? `https://doi.org/${p.doi_r}` : null;
  const oaUrl = p.oa_url_r || (p.url_r && p.url_r !== doiUrl ? p.url_r : null);
  const scholarQuery = encodeURIComponent(`${p.title_r || ""} ${p.authors_r || ""}`);
  const scholarUrl = `https://scholar.google.com/scholar?q=${scholarQuery}`;
  const oUrl = p.doi_o ? `https://doi.org/${p.doi_o}` : null;
  const oOaUrl = p.oa_url_o || null;
  const hasQuote = p.outcome_phrase && p.outcome_phrase.trim();
  const lockIcon = (open) => `<span class="oa-lock ${open ? "open" : "gated"}" aria-label="${open ? "open access" : "gated"}">${open ? "🔓" : "🔒"}</span>`;

  const pType = (p.type || "replication").toLowerCase();
  const oppositeType = pType === "replication" ? "reproduction" : "replication";
  const oppositeLabel = pType === "replication" ? "same data" : "different data";
  const gate1Body = onboarding
    ? `<p class="question">Is this paper actually validating a previous finding?</p>
       <div class="choices">
         <button class="choice success" data-type="replication">Replication<small>different data</small></button>
         <button class="choice success" data-type="reproduction">Reproduction<small>same data</small></button>
         <button class="choice danger" data-type="not_validation">Neither<small>not a validation</small></button>
       </div>`
    : `<p class="question">The system classified this as&ensp;<span class="outcome-label ${pType}">${pType}</span>&ensp;— is that correct?</p>
       <div class="choices">
         <button class="choice success" data-type="${pType}">✓ Correct</button>
         <button class="choice warn" data-type="${oppositeType}">Actually ${oppositeType}<small>${oppositeLabel}</small></button>
         <button class="choice danger" data-type="not_validation">✗ Not a validation<small>not studying replication</small></button>
       </div>`;
  const gate2Question = onboarding
    ? "Does this match the paper actually being validated?"
    : "The system identified this as the original study — is this the right paper?";
  const gate3Question = onboarding
    ? "Does the system's outcome judgement match the authors' actual conclusion?"
    : "Is the extracted outcome correct? Check the quote below against the paper.";

  container.innerHTML = `
    <div class="pair-header">
      <div class="pair-meta">
${onboarding ? `<span class="meta-item onboarding-tag">onboarding</span>` : ""}
        ${onboarding ? "" : `<button class="skip-btn" id="skip-btn">Skip — broken / unclear</button>`}
      </div>

      <h2>${escapeHtml(p.title_r || "(untitled)")}</h2>
      <div class="authors">${escapeHtml(p.authors_r || "?")} · ${fmtYear(p.year_r)}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}</div>

      <div class="abstract-block">
        <div class="abstract expanded" id="abstract-text">${escapeHtml(p.abstract_r || "(no abstract available)")}</div>
        <textarea class="abstract-edit hidden" id="abstract-edit"></textarea>
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
        <div class="gate-header-row">
          <h3><span class="gate-num">i.</span>Type check</h3>
          <span class="gate-chip hidden"></span>
          <button class="gate-change-btn link-btn hidden">change ↩</button>
        </div>
        <div class="gate-body">
          ${gate1Body}
        </div>
      </div>

      <div class="gate hidden" id="gate-2">
        <div class="gate-header-row">
          <h3><span class="gate-num">ii.</span>Original check</h3>
          <span class="gate-chip hidden"></span>
          <button class="gate-change-btn link-btn hidden">change ↩</button>
        </div>
        <div class="gate-body">
          <p class="question">${gate2Question}</p>
          <div class="original-info">
            <div class="title">${escapeHtml(p.title_o || "(no title)")}</div>
            <div class="meta">
              ${escapeHtml(p.authors_o || "?")} · ${fmtYear(p.year_o)}
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
          <div id="original-correction" class="hidden" style="margin-top:1rem;padding:0.85rem 1rem;background:var(--bg-alt);border-radius:8px;border:1px solid var(--rule)">
            <p style="font-size:0.85rem;color:var(--muted);margin:0 0 0.65rem">Optional — paste the correct paper below to help with the fix:</p>
            <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:0.2rem">DOI or URL</label>
            <input id="corrected-doi-input" type="text" placeholder="e.g. https://doi.org/10.1000/xyz"
              style="width:100%;box-sizing:border-box;padding:0.4rem 0.6rem;font-size:0.85rem;border:1px solid var(--rule);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:0.55rem">
            <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:0.2rem">Study title</label>
            <input id="corrected-study-input" type="text" placeholder="e.g. Smith et al. (2018) — The effect of..."
              style="width:100%;box-sizing:border-box;padding:0.4rem 0.6rem;font-size:0.85rem;border:1px solid var(--rule);border-radius:6px;background:var(--bg);color:var(--ink)">
          </div>
        </div>
      </div>

      <div class="gate hidden" id="gate-3">
        <div class="gate-header-row">
          <h3><span class="gate-num">iii.</span>Outcome check</h3>
          <span class="gate-chip hidden"></span>
          <button class="gate-change-btn link-btn hidden">change ↩</button>
        </div>
        <div class="gate-body">
          <p class="question">${gate3Question}</p>
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
          <div class="outcome-correction hidden" id="outcome-correction">
            <p id="outcome-correction-label" style="margin:12px 0 6px;font-size:13px;color:var(--muted);">What is the correct outcome?</p>
            <div class="choices">
              <button class="choice success" data-correct-outcome="success">Success</button>
              <button class="choice danger" data-correct-outcome="failed">Failed</button>
              <button class="choice warn" data-correct-outcome="mixed">Mixed</button>
              <button class="choice" data-correct-outcome="uninformative">Uninformative</button>
            </div>
          </div>
        </div>
      </div>

      <div class="note-section">
        <button class="note-toggle" id="note-toggle-btn" type="button">
          <span class="note-toggle-icon">＋</span> Add a note <span class="note-pts">(+3 pts)</span>
        </button>
        <div class="note-body hidden">
          <textarea class="comment" placeholder="Optional notes / why?"></textarea>
        </div>
      </div>

      <div class="actions">
        <span class="shortcut-hint">↵ submit · ⌘↵ from notes</span>
        <button class="btn-primary" id="submit-btn" disabled>Submit</button>
      </div>
    </div>
  `;

  container.querySelectorAll(".choice").forEach((b) => (b.onclick = () => onChoice(b)));
  container.querySelector(".comment").oninput = (e) => (state.judgement.comment = e.target.value);
  container.querySelector("#submit-btn").onclick = onboarding ? submitOnboarding : guardedSubmit;

  const noteToggleBtn = container.querySelector("#note-toggle-btn");
  const noteBody = container.querySelector(".note-body");
  noteToggleBtn.addEventListener("click", () => {
    const open = noteBody.classList.toggle("hidden") === false;
    noteToggleBtn.querySelector(".note-toggle-icon").textContent = open ? "−" : "＋";
    if (open) container.querySelector(".comment").focus();
  });

  if (!onboarding) {
    container.querySelector("#skip-btn").onclick = onSkip;
  }

  wireEditButtons(container, p);

  // Wire original correction inputs
  const doiInput   = container.querySelector("#corrected-doi-input");
  const studyInput = container.querySelector("#corrected-study-input");
  if (doiInput)   doiInput.oninput   = () => { state.judgement.corrected_doi_o   = doiInput.value.trim()   || null; };
  if (studyInput) studyInput.oninput = () => { state.judgement.corrected_study_o = studyInput.value.trim() || null; };

  // Click answered gate header to toggle its body open/closed
  container.querySelectorAll(".gate-header-row").forEach((row) => {
    row.addEventListener("click", () => {
      const gate = row.closest(".gate");
      if (!gate.classList.contains("gate-answered")) return;
      const body = gate.querySelector(".gate-body");
      const changeBtn = gate.querySelector(".gate-change-btn");
      const opening = !body.classList.contains("open");
      body.classList.toggle("open", opening);
      if (changeBtn) changeBtn.textContent = opening ? "collapse ↑" : "change ↩";
    });
  });
}

function wireEditButtons(container, p) {
  const editAbstractBtn = container.querySelector("#edit-abstract-btn");
  const abstractEdit = container.querySelector("#abstract-edit");
  const abstractText = container.querySelector("#abstract-text");
  editAbstractBtn.onclick = () => {
    if (abstractEdit.classList.contains("hidden")) {
      abstractEdit.value = p.abstract_r || "";
      abstractEdit.style.height = abstractText.offsetHeight + "px";
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
    const quoteText    = container.querySelector("#outcome-quote-text");
    const quoteEdit    = container.querySelector("#outcome-quote-edit");
    const outcomeChoices    = container.querySelector("#gate-3 .choices");
    const correctionRow     = container.querySelector("#outcome-correction");

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

        if (state.judgement.edited_outcome_quote) {
          // Quote was changed — auto-select "wrong" and skip the 3 buttons
          state.judgement.outcome = "wrong";
          state.judgement.corrected_outcome = null;
          if (outcomeChoices) outcomeChoices.classList.add("hidden");
          if (correctionRow) {
            correctionRow.classList.remove("hidden");
            const lbl = correctionRow.querySelector("#outcome-correction-label");
            if (lbl) lbl.textContent = "You edited the quote — what is the correct outcome label?";
          }
          editQuoteBtn.textContent = "edit quote (✓ edited)";
        } else {
          // Reverted — restore the 3 choice buttons
          state.judgement.outcome = null;
          state.judgement.corrected_outcome = null;
          if (outcomeChoices) outcomeChoices.classList.remove("hidden");
          if (correctionRow) {
            correctionRow.classList.add("hidden");
            correctionRow.querySelectorAll(".choice").forEach(b => b.classList.remove("selected"));
          }
          editQuoteBtn.textContent = "edit / extend quote";
        }
        updateSubmitState(container.closest(".pair-body"));
      }
    };
  }
}

function onChoice(btn) {
  const pairBody = btn.closest(".pair-body");
  const parent = btn.parentElement;
  const gate = btn.closest(".gate");
  const wasAnswered = gate.classList.contains("gate-answered");

  parent.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
  btn.classList.add("selected");

  if (btn.dataset.type) {
    state.judgement.type = btn.dataset.type;
    if (btn.dataset.type === "not_validation") {
      const g2 = pairBody.querySelector("#gate-2");
      const g3 = pairBody.querySelector("#gate-3");
      unanswerGate(g2); g2.classList.add("hidden");
      unanswerGate(g3); g3.classList.add("hidden");
      state.judgement.original = null;
      state.judgement.outcome = null;
      state.judgement.corrected_outcome = null;
    } else {
      if (wasAnswered) {
        unanswerGate(pairBody.querySelector("#gate-2"));
        unanswerGate(pairBody.querySelector("#gate-3"));
        pairBody.querySelector("#gate-3").classList.add("hidden");
        const cr = pairBody.querySelector("#outcome-correction");
        if (cr) cr.classList.add("hidden");
      }
      pairBody.querySelector("#gate-2").classList.remove("hidden");
    }
    pairBody.querySelector(".comment").classList.remove("hidden");
  } else if (btn.dataset.original) {
    state.judgement.original = btn.dataset.original;
    if (wasAnswered) {
      unanswerGate(pairBody.querySelector("#gate-3"));
      state.judgement.outcome = null;
      state.judgement.corrected_outcome = null;
      const cr = pairBody.querySelector("#outcome-correction");
      if (cr) cr.classList.add("hidden");
    }
    // Show / hide original correction panel
    const oc = pairBody.querySelector("#original-correction");
    if (oc) {
      const show = btn.dataset.original === "wrong";
      oc.classList.toggle("hidden", !show);
      if (!show) {
        state.judgement.corrected_doi_o   = null;
        state.judgement.corrected_study_o = null;
        const doi = oc.querySelector("#corrected-doi-input");
        const study = oc.querySelector("#corrected-study-input");
        if (doi)   doi.value   = "";
        if (study) study.value = "";
      }
    }
    pairBody.querySelector("#gate-3").classList.remove("hidden");
  } else if (btn.dataset.outcome) {
    state.judgement.outcome = btn.dataset.outcome;
    state.judgement.corrected_outcome = null;
    const correctionRow = pairBody.querySelector("#outcome-correction");
    if (correctionRow) {
      const show = btn.dataset.outcome === "wrong";
      correctionRow.classList.toggle("hidden", !show);
      correctionRow.querySelectorAll(".choice").forEach(b => b.classList.remove("selected"));
      if (show) {
        const lbl = correctionRow.querySelector("#outcome-correction-label");
        if (lbl) lbl.textContent = "What is the correct outcome?";
      }
    }
  } else if (btn.dataset.correctOutcome) {
    state.judgement.corrected_outcome = btn.dataset.correctOutcome;
    pairBody.querySelectorAll("[data-correct-outcome]").forEach(b => b.classList.remove("selected"));
    btn.classList.add("selected");
    updateSubmitState(pairBody);
    return;
  }

  updateSubmitState(pairBody);
  setTimeout(() => answerGate(gate, getAnswerLabel(btn), getAnswerClass(btn)), 300);
}

function answerGate(gate, label, cls) {
  gate.classList.add("gate-answered");
  const chip = gate.querySelector(".gate-chip");
  if (chip) {
    chip.textContent = label;
    chip.className = `gate-chip${cls ? " " + cls : ""}`;
    chip.classList.remove("hidden");
  }
  const changeBtn = gate.querySelector(".gate-change-btn");
  if (changeBtn) { changeBtn.textContent = "change ↩"; changeBtn.classList.remove("hidden"); }
  const body = gate.querySelector(".gate-body");
  if (body) body.classList.remove("open");
}

function unanswerGate(gate) {
  if (!gate) return;
  gate.classList.remove("gate-answered");
  const chip = gate.querySelector(".gate-chip");
  if (chip) { chip.textContent = ""; chip.classList.add("hidden"); }
  const changeBtn = gate.querySelector(".gate-change-btn");
  if (changeBtn) { changeBtn.classList.add("hidden"); }
  const body = gate.querySelector(".gate-body");
  if (body) body.classList.remove("open");
  gate.querySelectorAll(".choice.selected").forEach((b) => b.classList.remove("selected"));
}

function getAnswerLabel(btn) {
  if (btn.dataset.type) {
    return { replication: "Replication", reproduction: "Reproduction", not_validation: "Neither" }[btn.dataset.type] || btn.dataset.type;
  }
  if (btn.dataset.original) {
    return { correct: "Correct match", wrong: "Wrong paper", unsure: "Can't tell" }[btn.dataset.original] || btn.dataset.original;
  }
  if (btn.dataset.outcome) {
    return { correct: "Looks right", wrong: "Mischaracterised", unsure: "Can't tell" }[btn.dataset.outcome] || btn.dataset.outcome;
  }
  return "";
}

function getAnswerClass(btn) {
  if (btn.classList.contains("success")) return "success";
  if (btn.classList.contains("danger")) return "danger";
  if (btn.classList.contains("warn")) return "warn";
  return "";
}

function updateSubmitState(pairBody) {
  const j = state.judgement;
  const outcomeReady = j.outcome && (j.outcome !== "wrong" || j.corrected_outcome);
  const ready = j.type === "not_validation" || (j.type && j.original && outcomeReady);
  const btn = (pairBody || document).querySelector("#submit-btn");
  if (btn) btn.disabled = !ready;
}

async function onSkip() {
  const ok = await showDialog({
    title: "Skip this pair?",
    message: "You won't get points and it'll be re-served to others.",
    buttons: [
      { label: "Skip →", value: true, primary: true },
      { label: "Cancel", value: false },
    ],
  });
  if (!ok) return;
  clearPairTimer();
  try {
    await api("/skip", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: state.currentPair.pair_id,
    });
    showToast(0, "skipped");
    await refreshAll();
  } catch (e) {
    await showAlert(e.message);
  }
}

async function submitJudgement() {
  const btn = $("#submit-btn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  clearPairTimer();
  try {
    const j = state.judgement;
    const p = state.currentPair;
    const isNotValidation = j.type === "not_validation";
    const pType = (p.type || "").toLowerCase();

    // Map gate answers → new API fields
    const typeCheck = (!isNotValidation && j.type === pType) ? "correct" : "incorrect";
    const correctedType = isNotValidation ? "not_validation"
                        : (typeCheck === "incorrect" ? j.type : null);

    const resp = await api("/judge", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: p.pair_id,
      type_check:     isNotValidation ? "incorrect" : typeCheck,
      original_check: isNotValidation ? "incorrect" : (j.original === "correct" ? "correct" : "incorrect"),
      outcome_check:  isNotValidation ? "incorrect" : (j.outcome  === "correct" ? "correct" : "incorrect"),
      corrected_type:          correctedType || null,
      corrected_doi_o:         j.corrected_doi_o   || null,
      corrected_study_o:       j.corrected_study_o || null,
      corrected_outcome:       j.corrected_outcome || null,
      corrected_outcome_quote: j.edited_outcome_quote || null,
      corrected_abstract:      j.edited_abstract || null,
      validator_notes:         j.comment || null,
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
    await showAlert(e.message);
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
<span class="meta-item hard-tag">hard mode · 25 pts</span>
        <button class="skip-btn" id="skip-btn">Skip</button>
      </div>
      <h2>${escapeHtml(p.title_r || "(untitled)")}</h2>
      <div class="authors">${escapeHtml(p.authors_r || "?")} · ${fmtYear(p.year_r)}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}</div>
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
    await showAlert(e.message);
    submitBtn.disabled = false;
  }
}

/* ---------- Guided Tour ---------- */
const TOUR_STEPS = [
  {
    sel: null,
    title: "Here's your workspace",
    body: "Each entry is a replication study. You'll check three things the automated pipeline extracted: the study type, the original paper, and the reported outcome. Let's walk through it.",
  },
  {
    sel: ".pair-header",
    title: "The paper",
    body: "Read the title, authors, and abstract. Use <em>OA full text</em>, <em>DOI</em>, or <em>Scholar</em> to open the full paper when you need more context.",
  },
  {
    sel: "#gate-1",
    also: [".pair-header"],
    title: "i. Type check",
    body: "Is this a <em>Replication</em> (different data), a <em>Reproduction</em> (same data), or <em>Neither</em>? This is the first thing you verify.",
  },
  {
    sel: "#gate-2",
    also: [".pair-header"],
    title: "ii. Original check",
    body: "The system found the original study being replicated. Confirm it's the right paper — most are correct, but flag it if something looks off.",
  },
  {
    sel: "#gate-3",
    also: [".pair-header"],
    title: "iii. Outcome check",
    body: "Does the outcome label and supporting quote match what the authors actually concluded? You can <em>edit the quote</em> to extend or correct it.",
  },
  {
    sel: ".note-section",
    title: "Notes · +3 pts",
    body: "Add an optional note to flag anything unusual — a correction, an ambiguity, or useful context. Notes earn bonus points.",
  },
  {
    sel: ".actions",
    title: "Submit",
    body: "Once all three gates are answered, <em>Submit</em> becomes active. Your judgement is saved and the next entry loads straight away.",
  },
];

let _tourStep = 0;

function startTour(pair) {
  const card = $("#onb-card");
  card.classList.remove("hidden");
  renderPairInto(card, pair, { onboarding: true, judgeCount: 0 });

  // Reveal all gates so user can see the full layout
  card.querySelector("#gate-2")?.classList.remove("hidden");
  card.querySelector("#gate-3")?.classList.remove("hidden");

  // Disable all interactive elements so clicks don't trigger real actions
  card.querySelectorAll("button, input, textarea, a").forEach(el => {
    el.dataset.tourDisabled = "1";
    el.style.pointerEvents = "none";
  });

  applySplitLayout();
  $("#tour-overlay").classList.remove("hidden");
  showTourStep(0);
}

function showTourStep(idx) {
  _tourStep = idx;
  const step = TOUR_STEPS[idx];
  const total = TOUR_STEPS.length;

  $("#tour-step-label").textContent = `${idx + 1} / ${total}`;
  $("#tour-title").textContent = step.title;
  $("#tour-body").innerHTML = step.body;
  $("#tour-next-btn").textContent = idx === total - 1 ? "Start calibration →" : "Next →";

  // Dots
  $("#tour-dots").innerHTML = TOUR_STEPS.map((_, i) =>
    `<span class="tour-dot${i === idx ? " active" : ""}"></span>`
  ).join("");

  // Remove previous highlights
  document.querySelectorAll(".tour-highlight").forEach(el => el.classList.remove("tour-highlight"));
  document.querySelectorAll(".tour-highlight-secondary").forEach(el => el.classList.remove("tour-highlight-secondary"));

  // Apply highlight to primary target (white/bright) and secondary elements (dimmed)
  const card = $("#onb-card");
  let target = null;
  if (step.sel) {
    target = card.querySelector(step.sel);
    if (target) {
      target.classList.add("tour-highlight");
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }
  if (step.also) {
    step.also.forEach(sel => {
      const el = card.querySelector(sel);
      if (el) el.classList.add("tour-highlight-secondary");
    });
  }

  // Show & position callout
  const callout = $("#tour-callout");
  callout.classList.remove("hidden");
  positionTourCallout(target);
}

function positionTourCallout(target) {
  const callout = $("#tour-callout");
  const MARGIN = 12;
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const CW = Math.min(320, vw - MARGIN * 2);

  callout.style.width = CW + "px";

  if (!target) {
    callout.style.cssText = `width:${CW}px;top:50%;left:50%;transform:translate(-50%,-50%)`;
    return;
  }

  callout.style.transform = "";
  const rect = target.getBoundingClientRect();

  const calloutH = callout.offsetHeight || 220;
  let top;
  if (rect.bottom + calloutH + MARGIN < vh) {
    top = rect.bottom + MARGIN;
  } else {
    top = vh - calloutH - MARGIN;
  }
  top = Math.max(MARGIN, top);

  const left = Math.max(MARGIN, Math.min(rect.left, vw - CW - MARGIN));

  callout.style.top  = top + "px";
  callout.style.left = left + "px";
}

window.addEventListener("resize", () => {
  const callout = $("#tour-callout");
  if (!callout || callout.classList.contains("hidden")) return;
  const target = document.querySelector(".tour-highlight");
  positionTourCallout(target || null);
});

function endTour() {
  document.querySelectorAll(".tour-highlight").forEach(el => el.classList.remove("tour-highlight"));
  document.querySelectorAll(".tour-highlight-secondary").forEach(el => el.classList.remove("tour-highlight-secondary"));
  $("#tour-overlay").classList.add("hidden");
  $("#tour-callout").classList.add("hidden");

  // Reset card and show calibration intro
  const card = $("#onb-card");
  card.classList.add("hidden");
  card.innerHTML = "";
  $("#onb-intro").classList.remove("hidden");
}

$("#tour-next-btn").onclick = () => {
  if (_tourStep >= TOUR_STEPS.length - 1) endTour();
  else showTourStep(_tourStep + 1);
};
$("#tour-skip-btn").onclick = endTour;

/* ---------- Keyboard ---------- */
document.addEventListener("keydown", (e) => {
  const onb = !$("#onboarding-screen").classList.contains("hidden");
  const game = !$("#game-screen").classList.contains("hidden");
  if (e.key === "Escape") { closeFaq(); return; }
  if (!onb && !game) return;
  if (e.key === "Enter") {
    if (e.target.tagName === "TEXTAREA" && !(e.metaKey || e.ctrlKey)) return;
    if (e.target.tagName === "INPUT" && !(e.metaKey || e.ctrlKey)) return;
    e.preventDefault();
    const btn = $("#submit-btn");
    if (btn && !btn.disabled) btn.click();
  }
});

/* ---------- Too-fast guard ---------- */
const TOO_FAST_THRESHOLD_MS = 17000;

async function guardedSubmit() {
  const elapsed = _pairShownAt ? Date.now() - _pairShownAt : Infinity;
  if (elapsed < TOO_FAST_THRESHOLD_MS) {
    const result = await showDialog({
      icon: "⚡",
      title: "That was fast.",
      message: "You submitted in under 17 seconds. Are you sure you had enough time to read it?",
      buttons: [
        { label: "Yes, submit anyway →", value: "submit", primary: true },
        { label: "Read it again",         value: "reread" },
        { label: "Rest my eyes — log out", value: "logout", muted: true },
      ],
    });
    if (result === "reread")  return;
    if (result === "logout") { logout(); return; }
  }
  submitJudgement();
}

/* ---------- FAQ Modal ---------- */
const FAQ_URL = "https://raw.githubusercontent.com/forrtproject/fred-data/main/output/flora_faq.md";
let faqCache = null;

async function openFaq() {
  const modal = $("#faq-modal");
  const body  = $("#faq-body");
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";

  if (faqCache) { body.innerHTML = faqCache; return; }

  body.innerHTML = '<p class="faq-loading">Loading…</p>';
  try {
    const res = await fetch(FAQ_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const md = await res.text();
    faqCache = marked.parse(md);
    body.innerHTML = faqCache;
  } catch (err) {
    body.innerHTML = `<p class="faq-error">Could not load FAQ (${err.message}). <a href="https://github.com/forrtproject/fred-data/blob/main/output/flora_faq.md" target="_blank" rel="noopener">Open on GitHub →</a></p>`;
  }
}

function closeFaq() {
  const modal = $("#faq-modal");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  document.body.style.overflow = "";
}

$("#mobile-continue-btn").onclick = () =>
  $("#mobile-warning").classList.add("dismissed");

$("#login-faq-btn").onclick  = openFaq;
$("#game-faq-btn").onclick   = openFaq;
$("#onb-faq-btn").onclick    = openFaq;
$("#faq-close-btn").onclick  = closeFaq;

const HELP_EMAIL = "lukas.roeseler@uni-muenster.de";
async function openHelp() {
  const result = await showDialog({
    title: "Contact support",
    message: `For questions or issues, email us at:<br><strong>${HELP_EMAIL}</strong>`,
    buttons: [
      { label: "Send email →", value: "mail", primary: true },
      { label: "OK", value: "ok" },
    ],
  });
  if (result === "mail") window.location.href = `mailto:${HELP_EMAIL}`;
}
$("#game-help-btn").onclick = openHelp;
$("#onb-help-btn").onclick  = openHelp;
$("#faq-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeFaq(); });

/* ============================================================
   ADMIN PANEL
   ============================================================ */

let _adminToken   = null;
let _adminHandle  = null;
let _adminTrusted = false;
let _adminFilter  = "all";
let _adminPage    = 1;
const ADMIN_PER_PAGE = 50;

async function adminApi(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": _adminToken,
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch("/api/admin" + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function adminLogin(handle, password) {
  const resp = await fetch("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ handle, password }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    let msg = resp.statusText;
    if (err.detail) {
      msg = Array.isArray(err.detail)
        ? err.detail.map((e) => e.msg || JSON.stringify(e)).join("; ")
        : String(err.detail);
    }
    throw new Error(msg);
  }
  const data = await resp.json();
  _adminToken   = data.token;
  _adminHandle  = data.handle;
  _adminTrusted = !!data.trusted;
  enterAdminScreen();
  return true;
}

function enterAdminScreen() {
  $("#login-screen").classList.add("hidden");
  $("#onboarding-screen").classList.add("hidden");
  $("#game-screen").classList.add("hidden");
  $("#admin-screen").classList.remove("hidden");
  const badge = $("#admin-handle-badge");
  if (badge) badge.textContent = _adminHandle ? `Signed in as ${_adminHandle}` : "";
  fetchAdminEntries();
}

function signOutAdmin() {
  _adminToken   = null;
  _adminHandle  = null;
  _adminTrusted = false;
  location.reload();
}

async function fetchAdminEntries() {
  const body = $("#admin-table-body");
  body.innerHTML = '<tr><td colspan="8" class="admin-loading">Loading…</td></tr>';
  $("#admin-empty").classList.add("hidden");

  try {
    const data = await adminApi(
      `/entries?filter=${_adminFilter}&page=${_adminPage}&per_page=${ADMIN_PER_PAGE}`
    );
    renderAdminCounts(data.counts);
    renderAdminTable(data.entries, data.total);
    renderAdminPagination(data.total, data.page);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8" class="admin-loading">Error: ${e.message}</td></tr>`;
  }
}

function renderAdminCounts(counts) {
  $("#fc-all").textContent              = counts.all;
  $("#fc-pending-approval").textContent = counts.pending_approval;
  $("#fc-needs-review").textContent     = counts.needs_review;
  $("#fc-llm-errors").textContent       = counts.llm_errors;
  $("#fc-validated").textContent        = counts.validated;
  $("#fc-admin-checked").textContent    = counts.admin_checked;
}

const STATUS_LABELS = {
  unvalidated:          { text: "Unvalidated",      cls: "status-unvalidated" },
  validation_inprogress:{ text: "In progress",      cls: "status-inprogress"  },
  consensus_reached:    { text: "Pending approval", cls: "status-consensus"   },
  validated:            { text: "Validated",         cls: "status-validated"   },
  need_review:          { text: "Needs review",      cls: "status-review"      },
};

function renderAdminTable(entries, total) {
  const body = $("#admin-table-body");

  if (!entries.length) {
    body.innerHTML = "";
    $("#admin-empty").classList.remove("hidden");
    return;
  }
  $("#admin-empty").classList.add("hidden");

  const offset = (_adminPage - 1) * ADMIN_PER_PAGE;
  body.innerHTML = entries.map((e, i) => {
    const s      = STATUS_LABELS[e.validation_status] || { text: e.validation_status, cls: "" };
    const flags  = [
      e.has_llm_error  ? '<span class="admin-flag flag-llm" title="LLM error">LLM</span>' : "",
      e.is_tiebreaker  ? '<span class="admin-flag flag-tie" title="Tiebreaker">TIE</span>' : "",
      e.admin_checked  ? '<span class="admin-flag flag-admin" title="Admin checked">✓</span>' : "",
    ].join("");
    const validators = [
      e.has_v1  ? "V1" : "—",
      e.has_v2  ? "V2" : "—",
      e.has_llm ? "LLM" : "—",
    ].join(" ");
    const study = (e.study_r || e.doi_r || "—").substring(0, 60);
    const tc = e.trusted_validator_count || 0;
    const trustBadge = tc === 2
      ? '<span class="admin-trust-badge trust-double" title="Both validators are trusted">⭐⭐</span>'
      : tc === 1
      ? '<span class="admin-trust-badge trust-single" title="One trusted validator">⭐</span>'
      : "";
    const needsAttentionFlag = e.validation_status === "need_review" && tc === 0
      ? '<span class="admin-trust-badge trust-alert" title="No trusted validator involved — needs careful review">🔴</span>'
      : "";
    const actions = e.validation_status === "consensus_reached"
      ? `<button class="admin-approve-btn" data-id="${e.record_id}">Approve ✓</button>
         <button class="admin-review-btn ghost-btn" data-id="${e.record_id}">Review</button>`
      : `<button class="admin-review-btn ghost-btn" data-id="${e.record_id}">Review →</button>`;
    const approvedBy = e.admin_name
      ? `<span style="font-size:0.8rem;color:var(--muted)">${e.admin_name}</span>`
      : "—";
    return `<tr>
      <td class="admin-cell-num">${offset + i + 1}</td>
      <td class="admin-cell-study" title="${(e.study_r || "").replace(/"/g, "&quot;")}">${study}${flags}${trustBadge}${needsAttentionFlag}</td>
      <td>${e.type || "—"}</td>
      <td>${e.outcome || "—"}</td>
      <td><span class="admin-status ${s.cls}">${s.text}</span></td>
      <td class="admin-cell-validators">${validators}</td>
      <td>${approvedBy}</td>
      <td class="admin-cell-actions">${actions}</td>
    </tr>`;
  }).join("");

  body.querySelectorAll(".admin-review-btn").forEach((btn) => {
    btn.onclick = () => openAdminDetail(btn.dataset.id);
  });
  body.querySelectorAll(".admin-approve-btn").forEach((btn) => {
    btn.onclick = () => quickApprove(btn.dataset.id, btn);
  });
}

function renderAdminPagination(total, page) {
  const pages = Math.ceil(total / ADMIN_PER_PAGE);
  const el = $("#admin-pagination");
  if (pages <= 1) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <button class="ghost-btn" id="admin-prev" ${page <= 1 ? "disabled" : ""}>← Prev</button>
    <span class="admin-page-info">Page ${page} of ${pages} (${total} entries)</span>
    <button class="ghost-btn" id="admin-next" ${page >= pages ? "disabled" : ""}>Next →</button>
  `;
  $("#admin-prev").onclick = () => { _adminPage--; fetchAdminEntries(); };
  $("#admin-next").onclick = () => { _adminPage++; fetchAdminEntries(); };
}

async function quickApprove(recordId, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    await adminApi(`/entries/${recordId}/approve`, "POST");
    showToast("Entry approved.", 2000);
    fetchAdminEntries();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Approve ✓";
    await showAlert("Error: " + e.message);
  }
}

async function openAdminDetail(recordId) {
  const modal = $("#admin-detail-modal");
  const body  = $("#admin-detail-body");
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  body.innerHTML = '<p class="faq-loading">Loading…</p>';

  try {
    const data = await adminApi(`/entries/${recordId}`);
    renderAdminDetail(data);
  } catch (e) {
    body.innerHTML = `<p class="faq-error">Error: ${e.message}</p>`;
  }
}

function renderAdminDetail(data) {
  const rec = data.record;
  const abstractBanner = data.abstract_only_conflict
    ? `<div class="admin-abstract-banner">
         <strong>Abstract conflict only</strong> — all checks and corrections agree. Only the edited abstract differs between the two validators.
       </div>`
    : "";
  const v1  = rec.validator_1;
  const v2  = rec.validator_2;
  const llm = rec.llm_validator;

  const valCard = (label, v) => {
    if (!v) return `<div class="admin-val-card admin-val-empty"><strong>${label}</strong><p>Not yet submitted.</p></div>`;
    const checks = [
      `Type: <b>${v.type_check}</b>`,
      `Original: <b>${v.original_check}</b>`,
      `Outcome: <b>${v.outcome_check}</b>`,
    ].join(" · ");
    const corrections = [
      v.corrected_type          ? `Type → ${v.corrected_type}` : "",
      v.corrected_study_o       ? `Original → ${v.corrected_study_o}` : "",
      v.corrected_outcome       ? `Outcome → ${v.corrected_outcome}` : "",
      v.corrected_outcome_quote ? `Quote → ${v.corrected_outcome_quote}` : "",
    ].filter(Boolean).join("; ");
    const error = v.error ? `<p class="admin-val-error">LLM error: ${v.error}</p>` : "";
    return `<div class="admin-val-card">
      <div class="admin-val-label">${label}${v.validator_name ? ` · <em>${v.validator_name}</em>` : ""}</div>
      <div class="admin-val-checks">${checks}</div>
      ${corrections ? `<div class="admin-val-corrections">${corrections}</div>` : ""}
      ${v.validator_notes ? `<div class="admin-val-note">Note: ${v.validator_notes}</div>` : ""}
      ${error}
    </div>`;
  };

  const outcomeOpts = ["success","failure","mixed","uninformative","descriptive"]
    .map((o) => `<option value="${o}" ${rec.outcome === o ? "selected" : ""}>${o}</option>`).join("");
  const typeOpts = ["replication","reproduction"]
    .map((t) => `<option value="${t}" ${rec.type === t ? "selected" : ""}>${t}</option>`).join("");

  $("#admin-detail-title").textContent = (rec.study_r || rec.doi_r || "Entry Review").substring(0, 80);
  $("#admin-detail-body").innerHTML = `
    ${abstractBanner}
    <div class="admin-detail-cols">
      <!-- Left: pair info -->
      <div class="admin-detail-pair">
        <div class="pair-header" style="border-radius:1rem;border:1px solid var(--rule);padding:1rem 1.25rem;margin-bottom:0.75rem">
          <div class="pair-meta">
            <span class="pair-type-badge">${rec.type || "?"}</span>
            <span class="pair-outcome-badge">${rec.outcome || "?"}</span>
            <span class="pair-status-badge">${rec.validation_status}</span>
          </div>
          <h2 class="pair-title">${rec.study_r || rec.doi_r || "—"}</h2>
          <div class="pair-doi"><a href="https://doi.org/${rec.doi_r}" target="_blank" rel="noopener">${rec.doi_r}</a> · ${rec.year_r || "—"}</div>
          ${rec.abstract_r ? `<details class="pair-abstract-details"><summary>Abstract</summary><p class="pair-abstract">${rec.abstract_r}</p></details>` : ""}
          <hr class="pair-divider">
          <div class="pair-original-label">Identified original study</div>
          <div class="pair-original-title">${rec.study_o || rec.doi_o || "—"}</div>
          ${rec.doi_o ? `<div class="pair-doi"><a href="https://doi.org/${rec.doi_o}" target="_blank" rel="noopener">${rec.doi_o}</a></div>` : ""}
          ${rec.outcome_quote ? `<blockquote class="pair-quote">${rec.outcome_quote}</blockquote>` : ""}
        </div>

        <!-- Validator responses -->
        <div class="admin-val-cards">
          ${valCard("Validator 1", v1)}
          ${valCard("Validator 2", v2)}
          ${valCard("LLM", llm)}
        </div>
      </div>

      <!-- Right: admin resolution form -->
      <div class="admin-resolve-form">
        <h3>Admin Resolution</h3>
        <p class="admin-resolve-hint">Override the final values for this entry and mark it as resolved.</p>

        <label class="admin-form-label">Type check</label>
        <select id="ar-type-check" class="admin-select">
          <option value="correct">correct</option>
          <option value="incorrect">incorrect</option>
        </select>
        <input id="ar-corrected-type" class="admin-input" placeholder="Corrected type (if incorrect)">
        <select id="ar-corrected-type-sel" class="admin-select" style="margin-top:0.25rem">
          ${typeOpts}
        </select>

        <label class="admin-form-label" style="margin-top:1rem">Original study check</label>
        <select id="ar-original-check" class="admin-select">
          <option value="correct">correct</option>
          <option value="incorrect">incorrect</option>
        </select>
        <input id="ar-corrected-study" class="admin-input" placeholder="Corrected original study title (if incorrect)">
        <input id="ar-corrected-doi" class="admin-input" placeholder="Corrected DOI (if incorrect)" style="margin-top:0.25rem">

        <label class="admin-form-label" style="margin-top:1rem">Outcome check</label>
        <select id="ar-outcome-check" class="admin-select">
          <option value="correct">correct</option>
          <option value="incorrect">incorrect</option>
        </select>
        <select id="ar-corrected-outcome" class="admin-select" style="margin-top:0.25rem">
          ${outcomeOpts}
        </select>
        <input id="ar-corrected-quote" class="admin-input" placeholder="Corrected outcome quote (optional)" style="margin-top:0.25rem">

        <label class="admin-form-label" style="margin-top:1rem">Admin notes</label>
        <textarea id="ar-notes" class="admin-textarea" placeholder="Notes for the record (optional)"></textarea>

        <div class="admin-resolve-actions">
          <button id="admin-resolve-btn" class="btn-primary" data-id="${rec.record_id}">Mark as Resolved →</button>
          <button id="admin-detail-cancel" class="ghost-btn">Cancel</button>
        </div>
      </div>
    </div>
  `;

  $("#admin-resolve-btn").onclick  = () => submitAdminResolve(rec.record_id);
  $("#admin-detail-cancel").onclick = closeAdminDetail;
}

async function submitAdminResolve(recordId) {
  const btn = $("#admin-resolve-btn");
  btn.disabled = true;
  btn.textContent = "Saving…";

  const body = {
    admin_name:              "admin",
    type_check:              $("#ar-type-check").value,
    original_check:          $("#ar-original-check").value,
    outcome_check:           $("#ar-outcome-check").value,
    corrected_type:          $("#ar-type-check").value === "incorrect" ? $("#ar-corrected-type-sel").value : null,
    corrected_doi_o:         $("#ar-original-check").value === "incorrect" ? ($("#ar-corrected-doi").value.trim() || null) : null,
    corrected_study_o:       $("#ar-original-check").value === "incorrect" ? ($("#ar-corrected-study").value.trim() || null) : null,
    corrected_outcome:       $("#ar-outcome-check").value === "incorrect" ? $("#ar-corrected-outcome").value : null,
    corrected_outcome_quote: $("#ar-corrected-quote").value.trim() || null,
    admin_notes:             $("#ar-notes").value.trim() || null,
  };

  try {
    await adminApi(`/entries/${recordId}/resolve`, "POST", body);
    closeAdminDetail();
    showToast("Entry resolved.", 2500);
    fetchAdminEntries();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Mark as Resolved →";
    await showAlert("Error: " + e.message);
  }
}

function closeAdminDetail() {
  $("#admin-detail-modal").classList.add("hidden");
  document.body.style.overflow = "";
}

/* ---------- Admin tabs ---------- */
function switchAdminTab(tab) {
  $("#admin-tab-entries").classList.toggle("hidden", tab !== "entries");
  $("#admin-tab-stats").classList.toggle("hidden",   tab !== "stats");
  $("#admin-tab-admins").classList.toggle("hidden",  tab !== "admins");
  $("#admin-tabs").querySelectorAll(".admin-tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  if (tab === "stats")  fetchAdminStats();
  if (tab === "admins") fetchAdminAdmins();
}

async function fetchAdminStats() {
  const body = $("#admin-stats-body");
  body.innerHTML = '<tr><td colspan="9" class="admin-loading">Loading…</td></tr>';
  try {
    const data = await adminApi("/stats");
    renderAdminSummary(data.summary);
    renderAdminStats(data.validators);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9" class="admin-loading">Error: ${e.message}</td></tr>`;
  }
}

function renderAdminSummary(s) {
  $("#admin-summary-bar").innerHTML = `
    <div class="admin-summary-card"><span class="admin-summary-val">${s.total_validators}</span><span class="admin-summary-label">Active validators</span></div>
    <div class="admin-summary-card"><span class="admin-summary-val">${s.total_judgements}</span><span class="admin-summary-label">Total judgements</span></div>
    <div class="admin-summary-card"><span class="admin-summary-val">${s.total_validated}</span><span class="admin-summary-label">Validated entries</span></div>
    <div class="admin-summary-card"><span class="admin-summary-val">${s.total_review}</span><span class="admin-summary-label">Needs review</span></div>
  `;
}

function fmtMin(val) {
  if (val === null || val === undefined) return "—";
  return val < 1 ? `${Math.round(val * 60)}s` : `${val}m`;
}

function renderAdminStats(validators) {
  const body = $("#admin-stats-body");
  if (!validators.length) {
    body.innerHTML = "";
    $("#admin-stats-empty").classList.remove("hidden");
    return;
  }
  $("#admin-stats-empty").classList.add("hidden");
  body.innerHTML = validators.map((v, i) => `
    <tr>
      <td class="admin-cell-num">${i + 1}</td>
      <td><strong>${v.handle}</strong></td>
      <td>
        <button class="tier-cycle-btn" data-id="${v.id}" data-tier="${v.validator_tier}"
                title="${["Click to promote to Trusted","Click to promote to Senior","Click to reset to Regular"][v.validator_tier]}">
          ${["—","⭐ Trusted","★★ Senior"][v.validator_tier]}
        </button>
      </td>
      <td style="color:var(--muted);font-size:0.8rem">${v.joined || "—"}</td>
      <td>${v.total_judgements}</td>
      <td class="admin-time-cell">${fmtMin(v.avg_min)}</td>
      <td class="admin-time-cell">${fmtMin(v.median_min)}</td>
      <td class="admin-time-cell" style="color:var(--green)">${fmtMin(v.min_min)}</td>
      <td class="admin-time-cell" style="color:var(--muted)">${fmtMin(v.max_min)}</td>
    </tr>
  `).join("");

  const TIER_LABELS = ["—", "⭐ Trusted", "★★ Senior"];
  const TIER_TITLES = [
    "Click to promote to Trusted",
    "Click to promote to Senior",
    "Click to reset to Regular",
  ];

  body.querySelectorAll(".tier-cycle-btn").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      const next = (parseInt(btn.dataset.tier) + 1) % 3;
      try {
        const data = await adminApi(`/validators/${btn.dataset.id}/set-tier`, "POST", { tier: next });
        btn.dataset.tier = data.validator_tier;
        btn.textContent  = TIER_LABELS[data.validator_tier];
        btn.title        = TIER_TITLES[data.validator_tier];
      } catch (e) { await showAlert(e.message); }
      btn.disabled = false;
    };
  });
}

/* ---------- Admins management ---------- */
async function fetchAdminAdmins() {
  const list = $("#admin-admins-list");
  list.innerHTML = '<p class="admin-loading">Loading…</p>';
  try {
    const data = await adminApi("/admins");
    renderAdminAdmins(data.admins);
  } catch (e) {
    list.innerHTML = `<p class="admin-loading">Error: ${e.message}</p>`;
  }
}

function renderAdminAdmins(admins) {
  const list = $("#admin-admins-list");
  if (!admins.length) {
    list.innerHTML = '<p class="admin-loading">No admin accounts found.</p>';
    return;
  }
  list.innerHTML = `
    <table class="admin-table">
      <thead><tr><th>#</th><th>Handle</th><th>Trusted <span class="col-help" title="Trusted admins can add and remove other admin accounts.">?</span></th><th>Created</th><th></th></tr></thead>
      <tbody>
        ${admins.map((a, i) => {
          const isYou = a.handle === _adminHandle;
          const trustedCell = _adminTrusted && !isYou
            ? `<button class="trust-toggle-btn ${a.trusted ? "trusted" : ""} admin-toggle-trusted-btn"
                       data-id="${a.id}"
                       title="${a.trusted ? "Trusted — click to revoke" : "Click to mark as trusted"}">
                 ${a.trusted ? "⭐ Trusted" : "—"}
               </button>`
            : a.trusted ? '<span style="color:var(--muted);font-size:0.85rem">⭐ Trusted</span>' : '<span style="color:var(--muted)">—</span>';
          const actions = _adminTrusted && !isYou
            ? `<button class="ghost-btn admin-delete-admin-btn" data-id="${a.id}" style="color:var(--muted);font-size:0.8rem">Remove</button>`
            : "";
          return `<tr>
            <td class="admin-cell-num">${i + 1}</td>
            <td><strong>${a.handle}</strong>${isYou ? ' <span style="color:var(--muted);font-size:0.8rem">(you)</span>' : ""}</td>
            <td>${trustedCell}</td>
            <td style="color:var(--muted);font-size:0.8rem">${a.joined || "—"}</td>
            <td>${actions}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
  `;

  list.querySelectorAll(".admin-toggle-trusted-btn").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const data = await adminApi(`/admins/${btn.dataset.id}/toggle-trusted`, "POST");
        btn.classList.toggle("trusted", data.trusted);
        btn.textContent = data.trusted ? "⭐ Trusted" : "—";
        btn.title = data.trusted ? "Trusted — click to revoke" : "Click to mark as trusted";
      } catch (e) { await showAlert(e.message); }
      btn.disabled = false;
    };
  });

  list.querySelectorAll(".admin-delete-admin-btn").forEach((btn) => {
    btn.onclick = async () => {
      const confirmed = await showConfirm("Remove this admin account? They will no longer be able to sign in.");
      if (!confirmed) return;
      btn.disabled = true;
      try {
        await adminApi(`/admins/${btn.dataset.id}`, "DELETE");
        fetchAdminAdmins();
      } catch (e) {
        await showAlert("Error: " + e.message);
        btn.disabled = false;
      }
    };
  });

  // Show/hide add-admin form based on trusted status
  const addForm = $("#admin-add-form");
  if (addForm) addForm.style.display = _adminTrusted ? "" : "none";
  const noTrustMsg = $("#admin-no-trust-msg");
  if (noTrustMsg) noTrustMsg.style.display = _adminTrusted ? "none" : "";
}

async function addAdminAccount() {
  const handle   = $("#new-admin-handle").value.trim();
  const password = $("#new-admin-password").value.trim();
  if (!handle)   { await showAlert("Enter a handle for the new admin."); return; }
  if (!password) { await showAlert("Enter a password for the new admin."); return; }
  const btn = $("#add-admin-btn");
  btn.disabled = true;
  try {
    await adminApi("/admins", "POST", { handle, password });
    $("#new-admin-handle").value   = "";
    $("#new-admin-password").value = "";
    fetchAdminAdmins();
    showToast("Admin account created.", 2000);
  } catch (e) {
    await showAlert("Error: " + e.message);
  }
  btn.disabled = false;
}

$("#admin-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".admin-tab-btn");
  if (btn) switchAdminTab(btn.dataset.tab);
});

// Wire up admin screen events
$("#admin-logout-btn").onclick = signOutAdmin;
$("#admin-detail-close").onclick = closeAdminDetail;
$("#admin-detail-modal").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeAdminDetail();
});
$("#admin-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".admin-filter-btn");
  if (!btn) return;
  $("#admin-filters").querySelectorAll(".admin-filter-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  _adminFilter = btn.dataset.filter;
  _adminPage = 1;
  fetchAdminEntries();
});

/* ---------- Forgot handle ---------- */
function openForgotModal() {
  const loginEmail = $("#email-input")?.value.trim() || "";
  $("#forgot-email-input").value = loginEmail;
  $("#forgot-msg").textContent = "";
  $("#forgot-msg").style.color = "";
  $("#forgot-submit-btn").disabled = false;
  $("#forgot-submit-btn").textContent = "Send →";
  $("#forgot-modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  setTimeout(() => $("#forgot-email-input").focus(), 50);
}

function closeForgotModal() {
  $("#forgot-modal").classList.add("hidden");
  document.body.style.overflow = "";
}

async function submitForgotHandle() {
  const email = $("#forgot-email-input").value.trim();
  const msg   = $("#forgot-msg");
  if (!email || !email.includes("@")) {
    msg.style.color = "var(--red)";
    msg.textContent = "Please enter a valid email address.";
    return;
  }
  const btn = $("#forgot-submit-btn");
  btn.disabled = true;
  btn.textContent = "Sending…";
  msg.textContent = "";
  try {
    await api("/forgot-handle", "POST", { email });
    msg.style.color = "var(--green)";
    msg.textContent = "Sent! Check your inbox (and spam folder).";
    btn.textContent = "Sent ✓";
    setTimeout(closeForgotModal, 2500);
  } catch (e) {
    msg.style.color = "var(--red)";
    msg.textContent = e.message;
    btn.disabled = false;
    btn.textContent = "Send →";
  }
}

$("#forgot-handle-btn").onclick  = openForgotModal;
$("#forgot-close-btn").onclick   = closeForgotModal;
$("#forgot-cancel-btn").onclick  = closeForgotModal;
$("#forgot-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeForgotModal(); });
$("#forgot-submit-btn").onclick  = submitForgotHandle;
$("#forgot-email-input").addEventListener("keydown", (e) => { if (e.key === "Enter") submitForgotHandle(); });
