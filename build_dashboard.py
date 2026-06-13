#!/usr/bin/env python3
"""Generates the dashboard HTML pages with a shared shell."""
import os

OUT = os.path.join(os.path.dirname(__file__), "dashboard")

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&'
    'family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">'
)

NAVLINKS = [
    ("home", "Overview", "/dashboard/index.html"),
    ("leads", "Leads", "/dashboard/leads.html"),
    ("followups", "Follow-ups", "/dashboard/followups.html"),
    ("pipeline", "Pipeline", "/dashboard/pipeline.html"),
    ("deals", "Deals", "/dashboard/deals.html"),
    ("buyers", "Buyers", "/dashboard/buyers.html"),
    ("calculator", "Calculator", "/dashboard/calculator.html"),
    ("templates", "Templates", "/dashboard/templates.html"),
    ("import", "Import", "/dashboard/import.html"),
    ("settings", "Settings", "/dashboard/settings.html"),
]


def nav():
    links = "".join(
        f'<a data-page="{p}" href="{href}">{label}</a>' for p, label, href in NAVLINKS
    )
    return (
        '<nav class="nav">'
        '<div class="brand"><span class="dot"></span>TEX<span class="tx">·</span>Wholesale</div>'
        f'<div class="nav-links">{links}</div>'
        '<div class="spacer"></div>'
        '<div class="status-chip off" id="health-chip"><span class="led"></span>…</div>'
        "</nav>"
    )


def page(slug, title, body, scripts=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · TexWholesale Engine</title>
{FONTS}
<link rel="stylesheet" href="/dashboard/styles.css">
</head>
<body data-page="{slug}">
{nav()}
<main class="wrap">
{body}
</main>
<div id="toasts"></div>
<script src="/dashboard/app.js"></script>
{scripts}
</body>
</html>"""


def write(name, html):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write(html)
    print("wrote", name)


# ---------------- index / overview ----------------
index_body = """
<div class="page-head">
  <div class="eyebrow">Acquisitions command center</div>
  <h1>Overview</h1>
  <p>Texas wholesaling pipeline — leads, buyers, and market signal in one place.</p>
</div>

<div class="grid cols-4" style="margin-bottom:18px">
  <div class="card hoverable stat"><div class="label">Total leads</div><div class="value cyan" id="stat-leads">—</div><div class="sub">scored & ranked</div></div>
  <div class="card hoverable stat"><div class="label">Hot leads</div><div class="value" id="stat-hot" style="color:var(--green)">—</div><div class="sub">score ≥ 70</div></div>
  <div class="card hoverable stat"><div class="label">In pipeline</div><div class="value amber" id="stat-pipeline">—</div><div class="sub">not yet closed</div></div>
  <div class="card hoverable stat"><div class="label">Cash buyers</div><div class="value" id="stat-buyers">—</div><div class="sub">on your list</div></div>
</div>

<div class="grid cols-2">
  <div class="card" style="grid-column: span 1">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <h3 style="margin:0">Texas metros</h3>
      <span class="src-pill" id="market-source">…</span>
    </div>
    <div style="height:300px"><canvas id="marketChart"></canvas></div>
  </div>
  <div class="card">
    <h3>Run ingestion</h3>
    <p style="color:var(--text-muted);font-size:13.5px;margin-top:0">
      Pulls from every enabled source across your target counties, scores each lead,
      and (if a Claude key is set) re-ranks the top 50.</p>
    <div class="btn-row">
      <button class="btn primary" id="run-btn" onclick="runSources()">Run ingestion</button>
      <a class="btn ghost" href="/dashboard/settings.html">Configure sources</a>
    </div>
    <div class="note">Scrapers are off by default. Enable them in your environment only after
      reviewing each site's terms of service. Turn on <code>DEMO_MODE</code> to explore with sample data.</div>
  </div>
</div>
"""
write("index.html", page("home", "Overview", index_body,
      '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>'))

# ---------------- leads ----------------
leads_body = """
<div class="page-head" style="display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px">
  <div>
    <div class="eyebrow">Deal flow</div>
    <h1>Leads</h1>
    <p id="leads-count">…</p>
  </div>
  <div class="btn-row">
    <select id="status-filter" style="width:auto">
      <option value="">All statuses</option>
      <option>New</option><option>Contacted</option><option>Offer Sent</option>
      <option>Under Contract</option><option>Assigned</option><option>Closed</option>
    </select>
    <button class="btn amber" onclick="exportTopLeads()">Export top 50 (CSV)</button>
  </div>
</div>
<div class="tbl-wrap">
  <table>
    <thead><tr><th>Score</th><th>Property</th><th>Owner</th><th>Signals</th><th>Source</th><th>Est. value</th></tr></thead>
    <tbody id="leads-body"></tbody>
  </table>
</div>
"""
write("leads.html", page("leads", "Leads", leads_body))

# ---------------- pipeline ----------------
pipeline_body = """
<div class="page-head">
  <div class="eyebrow">CRM</div>
  <h1>Pipeline</h1>
  <p>Move deals through the funnel. Status updates save instantly.</p>
</div>
<div class="kanban" id="kanban"></div>
"""
write("pipeline.html", page("pipeline", "Pipeline", pipeline_body))

# ---------------- buyers ----------------
buyers_body = """
<div class="page-head" style="display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px">
  <div>
    <div class="eyebrow">Disposition</div>
    <h1>Buyers</h1>
    <p id="buyers-count">…</p>
  </div>
  <button class="btn primary" onclick="openModal('buyer-modal')">+ Add buyer</button>
</div>
<div class="tbl-wrap">
  <table>
    <thead><tr><th>Score</th><th>Buyer</th><th>Contact</th><th>Budget</th><th>Target area</th><th>POF</th></tr></thead>
    <tbody id="buyers-body"></tbody>
  </table>
</div>
<div class="note">Click a buyer to edit their buy box (target ZIPs/cities, max rehab, asset types) and proof-of-funds — these drive deal matching.</div>

<div class="modal-backdrop" id="buyer-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('buyer-modal')">&times;</button>
    <h2>Add buyer</h2>
    <div class="grid cols-2">
      <label class="field"><span class="lbl">Name *</span><input id="b-name" placeholder="Lone Star REI LLC"></label>
      <label class="field"><span class="lbl">Entity type</span><input id="b-entity" placeholder="LLC"></label>
      <label class="field"><span class="lbl">Email</span><input id="b-email" type="email"></label>
      <label class="field"><span class="lbl">Phone</span><input id="b-phone"></label>
      <label class="field"><span class="lbl">City</span><input id="b-city"></label>
      <label class="field"><span class="lbl">State</span><input id="b-state" value="TX"></label>
      <label class="field"><span class="lbl">Budget min</span><input id="b-bmin" type="number"></label>
      <label class="field"><span class="lbl">Budget max</span><input id="b-bmax" type="number"></label>
      <label class="field"><span class="lbl">Preferred areas</span><input id="b-areas"></label>
      <label class="field"><span class="lbl">Property types</span><input id="b-ptypes" placeholder="SFR, duplex"></label>
    </div>
    <label class="field"><span class="lbl">Notes</span><textarea id="b-notes"></textarea></label>
    <div class="btn-row"><button class="btn primary" id="save-buyer">Save buyer</button>
      <button class="btn ghost" onclick="closeModal('buyer-modal')">Cancel</button></div>
    <div class="note">Name plus at least one of email or phone is required.</div>
  </div>
</div>

<div class="modal-backdrop" id="buyer-edit-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('buyer-edit-modal')">&times;</button>
    <h2 id="be-title">Edit buyer</h2>
    <div class="grid cols-2">
      <label class="field"><span class="lbl">Budget min</span><input id="be-bmin" type="number"></label>
      <label class="field"><span class="lbl">Budget max</span><input id="be-bmax" type="number"></label>
      <label class="field"><span class="lbl">Target ZIPs (comma-sep)</span><input id="be-zips" placeholder="75001, 75002"></label>
      <label class="field"><span class="lbl">Target cities (comma-sep)</span><input id="be-cities"></label>
      <label class="field"><span class="lbl">Target counties</span><input id="be-counties"></label>
      <label class="field"><span class="lbl">Asset types</span><input id="be-assets" placeholder="Single Family, Duplex"></label>
      <label class="field"><span class="lbl">Min beds</span><input id="be-minbeds" type="number" step="0.5"></label>
      <label class="field"><span class="lbl">Max rehab budget</span><input id="be-maxrehab" type="number"></label>
      <label class="field"><span class="lbl">Recent cash deals</span><input id="be-deals" type="number"></label>
      <label class="field"><span class="lbl">Active</span><select id="be-active"><option value="true">Yes</option><option value="false">No</option></select></label>
    </div>
    <h3 style="margin-top:6px">Proof of funds</h3>
    <div class="grid cols-3">
      <label class="field"><span class="lbl">On file</span><select id="be-pof"><option value="false">No</option><option value="true">Yes</option></select></label>
      <label class="field"><span class="lbl">Amount</span><input id="be-pofamt" type="number"></label>
      <label class="field"><span class="lbl">Expires</span><input id="be-pofexp" placeholder="2026-12-31"></label>
    </div>
    <div class="btn-row"><button class="btn primary" onclick="saveBuyerEdit()">Save changes</button>
      <button class="btn ghost" onclick="closeModal('buyer-edit-modal')">Cancel</button></div>
  </div>
</div>
"""
write("buyers.html", page("buyers", "Buyers", buyers_body))

# ---------------- calculator ----------------
calc_body = """
<div class="page-head">
  <div class="eyebrow">Underwriting</div>
  <h1>Deal calculator</h1>
  <p>70% rule with live margin gauge. Adjust inputs to see numbers update instantly.</p>
</div>
<div class="grid cols-2">
  <div class="card">
    <h3>Inputs</h3>
    <label class="field"><span class="lbl">Purchase price</span><input id="c-purchase" type="number" value="180000"></label>
    <label class="field"><span class="lbl">Repair estimate</span><input id="c-repair" type="number" value="35000"></label>
    <label class="field"><span class="lbl">After-repair value (ARV)</span><input id="c-arv" type="number" value="320000"></label>
    <label class="field"><span class="lbl">Closing costs (%)</span><input id="c-closing" type="number" value="6" step="0.5"></label>
    <label class="field"><span class="lbl">Assignment fee — <span id="c-fee-out">$10,000</span></span>
      <input id="c-fee" type="range" min="0" max="50000" step="500" value="10000"></label>
    <button class="btn amber" onclick="logDeal()">Log this deal</button>
  </div>
  <div class="card">
    <h3>Result</h3>
    <div class="gauge-wrap">
      <div class="gauge">
        <svg width="180" height="180" viewBox="0 0 180 180">
          <circle class="ring-bg" cx="90" cy="90" r="75"></circle>
          <circle class="ring-fg" id="gauge-ring" cx="90" cy="90" r="75"></circle>
        </svg>
        <div class="center"><div class="pct" id="gauge-pct">0%</div><div class="cap">flip margin</div></div>
      </div>
      <div id="margin-flag" style="font-weight:600;font-size:13.5px"></div>
    </div>
    <div style="margin-top:18px">
      <div class="result-row"><span class="k">Max allowable offer (MAO)</span><span class="v" id="r-mao">—</span></div>
      <div class="result-row"><span class="k">Net profit — assign</span><span class="v" id="r-assign">—</span></div>
      <div class="result-row"><span class="k">Net profit — fix & list</span><span class="v" id="r-list">—</span></div>
      <div class="result-row"><span class="k">Cash buyer target price</span><span class="v" id="r-target">—</span></div>
      <div class="result-row"><span class="k">Est. closing costs</span><span class="v" id="r-closing">—</span></div>
    </div>
  </div>
</div>
"""
write("calculator.html", page("calculator", "Calculator", calc_body))

# ---------------- templates ----------------
templates_body = """
<div class="page-head">
  <div class="eyebrow">Documents & outreach</div>
  <h1>Templates</h1>
  <p>Generate offer letters, assignment contracts, and outreach copy. Claude-written when a key is set, static otherwise.</p>
</div>

<div class="grid cols-2">
  <div class="card">
    <h3>Offer letter (LOI)</h3>
    <label class="field"><span class="lbl">Property address</span><input id="o-addr"></label>
    <label class="field"><span class="lbl">Seller name</span><input id="o-seller"></label>
    <div class="grid cols-2">
      <label class="field"><span class="lbl">Purchase price</span><input id="o-price" type="number"></label>
      <label class="field"><span class="lbl">Earnest money</span><input id="o-earnest" type="number" value="1000"></label>
      <label class="field"><span class="lbl">Closing date</span><input id="o-closing" placeholder="2026-08-01"></label>
      <label class="field"><span class="lbl">Buyer entity</span><input id="o-entity"></label>
    </div>
    <div class="btn-row"><button class="btn primary" onclick="genOffer()">Generate</button>
      <button class="btn ghost sm" onclick="copyOut('o-out')">Copy</button></div>
    <pre class="codebox" id="o-out" style="margin-top:14px">Your letter will appear here.</pre>
  </div>

  <div class="card">
    <h3>Assignment contract</h3>
    <label class="field"><span class="lbl">Property / seller address</span><input id="a-addr"></label>
    <div class="grid cols-2">
      <label class="field"><span class="lbl">Original contract price</span><input id="a-price" type="number"></label>
      <label class="field"><span class="lbl">Assignment fee</span><input id="a-fee" type="number" value="10000"></label>
      <label class="field"><span class="lbl">Assignee name</span><input id="a-assignee"></label>
      <label class="field"><span class="lbl">Assignee entity</span><input id="a-entity"></label>
    </div>
    <label class="field"><span class="lbl">Closing date</span><input id="a-closing" placeholder="2026-08-01"></label>
    <div class="btn-row"><button class="btn primary" onclick="genAssignment()">Generate</button>
      <button class="btn ghost sm" onclick="copyOut('a-out')">Copy</button></div>
    <pre class="codebox" id="a-out" style="margin-top:14px">Your contract will appear here.</pre>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <h3>Outreach copy</h3>
  <div class="grid cols-3">
    <label class="field"><span class="lbl">Type</span>
      <select id="t-type"><option value="seller_letter">Seller letter</option>
        <option value="call_script">Call script</option><option value="buyer_text">Buyer text blast</option></select></label>
    <label class="field"><span class="lbl">Owner / buyer name</span><input id="t-owner"></label>
    <label class="field"><span class="lbl">Your name</span><input id="t-investor"></label>
    <label class="field"><span class="lbl">Property address</span><input id="t-addr"></label>
    <label class="field"><span class="lbl">City</span><input id="t-city"></label>
    <label class="field"><span class="lbl">Your phone</span><input id="t-phone"></label>
    <label class="field"><span class="lbl">Your email</span><input id="t-email"></label>
    <label class="field"><span class="lbl">ARV</span><input id="t-arv"></label>
    <label class="field"><span class="lbl">Repairs</span><input id="t-repairs"></label>
    <label class="field"><span class="lbl">Assign price</span><input id="t-price"></label>
  </div>
  <div class="btn-row"><button class="btn primary" onclick="genTemplate()">Generate</button>
    <button class="btn ghost sm" onclick="copyOut('t-out')">Copy</button></div>
  <pre class="codebox" id="t-out" style="margin-top:14px">Your copy will appear here.</pre>
  <div class="note">Generated legal documents are drafts, not legal advice. Have a Texas real estate attorney review contracts before use.</div>
</div>
"""
write("templates.html", page("templates", "Templates", templates_body))

# ---------------- import ----------------
import_body = """
<div class="page-head">
  <div class="eyebrow">Data in</div>
  <h1>Import</h1>
  <p>Drop in a PropStream / county / buyer-list CSV. Headers are matched case-insensitively.</p>
</div>
<div class="grid cols-2">
  <div class="card">
    <h3>Import properties</h3>
    <p style="color:var(--text-muted);font-size:13.5px">Columns: address, city, state, zip, county, owner_name, est_value,
      est_equity_pct, tax_delinquent, days_on_market, distress_signals (semicolon-separated).</p>
    <input type="file" id="props-file" accept=".csv">
  </div>
  <div class="card">
    <h3>Import buyers</h3>
    <p style="color:var(--text-muted);font-size:13.5px">Columns: name, entity_type, email, phone, city, state,
      budget_min, budget_max, preferred_areas, property_types.</p>
    <input type="file" id="buyers-file" accept=".csv">
  </div>
</div>
"""
write("import.html", page("import", "Import", import_body))

# ---------------- settings ----------------
settings_body = """
<div class="page-head">
  <div class="eyebrow">Configuration</div>
  <h1>Settings</h1>
  <p>Connection status. Keys live in your environment, never in the browser.</p>
</div>
<div class="grid cols-2">
  <div class="card">
    <h3>Integrations</h3>
    <div id="integrations"></div>
  </div>
  <div class="card">
    <h3>System</h3>
    <div class="result-row"><span class="k">Database</span><span class="v" id="db-type">—</span></div>
    <div class="result-row"><span class="k">Demo mode</span><span class="v" id="demo-state">—</span></div>
    <h3 style="margin-top:20px">Enabled sources</h3>
    <div id="sources-list"></div>
  </div>
</div>
<div class="card" style="margin-top:16px">
  <h3>About the scrapers</h3>
  <p style="color:var(--text-muted);font-size:13.5px;line-height:1.6">
    Every scraper ships disabled. Each one targets a third-party site whose terms of service and
    robots rules you should confirm before turning it on — some (e.g. Zillow) prohibit scraping outright,
    and licensed data feeds are the safer route. ATTOM integrations use a documented API and activate
    automatically when <code>ATTOM_API_KEY</code> is set. Until you enable a source or demo mode, runs
    return no records, which is expected.</p>
</div>
"""
write("settings.html", page("settings", "Settings", settings_body))

# ---------------- follow-ups ----------------
followups_body = """
<div class="page-head" style="display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px">
  <div>
    <div class="eyebrow">The fortune is in the follow-up</div>
    <h1>Follow-ups</h1>
    <p id="fu-count">…</p>
  </div>
  <div class="btn-row">
    <select id="fu-window" style="width:auto">
      <option value="today">Due today + overdue</option>
      <option value="overdue">Overdue only</option>
      <option value="week">Next 7 days</option>
      <option value="all">All scheduled</option>
    </select>
  </div>
</div>
<div class="tbl-wrap">
  <table>
    <thead><tr><th>Due</th><th>Property</th><th>Owner</th><th>Phone</th><th>Touches</th><th>Log a touch</th></tr></thead>
    <tbody id="fu-body"></tbody>
  </table>
</div>
<div class="note">Logging a touch automatically schedules the next follow-up (1 → 3 → 7 → 14 → 30 days, then monthly) and moves a brand-new lead to Contacted.</div>
"""
write("followups.html", page("followups", "Follow-ups", followups_body))

# ---------------- deals (workspace + deadlines) ----------------
deals_body = """
<div class="page-head">
  <div class="eyebrow">Disposition & closing</div>
  <h1>Deals</h1>
  <p>Set deal terms, pull comps, match buyers, print a property info sheet, and track deadlines.</p>
</div>

<div class="card" style="margin-bottom:16px">
  <h3>Closing deadlines</h3>
  <div id="deadlines"></div>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
    <h3 style="margin:0">Deal workspace</h3>
    <select id="deal-lead" style="width:auto;min-width:280px"><option value="">Select a lead…</option></select>
  </div>

  <div id="deal-panel" style="display:none;margin-top:18px">
    <div class="grid cols-2">
      <div>
        <h3>Deal terms</h3>
        <div class="grid cols-2">
          <label class="field"><span class="lbl">ARV</span><input id="d-arv" type="number"></label>
          <label class="field"><span class="lbl">Repair estimate</span><input id="d-repair" type="number"></label>
          <label class="field"><span class="lbl">Asking price (assignment incl.)</span><input id="d-ask" type="number"></label>
          <label class="field"><span class="lbl">Earnest money</span><input id="d-earnest" type="number"></label>
          <label class="field"><span class="lbl">Beds</span><input id="d-beds" type="number" step="0.5"></label>
          <label class="field"><span class="lbl">Baths</span><input id="d-baths" type="number" step="0.5"></label>
          <label class="field"><span class="lbl">Sq ft</span><input id="d-sqft" type="number"></label>
          <label class="field"><span class="lbl">Occupancy</span><input id="d-occ" placeholder="vacant / owner / tenant"></label>
          <label class="field"><span class="lbl">Inspection ends</span><input id="d-insp" placeholder="2026-06-20"></label>
          <label class="field"><span class="lbl">Close date</span><input id="d-close" placeholder="2026-07-01"></label>
        </div>
        <div class="btn-row">
          <button class="btn primary" onclick="saveDealTerms()">Save terms</button>
          <a class="btn amber" id="d-pisheet" target="_blank" rel="noopener">Open PI sheet ↗</a>
        </div>
      </div>
      <div>
        <h3>Comps → ARV</h3>
        <p style="color:var(--text-muted);font-size:13px;margin-top:0">Enter sold comps (one per line): <code>sqft, sale_price</code>. Computes ARV from the median $/sqft and drops in above.</p>
        <textarea id="d-comps" style="min-height:120px;font-family:var(--mono,monospace)" placeholder="1750, 270000&#10;1900, 295000&#10;1700, 260000"></textarea>
        <div class="btn-row"><button class="btn primary" onclick="runComps()">Compute ARV</button></div>
        <div id="comps-out" style="margin-top:10px"></div>
      </div>
    </div>

    <h3 style="margin-top:8px">Matched buyers</h3>
    <div class="btn-row" style="margin-bottom:8px">
      <button class="btn ghost sm" onclick="loadMatches()">Refresh matches</button>
      <button class="btn primary sm" onclick="genBlast()">Generate blast</button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Match</th><th>Buyer</th><th>Contact</th><th>Budget</th><th>POF</th><th>Why</th></tr></thead>
        <tbody id="match-body"></tbody>
      </table>
    </div>
    <div id="blast-out"></div>
  </div>
</div>
"""
write("deals.html", page("deals", "Deals", deals_body))

print("done")
