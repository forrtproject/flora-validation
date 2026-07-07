const state = {
  coder: null,
  currentPair: null,
  judgement: blankJudgement(),
  mode: "normal",          // "normal" | "hard"
  assignment: null,        // record_id when validating an assignment, else null
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
    repro_computation: null,   // reproduction outcome axis 1
    repro_robustness: null,    // reproduction outcome axis 2
    corrected_doi_o: null,
    corrected_study_o: null,
    corrected_study_r: null,
    corrected_url_r: null,
    comment: "",
    edited_abstract: null,
    edited_outcome_quote: null,
    no_access: false,          // hard mode: "I cannot access this article"
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
let _chipTimer = null;

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

  const res = await fetch("/api" + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    let msg = res.statusText || `Server error (HTTP ${res.status})`;
    if (err.detail) {
      if (Array.isArray(err.detail)) {
        msg = err.detail.map((e) => e.msg || JSON.stringify(e)).join("; ");
      } else {
        msg = String(err.detail);
      }
    }
    const httpErr = new Error(msg);
    httpErr.status = res.status;
    throw httpErr;
  }
  return res.json();
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
      coders[key] = { coder_id: id, code: code || null, email: email || null, handle, onboarded: false, last_seen_update: 0 };
      writeJSON(STORAGE.CODERS, coders);
    }
    return { ...coders[key], update_version: 1, last_seen_update: coders[key].last_seen_update ?? 0 };
  }

  if (route === "/onboarding") {
    return { pairs: STATIC_DATA.onboarding };
  }

  if (route === "/onboarding/complete") {
    const cid = body.coder_id;
    for (const k of Object.keys(coders)) {
      if (coders[k].coder_id === cid) {
        coders[k].onboarded = true;
        coders[k].last_seen_update = 1; // treated as up-to-date after onboarding
      }
    }
    writeJSON(STORAGE.CODERS, coders);
    return { onboarded: true };
  }

  if (route === "/update-seen") {
    const cid = body.coder_id;
    for (const k of Object.keys(coders)) {
      if (coders[k].coder_id === cid) coders[k].last_seen_update = 1;
    }
    writeJSON(STORAGE.CODERS, coders);
    return { ok: true };
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

  if (route === "/my-judgements") {
    const cid = +params.get("coder_id");
    const judgements = readJSON(STORAGE.JUDGEMENTS, [])
      .filter(j => j.coder_id === cid && j.type_judgement !== "skip")
      .reverse()
      .slice(0, 100)
      .map(j => ({
        queue_id: j.pair_id,
        record_id: j.pair_id,
        type_check: j.type_check || null,
        original_check: j.original_check || null,
        outcome_check: j.outcome_check || null,
        corrected_doi_o: j.corrected_doi_o || null,
        corrected_study_o: j.corrected_study_o || null,
        corrected_outcome: j.corrected_outcome || null,
        corrected_type: j.corrected_type || null,
        corrected_study_r: j.corrected_study_r || null,
        points: j.points || 0,
        validated_at: j.created_at || null,
        flagged: false,
        flag_reason: null,
        title_r: j.study_r || j.pair_id,
        doi_r: j.doi_r || null,
        year_r: j.year_r || null,
        extracted_outcome: j.outcome || null,
        msg_id: null,
        msg_body: null,
        msg_sent_at: null,
        msg_is_read: null,
      }));
    return { judgements };
  }

  // Detail: /my-judgements/<queue_id>
  if (route.startsWith("/my-judgements/")) {
    const queueId = route.split("/my-judgements/")[1];
    const cid = +params.get("coder_id");
    const j = readJSON(STORAGE.JUDGEMENTS, []).find(x => x.pair_id === queueId && x.coder_id === cid);
    if (!j) throw new Error("Judgement not found");
    return {
      queue_id: j.pair_id,
      record_id: j.pair_id,
      validator_slot: "human_1",
      type_check: j.type_check || null,
      original_check: j.original_check || null,
      outcome_check: j.outcome_check || null,
      corrected_doi_o: j.corrected_doi_o || null,
      corrected_study_o: j.corrected_study_o || null,
      corrected_outcome: j.corrected_outcome || null,
      corrected_outcome_quote: j.corrected_outcome_quote || null,
      corrected_abstract: j.corrected_abstract || null,
      corrected_type: j.corrected_type || null,
      corrected_study_r: j.corrected_study_r || null,
      additional_checks: j.additional_checks || null,
      validator_notes: j.validator_notes || null,
      points: j.points || 0,
      validated_at: j.created_at || null,
      flagged: false,
      flag_reason: null,
      study_r: j.study_r || j.pair_id,
      doi_r: j.doi_r || null,
      year_r: j.year_r || null,
      abstract_r: j.abstract_r || null,
      doi_o: j.doi_o || null,
      study_o: j.study_o || null,
      year_o: j.year_o || null,
      extracted_type: j.type || null,
      extracted_outcome: j.outcome || null,
      outcome_quote: j.outcome_quote || null,
      has_validated: false,
      val_study_r: null, val_doi_r: null, val_year_r: null, val_abstract_r: null,
      val_doi_o: null, val_study_o: null, val_year_o: null,
      val_type: null, val_outcome: null, val_outcome_quote: null,
      val_admin_approved: false, val_validated_at: null,
      messages: [],
    };
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

// Pretty display label for an outcome value. Keeps CSS class names raw (use the
// original value for those); this is for the visible text only. Unknown values
// (e.g. reproduction combined labels) pass through unchanged.
function fmtOutcome(v) {
  if (!v) return v;
  return { cannot_be_determined: "Cannot be determined" }[String(v).toLowerCase()] || v;
}

// "Hamid", "Hamid and Luke", "Hamid, Luke, and the LLM"
function _nameList(names) {
  if (!names.length) return "";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

// Plain-language summary of where the validators (+ LLM) agreed / disagreed.
// Returns an array of sentences for the admin detail view.
function buildConsensusSummary(v1, v2, llm) {
  const voters = [];
  if (v1) voters.push({ name: v1.validator_name || "Validator 1", j: v1 });
  if (v2) voters.push({ name: v2.validator_name || "Validator 2", j: v2 });
  if (llm && !llm.error) voters.push({ name: "the LLM", j: llm });
  if (!voters.length) return [];

  const groupBy = (fn) => {
    const m = new Map();
    for (const vt of voters) {
      const s = fn(vt.j);
      if (s == null) continue;
      if (!m.has(s)) m.set(s, []);
      m.get(s).push(vt.name);
    }
    return m;
  };
  const summarize = (m, phrase) => {
    const e = [...m.entries()];
    if (!e.length) return null;
    if (e.length === 1) {
      const [s, ns] = e[0];
      return `${_nameList(ns)} ${ns.length > 1 ? "agree" : "says"} ${phrase(s)}.`;
    }
    return e.map(([s, ns]) => `${_nameList(ns)} ${ns.length > 1 ? "say" : "says"} ${phrase(s)}`).join("; ") + ".";
  };

  const lines = [];

  const typeS = summarize(
    groupBy(j => j.type_check === "correct" ? "ok" : (j.corrected_type || "incorrect")),
    s => s === "ok" ? "the type is correct"
       : s === "incorrect" ? "the type is wrong"
       : s === "not_validation" ? "it's not a validation"
       : `the type should be ${s}`
  );
  if (typeS) lines.push(typeS);

  const origS = summarize(
    groupBy(j => j.original_check === "correct" ? "right" : j.original_check === "incorrect" ? "wrong" : null),
    s => `the original is the ${s} paper`
  );
  if (origS) lines.push(origS);

  const outS = summarize(
    groupBy(j => j.outcome_check === "correct" ? "ok" : (j.corrected_outcome || "incorrect")),
    s => s === "ok" ? "the outcome is correct"
       : s === "incorrect" ? "the outcome is wrong"
       : `the outcome should be ${fmtOutcome(s)}`
  );
  if (outS) lines.push(outS);

  // Who edited free-text fields.
  const editors = (key) => voters.filter(vt => vt.j[key]).map(vt => vt.name);
  let e;
  if ((e = editors("corrected_outcome_quote")).length) lines.push(`${_nameList(e)} changed the outcome quote.`);
  if ((e = editors("corrected_abstract")).length)      lines.push(`${_nameList(e)} edited the abstract.`);
  if ((e = editors("corrected_study_r")).length)        lines.push(`${_nameList(e)} fixed the replication title.`);
  if ((e = editors("corrected_url_r")).length)          lines.push(`${_nameList(e)} suggested a replication link.`);

  return lines;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function showToast(numOrMsg, label) {
  const toast = $("#toast");
  if (label === undefined) {
    toast.textContent = numOrMsg;
  } else {
    toast.innerHTML = `<span class="num">+${escapeHtml(String(numOrMsg))}</span> ${escapeHtml(String(label))}`;
  }
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1800);
}

/* ---------- Auth ---------- */
let loginMode = "email";
document.addEventListener("DOMContentLoaded", () => {
  $("#toggle-label-email").classList.add("toggle-active");
  if (sessionStorage.getItem("flora.idleNotice")) {
    sessionStorage.removeItem("flora.idleNotice");
    setTimeout(() => showToast("Signed out after 30 minutes of inactivity."), 400);
  }
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
        rememberLogin(handle, fieldVal);   // remember username + password (stored in plaintext)
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
    if (code.length < 8) {
      await showAlert("Please fill in all four parts of your code.");
      return;
    }
    body = { handle, code };
  }
  try {
    const resp = await api("/login", "POST", body);
    rememberLogin(handle, body.email || "");   // pre-fill next time (never the code)
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

// Remember the last username/email on this device so the login fields can be
// pre-filled next time (Option B). The secret code and admin password are never stored.
const LAST_LOGIN_KEY = "flora.lastLogin";
function rememberLogin(handle, email) {
  try {
    localStorage.setItem(LAST_LOGIN_KEY, JSON.stringify({ handle: handle || "", email: email || "" }));
  } catch {}
}
function prefillLogin() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAST_LOGIN_KEY) || "null");
    if (!saved) return;
    const h = $("#handle-input"), e = $("#email-input");
    if (h && saved.handle) h.value = saved.handle;
    if (e && saved.email)  e.value = saved.email;
  } catch {}
}

async function startup() {
  await detectMode();
  prefillLogin();
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
  } else if ((state.coder.last_seen_update ?? 0) < (state.coder.update_version ?? 0)) {
    enterUpdateScreen();
  } else {
    enterGame();
  }
}

function clearSession() {
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
$("#update-logout-btn").onclick = logout;

/* ---------- Inactivity auto-logout (30 min, warn at 25, wall-clock, cross-tab) ----------
   "Last activity" is a localStorage timestamp updated (throttled) by user input,
   shared across tabs. We compare elapsed real time — robust to background-tab
   timer throttling and laptop sleep. On expiry: clear session → redirect to login. */
const _IDLE_LIMIT_MS  = 30 * 60 * 1000;
const _IDLE_WARN_MS   = 25 * 60 * 1000;
const _IDLE_ACT_KEY   = "flora.lastActivity";
const _IDLE_OUT_KEY   = "flora.idleLogout";
const _IDLE_EVENTS    = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"];
let _idleActive = false;
let _idleCheckTimer = null;
let _idleCountdownTimer = null;
let _idleLastWrite = 0;

function _idleGetLast() {
  return parseInt(localStorage.getItem(_IDLE_ACT_KEY) || "0", 10) || Date.now();
}
function _idleTouch() {                    // record activity (throttled)
  const now = Date.now();
  if (now - _idleLastWrite < 3000) return;
  _idleLastWrite = now;
  localStorage.setItem(_IDLE_ACT_KEY, String(now));
  _idleHideWarning();
}
function _idleOnStorage(e) {
  if (e.key === _IDLE_ACT_KEY) _idleCheck();          // another tab active → sync
  else if (e.key === _IDLE_OUT_KEY) _idleRedirect();  // another tab logged out → follow
}
function startIdleLogout() {
  if (_idleActive) return;
  _idleActive = true;
  localStorage.setItem(_IDLE_ACT_KEY, String(Date.now()));   // reset on entry
  _idleEvents("add");
  document.addEventListener("visibilitychange", _idleCheck);
  window.addEventListener("storage", _idleOnStorage);
  clearInterval(_idleCheckTimer);
  _idleCheckTimer = setInterval(_idleCheck, 20_000);
}
function _idleEvents(mode) {
  const fn = mode === "add" ? "addEventListener" : "removeEventListener";
  _IDLE_EVENTS.forEach(ev => document[fn](ev, _idleTouch, { passive: true, capture: true }));
}
function _idleCheck() {
  if (!_idleActive) return;
  const elapsed = Date.now() - _idleGetLast();
  if (elapsed >= _IDLE_LIMIT_MS) _idleLogout();
  else if (elapsed >= _IDLE_WARN_MS) _idleShowWarning();
  else _idleHideWarning();
}
function _idleShowWarning() {
  const modal = $("#idle-warn-modal");
  if (!modal || !modal.classList.contains("hidden")) return;   // already shown
  modal.classList.remove("hidden");
  clearInterval(_idleCountdownTimer);
  const tick = () => {
    const remaining = _IDLE_LIMIT_MS - (Date.now() - _idleGetLast());
    if (remaining <= 0) return _idleLogout();
    if (remaining > _IDLE_LIMIT_MS - _IDLE_WARN_MS) return _idleHideWarning(); // became active elsewhere
    const s = Math.ceil(remaining / 1000);
    const el = $("#idle-countdown");
    if (el) el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  tick();
  _idleCountdownTimer = setInterval(tick, 1000);
}
function _idleHideWarning() {
  $("#idle-warn-modal")?.classList.add("hidden");
  clearInterval(_idleCountdownTimer);
  _idleCountdownTimer = null;
}
function _idleStay() {
  _idleLastWrite = Date.now();
  localStorage.setItem(_IDLE_ACT_KEY, String(Date.now()));
  _idleHideWarning();
}
function _idleLogout() {
  if (!_idleActive) return;
  _idleActive = false;
  clearInterval(_idleCheckTimer);
  _idleEvents("remove");
  try {
    sessionStorage.setItem("flora.idleNotice", "1");
    localStorage.setItem(_IDLE_OUT_KEY, String(Date.now()));   // signal other tabs
  } catch (_) {}
  _idleRedirect();
}
function _idleRedirect() {
  clearSession();      // validator session; admin token is in-memory and dies on reload
  location.reload();   // → login / home
}
$("#idle-stay-btn")?.addEventListener("click", _idleStay);

/* ---------- Onboarding ---------- */
async function enterOnboarding() {
  $("#onboarding-screen").classList.remove("hidden");
  startIdleLogout();
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

/* ---------- Update screen ---------- */
function enterUpdateScreen() {
  $("#update-screen").classList.remove("hidden");
  startIdleLogout();
  fetch("./updates.json")
    .then(r => r.json())
    .then(data => {
      const cards = data.cards || [];
      $("#update-cards").innerHTML = cards.map(c => `
        <div class="update-card">
          <div class="update-card-icon-wrap">${c.icon}</div>
          <div class="update-card-content">
            <div class="update-card-title">${c.title}</div>
            <div class="update-card-body">${c.body}</div>
          </div>
        </div>
      `).join("");
    })
    .catch(() => {});
}

$("#update-continue-btn").onclick = async () => {
  try {
    await api("/update-seen", "POST", { coder_id: state.coder.coder_id });
  } catch {}
  state.coder.last_seen_update = state.coder.update_version ?? 0;
  localStorage.setItem(STORAGE.CODER, JSON.stringify(state.coder));
  $("#update-screen").classList.add("hidden");
  enterGame();
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
  } else if (exp.type === "reproduction") {
    if (j.type !== "reproduction") {
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

  // Title typo check — validator must use the fix-typo button when the title has a known error
  if (exp.study_r_typo && j.type !== "not_validation" && !j.corrected_study_r) {
    errors.push({ key: "study_r_typo_missed", text: pair.feedback.study_r_typo_missed });
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
  $("#mode-toggle").classList.remove("hidden");   // normal / hard mode switch
  startIdleLogout();
  startMaintenanceSystem();
  startKeepWarm();             // ping /api/health so the instance doesn't sleep mid-session
  initInbox();
  initHistory();
  _updatePendingIndicator();   // reflect any submits left over from a prior session
  _processSubmitQueue();       // resume sending them
  refreshAssignments();        // show the Assignments button if any are open
  startAssignmentsPoll();      // near-real-time updates while the app is open
  await refreshAll();
  fetchMessages(); // after refreshAll so resume dialog (if any) fires first
}

/* ---------- Assignments (restricted records handed to this validator) ---------- */
let _assignments = [];
let _assignmentsPollTimer = null;
let _assignmentsLoaded = false;
let _lastAssignmentCount = 0;

async function refreshAssignments() {
  if (API_MODE === "static" || !state.coder) return;
  try {
    const data = await api(`/my-assignments?coder_id=${state.coder.coder_id}`);
    _assignments = data.assignments || [];
  } catch (_) { return; }   // keep prior state on a transient failure
  const btn = $("#assignments-btn");
  const cnt = $("#assignments-count");
  if (cnt) cnt.textContent = _assignments.length;
  if (btn) btn.classList.toggle("hidden", _assignments.length === 0);

  // Toast when a new assignment arrives (skip the first load of the session).
  const grew = _assignmentsLoaded && _assignments.length > _lastAssignmentCount;
  _lastAssignmentCount = _assignments.length;
  _assignmentsLoaded = true;
  if (grew) showToast("📋 New assignment from the review team.");

  // If the panel is open, keep its list in sync.
  if (!$("#assignments-modal")?.classList.contains("hidden")) _renderAssignmentsList();
}

// Near-real-time: poll every 30s while the app is open (matches the inbox poll),
// so an admin's assignment surfaces without a page reload.
function startAssignmentsPoll() {
  if (API_MODE === "static") return;
  clearInterval(_assignmentsPollTimer);
  _assignmentsPollTimer = setInterval(() => { if (state.coder) refreshAssignments(); }, 30_000);
}

function _renderAssignmentsList() {
  const body = $("#assignments-body");
  if (!body) return;
  if (!_assignments.length) {
    body.innerHTML = `<p class="hist-empty">No open assignments.</p>`;
    return;
  }
  body.innerHTML = _assignments.map(a => `
    <div class="assign-item" data-record="${escapeHtml(a.record_id)}" role="button" tabindex="0">
      <div class="assign-item-main">
        <div class="assign-item-title">${escapeHtml((a.study_r || a.doi_r || a.record_id).slice(0, 80))}</div>
        <div class="assign-item-sub">${a.doi_r ? escapeHtml(a.doi_r) : "—"}${a.year_r ? " · " + fmtYear(a.year_r) : ""} · ${escapeHtml(fmtOutcome(a.outcome) || "—")}</div>
      </div>
      <span class="assign-item-open">Open →</span>
    </div>`).join("");
  body.querySelectorAll(".assign-item").forEach(el => {
    const open = () => openAssignment(el.dataset.record);
    el.addEventListener("click", open);
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") open(); });
  });
}

function openAssignmentsPanel() {
  const modal = $("#assignments-modal");
  if (!modal) return;
  _renderAssignmentsList();
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeAssignmentsPanel() {
  $("#assignments-modal")?.classList.add("hidden");
  document.body.style.overflow = "";
}

async function openAssignment(recordId) {
  try {
    const resp = await api(`/assignment/${recordId}?coder_id=${state.coder.coder_id}`);
    if (!resp.pair) throw new Error("Could not load assignment.");
    closeAssignmentsPanel();
    state.assignment = recordId;
    state.currentPair = resp.pair;
    state.judgement = blankJudgement();
    clearPairTimer();
    $("#done-screen").classList.add("hidden");
    $("#assignment-banner").classList.remove("hidden");
    $("#pair-card").classList.remove("hidden");
    renderPairInto($("#pair-card"), resp.pair, { onboarding: false, judgeCount: resp.pair.judge_count });
    applySplitLayout();
  } catch (e) {
    await showAlert(e.message);
  }
}

function exitAssignment() {
  state.assignment = null;
  $("#assignment-banner").classList.add("hidden");
  loadNextPair();   // back to the normal/hard queue
}

$("#assignments-btn")?.addEventListener("click", openAssignmentsPanel);
$("#assignments-close-btn")?.addEventListener("click", closeAssignmentsPanel);
$("#assignments-modal")?.addEventListener("click", (e) => {
  if (e.target === $("#assignments-modal")) closeAssignmentsPanel();
});
$("#assignment-exit-btn")?.addEventListener("click", exitAssignment);

/* Keep-warm: ping the lightweight /api/health every 10 min while the app is open
   so a free-tier host doesn't sleep mid-session (under the typical ~15-min idle
   threshold). Raw fetch — bypasses api() so it never shows the "waking up" toast. */
let _keepWarmTimer = null;
function startKeepWarm() {
  if (API_MODE === "static") return;   // no backend in the static demo
  clearInterval(_keepWarmTimer);
  _keepWarmTimer = setInterval(() => {
    fetch("/api/health", { method: "GET", cache: "no-store" }).catch(() => {});
  }, 10 * 60 * 1000);
}

/* ============================================================
   Validator Inbox
   ============================================================ */

let _inboxMessages = [];

function initInbox() {
  $("#inbox-btn")?.addEventListener("click", openInbox);
  $("#inbox-close-btn")?.addEventListener("click", closeInbox);
  $("#inbox-modal")?.addEventListener("click", e => {
    if (e.target === $("#inbox-modal")) closeInbox();
  });
}

async function fetchMessages() {
  if (!state.coder) return;
  try {
    const resp = await api(`/messages?coder_id=${state.coder.coder_id}`);
    _inboxMessages = resp.messages || [];
    _updateInboxBadge();
    const unread = _inboxMessages.filter(m => m.direction !== "inbound" && !m.is_read).length;
    if (unread > 0) {
      const v = await showDialog({
        icon: "✉",
        title: "You have new messages",
        message: `You have ${unread} unread message${unread > 1 ? "s" : ""} in your inbox.`,
        buttons: [
          { label: "Open Inbox", value: "open", primary: true },
          { label: "Later", value: "later" },
        ],
      });
      if (v === "open") openInbox();
    }
  } catch {}
}

function _updateInboxBadge() {
  const badge = $("#inbox-badge");
  if (!badge) return;
  const unread = _inboxMessages.filter(m => m.direction !== "inbound" && !m.is_read).length;
  if (unread > 0) {
    badge.textContent = unread > 99 ? "99+" : String(unread);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

let _inboxPollTimer = null;

function openInbox() {
  const modal = $("#inbox-modal");
  if (!modal) return;
  clearInterval(_inboxPollTimer);
  renderInbox();
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  // Poll for new messages every 30s while inbox is open
  _inboxPollTimer = setInterval(async () => {
    if ($("#inbox-modal")?.classList.contains("hidden")) return;
    try {
      const r = await api(`/messages?coder_id=${state.coder.coder_id}`);
      const prev = _inboxMessages.length;
      _inboxMessages = r.messages || [];
      _updateInboxBadge();
      // Only re-render list if we're on the list view (not inside a thread)
      const body = $("#inbox-body");
      if (body && !body.querySelector(".inbox-thread-view")) {
        const hadMore = _inboxMessages.length > prev;
        if (hadMore) renderInbox();
      }
    } catch (_) {}
  }, 30000);
}

function closeInbox() {
  $("#inbox-modal")?.classList.add("hidden");
  document.body.style.overflow = "";
  clearInterval(_inboxPollTimer);
  _inboxPollTimer = null;
}

function _buildConvMap() {
  const convMap = new Map();
  _inboxMessages.forEach(m => {
    const key = m.queue_id || `msg-${m.parent_id || m.id}`;
    if (!convMap.has(key)) {
      const root = m.direction === "outbound" ? m : _inboxMessages.find(x => x.id === m.parent_id) || m;
      convMap.set(key, {
        queueId:   m.queue_id || null,
        rootMsgId: root.id,
        subject:   root.subject,
        messages:  [],
        hasUnread: false,
        latestDate: null,
      });
    }
    const conv = convMap.get(key);
    conv.messages.push(m);
    if (!m.is_read && m.direction !== "inbound") conv.hasUnread = true;
    const d = new Date(m.sent_at);
    if (!conv.latestDate || d > conv.latestDate) conv.latestDate = d;
  });
  return convMap;
}

function renderInbox() {
  const body = $("#inbox-body");
  if (!body) return;
  if (_inboxMessages.length === 0) {
    body.innerHTML = `<p class="inbox-empty">No messages yet.</p>`;
    return;
  }

  const convMap = _buildConvMap();
  const convs   = [...convMap.values()];

  const fmtDate = d => d ? d.toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }) : "";

  body.innerHTML = convs.map((conv, idx) => {
    const num        = idx + 1;
    const unreadBadge = conv.hasUnread ? `<span class="inbox-unread-badge">UNREAD</span>` : "";
    return `<div class="inbox-conv${conv.hasUnread ? " inbox-conv-unread" : ""}" data-idx="${idx}">
      <div class="inbox-conv-row">
        <span class="inbox-conv-num">#${num}</span>
        <span class="inbox-conv-subject">${escapeHtml(conv.subject)}</span>
        ${unreadBadge}
        <span class="inbox-conv-date">${fmtDate(conv.latestDate)}</span>
      </div>
    </div>`;
  }).join("");

  // Wire conversation click → mark read + show thread
  body.querySelectorAll(".inbox-conv").forEach(el => {
    el.addEventListener("click", () => {
      const idx  = +el.dataset.idx;
      const conv = convs[idx];
      // Mark this conversation's unread messages as read
      conv.messages.forEach(m => {
        if (!m.is_read && m.direction !== "inbound") {
          api(`/messages/${m.id}/read?coder_id=${state.coder.coder_id}`, "POST").catch(() => {});
          m.is_read = true;
        }
      });
      conv.hasUnread = false;
      _updateInboxBadge();
      _renderInboxThread(body, conv, idx + 1);
    });
  });
}

function _renderInboxThread(body, conv, num) {
  const fmtDate = d => new Date(d).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });

  const bubbles = conv.messages.map(m => {
    const isTeam = m.direction === "outbound";
    let msgBody = m.body;
    if (isTeam) {
      const idx = msgBody.indexOf("Reason:");
      if (idx !== -1) msgBody = msgBody.slice(idx + "Reason:".length).trim();
    }
    return `<div class="inbox-bubble ${isTeam ? "inbox-bubble-team" : "inbox-bubble-you"}">
      <p class="inbox-bubble-body">${escapeHtml(msgBody).replace(/\n/g, "<br>")}</p>
      <div class="inbox-bubble-meta">
        <span class="inbox-bubble-who">${isTeam ? "Review team" : "You"}</span>
        <span class="inbox-bubble-time">${fmtDate(m.sent_at)}</span>
      </div>
    </div>`;
  }).join("");

  const viewBtn = conv.queueId
    ? `<button class="inbox-view-judgement-btn ghost-btn" data-queue-id="${conv.queueId}">View judgement →</button>`
    : "";

  body.innerHTML = `
    <div class="inbox-thread-view">
      <div class="inbox-thread-topbar">
        <button class="inbox-back-btn ghost-btn">← Back</button>
        <span class="inbox-thread-title">Conversation #${num}</span>
        ${viewBtn}
      </div>
      <div class="inbox-thread-bubbles" id="inbox-thread-bubbles">
        ${bubbles}
      </div>
      <div class="inbox-thread-input-row" data-root-msg-id="${conv.rootMsgId}">
        <textarea class="inbox-thread-textarea" placeholder="Write a reply…" rows="1"></textarea>
        <button class="inbox-thread-send btn-primary">Send</button>
      </div>
    </div>
  `;

  // Back button — refresh messages then re-render list
  body.querySelector(".inbox-back-btn").addEventListener("click", async () => {
    try {
      const r = await api(`/messages?coder_id=${state.coder.coder_id}`);
      _inboxMessages = r.messages || [];
      _updateInboxBadge();
    } catch (_) {}
    renderInbox();
  });

  // View judgement button — opens detail on top without closing inbox
  body.querySelector(".inbox-view-judgement-btn")?.addEventListener("click", () => {
    openHistDetail(conv.queueId);
  });

  // Auto-grow textarea
  const textarea = body.querySelector(".inbox-thread-textarea");
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
  });

  // Send reply
  const sendBtn = body.querySelector(".inbox-thread-send");
  const inputRow = body.querySelector(".inbox-thread-input-row");
  const threadBubbles = body.querySelector("#inbox-thread-bubbles");
  const rootMsgId = +inputRow.dataset.rootMsgId;

  const doSend = async () => {
    const text = textarea.value.trim();
    if (!text) return;
    sendBtn.disabled = true;
    try {
      await api(`/messages/${rootMsgId}/reply`, "POST", { coder_id: state.coder.coder_id, body: text });
      textarea.value = "";
      textarea.style.height = "auto";
      const bubble = document.createElement("div");
      bubble.className = "inbox-bubble inbox-bubble-you";
      bubble.innerHTML = `
        <p class="inbox-bubble-body">${escapeHtml(text).replace(/\n/g, "<br>")}</p>
        <div class="inbox-bubble-meta">
          <span class="inbox-bubble-who">You</span>
          <span class="inbox-bubble-time">just now</span>
        </div>`;
      threadBubbles.appendChild(bubble);
      threadBubbles.scrollTop = threadBubbles.scrollHeight;
    } catch (e) {
      await showAlert("Failed to send: " + e.message);
    }
    sendBtn.disabled = false;
  };

  sendBtn.addEventListener("click", doSend);
  textarea.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) doSend(); });

  // Scroll to bottom
  threadBubbles.scrollTop = threadBubbles.scrollHeight;
}

/* ============================================================
   My Judgements History
   ============================================================ */

let _histJudgements = [];

function initHistory() {
  $("#history-btn")?.addEventListener("click", openHistory);
  $("#history-close-btn")?.addEventListener("click", closeHistory);
  $("#history-modal")?.addEventListener("click", e => {
    if (e.target === $("#history-modal")) closeHistory();
  });
  $("#hist-detail-close-btn")?.addEventListener("click", closeHistDetail);
  $("#hist-detail-back-btn")?.addEventListener("click", closeHistDetail);
  $("#hist-detail-modal")?.addEventListener("click", e => {
    if (e.target === $("#hist-detail-modal")) closeHistDetail();
  });
}

async function openHistory() {
  const modal = $("#history-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  const body = $("#history-body");
  body.innerHTML = `<p class="faq-loading">Loading…</p>`;
  try {
    const resp = await api(`/my-judgements?coder_id=${state.coder.coder_id}`);
    _histJudgements = resp.judgements || [];
    renderHistory();
  } catch (e) {
    body.innerHTML = `<p class="faq-error">Could not load history (${escapeHtml(e.message)}).</p>`;
  }
}

function closeHistory() {
  $("#history-modal")?.classList.add("hidden");
  document.body.style.overflow = "";
}

function _histCheckChip(value, label) {
  if (!value) return `<span class="hist-check unsure" title="${label}">?</span>`;
  return value === "correct"
    ? `<span class="hist-check correct" title="${label}">✓</span>`
    : `<span class="hist-check incorrect" title="${label}">✗</span>`;
}

// Maps a record's validation_status → label + colour class for the validator's
// own "My Judgements" view. Covers every lifecycle stage the validator can land in.
const _HIST_STATUS = {
  unvalidated:           { text: "Awaiting second validator", short: "Pending 2nd",      cls: "hd-status-pending"   },
  validation_inprogress: { text: "Awaiting second validator", short: "Pending 2nd",      cls: "hd-status-pending"   },
  consensus_reached:     { text: "Pending approval",          short: "Pending approval", cls: "hd-status-validated" },
  need_review:           { text: "Under review",              short: "In review",        cls: "hd-status-review"    },
  validated:             { text: "Approved",                  short: "Approved",         cls: "hd-status-approved"  },
  rejected:              { text: "Excluded",                  short: "Excluded",         cls: "hd-status-rejected"  },
};
function _histStatusBadge(status, { compact = false } = {}) {
  const s = _HIST_STATUS[status] || _HIST_STATUS.validation_inprogress;
  const label = compact ? s.short : s.text;
  return `<span class="hd-status-badge${compact ? " hist-status-compact" : ""} ${s.cls}">${label}</span>`;
}

function renderHistory() {
  const body = $("#history-body");
  if (!body) return;
  if (_histJudgements.length === 0) {
    body.innerHTML = `<p class="hist-empty">No completed judgements yet.</p>`;
    return;
  }
  // Sort newest first
  const sorted = [..._histJudgements].sort((a, b) => {
    if (!a.validated_at && !b.validated_at) return 0;
    if (!a.validated_at) return 1;
    if (!b.validated_at) return -1;
    return new Date(b.validated_at) - new Date(a.validated_at);
  });

  body.innerHTML = sorted.map((j, idx) => {
    const num   = sorted.length - idx;  // newest = highest number
    const title = escapeHtml(j.title_r || j.doi_r || "Unknown record");
    const year  = j.year_r ? ` (${fmtYear(j.year_r)})` : "";
    const date  = j.validated_at ? _fmtRelTime(new Date(j.validated_at)) : "";
    const pts   = j.points != null ? `+${j.points} pts` : "";

    const chips = [
      _histCheckChip(j.type_check, "Study type"),
      _histCheckChip(j.original_check, "Original study"),
      _histCheckChip(j.outcome_check, "Outcome"),
    ].join("");

    const corrections = [];
    if (j.corrected_study_r) corrections.push(`Title correction: <em>${escapeHtml(j.corrected_study_r)}</em>`);
    if (j.corrected_type)    corrections.push(`Type → <em>${escapeHtml(j.corrected_type)}</em>`);
    if (j.corrected_outcome) corrections.push(`Outcome → <em>${escapeHtml(fmtOutcome(j.corrected_outcome))}</em>`);
    if (j.corrected_study_o) corrections.push(`Original title → <em>${escapeHtml(j.corrected_study_o)}</em>`);
    if (j.corrected_doi_o)   corrections.push(`Original DOI → <em>${escapeHtml(j.corrected_doi_o)}</em>`);
    const corrHtml = corrections.length
      ? `<div class="hist-corrections">${corrections.join(" · ")}</div>`
      : "";

    const flagHtml = j.flagged
      ? `<div class="hist-flag-bar">
           <span class="hist-flag-label">⚑ Flagged for review</span>
           ${j.flag_reason ? `<span class="hist-flag-reason">${escapeHtml(j.flag_reason)}</span>` : ""}
         </div>`
      : "";

    const msgHtml = j.msg_body
      ? `<div class="hist-msg ${j.msg_is_read === false ? "hist-msg-unread" : ""}">
           <span class="hist-msg-label">Message from team</span>
           <span class="hist-msg-body">${escapeHtml(j.msg_body)}</span>
         </div>`
      : "";

    return `
      <div class="hist-item ${j.flagged ? "hist-item-flagged" : ""}" role="button" tabindex="0"
           data-queue-id="${escapeHtml(j.queue_id)}" style="cursor:pointer">
        <div class="hist-item-top">
          <div class="hist-item-title">
            <span class="hist-item-num">#${num}</span>${title}${year}
          </div>
          <div class="hist-item-meta">
            ${j.validation_status ? _histStatusBadge(j.validation_status, { compact: true }) : ""}
            ${pts ? `<span class="hist-item-pts">${pts}</span>` : ""}
            ${date ? `<span class="hist-item-date">${date}</span>` : ""}
            <span class="hist-item-arrow">›</span>
          </div>
        </div>
        <div class="hist-chips">${chips}</div>
        ${corrHtml}
        ${flagHtml}
        ${msgHtml}
      </div>
    `;
  }).join("");

  body.querySelectorAll(".hist-item[data-queue-id]").forEach(el => {
    const open = () => openHistDetail(el.dataset.queueId);
    el.addEventListener("click", open);
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") open(); });
  });
}

/* ============================================================
   My Judgements Detail
   ============================================================ */

const _histDetailCache = {};

async function openHistDetail(queueId) {
  const modal = $("#hist-detail-modal");
  if (!modal) return;
  const body = $("#hist-detail-body");
  body.innerHTML = `<p class="faq-loading">Loading…</p>`;
  modal.classList.remove("hidden");

  if (_histDetailCache[queueId]) {
    renderHistDetail(_histDetailCache[queueId]);
    return;
  }
  try {
    const data = await api(`/my-judgements/${queueId}?coder_id=${state.coder.coder_id}`);
    _histDetailCache[queueId] = data;
    renderHistDetail(data);
  } catch (e) {
    body.innerHTML = `<p class="faq-error">Could not load record (${escapeHtml(e.message)}).</p>`;
  }
}

function closeHistDetail() {
  $("#hist-detail-modal")?.classList.add("hidden");
}

function _detailCheckRow(label, extracted, checkVal, corrected) {
  const statusIcon = checkVal === "correct"
    ? `<span class="hd-chk hd-chk-ok">✓ Confirmed</span>`
    : checkVal === "incorrect"
    ? `<span class="hd-chk hd-chk-fail">✗ Corrected</span>`
    : `<span class="hd-chk hd-chk-na">— Not checked</span>`;
  const extrHtml = extracted
    ? `<div class="hd-row-val"><span class="hd-row-tag">Extracted</span><span>${escapeHtml(String(extracted))}</span></div>`
    : "";
  const corrHtml = corrected && checkVal === "incorrect"
    ? `<div class="hd-row-val hd-row-val-corr"><span class="hd-row-tag">Your correction</span><span>${escapeHtml(corrected)}</span></div>`
    : "";
  return `
    <div class="hd-check-row">
      <div class="hd-check-head">
        <span class="hd-check-label">${label}</span>${statusIcon}
      </div>
      ${extrHtml}${corrHtml}
    </div>`;
}

function renderHistDetail(d) {
  const body = $("#hist-detail-body");
  if (!body) return;

  const doiLink = doi => doi
    ? `<a class="hd-doi-link" href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener">${escapeHtml(doi)} ↗</a>`
    : `<span class="hd-na">—</span>`;

  const fmtDate = iso => iso
    ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })
    : "";

  const notVal = d.corrected_type === "not_validation";
  const typeCorrDisplay = d.corrected_type
    ? (notVal ? "Not a replication/reproduction" : d.corrected_type)
    : null;

  const dateStr = d.validated_at ? fmtDate(d.validated_at) : "";

  // Use validated consensus values if available, else fall back to extracted
  const rec = {
    study_r:  d.val_study_r  || d.study_r,
    doi_r:    d.val_doi_r    || d.doi_r,
    year_r:   d.val_year_r   || d.year_r,
    abstract_r: d.val_abstract_r || d.abstract_r,
    doi_o:    d.val_doi_o    || d.doi_o,
    study_o:  d.val_study_o  || d.study_o,
    year_o:   d.val_year_o   || d.year_o,
    type:     d.val_type     || d.extracted_type,
    outcome:  d.val_outcome  || d.extracted_outcome,
    outcome_quote: d.val_outcome_quote || d.outcome_quote,
  };

  // ---- LEFT COLUMN: saved record ----
  // Prefer the record's real lifecycle status (covers under-review / rejected);
  // fall back to the validated-table flags only if status is missing.
  const statusBadge = d.validation_status
    ? _histStatusBadge(d.validation_status)
    : (d.has_validated
        ? (d.val_admin_approved
            ? `<span class="hd-status-badge hd-status-approved">Approved</span>`
            : `<span class="hd-status-badge hd-status-validated">Consensus reached</span>`)
        : `<span class="hd-status-badge hd-status-pending">Awaiting second validator</span>`);

  const abstractHtml = rec.abstract_r
    ? `<div class="hd-lsection">
         <div class="hd-section-label">Abstract</div>
         <p class="hd-abstract-text">${escapeHtml(rec.abstract_r)}</p>
       </div>`
    : "";

  const classificationHtml = (rec.type || rec.outcome)
    ? `<div class="hd-lsection">
         <div class="hd-section-label">Classification</div>
         ${rec.type    ? `<div class="hd-field"><span class="hd-field-label">Type</span><span class="hd-val-pill">${escapeHtml(rec.type)}</span></div>` : ""}
         ${rec.outcome ? `<div class="hd-field"><span class="hd-field-label">Outcome</span><span class="hd-val-pill hd-val-pill-${escapeHtml(rec.outcome)}">${escapeHtml(fmtOutcome(rec.outcome))}</span></div>` : ""}
         ${rec.outcome_quote ? `<div class="hd-field hd-field-block"><span class="hd-field-label">Quote</span><span class="hd-field-quote">${escapeHtml(rec.outcome_quote)}</span></div>` : ""}
       </div>`
    : "";

  const origHtml = (rec.study_o || rec.doi_o)
    ? `<div class="hd-lsection">
         <div class="hd-section-label">Original study</div>
         ${rec.study_o ? `<div class="hd-field"><span class="hd-field-label">Title</span><span>${escapeHtml(rec.study_o)}</span></div>` : ""}
         <div class="hd-field"><span class="hd-field-label">DOI</span>${doiLink(rec.doi_o)}</div>
         ${rec.year_o  ? `<div class="hd-field"><span class="hd-field-label">Year</span><span>${fmtYear(rec.year_o)}</span></div>` : ""}
       </div>`
    : "";

  // ---- RIGHT COLUMN: this validator's judgement ----
  const titleCorrHtml = d.corrected_study_r
    ? `<div class="hd-check-row">
         <div class="hd-check-head"><span class="hd-check-label">Title fix</span><span class="hd-chk hd-chk-edit">✎ Corrected</span></div>
         <div class="hd-row-val"><span class="hd-row-tag">Was</span><span>${escapeHtml(d.study_r || "—")}</span></div>
         <div class="hd-row-val hd-row-val-corr"><span class="hd-row-tag">Corrected to</span><span>${escapeHtml(d.corrected_study_r)}</span></div>
       </div>`
    : "";

  const quoteHtml = (d.outcome_quote || d.corrected_outcome_quote)
    ? `<div class="hd-check-row">
         <div class="hd-check-head">
           <span class="hd-check-label">Outcome quote</span>
           ${d.corrected_outcome_quote ? `<span class="hd-chk hd-chk-edit">✎ Edited</span>` : ""}
         </div>
         ${d.outcome_quote ? `<div class="hd-row-val"><span class="hd-row-tag">Extracted</span><span>${escapeHtml(d.outcome_quote)}</span></div>` : ""}
         ${d.corrected_outcome_quote ? `<div class="hd-row-val hd-row-val-corr"><span class="hd-row-tag">Your edit</span><span>${escapeHtml(d.corrected_outcome_quote)}</span></div>` : ""}
       </div>`
    : "";

  const notesHtml = d.validator_notes
    ? `<div class="hd-notes-block">
         <div class="hd-section-label">Your notes</div>
         <p class="hd-notes-text">${escapeHtml(d.validator_notes)}</p>
       </div>`
    : "";

  // Combined flag + thread section
  const thread = d.messages || [];
  const rootMsg = thread.find(m => m.direction === "outbound");
  const rootMsgId = rootMsg ? rootMsg.id : null;

  const bubbles = thread.map(m => {
    const isTeam = m.direction === "outbound";
    const time = _fmtRelTime(new Date(m.sent_at));
    // Strip boilerplate preamble from admin messages — only show the reason text
    let body = m.body;
    if (isTeam) {
      const reasonIdx = body.indexOf("Reason:");
      if (reasonIdx !== -1) body = body.slice(reasonIdx + "Reason:".length).trim();
    }
    return `<div class="hd-bubble ${isTeam ? "hd-bubble-team" : "hd-bubble-you"}">
      <p class="hd-bubble-body">${escapeHtml(body)}</p>
      <div class="hd-bubble-meta">
        <span class="hd-bubble-who">${isTeam ? "Review team" : "You"}</span>
        <span class="hd-bubble-time">${time}</span>
      </div>
    </div>`;
  }).join("");

  const msgHtml = (d.flagged || thread.length || rootMsgId)
    ? `<div class="hd-chat-section">
         <div class="hd-chat-header">
           <span class="hd-flag-icon">⚑</span>
           <span class="hd-flag-title">Flagged for review</span>
         </div>
         <div class="hd-chat-thread" id="hd-chat-thread">
           ${thread.length ? bubbles : '<p class="hd-chat-empty">No messages yet.</p>'}
         </div>
         ${rootMsgId ? `<div class="hd-chat-input-row" data-parent-id="${rootMsgId}">
           <textarea class="hd-reply-input" placeholder="Write a reply…" rows="1"></textarea>
           <button class="hd-reply-btn btn-primary">Send</button>
         </div>` : ""}
       </div>`
    : "";

  body.innerHTML = `
    <div class="hd-top-bar">
      <div class="hd-top-meta">
        ${rec.doi_r ? `<span>${doiLink(rec.doi_r)}</span>` : ""}
        ${rec.year_r ? `<span class="hd-record-year">${fmtYear(rec.year_r)}</span>` : ""}
        ${dateStr ? `<span class="hd-record-date">Judged ${dateStr}</span>` : ""}
        ${statusBadge}
      </div>
      <span class="hd-pts-badge">+${d.points ?? 0} pts</span>
    </div>

    <div class="hd-cols">
      <div class="hd-col-left">
        <div class="hd-col-label">${d.has_validated ? "Saved record" : "Record"}</div>
        <h3 class="hd-record-title">${escapeHtml(rec.study_r || rec.doi_r || "Unknown record")}</h3>
        ${classificationHtml}
        ${abstractHtml}
        ${origHtml}
      </div>

      <div class="hd-col-right">
        <div class="hd-col-label">Your judgement</div>
        ${titleCorrHtml}
        ${_detailCheckRow("Study type", d.extracted_type, d.type_check, typeCorrDisplay)}
        ${_detailCheckRow("Original study", d.study_o || d.doi_o || null, d.original_check, d.corrected_study_o || d.corrected_doi_o)}
        ${_detailCheckRow("Outcome", d.extracted_outcome, d.outcome_check, d.corrected_outcome)}
        ${quoteHtml}
        ${notesHtml}
        ${msgHtml}
      </div>
    </div>
  `;

  $("#hist-detail-title").textContent = (rec.study_r || rec.doi_r || "Judgement Detail").substring(0, 70);

  // Wire reply button
  const replyWrap = body.querySelector(".hd-chat-input-row");
  if (replyWrap) {
    const parentId = +replyWrap.dataset.parentId;
    const textarea = replyWrap.querySelector(".hd-reply-input");
    const btn      = replyWrap.querySelector(".hd-reply-btn");
    const chatThread = body.querySelector("#hd-chat-thread");

    // Auto-grow textarea
    textarea.addEventListener("input", () => {
      textarea.style.height = "auto";
      textarea.style.height = textarea.scrollHeight + "px";
    });

    btn.addEventListener("click", async () => {
      const text = textarea.value.trim();
      if (!text) return;
      btn.disabled = true;
      try {
        await api(`/messages/${parentId}/reply`, "POST", { coder_id: state.coder.coder_id, body: text });
        textarea.value = "";
        textarea.style.height = "auto";
        // Remove "no messages yet" placeholder if present
        const empty = chatThread?.querySelector(".hd-chat-empty");
        if (empty) empty.remove();
        // Append bubble immediately
        if (chatThread) {
          const bubble = document.createElement("div");
          bubble.className = "hd-bubble hd-bubble-you";
          bubble.innerHTML = `
            <p class="hd-bubble-body">${escapeHtml(text)}</p>
            <div class="hd-bubble-meta">
              <span class="hd-bubble-who">You</span>
              <span class="hd-bubble-time">just now</span>
            </div>`;
          chatThread.appendChild(bubble);
          chatThread.scrollTop = chatThread.scrollHeight;
        }
        delete _histDetailCache[d.queue_id];
      } catch (e) {
        await showAlert("Could not send: " + e.message);
      }
      btn.disabled = false;
    });
    textarea.addEventListener("keydown", e => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) btn.click();
    });
    // Scroll thread to bottom on load
    if (chatThread) chatThread.scrollTop = chatThread.scrollHeight;
  }
}

/* ============================================================
   Maintenance banner system
   - Handles three layers of messages (priority high→low):
       1. Admin broadcast  – set from admin panel, polled every 60s
       2. Time-based active  – 00:00–01:00 CET, non-dismissible
       3. Time-based warning – 23:40–23:59 CET, dismissible
       4. Login reminder – shown for 30s after login, dismissible
   ============================================================ */

const _MAINT_WARN_MSG   = "Scheduled maintenance starts in less than 20 minutes (at 00:00 CET). Please save your work and plan to return after 01:00 CET.";
const _MAINT_ACTIVE_MSG = "Nightly maintenance is now underway. Please save your work and return after 01:00 CET.";
const _MAINT_LOGIN_MSG  = "A reminder: nightly maintenance runs 00:00–01:00 CET. The app will be briefly unavailable during this window.";

const _bann = {
  phase:          "normal",   // "normal" | "warning" | "active"
  adminMsg:       null,       // string from /api/banner poll, or null
  _loginNotif:    false,      // true for 30s after enterGame
  _dismissed:     new Set(),  // "warning" | "loginNotif" | "admin"
  _loginTimer:    null,
  _watchInterval: null,
  _pollInterval:  null,
};

function _getCETHourMin() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Berlin",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  return {
    h: parseInt(parts.find(p => p.type === "hour").value,   10) % 24,  // guard against "24" at midnight
    m: parseInt(parts.find(p => p.type === "minute").value, 10),
  };
}

function _getTimePhase() {
  const {h, m} = _getCETHourMin();
  if (h === 0)               return "active";   // 00:00–00:59 CET
  if (h === 23 && m >= 40)   return "warning";  // 23:40–23:59 CET
  return "normal";
}

function _showMaintBanner(text, type, dismissible) {
  const el      = $("#maint-banner");
  const icons   = { info: "◆", warning: "▲", active: "▲", admin: "▲" };
  $("#maint-banner-icon").textContent   = icons[type] || "⚠️";
  $("#maint-banner-text").textContent   = text;
  $("#maint-banner-close").style.display = dismissible ? "" : "none";
  el.className = `maint-banner maint-banner-${type}`;
  document.body.classList.add("has-maint-banner");
}

function _hideMaintBanner() {
  $("#maint-banner").className = "maint-banner hidden";
  document.body.classList.remove("has-maint-banner");
}

function _updateMaintBanner() {
  const {phase, adminMsg, _loginNotif, _dismissed} = _bann;

  if (adminMsg && !_dismissed.has("admin")) {
    _showMaintBanner(adminMsg, "admin", true);
    return;
  }
  if (phase === "active" && !_dismissed.has("active")) {
    _showMaintBanner(_MAINT_ACTIVE_MSG, "active", true);
    return;
  }
  if (phase === "warning" && !_dismissed.has("warning")) {
    _showMaintBanner(_MAINT_WARN_MSG, "warning", true);
    return;
  }
  if (_loginNotif && !_dismissed.has("loginNotif")) {
    _showMaintBanner(_MAINT_LOGIN_MSG, "info", true);
    return;
  }
  _hideMaintBanner();
}

async function _pollAdminBanner() {
  try {
    const data = await (await fetch("/api/banner")).json();
    const prev = _bann.adminMsg;
    _bann.adminMsg = (data.active && data.message) ? data.message : null;
    if (_bann.adminMsg !== prev) _bann._dismissed.delete("admin");
    // If an admin banner is active, suppress the login notif so the
    // validator only ever sees one banner.
    if (_bann.adminMsg) {
      _bann._loginNotif = false;
      clearTimeout(_bann._loginTimer);
    }
    _updateMaintBanner();
  } catch (_) {}  // silent — network may be down during maintenance
}

function startMaintenanceSystem() {
  // 30-second login reminder
  _bann._loginNotif = true;
  _bann._loginTimer = setTimeout(() => {
    _bann._loginNotif = false;
    _updateMaintBanner();
  }, 30_000);

  // Initial state
  _bann.phase = _getTimePhase();
  _updateMaintBanner();

  // Check time every 30 seconds
  _bann._watchInterval = setInterval(() => {
    const newPhase = _getTimePhase();
    if (newPhase !== _bann.phase) {
      if (newPhase === "active") _bann._dismissed.delete("warning");
      if (newPhase === "normal") {
        _bann._dismissed.delete("warning");
        _bann._dismissed.delete("loginNotif");
      }
      _bann.phase = newPhase;
    }
    _updateMaintBanner();
  }, 30_000);

  // Poll admin banner every 60 seconds
  _pollAdminBanner();
  _bann._pollInterval = setInterval(_pollAdminBanner, 60_000);
}

$("#maint-banner-close").onclick = () => {
  const {phase, adminMsg, _dismissed} = _bann;
  if (adminMsg && !_dismissed.has("admin")) {
    _dismissed.add("admin");
  } else if (phase === "warning" && !_dismissed.has("warning")) {
    _dismissed.add("warning");
  } else if (phase === "active" && !_dismissed.has("active")) {
    _dismissed.add("active");
  }
  _bann._loginNotif = false;
  clearTimeout(_bann._loginTimer);
  _updateMaintBanner();
};


$("#mode-toggle").onclick = async () => {
  state.mode = state.mode === "normal" ? "hard" : "normal";
  $("#mode-toggle").textContent = state.mode === "hard" ? "Hard mode" : "Normal mode";
  $("#mode-toggle").classList.toggle("active", state.mode === "hard");
  _pairBuffer = [];   // buffered (normal-mode) pairs are short-locked; let them lapse
  await loadNextPair();
  await refreshStats();
};

$("#pending-saves-btn")?.addEventListener("click", () => {
  const lines = _submitQueue.map(it =>
    `• ${(it.paper || "a pair").slice(0, 50)} — ${it.attempts > 0 ? `retrying (try ${it.attempts})` : "saving…"}`);
  showDialog({
    title: "Background saves",
    message: lines.length ? lines.join("\n") : "All judgements saved.",
    buttons: [{ label: "OK", value: true, primary: true }],
  });
});

/* ---------- Split-layout toggle ---------- */
let splitLayout = localStorage.getItem("flora.splitLayout") !== "0"; // default on

function applySplitLayout() {
  const cards = [$("#pair-card"), $("#onb-card")].filter(Boolean);
  cards.forEach(c => c.classList.toggle("split-layout", splitLayout));
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

/* ---------- Draft save/restore (Option B — persists text fields across reloads) ---------- */
const _DRAFT_KEY = (pair_id) => `flora.draft.${pair_id}`;
let _draftInterval = null;

function _startDraftSave(pair_id) {
  clearInterval(_draftInterval);
  _draftInterval = setInterval(() => {
    if (state.currentPair?.pair_id === pair_id) {
      localStorage.setItem(_DRAFT_KEY(pair_id), JSON.stringify(state.judgement));
    }
  }, 10_000);
}

function _clearDraft(pair_id) {
  clearInterval(_draftInterval);
  _draftInterval = null;
  if (pair_id) localStorage.removeItem(_DRAFT_KEY(pair_id));
}

function _restoreDraftInputs(card, draft) {
  if (!draft) return;
  // Pre-fill text corrections (the gate selections are restored by _replayGates).
  const comment = card.querySelector(".comment");
  if (comment && draft.comment) comment.value = draft.comment;

  const doiInput   = card.querySelector("#corrected-doi-input");
  const studyInput = card.querySelector("#corrected-study-input");
  if (doiInput   && draft.corrected_doi_o)   doiInput.value   = draft.corrected_doi_o;
  if (studyInput && draft.corrected_study_o) studyInput.value = draft.corrected_study_o;

  const urlBtn = card.querySelector("#suggest-url-r-btn");
  if (urlBtn && draft.corrected_url_r) urlBtn.textContent = "suggest link (✓ edited)";

  // Restore the hard-mode "can't access" checkbox so state and UI stay in sync.
  const noAccessCb = card.querySelector("#no-access-cb");
  if (noAccessCb && draft.no_access) {
    noAccessCb.checked = true;
    card.querySelector("#gate-3")?.classList.add("no-access-on");
    const sb = card.querySelector("#submit-btn");
    if (sb) sb.textContent = "Report — can't access";
    updateSubmitState(card.querySelector(".pair-body"));
  }
}

// Replay the saved gate answers on resume so the form shows exactly where the
// validator left off — gates revealed, choices highlighted, Submit reflecting
// real readiness. Without this the card renders blank with Submit disabled even
// though state.judgement is fully restored, and a partial re-click could submit
// stale draft values. Answers are pushed back through onChoice so the reveal
// logic isn't duplicated.
function _replayGates(card, draft) {
  if (!draft || draft.no_access) return;
  // IMPORTANT: state.judgement IS this same draft object, and onChoice resets
  // outcome/repro fields when the type is (re)clicked — which would erase the very
  // answers we're about to replay. Snapshot them first, then drive from the copy.
  const a = {
    type: draft.type, original: draft.original, outcome: draft.outcome,
    corrected_outcome: draft.corrected_outcome,
    corrected_doi_o: draft.corrected_doi_o, corrected_study_o: draft.corrected_study_o,
    repro_computation: draft.repro_computation, repro_robustness: draft.repro_robustness,
  };
  const body = card.querySelector(".pair-body");
  const click = (sel) => { const b = card.querySelector(sel); if (b) onChoice(b); return b; };

  // 1. Type
  if (!a.type) return;
  click(`[data-type="${a.type}"]`);
  if (a.type === "not_validation") { updateSubmitState(body); return; }

  // 2. Original
  if (!a.original) { updateSubmitState(body); return; }
  click(`[data-original="${a.original}"]`);

  // "Wrong paper" keeps gate-3 hidden until the suggestion is saved/skipped. If
  // the draft got past that (a suggestion was drafted, or any outcome was picked),
  // reflect that resolved state and reveal gate-3.
  if (a.original === "wrong") {
    const hasSuggestion  = a.corrected_doi_o || a.corrected_study_o;
    const reachedOutcome = a.outcome || a.corrected_outcome ||
                           a.repro_computation || a.repro_robustness;
    if (hasSuggestion || reachedOutcome) {
      const oc = card.querySelector("#original-correction");
      if (hasSuggestion) {
        oc?.querySelector("#oc-form")?.classList.add("hidden");
        oc?.querySelector("#oc-saved-confirm")?.classList.remove("hidden");
      }
      card.querySelector("#gate-3")?.classList.remove("hidden");
    }
  }

  // 3. Outcome — reproduction has two axes; replication is one pick (+ correction
  //    when "Mischaracterised").
  if (a.type === "reproduction") {
    if (a.repro_computation) click(`[data-repro-comp="${a.repro_computation}"]`);
    if (a.repro_robustness)  click(`[data-repro-robust="${a.repro_robustness}"]`);
  } else if (a.outcome) {
    click(`[data-outcome="${a.outcome}"]`);
    if (a.outcome === "wrong" && a.corrected_outcome) {
      click(`[data-correct-outcome="${a.corrected_outcome}"]`);
    }
  }
  updateSubmitState(body);
}

/* ===================================================================
   Prefetch buffer + optimistic background submission (normal mode)
   - Buffer up to BUFFER_TARGET pairs so the next pair shows instantly.
   - Only the active pair is "started" (5-day lock); buffered pairs hold a
     short lock so they don't starve the pool.
   - Submits go to a persistent queue, sent in the background with retry.
     Points are shown deferred-but-accurate (when the server confirms).
   =================================================================== */
const BUFFER_TARGET = 3;
const SUBMIT_MAX_ATTEMPTS = 8;
let _pairBuffer = [];
let _bufferFilling = false;

// Buffering + optimistic submit for the live backend (normal AND hard mode —
// both use the same gate layout, just different record pools).
const _bufferEligible = () => API_MODE !== "static";
const _sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function _fillBuffer() {
  if (!_bufferEligible() || _bufferFilling || _pairBuffer.length >= BUFFER_TARGET) return;
  _bufferFilling = true;
  try {
    const need = BUFFER_TARGET - _pairBuffer.length;
    const resp = await api(`/next-pairs?coder_id=${state.coder.coder_id}&count=${need}&buffered_only=true&mode=${state.mode}`);
    for (const p of (resp.pairs || [])) {
      if (!_pairBuffer.some(b => b.queue_id === p.queue_id)) _pairBuffer.push(p);
    }
  } catch (_) { /* best-effort prefetch */ }
  finally { _bufferFilling = false; }
}

// record_ids whose judgement is still in flight (optimistically submitted) —
// must never be re-served, even if the server resume would return them.
const _pendingRecordIds = () => new Set(_submitQueue.map(it => String(it.record_id)));

// Returns the next active (started) pair, or null when the pool is empty.
// allowResume=true lets the first fetch return a previous-session started pair;
// post-submit advances pass false so the just-submitted pair can't come back.
async function _takeActivePair(allowResume = true) {
  const pending = _pendingRecordIds();
  // Bound the attempts so a persistently-failing /start (e.g. server 500) can't
  // spin forever; give up → caller shows the done/empty state.
  let tries = 0;
  const MAX_TRIES = BUFFER_TARGET * 4;
  while (tries++ < MAX_TRIES) {
    let cand;
    if (_pairBuffer.length) {
      cand = _pairBuffer.shift();
    } else {
      const resp = await api(`/next-pairs?coder_id=${state.coder.coder_id}&count=${BUFFER_TARGET}&buffered_only=${!allowResume}&mode=${state.mode}`);
      const pairs = resp.pairs || [];
      if (!pairs.length) return null;
      _pairBuffer.push(...pairs);
      allowResume = false;             // only the first fetch may resume — avoids a refetch loop
      cand = _pairBuffer.shift();
    }
    if (pending.has(String(cand.record_id))) continue;   // already submitted optimistically
    try {
      // Promote to the active 5-day lock. Harmless on an already-started resume.
      await api(`/pairs/${cand.queue_id}/start`, "POST", { coder_id: state.coder.coder_id });
      return cand;
    } catch (_) {
      // Slot reaped/reassigned (or transient) — discard and try the next.
    }
  }
  return null;
}

async function loadNextPair(allowResume = true) {
  if (!_bufferEligible()) return _loadSinglePair();   // hard mode: single fetch

  // (B) If the buffer is empty we may need a network fetch — show a loading
  // placeholder so we never strand the user on the just-submitted card.
  if (!_pairBuffer.length) _showLoadingCard();

  // (A) Retry transient failures (cold start / network) with backoff before
  // surfacing an inline retry. ~17s of cover, which a waking server beats.
  const RETRY_DELAYS = [0, 2000, 5000, 10000];
  for (let i = 0; i < RETRY_DELAYS.length; i++) {
    if (RETRY_DELAYS[i]) await _sleep(RETRY_DELAYS[i]);
    try {
      const active = await _takeActivePair(allowResume);
      if (!active) {
        $("#pair-card").classList.add("hidden");
        $("#done-screen").classList.remove("hidden");
        _renderPendingPanel();
        return;
      }
      _showActivePair(active);
      _fillBuffer();   // background top-up; don't await
      return;
    } catch (_) {
      // transient — fall through to the next retry
    }
  }
  _showRetryCard(allowResume);   // (B) recoverable state instead of a dead card
}

function _showLoadingCard() {
  $("#done-screen").classList.add("hidden");
  const card = $("#pair-card");
  card.classList.remove("hidden");
  card.innerHTML = `<div class="pair-status"><div class="pair-spinner"></div><p>Loading next pair…</p></div>`;
}

function _showRetryCard(allowResume) {
  $("#done-screen").classList.add("hidden");
  const card = $("#pair-card");
  card.classList.remove("hidden");
  card.innerHTML = `<div class="pair-status">
    <p>Couldn't load the next pair — the server may be waking up.</p>
    <button id="pair-retry-btn" class="btn-primary">Retry</button>
  </div>`;
  card.querySelector("#pair-retry-btn").onclick = () => loadNextPair(allowResume);
}

function _showActivePair(pair) {
  clearPairTimer();   // defensive: no timer is armed anymore, but stay safe
  $("#done-screen").classList.add("hidden");
  $("#pair-card").classList.remove("hidden");
  state.currentPair = pair;
  const pair_id  = pair.pair_id;
  const draftRaw = pair.resumed ? localStorage.getItem(_DRAFT_KEY(pair_id)) : null;
  const draft    = draftRaw ? (() => { try { return JSON.parse(draftRaw); } catch { return null; } })() : null;
  state.judgement = draft || blankJudgement();
  const card = $("#pair-card");
  renderPairInto(card, pair, { onboarding: false, judgeCount: pair.judge_count });
  if (pair.resumed) { _restoreDraftInputs(card, draft); _replayGates(card, draft); }
  _startDraftSave(pair_id);
  applySplitLayout();
  // No client auto-skip in either mode: the server holds a 5-day lock so the
  // validator can leave and resume — hard mode behaves exactly like normal mode.
}

// Single-fetch path for hard mode (no buffering / no optimistic submit).
async function _loadSinglePair() {
  const resp = await api(`/next-pair?coder_id=${state.coder.coder_id}&mode=${state.mode}`);
  if (!resp.pair) {
    $("#pair-card").classList.add("hidden");
    $("#done-screen").classList.remove("hidden");
    return;
  }
  $("#done-screen").classList.add("hidden");
  $("#pair-card").classList.remove("hidden");
  state.currentPair = resp.pair;

  const pair_id  = resp.pair.pair_id;
  const draftRaw = resp.resumed ? localStorage.getItem(_DRAFT_KEY(pair_id)) : null;
  const draft    = draftRaw ? (() => { try { return JSON.parse(draftRaw); } catch { return null; } })() : null;
  state.judgement = draft || blankJudgement();

  const card = $("#pair-card");
  renderPairInto(card, resp.pair, { onboarding: false, judgeCount: resp.judge_count });
  if (resp.resumed) { _restoreDraftInputs(card, draft); _replayGates(card, draft); }

  _startDraftSave(pair_id);
  applySplitLayout();
}

/* ---------- Submission queue (persistent, retry-then-reassign) ---------- */
const _SUBMIT_KEY = "flora.pendingSubmits";
const _FAILED_KEY = "flora.failedSubmits";
let _submitQueue  = (() => { try { return JSON.parse(localStorage.getItem(_SUBMIT_KEY)) || []; } catch { return []; } })();
let _failedSubmits = (() => { try { return JSON.parse(localStorage.getItem(_FAILED_KEY)) || []; } catch { return []; } })();
let _submitProcessing = false;

const _persistSubmits = () => localStorage.setItem(_SUBMIT_KEY, JSON.stringify(_submitQueue));
const _persistFailed  = () => localStorage.setItem(_FAILED_KEY, JSON.stringify(_failedSubmits));

// Terminal = won't succeed on retry (slot gone / validation error). Network
// errors and 5xx (server waking) are retryable.
function _isTerminalErr(e) {
  if (e && typeof e.status === "number") return e.status >= 400 && e.status < 500;
  const m = (e && e.message) || "";
  return /No open slot|Already judged|no longer assigned/i.test(m);
}

function _enqueueSubmit(payload, paper) {
  _submitQueue.push({ key: `${payload.record_id}:${Date.now()}`, payload, record_id: payload.record_id, paper, attempts: 0 });
  _persistSubmits();
  _updatePendingIndicator();
  _processSubmitQueue();
}

async function _processSubmitQueue() {
  if (_submitProcessing) return;
  _submitProcessing = true;
  while (_submitQueue.length) {
    const item = _submitQueue[0];
    try {
      const resp = await api("/judge", "POST", item.payload);
      _submitQueue.shift(); _persistSubmits();
      _celebratePoints(resp.points_earned);
      refreshStats().catch(() => {});
      refreshLeaderboard().catch(() => {});
    } catch (e) {
      item.attempts++;
      const giveUp = _isTerminalErr(e) || item.attempts >= SUBMIT_MAX_ATTEMPTS;
      if (giveUp) {
        // Release the slot so another validator can pick this record up.
        await api("/skip", "POST", { coder_id: state.coder.coder_id, record_id: String(item.record_id) }).catch(() => {});
        _submitQueue.shift(); _persistSubmits();
        _failedSubmits.push({ paper: item.paper, at: Date.now() });
        if (_failedSubmits.length > 10) _failedSubmits = _failedSubmits.slice(-10);
        _persistFailed();
        showToast(`Couldn't save "${(item.paper || "a pair").slice(0, 40)}" — released to another validator.`);
      } else {
        _persistSubmits();
        await _sleep(Math.min(30000, 1000 * 2 ** item.attempts));  // backoff; slot is locked 5d so it's safe to wait
        continue;  // retry the same item
      }
    }
    _updatePendingIndicator();
  }
  _submitProcessing = false;
  _updatePendingIndicator();
}

function _celebratePoints(points) {
  if (points == null) return;
  showToast(points, "points");
  if (typeof confetti !== "undefined") {
    confetti({ particleCount: 60, spread: 60, origin: { y: 0.5 },
               colors: ["#b54614", "#1a1612", "#d6a87e", "#4a6b3e"], scalar: 0.9 });
  }
}

function _updatePendingIndicator() {
  const n = _submitQueue.length;
  const btn = $("#pending-saves-btn");
  const cnt = $("#pending-saves-count");
  if (cnt) cnt.textContent = n;
  if (btn) btn.classList.toggle("hidden", n === 0);
  if (!$("#done-screen")?.classList.contains("hidden")) _renderPendingPanel();
}

function _renderPendingPanel() {
  const panel = $("#pending-panel");
  if (!panel) return;
  const rows = [];
  for (const it of _submitQueue) {
    const label = it.attempts > 0 ? `retrying (try ${it.attempts})` : "saving…";
    rows.push(`<li class="pending-row pending-row-active"><span>${escapeHtml((it.paper || "a pair").slice(0, 60))}</span><span class="pending-tag">${label}</span></li>`);
  }
  for (const it of _failedSubmits) {
    rows.push(`<li class="pending-row pending-row-failed"><span>${escapeHtml((it.paper || "a pair").slice(0, 60))}</span><span class="pending-tag">reassigned</span></li>`);
  }
  if (!rows.length) { panel.classList.add("hidden"); panel.innerHTML = ""; return; }
  panel.classList.remove("hidden");
  panel.innerHTML = `<h3 class="pending-title">Background saves</h3><ul class="pending-list">${rows.join("")}</ul>`;
}

/* ---------- Pair timer cleanup ----------
   The 30-min hard-mode auto-skip was removed: hard mode now behaves exactly like
   normal mode — the server holds a 5-day lock and releases abandoned records via
   the reaper. clearPairTimer stays as a harmless no-op for the defensive calls. */
let _pairExpireTimer = null;

function clearPairTimer() {
  clearTimeout(_pairExpireTimer);
  _pairExpireTimer = null;
}

/* ---------- Generic styled dialog (promise-based) ---------- */
function showAlert(message) {
  return showDialog({ message: message || "An unexpected error occurred.", buttons: [{ label: "OK", value: true, primary: true }] });
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

function showDialog({ icon, title, message, buttons, layout = "column", rawHtml = false }) {
  return new Promise(resolve => {
    const iconEl  = $("#dialog-icon");
    const titleEl = $("#dialog-title");
    const msgEl   = $("#dialog-message");
    const btnsEl  = $("#dialog-buttons");

    iconEl.textContent  = icon || "";
    iconEl.style.display = icon ? "" : "none";
    titleEl.textContent = title || "";
    titleEl.style.display = title ? "" : "none";
    if (rawHtml) { msgEl.innerHTML = message || ""; }
    else         { msgEl.textContent = message || ""; }

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

/* ---------- Pair rendering (normal + onboarding) ---------- */
let _pairShownAt = null;

function renderPairInto(container, p, { onboarding, judgeCount }) {
  if (!onboarding) _pairShownAt = Date.now();
  const isHard = !onboarding && state.mode === "hard" && !state.assignment;
  const outcomeLabel = (p.outcome || "uninformative").toLowerCase();
  const doiUrl = p.doi_r ? `https://doi.org/${p.doi_r}` : null;
  const oaUrl = p.oa_url_r || (p.url_r && p.url_r !== doiUrl ? p.url_r : null);
  const scholarQuery = encodeURIComponent(`${p.title_r || ""} ${p.authors_r || ""}`);
  const scholarUrl = `https://scholar.google.com/scholar?q=${scholarQuery}`;
  const oUrl = p.doi_o ? `https://doi.org/${p.doi_o}` : null;
  const oOaUrl = p.oa_url_o || null;
  // Journal of the original study, pulled from ref_o ("Surname · Year · Journal").
  const oJournal = (() => {
    const parts = (p.ref_o || "").split("·").map(s => s.trim()).filter(Boolean);
    return parts.length >= 3 ? parts.slice(2).join(" · ") : "";
  })();
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
         <button class="choice danger" data-type="not_validation">✗ Not either type<small>not a replication</small></button>
       </div>`
    : `<p class="question">The system classified this as&ensp;<span class="outcome-label ${pType}">${pType}</span>&ensp;— is that correct?</p>
       <div class="choices">
         <button class="choice success" data-type="${pType}">✓ Correct</button>
         <button class="choice warn" data-type="${oppositeType}">Actually ${oppositeType}<small>${oppositeLabel}</small></button>
         <button class="choice danger" data-type="not_validation">✗ Not either type<small>not studying replication</small></button>
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

      <div class="abstract-block">
        <div class="title-row">
          <h2 id="title-text">${escapeHtml(p.title_r || "(untitled)")}</h2>
          <button class="link-btn" id="fix-title-btn" title="Fix a typographical error in this title">fix typo</button>
        </div>
        <input type="text" class="title-edit hidden" id="title-edit" placeholder="Corrected title…">
        <div class="authors">${escapeHtml(p.authors_r || "?")} · ${fmtYear(p.year_r)}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}${doiUrl ? ` · <a href="${escapeHtml(oaUrl || doiUrl)}" target="_blank" rel="noopener" title="${oaUrl ? "Open access PDF" : "Publisher page (likely paywalled)"}">${lockIcon(!!oaUrl)} ${escapeHtml(p.doi_r)}</a>` : ""}</div>
        <div class="abstract expanded" id="abstract-text">${escapeHtml(p.abstract_r || "(no abstract available)")}</div>
        <textarea class="abstract-edit hidden" id="abstract-edit"></textarea>
        <div class="abstract-tools">
          <button class="link-btn" id="edit-abstract-btn">edit abstract</button>
          <span class="abstract-tools-spacer"></span>
          <a href="${escapeHtml(scholarUrl)}" target="_blank" rel="noopener" title="Search for an alternate copy">Scholar</a>
          <button class="link-btn" id="suggest-url-r-btn" title="Suggest the correct link for this paper">suggest link</button>
        </div>
        <input type="text" class="title-edit hidden" id="url-r-edit" placeholder="https://… correct link for this paper">
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

      <!-- Senior validator fast-reject panel -->
      <div id="senior-reject-panel" class="senior-reject-panel${(state.coder && state.coder.validator_tier >= 2) ? '' : ' hidden'}">
        <details>
          <summary class="senior-reject-summary">Senior override: mark as not a replication</summary>
          <div class="senior-reject-body">
            <p class="senior-reject-hint">As a senior validator you can immediately reject this record. No second validator or LLM check will run.</p>
            <textarea id="senior-reject-notes" class="senior-reject-notes" placeholder="Notes (optional)…"></textarea>
            <button id="senior-reject-btn" class="btn-reject">✗ Mark as Not a Replication</button>
          </div>
        </details>
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
              ${escapeHtml(p.authors_o || "?")} · ${fmtYear(p.year_o)}${oJournal ? " · " + escapeHtml(oJournal) : ""}
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
      </div>

      <!-- Original correction panel — lives BETWEEN gates, not inside Gate 2 body -->
      <div id="original-correction" class="hidden" style="margin:0.5rem 0 0.75rem;padding:1rem 1.1rem;background:var(--bg-alt);border-radius:10px;border:1px solid var(--rule)">
        <div style="display:flex;align-items:baseline;gap:0.5rem;margin-bottom:0.75rem">
          <span style="font-size:0.9rem;font-weight:600;color:var(--ink)">Suggest the correct paper</span>
          <span style="font-size:0.78rem;color:var(--muted)">(optional — you can skip this)</span>
        </div>
        <div id="oc-form">
          <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:0.2rem">DOI or URL</label>
          <input id="corrected-doi-input" type="text" placeholder="e.g. https://doi.org/10.1000/xyz"
            style="width:100%;box-sizing:border-box;padding:0.4rem 0.65rem;font-size:0.85rem;border:1px solid var(--rule);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:0.55rem;outline:none">
          <label style="font-size:0.8rem;color:var(--muted);display:block;margin-bottom:0.2rem">Study title</label>
          <input id="corrected-study-input" type="text" placeholder="e.g. Smith et al. (2018) — The effect of..."
            style="width:100%;box-sizing:border-box;padding:0.4rem 0.65rem;font-size:0.85rem;border:1px solid var(--rule);border-radius:6px;background:var(--bg);color:var(--ink);margin-bottom:0.75rem;outline:none">
          <div style="display:flex;gap:0.6rem;align-items:center">
            <button id="oc-save-btn" class="btn-primary" style="font-size:0.82rem;padding:0.35rem 1rem">Save suggestion</button>
            <button id="oc-skip-btn" class="ghost-btn" style="font-size:0.82rem;padding:0.35rem 0.85rem;color:var(--muted)">Skip</button>
          </div>
        </div>
        <div id="oc-saved-confirm" class="hidden" style="font-size:0.85rem;color:var(--green)">✓ Suggestion saved — <button class="link-btn" id="oc-edit-btn" style="font-size:0.82rem">edit</button></div>
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
            <span class="outcome-label ${escapeHtml(outcomeLabel)}">${escapeHtml(fmtOutcome(outcomeLabel))}</span>
            <div class="outcome-quote-wrap">
              <div class="outcome-quote${hasQuote ? "" : " hidden"}" id="outcome-quote-text">${hasQuote ? `"${escapeHtml(p.outcome_phrase)}"` : ""}</div>
              ${hasQuote ? "" : '<p id="outcome-quote-empty" style="margin:0.4rem 0"><em>No outcome quote was extracted.</em></p>'}
              <textarea class="outcome-quote-edit hidden" id="outcome-quote-edit" rows="3"></textarea>
              <button class="link-btn small" id="edit-quote-btn">edit / extend quote</button>
            </div>
          </div>
          <div id="replication-outcome">
            <div class="choices">
              <button class="choice success" data-outcome="correct">Looks right</button>
              <button class="choice danger" data-outcome="wrong">Mischaracterised</button>
              <button class="choice warn" data-outcome="unsure">Can't tell</button>
            </div>
            <div class="outcome-correction hidden" id="outcome-correction">
              <p id="outcome-correction-label" style="margin:12px 0 6px;font-size:13px;color:var(--muted);">What is the correct outcome?</p>
              <div class="choices">
                <button class="choice success" data-correct-outcome="success">Success</button>
                <button class="choice danger" data-correct-outcome="failure">Failed</button>
                <button class="choice warn" data-correct-outcome="mixed">Mixed</button>
                <button class="choice" data-correct-outcome="uninformative">Uninformative</button>
              </div>
            </div>
          </div>
          <div class="repro-outcome hidden" id="repro-outcome">
            <div class="repro-axis">
              <span class="repro-axis-label">Computational reproduction</span>
              <div class="choices">
                <button class="choice success" data-repro-comp="successful">Successful</button>
                <button class="choice danger" data-repro-comp="issues">Issues</button>
                <button class="choice warn" data-repro-comp="not_checked">Not checked</button>
              </div>
            </div>
            <div class="repro-axis">
              <span class="repro-axis-label">Robustness</span>
              <div class="choices">
                <button class="choice success" data-repro-robust="robust">Robust</button>
                <button class="choice danger" data-repro-robust="challenges">Challenges</button>
                <button class="choice warn" data-repro-robust="not_checked">Not checked</button>
              </div>
            </div>
          </div>
          ${isHard ? `
          <label class="no-access-row">
            <input type="checkbox" id="no-access-cb">
            <span>I cannot access this article — send it to the review team</span>
          </label>` : ""}
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

  // Hard-mode "I cannot access this article" — checking it lets the validator
  // report the record (no judgement / no points) instead of judging it.
  const noAccessCb = container.querySelector("#no-access-cb");
  if (noAccessCb) {
    noAccessCb.onchange = () => {
      state.judgement.no_access = noAccessCb.checked;
      const gate3 = container.querySelector("#gate-3");
      if (gate3) gate3.classList.toggle("no-access-on", noAccessCb.checked);
      const btn = container.querySelector("#submit-btn");
      if (btn) btn.textContent = noAccessCb.checked ? "Report — can't access" : "Submit";
      updateSubmitState(container.querySelector(".pair-body"));
    };
  }

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

  // Wire original correction panel
  const doiInput      = container.querySelector("#corrected-doi-input");
  const studyInput    = container.querySelector("#corrected-study-input");
  const ocPanel       = container.querySelector("#original-correction");
  const ocSaveBtn     = container.querySelector("#oc-save-btn");
  const ocSkipBtn     = container.querySelector("#oc-skip-btn");
  const ocConfirm     = container.querySelector("#oc-saved-confirm");
  const ocEditBtn     = container.querySelector("#oc-edit-btn");
  const gate3         = container.querySelector("#gate-3");

  const ocForm = ocPanel?.querySelector("#oc-form");

  function ocShowForm() {
    if (ocForm)    ocForm.classList.remove("hidden");
    if (ocConfirm) ocConfirm.classList.add("hidden");
  }

  function _revealGate3() {
    if (gate3) gate3.classList.remove("hidden");
  }

  if (doiInput)   doiInput.oninput   = () => { state.judgement.corrected_doi_o   = doiInput.value.trim()   || null; };
  if (studyInput) studyInput.oninput = () => { state.judgement.corrected_study_o = studyInput.value.trim() || null; };

  if (ocSaveBtn) {
    ocSaveBtn.onclick = () => {
      state.judgement.corrected_doi_o   = doiInput?.value.trim()   || null;
      state.judgement.corrected_study_o = studyInput?.value.trim() || null;
      if (ocForm)    ocForm.classList.add("hidden");
      if (ocConfirm) ocConfirm.classList.remove("hidden");
      _revealGate3();
    };
  }

  if (ocSkipBtn) {
    ocSkipBtn.onclick = () => {
      state.judgement.corrected_doi_o   = null;
      state.judgement.corrected_study_o = null;
      if (doiInput)   doiInput.value   = "";
      if (studyInput) studyInput.value = "";
      if (ocPanel)    ocPanel.classList.add("hidden");
      _revealGate3();
    };
  }

  if (ocEditBtn) {
    ocEditBtn.onclick = () => {
      ocShowForm();
      // Hide gate 3 again and reset its answer so the validator
      // reconsiders the outcome with the updated paper in mind.
      if (gate3) {
        gate3.classList.add("hidden");
        unanswerGate(gate3);
      }
      state.judgement.outcome = null;
      state.judgement.corrected_outcome = null;
      const cr = container.querySelector("#outcome-correction");
      if (cr) cr.classList.add("hidden");
      updateSubmitState(container.querySelector(".pair-body"));
    };
  }

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
  const fixTitleBtn = container.querySelector("#fix-title-btn");
  if (fixTitleBtn) {
    const titleText = container.querySelector("#title-text");
    const titleEdit = container.querySelector("#title-edit");
    const _saveTitle = () => {
      if (titleEdit.classList.contains("hidden")) return;
      const v = titleEdit.value.trim();
      state.judgement.corrected_study_r = v && v !== (p.title_r || "").trim() ? v : null;
      if (titleText) titleText.textContent = state.judgement.corrected_study_r || p.title_r || "(untitled)";
      titleEdit.classList.add("hidden");
      fixTitleBtn.textContent = state.judgement.corrected_study_r ? "fix typo (✓ edited)" : "fix typo";
    };
    fixTitleBtn.onclick = () => {
      if (titleEdit.classList.contains("hidden")) {
        titleEdit.value = state.judgement.corrected_study_r || p.title_r || "";
        titleEdit.classList.remove("hidden");
        fixTitleBtn.textContent = "save title";
        titleEdit.focus();
        titleEdit.select();
      } else {
        _saveTitle();
      }
    };
    // Auto-save when the user clicks/tabs away from the field.
    titleEdit.addEventListener("blur", (e) => {
      if (e.relatedTarget === fixTitleBtn) return;
      _saveTitle();
    });
  }

  const suggestUrlBtn = container.querySelector("#suggest-url-r-btn");
  if (suggestUrlBtn) {
    const urlEdit = container.querySelector("#url-r-edit");
    const _origUrl = (p.url_r || "").trim();
    const _saveUrl = () => {
      if (urlEdit.classList.contains("hidden")) return;
      const v = urlEdit.value.trim();
      // Keep any non-empty value that differs from the current link; the admin
      // reviews and sanitises suggestions, so don't silently drop edge cases.
      state.judgement.corrected_url_r = v && v !== _origUrl ? v : null;
      urlEdit.classList.add("hidden");
      suggestUrlBtn.textContent = state.judgement.corrected_url_r ? "suggest link (✓ edited)" : "suggest link";
    };
    suggestUrlBtn.onclick = () => {
      if (urlEdit.classList.contains("hidden")) {
        urlEdit.value = state.judgement.corrected_url_r || _origUrl || "";
        urlEdit.classList.remove("hidden");
        suggestUrlBtn.textContent = "save link";
        urlEdit.focus();
        urlEdit.select();
      } else {
        _saveUrl();
      }
    };
    // Auto-save when the user clicks/tabs away from the field.
    urlEdit.addEventListener("blur", (e) => {
      if (e.relatedTarget === suggestUrlBtn) return;
      _saveUrl();
    });
  }

  const editAbstractBtn = container.querySelector("#edit-abstract-btn");
  const abstractEdit = container.querySelector("#abstract-edit");
  const abstractText = container.querySelector("#abstract-text");
  const _saveAbstract = () => {
    if (abstractEdit.classList.contains("hidden")) return;
    const v = abstractEdit.value.trim();
    if (!v) {
      // Block saving an empty abstract — shake the button and hint
      editAbstractBtn.classList.add("unsaved-shake");
      editAbstractBtn.addEventListener("animationend", () => editAbstractBtn.classList.remove("unsaved-shake"), { once: true });
      const existing = container.querySelector(".abstract-empty-hint");
      if (!existing) {
        const hint = document.createElement("div");
        hint.className = "abstract-empty-hint unsaved-hint";
        hint.textContent = "Abstract cannot be empty — please add the text before saving.";
        abstractEdit.insertAdjacentElement("afterend", hint);
        setTimeout(() => hint.remove(), 5000);
      }
      return;
    }
    state.judgement.edited_abstract = v && v !== (p.abstract_r || "").trim() ? v : null;
    abstractText.textContent = v;
    abstractText.classList.remove("hidden");
    abstractEdit.classList.add("hidden");
    editAbstractBtn.textContent = state.judgement.edited_abstract ? "edit abstract (✓ edited)" : "edit abstract";
  };
  editAbstractBtn.onclick = () => {
    if (abstractEdit.classList.contains("hidden")) {
      // Seed from the already-saved edit so reopening to fix more builds on it;
      // fall back to the extracted abstract only before any edit exists.
      abstractEdit.value = state.judgement.edited_abstract || p.abstract_r || "";
      abstractEdit.style.height = abstractText.offsetHeight + "px";
      abstractText.classList.add("hidden");
      abstractEdit.classList.remove("hidden");
      editAbstractBtn.textContent = "save edited abstract";
      abstractEdit.focus();
    } else {
      _saveAbstract();
    }
  };
  // Auto-save when the user clicks/tabs away from the field. If it's empty the
  // save is blocked (the field stays open with the hint), same as the button.
  abstractEdit.addEventListener("blur", (e) => {
    if (e.relatedTarget === editAbstractBtn) return;
    _saveAbstract();
  });

  const seniorRejectBtn = container.querySelector("#senior-reject-btn");
  if (seniorRejectBtn) {
    seniorRejectBtn.onclick = async () => {
      const notes = container.querySelector("#senior-reject-notes")?.value.trim() || null;
      seniorRejectBtn.disabled = true;
      seniorRejectBtn.textContent = "Rejecting…";
      try {
        const resp = await api("/senior-reject", "POST", {
          coder_id:        state.coder.coder_id,
          record_id:       String(p.record_id),
          validator_notes: notes,
        });
        showToast(resp.points_earned, "pts — rejected");
        await loadNextPair();
      } catch (e) {
        seniorRejectBtn.disabled = false;
        seniorRejectBtn.textContent = "✗ Mark as Not a Replication";
        await showAlert("Error: " + e.message);
      }
    };
  }

  const editQuoteBtn = container.querySelector("#edit-quote-btn");
  if (editQuoteBtn) {
    const quoteText    = container.querySelector("#outcome-quote-text");
    const quoteEdit    = container.querySelector("#outcome-quote-edit");
    const outcomeChoices    = container.querySelector("#gate-3 .choices");
    const correctionRow     = container.querySelector("#outcome-correction");

    const _lockChoices = () => {
      if (!outcomeChoices) return;
      outcomeChoices.classList.add("quote-edit-open");
      outcomeChoices.querySelectorAll(".choice").forEach(b => {
        b.setAttribute("data-pre-lock-title", b.title || "");
        b.title = "Save your edited quote first";
      });
    };
    const _unlockChoices = () => {
      if (!outcomeChoices) return;
      outcomeChoices.classList.remove("quote-edit-open");
      outcomeChoices.querySelectorAll(".choice").forEach(b => {
        b.title = b.getAttribute("data-pre-lock-title") || "";
        b.removeAttribute("data-pre-lock-title");
      });
    };

    const _saveQuote = () => {
      if (quoteEdit.classList.contains("hidden")) return;
      const v = quoteEdit.value.trim();
      state.judgement.edited_outcome_quote = v && v !== (p.outcome_phrase || "").trim() ? v : null;
      const emptyMsg = container.querySelector("#outcome-quote-empty");
      if (quoteText) {
        quoteText.textContent = v ? `"${v}"` : "";
        if (v) {
          quoteText.classList.remove("hidden");
          if (emptyMsg) emptyMsg.classList.add("hidden");
        } else {
          quoteText.classList.add("hidden");
          if (emptyMsg) emptyMsg.classList.remove("hidden");
        }
      }
      quoteEdit.classList.add("hidden");
      _unlockChoices();

      if (state.judgement.type === "reproduction") {
        // Reproduction: the quote is context only — just save the edit; don't
        // run the replication "auto-select wrong" flow (it would wipe the
        // reproduction outcome).
        editQuoteBtn.textContent = state.judgement.edited_outcome_quote ? "edit quote (✓ edited)" : "edit / extend quote";
        updateSubmitState(container.querySelector(".pair-body"));
        return;
      }

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
      updateSubmitState(container.querySelector(".pair-body"));
    };

    editQuoteBtn.onclick = () => {
      if (quoteEdit.classList.contains("hidden")) {
        // Seed from the already-saved edit so repeated extensions build on each
        // other; fall back to the extracted quote only before any edit exists.
        quoteEdit.value = state.judgement.edited_outcome_quote || p.outcome_phrase || "";
        if (quoteText) quoteText.classList.add("hidden");
        quoteEdit.classList.remove("hidden");
        editQuoteBtn.textContent = "save edited quote";
        _lockChoices();
        quoteEdit.focus();
      } else {
        _saveQuote();
      }
    };

    // Auto-save when the user clicks/tabs away from the textarea. If focus is
    // moving to the edit/save button, let its own click toggle handle the save
    // so we don't run it twice.
    quoteEdit.addEventListener("blur", (e) => {
      if (e.relatedTarget === editQuoteBtn) return;
      _saveQuote();
    });
  }
}

function onChoice(btn) {
  // Block clicks while the quote edit textarea is open
  if (btn.closest(".choices")?.classList.contains("quote-edit-open")) return;

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
      state.judgement.repro_computation = null;
      state.judgement.repro_robustness = null;
    } else {
      if (wasAnswered) {
        unanswerGate(pairBody.querySelector("#gate-2"));
        unanswerGate(pairBody.querySelector("#gate-3"));
        pairBody.querySelector("#gate-3").classList.add("hidden");
        const cr = pairBody.querySelector("#outcome-correction");
        if (cr) cr.classList.add("hidden");
      }
      // Outcome taxonomy differs by type — clear any prior outcome picks and
      // switch gate-3 between the replication and reproduction selectors.
      state.judgement.outcome = null;
      state.judgement.corrected_outcome = null;
      state.judgement.repro_computation = null;
      state.judgement.repro_robustness = null;
      pairBody.querySelectorAll("#gate-3 .choice.selected").forEach(b => b.classList.remove("selected"));
      _applyOutcomeMode(pairBody);
      pairBody.querySelector("#gate-2").classList.remove("hidden");
    }
    pairBody.querySelector(".comment").classList.remove("hidden");
  } else if (btn.dataset.original) {
    state.judgement.original = btn.dataset.original;
    if (wasAnswered) {
      unanswerGate(pairBody.querySelector("#gate-3"));
      state.judgement.outcome = null;
      state.judgement.corrected_outcome = null;
      state.judgement.repro_computation = null;
      state.judgement.repro_robustness = null;
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
      } else if (!state.judgement.corrected_doi_o && !state.judgement.corrected_study_o) {
        // Correction was cleared (user changed away and came back) — reset to form state
        const ocForm    = oc.querySelector("#oc-form");
        const ocConfirm = oc.querySelector("#oc-saved-confirm");
        if (ocForm)    ocForm.classList.remove("hidden");
        if (ocConfirm) ocConfirm.classList.add("hidden");
      }
    }
    // Show gate-3 immediately unless "wrong paper" is selected and the
    // suggestion panel is still open (not yet saved or skipped).
    const gate3 = pairBody.querySelector("#gate-3");
    if (btn.dataset.original === "wrong") {
      const ocPanel   = pairBody.querySelector("#original-correction");
      const ocConfirm = pairBody.querySelector("#oc-saved-confirm");
      const resolved  = (ocConfirm && !ocConfirm.classList.contains("hidden")) ||
                        (!ocPanel  || ocPanel.classList.contains("hidden"));
      if (resolved) gate3.classList.remove("hidden");
      // else leave gate-3 hidden — save/skip handlers will reveal it
    } else {
      gate3.classList.remove("hidden");
    }
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
    // Now that the correct outcome is chosen, collapse gate-3.
    const correctMap = { success: "Success", failure: "Failed", mixed: "Mixed", uninformative: "Uninformative" };
    const label = `Mischaracterised → ${correctMap[btn.dataset.correctOutcome] || btn.dataset.correctOutcome}`;
    clearTimeout(_chipTimer);
    _chipTimer = setTimeout(() => answerGate(gate, label, "danger"), 300);
    return;
  } else if (btn.dataset.reproComp || btn.dataset.reproRobust) {
    // Reproduction outcome: two axes → combined string in corrected_outcome.
    // (Sibling de-select within each axis is handled at the top of onChoice.)
    if (btn.dataset.reproComp)   state.judgement.repro_computation = btn.dataset.reproComp;
    if (btn.dataset.reproRobust) state.judgement.repro_robustness  = btn.dataset.reproRobust;
    const comp = state.judgement.repro_computation, rob = state.judgement.repro_robustness;
    if (comp && rob) {
      state.judgement.corrected_outcome = _reproLabel(comp, rob);
      updateSubmitState(pairBody);
      clearTimeout(_chipTimer);
      _chipTimer = setTimeout(() => answerGate(gate, state.judgement.corrected_outcome, "success"), 300);
    } else {
      state.judgement.corrected_outcome = null;   // need both axes before it counts
      updateSubmitState(pairBody);
    }
    return;
  }

  updateSubmitState(pairBody);
  clearTimeout(_chipTimer);
  // Keep gate-3 open after "Mischaracterised" so the user can pick the correct
  // outcome inline; it collapses once they do (handled in the branch above).
  if (btn.dataset.outcome === "wrong") return;
  _chipTimer = setTimeout(() => answerGate(gate, getAnswerLabel(btn), getAnswerClass(btn)), 300);
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

// Reproduction outcome taxonomy (combined-string labels match the extractor).
const _REPRO_COMP   = { successful: "computationally successful", issues: "computational issues", not_checked: "computation not checked" };
const _REPRO_ROBUST = { robust: "robust", challenges: "robustness challenges", not_checked: "robustness not checked" };
const _reproLabel = (comp, rob) => `${_REPRO_COMP[comp]}, ${_REPRO_ROBUST[rob]}`;

// All 9 reproduction outcome combinations — must match the validator selector's strings.
const _REPRO_OUTCOMES = Object.keys(_REPRO_COMP)
  .flatMap(c => Object.keys(_REPRO_ROBUST).map(r => _reproLabel(c, r)));

// Build <option>s for the admin outcome <select> by type. Keeps the current value even
// if it isn't in the canonical list (e.g. legacy / cannot_be_determined) so nothing is lost.
function _outcomeOptionsFor(type, selected) {
  const base = type === "reproduction"
    ? _REPRO_OUTCOMES.slice()
    : ["success", "failure", "mixed", "uninformative", "descriptive"];
  if (selected && !base.includes(selected)) base.unshift(selected);
  return base.map(o =>
    `<option value="${escapeHtml(o)}" ${selected === o ? "selected" : ""}>${escapeHtml(fmtOutcome(o))}</option>`
  ).join("");
}

// Switch gate-3 between the replication outcome check and the reproduction
// 2-axis selector based on the chosen type.
function _applyOutcomeMode(pairBody) {
  const isRepro = state.judgement.type === "reproduction";
  const repl  = pairBody.querySelector("#replication-outcome");
  const repro = pairBody.querySelector("#repro-outcome");
  const label = pairBody.querySelector("#gate-3 .outcome-label");
  const q     = pairBody.querySelector("#gate-3 .question");
  if (repl)  repl.classList.toggle("hidden", isRepro);
  if (repro) repro.classList.toggle("hidden", !isRepro);
  if (label) label.classList.toggle("hidden", isRepro);   // extracted (replication) outcome label is N/A for reproductions
  if (q) {
    if (!q.dataset.replQuestion) q.dataset.replQuestion = q.textContent;
    q.textContent = isRepro
      ? "How did the reproduction turn out? Use the quote below for context."
      : q.dataset.replQuestion;
  }
}

function updateSubmitState(pairBody) {
  const j = state.judgement;
  const btnEl = (pairBody || document).querySelector("#submit-btn");
  if (j.no_access) {   // hard-mode report — submittable without judging
    if (btnEl) btnEl.disabled = false;
    return;
  }
  const outcomeReady = j.type === "reproduction"
    ? !!j.corrected_outcome                                          // both axes chosen
    : (j.outcome && (j.outcome !== "wrong" || j.corrected_outcome)); // replication flow
  const ready = j.type === "not_validation" || (j.type && j.original && outcomeReady);
  const btn = (pairBody || document).querySelector("#submit-btn");
  if (btn) btn.disabled = !ready;
}

async function onSkip() {
  const ok = await showDialog({
    title: "Skip this pair?",
    message: "You won't get points and it'll be re-served to others. If you resumed this study and would prefer a fresh one, skipping is the right choice.",
    buttons: [
      { label: "Skip →", value: true, primary: true },
      { label: "Cancel", value: false },
    ],
  });
  if (!ok) return;
  clearPairTimer();
  _clearDraft(state.currentPair?.pair_id);
  try {
    await api("/skip", "POST", {
      coder_id:  state.coder.coder_id,
      record_id: String(state.currentPair.record_id),
      pair_id:   state.currentPair.pair_id || null,
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

  const j = state.judgement;
  const p = state.currentPair;

  // Hard-mode "I cannot access this article" — report it (no judgement, no
  // points) and move on. The record leaves circulation for the admin queue.
  if (j.no_access) {
    _clearDraft(p.pair_id);
    try {
      await api("/restricted", "POST", { coder_id: state.coder.coder_id, record_id: String(p.record_id) });
      showToast("Sent to the review team.");
    } catch (e) {
      await showAlert(e.message);
      btn.disabled = false;
      return;
    }
    await loadNextPair(false);
    return;
  }

  const isNotValidation = j.type === "not_validation";
  const pType = (p.type || "").toLowerCase();
  const typeCheck = (!isNotValidation && j.type === pType) ? "correct" : "incorrect";
  const correctedType = isNotValidation ? "not_validation"
                      : (typeCheck === "incorrect" ? j.type : null);

  // "Can't tell" collapses to "incorrect" in the check columns, so preserve the
  // real intent in additional_checks — consensus uses it to route to review.
  const addl = {};
  if (!isNotValidation && j.original === "unsure") addl.was_unsure_original = true;
  if (!isNotValidation && j.outcome  === "unsure") addl.was_unsure_outcome  = true;

  const payload = {
    coder_id:  state.coder.coder_id,
    record_id: String(p.record_id),
    pair_id:   p.pair_id || null,
    type_check:     isNotValidation ? "incorrect" : typeCheck,
    original_check: isNotValidation ? "incorrect" : (j.original === "correct" ? "correct" : "incorrect"),
    outcome_check:  isNotValidation ? "incorrect" : (j.outcome  === "correct" ? "correct" : "incorrect"),
    additional_checks:       Object.keys(addl).length ? addl : null,
    corrected_type:          correctedType || null,
    corrected_doi_o:         j.corrected_doi_o   || null,
    corrected_study_o:       j.corrected_study_o || null,
    corrected_outcome:       j.corrected_outcome || null,
    corrected_outcome_quote: j.edited_outcome_quote || null,
    corrected_abstract:      j.edited_abstract || null,
    corrected_study_r:       j.corrected_study_r || null,
    corrected_url_r:         j.corrected_url_r || null,
    validator_notes:         j.comment || null,
  };

  // Assignment validation — resolves the record directly (double points),
  // submitted synchronously (not via the optimistic queue) and one-off.
  if (state.assignment) {
    try {
      const resp = await api("/assignment-judge", "POST", payload);
      _clearDraft(p.pair_id);
      _celebratePoints(resp.points_earned);
      state.assignment = null;
      $("#assignment-banner").classList.add("hidden");
      await refreshAssignments();
      await refreshStats();
      await loadNextPair();   // back to the normal/hard queue
    } catch (e) {
      await showAlert(e.message);
      btn.disabled = false;
    }
    return;
  }

  // Static (demo) mode: keep the simple synchronous submit.
  if (!_bufferEligible()) {
    try {
      const resp = await api("/judge", "POST", payload);
      _clearDraft(p.pair_id);
      _celebratePoints(resp.points_earned);
      await refreshAll();
    } catch (e) {
      await showAlert(e.message);
      btn.disabled = false;
    }
    return;
  }

  // Optimistic: queue the submit (sent + retried in the background), clear the
  // draft, and jump straight to the next buffered pair. Points land deferred-
  // but-accurate when the server confirms (see _processSubmitQueue).
  _clearDraft(p.pair_id);
  _enqueueSubmit(payload, p.study_r || p.title_r || p.doi_r || "this pair");
  await loadNextPair(false);   // don't resume the pair we just submitted
}

function submitOnboarding() {
  const j = state.judgement;
  const pair = state.currentPair;
  // Reproductions provide the outcome via the 2-axis selector (corrected_outcome),
  // not j.outcome — mirror updateSubmitState so they can submit.
  const outcomeReady = j.type === "reproduction" ? !!j.corrected_outcome : j.outcome;
  const ready = j.type === "not_validation" || (j.type && j.original && outcomeReady);
  if (!ready) return;
  const errors = evaluateOnboarding(pair, j);
  showOnboardingFeedback(pair, errors);
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
    body: "Read the title, authors, and abstract. Click the <em>DOI</em> link (next to the authors) or <em>Scholar</em> to open the full paper. If the title has a typo, use <em>fix typo</em>; if the abstract is missing or wrong, use <em>edit abstract</em>; and if the link is wrong or paywalled, use <em>suggest link</em> to propose a better one.",
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
    body: "The system found the original study being replicated. Confirm it's the right paper — most are correct. If it looks wrong, choose <em>Wrong paper</em> and a form will appear so you can suggest the correct DOI or title. You can also skip the suggestion if you're unsure.",
  },
  {
    sel: "#gate-3",
    also: [".pair-header"],
    title: "iii. Outcome check",
    body: "Does the outcome label and supporting quote match what the authors actually concluded? You can <em>edit the quote</em> to extend or correct it.",
  },
  {
    sel: ".note-section",
    side: "left",
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
let _tourDragged = false;   // true once the user drags the callout (until next step)

function _makeTourDraggable() {
  const callout = $("#tour-callout");
  if (!callout || callout._dragWired) return;
  callout._dragWired = true;
  let sx, sy, sl, st, dragging = false;
  callout.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, a, input, textarea")) return;   // not from controls
    const rect = callout.getBoundingClientRect();
    callout.style.transform = "";
    callout.style.left = rect.left + "px";
    callout.style.top  = rect.top + "px";
    sx = e.clientX; sy = e.clientY; sl = rect.left; st = rect.top;
    dragging = true; _tourDragged = true;
    callout.classList.add("tour-dragging");
    try { callout.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
  });
  callout.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const nl = Math.max(0, Math.min(sl + (e.clientX - sx), window.innerWidth  - callout.offsetWidth));
    const nt = Math.max(0, Math.min(st + (e.clientY - sy), window.innerHeight - callout.offsetHeight));
    callout.style.left = nl + "px";
    callout.style.top  = nt + "px";
  });
  const end = (e) => { dragging = false; callout.classList.remove("tour-dragging"); try { callout.releasePointerCapture(e.pointerId); } catch (_) {} };
  callout.addEventListener("pointerup", end);
  callout.addEventListener("pointercancel", end);
}

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
  _makeTourDraggable();
  showTourStep(0);
}

function showTourStep(idx) {
  _tourStep = idx;
  _tourDragged = false;   // each step re-positions; user can drag within a step
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
  positionTourCallout(target, step.side);
}

function positionTourCallout(target, side) {
  const callout = $("#tour-callout");
  if (_tourDragged) return;   // keep the user's dragged position
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

  let top, left;

  if (side === "left") {
    left = rect.left - CW - MARGIN;
    if (left < MARGIN) left = MARGIN;
    top = Math.max(MARGIN, Math.min(rect.top, vh - calloutH - MARGIN));
  } else {
    if (rect.bottom + calloutH + MARGIN < vh) {
      top = rect.bottom + MARGIN;
    } else {
      top = vh - calloutH - MARGIN;
    }
    top = Math.max(MARGIN, top);
    left = Math.max(MARGIN, Math.min(rect.left, vw - CW - MARGIN));
  }

  callout.style.top  = top + "px";
  callout.style.left = left + "px";
}

window.addEventListener("resize", () => {
  const callout = $("#tour-callout");
  if (!callout || callout.classList.contains("hidden")) return;
  const target = document.querySelector(".tour-highlight");
  const step = TOUR_STEPS[_tourStep];
  positionTourCallout(target || null, step?.side);
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

function _shakeAndHint(anchorEl, message) {
  const card = $("#pair-card");
  const existing = card?.querySelector(".unsaved-hint");
  if (existing) existing.remove();

  const hint = document.createElement("div");
  hint.className = "unsaved-hint";
  hint.textContent = message;

  // Shake the anchor button
  if (anchorEl) {
    anchorEl.classList.add("unsaved-shake");
    anchorEl.addEventListener("animationend", () => anchorEl.classList.remove("unsaved-shake"), { once: true });
  }

  // Insert the hint above the first gate (consistent position for all three warning types)
  const firstGate = card?.querySelector(".gate");
  if (firstGate) {
    firstGate.insertAdjacentElement("beforebegin", hint);
    hint.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } else if (card) {
    card.prepend(hint);
  }

  // Auto-remove after 6 seconds
  setTimeout(() => hint.remove(), 6000);
}

async function guardedSubmit() {
  // Check for unsaved edits in the three interactive sections
  const card = $("#pair-card");

  // "I cannot access this article" — this is a report, not a judgement, so skip
  // all the judgement guards (unsaved edits, abstract, too-fast).
  if (state.judgement.no_access) { await submitJudgement(); return; }

  // 1. Title correction panel open but not saved — block regardless of content
  const titleEdit = card?.querySelector("#title-edit");
  if (titleEdit && !titleEdit.classList.contains("hidden")) {
    _shakeAndHint(
      card.querySelector("#fix-title-btn"),
      'Title correction is not saved — please save it to proceed.'
    );
    return;
  }

  // 2. Paper suggestion open but not saved (oc-saved-confirm not visible AND inputs have content)
  const ocConfirm = card?.querySelector("#oc-saved-confirm");
  const ocDoi     = card?.querySelector("#corrected-doi-input");
  const ocStudy   = card?.querySelector("#corrected-study-input");
  const ocVisible = card?.querySelector("#original-correction");
  if (
    ocVisible && !ocVisible.classList.contains("hidden") &&
    ocConfirm && ocConfirm.classList.contains("hidden") &&
    ((ocDoi?.value.trim()) || (ocStudy?.value.trim()))
  ) {
    _shakeAndHint(
      card.querySelector("#oc-save-btn"),
      'You have unsaved content — click "Save suggestion" to keep it, or "Skip" to continue without saving.'
    );
    return;
  }

  // 3. Quote edit open but not saved
  const quoteEdit = card?.querySelector("#outcome-quote-edit");
  if (quoteEdit && !quoteEdit.classList.contains("hidden") && quoteEdit.value.trim()) {
    _shakeAndHint(
      card.querySelector("#edit-quote-btn"),
      'You have an unsaved quote edit — click "save edited quote" first.'
    );
    return;
  }

  // 4. Abstract edit open but not saved
  const abstractEdit = card?.querySelector("#abstract-edit");
  if (abstractEdit && !abstractEdit.classList.contains("hidden")) {
    _shakeAndHint(
      card.querySelector("#edit-abstract-btn"),
      'You have an unsaved abstract edit — click "save edited abstract" first.'
    );
    return;
  }

  // 5. Abstract must not be empty — required in normal mode. Hard-mode records
  // are expected to lack an abstract (the validator opens the full article).
  const hasAbstract = (state.currentPair?.abstract_r || "").trim() ||
                      (state.judgement.edited_abstract || "").trim();
  if (state.mode !== "hard" && !state.assignment && !hasAbstract) {
    _shakeAndHint(
      card?.querySelector("#edit-abstract-btn"),
      'This record has no abstract — please find and paste it in using "edit abstract" before submitting.'
    );
    return;
  }

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
  await submitJudgement();
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
    rawHtml: true,
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
let _adminFilter    = "all";
let _adminSearch    = "";
let _adminPage      = 1;
let _adminSort      = "";        // column key, "" = default ordering
let _adminSortDir   = "desc";    // "asc" | "desc"
let _adminEntryIds   = [];   // ordered record_ids on current page
let _adminCurrentIdx = -1;  // index of currently open record
let _adminDetailCache = {};  // record_id → preloaded detail data
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
    throw new Error(err.detail || res.statusText || `Server error (HTTP ${res.status})`);
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
  startIdleLogout();   // admins included
  const badge = $("#admin-handle-badge");
  if (badge) badge.textContent = _adminHandle ? `Signed in as ${_adminHandle}` : "";
  startMaintenanceSystem();
  fetchAdminEntries();
  // Populate the Restricted-access badge proactively so admins see the count.
  adminApi("/restricted").then(d => _updateRestrictedBadge(d.records || [])).catch(() => {});
}

function signOutAdmin() {
  _adminToken   = null;
  _adminHandle  = null;
  _adminTrusted = false;
  location.reload();
}

async function fetchAdminEntries(resetState = true) {
  const body = $("#admin-table-body");
  if (resetState) {
    body.innerHTML = '<tr><td colspan="10" class="admin-loading">Loading…</td></tr>';
    $("#admin-empty").classList.add("hidden");
  }

  try {
    const searchParam = _adminSearch ? `&search=${encodeURIComponent(_adminSearch)}` : "";
    const sortParam   = _adminSort ? `&sort=${_adminSort}&dir=${_adminSortDir}` : "";
    const data = await adminApi(
      `/entries?filter=${_adminFilter}&page=${_adminPage}&per_page=${ADMIN_PER_PAGE}${searchParam}${sortParam}`
    );
    renderAdminCounts(data.counts);
    renderAdminTable(data.entries, data.total);
    _updateSortIndicators();
    renderAdminPagination(data.total, data.page);
    if (resetState) {
      _adminEntryIds = data.entries.map(e => e.record_id);
      _adminDetailCache = {};
      preloadAdminDetails();
    } else {
      // Mid-review background refresh: rebuild the nav list from the fresh data so
      // it can't drift from the re-rendered table, but keep the admin on the record
      // they're currently viewing (re-find it by id). If it's no longer in the list
      // (e.g. it moved filters), clamp the index into range.
      const viewedId = _adminEntryIds[_adminCurrentIdx];
      _adminEntryIds = data.entries.map(e => e.record_id);
      const idx = _adminEntryIds.indexOf(viewedId);
      _adminCurrentIdx = idx >= 0 ? idx : Math.min(_adminCurrentIdx, _adminEntryIds.length - 1);
    }
  } catch (e) {
    if (resetState) {
      body.innerHTML = `<tr><td colspan="10" class="admin-loading">
        Error: ${escapeHtml(e.message)}
        <button id="admin-retry-btn" style="margin-left:0.75rem;font-size:0.78rem;padding:0.3rem 0.8rem;border-radius:999px;border:1px solid var(--ink);background:transparent;cursor:pointer;">↺ Retry</button>
      </td></tr>`;
      document.getElementById("admin-retry-btn")?.addEventListener("click", () => fetchAdminEntries());
    }
  }
}

function renderAdminCounts(counts) {
  $("#fc-all").textContent              = counts.all;
  $("#fc-pending-approval").textContent = counts.pending_approval;
  $("#fc-needs-review").textContent     = counts.needs_review;
  $("#fc-validated").textContent        = counts.validated;
  $("#fc-rejected").textContent         = counts.rejected ?? 0;
  const _fcReverted = $("#fc-reverted");
  if (_fcReverted) _fcReverted.textContent = counts.reverted ?? 0;
  const _fcAdminChecked = $("#fc-admin-checked");
  if (_fcAdminChecked) _fcAdminChecked.textContent = counts.admin_checked;
}

const STATUS_LABELS = {
  unvalidated:          { text: "Unvalidated",      cls: "status-unvalidated" },
  validation_inprogress:{ text: "In progress",      cls: "status-inprogress"  },
  consensus_reached:    { text: "Pending approval", cls: "status-consensus"   },
  validated:            { text: "Validated",         cls: "status-validated"   },
  need_review:          { text: "Needs review",      cls: "status-review"      },
  rejected:             { text: "Excluded",          cls: "status-rejected"    },
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
      e.has_v1  ? `<span class="val-badge" title="${escapeHtml(e.v1_handle || "Validator 1")}">V1</span>` : `<span class="val-badge val-badge-empty">—</span>`,
      e.has_v2  ? `<span class="val-badge" title="${escapeHtml(e.v2_handle || "Validator 2")}">V2</span>` : `<span class="val-badge val-badge-empty">—</span>`,
      e.has_llm ? `<span class="val-badge val-badge-llm" title="LLM validator">LLM</span>` : `<span class="val-badge val-badge-empty">—</span>`,
    ].join("");
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
    const noteFlag = e.admin_notes
      ? `<span class="admin-note-flag" title="${escapeHtml((e.note_saved_by ? e.note_saved_by + ": " : "") + e.admin_notes)}">📝</span>`
      : "";
    const ap = e.agreement_pct;
    const agreeCell = (ap == null)
      ? '<span class="agree-na">—</span>'
      : `<span class="agree-pct ${ap >= 100 ? "agree-full" : ap >= 67 ? "agree-mid" : "agree-low"}">${ap}%</span>`;
    const approveCell = e.validation_status === "consensus_reached"
      ? `<button class="admin-approve-btn" data-id="${e.record_id}">Approve ✓</button>`
      : `<span class="agree-na">—</span>`;
    const reviewCell = `<button class="admin-review-btn ghost-btn" data-id="${e.record_id}">Review →</button>`;
    const approvedBy = e.admin_name
      ? `<span style="font-size:0.8rem;color:var(--muted)">${escapeHtml(e.admin_name)}</span>`
      : "—";
    return `<tr>
      <td class="admin-cell-num">${offset + i + 1}</td>
      <td class="admin-cell-study" title="${(e.study_r || "").replace(/"/g, "&quot;")}">${escapeHtml(study)}${flags}${trustBadge}${needsAttentionFlag}${noteFlag}</td>
      <td>${escapeHtml(e.final_type || e.type || "—")}</td>
      <td>${escapeHtml(fmtOutcome(e.final_outcome || e.outcome) || "—")}</td>
      <td><span class="admin-status ${s.cls}">${s.text}</span></td>
      <td class="admin-cell-validators">${validators}</td>
      <td class="admin-cell-agree">${agreeCell}</td>
      <td>${approvedBy}</td>
      <td class="admin-cell-approve">${approveCell}</td>
      <td class="admin-cell-review">${reviewCell}</td>
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
    showToast("Entry approved.");
    fetchAdminEntries();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Approve ✓";
    await showAlert("Error: " + e.message);
  }
}

const PRELOAD_WINDOW = 15;

function preloadAdminDetails() {
  const start = Math.max(0, _adminCurrentIdx);
  const end   = Math.min(start + PRELOAD_WINDOW, _adminEntryIds.length);
  for (let i = start; i < end; i++) {
    const id = _adminEntryIds[i];
    if (id && !_adminDetailCache[id]) {
      (async () => {
        try { _adminDetailCache[id] = await adminApi(`/entries/${id}`); }
        catch (e) { /* ignore preload failures */ }
      })();
    }
  }
}

async function openAdminDetail(recordId) {
  const modal = $("#admin-detail-modal");
  const body  = $("#admin-detail-body");
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  _adminCurrentIdx = _adminEntryIds.indexOf(recordId);
  $("#admin-detail-title").textContent = "";

  if (_adminDetailCache[recordId]) {
    renderAdminDetail(_adminDetailCache[recordId]);
    return;
  }

  body.innerHTML = '<p class="faq-loading">Loading…</p>';
  try {
    const data = await adminApi(`/entries/${recordId}`);
    _adminDetailCache[recordId] = data;
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
  const overrideBanner = rec.admin_override
    ? `<div class="admin-override-banner">
         <strong>Admin override</strong> — this record was previously rejected by validators but was validated by ${escapeHtml(rec.admin_name || "an admin")}. Validator cards below reflect the original (rejected) submissions.
       </div>`
    : "";
  const v1  = rec.validator_1;
  const v2  = rec.validator_2;
  const llm = rec.llm_validator;
  const slots = data.queue_slots || [];
  const q1 = slots.find(s => s.validator_slot === "human_1") || {};
  const q2 = slots.find(s => s.validator_slot === "human_2") || {};

  const chk = (val) => val === "correct"
    ? `<span class="chk-ok">✓ correct</span>`
    : val === "incorrect"
    ? `<span class="chk-fail">✗ incorrect</span>`
    : `<span class="chk-na">—</span>`;

  const tierBadge = (tier) => {
    if (tier >= 2) return `<span class="tier-badge tier-senior">Senior</span>`;
    if (tier >= 1) return `<span class="tier-badge tier-trusted">Trusted</span>`;
    return "";
  };

  const fmtDate = (iso) => {
    if (!iso) return "";
    return new Date(iso).toLocaleDateString("en-GB", {day:"numeric", month:"short", year:"numeric"});
  };

  const doiLink = (doi) => doi
    ? `<a href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener">${escapeHtml(doi)}</a>`
    : "—";

  const humanCard = (label, v, qs = {}) => {
    if (!v) return `<div class="admin-val-card admin-val-empty"><div class="admin-val-label">${label}</div><p>Not yet submitted.</p></div>`;
    const tier = v.validator_tier ?? 0;
    const isSeniorReject = !!v.senior_reject;
    const queueId  = qs.queue_id  || null;
    const isFlagged  = !!qs.flagged;
    const flagReason = qs.flag_reason || null;
    const isNotVal = v.corrected_type === "not_validation";
    const who = v.validator_name ? escapeHtml(v.validator_name) : "validator";

    // Short inline row for enum-like fields (Type, Outcome)
    const shortRow = (fieldLabel, extracted, checkVal, corrDisplay) => {
      const agreed = checkVal === "correct";
      const extr = `<span class="chk-extr-val">${escapeHtml(String(extracted || "—"))}</span>`;
      if (agreed) return `<div class="chk-row"><span class="chk-label">${fieldLabel}</span>${extr}<span class="chk-ok">✓ ${who} agreed</span></div>`;
      const corrPart = corrDisplay
        ? `<span class="chk-correction">${corrDisplay}</span>`
        : (isNotVal ? `<span class="chk-na-note">n/a</span>` : "");
      const failLabel = corrDisplay ? `<span class="chk-fail">✗ ${who} suggests:</span>` : `<span class="chk-fail">✗</span>`;
      return `<div class="chk-row"><span class="chk-label">${fieldLabel}</span>${extr}${failLabel}${corrPart}</div>`;
    };

    // Long expandable block row for free-text fields (Original, etc.)
    const longRow = (fieldLabel, extracted, checkVal, corrVal) => {
      const agreed = checkVal === "correct";
      const verdict = agreed
        ? `<span class="chk-ok">✓ ${who} agreed</span>`
        : corrVal
          ? `<span class="chk-fail">✗ ${who} corrected:</span>`
          : `<span class="chk-fail">✗ ${who} flagged</span>`;
      const origBlock = extracted
        ? `<div class="chk-long-group"><span class="chk-long-tag">Extracted</span><span class="chk-long-val">${escapeHtml(extracted)}</span></div>` : "";
      const corrBlock = !agreed && corrVal
        ? `<div class="chk-long-group chk-long-group-diff"><span class="chk-long-tag">→ Suggests</span><span class="chk-long-val">${escapeHtml(corrVal)}</span></div>`
        : (!agreed && isNotVal ? `<span class="chk-na-note" style="margin-left:0.5rem">n/a (not a replication)</span>` : "");
      return `<div class="chk-row-long"><div class="chk-row-long-head"><span class="chk-label">${fieldLabel}</span>${verdict}</div>${origBlock}${corrBlock}</div>`;
    };

    // Edit-only block row for freeform corrections (Title fix, Quote, Abstract)
    const editRow = (fieldLabel, origVal, corrVal, collapsible = false) => {
      if (!corrVal) return "";
      const origBlock = origVal
        ? `<div class="chk-long-group"><span class="chk-long-tag">Extracted</span><span class="chk-long-val">${escapeHtml(origVal)}</span></div>` : "";
      const corrBlock = `<div class="chk-long-group chk-long-group-diff"><span class="chk-long-tag">→ Suggests</span><span class="chk-long-val">${escapeHtml(corrVal)}</span></div>`;
      if (collapsible) {
        const preview = escapeHtml(String(corrVal).length > 55 ? String(corrVal).substring(0, 55) + "…" : String(corrVal));
        return `<div class="chk-row-long">
          <details class="chk-collapsible">
            <summary class="chk-collapsible-summary">
              <span class="chk-label">${fieldLabel}</span>
              <span class="chk-edit-badge">✎ ${who} edited</span>
              <span class="chk-collapse-preview">${preview}</span>
            </summary>
            ${origBlock}${corrBlock}
          </details>
        </div>`;
      }
      return `<div class="chk-row-long">
        <div class="chk-row-long-head"><span class="chk-label">${fieldLabel}</span><span class="chk-edit-badge">✎ ${who} edited</span></div>
        ${origBlock}${corrBlock}
      </div>`;
    };

    const typeCorr = v.corrected_type ? escapeHtml(v.corrected_type === "not_validation" ? "not a replication" : v.corrected_type) : null;
    const doiCorrRow = v.corrected_doi_o ? `<div class="chk-row-long">
        <div class="chk-row-long-head"><span class="chk-label">Orig. DOI</span><span class="chk-fail">✗ ${who} corrected:</span></div>
        <div class="chk-long-group"><span class="chk-long-tag">Extracted</span><span class="chk-long-val">${escapeHtml(rec.doi_o || "—")}</span></div>
        <div class="chk-long-group chk-long-group-diff"><span class="chk-long-tag">→ Suggests</span><span class="chk-long-val">${doiLink(v.corrected_doi_o)}</span></div>
      </div>` : "";
    const repDoiCorrRow = v.corrected_doi_r ? `<div class="chk-row-long">
        <div class="chk-row-long-head"><span class="chk-label">Rep. DOI fix</span><span class="chk-edit-badge">✎ ${who} edited</span></div>
        <div class="chk-long-group"><span class="chk-long-tag">Extracted</span><span class="chk-long-val">${escapeHtml(rec.doi_r || "—")}</span></div>
        <div class="chk-long-group chk-long-group-diff"><span class="chk-long-tag">→ Suggests</span><span class="chk-long-val">${doiLink(v.corrected_doi_r)}</span></div>
      </div>` : "";
    const urlLink = (u) => `<a href="${escapeHtml(u)}" target="_blank" rel="noopener" class="doi-link">${escapeHtml(u.length > 50 ? u.substring(0, 50) + "…" : u)}</a>`;
    const repUrlCorrRow = v.corrected_url_r ? `<div class="chk-row-long">
        <div class="chk-row-long-head"><span class="chk-label">Link fix</span><span class="chk-edit-badge">✎ ${who} edited</span></div>
        <div class="chk-long-group"><span class="chk-long-tag">Extracted</span><span class="chk-long-val">${rec.url_r ? urlLink(rec.url_r) : "—"}</span></div>
        <div class="chk-long-group chk-long-group-diff"><span class="chk-long-tag">→ Suggests</span><span class="chk-long-val">${urlLink(v.corrected_url_r)}</span></div>
      </div>` : "";

    return `<div class="admin-val-card${isSeniorReject ? " admin-val-senior-reject" : ""}">
      <div class="admin-val-label">
        <span class="admin-val-label-text">
          ${label}${v.validator_name ? ` · <em>${escapeHtml(v.validator_name)}</em>` : ""}
          ${tierBadge(tier)}
          ${isSeniorReject ? `<span class="tier-badge tier-fast-reject">Fast Reject</span>` : ""}
        </span>
        ${queueId ? `<button class="val-flag-btn${isFlagged ? " flagged" : ""}" data-queue-id="${queueId}" title="${isFlagged ? "Unflag judgement" : "Flag judgement as problematic"}">🚩</button>` : ""}
      </div>
      ${isFlagged && flagReason ? `<div class="flag-reason-bar">🚩 Flagged: ${escapeHtml(flagReason)}</div>` : ""}
      <div class="admin-val-meta">${fmtDate(v.validated_at)}${v.points != null ? ` · +${v.points} pts` : ""}</div>
      <div class="admin-val-checks">
        ${shortRow("Type",    rec.type,    v.type_check,    typeCorr)}
        ${longRow("Orig. Title", rec.study_o, v.original_check, v.corrected_study_o || null)}
        ${doiCorrRow}
        ${shortRow("Outcome", fmtOutcome(rec.outcome), v.outcome_check, v.corrected_outcome ? escapeHtml(fmtOutcome(v.corrected_outcome)) : null)}
        ${editRow("Title fix",  rec.study_r,      v.corrected_study_r)}
        ${repDoiCorrRow}
        ${repUrlCorrRow}
        ${editRow("Quote",      rec.outcome_quote, v.corrected_outcome_quote, true)}
        ${editRow("Abstract",   rec.abstract_r,    v.corrected_abstract,      true)}
        ${v.validator_notes ? `<div class="chk-row-long"><div class="chk-row-long-head"><span class="chk-label">Notes</span></div><p class="chk-notes-text">${escapeHtml(v.validator_notes)}</p></div>` : ""}
      </div>
    </div>`;
  };

  const llmCard = (v) => {
    if (!v) return `<div class="admin-val-card admin-val-empty"><div class="admin-val-label">LLM</div><p>Not run.</p></div>`;
    const ctxLabel = v.context === "tiebreaker"
      ? `<span class="tier-badge tier-tiebreaker">Tiebreaker</span>`
      : v.context === "sanity_check"
      ? `<span class="tier-badge tier-sanity">Sanity Check</span>`
      : "";
    if (v.error) return `<div class="admin-val-card admin-val-error-card">
      <div class="admin-val-label">LLM ${ctxLabel}</div>
      <div class="admin-val-meta">${escapeHtml(v.model || "")} · ${fmtDate(v.validated_at)}</div>
      <div class="admin-val-error">Error: ${escapeHtml(v.error)}</div>
    </div>`;
    return `<div class="admin-val-card">
      <div class="admin-val-label">LLM ${ctxLabel}</div>
      <div class="admin-val-meta">${escapeHtml(v.model || "")} · ${fmtDate(v.validated_at)}${v.vote_score != null ? ` · score ${v.vote_score}` : ""}</div>
      <div class="admin-val-checks">
        <div class="chk-row"><span class="chk-label">Type</span>${chk(v.type_check)}${v.corrected_type ? `<span class="chk-correction">→ ${escapeHtml(v.corrected_type === "not_validation" ? "not a replication" : v.corrected_type)}</span>` : ""}</div>
        <div class="chk-row"><span class="chk-label">Original</span>${chk(v.original_check)}${v.corrected_doi_o ? `<span class="chk-correction">→ ${doiLink(v.corrected_doi_o)}</span>` : ""}</div>
        <div class="chk-row"><span class="chk-label">Outcome</span>${chk(v.outcome_check)}${v.corrected_outcome ? `<span class="chk-correction">→ ${escapeHtml(v.corrected_outcome)}</span>` : ""}</div>
      </div>
      ${v.notes ? `<div class="admin-val-reasoning"><strong>Reasoning:</strong> ${escapeHtml(v.notes)}</div>` : ""}
    </div>`;
  };

  const finalPreview = () => {
    // Uses outer final* variables computed above

    const changes = [
      rec.final_study_r        && rec.final_study_r        !== rec.study_r        ? ["Replication title", rec.study_r,        rec.final_study_r]        : null,
      rec.final_doi_r          && rec.final_doi_r          !== rec.doi_r          ? ["Replication DOI",   rec.doi_r,          rec.final_doi_r]          : null,
      rec.final_url_r          && rec.final_url_r          !== rec.url_r          ? ["Replication URL",   rec.url_r,          rec.final_url_r]          : null,
      rec.final_study_o        && rec.final_study_o        !== rec.study_o        ? ["Original title",    rec.study_o,        rec.final_study_o]        : null,
      rec.final_doi_o          && rec.final_doi_o          !== rec.doi_o          ? ["Original DOI",      rec.doi_o,          rec.final_doi_o]          : null,
      rec.final_type           && rec.final_type           !== rec.type           ? ["Type",              rec.type,           rec.final_type]           : null,
      rec.final_outcome        && rec.final_outcome        !== rec.outcome        ? ["Outcome",           fmtOutcome(rec.outcome),        fmtOutcome(rec.final_outcome)]        : null,
      rec.final_outcome_quote  && rec.final_outcome_quote  !== rec.outcome_quote  ? ["Quote",             rec.outcome_quote,  rec.final_outcome_quote]  : null,
      rec.final_abstract_r     && rec.final_abstract_r     !== rec.abstract_r     ? ["Abstract",          rec.abstract_r,     rec.final_abstract_r]     : null,
    ].filter(Boolean);

    const changesSection = changes.length > 0 ? `
      <details class="fp-changes">
        <summary class="fp-changes-summary">show changes (${changes.length} field${changes.length !== 1 ? "s" : ""} edited)</summary>
        <table class="fp-changes-table">
          <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
          <tbody>${changes.map(([field, before, after]) => `
            <tr>
              <td class="fp-ch-field">${field}</td>
              <td class="fp-ch-before">${escapeHtml((before || "—").substring(0, 80))}</td>
              <td class="fp-ch-after">${escapeHtml((after  || "—").substring(0, 80))}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </details>` : `<p class="fp-no-changes">No field corrections — raw values will be published as-is.</p>`;

    return `<div class="final-preview-card">
      <div class="final-preview-header">
        <span class="fp-title">Final Preview</span>
        <span class="fp-subtitle">what gets published to FLoRA if approved</span>
      </div>
      <div class="fp-fields">
        <div class="fp-row"><span class="fp-label">Replication</span><span class="fp-value">${escapeHtml(finalStudyR || "—")}</span></div>
        <div class="fp-row"><span class="fp-label">DOI</span><span class="fp-value">${doiLink(finalDoiR)} · ${fmtYear(rec.year_r)}</span></div>
        ${finalUrlR ? `<div class="fp-row"><span class="fp-label">URL</span><span class="fp-value"><a href="${escapeHtml(finalUrlR)}" target="_blank" rel="noopener" class="doi-link">${escapeHtml(finalUrlR.length > 50 ? finalUrlR.substring(0, 50) + "…" : finalUrlR)}</a></span></div>` : ""}
        <div class="fp-row fp-divider"></div>
        <div class="fp-row"><span class="fp-label">Original</span><span class="fp-value">${escapeHtml(finalStudyO || "—")}</span></div>
        <div class="fp-row"><span class="fp-label">DOI</span><span class="fp-value">${doiLink(finalDoiO)}</span></div>
        <div class="fp-row fp-divider"></div>
        <div class="fp-row"><span class="fp-label">Type</span><span class="fp-value">${escapeHtml(finalType || "—")}</span></div>
        <div class="fp-row"><span class="fp-label">Outcome</span><span class="fp-value">${escapeHtml(fmtOutcome(finalOutcome) || "—")}</span></div>
        ${finalQuote ? `<div class="fp-row fp-quote-row"><span class="fp-label">Quote</span><span class="fp-value fp-quote">"${escapeHtml(finalQuote)}"</span></div>` : ""}
        ${finalQuote ? `<div class="fp-row"><span class="fp-label">Quote source</span><span class="fp-value">${finalSource === "abstract" ? "Abstract" : finalSource === "full_text" ? "Full text" : "<em>auto-detect on save</em>"}${rec.out_quote_source_by ? ` <span class="fp-src-by">· set by ${escapeHtml(rec.out_quote_source_by)}</span>` : ""}</span></div>` : ""}
      </div>
      ${finalAbstractR ? `<details class="fp-abstract"><summary>Abstract${rec.final_abstract_r && rec.final_abstract_r !== rec.abstract_r ? " (edited)" : ""}</summary><p class="fp-abstract-text">${escapeHtml(finalAbstractR)}</p></details>` : ""}
      ${changesSection}
    </div>`;
  };

  // Final values for the unified edit form (pre-filled from consensus / admin corrections)
  const finalStudyR    = rec.final_study_r       || rec.study_r;
  const finalDoiR      = rec.final_doi_r         || rec.doi_r;
  const finalStudyO    = rec.final_study_o       || rec.study_o;
  const finalDoiO      = rec.final_doi_o         || rec.doi_o;
  const finalType      = rec.final_type          || rec.type;
  const finalOutcome   = rec.final_outcome       || rec.outcome;
  const finalQuote     = rec.final_outcome_quote || rec.outcome_quote;
  const finalAbstractR = rec.final_abstract_r    || rec.abstract_r;
  const finalUrlR      = rec.final_url_r         || rec.url_r;
  const finalSource    = rec.final_out_quote_source || rec.out_quote_source || "";


  const outcomeOpts = _outcomeOptionsFor(finalType, finalOutcome);
  const typeOpts = ["replication","reproduction"]
    .map((t) => `<option value="${t}" ${finalType === t ? "selected" : ""}>${t}</option>`).join("");

  const hasNotValidation =
    (v1 && v1.corrected_type === "not_validation") ||
    (v2 && v2.corrected_type === "not_validation");
  const notValWho = [v1 && v1.corrected_type === "not_validation" ? v1.validator_name || "Validator 1" : "",
                     v2 && v2.corrected_type === "not_validation" ? v2.validator_name || "Validator 2" : ""]
    .filter(Boolean).join(" and ");

  $("#admin-detail-title").textContent = (rec.study_r || rec.doi_r || "Entry Review").substring(0, 80);
  $("#admin-detail-body").innerHTML = `
    ${abstractBanner}
    ${overrideBanner}
    <div class="admin-detail-cols">
      <!-- Left: final preview + validator cards -->
      <div class="admin-detail-pair">
        ${finalPreview()}

        ${(() => {
          const lines = buildConsensusSummary(v1, v2, llm);
          return lines.length ? `<div class="consensus-summary">
            <div class="cs-title">Where validators landed</div>
            <ul class="cs-list">${lines.map(l => `<li>${escapeHtml(l)}</li>`).join("")}</ul>
          </div>` : "";
        })()}

        <div class="admin-val-cards">
          ${humanCard("Validator 1", v1, q1)}
          ${humanCard("Validator 2", v2, q2)}
          ${llmCard(llm)}
        </div>
      </div>

      <!-- Right: admin resolution form -->
      <div class="admin-resolve-form">
        <div class="admin-notes-box">
          <div class="admin-notes-header">
            <span class="admin-notes-title">Admin Notes</span>
            ${rec.note_saved_by ? `<span class="admin-notes-meta">Last saved by <strong>${escapeHtml(rec.note_saved_by)}</strong> · ${fmtDate(rec.note_saved_at)}</span>` : `<span class="admin-notes-meta">No notes yet</span>`}
          </div>
          <textarea id="admin-note-text" class="admin-textarea admin-notes-textarea" placeholder="Add a note for other admins — e.g. DOI doesn't resolve, waiting on a decision…">${escapeHtml(rec.admin_notes || "")}</textarea>
          <button id="admin-save-note-btn" class="btn-save-note">Save Note</button>
        </div>

        <h3>Admin Resolution</h3>

        ${rec.validation_status === "rejected" ? `
        <div class="not-val-decision">
          <p class="not-val-who">⚠ This record was <strong>rejected</strong> as not a replication${notValWho ? ` by <strong>${notValWho}</strong>` : ""}. Validation happens once — it won't be sent back for re-validation.</p>
          <p class="not-val-hint">Need to change it? Review and edit the fields below.</p>
          <div class="not-val-buttons">
            <button id="confirm-is-rep-btn" class="btn-outline">Review / edit fields</button>
            <button id="admin-skip-notval-btn" class="ghost-btn">Skip →</button>
          </div>
        </div>
        ` : hasNotValidation ? `
        <div class="not-val-decision">
          <p class="not-val-who">⚠ <strong>${notValWho}</strong> marked this as <strong>not a replication</strong>.</p>
          <p class="not-val-question">What is your decision?</p>
          <div class="not-val-buttons">
            <button id="confirm-reject-btn" class="btn-reject">✗ Confirm — Not a Replication</button>
            <button id="confirm-is-rep-btn" class="btn-outline">✓ Override — It IS a Replication</button>
            <button id="admin-skip-notval-btn" class="ghost-btn">Skip →</button>
          </div>
          <p class="not-val-hint">↑ Clicking Override will open the edit form so you can review and correct all fields before resolving.</p>
          <textarea id="ar-notes-quick" class="admin-textarea" placeholder="Notes (optional)" style="margin-top:0.75rem"></textarea>
        </div>
        ` : ""}

        <div id="ar-normal-form" class="${(hasNotValidation || rec.validation_status === "rejected") ? "hidden" : ""}"
             data-orig-study-r="${escapeHtml(finalStudyR || "")}"
             data-orig-doi-r="${escapeHtml(finalDoiR || "")}"
             data-orig-study-o="${escapeHtml(finalStudyO || "")}"
             data-orig-doi-o="${escapeHtml(finalDoiO || "")}"
             data-orig-type="${escapeHtml(finalType || "")}"
             data-orig-outcome="${escapeHtml(finalOutcome || "")}"
             data-orig-abstract-r="${escapeHtml(finalAbstractR || "")}"
             data-orig-url-r="${escapeHtml(finalUrlR || "")}">
          ${hasNotValidation
            ? `<p class="admin-resolve-hint">Fill in the correct values — this will override the "not a replication" call.</p>`
            : `<p class="admin-resolve-hint">Edit the final values directly and mark as resolved. Changes are auto-detected.</p>`}

          <label class="admin-form-label">Replication Title</label>
          <input id="ar-study-r" class="admin-input" value="${escapeHtml(finalStudyR || "")}">

          <label class="admin-form-label">Replication DOI</label>
          <input id="ar-doi-r" class="admin-input" value="${escapeHtml(finalDoiR || "")}">

          <label class="admin-form-label">Replication URL</label>
          <input id="ar-url-r" class="admin-input" value="${escapeHtml(finalUrlR || "")}" placeholder="https://…">

          <label class="admin-form-label">Original Title</label>
          <input id="ar-study-o" class="admin-input" value="${escapeHtml(finalStudyO || "")}">

          <label class="admin-form-label">Original DOI</label>
          <input id="ar-doi-o" class="admin-input" value="${escapeHtml(finalDoiO || "")}">

          <label class="admin-form-label">Type</label>
          <select id="ar-type-sel" class="admin-select">${typeOpts}</select>

          <label class="admin-form-label">Outcome</label>
          <select id="ar-outcome-sel" class="admin-select">${outcomeOpts}</select>

          <label class="admin-form-label">Outcome Quote</label>
          <textarea id="ar-quote" class="admin-textarea" placeholder="Outcome quote…">${escapeHtml(finalQuote || "")}</textarea>

          <label class="admin-form-label">Outcome Quote Source</label>
          <select id="ar-quote-source" class="admin-select">
            <option value="" ${!finalSource ? "selected" : ""}>Auto-detect (is the quote in the abstract?)</option>
            <option value="abstract" ${finalSource === "abstract" ? "selected" : ""}>Abstract — quote appears in the abstract</option>
            <option value="full_text" ${finalSource === "full_text" ? "selected" : ""}>Full text — quote is from the paper body</option>
          </select>

          <label class="admin-form-label">Abstract</label>
          <textarea id="ar-abstract-r" class="admin-textarea" style="min-height:120px" placeholder="Abstract…">${escapeHtml(finalAbstractR || "")}</textarea>

          <label class="admin-form-label">Admin notes</label>
          <textarea id="ar-notes" class="admin-textarea" placeholder="Notes for the record (optional)"></textarea>

          <div class="admin-resolve-actions">
            <button id="admin-resolve-btn" class="btn-primary" data-id="${rec.record_id}">Mark as Resolved →</button>
            ${["consensus_reached","need_review"].includes(rec.validation_status) ? `<button id="admin-flag-review-btn" class="btn-outline" data-id="${rec.record_id}">⚑ Flag for Review</button>` : ""}
            <button id="admin-skip-btn" class="ghost-btn">Skip →</button>
            <button id="admin-detail-cancel" class="ghost-btn">Cancel</button>
          </div>
          <div class="admin-reject-action">
            <button id="admin-reject-btn" class="btn-reject-outline">✗ Reject — Not a Replication</button>
          </div>
        </div>
      </div>
    </div>
  `;

  if (rec.validation_status === "rejected" || hasNotValidation) {
    $("#confirm-reject-btn")?.addEventListener("click", () => submitQuickReject(rec.record_id));
    $("#confirm-is-rep-btn")?.addEventListener("click", () => {
      $(".not-val-decision")?.classList.add("hidden");
      $("#ar-normal-form").classList.remove("hidden");
    });
    $("#admin-skip-notval-btn")?.addEventListener("click", () => advanceToNextAdminEntry());
  }
  $("#admin-save-note-btn")?.addEventListener("click", async () => {
    const btn = $("#admin-save-note-btn");
    const note = $("#admin-note-text").value.trim();
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      await adminApi(`/entries/${rec.record_id}/note`, "POST", { note: note || null });
      btn.textContent = "Saved ✓";
      const meta = btn.closest(".admin-notes-box").querySelector(".admin-notes-meta");
      if (meta) meta.innerHTML = `Last saved by <strong>${escapeHtml(_adminHandle || "admin")}</strong> · just now`;
      setTimeout(() => { btn.textContent = "Save Note"; btn.disabled = false; }, 2500);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Save Note";
      await showAlert("Error: " + e.message);
    }
  });
  // Switch the outcome options to match the chosen type (reproduction has its own scheme)
  $("#ar-type-sel")?.addEventListener("change", (ev) => {
    const sel = $("#ar-outcome-sel");
    if (sel) sel.innerHTML = _outcomeOptionsFor(ev.target.value, "");
  });
  $("#admin-resolve-btn")?.addEventListener("click", () => submitAdminResolve(rec.record_id));
  $("#admin-reject-btn")?.addEventListener("click", async () => {
    const btn = $("#admin-reject-btn");
    btn.disabled = true;
    btn.textContent = "Rejecting…";
    try {
      await adminApi(`/entries/${rec.record_id}/resolve`, "POST", {
        admin_name:     _adminHandle || "admin",
        type_check:     "incorrect",
        original_check: "incorrect",
        outcome_check:  "incorrect",
        corrected_type: "not_validation",
        admin_notes:    $("#ar-notes")?.value.trim() || null,
      });
      showToast("Record rejected — marked as not a replication.");
      await advanceToNextAdminEntry();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "✗ Reject — Not a Replication";
      await showAlert("Error: " + e.message);
    }
  });
  $("#admin-detail-cancel")?.addEventListener("click", closeAdminDetail);
  $("#admin-skip-btn")?.addEventListener("click", () => advanceToNextAdminEntry());

  document.querySelectorAll(".val-flag-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const qId = btn.dataset.queueId;
      const isCurrentlyFlagged = btn.classList.contains("flagged");

      // When unflagging, skip the reason dialog
      let reason = "";
      if (!isCurrentlyFlagged) {
        const rawHtml = `<label style="display:block;text-align:left;margin-bottom:0.5rem;font-size:0.9rem;color:var(--ink-soft)">Reason <span style="color:var(--muted)">(optional — sent to validator)</span></label><textarea id="flag-reason-input" rows="3" style="width:100%;box-sizing:border-box;padding:0.5rem;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:0.9rem;resize:vertical" placeholder="e.g. Outcome check seems incorrect — please review the abstract again."></textarea>`;
        const confirmed = await showDialog({
          icon: "🚩",
          title: "Flag this judgement",
          message: rawHtml,
          rawHtml: true,
          buttons: [
            { label: "Cancel", value: false },
            { label: "Flag", value: true, primary: true },
          ],
          layout: "row",
        });
        if (!confirmed) return;
        reason = ($("#flag-reason-input")?.value || "").trim();
      }

      btn.disabled = true;
      try {
        const resp = await adminApi(`/queue/${qId}/flag`, "POST", { reason });
        btn.classList.toggle("flagged", resp.flagged);
        btn.title = resp.flagged ? "Unflag judgement" : "Flag judgement as problematic";
        // Bust cache so re-opening reflects new state
        const cachedRecord = Object.values(_adminDetailCache).find(d =>
          (d.queue_slots || []).some(s => s.queue_id === qId)
        );
        if (cachedRecord) {
          const slot = cachedRecord.queue_slots.find(s => s.queue_id === qId);
          if (slot) {
            slot.flagged = resp.flagged;
            slot.flag_reason = resp.flagged ? (reason || null) : null;
          }
        }
      } catch (e) {
        await showAlert("Error: " + e.message);
      } finally {
        btn.disabled = false;
      }
    });
  });

  const _flagReviewBtn = $("#admin-flag-review-btn");
  if (_flagReviewBtn) {
    _flagReviewBtn.onclick = async () => {
      const btn = $("#admin-flag-review-btn");
      const recordId = btn.dataset.id;
      const notes = $("#ar-notes")?.value.trim() || null;
      btn.disabled = true;
      btn.textContent = "Flagging…";
      try {
        await adminApi(`/entries/${recordId}/flag-review`, "POST", notes ? { admin_notes: notes } : {});
        showToast("Record flagged for review.");
        await advanceToNextAdminEntry();
      } catch (e) {
        showAlert(e.message || "Failed to flag record.");
        btn.disabled = false;
        btn.textContent = "⚑ Flag for Review";
      }
    };
  }
}

async function submitQuickReject(recordId) {
  const btn = $("#confirm-reject-btn");
  btn.disabled = true;
  btn.textContent = "Rejecting…";
  try {
    await adminApi(`/entries/${recordId}/resolve`, "POST", {
      admin_name:     _adminHandle || "admin",
      type_check:     "incorrect",
      original_check: "incorrect",
      outcome_check:  "incorrect",
      corrected_type: "not_validation",
      admin_notes:    $("#ar-notes-quick")?.value.trim() || null,
    });
    showToast("Marked as not a replication.");
    await advanceToNextAdminEntry();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "✗ Confirm — Not a Replication";
    await showAlert("Error: " + e.message);
  }
}

async function submitAdminResolve(recordId) {
  const btn = $("#admin-resolve-btn");
  btn.disabled = true;
  btn.textContent = "Saving…";

  const form = $("#ar-normal-form");
  const newStudyR    = $("#ar-study-r").value.trim();
  const newDoiR      = $("#ar-doi-r").value.trim();
  const newUrlR      = $("#ar-url-r").value.trim();
  const newStudyO    = $("#ar-study-o").value.trim();
  const newDoiO      = $("#ar-doi-o").value.trim();
  const newType      = $("#ar-type-sel").value;
  const newOutcome   = $("#ar-outcome-sel").value;
  const newQuote     = $("#ar-quote").value.trim();
  const newAbstractR = $("#ar-abstract-r").value.trim();

  const origStudyR    = form.dataset.origStudyR;
  const origDoiR      = form.dataset.origDoiR;
  const origUrlR      = form.dataset.origUrlR;
  const origStudyO    = form.dataset.origStudyO;
  const origDoiO      = form.dataset.origDoiO;
  const origType      = form.dataset.origType;
  const origOutcome   = form.dataset.origOutcome;
  const origAbstractR = form.dataset.origAbstractR;

  const typeChanged     = newType      !== origType;
  const origChanged     = newStudyO    !== origStudyO || newDoiO !== origDoiO;
  const outcomeChanged  = newOutcome   !== origOutcome;
  const studyRChanged   = newStudyR    !== origStudyR;
  const doiRChanged     = newDoiR      !== origDoiR;
  const urlRChanged     = newUrlR      !== origUrlR;
  const abstractChanged = newAbstractR !== origAbstractR;

  const body = {
    admin_name:              _adminHandle || "admin",
    type_check:              typeChanged    ? "incorrect" : "correct",
    original_check:          origChanged    ? "incorrect" : "correct",
    outcome_check:           outcomeChanged ? "incorrect" : "correct",
    corrected_type:          typeChanged     ? newType                  : null,
    corrected_doi_o:         origChanged     ? (newDoiO      || null)   : null,
    corrected_study_o:       origChanged     ? (newStudyO    || null)   : null,
    corrected_outcome:       outcomeChanged  ? newOutcome               : null,
    corrected_outcome_quote: newQuote        || null,
    corrected_study_r:       studyRChanged   ? (newStudyR    || null)   : null,
    corrected_doi_r:         doiRChanged     ? (newDoiR      || null)   : null,
    corrected_url_r:         urlRChanged     ? (newUrlR      || null)   : null,
    corrected_abstract_r:    abstractChanged ? (newAbstractR || null)   : null,
    out_quote_source:        $("#ar-quote-source")?.value || null,
    admin_notes:             $("#ar-notes").value.trim() || null,
  };

  try {
    await adminApi(`/entries/${recordId}/resolve`, "POST", body);
    showToast("Entry resolved.");
    await advanceToNextAdminEntry();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Mark as Resolved →";
    await showAlert("Error: " + e.message);
  }
}

async function advanceToNextAdminEntry() {
  if (_adminCurrentIdx < 0) { closeAdminDetail(); return; }
  const currentId = _adminEntryIds[_adminCurrentIdx];
  _adminEntryIds.splice(_adminCurrentIdx, 1);
  delete _adminDetailCache[currentId];

  if (_adminEntryIds.length === 0) {
    closeAdminDetail();
    return;
  }
  const nextIdx = Math.min(_adminCurrentIdx, _adminEntryIds.length - 1);
  _adminCurrentIdx = nextIdx;
  await openAdminDetail(_adminEntryIds[nextIdx]);

  // Background: extend preload window + sync table without resetting review state
  preloadAdminDetails();
  fetchAdminEntries(false);
}

function closeAdminDetail() {
  $("#admin-detail-modal").classList.add("hidden");
  const flagsOpen = !$("#validator-flags-modal").classList.contains("hidden");
  if (!flagsOpen) {
    document.body.style.overflow = "";
    fetchAdminEntries();
  }
}

/* ---------- Admin tabs ---------- */
function switchAdminTab(tab) {
  $("#admin-tab-entries").classList.toggle("hidden",    tab !== "entries");
  $("#admin-tab-stats").classList.toggle("hidden",      tab !== "stats");
  $("#admin-tab-admins").classList.toggle("hidden",     tab !== "admins");
  $("#admin-tab-dashboard").classList.toggle("hidden",  tab !== "dashboard");
  $("#admin-tab-restricted").classList.toggle("hidden", tab !== "restricted");
  $("#admin-tab-messages").classList.toggle("hidden",   tab !== "messages");
  $("#admin-tabs").querySelectorAll(".admin-tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  if (tab === "stats")      fetchAdminStats();
  if (tab === "admins")     { fetchAdminAdmins(); fetchAdminBannerStatus(); }
  if (tab === "dashboard")  fetchAdminDashboard();
  if (tab === "restricted") fetchAdminRestricted();
  if (tab === "messages")   fetchAdminMessages();
}

/* ---------- Admin: Restricted-access queue ---------- */
async function fetchAdminRestricted() {
  const body = $("#admin-restricted-body");
  if (!body) return;
  body.innerHTML = `<p class="admin-loading">Loading…</p>`;
  try {
    await _ensureValidatorList();
    const data = await adminApi("/restricted");
    renderRestricted(data.records || []);
    _updateRestrictedBadge(data.records || []);
  } catch (e) {
    body.innerHTML = `<p class="faq-error">Could not load restricted records (${escapeHtml(e.message)}).</p>`;
  }
}

function _updateRestrictedBadge(records) {
  const badge = $("#admin-restricted-badge");
  if (!badge) return;
  const unassigned = records.filter(r => !r.assignee_id).length;
  badge.textContent = unassigned;
  badge.classList.toggle("hidden", unassigned === 0);
}

function renderRestricted(records) {
  const body = $("#admin-restricted-body");
  if (!records.length) {
    body.innerHTML = `<p class="admin-empty">No restricted-access records.</p>`;
    return;
  }
  const fmtD = (iso) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—";
  const opts = _allValidators.map(v => `<option value="${v.id}">${escapeHtml(v.handle)}</option>`).join("");
  body.innerHTML = `
    <div class="admin-table-wrap">
      <table class="admin-table">
        <thead><tr><th>Study</th><th>Reported by</th><th>When</th><th>Assignment</th></tr></thead>
        <tbody>
          ${records.map(r => `
            <tr>
              <td>
                <div class="restr-title" title="${escapeHtml(r.study_r || "")}">${escapeHtml((r.study_r || r.record_id).slice(0, 70))}</div>
                <div class="restr-sub">${r.doi_r ? escapeHtml(r.doi_r) : "—"}${r.year_r ? " · " + fmtYear(r.year_r) : ""} · ${escapeHtml(fmtOutcome(r.outcome) || "—")}</div>
              </td>
              <td>${escapeHtml(r.reporter_handle || "—")}</td>
              <td style="white-space:nowrap">${fmtD(r.restricted_reported_at)}</td>
              <td>
                ${r.assignee_handle ? `<div class="restr-assigned">→ <strong>${escapeHtml(r.assignee_handle)}</strong>${r.assignment_status === "done" ? " · ✓ done" : ""}</div>` : ""}
                <div class="restr-assign-row">
                  <select class="admin-select restr-assign-select">${opts}</select>
                  <button class="btn-primary restr-assign-btn" data-record="${escapeHtml(r.record_id)}">${r.assignee_handle ? "Reassign" : "Assign"}</button>
                </div>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;

  body.querySelectorAll(".restr-assign-btn").forEach(btn => {
    btn.onclick = async () => {
      const sel = btn.closest("tr").querySelector(".restr-assign-select");
      const validatorId = parseInt(sel.value);
      if (!validatorId) return;
      btn.disabled = true; btn.textContent = "Assigning…";
      try {
        await adminApi("/assign", "POST", { record_id: btn.dataset.record, validator_id: validatorId });
        showToast("Assigned.");
        fetchAdminRestricted();
      } catch (e) {
        showToast("Error: " + e.message);
        btn.disabled = false; btn.textContent = "Assign";
      }
    };
  });
}

async function fetchAdminStats() {
  const body = $("#admin-stats-body");
  body.innerHTML = '<tr><td colspan="13" class="admin-loading">Loading…</td></tr>';
  try {
    const data = await adminApi("/stats");
    renderAdminSummary(data.summary);
    renderAdminStats(data.validators);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="13" class="admin-loading">Error: ${e.message}</td></tr>`;
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

/* ---------- Admin Dashboard ---------- */

let _dashCharts = {};

async function fetchAdminDashboard() {
  const body = $("#admin-dashboard-body");
  body.innerHTML = '<p class="admin-loading">Loading…</p>';
  try {
    const data = await adminApi("/dashboard");
    renderAdminDashboard(data);
  } catch (e) {
    body.innerHTML = `<p class="admin-loading">Error: ${escapeHtml(e.message)}</p>`;
  }
}

function _destroyDashCharts() {
  Object.values(_dashCharts).forEach((c) => { try { c.destroy(); } catch (_) {} });
  _dashCharts = {};
}

function renderAdminDashboard(d) {
  _destroyDashCharts();
  const p = d.pipeline;
  const q = d.quality;
  const o = d.outcomes;
  const c = d.corrections;

  const pct = (n, total) => total > 0 ? Math.round((n / total) * 100) + "%" : "—";
  const agreeRate = q.agreement_rate !== null && q.agreement_rate !== undefined
    ? Math.round(q.agreement_rate * 100) + "%"
    : "—";

  $("#admin-dashboard-body").innerHTML = `
    <div class="dash-section">
      <div class="dash-section-label">Pipeline Progress</div>
      <div class="dash-cards">
        <div class="dash-card dash-card-neutral">
          <span class="dash-card-val">${p.total}</span>
          <span class="dash-card-label">Total Records</span>
        </div>
        <div class="dash-card dash-card-muted">
          <span class="dash-card-val">${p.unvalidated}</span>
          <span class="dash-card-label">Unvalidated</span>
          <span class="dash-card-sub">${pct(p.unvalidated, p.total)}</span>
        </div>
        <div class="dash-card dash-card-amber">
          <span class="dash-card-val">${p.in_progress}</span>
          <span class="dash-card-label">In Progress</span>
          <span class="dash-card-sub">${pct(p.in_progress, p.total)}</span>
        </div>
        <div class="dash-card dash-card-amber">
          <span class="dash-card-val">${p.consensus_reached}</span>
          <span class="dash-card-label">Consensus Reached</span>
          <span class="dash-card-sub">${pct(p.consensus_reached, p.total)}</span>
        </div>
        <div class="dash-card dash-card-red">
          <span class="dash-card-val">${p.need_review}</span>
          <span class="dash-card-label">Needs Review</span>
          <span class="dash-card-sub">${pct(p.need_review, p.total)}</span>
        </div>
        <div class="dash-card dash-card-green">
          <span class="dash-card-val">${p.validated}</span>
          <span class="dash-card-label">Validated</span>
          <span class="dash-card-sub">${pct(p.validated, p.total)}</span>
        </div>
        <div class="dash-card dash-card-muted">
          <span class="dash-card-val">${p.rejected}</span>
          <span class="dash-card-label">Excluded</span>
          <span class="dash-card-sub">${pct(p.rejected, p.total)}</span>
        </div>
      </div>
    </div>

    <div class="dash-section">
      <div class="dash-section-label">Validation Quality</div>
      <div class="dash-cards">
        <div class="dash-card dash-card-neutral">
          <span class="dash-card-val">${q.total_judgements}</span>
          <span class="dash-card-label">Total Judgements</span>
        </div>
        <div class="dash-card dash-card-neutral">
          <span class="dash-card-val">${q.active_validators}</span>
          <span class="dash-card-label">Active Validators</span>
        </div>
        <div class="dash-card dash-card-neutral">
          <span class="dash-card-val">${q.records_with_2_validators}</span>
          <span class="dash-card-label">Both Slots Filled</span>
        </div>
        <div class="dash-card ${q.agreement_rate !== null && q.agreement_rate >= 0.75 ? "dash-card-green" : "dash-card-amber"}">
          <span class="dash-card-val">${agreeRate}</span>
          <span class="dash-card-label">Full Agreement Rate</span>
          <span class="dash-card-sub">${q.full_agreements} of ${q.records_with_2_validators} records</span>
        </div>
        <div class="dash-card dash-card-muted">
          <span class="dash-card-val">${p.tiebreakers}</span>
          <span class="dash-card-label">Tiebreakers</span>
        </div>
        <div class="dash-card dash-card-muted">
          <span class="dash-card-val">${p.admin_overrides}</span>
          <span class="dash-card-label">Admin Overrides</span>
        </div>
      </div>
    </div>

    <div class="dash-section">
      <div class="dash-section-label">Disagreements <span class="dash-chart-sub">(articles — hover a row for the confusion matrix)</span></div>
      <div class="disagree-toggle">
        <button class="disagree-tab active" data-view="validator">Validator vs Validator</button>
        <button class="disagree-tab" data-view="pipeline">Pipeline vs Final</button>
      </div>
      <div id="disagree-body"></div>
    </div>

    <div class="dash-charts">
      <div class="dash-chart-box">
        <div class="dash-chart-title">Outcome Distribution <span class="dash-chart-sub">(validated records)</span></div>
        <div class="dash-chart-canvas-wrap">
          <canvas id="dash-outcome-chart"></canvas>
        </div>
      </div>
    </div>
  `;

  _dashData = d;
  _renderDisagree("validator");
  $("#admin-dashboard-body").querySelectorAll(".disagree-tab").forEach((t) => {
    t.onclick = () => {
      $("#admin-dashboard-body").querySelectorAll(".disagree-tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      _renderDisagree(t.dataset.view);
    };
  });

  const OUTCOME_COLORS = {
    success:       "#4a6b3e",
    failure:       "#a83232",
    mixed:         "#b88019",
    uninformative: "#7a6e5f",
    descriptive:   "#4a6a8a",
  };
  const outcomeLabels = Object.keys(o).map((k) => k.charAt(0).toUpperCase() + k.slice(1));
  const outcomeData   = Object.values(o);
  const outcomeColors = Object.keys(o).map((k) => OUTCOME_COLORS[k] || "#999");

  _dashCharts.outcome = new Chart($("#dash-outcome-chart"), {
    type: "doughnut",
    data: {
      labels:   outcomeLabels,
      datasets: [{ data: outcomeData, backgroundColor: outcomeColors, borderWidth: 2, borderColor: "#f4efe6" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { font: { family: "'Inter Tight', sans-serif", size: 12 }, padding: 14, color: "#36361a" } },
      },
      cutout: "58%",
    },
  });

}

let _dashData = null;

function _renderMatrix(m, rowLabel, colLabel) {
  if (!m || !m.labels || !m.labels.length) return '<p class="mx-empty">No data.</p>';
  const lab = (s) => escapeHtml(fmtOutcome(s) || s);
  const head = m.labels.map((l) => `<th>${lab(l)}</th>`).join("");
  const rows = m.labels.map((rl, i) =>
    `<tr><th class="mx-rowlab">${lab(rl)}</th>` +
    m.labels.map((cl, j) => {
      const n = m.grid[i][j];
      return `<td class="${i === j ? "mx-diag" : (n > 0 ? "mx-off" : "")}">${n || ""}</td>`;
    }).join("") + `</tr>`
  ).join("");
  return `<div class="mx-axes">${escapeHtml(rowLabel)} / ${escapeHtml(colLabel)}</div>
    <table class="mx-table"><tr><th></th>${head}</tr>${rows}</table>`;
}

function _renderDisagree(view) {
  const dd = _dashData && _dashData.disagreements;
  const body = $("#disagree-body");
  if (!dd || !body) return;
  const plural = (n) => (n !== 1 ? "s" : "");

  if (view === "validator") {
    const dims = [["type", "Type"], ["original", "Original"], ["outcome", "Outcome"]];
    body.innerHTML =
      `<p class="disagree-caption">${dd.validator.total_records} articles with both validators · counts = records where V1 and V2 differ</p>` +
      dims.map(([k, label]) => {
        const x = dd.validator[k];
        const n = (x.validated || 0) + (x.unvalidated || 0);
        return `<div class="disagree-row">
          <span class="disagree-dim">${label}</span>
          <span class="disagree-count">${n} <span class="disagree-unit">article${plural(n)}</span>
            <span class="disagree-split">${x.validated} validated · ${x.unvalidated} open</span></span>
          <div class="matrix-popover">${_renderMatrix(x.matrix, "V1 ↓", "V2 →")}</div>
        </div>`;
      }).join("");
  } else {
    const dims = [["type", "Type"], ["outcome", "Outcome"]];
    body.innerHTML =
      `<p class="disagree-caption">${dd.pipeline.total_validated} validated articles · counts = where the final value differs from the pipeline's extracted value</p>` +
      dims.map(([k, label]) => {
        const x = dd.pipeline[k];
        return `<div class="disagree-row">
          <span class="disagree-dim">${label}</span>
          <span class="disagree-count">${x.count} <span class="disagree-unit">article${plural(x.count)}</span></span>
          <div class="matrix-popover">${_renderMatrix(x.matrix, "Extracted ↓", "Final →")}</div>
        </div>`;
      }).join("") +
      `<div class="disagree-row disagree-row-nomatrix">
        <span class="disagree-dim">Original</span>
        <span class="disagree-count">${dd.pipeline.original.count} <span class="disagree-unit">article${plural(dd.pipeline.original.count)}</span>
          <span class="disagree-split">original DOI corrected</span></span>
      </div>`;
  }
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
      <td><strong class="validator-name-cell" title="${v.email ? escapeHtml(v.email) : ""}" style="${v.email ? "cursor:help" : ""}">${escapeHtml(v.handle)}</strong></td>
      <td>
        <button class="tier-cycle-btn" data-id="${v.id}" data-tier="${v.validator_tier}"
                title="${["Click to promote to Trusted","Click to promote to Senior","Click to reset to Regular"][v.validator_tier]}">
          ${["—","⭐ Trusted","★★ Senior"][v.validator_tier]}
        </button>
      </td>
      <td style="color:var(--muted);font-size:0.8rem">${v.joined ? new Date(v.joined).toLocaleDateString("en-GB", {day:"numeric",month:"short",year:"numeric"}) : "—"}</td>
      <td>${v.total_judgements}</td>
      <td class="admin-time-cell">${fmtMin(v.avg_min)}</td>
      <td class="admin-time-cell">${fmtMin(v.median_min)}</td>
      <td class="admin-time-cell" style="color:var(--green)">${fmtMin(v.min_min)}</td>
      <td class="admin-time-cell" style="color:var(--muted)">${fmtMin(v.max_min)}</td>
      <td class="admin-flag-cell">${v.flagged_count > 0 ? `<span class="flag-count-badge" data-validator-id="${v.id}" data-handle="${escapeHtml(v.handle)}">🚩 ${v.flagged_count}</span>` : "—"}</td>
      <td class="admin-approved-cell" style="color:var(--green);font-weight:600">${v.approved_count > 0 ? v.approved_count : "—"}</td>
      <td style="font-size:0.78rem;color:var(--muted)">${v.last_login_at ? new Date(v.last_login_at).toLocaleString("en-GB", {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}) : "—"}</td>
      <td><button class="compose-msg-btn ghost-btn" data-id="${v.id}" data-handle="${escapeHtml(v.handle)}" title="Send message to ${escapeHtml(v.handle)}">✉</button></td>
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

  body.querySelectorAll(".compose-msg-btn").forEach((btn) => {
    btn.onclick = () => openComposeDialog(parseInt(btn.dataset.id), btn.dataset.handle);
  });

  body.querySelectorAll(".flag-count-badge[data-validator-id]").forEach((badge) => {
    badge.style.cursor = "pointer";
    badge.onclick = () => openValidatorFlagsModal(parseInt(badge.dataset.validatorId), badge.dataset.handle);
  });
}

async function openValidatorFlagsModal(validatorId, handle) {
  const modal = $("#validator-flags-modal");
  const body  = $("#vflags-body");
  const title = $("#vflags-title");
  if (!modal) return;

  title.textContent = `Flagged items — ${handle}`;
  body.innerHTML = '<p class="admin-loading">Loading…</p>';
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";

  try {
    const data = await adminApi(`/validators/${validatorId}/flagged`);
    const items = data.items;
    if (!items.length) {
      body.innerHTML = '<p class="admin-loading">No flagged items.</p>';
      return;
    }
    const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—";
    body.innerHTML = `
      <table class="admin-table vflags-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Record</th>
            <th>Year</th>
            <th>Outcome</th>
            <th>Status</th>
            <th>Flag reason</th>
            <th>Judged</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((item, i) => `
            <tr class="vflags-row" data-record-id="${escapeHtml(item.record_id)}">
              <td class="admin-cell-num">${i + 1}</td>
              <td class="vflags-title" title="${escapeHtml(item.study_r || "")}">
                ${escapeHtml((item.study_r || item.record_id).slice(0, 60))}${(item.study_r || "").length > 60 ? "…" : ""}
              </td>
              <td style="font-size:0.8rem;color:var(--muted)">${item.year_r || "—"}</td>
              <td style="font-size:0.8rem">${escapeHtml(fmtOutcome(item.outcome) || "—")}</td>
              <td>${(() => { const s = STATUS_LABELS[item.validation_status] || { text: item.validation_status || "—", cls: "" }; return `<span class="admin-status ${s.cls}">${escapeHtml(s.text)}</span>`; })()}</td>
              <td class="vflags-reason">${item.flag_reason ? escapeHtml(item.flag_reason) : '<span style="color:var(--muted)">—</span>'}</td>
              <td style="font-size:0.78rem;color:var(--muted);white-space:nowrap">${fmtDate(item.validated_at)}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;

    body.querySelectorAll(".vflags-row").forEach(row => {
      row.style.cursor = "pointer";
      row.title = "Open record detail";
      row.addEventListener("click", () => {
        body.querySelectorAll(".vflags-row").forEach(r => r.classList.remove("vflags-row-active"));
        row.classList.add("vflags-row-active");
        openAdminDetail(row.dataset.recordId);
      });
    });
  } catch (e) {
    body.innerHTML = `<p class="admin-loading">Error: ${escapeHtml(e.message)}</p>`;
  }
}

$("#vflags-close")?.addEventListener("click", () => {
  $("#validator-flags-modal").classList.add("hidden");
  document.body.style.overflow = "";
});

$("#validator-flags-modal")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.classList.add("hidden");
    document.body.style.overflow = "";
  }
});

async function openComposeDialog(validatorId, handle) {
  const rawHtml = `
    <p style="text-align:left;margin-bottom:0.75rem;font-size:0.88rem;color:var(--muted)">To: <strong>${escapeHtml(handle)}</strong></p>
    <label style="display:block;text-align:left;margin-bottom:0.25rem;font-size:0.85rem;color:var(--ink-soft)">Subject</label>
    <input id="compose-subject" type="text" style="width:100%;box-sizing:border-box;padding:0.45rem 0.6rem;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:0.9rem;margin-bottom:0.75rem" placeholder="e.g. Feedback on your recent judgement">
    <label style="display:block;text-align:left;margin-bottom:0.25rem;font-size:0.85rem;color:var(--ink-soft)">Message</label>
    <textarea id="compose-body" rows="4" style="width:100%;box-sizing:border-box;padding:0.5rem;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:0.9rem;resize:vertical" placeholder="Write your message here…"></textarea>`;
  const confirmed = await showDialog({
    icon: "✉",
    title: "Send message",
    message: rawHtml,
    rawHtml: true,
    buttons: [
      { label: "Cancel", value: false },
      { label: "Send →", value: true, primary: true },
    ],
    layout: "row",
  });
  if (!confirmed) return;
  const subject = ($("#compose-subject")?.value || "").trim();
  const body = ($("#compose-body")?.value || "").trim();
  if (!subject || !body) { await showAlert("Subject and message are both required."); return; }
  try {
    await adminApi("/message", "POST", { validator_id: validatorId, subject, body });
    showToast("Message sent.");
  } catch (e) {
    await showAlert("Failed to send: " + e.message);
  }
}

/* ---------- Admin messages tab ---------- */

let _adminMsgSelectedId = null;

function _fmtRelTime(date) {
  const diff = Date.now() - date.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2)   return "just now";
  if (mins < 60)  return `${mins}m ago`;
  const hrs = Math.floor(diff / 3600000);
  if (hrs < 24)   return `${hrs}h ago`;
  const days = Math.floor(diff / 86400000);
  if (days < 7)   return `${days}d ago`;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

async function fetchAdminMessages() {
  const list = $("#msg-convo-list");
  if (!list) return;
  list.innerHTML = '<p class="admin-loading">Loading…</p>';
  _ensureValidatorList(); // prefetch for compose
  try {
    const data = await adminApi("/messages");
    renderAdminConversations(data.conversations);
    _updateAdminMsgBadge(data.conversations);
  } catch (e) {
    list.innerHTML = `<p class="admin-loading">Error: ${escapeHtml(e.message)}</p>`;
  }
}

function _updateAdminMsgBadge(conversations) {
  const badge = $("#admin-msg-badge");
  if (!badge) return;
  const total = (conversations || []).reduce((s, c) => s + (c.unread_count || 0), 0);
  if (total > 0) {
    badge.textContent = total > 99 ? "99+" : String(total);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function renderAdminConversations(conversations) {
  const list = $("#msg-convo-list");
  if (!list) return;
  if (!conversations.length) {
    list.innerHTML = '<p class="inbox-empty">No messages yet.</p>';
    return;
  }
  list.innerHTML = conversations.map(c => {
    const hasUnread  = c.unread_count > 0;
    const isSelected = c.thread_id === _adminMsgSelectedId;
    const time = c.last_activity ? _fmtRelTime(new Date(c.last_activity)) : "";
    const arrow = c.last_direction === "inbound" ? "↩ " : "";
    const preview = arrow + escapeHtml(c.preview || "");
    return `<div class="msg-convo-row${isSelected ? " msg-convo-active" : ""}${hasUnread ? " msg-convo-unread" : ""}"
              data-id="${c.thread_id}">
      ${hasUnread ? `<span class="msg-convo-dot" title="${c.unread_count} unread">●</span>` : ""}
      <div class="msg-convo-row-top">
        <span class="msg-convo-subject">${escapeHtml(c.subject || "(no subject)")}</span>
        <span class="msg-convo-time">${time}</span>
      </div>
      <div class="msg-convo-row-meta">
        <span class="msg-convo-validator">${escapeHtml(c.validator_handle)}</span>
        ${c.admin_name ? `<span class="msg-convo-admin-tag">via ${escapeHtml(c.admin_name)}</span>` : ""}
      </div>
      <div class="msg-convo-preview">${preview}</div>
    </div>`;
  }).join("");

  list.querySelectorAll(".msg-convo-row").forEach(row => {
    row.addEventListener("click", () => {
      _adminMsgSelectedId = parseInt(row.dataset.id);
      list.querySelectorAll(".msg-convo-row").forEach(r => r.classList.toggle("msg-convo-active", r === row));
      openThread(parseInt(row.dataset.id));
    });
  });

  if (_adminMsgSelectedId) {
    const selected = list.querySelector(`.msg-convo-row[data-id="${_adminMsgSelectedId}"]`);
    if (selected) selected.classList.add("msg-convo-active");
  }
}

async function openThread(threadId) {
  const panel = $("#msg-thread-panel");
  if (!panel) return;
  panel.innerHTML = '<p class="admin-loading">Loading…</p>';
  try {
    const data = await adminApi(`/thread/${threadId}?mark_read=1`);
    renderConversationThread(data.messages, data.handle, data.subject, data.admin_name, threadId);
    const listData = await adminApi("/messages");
    renderAdminConversations(listData.conversations);
    _updateAdminMsgBadge(listData.conversations);
  } catch (e) {
    panel.innerHTML = `<p class="admin-loading">Error: ${escapeHtml(e.message)}</p>`;
  }
}

function renderConversationThread(messages, handle, subject, adminName, threadId) {
  const panel = $("#msg-thread-panel");
  if (!panel) return;
  const fmtFull = (iso) => new Date(iso).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  panel.innerHTML = `
    <div class="msg-thread-header">
      <div class="msg-thread-subject">${escapeHtml(subject || "(no subject)")}</div>
      <div class="msg-thread-meta">
        <span class="msg-thread-validator">${escapeHtml(handle)}</span>
        ${adminName ? `<span class="msg-thread-admin">· via ${escapeHtml(adminName)}</span>` : ""}
      </div>
    </div>
    <div class="msg-bubbles-wrap" id="msg-bubbles-wrap">
      ${messages.length === 0
        ? `<p class="msg-bubbles-empty">No messages yet.</p>`
        : messages.map(msg => {
          const isOut  = msg.direction === "outbound";
          const sender = isOut ? (msg.sent_by || adminName || "Admin") : escapeHtml(handle);
          return `<div class="msg-bubble ${isOut ? "msg-bubble-out" : "msg-bubble-in"}">
            <div class="msg-bubble-body">${escapeHtml(msg.body).replace(/\n/g, "<br>")}</div>
            <div class="msg-bubble-meta">${escapeHtml(sender)} · ${fmtFull(msg.sent_at)}</div>
          </div>`;
        }).join("")}
    </div>
    <div class="msg-reply-bar">
      <textarea class="msg-reply-input" placeholder="Write a reply to ${escapeHtml(handle)}…" rows="1"></textarea>
      <button class="msg-reply-send btn-primary">Send</button>
    </div>`;

  const wrap     = panel.querySelector("#msg-bubbles-wrap");
  const textarea = panel.querySelector(".msg-reply-input");
  const sendBtn  = panel.querySelector(".msg-reply-send");

  if (wrap) wrap.scrollTop = wrap.scrollHeight;

  // Auto-grow textarea
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
  });

  const doSend = async () => {
    const text = textarea.value.trim();
    if (!text) return;
    sendBtn.disabled = true;
    textarea.disabled = true;
    try {
      const res = await adminApi(`/thread/${threadId}/reply`, "POST", { body: text });
      textarea.value = "";
      textarea.style.height = "auto";
      textarea.disabled = false;
      // Remove empty-state placeholder if present
      wrap.querySelector(".msg-bubbles-empty")?.remove();
      // Append bubble immediately
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble msg-bubble-out";
      bubble.innerHTML = `
        <div class="msg-bubble-body">${escapeHtml(text).replace(/\n/g, "<br>")}</div>
        <div class="msg-bubble-meta">${escapeHtml(_adminHandle || "Admin")} · ${fmtFull(res.sent_at)}</div>`;
      wrap.appendChild(bubble);
      wrap.scrollTop = wrap.scrollHeight;
      // Refresh conversation list so preview updates
      adminApi("/messages").then(d => {
        renderAdminConversations(d.conversations);
        _updateAdminMsgBadge(d.conversations);
      }).catch(() => {});
    } catch (e) {
      await showAlert("Failed to send: " + e.message);
      textarea.disabled = false;
    }
    sendBtn.disabled = false;
    textarea.focus();
  };

  sendBtn.addEventListener("click", doSend);
  textarea.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) doSend();
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

/* ---------- Admin broadcast banner ---------- */
async function fetchAdminBannerStatus() {
  const statusEl = $("#admin-banner-status");
  const msgEl    = $("#admin-banner-msg");
  if (!statusEl) return;
  try {
    const data = await (await fetch("/api/banner")).json();
    if (data.active && data.message) {
      statusEl.innerHTML = `<span style="color:var(--red)">● Live</span> — banner is currently shown to all users`;
      if (msgEl) msgEl.value = data.message;
    } else {
      statusEl.innerHTML = `<span style="color:var(--muted)">○ Inactive</span> — no banner currently active`;
      if (msgEl) msgEl.value = "";
    }
  } catch (_) {
    if (statusEl) statusEl.textContent = "Could not load banner status.";
  }
}

async function saveAdminBanner(active) {
  const msgEl = $("#admin-banner-msg");
  const msg   = msgEl ? msgEl.value.trim() : "";
  if (active && !msg) {
    await showAlert("Please enter a message before sending.");
    return;
  }
  try {
    await adminApi("/banner", "POST", { message: msg, active });
    await fetchAdminBannerStatus();
    showToast(active ? "Banner sent to all users." : "Banner cleared.");
  } catch (e) {
    await showAlert(e.message);
  }
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
    showToast("Admin account created.");
  } catch (e) {
    await showAlert("Error: " + e.message);
  }
  btn.disabled = false;
}

// ── Compose new admin message ────────────────────────────────────────────────

let _allValidators = [];

async function _ensureValidatorList() {
  if (_allValidators.length) return;
  try {
    const data = await adminApi("/validators");
    _allValidators = data.validators || [];
  } catch (_) {}
}

let _composeOutsideClick = null;   // single document click-outside handler for the picker

function openComposePanel() {
  const panel = $("#msg-thread-panel");
  if (!panel) return;
  _ensureValidatorList().then(() => {
    panel.innerHTML = `
      <div class="msg-compose-panel">
        <div class="msg-compose-header">
          <span class="msg-compose-title">New Message</span>
          <button class="msg-compose-cancel ghost-btn" id="msg-compose-cancel">Cancel</button>
        </div>
        <div class="msg-compose-field">
          <label class="msg-compose-label">To</label>
          <div class="msg-validator-picker" id="msg-validator-picker">
            <input type="text" class="msg-validator-search" id="msg-validator-search"
                   placeholder="Search validators…" autocomplete="off" />
            <div class="msg-validator-dropdown hidden" id="msg-validator-dropdown"></div>
          </div>
        </div>
        <div class="msg-compose-field">
          <label class="msg-compose-label">Subject</label>
          <input type="text" class="msg-compose-input" id="msg-compose-subject" placeholder="Subject…" maxlength="200" />
        </div>
        <div class="msg-compose-field msg-compose-field-grow">
          <label class="msg-compose-label">Message</label>
          <textarea class="msg-compose-textarea" id="msg-compose-body" placeholder="Write your message…" rows="6"></textarea>
        </div>
        <div class="msg-compose-actions">
          <button class="msg-compose-send" id="msg-compose-send">Send</button>
        </div>
      </div>`;

    let _selectedValidator = null;

    const searchEl  = $("#msg-validator-search");
    const dropdown  = $("#msg-validator-dropdown");

    function renderDropdown(query) {
      const q = query.trim().toLowerCase();
      const matches = q
        ? _allValidators.filter(v => v.handle.toLowerCase().includes(q) || (v.email || "").toLowerCase().includes(q))
        : _allValidators.slice(0, 50);
      // Pinned "All validators" broadcast option — shown when not searching or
      // when the query matches "all".
      const showAll = !q || "all validators".includes(q);
      const allItem = showAll
        ? `<div class="msg-vdrop-item msg-vdrop-all" data-id="all" data-handle="All validators">
             <span class="msg-vdrop-handle">📢 All validators</span>
             <span class="msg-vdrop-email">Send to every validator at once</span>
           </div>`
        : "";
      if (!matches.length && !allItem) {
        dropdown.innerHTML = `<div class="msg-vdrop-empty">No validators found</div>`;
      } else {
        dropdown.innerHTML = allItem + matches.map(v =>
          `<div class="msg-vdrop-item" data-id="${v.id}" data-handle="${escapeHtml(v.handle)}">
             <span class="msg-vdrop-handle">${escapeHtml(v.handle)}</span>
             ${v.email ? `<span class="msg-vdrop-email">${escapeHtml(v.email)}</span>` : ""}
           </div>`
        ).join("");
        dropdown.querySelectorAll(".msg-vdrop-item").forEach(item => {
          item.addEventListener("click", () => {
            const isAll = item.dataset.id === "all";
            _selectedValidator = { id: isAll ? "all" : parseInt(item.dataset.id), handle: item.dataset.handle };
            searchEl.value = item.dataset.handle;
            dropdown.classList.add("hidden");
          });
        });
      }
      dropdown.classList.remove("hidden");
    }

    searchEl.addEventListener("input", () => renderDropdown(searchEl.value));
    searchEl.addEventListener("focus", () => renderDropdown(searchEl.value));
    // Only ever keep one document-level "click outside → close" handler, so
    // reopening the compose panel doesn't stack duplicates.
    if (_composeOutsideClick) document.removeEventListener("click", _composeOutsideClick);
    _composeOutsideClick = function hideDropdown(e) {
      if (!$("#msg-validator-picker")?.contains(e.target)) {
        dropdown.classList.add("hidden");
        document.removeEventListener("click", hideDropdown);
        _composeOutsideClick = null;
      }
    };
    document.addEventListener("click", _composeOutsideClick);

    $("#msg-compose-cancel").addEventListener("click", () => {
      panel.innerHTML = `<p class="msg-thread-empty">Select a conversation to view messages.</p>`;
    });

    $("#msg-compose-send").addEventListener("click", async () => {
      const subject = ($("#msg-compose-subject")?.value || "").trim();
      const body    = ($("#msg-compose-body")?.value || "").trim();
      if (!_selectedValidator) return showToast("Please select a validator.");
      if (!subject)            return showToast("Please enter a subject.");
      if (!body)               return showToast("Please enter a message.");
      const isBroadcast = _selectedValidator.id === "all";
      const btn = $("#msg-compose-send");
      btn.disabled = true;
      btn.textContent = "Sending…";
      try {
        const payload = isBroadcast
          ? { broadcast: true, subject, body }
          : { validator_id: _selectedValidator.id, subject, body };
        const resp = await adminApi("/message", "POST", payload);
        showToast(isBroadcast ? `Message sent to ${resp.sent} validators.` : "Message sent.");
        const listData = await adminApi("/messages");
        renderAdminConversations(listData.conversations);
        _updateAdminMsgBadge(listData.conversations);
        // Open the newly created thread (last one for this validator). Broadcast
        // has no single thread, so just reset the panel.
        const newThread = isBroadcast ? null
          : (listData.conversations || []).find(c => c.validator_handle === _selectedValidator.handle);
        if (newThread) {
          _adminMsgSelectedId = newThread.thread_id;
          openThread(newThread.thread_id);
        } else {
          panel.innerHTML = `<p class="msg-thread-empty">Select a conversation to view messages.</p>`;
        }
      } catch (e) {
        showToast("Error: " + e.message);
        btn.disabled = false;
        btn.textContent = "Send";
      }
    });
  });
}

document.addEventListener("click", (e) => {
  if (e.target.id === "msg-compose-btn") openComposePanel();
});

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
  _adminSearch = "";
  const searchInput = $("#admin-search-input");
  if (searchInput) searchInput.value = "";
  fetchAdminEntries();
});

// Sortable Entries column headers.
$("#admin-entries-head")?.addEventListener("click", (e) => {
  const th = e.target.closest(".th-sort");
  if (!th) return;
  const col = th.dataset.sort;
  if (_adminSort === col) {
    _adminSortDir = _adminSortDir === "asc" ? "desc" : "asc";
  } else {
    _adminSort = col;
    _adminSortDir = "asc";
  }
  _adminPage = 1;
  fetchAdminEntries();
});

function _updateSortIndicators() {
  document.querySelectorAll("#admin-entries-head .th-sort").forEach((th) => {
    const active = th.dataset.sort === _adminSort;
    th.classList.toggle("th-sort-active", active);
    th.setAttribute("data-arrow", active ? (_adminSortDir === "asc" ? " ▲" : " ▼") : "");
  });
}

/* ---------- Admin search ---------- */
let _adminSearchTimer = null;
document.addEventListener("input", (e) => {
  if (e.target.id !== "admin-search-input") return;
  clearTimeout(_adminSearchTimer);
  _adminSearchTimer = setTimeout(() => {
    _adminSearch = e.target.value.trim();
    _adminPage = 1;
    fetchAdminEntries();
  }, 350);
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
