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
