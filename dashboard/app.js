/* =====================================================================
   TexWholesale Engine — dashboard client
   One file, page-routed via <body data-page="...">.
   ===================================================================== */

const API = {
  async req(method, path, body, isText = false) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      if (typeof body === "string") {
        opts.headers["Content-Type"] = "text/plain";
        opts.body = body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return isText ? res.text() : res.json();
  },
  get: (p) => API.req("GET", p),
  post: (p, b) => API.req("POST", p, b),
  postText: (p, b) => API.req("POST", p, b),
  put: (p, b) => API.req("PUT", p, b),
};

/* ---------- formatting ---------- */
const fmtMoney = (n) =>
  "$" + Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
const fmtMoney2 = (n) =>
  "$" + Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function scoreClass(s) {
  if (s >= 70) return "hot";
  if (s >= 45) return "warm";
  return "cold";
}

/* ---------- toast ---------- */
function toast(title, msg = "", kind = "") {
  let host = document.getElementById("toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "toasts";
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `<div class="tt">${title}</div>${msg ? `<div class="tm">${msg}</div>` : ""}`;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s, transform .3s";
    el.style.opacity = "0";
    el.style.transform = "translateX(40px)";
    setTimeout(() => el.remove(), 300);
  }, 3600);
}

/* ---------- modal ---------- */
function openModal(id) { document.getElementById(id)?.classList.add("open"); }
function closeModal(id) { document.getElementById(id)?.classList.remove("open"); }

/* ---------- nav: active link + health chip ---------- */
async function initChrome() {
  const page = document.body.dataset.page;
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.page === page) a.classList.add("active");
  });
  const chip = document.getElementById("health-chip");
  if (chip) {
    try {
      const h = await API.get("/api/health");
      const live = h.enabled_sources.length || h.demo_mode;
      chip.className = "status-chip" + (live ? "" : " off");
      chip.innerHTML = `<span class="led"></span>${
        h.demo_mode ? "DEMO MODE" : h.enabled_sources.length + " sources live"
      }`;
    } catch (_) {
      chip.className = "status-chip off";
      chip.innerHTML = `<span class="led"></span>offline`;
    }
  }
}

/* =====================================================================
   PAGE: home / dashboard
   ===================================================================== */
async function pageHome() {
  try {
    const s = await API.get("/api/stats");
    setText("stat-leads", s.total_leads);
    setText("stat-buyers", s.total_buyers);
    setText("stat-hot", s.hot_leads);
    const inPipe = Object.entries(s.pipeline)
      .filter(([k]) => k !== "Closed")
      .reduce((a, [, v]) => a + v, 0);
    setText("stat-pipeline", inPipe);
  } catch (e) {
    toast("Couldn't load stats", e.message, "err");
  }
  loadMarket();
}

async function loadMarket() {
  try {
    const data = await API.get("/api/market-stats");
    const labels = Object.keys(data.markets);
    const prices = labels.map((m) => data.markets[m].median_price);
    const dom = labels.map((m) => data.markets[m].dom);
    setText("market-source", data.source === "attom" ? "Live · ATTOM" : "Reference data");

    const ctx = document.getElementById("marketChart");
    if (ctx && window.Chart) {
      new Chart(ctx, {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "Median price",
              data: prices,
              backgroundColor: "rgba(45,212,255,0.55)",
              borderColor: "#2dd4ff",
              borderWidth: 1,
              borderRadius: 6,
              yAxisID: "y",
            },
            {
              label: "Days on market",
              data: dom,
              type: "line",
              borderColor: "#ffb020",
              backgroundColor: "rgba(255,176,32,0.2)",
              tension: 0.35,
              pointBackgroundColor: "#ffb020",
              yAxisID: "y1",
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#8194b4", font: { family: "JetBrains Mono" } } } },
          scales: {
            x: { ticks: { color: "#8194b4" }, grid: { color: "rgba(86,122,184,0.08)" } },
            y: { position: "left", ticks: { color: "#8194b4", callback: (v) => "$" + v / 1000 + "k" }, grid: { color: "rgba(86,122,184,0.08)" } },
            y1: { position: "right", ticks: { color: "#ffb020" }, grid: { drawOnChartArea: false } },
          },
        },
      });
    }
  } catch (e) {
    setText("market-source", "unavailable");
  }
}

async function runSources() {
  const btn = document.getElementById("run-btn");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Running…`; }
  try {
    const r = await API.post("/api/runs", {});
    toast("Run complete", `${r.leads_found} leads, ${r.buyers_found} buyers. ${r.notes}`, "ok");
    pageHome();
  } catch (e) {
    toast("Run failed", e.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = "Run ingestion"; }
  }
}

function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

/* =====================================================================
   PAGE: leads
   ===================================================================== */
let LEADS_CACHE = [];
let BUYERS_CACHE = [];
let EDIT_BUYER_ID = null;
async function pageLeads() {
  const filter = document.getElementById("status-filter");
  if (filter) filter.addEventListener("change", () => renderLeads(filter.value));
  await renderLeads("");
}

async function renderLeads(status) {
  const body = document.getElementById("leads-body");
  if (body) body.innerHTML = skeletonRows(7, 6);
  try {
    const q = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await API.get("/api/leads" + q);
    LEADS_CACHE = data.leads;
    setText("leads-count", data.count + " leads");
    if (!data.leads.length) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty"><div class="big">No leads yet</div>Run ingestion or import a CSV to populate this list.</div></td></tr>`;
      return;
    }
    body.innerHTML = data.leads.map((l) => `
      <tr>
        <td><span class="score ${scoreClass(l.final_score)}">${l.final_score}</span></td>
        <td><strong>${esc(l.address)}</strong><br><span style="color:var(--text-faint);font-size:12px">${esc(l.city)}, ${esc(l.state)} ${esc(l.zip_code)}</span></td>
        <td>${esc(l.owner_name) || "<span style='color:var(--text-faint)'>—</span>"}</td>
        <td>${(l.distress_signals || []).slice(0, 3).map((s) => `<span class="tag">${esc(s)}</span>`).join("") || "—"}</td>
        <td><span class="src-pill">${esc(l.source)}</span></td>
        <td>${l.est_value ? fmtMoney(l.est_value) : "—"}</td>
      </tr>`).join("");
  } catch (e) {
    toast("Couldn't load leads", e.message, "err");
  }
}

async function exportTopLeads() {
  try {
    const text = await API.req("GET", "/api/export/top-leads", undefined, true);
    const blob = new Blob([text], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "top-leads.csv"; a.click();
    URL.revokeObjectURL(url);
    toast("Export ready", "top-leads.csv downloaded", "ok");
  } catch (e) {
    toast("Export failed", e.message, "err");
  }
}

/* =====================================================================
   PAGE: buyers
   ===================================================================== */
async function pageBuyers() {
  await renderBuyers();
  document.getElementById("save-buyer")?.addEventListener("click", saveBuyer);
}

async function renderBuyers() {
  const body = document.getElementById("buyers-body");
  if (body) body.innerHTML = skeletonRows(6, 6);
  try {
    const data = await API.get("/api/buyers");
    BUYERS_CACHE = data.buyers;
    setText("buyers-count", data.count + " buyers");
    if (!data.buyers.length) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty"><div class="big">No buyers yet</div>Add one manually or import your buyer list.</div></td></tr>`;
      return;
    }
    body.innerHTML = data.buyers.map((b) => {
      const area = (b.target_zips || []).concat(b.target_cities || []).slice(0, 3).join(", ") || (b.city || "—");
      const pof = b.pof_received ? `<span style="color:var(--green,#34d399)">✓ ${fmtMoney(b.pof_amount)}</span>` : "—";
      return `<tr style="cursor:pointer" onclick="openBuyerEdit('${b.id}')">
        <td><span class="score ${scoreClass(b.cash_buyer_score)}">${b.cash_buyer_score}</span></td>
        <td><strong>${esc(b.name)}</strong> ${b.entity_type ? `<span class="src-pill">${esc(b.entity_type)}</span>` : ""}${b.active ? "" : ` <span class="src-pill">inactive</span>`}</td>
        <td>${esc(b.email) || "—"}<br><span style="color:var(--text-faint);font-size:12px">${esc(b.phone) || ""}</span></td>
        <td>${b.budget_max ? fmtMoney(b.budget_min) + "–" + fmtMoney(b.budget_max) : "—"}</td>
        <td>${esc(area)}</td>
        <td>${pof}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    toast("Couldn't load buyers", e.message, "err");
  }
}

function openBuyerEdit(id) {
  const b = (BUYERS_CACHE || []).find((x) => x.id === id);
  if (!b) return;
  EDIT_BUYER_ID = id;
  const set = (k, v) => { const el = document.getElementById(k); if (el) el.value = v ?? ""; };
  setText("be-title", "Edit — " + b.name);
  set("be-bmin", b.budget_min || ""); set("be-bmax", b.budget_max || "");
  set("be-zips", (b.target_zips || []).join(", "));
  set("be-cities", (b.target_cities || []).join(", "));
  set("be-counties", (b.target_counties || []).join(", "));
  set("be-assets", (b.asset_types || []).join(", "));
  set("be-minbeds", b.min_beds || ""); set("be-maxrehab", b.max_rehab || "");
  set("be-deals", b.recent_cash_deals || "");
  set("be-active", b.active ? "true" : "false");
  set("be-pof", b.pof_received ? "true" : "false");
  set("be-pofamt", b.pof_amount || "");
  set("be-pofexp", (b.pof_expires || "").slice(0, 10));
  openModal("buyer-edit-modal");
}

async function saveBuyerEdit() {
  if (!EDIT_BUYER_ID) return;
  const num = (id) => { const x = parseFloat(document.getElementById(id).value); return isNaN(x) ? null : x; };
  const str = (id) => document.getElementById(id).value.trim();
  const payload = {
    budget_min: num("be-bmin"), budget_max: num("be-bmax"),
    target_zips: str("be-zips"), target_cities: str("be-cities"), target_counties: str("be-counties"),
    asset_types: str("be-assets"), min_beds: num("be-minbeds"), max_rehab: num("be-maxrehab"),
    recent_cash_deals: num("be-deals"), active: document.getElementById("be-active").value === "true",
  };
  Object.keys(payload).forEach((k) => payload[k] === null && delete payload[k]);
  try {
    await API.put(`/api/buyers/${EDIT_BUYER_ID}`, payload);
    await API.put(`/api/buyers/${EDIT_BUYER_ID}/pof`, {
      pof_received: document.getElementById("be-pof").value === "true",
      pof_amount: num("be-pofamt") || 0,
      pof_expires: str("be-pofexp") || null,
    });
    toast("Buyer updated", "", "ok");
    closeModal("buyer-edit-modal");
    renderBuyers();
  } catch (e) { toast("Couldn't save", e.message, "err"); }
}

async function saveBuyer() {
  const v = (id) => document.getElementById(id).value.trim();
  const payload = {
    name: v("b-name"), entity_type: v("b-entity"), email: v("b-email"),
    phone: v("b-phone"), city: v("b-city"), state: v("b-state") || "TX",
    budget_min: parseFloat(v("b-bmin")) || 0, budget_max: parseFloat(v("b-bmax")) || 0,
    preferred_areas: v("b-areas"), property_types: v("b-ptypes"), notes: v("b-notes"),
  };
  if (!payload.name) return toast("Name required", "Buyer needs a name", "warn");
  if (!payload.email && !payload.phone) return toast("Contact required", "Add an email or phone", "warn");
  try {
    await API.post("/api/buyers/manual", payload);
    toast("Buyer added", payload.name, "ok");
    closeModal("buyer-modal");
    document.querySelectorAll("#buyer-modal input, #buyer-modal textarea").forEach((i) => (i.value = ""));
    renderBuyers();
  } catch (e) {
    toast("Couldn't add buyer", e.message, "err");
  }
}

/* =====================================================================
   PAGE: pipeline (kanban)
   ===================================================================== */
const STAGES = ["New", "Contacted", "Offer Sent", "Under Contract", "Assigned", "Closed"];
async function pagePipeline() { await renderKanban(); }

async function renderKanban() {
  const board = document.getElementById("kanban");
  if (!board) return;
  board.innerHTML = STAGES.map((s) => `<div class="kcol"><div class="kcol-head"><span class="name">${s}</span><span class="cnt" id="cnt-${slug(s)}">0</span></div><div id="col-${slug(s)}"></div></div>`).join("");
  try {
    const data = await API.get("/api/leads");
    const byStage = {};
    STAGES.forEach((s) => (byStage[s] = []));
    data.leads.forEach((l) => (byStage[l.status] || byStage["New"]).push(l));
    STAGES.forEach((s) => {
      const col = document.getElementById("col-" + slug(s));
      setText("cnt-" + slug(s), byStage[s].length);
      const i = STAGES.indexOf(s);
      const prev = STAGES[i - 1], next = STAGES[i + 1];
      col.innerHTML = byStage[s].map((l) => `
        <div class="kcard">
          <div class="addr">${esc(l.address)}</div>
          <div class="meta">${esc(l.city)} · <span class="score ${scoreClass(l.final_score)}" style="padding:0 5px">${l.final_score}</span></div>
          <div class="kbtns">
            ${prev ? `<button onclick="moveLead('${l.id}','${prev}')">← ${prev}</button>` : ""}
            ${next ? `<button onclick="moveLead('${l.id}','${next}')">${next} →</button>` : ""}
          </div>
        </div>`).join("") || `<div style="color:var(--text-faint);font-size:12px;padding:6px">Empty</div>`;
    });
  } catch (e) {
    toast("Couldn't load pipeline", e.message, "err");
  }
}

async function moveLead(id, status) {
  try {
    await API.put(`/api/leads/${id}/status`, { status });
    toast("Moved", "→ " + status, "ok");
    renderKanban();
  } catch (e) {
    toast("Move failed", e.message, "err");
  }
}

/* =====================================================================
   PAGE: calculator
   ===================================================================== */
function pageCalculator() {
  ["c-purchase", "c-repair", "c-arv", "c-closing", "c-fee"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", calcDeal);
  });
  const fee = document.getElementById("c-fee");
  const feeOut = document.getElementById("c-fee-out");
  fee?.addEventListener("input", () => (feeOut.textContent = fmtMoney(fee.value)));
  calcDeal();
}

function calcDeal() {
  const num = (id) => parseFloat(document.getElementById(id)?.value) || 0;
  const purchase = num("c-purchase"), repair = num("c-repair"), arv = num("c-arv");
  const closingPct = num("c-closing"), fee = num("c-fee");

  const closingCosts = arv * (closingPct / 100);
  const mao = arv * 0.7 - repair - fee;
  const netAssign = fee;
  const netList = arv - purchase - repair - closingCosts;
  const margin = arv ? (netList / arv) * 100 : 0;
  const cashTarget = purchase + fee;

  setText("r-mao", fmtMoney(mao));
  setText("r-assign", fmtMoney(netAssign));
  setText("r-list", fmtMoney(netList));
  setText("r-target", fmtMoney(cashTarget));
  setText("r-closing", fmtMoney(closingCosts));

  // gauge
  const ring = document.getElementById("gauge-ring");
  const pctEl = document.getElementById("gauge-pct");
  const R = 75, C = 2 * Math.PI * R;
  const clamped = Math.max(0, Math.min(margin, 30)) / 30; // 30% = full ring
  if (ring) {
    ring.style.strokeDasharray = C;
    ring.style.strokeDashoffset = C * (1 - clamped);
    const color = margin >= 15 ? "#34d399" : margin >= 8 ? "#fbbf24" : "#f87171";
    ring.style.stroke = color;
    pctEl.style.color = color;
  }
  if (pctEl) pctEl.textContent = margin.toFixed(1) + "%";

  const flag = document.getElementById("margin-flag");
  if (flag) {
    if (margin >= 15) { flag.textContent = "Strong margin — green light"; flag.style.color = "#34d399"; }
    else if (margin >= 8) { flag.textContent = "Thin margin — proceed with caution"; flag.style.color = "#fbbf24"; }
    else { flag.textContent = "Below target — likely a pass"; flag.style.color = "#f87171"; }
  }
}

async function logDeal() {
  const num = (id) => parseFloat(document.getElementById(id)?.value) || 0;
  try {
    await API.post("/api/calculate-deal", {
      purchase_price: num("c-purchase"), repair: num("c-repair"), arv: num("c-arv"),
      closing_pct: num("c-closing"), assignment_fee: num("c-fee"),
    });
    toast("Deal logged", "Saved to your run history", "ok");
  } catch (e) { toast("Couldn't log", e.message, "err"); }
}

/* =====================================================================
   PAGE: templates / generators
   ===================================================================== */
async function genOffer() {
  const v = (id) => document.getElementById(id).value.trim();
  try {
    const r = await API.post("/api/generate/offer-letter", {
      property_address: v("o-addr"), seller_name: v("o-seller"),
      purchase_price: parseFloat(v("o-price")) || 0,
      earnest_money: parseFloat(v("o-earnest")) || 1000,
      closing_date: v("o-closing"), buyer_entity: v("o-entity"),
    });
    document.getElementById("o-out").textContent = r.letter;
    toast("Offer letter ready", "", "ok");
  } catch (e) { toast("Generation failed", e.message, "err"); }
}

async function genAssignment() {
  const v = (id) => document.getElementById(id).value.trim();
  try {
    const r = await API.post("/api/generate/assignment-contract", {
      original_contract_price: parseFloat(v("a-price")) || 0,
      assignment_fee: parseFloat(v("a-fee")) || 0,
      seller_address: v("a-addr"), assignee_name: v("a-assignee"),
      assignee_entity: v("a-entity"), closing_date: v("a-closing"),
    });
    document.getElementById("a-out").textContent = r.contract;
    toast("Assignment contract ready", "Have an attorney review before use", "warn");
  } catch (e) { toast("Generation failed", e.message, "err"); }
}

async function genTemplate() {
  const type = document.getElementById("t-type").value;
  const v = (id) => document.getElementById(id).value.trim();
  const context = {
    owner_name: v("t-owner"), investor_name: v("t-investor"), address: v("t-addr"),
    phone: v("t-phone"), email: v("t-email"), city: v("t-city"),
    buyer_name: v("t-owner"), arv: v("t-arv"), repairs: v("t-repairs"), price: v("t-price"),
  };
  try {
    const r = await API.post("/api/generate/template", { type, context });
    document.getElementById("t-out").textContent = r.text;
    toast("Template ready", r.source === "claude" ? "Written by Claude" : "Static template", "ok");
  } catch (e) { toast("Generation failed", e.message, "err"); }
}

function copyOut(id) {
  const t = document.getElementById(id).textContent;
  navigator.clipboard.writeText(t).then(() => toast("Copied", "", "ok"));
}

/* =====================================================================
   PAGE: import
   ===================================================================== */
function pageImport() {
  bindImport("buyers-file", "/api/import/buyers", "buyers");
  bindImport("props-file", "/api/import/properties", "properties");
}

function bindImport(inputId, endpoint, label) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    const text = await file.text();
    try {
      const r = await API.postText(endpoint, text);
      toast(`Imported ${label}`, `${r.imported} added, ${r.skipped} skipped`, "ok");
    } catch (e) {
      toast("Import failed", e.message, "err");
    }
    input.value = "";
  });
}

/* =====================================================================
   PAGE: settings
   ===================================================================== */
async function pageSettings() {
  try {
    const h = await API.get("/api/health");
    const map = {
      anthropic: "Claude scoring & copywriting",
      attom: "ATTOM property + buyer data",
      usps: "USPS address validation",
      google_maps: "Google Maps street view",
      batchdata: "BatchData skip trace",
      tracerfy: "Tracerfy skip trace",
    };
    const host = document.getElementById("integrations");
    host.innerHTML = Object.entries(map).map(([k, label]) => {
      const on = h.integrations[k];
      return `<div class="result-row"><span class="k">${label}</span><span class="v" style="color:${on ? "var(--green)" : "var(--text-faint)"}">${on ? "Connected" : "Not set"}</span></div>`;
    }).join("");

    setText("db-type", h.database);
    setText("demo-state", h.demo_mode ? "ON" : "OFF");
    const srcHost = document.getElementById("sources-list");
    srcHost.innerHTML = h.enabled_sources.length
      ? h.enabled_sources.map((s) => `<span class="tag">${esc(s)}</span>`).join("")
      : `<span style="color:var(--text-faint);font-size:13px">No sources enabled. Set ENABLE_* env vars (after reviewing each site's terms) or DEMO_MODE=true.</span>`;
  } catch (e) {
    toast("Couldn't load settings", e.message, "err");
  }
}

/* ---------- utils ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function slug(s) { return s.toLowerCase().replace(/\s+/g, "-"); }
function skeletonRows(rows, cols) {
  return Array.from({ length: rows }).map(() =>
    `<tr>${Array.from({ length: cols }).map(() => `<td><div class="skeleton" style="height:14px"></div></td>`).join("")}</tr>`
  ).join("");
}

/* =====================================================================
   PAGE: follow-ups
   ===================================================================== */
async function pageFollowups() {
  const sel = document.getElementById("fu-window");
  sel?.addEventListener("change", () => renderFollowups(sel.value));
  await renderFollowups(sel ? sel.value : "today");
}

async function renderFollowups(window) {
  const body = document.getElementById("fu-body");
  if (body) body.innerHTML = skeletonRows(6, 6);
  try {
    const data = await API.get("/api/follow-ups/due?window=" + encodeURIComponent(window));
    setText("fu-count", data.count + (data.count === 1 ? " lead" : " leads") + " to work");
    if (!data.leads.length) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty"><div class="big">Nothing due</div>You're all caught up for this window.</div></td></tr>`;
      return;
    }
    body.innerHTML = data.leads.map((l) => {
      const d = l.due_in_days;
      const due = d < 0 ? `<span style="color:var(--red,#f87171)">${Math.abs(d)}d overdue</span>`
        : d === 0 ? `<span style="color:var(--amber,#ffb020)">today</span>`
        : `in ${d}d`;
      return `<tr>
        <td>${due}</td>
        <td><strong>${esc(l.address)}</strong><br><span style="color:var(--text-faint);font-size:12px">${esc(l.city)}, ${esc(l.state)}</span></td>
        <td>${esc(l.owner_name) || "—"}</td>
        <td>${esc(l.owner_phone) || "—"}</td>
        <td>${l.contact_count || 0}</td>
        <td><div class="btn-row" style="gap:6px">
          <button class="btn ghost sm" onclick="logTouch('${l.id}','call','spoke')">Called</button>
          <button class="btn ghost sm" onclick="logTouch('${l.id}','text','no_answer')">Texted</button>
          <button class="btn ghost sm" onclick="logTouch('${l.id}','call','no_answer')">No answer</button>
        </div></td>
      </tr>`;
    }).join("");
  } catch (e) {
    toast("Couldn't load follow-ups", e.message, "err");
  }
}

async function logTouch(leadId, channel, outcome) {
  try {
    const r = await API.post(`/api/leads/${leadId}/contact-log`, { channel, outcome });
    const next = r.lead.days_until_follow_up;
    toast("Touch logged", `Next follow-up in ${next}d`, "ok");
    const sel = document.getElementById("fu-window");
    renderFollowups(sel ? sel.value : "today");
  } catch (e) {
    toast("Couldn't log touch", e.message, "err");
  }
}

/* =====================================================================
   PAGE: deals (workspace + deadlines)
   ===================================================================== */
let CURRENT_DEAL = null;

async function pageDeals() {
  await renderDeadlines();
  const sel = document.getElementById("deal-lead");
  try {
    const data = await API.get("/api/leads");
    sel.innerHTML = `<option value="">Select a lead…</option>` +
      data.leads.map((l) => `<option value="${l.id}">${esc(l.address)} — ${esc(l.city)}</option>`).join("");
  } catch (e) { toast("Couldn't load leads", e.message, "err"); }
  sel?.addEventListener("change", () => selectDeal(sel.value));
}

async function renderDeadlines() {
  const host = document.getElementById("deadlines");
  if (!host) return;
  try {
    const data = await API.get("/api/deals/deadlines");
    if (!data.count) {
      host.innerHTML = `<div style="color:var(--text-faint);font-size:13px">No deadlines set. Add inspection or close dates to a deal below.</div>`;
      return;
    }
    host.innerHTML = `<div class="grid cols-3">` + data.deals.map((d) => {
      const s = d.soonest_days;
      const color = s < 0 ? "var(--red,#f87171)" : s <= 3 ? "var(--amber,#ffb020)" : "var(--green,#34d399)";
      const events = d.deadlines.map((e) =>
        `<div style="font-size:12.5px;color:var(--text-muted)">${e.label}: <b style="color:${color}">${e.days_remaining < 0 ? Math.abs(e.days_remaining) + "d ago" : e.days_remaining + "d"}</b> · ${e.date}</div>`).join("");
      return `<div class="card" style="padding:14px">
        <div style="font-weight:600">${esc(d.address)}</div>
        <div style="color:var(--text-faint);font-size:12px;margin-bottom:6px">${esc(d.city)}, ${esc(d.state)} · ${esc(d.status)}</div>
        ${events}</div>`;
    }).join("") + `</div>`;
  } catch (e) { toast("Couldn't load deadlines", e.message, "err"); }
}

async function selectDeal(id) {
  const panel = document.getElementById("deal-panel");
  if (!id) { panel.style.display = "none"; CURRENT_DEAL = null; return; }
  try {
    CURRENT_DEAL = await API.get(`/api/leads/${id}`);
    panel.style.display = "block";
    const v = (k, val) => { const el = document.getElementById(k); if (el) el.value = (val ?? "") === 0 ? "" : (val ?? ""); };
    v("d-arv", CURRENT_DEAL.arv); v("d-repair", CURRENT_DEAL.repair_estimate);
    v("d-ask", CURRENT_DEAL.asking_price); v("d-earnest", CURRENT_DEAL.earnest_money);
    v("d-beds", CURRENT_DEAL.beds); v("d-baths", CURRENT_DEAL.baths);
    v("d-sqft", CURRENT_DEAL.sqft); v("d-occ", CURRENT_DEAL.occupancy);
    v("d-insp", (CURRENT_DEAL.inspection_end_date || "").slice(0, 10));
    v("d-close", (CURRENT_DEAL.close_date || "").slice(0, 10));
    document.getElementById("d-pisheet").href = `/api/leads/${id}/pi-sheet`;
    document.getElementById("comps-out").innerHTML = "";
    document.getElementById("blast-out").innerHTML = "";
    await loadMatches();
  } catch (e) { toast("Couldn't load deal", e.message, "err"); }
}

async function saveDealTerms() {
  if (!CURRENT_DEAL) return;
  const num = (id) => { const x = parseFloat(document.getElementById(id).value); return isNaN(x) ? null : x; };
  const str = (id) => document.getElementById(id).value.trim() || null;
  const payload = {
    arv: num("d-arv"), repair_estimate: num("d-repair"), asking_price: num("d-ask"),
    earnest_money: num("d-earnest"), beds: num("d-beds"), baths: num("d-baths"),
    sqft: num("d-sqft"), occupancy: str("d-occ"),
    inspection_end_date: str("d-insp"), close_date: str("d-close"),
  };
  Object.keys(payload).forEach((k) => payload[k] === null && delete payload[k]);
  try {
    CURRENT_DEAL = await API.put(`/api/leads/${CURRENT_DEAL.id}/deal-terms`, payload);
    toast("Deal terms saved", "", "ok");
    renderDeadlines();
    loadMatches();
  } catch (e) { toast("Couldn't save", e.message, "err"); }
}

async function runComps() {
  if (!CURRENT_DEAL) return;
  const lines = document.getElementById("d-comps").value.trim().split("\n").filter(Boolean);
  const comps = lines.map((ln) => {
    const [sqft, price] = ln.split(",").map((x) => parseFloat(x.trim()));
    return { sqft: sqft || 0, sale_price: price || 0 };
  }).filter((c) => c.sqft && c.sale_price);
  if (!comps.length) return toast("No comps", "Enter lines like: 1800, 290000", "warn");
  try {
    const subjectSqft = parseFloat(document.getElementById("d-sqft").value) || CURRENT_DEAL.sqft || 0;
    const r = await API.post("/api/comps", { lead_id: CURRENT_DEAL.id, subject_sqft: subjectSqft, comps });
    if (!r.arv) { document.getElementById("comps-out").innerHTML = `<div class="note">${esc(r.note)}</div>`; return; }
    document.getElementById("d-arv").value = r.arv;
    const confColor = r.confidence === "high" ? "var(--green,#34d399)" : r.confidence === "medium" ? "var(--amber,#ffb020)" : "var(--red,#f87171)";
    document.getElementById("comps-out").innerHTML =
      `<div class="result-row"><span class="k">ARV (median $/sqft × ${subjectSqft})</span><span class="v">${fmtMoney(r.arv)}</span></div>
       <div class="result-row"><span class="k">Range</span><span class="v">${fmtMoney(r.arv_low)} – ${fmtMoney(r.arv_high)}</span></div>
       <div class="result-row"><span class="k">Comps used / confidence</span><span class="v">${r.qualified_count} · <span style="color:${confColor}">${r.confidence}</span></span></div>`;
    toast("ARV computed", "Dropped into the ARV field", "ok");
  } catch (e) { toast("Comps failed", e.message, "err"); }
}

async function loadMatches() {
  if (!CURRENT_DEAL) return;
  const body = document.getElementById("match-body");
  if (body) body.innerHTML = skeletonRows(4, 6);
  try {
    const data = await API.get(`/api/leads/${CURRENT_DEAL.id}/matched-buyers`);
    if (!data.buyers.length) { body.innerHTML = `<tr><td colspan="6"><div class="empty">No buyers yet.</div></td></tr>`; return; }
    body.innerHTML = data.buyers.map((b) => `
      <tr>
        <td><span class="score ${scoreClass(b.match_score)}">${b.match_score}</span></td>
        <td><strong>${esc(b.name)}</strong></td>
        <td>${esc(b.email) || "—"}<br><span style="color:var(--text-faint);font-size:12px">${esc(b.phone) || ""}</span></td>
        <td>${b.budget_max ? fmtMoney(b.budget_min) + "–" + fmtMoney(b.budget_max) : "—"}</td>
        <td>${b.pof_received ? `<span style="color:var(--green,#34d399)">✓ ${fmtMoney(b.pof_amount)}</span>` : "—"}</td>
        <td><span style="font-size:12px;color:var(--text-muted)">${(b.match_reasons || []).join(" · ") || "—"}</span></td>
      </tr>`).join("");
  } catch (e) { toast("Couldn't match buyers", e.message, "err"); }
}

async function genBlast() {
  if (!CURRENT_DEAL) return;
  try {
    const r = await API.post(`/api/leads/${CURRENT_DEAL.id}/blast`, {});
    document.getElementById("blast-out").innerHTML = `
      <div class="card" style="margin-top:14px">
        <h3>Email blast — ${r.matched_buyers.length} matched buyers</h3>
        <div class="result-row"><span class="k">Subject</span><span class="v">${esc(r.email.subject)}</span></div>
        <pre class="codebox" style="margin-top:10px">${esc(r.email.body)}</pre>
        <h3 style="margin-top:14px">SMS</h3>
        <pre class="codebox">${esc(r.sms.body)}</pre>
        <div class="note">${esc(r.note)}</div>
      </div>`;
    toast("Blast ready", "Review before sending", "ok");
  } catch (e) { toast("Couldn't generate blast", e.message, "err"); }
}

/* =====================================================================
   PAGE: boot
   ===================================================================== */
document.addEventListener("DOMContentLoaded", () => {
  initChrome();
  const routes = {
    home: pageHome, leads: pageLeads, buyers: pageBuyers, pipeline: pagePipeline,
    calculator: pageCalculator, templates: () => {}, import: pageImport, settings: pageSettings,
    followups: pageFollowups, deals: pageDeals,
  };
  const fn = routes[document.body.dataset.page];
  if (fn) fn();
});
