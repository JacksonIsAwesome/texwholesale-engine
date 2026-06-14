# TexWholesale Engine

A single-service FastAPI app for Texas real estate wholesaling: lead sourcing, cash-buyer
matching, deal math, a kanban pipeline/CRM, document generation, and a dark cyber dashboard.
SQLite for local dev, Postgres in production. No build step for the frontend.

---

## Quick start (Mac M4 / local)

```bash
cd app
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Try it with sample data first — no API keys needed:
DEMO_MODE=true uvicorn main:app --reload
```

Then open **http://localhost:8000/dashboard/** in your browser.

To seed the demo data, either set `DEMO_MODE=true` (auto-seeds on first run) or hit the
"Run Sources" button on the dashboard, which POSTs to `/api/runs`.

Without `DEMO_MODE`, the app starts empty and pulls only from whatever sources you've
enabled and supplied keys for.

---

## Deploy to Railway

1. Push this folder to a GitHub repo.
2. Create a new Railway project from the repo. The included `Dockerfile` and `railway.toml`
   are picked up automatically; health check is `/api/health`.
3. Add a Postgres plugin — Railway injects `DATABASE_URL`. The app rewrites the legacy
   `postgres://` scheme to `postgresql://` for you.
4. Add whatever API keys you want active (see below) as Railway variables.
5. Deploy. Railway sets `$PORT`; the container honors it.

---

## Environment variables

Copy `.env.example` to `.env` and fill in what you have. **Everything is optional** — the app
degrades gracefully when a key or source is missing.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL in prod; omit for local SQLite |
| `ANTHROPIC_API_KEY` | Enables Claude lead enrichment + AI-written templates |
| `ATTOM_API_KEY` | Enables ATTOM property / foreclosure / off-market / buyer data |
| `ATTOM_BASE` | Defaults to `https://api.developer.attomdata.com` |
| `ATTOM_BUYER_PATH` | Defaults to `/transaction/aggregation` |
| `USPS_USER_ID` | Enables USPS address standardization |
| `GOOGLE_MAPS_API_KEY` | Shows Street View thumbnails on lead detail |
| `BATCHDATA_API_KEY` / `TRACERFY_API_KEY` | Enables skip-trace batch calls |
| `DEMO_MODE` | `true` seeds ~30 sample leads + 6 buyers |
| `ENABLE_*_SCRAPER` | Per-source scraper toggles, all `false` by default |

---

## How the data sources work

ATTOM agents make real API calls the moment `ATTOM_API_KEY` is set. If a buyer endpoint
returns 403/404/empty, it logs a clear message and falls through to the other buyer sources
(and ultimately to your manual/CSV entries) instead of crashing the run.

The non-API scrapers (county tax-delinquent, HUD, probate, code-violation, Zillow FSBO, deed
records, TREC, hard-money) ship as **honest runtime stubs, disabled by default**. Each is wired
into the registry and toggled by its `ENABLE_*_SCRAPER` flag, but the parsing logic is left as
a stub on purpose — see the caveat below before you turn any of them on.

---

## Two things to read before you rely on this

**1. The scrapers are off for a reason.** Each target site has its own Terms of Service and
`robots.txt`, and they don't all allow automated collection — Zillow's terms specifically
prohibit scraping, for instance. Before you enable a scraper and write its parser, check that
site's ToS and robots rules, and prefer an official API or data-licensing program where one
exists. That's a real legal exposure for the business, not just a formality, so it's worth
getting right (and worth a quick word with an attorney if you plan to commercialize the data).

**2. The generated offer letters and assignment contracts are drafts, not legal advice.** They
include the Texas Occupations Code §1101.0045 equitable-interest disclosure and reasonable
boilerplate, but I'm not a lawyer and neither is the app. Have a Texas real estate attorney
review your templates once before you send anything to a real seller or buyer — assignment and
wholesaling rules are exactly the kind of thing that's cheaper to get right up front.

---

## Project layout

```
app/
  main.py            # app, models, scoring, generators, all routes, dashboard serving
  sources.py         # pluggable SourceAgent registry (ATTOM + disabled scraper stubs)
  build_dashboard.py # regenerates the 8 dashboard HTML pages from shared nav/head
  requirements.txt
  Dockerfile
  railway.toml
  .env.example
  dashboard/
    index.html leads.html pipeline.html buyers.html
    calculator.html templates.html import.html settings.html
    app.js styles.css
```

If you edit the shared nav or `<head>`, change it in `build_dashboard.py` and re-run
`python build_dashboard.py` rather than hand-editing all eight pages.

---

## API reference (highlights)

- `GET /api/health` — status + which integrations are live
- `GET /api/leads?status=` — list/filter leads
- `PUT /api/leads/{id}/status` — move a lead across the pipeline
- `POST /api/buyers/manual` — add a buyer (needs name + email or phone)
- `POST /api/import/{buyers,properties}` — CSV import
- `GET /api/export/top-leads` — top 50 by score as CSV
- `POST /api/calculate-deal` — MAO / margin math (also powers the calculator page)
- `POST /api/generate/{offer-letter,assignment-contract,template}` — documents
- `POST /api/validate-address` — USPS standardization
- `POST /api/skip-trace/batch` — skip-trace or CSV-ready fallback
- `GET /api/market-stats` — five-metro stats for the home dashboard
- `GET /api/runs` / `POST /api/runs` — list past runs / trigger a new source run

## Wholesaling workflow (v1.1.0)

These features take a lead from "interesting" to "assigned":

- **Comps → ARV** — `POST /api/comps`. Computes ARV from the median $/sqft of qualified
  sold comps applied to the subject's square footage, with a value range and a
  high/medium/low confidence rating (based on comp count and price dispersion). Outliers
  outside a ±25% sqft band are dropped. Works with manually-entered comps today and
  auto-pulls from ATTOM's `sale/snapshot` once your key/IP is live. Lives on the **Deals** page.
- **Follow-ups** — `POST /api/leads/{id}/contact-log` logs each call/text/email and
  auto-schedules the next touch on a 1→3→7→14→30-day cadence (then monthly).
  `GET /api/follow-ups/due?window=today|overdue|week|all` is your daily call list. See the
  **Follow-ups** page.
- **Buyer matching** — `GET /api/leads/{id}/matched-buyers` ranks your buyer list against a
  deal by price fit, geography, asset type, beds, rehab capacity, proof of funds, and recent
  activity, each with reasons. On the **Deals** page.
- **Deal terms & deadlines** — `PUT /api/leads/{id}/deal-terms` stores ARV, repairs, asking
  price, beds/baths/sqft, occupancy, and inspection/close dates. `GET /api/deals/deadlines`
  lists everything with a deadline, soonest first.
- **Buy box + proof of funds** — `PUT /api/buyers/{id}` and `PUT /api/buyers/{id}/pof`. Edit
  inline by clicking a buyer on the **Buyers** page.
- **Property info sheet** — `GET /api/leads/{id}/pi-sheet` renders a print-to-PDF one-pager
  (photos, numbers, terms, as-is/assignable disclaimers) you can send to buyers.
- **Deal blast** — `POST /api/leads/{id}/blast` returns the matched buyers plus ready-to-send
  email and SMS copy. It does **not** send anything — you review and send from your own
  email/phone.

### A note on the ARV number

The comps tool gives you a fast, auditable ballpark — median $/sqft is transparent and hard to
fudge — but it is not a substitute for a full walkthrough and a local agent's BPO. Treat it as a
screening number, not gospel, especially in mixed neighborhoods where $/sqft varies a lot.

## Contracts, outreach & AI (v1.2–v1.3)

**Contracts & closing**
- `POST /api/generate/contract` — assignment, double close, lease-option, or subject-to, each
  with the Texas §1101.0045 disclosure (subject-to adds a due-on-sale acknowledgment;
  lease-option flags the Property Code Ch. 5 executory-contract rules). Auto-selects a type if
  you don't specify. **Contracts** page.
- `POST /api/contracts/send-for-signature` — saves the contract; with a HelloSign/DocuSign key
  it marks it sent, otherwise returns sign-manually instructions. `GET /api/contracts` and
  `PUT /api/contracts/{id}/status` track signing.

**AI intelligence**
- `POST /api/ai/offer-recommendation` — MAO (70% rule), suggested assignment fee, the offer
  ceiling that still leaves your buyer a target margin, and competing-offer scenarios.
- `POST /api/ai/parse-reply` — classifies a seller reply (interested / not interested / callback
  / price objection / wrong number), logs it, updates status, flags suppression. Claude when a
  key is set, keyword matching otherwise.
- `POST /api/ai/personalize-template` — copy whose tone adapts to the lead's distress signal.

**Outreach drafting (you send, the app never does)**
- `POST /api/outreach/queue` — builds a send list: for each seller (or matched buyer) it drafts
  the message and a pre-filled `mailto:` link. You click, your mail app opens, you send.
- `GET /api/outreach/sequence` — seller sequence (intro → follow-ups 1–5 → still-interested →
  breakup). `POST /api/outreach/mark-sent` logs the touch and schedules the next follow-up;
  `GET /api/outreach/logs` is the history. **Outreach** page ties it together.

> Why drafting instead of auto-send: automated calls/texts/voicemails to homeowners are covered
> by the TCPA, with penalties of $500–$1,500 *per message*. Drafting while you send from your own
> inbox keeps you out of that entirely.

## Data quality, comps & market context (v1.4)

- **Hard sanity gate on ingestion** — every lead must have a real US state and a 5-digit ZIP or
  it's rejected before saving (count recorded in the run). ATTOM calls now log their full URL and
  params, and on any non-200/failure the error is captured and written to the run notes — the app
  never silently inserts placeholder rows when a source fails.
- **`data_quality` on every lead** — `verified` (USPS confirmed), `unverified` (no USPS key, saved
  anyway), or `invalid` (USPS rejected — not saved). Shown as a colored dot in the leads table and
  a badge on the detail page.
- **`GET /api/leads/{id}/comps`** — 3–5 recent sold comps near the lead from ATTOM; empty array
  with a clear message when ATTOM isn't reachable. Never fabricated.
- **`GET /api/market-stats?zip=&county=`** — ZIP-scoped snapshot when ATTOM supports it, otherwise
  county-level static fallback.
- **Lead detail page** — `/dashboard/leads/detail.html?id=...`: full lead info, the comps table, a
  Google Static Maps embed (hidden gracefully without a key), and a neighborhood market widget.
  Click any row in the leads table to open it.

> DEMO_MODE remains a separate, explicit opt-in. The data-quality gates apply to real ATTOM/CSV
> ingestion; demo rows are marked `unverified`.

### Database migrations

New columns are added automatically on boot by `ensure_schema()` (it only ever *adds* missing
columns, never drops or alters data), so deploying v1.1.0 over an existing v1.0.0 Postgres
database is safe — your existing leads and buyers are preserved.

