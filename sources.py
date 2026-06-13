"""
sources.py — Multi-source ingestion engine.

Each source is a SourceAgent subclass with:
  - name           : stable identifier stored on leads/buyers
  - env_flag       : the ENABLE_* / key env var that turns it on
  - kind           : "lead" or "buyer"
  - enabled()      : True when its env var is truthy
  - fetch(county)  : returns raw dicts (network/scrape) or [] on any failure
  - normalize(raw) : maps raw dicts to the Lead/Buyer schema dicts

Design rules honored here:
  * Everything is OFF by default. Scrapers stay disabled unless their ENABLE_*
    flag is set, because they touch third-party sites whose terms of service
    and robots rules must be reviewed per-source before you turn them on.
  * Every fetch is wrapped so a 403/404/timeout/empty result logs a clear
    message and returns [] — the run continues with whatever else is enabled.
  * 5s timeout on every outgoing request; a polite delay between scrape calls.
  * ATTOM agents make real API calls when ATTOM_API_KEY is set.
"""

from __future__ import annotations

import os
import time
from typing import Iterable

import httpx

HTTP_TIMEOUT = 5.0
SCRAPE_DELAY = 1.5  # seconds between scrape requests (politeness)

ATTOM_BASE = os.getenv("ATTOM_BASE", "https://api.developer.attomdata.com").rstrip("/")
ATTOM_KEY = os.getenv("ATTOM_API_KEY", "").strip()
ATTOM_BUYER_PATH = os.getenv("ATTOM_BUYER_PATH", "/transaction/aggregation")

DEFAULT_COUNTIES = [
    "Dallas", "Harris", "Tarrant", "Bexar", "Travis",
    "Collin", "Denton", "Fort Bend", "Montgomery", "Williamson",
]

COUNTY_SEAT = {
    "Dallas": ("Dallas", "TX"),
    "Harris": ("Houston", "TX"),
    "Tarrant": ("Fort Worth", "TX"),
    "Bexar": ("San Antonio", "TX"),
    "Travis": ("Austin", "TX"),
    "Collin": ("Plano", "TX"),
    "Denton": ("Denton", "TX"),
    "Fort Bend": ("Sugar Land", "TX"),
    "Montgomery": ("Conroe", "TX"),
    "Williamson": ("Round Rock", "TX"),
}


def _truthy(var: str) -> bool:
    return os.getenv(var, "").strip().lower() in {"1", "true", "yes", "on"}


def _log(agent: str, msg: str) -> None:
    print(f"[source:{agent}] {msg}")


# --------------------------------------------------------------------------- #
# Base class
# --------------------------------------------------------------------------- #

class SourceAgent:
    name = "base"
    env_flag = ""
    kind = "lead"  # "lead" | "buyer"

    def enabled(self) -> bool:
        return _truthy(self.env_flag) if self.env_flag else False

    def fetch(self, county: str) -> list[dict]:
        raise NotImplementedError

    def normalize(self, raw: list[dict], county: str) -> list[dict]:
        return raw

    def collect(self, county: str) -> list[dict]:
        try:
            raw = self.fetch(county)
        except Exception as exc:  # any failure is non-fatal
            _log(self.name, f"fetch failed for {county}: {exc} — continuing")
            return []
        if not raw:
            _log(self.name, f"no records for {county}")
            return []
        try:
            return self.normalize(raw, county)
        except Exception as exc:
            _log(self.name, f"normalize failed: {exc}")
            return []


class ScraperStub(SourceAgent):
    """
    Base for site scrapers. Disabled by default. When enabled, performs a
    bounded, polite request and returns []. Each site's parser is intentionally
    left as a clearly-marked runtime stub: the page structure differs per county
    portal and changes over time, and you must confirm each site's ToS/robots
    rules before parsing. Wire the parser per-site when you turn the flag on.
    """

    target_hint = ""

    def fetch(self, county: str) -> list[dict]:
        if not self.enabled():
            return []
        _log(
            self.name,
            f"enabled but parser not wired for {county}. "
            f"Target: {self.target_hint or 'see source notes'}. "
            f"Confirm ToS/robots, then implement the per-site parser. Returning [].",
        )
        time.sleep(SCRAPE_DELAY)
        return []


# --------------------------------------------------------------------------- #
# ATTOM agents (real API)
# --------------------------------------------------------------------------- #

def _attom_get(path: str, params: dict) -> dict | None:
    if not ATTOM_KEY:
        return None
    url = f"{ATTOM_BASE}{path}"
    headers = {"Accept": "application/json", "apikey": ATTOM_KEY}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(url, params=params, headers=headers)
        if resp.status_code in (403, 404):
            _log("attom", f"{path} -> {resp.status_code}; skipping")
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        _log("attom", f"{path} request failed: {exc}")
        return None


class AttomPropertyRecords(SourceAgent):
    name = "attom_property"
    env_flag = ""  # active whenever ATTOM_KEY is present
    kind = "lead"

    def enabled(self) -> bool:
        return bool(ATTOM_KEY)

    def fetch(self, county: str) -> list[dict]:
        city, state = COUNTY_SEAT.get(county, (county, "TX"))
        data = _attom_get(
            "/propertyapi/v1.0.0/property/snapshot",
            {"address2": f"{city}, {state}", "pagesize": "25"},
        )
        return (data or {}).get("property", []) if data else []

    def normalize(self, raw: list[dict], county: str) -> list[dict]:
        out = []
        city, state = COUNTY_SEAT.get(county, (county, "TX"))
        for p in raw:
            addr = (p.get("address") or {})
            out.append({
                "address": addr.get("line1", ""),
                "city": addr.get("locality", city),
                "state": addr.get("countrySubd", state),
                "zip_code": addr.get("postal1", ""),
                "county": county,
                "owner_name": (p.get("owner") or {}).get("owner1", {}).get("fullname", ""),
                "est_value": float((p.get("avm") or {}).get("amount", {}).get("value", 0) or 0),
                "distress_signals": [],
            })
        return out


class AttomForeclosure(SourceAgent):
    name = "attom_foreclosure"
    kind = "lead"

    def enabled(self) -> bool:
        return bool(ATTOM_KEY)

    def fetch(self, county: str) -> list[dict]:
        data = _attom_get("/property/v3/foreclosure/aggregation", {"geoIdV4": county})
        return (data or {}).get("foreclosure", []) if data else []

    def normalize(self, raw: list[dict], county: str) -> list[dict]:
        return [
            {
                "address": r.get("address", ""),
                "county": county,
                "distress_signals": ["foreclosure"],
                "est_equity_pct": 40.0,
            }
            for r in raw
        ]


class AttomOffMarket(SourceAgent):
    name = "attom_offmarket"
    kind = "lead"

    def enabled(self) -> bool:
        return bool(ATTOM_KEY)

    def fetch(self, county: str) -> list[dict]:
        data = _attom_get("/propertypoint/offmarket/aggregation", {"geoIdV4": county})
        return (data or {}).get("property", []) if data else []

    def normalize(self, raw: list[dict], county: str) -> list[dict]:
        return [
            {
                "address": (r.get("address") or {}).get("line1", ""),
                "county": county,
                "distress_signals": ["absentee-owner"],
            }
            for r in raw
        ]


class AttomTransactionsBuyers(SourceAgent):
    """Cash sales in the last 90 days -> likely active cash buyers."""

    name = "attom_buyers"
    kind = "buyer"

    def enabled(self) -> bool:
        return bool(ATTOM_KEY)

    def fetch(self, county: str) -> list[dict]:
        data = _attom_get(ATTOM_BUYER_PATH, {"geoIdV4": county, "interval": "90"})
        if data is None:
            _log("attom_buyers", f"buyer path {ATTOM_BUYER_PATH} empty/blocked; "
                                 "falling back to manual/CSV buyers")
            return []
        return data.get("transaction", []) or data.get("property", [])

    def normalize(self, raw: list[dict], county: str) -> list[dict]:
        city, state = COUNTY_SEAT.get(county, (county, "TX"))
        out = []
        for r in raw:
            buyer = (r.get("buyer") or {})
            name = buyer.get("name") or (r.get("sale") or {}).get("buyerName")
            if not name:
                continue
            out.append({
                "name": name,
                "entity_type": "LLC" if "LLC" in str(name).upper() else "",
                "city": city,
                "state": state,
                "recent_cash_deals": 1,
            })
        return out


# --------------------------------------------------------------------------- #
# Scraper agents (disabled by default; honest stubs)
# --------------------------------------------------------------------------- #

class TaxDelinquentScraper(ScraperStub):
    name = "tax_delinquent"
    env_flag = "ENABLE_TAX_DELINQUENT_SCRAPER"
    kind = "lead"
    target_hint = "County CAD / tax assessor delinquent rolls"


class HudForeclosureScraper(ScraperStub):
    name = "hud_foreclosure"
    env_flag = "ENABLE_HUD_SCRAPER"
    kind = "lead"
    target_hint = "HUD Home Store TX listings (hudhomestore.gov)"


class ProbateScraper(ScraperStub):
    name = "probate"
    env_flag = "ENABLE_PROBATE_SCRAPER"
    kind = "lead"
    target_hint = "County probate court portals (Dallas/Harris/Tarrant/Bexar/Travis)"


class CodeViolationScraper(ScraperStub):
    name = "code_violation"
    env_flag = "ENABLE_CODE_VIOLATION_SCRAPER"
    kind = "lead"
    target_hint = "City open-data code enforcement (Dallas/Houston/SA/Austin)"


class ZillowFsboScraper(ScraperStub):
    name = "zillow_fsbo"
    env_flag = "ENABLE_ZILLOW_SCRAPER"
    kind = "lead"
    target_hint = ("Zillow FSBO. NOTE: Zillow's ToS prohibit scraping. Prefer their "
                   "permitted data partners / a licensed feed before enabling.")


class CountyDeedScraper(ScraperStub):
    name = "county_deed"
    env_flag = "ENABLE_COUNTY_SCRAPER"
    kind = "buyer"
    target_hint = "County clerk deed records (cash grantees)"


class TxSosScraper(ScraperStub):
    name = "tx_sos"
    env_flag = "ENABLE_TX_SOS_SCRAPER"
    kind = "buyer"
    target_hint = "Texas SOSDirect business entity search (real-estate LLCs)"


class TrecScraper(ScraperStub):
    name = "trec"
    env_flag = "ENABLE_TREC_SCRAPER"
    kind = "buyer"
    target_hint = "TREC license holder search (active investor-agents)"


class HardMoneyScraper(ScraperStub):
    name = "hard_money"
    env_flag = "ENABLE_HARD_MONEY_SCRAPER"
    kind = "buyer"
    target_hint = "TX hard-money lender portfolio/borrower pages"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

LEAD_AGENTS: list[SourceAgent] = [
    AttomPropertyRecords(),
    AttomForeclosure(),
    AttomOffMarket(),
    TaxDelinquentScraper(),
    HudForeclosureScraper(),
    ProbateScraper(),
    CodeViolationScraper(),
    ZillowFsboScraper(),
]

BUYER_AGENTS: list[SourceAgent] = [
    AttomTransactionsBuyers(),
    CountyDeedScraper(),
    TxSosScraper(),
    TrecScraper(),
    HardMoneyScraper(),
]


def enabled_source_names() -> list[str]:
    return [a.name for a in (LEAD_AGENTS + BUYER_AGENTS) if a.enabled()]


def _dedupe_leads(rows: list[tuple[dict, str]]) -> list[tuple[dict, str]]:
    seen: dict[str, tuple[dict, str]] = {}
    for rec, src in rows:
        key = (rec.get("address", "") + "|" + rec.get("zip_code", "")).strip().lower()
        if not key or key == "|":
            key = f"_no_addr_{len(seen)}"
        if key in seen:
            # merge distress signals from duplicate sources
            existing = seen[key][0]
            sigs = set(existing.get("distress_signals") or []) | set(rec.get("distress_signals") or [])
            existing["distress_signals"] = sorted(sigs)
        else:
            seen[key] = (rec, src)
    return list(seen.values())


def _dedupe_buyers(rows: list[tuple[dict, str]]) -> list[tuple[dict, str]]:
    seen: dict[str, tuple[dict, str]] = {}
    for rec, src in rows:
        key = str(rec.get("name", "")).strip().lower() or f"_anon_{len(seen)}"
        if key not in seen:
            seen[key] = (rec, src)
    return list(seen.values())


def collect_leads(counties: Iterable[str], demo: bool = False) -> list[tuple[dict, str]]:
    if demo:
        return _demo_leads(counties)
    rows: list[tuple[dict, str]] = []
    for agent in LEAD_AGENTS:
        if not agent.enabled():
            continue
        for county in counties:
            for rec in agent.collect(county):
                rows.append((rec, agent.name))
    return _dedupe_leads(rows)


def collect_buyers(counties: Iterable[str], demo: bool = False) -> list[tuple[dict, str]]:
    if demo:
        return _demo_buyers(counties)
    rows: list[tuple[dict, str]] = []
    for agent in BUYER_AGENTS:
        if not agent.enabled():
            continue
        for county in counties:
            for rec in agent.collect(county):
                rows.append((rec, agent.name))
    return _dedupe_buyers(rows)


def fetch_market_stats(attom_key: str, markets: list[str]) -> dict | None:
    """Best-effort ATTOM market snapshot; None on any failure -> caller uses fallback."""
    if not attom_key:
        return None
    # ATTOM market endpoints vary by plan; treat absence as "use static fallback".
    _log("attom", "market-stats endpoint not configured for this plan; using fallback")
    return None


# --------------------------------------------------------------------------- #
# Demo data (DEMO_MODE=true) — lets the dashboard light up with no keys
# --------------------------------------------------------------------------- #

_DEMO_STREETS = [
    "1420 Magnolia St", "905 Birchwood Dr", "317 Lamar Ave", "2210 Oak Cliff Blvd",
    "88 Cedar Springs Rd", "640 Pecan Grove Ln", "1199 Bluebonnet Way",
    "5005 Greenville Ave", "742 Riverside Dr", "61 Mockingbird Ln",
]
_DEMO_SIGNALS = [
    ["tax-delinquent", "vacant"], ["foreclosure"], ["probate", "absentee-owner"],
    ["code-violation"], ["pre-foreclosure", "liens"], ["fsbo"], ["divorce"],
    ["expired-listing"], ["vacant", "absentee-owner"], ["auction"],
]


def _demo_leads(counties: Iterable[str]) -> list[tuple[dict, str]]:
    rows = []
    counties = list(counties) or DEFAULT_COUNTIES
    for i, county in enumerate(counties):
        city, state = COUNTY_SEAT.get(county, (county, "TX"))
        for j in range(3):
            idx = (i * 3 + j) % len(_DEMO_STREETS)
            sigs = _DEMO_SIGNALS[idx]
            rows.append(({
                "address": _DEMO_STREETS[idx],
                "city": city,
                "state": state,
                "zip_code": f"75{(idx*7) % 900:03d}",
                "county": county,
                "owner_name": ["J. Carter", "M. Alvarez", "R. Nguyen", "T. Brooks"][idx % 4],
                "est_value": 250000 + idx * 18500,
                "est_equity_pct": 35 + (idx * 6) % 55,
                "tax_delinquent": "tax-delinquent" in sigs,
                "days_on_market": (idx * 23) % 160,
                "distress_signals": sigs,
            }, "demo"))
    return rows


def _demo_buyers(counties: Iterable[str]) -> list[tuple[dict, str]]:
    names = [
        ("Lone Star REI LLC", "LLC"), ("BlueSky Capital", "Inc"),
        ("Hill Country Homes LP", "LP"), ("M. Alvarez", ""),
        ("Trinity Buy-Box LLC", "LLC"), ("Gulf Coast Holdings", "Trust"),
    ]
    rows = []
    counties = list(counties) or DEFAULT_COUNTIES
    for i, (name, etype) in enumerate(names):
        county = counties[i % len(counties)]
        city, state = COUNTY_SEAT.get(county, (county, "TX"))
        rows.append(({
            "name": name,
            "entity_type": etype,
            "email": f"acq{i}@example.com",
            "phone": f"214-555-0{100 + i}",
            "city": city,
            "state": state,
            "budget_min": 100000,
            "budget_max": 300000 + i * 40000,
            "recent_cash_deals": (i % 4) + 1,
        }, "demo"))
    return rows
