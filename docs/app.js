const state = {
  coder: null,
  currentPair: null,
  judgement: { type: null, original: null, outcome: null, comment: "" },
  mode: "online",
  pairs: null,
};

const $ = (sel) => document.querySelector(sel);

const STORAGE = {
  CODER: "flora.coder",
  JUDGEMENTS: "flora.judgements",
  CODERS: "flora.coders",
};

async function detectMode() {
  try {
    const r = await fetch("./api/leaderboard", { method: "GET" });
    if (r.ok) { state.mode = "online"; return; }
  } catch {}
  state.mode = "static";
  $("#banner").classList.remove("hidden");
  const r = await fetch("./pairs.json");
  state.pairs = await r.json();
}

async function api(path, method = "GET", body = null) {
  if (state.mode === "static") return staticApi(path, method, body);
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

function pointsFor(req) {
  if (req.type_judgement === "false_positive") return 5;
  let p = 10;
  if (req.original_judgement) p += 5;
  if (req.outcome_judgement) p += 5;
  if (req.comment && req.comment.trim()) p += 3;
  return p;
}

function staticApi(path, method, body) {
  const url = new URL("http://x" + path);
  const route = url.pathname;

  if (route === "/login") {
    const name = (body.name || "").trim();
    if (!name) throw new Error("Name required");
    const coders = readJSON(STORAGE.CODERS, {});
    if (!coders[name]) coders[name] = { coder_id: Date.now() + Math.floor(Math.random() * 1000), name };
    writeJSON(STORAGE.CODERS, coders);
    return Promise.resolve(coders[name]);
  }

  if (route === "/next-pair") {
    const cid = +url.searchParams.get("coder_id");
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    const judged = new Set(judgements.filter((j) => j.coder_id === cid).map((j) => j.pair_id));
    const remaining = state.pairs.filter((p) => !judged.has(p.pair_id));
    const total = state.pairs.length;
    const done = judged.size;
    if (!remaining.length) return Promise.resolve({ pair: null, done, total });
    const pair = remaining[Math.floor(Math.random() * remaining.length)];
    return Promise.resolve({ pair, judge_count: 0, done, total });
  }

  if (route === "/judge") {
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    if (judgements.find((j) => j.coder_id === body.coder_id && j.pair_id === body.pair_id)) {
      throw new Error("Already judged this pair");
    }
    const points = pointsFor(body);
    judgements.push({ ...body, points, created_at: new Date().toISOString() });
    writeJSON(STORAGE.JUDGEMENTS, judgements);
    const totalPts = judgements
      .filter((j) => j.coder_id === body.coder_id)
      .reduce((a, b) => a + b.points, 0);
    return Promise.resolve({ points_earned: points, total_points: totalPts, rank: rankIn(totalPts) });
  }

  if (route === "/leaderboard") {
    const coders = readJSON(STORAGE.CODERS, {});
    const idToName = {};
    Object.values(coders).forEach((c) => (idToName[c.coder_id] = c.name));
    const judgements = readJSON(STORAGE.JUDGEMENTS, []);
    const agg = {};
    judgements.forEach((j) => {
      if (!agg[j.coder_id]) agg[j.coder_id] = { points: 0, pairs: 0 };
      agg[j.coder_id].points += j.points;
      agg[j.coder_id].pairs += 1;
    });
    return Promise.resolve(
      Object.entries(agg)
        .map(([id, v]) => ({ name: idToName[id] || "anon", points: v.points, pairs: v.pairs }))
        .sort((a, b) => b.points - a.points || b.pairs - a.pairs)
    );
  }

  if (route === "/stats") {
    const cid = +url.searchParams.get("coder_id");
    const judgements = readJSON(STORAGE.JUDGEMENTS, []).filter((j) => j.coder_id === cid);
    const points = judgements.reduce((a, b) => a + b.points, 0);
    return Promise.resolve({
      done: judgements.length,
      points,
      total: state.pairs.length,
      rank: rankIn(points),
    });
  }

  throw new Error("Unknown static route: " + route);
}

function rankIn(points) {
  const judgements = readJSON(STORAGE.JUDGEMENTS, []);
  const totals = {};
  judgements.forEach((j) => (totals[j.coder_id] = (totals[j.coder_id] || 0) + j.points));
  return 1 + Object.values(totals).filter((p) => p > points).length;
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
$("#login-btn").onclick = doLogin;
$("#name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });

async function doLogin() {
  const name = $("#name-input").value.trim();
  if (!name) return;
  try {
    const resp = await api("/login", "POST", { name });
    state.coder = resp;
    localStorage.setItem(STORAGE.CODER, JSON.stringify(resp));
    enterGame();
  } catch (e) {
    alert(e.message);
  }
}

async function startup() {
  await detectMode();
  const stored = localStorage.getItem(STORAGE.CODER);
  if (stored) {
    try { state.coder = JSON.parse(stored); await enterGame(); } catch {}
  }
}
startup();

async function enterGame() {
  $("#login-screen").classList.add("hidden");
  $("#game-screen").classList.remove("hidden");
  $("#stat-name").textContent = state.coder.name;
  await refreshAll();
}

$("#logout-btn").onclick = () => {
  localStorage.removeItem(STORAGE.CODER);
  location.reload();
};

/* ---------- Refresh ---------- */
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
  if (!lb.length) {
    list.innerHTML = '<li style="opacity:0.5;font-style:italic">No scores yet</li>';
    return;
  }
  lb.slice(0, 10).forEach((entry) => {
    const li = document.createElement("li");
    if (entry.name === state.coder.name) li.classList.add("me");
    li.innerHTML = `<span class="lb-name">${escapeHtml(entry.name)}</span>
      <span class="lb-pts">${entry.points} · ${entry.pairs}p</span>`;
    list.appendChild(li);
  });
}

/* ---------- Pair flow ---------- */
async function loadNextPair() {
  const resp = await api(`/next-pair?coder_id=${state.coder.coder_id}`);
  if (!resp.pair) {
    $("#pair-card").classList.add("hidden");
    $("#done-screen").classList.remove("hidden");
    return;
  }
  $("#done-screen").classList.add("hidden");
  $("#pair-card").classList.remove("hidden");
  state.currentPair = resp.pair;
  state.judgement = { type: null, original: null, outcome: null, comment: "" };
  renderPair(resp.pair, resp.judge_count);
}

function renderPair(p, judgeCount) {
  const sysType = p.type || "?";
  const outcomeLabel = (p.outcome || "uninformative").toLowerCase();
  const url = p.url_r || (p.doi_r ? `https://doi.org/${p.doi_r}` : null);
  const oUrl = p.doi_o ? `https://doi.org/${p.doi_o}` : null;
  const hasQuote = p.outcome_phrase && p.outcome_phrase.trim();

  $("#pair-card").innerHTML = `
    <div class="pair-meta">
      <span class="meta-item">id <b>${escapeHtml(p.pair_id.slice(0, 8))}</b></span>
      <span class="meta-item system">system call <b>${escapeHtml(sysType)}</b></span>
      <span class="meta-item">${judgeCount} other coder${judgeCount === 1 ? "" : "s"}</span>
      ${p.link_confidence ? `<span class="meta-item">link conf <b>${escapeHtml(p.link_confidence)}</b></span>` : ""}
    </div>

    <h2>${escapeHtml(p.title_r || "(untitled)")}</h2>
    <div class="authors">${escapeHtml(p.authors_r || "?")} · ${escapeHtml(p.year_r || "?")}${p.journal_r ? " · " + escapeHtml(p.journal_r) : ""}</div>
    <div class="abstract">${escapeHtml(p.abstract_r || "(no abstract available)")}</div>
    ${url ? `<div class="link-row"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">→ open paper</a></div>` : ""}

    <div class="gate" id="gate-1">
      <h3><span class="gate-num">i.</span>Type check</h3>
      <p class="question">Is this paper actually validating a previous finding?</p>
      <div class="choices">
        <button class="choice success" data-type="replication">Replication<small>different data</small></button>
        <button class="choice success" data-type="reproduction">Reproduction<small>same data</small></button>
        <button class="choice danger" data-type="false_positive">False positive<small>neither</small></button>
      </div>
    </div>

    <div class="gate hidden" id="gate-2">
      <h3><span class="gate-num">ii.</span>Original check</h3>
      <p class="question">Does this match the paper actually being validated?</p>
      <div class="original-info">
        <div class="title">${escapeHtml(p.title_o || "(no title)")}</div>
        <div class="meta">
          ${escapeHtml(p.authors_o || "?")} · ${escapeHtml(p.year_o || "?")}
          ${oUrl ? ` · <a href="${escapeHtml(oUrl)}" target="_blank" rel="noopener">${escapeHtml(p.doi_o)}</a>` : ""}
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
        ${hasQuote ? `<div class="outcome-quote">"${escapeHtml(p.outcome_phrase)}"</div>` : '<p style="margin:0.4rem 0"><em>No outcome quote was extracted.</em></p>'}
        <small>source ${escapeHtml(p.out_quote_source || "?")}${p.outcome_confidence ? ` · conf ${escapeHtml(p.outcome_confidence)}` : ""}</small>
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
  `;

  $("#pair-card").querySelectorAll(".choice").forEach((b) => (b.onclick = () => onChoice(b)));
  $("#pair-card").querySelector(".comment").oninput = (e) => (state.judgement.comment = e.target.value);
  $("#submit-btn").onclick = submitJudgement;
}

function onChoice(btn) {
  const parent = btn.parentElement;
  parent.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
  btn.classList.add("selected");

  if (btn.dataset.type) {
    state.judgement.type = btn.dataset.type;
    if (btn.dataset.type === "false_positive") {
      $("#gate-2").classList.add("hidden");
      $("#gate-3").classList.add("hidden");
      state.judgement.original = null;
      state.judgement.outcome = null;
    } else {
      $("#gate-2").classList.remove("hidden");
    }
    document.querySelector(".comment").classList.remove("hidden");
  } else if (btn.dataset.original) {
    state.judgement.original = btn.dataset.original;
    $("#gate-3").classList.remove("hidden");
  } else if (btn.dataset.outcome) {
    state.judgement.outcome = btn.dataset.outcome;
  }
  updateSubmitState();
}

function updateSubmitState() {
  const j = state.judgement;
  const ready = j.type === "false_positive" || (j.type && j.original && j.outcome);
  $("#submit-btn").disabled = !ready;
}

async function submitJudgement() {
  if ($("#submit-btn").disabled) return;
  $("#submit-btn").disabled = true;
  try {
    const resp = await api("/judge", "POST", {
      coder_id: state.coder.coder_id,
      pair_id: state.currentPair.pair_id,
      type_judgement: state.judgement.type,
      original_judgement: state.judgement.original,
      outcome_judgement: state.judgement.outcome,
      comment: state.judgement.comment,
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
    $("#submit-btn").disabled = false;
  }
}

document.addEventListener("keydown", (e) => {
  if ($("#game-screen").classList.contains("hidden")) return;
  if (e.key === "Enter") {
    if (e.target.tagName === "TEXTAREA" && !(e.metaKey || e.ctrlKey)) return;
    e.preventDefault();
    submitJudgement();
  }
});
